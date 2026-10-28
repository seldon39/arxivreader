"""Pertinence : mots, noms d'auteurs, calibration relative au lot du jour."""

import unittest

from app.profile import _initials_only
from app.relevance import _robust_z, author_key, cosine_sparse, tfidf_vectors, tokens


class Relevance(unittest.TestCase):
    def test_tokens(self):
        words = tokens("Toeplitz operators and the Bergman kernels of the $\\bar\\partial$ operator")
        self.assertIn("toeplitz operator", words)
        self.assertIn("bergman kernel", words)
        self.assertNotIn("the", words)

    def test_author_key(self):
        self.assertEqual(author_key("G. Marinescu"), author_key("George Marinescu"))
        self.assertEqual(author_key("Siegfried Hörmann"), ("hormann", "s"))
        self.assertNotEqual(author_key("Xiaonan Ma"), author_key("Y. Ma"))
        self.assertTrue(_initials_only("X. Ma") and _initials_only("J.-M. Bismut") is not None)
        self.assertFalse(_initials_only("Xiaonan Ma"))

    def test_tfidf_ranks_shared_vocabulary(self):
        profile, close, far = tfidf_vectors([
            "Asymptotics of Toeplitz operators and Bergman kernels on symplectic manifolds",
            "Bergman kernels and Toeplitz operators with singular symbols",
            "Ricci flow with bounded scalar curvature in dimension seven",
        ])
        self.assertGreater(cosine_sparse(profile, close), cosine_sparse(profile, far))

    def test_robust_z(self):
        z = _robust_z([0.80, 0.81, 0.82, 0.83, 0.84, 0.95])
        self.assertGreater(z[-1], 3)
        self.assertAlmostEqual(sorted(z)[len(z) // 2], 0.0, places=6)




class LibraryRelevance(unittest.TestCase):
    """Tri des dossiers : une pertinence par article de la bibliothèque, calculée sans réseau si besoin."""

    def test_scores_by_paper_id(self):
        from unittest import mock
        from app import relevance
        papers = [{"id": "2601.00001", "arxiv_id": "2601.00001", "title": "Bergman kernels", "abstract": "", "authors": ["A"]},
                  {"id": "2601.00002", "arxiv_id": "2601.00002", "title": "Fluid dynamics", "abstract": None, "authors": []}]
        profile = ([{"label": "Bergman", "text": "Bergman kernels and Toeplitz operators", "s2": None, "weight": 1.0}], [], {}, [])
        with mock.patch.object(relevance.db, "list_papers", return_value=papers), \
             mock.patch.object(relevance, "fetch_embeddings", side_effect=OSError("offline")), \
             mock.patch.object(relevance, "profile_documents", return_value=profile), \
             mock.patch.object(relevance, "cached_embeddings", return_value={}):
            scores = relevance.library_relevance()
        self.assertEqual(set(scores), {"2601.00001", "2601.00002"})
        self.assertGreater(scores["2601.00001"], scores["2601.00002"])


class FollowedAuthors(unittest.TestCase):
    """Auteurs suivis : un homonyme d'un autre domaine (« Lei Wang » en physique) n'apparaît pas."""

    def test_homonym_from_another_field(self):
        import time
        from unittest import mock
        from app import profile
        today = time.strftime("%Y-%m-%d")
        entry = lambda arxiv, name, categories: (  # noqa: E731
            f"<entry><id>http://arxiv.org/abs/{arxiv}v1</id><published>{today}T00:00:00Z</published>"
            f"<title>T {arxiv}</title><summary>S</summary><author><name>{name}</name></author>"
            + "".join(f'<category term="{c}"/>' for c in categories) + "</entry>")
        atom = ('<feed xmlns="http://www.w3.org/2005/Atom">' + entry("2610.00001", "Lei Wang", ["hep-th"])
                + entry("2610.00002", "Lei Wang", ["math.AP"]) + entry("2610.00003", "Lei Wang", ["math-ph", "hep-th"])
                + entry("2610.00004", "Lin Wang", ["math.DG"]) + "</feed>")
        client = mock.MagicMock()
        client.__enter__.return_value.get.return_value = mock.Mock(status_code=200, text=atom)
        with mock.patch.object(profile, "list_items", return_value=[{"kind": "author", "label": "Lei Wang"}]), \
             mock.patch.object(profile.httpx, "Client", return_value=client):
            papers = profile.followed_author_papers(["math.DG", "math.CV"])
        self.assertEqual(sorted(p["arxiv_id"] for p in papers), ["2610.00002", "2610.00003"])
        # grands domaines : physique d'un seul tenant (hep-th, gr-qc…), math-ph pour les mathématiques et la physique
        from app.profile import _fields
        self.assertEqual(_fields("gr-qc"), _fields("hep-th"))
        self.assertEqual(_fields("math-ph"), {"math", "physics"})
        self.assertEqual(_fields("stat.ML"), _fields("cs.LG"))
        self.assertNotEqual(_fields("q-bio.PE"), _fields("math.PR"))
        with mock.patch.object(profile, "list_items", return_value=[{"kind": "author", "label": "Lei Wang"}]), \
             mock.patch.object(profile.httpx, "Client", return_value=client):
            physics = profile.followed_author_papers(["math-ph"])  # feed de physique mathématique seule
        self.assertEqual(sorted(p["arxiv_id"] for p in physics), ["2610.00001", "2610.00002", "2610.00003"])


class FollowedAuthorReason(unittest.TestCase):
    """Raison « auteur suivi » du feed : la même personne, pas un homonyme de même initiale."""

    def test_same_initial_homonym(self):
        from unittest import mock
        from app import relevance
        items = [{"key": "a", "arxiv_id": "2610.28478", "title": "Single generation of C*-algebras", "abstract": "",
                  "authors": ["Xin Ma"]},
                 {"key": "b", "arxiv_id": "2610.28479", "title": "Toeplitz operators", "abstract": "",
                  "authors": ["X. Ma", "G. Marinescu"]}]
        followed = [{"id": 1, "label": "Xiaonan Ma", "only": [], "excluded": []}]
        with mock.patch.object(relevance, "profile_documents", return_value=([], [], followed, [])), \
             mock.patch.object(relevance, "cached_embeddings", return_value={}):
            relevance.score_items(items)
        reasons = {i["key"]: [r["label"] for r in i["reasons"] if r["kind"] == "author"] for i in items}
        self.assertEqual(reasons, {"a": [], "b": ["Xiaonan Ma"]})


class Namesakes(unittest.TestCase):
    """Homonyme d'un auteur suivi : catégories gardées pour lui, article écarté (« pas cette personne »)."""

    def setUp(self):
        import pathlib
        import tempfile
        from app import db
        self.saved = (db.DB_PATH, db.LIBRARY)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = self.tmp / "test.db", self.tmp
        db.init()

    def tearDown(self):
        from app import db
        db.DB_PATH, db.LIBRARY = self.saved

    def test_rule(self):
        from app.relevance import follows
        author = {"label": "Jan Novák", "only": ["math.CV", "math-ph"], "excluded": ["2610.00009"]}
        self.assertTrue(follows(author, ["Jan Novak"], ["math.CV"], "2610.00001"))
        self.assertFalse(follows(author, ["Jan Novák"], ["math.NT"], "2610.00002"))   # autre sous-domaine
        self.assertTrue(follows(author, ["Jan Novák"], ["math.MP"], "2610.00003"))    # math-ph = math.MP
        self.assertFalse(follows(author, ["Jan Novák"], ["math.CV"], "2610.00009"))   # écarté à la main
        self.assertTrue(follows({"label": "Jan Novák"}, ["J. Novák"], ["math.NT"]))   # sans règle : comme avant

    def test_set_rule_and_categories(self):
        from fastapi.testclient import TestClient
        from app import db, feed, main, profile
        item_id = profile.add_item("author", "Jan Novák")
        # trois articles annoncés dans vos catégories, un trouvé seulement par la recherche de ses articles (hep-th)
        papers = [("2610.00001", ["math.CV"]), ("2610.00002", ["math.CV", "math.DG"]), ("2610.00003", ["math.NT"]),
                  ("2610.00004", ["hep-th"])]
        with db.connect() as connection:
            for arxiv_id, categories in papers:
                item = {"key": arxiv_id, "arxiv_id": arxiv_id, "version": 1, "title": "T", "authors": ["Jan Novák"], "abstract": "",
                        "categories": categories, "primary_category": categories[0], "announced": feed.now()[:10], "link": ""}
                if arxiv_id != "2610.00004":
                    feed._store(connection, item)
                feed._store(connection, item, followed=True)
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        info = client.get(f"/api/profile/items/{item_id}/categories", params={"paper": "2610.00003"}).json()
        # hep-th : peut-être l'homonyme, pas proposé d'office
        self.assertEqual(info["kept"], [{"category": "math.CV", "count": 2}, {"category": "math.DG", "count": 1}])
        self.assertEqual(info["paper"], ["math.NT"])
        response = client.put(f"/api/profile/items/{item_id}/rule", json={"only": ["math.CV", "math.DG"], "exclude": "2610.00003"})
        author = next(i for i in response.json()["items"] if i["id"] == item_id)
        self.assertEqual((author["only"], author["excluded"]), (["math.CV", "math.DG"], ["2610.00003"]))
        with db.connect() as connection:
            followed = {r["key"]: r["followed"] for r in connection.execute("SELECT key, followed FROM feed_items")}
        self.assertEqual(followed, {"2610.00001": 1, "2610.00002": 1, "2610.00003": 0, "2610.00004": 0})  # quittent la section
        self.assertEqual(client.put(f"/api/profile/items/{item_id}/rule", json={"only": ["not a category!"]}).status_code, 400)
        # catégories écrites comme arXiv les écrit
        client.put(f"/api/profile/items/{item_id}/rule", json={"only": ["math.dg", "cond-mat.stat-mech", "physics.Optics"]})
        self.assertEqual(next(i for i in profile.list_items() if i["id"] == item_id)["only"], ["cond-mat.stat-mech", "math.DG", "physics.optics"])
        # retrait annulé : la règle revient avec l'auteur
        rule = next(i for i in profile.list_items() if i["id"] == item_id)
        client.delete(f"/api/profile/items/{item_id}")
        client.post("/api/profile/items", json={"kind": "author", "label": "Jan Novák", "only": rule["only"], "excluded": rule["excluded"]})
        restored = next(i for i in profile.list_items() if i["label"] == "Jan Novák")
        self.assertEqual((restored["only"], restored["excluded"]), (rule["only"], ["2610.00003"]))
        item_id = restored["id"]
        client.put(f"/api/profile/items/{item_id}/rule", json={"only": []})  # plus de règle : toutes les catégories
        self.assertEqual(next(i for i in profile.list_items() if i["id"] == item_id)["only"], [])
        self.assertEqual(next(i for i in profile.list_items() if i["id"] == item_id)["excluded"], ["2610.00003"])


if __name__ == "__main__":
    unittest.main()
