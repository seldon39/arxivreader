"""Papiers liés : proximité (références communes, co-citations), doublons, mise en cache — Semantic Scholar simulé."""

import pathlib
import tempfile
import threading
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import db, main, related
from app.arxiv import Metadata


def paper(pid, title, year=2000, cites=10, arxiv=None):
    return {"paperId": pid, "title": title, "year": year, "citationCount": cites, "externalIds": {"ArXiv": arxiv} if arxiv else {},
            "authors": [{"name": "A. Author"}]}


# l'article cite R1, R2, R3 ; il est cité par C1, C2 ; R1 et R1bis sont deux fiches du même article ;
# S1, cité par trois de ces voisins (R2, C1, C2), est un voisin de second niveau
REFS = [{"citedPaper": paper("R1", "Toeplitz quantization of Kahler manifolds and gl(N)", 1993, 300, "hep-th/9309134")},
        {"citedPaper": paper("R1bis", "Toeplitz quantization of Kahler manifolds anggl(N)", 1993, 200)},
        {"citedPaper": paper("R2", "Bergman kernels", 2004, 150, "math/0404494")},
        {"citedPaper": paper("R3", "Unrelated topic", 1970, 5)}]
CITES = [{"citingPaper": paper("C1", "Berezin-Toeplitz quantization", 2010, 50, "1003.2523")},
         {"citingPaper": paper("C2", "Later work", 2020, 3)}]
BATCH = {"R1": ["X"], "R1bis": ["X"], "R2": ["R1", "S1"], "R3": [], "C1": ["R1", "R2", "R1bis", "S1"], "C2": ["R1", "R2", "C1", "S1"],
         "S1": ["R1", "X"]}


class Related(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        self.calls = []
        self.batches = []

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def fake(self, send):
        """Réponse de Semantic Scholar à la dernière requête notée par le client simulé."""
        url, kind, body = self.client.last
        self.calls.append(url)
        if url.endswith("/references"):
            return mock.Mock(status_code=200, json=lambda: {"data": REFS}, raise_for_status=lambda: None)
        if url.endswith("/citations"):
            return mock.Mock(status_code=200, json=lambda: {"data": CITES}, raise_for_status=lambda: None)
        self.batches.append(body["ids"])
        second = "title" in kind  # fiches complètes (second niveau) ou seulement leurs références
        return mock.Mock(status_code=200, json=lambda: [{**(paper(i, "Second level paper", 1990, 400) if second else {}), "paperId": i,
                                                          "references": [{"paperId": r} for r in BATCH[i]]} for i in body["ids"]],
                         raise_for_status=lambda: None)

    def run_build(self, titles=None):
        test = self

        class Client:
            def __init__(self, *a, **k):
                self.last = None
                test.client = self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None):
                self.last = (url, "get", None)

            def post(self, url, params=None, json=None):
                self.last = (url, params["fields"], json)

        def with_retry(send, **kwargs):
            send()
            return self.fake(send)

        meta = Metadata(arxiv_id="0806.2370", version=1, title="Toeplitz operators on symplectic manifolds", authors=["X. Ma", "G. Marinescu"],
                        abstract="", published="2008-06-14", updated="", categories=[], primary_category="math.DG")
        with mock.patch.object(related.httpx, "Client", Client), mock.patch.object(related, "with_retry", with_retry), \
             mock.patch.object(related, "fetch_metadata", return_value=meta), mock.patch.object(related.time, "sleep"), \
             mock.patch.object(related, "fetch_titles", **({"side_effect": titles} if isinstance(titles, Exception) else {"return_value": titles or {}})) as arxiv:
            self.arxiv = arxiv
            return related.graph("0806.2370")

    def test_closeness_duplicates_and_cache(self):
        data = self.run_build()
        self.assertEqual(data["origin"]["title"], "Toeplitz operators on symplectic manifolds")
        ids = [n["id"] for n in data["nodes"]]
        self.assertIn("R1", ids)
        self.assertNotIn("R1bis", ids)              # doublon (titre presque identique, même année) : fusionné
        self.assertEqual(ids[0], "C1")              # 3 références communes avec l'article, et cité avec lui par C2
        r2 = next(n for n in data["nodes"] if n["id"] == "R2")
        self.assertEqual((r2["coupling"], r2["cocited"]), (1, 2))  # cite R1 ; cité par C1 et C2
        self.assertTrue(any({a, b} == {"C1", "C2"} for a, b, *_ in data["edges"]))  # C2 cite C1
        self.assertEqual(self.batches[1], ["S1"])   # second niveau : seul S1 est cité par trois voisins (X par deux)
        s1 = next(n for n in data["nodes"] if n["id"] == "S1")
        self.assertEqual((s1["rel"], s1["coupling"], s1["cocited"]), ("second", 1, 2))
        n = len(self.calls)
        again = self.run_build()                    # gardé : pas de nouvelle requête
        self.assertEqual(len(self.calls), n)
        self.assertEqual(again["nodes"][0]["id"], "C1")

    def test_titles_from_arxiv(self):
        # titre abîmé par Semantic Scholar (« anggl(N) ») : repris d'arXiv, avec ses auteurs ; « -- » devient « – »
        data = self.run_build({"hep-th/9309134": ("Toeplitz quantization of Kähler manifolds and $gl(N)$, $N\\to\\infty$ limits",
                                                   ["Martin Bordemann", "Eckhard Meinrenken", "Martin Schlichenmaier"]),
                               "1003.2523": ("Berezin--Toeplitz quantization", ["Martin Schlichenmaier"])})
        self.assertEqual(sorted(self.arxiv.call_args.args[0]), ["1003.2523", "hep-th/9309134", "math/0404494"])  # les articles affichés sur arXiv
        r1 = next(n for n in data["nodes"] if n["id"] == "R1")
        self.assertEqual((r1["title"], r1["authors"][0]), ("Toeplitz quantization of Kähler manifolds and $gl(N)$, $N\\to\\infty$ limits", "Martin Bordemann"))
        self.assertEqual(next(n for n in data["nodes"] if n["id"] == "C1")["title"], "Berezin–Toeplitz quantization")
        self.assertNotIn("R1bis", [n["id"] for n in data["nodes"]])  # toujours reconnu comme doublon (titre TeX d'arXiv)

    def test_titles_arxiv_unreachable(self):
        data = self.run_build(OSError("hors ligne"))  # titres de Semantic Scholar, graphe non gardé
        self.assertEqual(next(n for n in data["nodes"] if n["id"] == "R1")["title"], "Toeplitz quantization of Kahler manifolds and gl(N)")
        self.assertIsNone(data["builtAt"])
        self.assertIsNone(related._cached("0806.2370"))

    def test_short_batch_answer(self):
        # Semantic Scholar rend moins de fiches que demandé, ou une fiche nulle : candidats sans références, pas d'erreur
        saved = self.fake
        def fake(send):
            answer = saved(send)
            if "/batch" in self.client.last[0]:
                full = answer.json()
                return mock.Mock(status_code=200, json=lambda: [None, *full[1:3]], raise_for_status=lambda: None)
            return answer
        self.fake = fake
        data = self.run_build()
        self.assertTrue(data["nodes"])

    def test_library_marks_on_side_lists(self):
        with db.connect() as connection:
            connection.execute("INSERT INTO papers (id, arxiv_id, title, in_library) VALUES ('p1', '1003.2523', 'X', 1)")
        data = related.annotate({"origin": {"arxivId": "0806.2370"}, "nodes": [], "prior": [{"arxivId": "1003.2523"}], "derivative": [{"arxivId": None}]})
        self.assertEqual((data["prior"][0]["libraryId"], data["derivative"][0]["libraryId"]), ("p1", None))

    def test_old_format_rebuilt(self):
        related._ensure()
        with db.connect() as connection:  # graphe gardé par la version 1.6.0 (sans travaux antérieurs ni dérivés)
            connection.execute("INSERT INTO related_graphs (arxiv_id, data) VALUES (?, ?)", ("0806.2370", '{"origin": {}, "nodes": [], "edges": []}'))
        self.assertEqual(self.run_build()["format"], related.FORMAT)

    def follow(self, client, view):
        """Construction suivie comme le fait la page, jusqu'à sa fin."""
        for _ in range(200):
            if view["state"] != "building":
                return view
            time.sleep(0.02)
            view = client.get("/api/related/status", params={"id": view["id"]}).json()
        self.fail("construction sans fin")

    def test_route(self):
        # la requête n'attend pas la construction : elle la lance, la page la suit
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        self.assertEqual(client.post("/api/related", json={"query": "pas un identifiant"}).status_code, 400)
        with mock.patch.object(related, "graph", side_effect=related.RelatedError("rate-limited")):
            view = client.post("/api/related", json={"query": "arXiv:0806.2370"}).json()
            self.assertEqual(view["id"], "0806.2370")
            view = self.follow(client, view)
        self.assertEqual((view["state"], view["code"]), ("error", "rate-limited"))
        with mock.patch.object(related, "graph", return_value={"origin": {"arxivId": "0806.2370"}, "nodes": [], "builtAt": None}):
            view = self.follow(client, client.post("/api/related", json={"query": "0806.2370", "refresh": True}).json())
        self.assertEqual(view["state"], "done")
        self.assertIsNone(view["graph"]["origin"]["libraryId"])  # marqué pour la page

    def test_kept_only_and_busy(self):
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        # retour sur la page : marques remises à jour, aucune construction lancée pour un graphe plus gardé
        with mock.patch.object(related, "graph") as graph:
            view = client.post("/api/related", json={"query": "0806.2370", "kept": True}).json()
        self.assertEqual(view["state"], "none")
        graph.assert_not_called()
        # trop de constructions en cours : la page dit « un autre graphe… » au lieu d'empiler des fils
        gate = threading.Event()
        with mock.patch.object(related, "graph", side_effect=lambda *a: gate.wait(2) and {"origin": {}, "nodes": []}):
            states = [client.post("/api/related", json={"query": q, "refresh": True}).json()
                      for q in ("1001.0001", "1001.0002", "1001.0003", "1001.0004")]
            gate.set()
        self.assertEqual([s["state"] for s in states], ["building"] * 3 + ["error"])
        self.assertEqual(states[3]["code"], "busy")

    def test_cancel(self):
        # annulée : aussitôt pour la page ; le fil s'arrête à son prochain point de contrôle, rien n'est gardé
        reached, stopped = threading.Event(), threading.Event()
        def slow(*args):
            reached.set()
            try:
                for _ in range(500):
                    related._wait(0.01)
                    related._step("neighbours", 1, 3)
            except related.Cancelled:
                stopped.set()
                raise
            return {"origin": {}, "nodes": []}
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        with mock.patch.object(related, "graph", side_effect=slow):
            view = client.post("/api/related", json={"query": "0806.2370", "refresh": True}).json()
            self.assertTrue(reached.wait(2))
            self.assertEqual(client.post("/api/related/cancel", json={"id": "0806.2370"}).json()["state"], "cancelled")
            self.assertTrue(stopped.wait(2))
        self.assertEqual(client.get("/api/related/status", params={"id": view["id"]}).json()["state"], "cancelled")
        self.assertIsNone(related._cached("0806.2370"))


class SideLists(unittest.TestCase):
    """Proximité rapportée à la taille des listes ; travaux antérieurs (cités par le graphe) et dérivés (le citent)."""

    def test_prior_and_derivative(self):
        def p(i, rel, cites, year):
            return {"id": i, "arxivId": None, "doi": None, "title": f"Paper about topic {i}", "year": year, "citations": cites, "authors": ["A. B"], "rel": rel}
        origin = {"id": "arXiv:1", "arxivId": "1", "doi": None, "title": "Origin", "year": 2001, "citations": 3, "authors": [], "refs": ["A", "B", "K"]}
        candidates = {"A": p("A", "ref", 50, 1990), "B": p("B", "ref", 20, 1995), "K": p("K", "ref", 1000, 1970),
                      "C": p("C", "cite", 5, 2005), "D": p("D", "cite", 5, 2006), "E": p("E", "cite", 2, 2010)}
        refs = {"A": ["K"], "B": ["K", "A"], "K": [], "C": ["A", "B", "K"], "D": ["A", "B", "K", "C"], "E": ["C", "D"]}
        with mock.patch.object(related, "NODES", 3):
            data = related.compute(origin, candidates, refs)
        self.assertEqual([n["id"] for n in data["nodes"]], ["C", "B", "D"])
        self.assertEqual([(x["id"], sorted(x["links"])) for x in data["prior"]], [("K", ["B", "C", "D", "origin"]), ("A", ["B", "C", "D", "origin"])])
        self.assertEqual([(x["id"], sorted(x["links"])) for x in data["derivative"]], [("E", ["C", "D", "origin"])])
        self.assertNotIn("refs", data["origin"])

    def test_all_links_kept_strongest_drawn(self):
        # tous les liens sont rendus ; chaque article en a au moins quatre dessinés d'emblée (ses plus forts), pas tous
        def p(i, rel):
            return {"id": i, "arxivId": None, "doi": None, "title": f"Paper about topic {i}", "year": 2000, "citations": 5, "authors": ["A. B"], "rel": rel}
        ids = "ABCDEFG"
        origin = {"id": "arXiv:1", "arxivId": "1", "doi": None, "title": "Origin", "year": 2001, "citations": 3, "authors": [], "refs": ["R1", "R2", "R3"]}
        shared = [f"S{k}" for k in range(9)]
        refs = {"A": ["R1", "R2", "R3", *shared], "B": ["R1", *shared[:8]], "C": ["R1", *shared[:7]], "D": ["R1", *shared[:6]],
                "E": ["R1", *shared[:5]], "F": ["R1", *shared[:3]], "G": ["R2"]}
        with mock.patch.object(related, "NODES", 7):
            data = related.compute(origin, {i: p(i, "ref") for i in ids}, refs)
        a_links = {(a if b == "A" else b) for a, b, *_ in data["edges"] if "A" in (a, b)}
        self.assertEqual(a_links, {"B", "C", "D", "E", "F"})
        for node in "ABCDEF":
            self.assertGreaterEqual(sum(drawn for a, b, _, drawn in data["edges"] if node in (a, b)), 4)
        self.assertTrue(any(not drawn for *_, drawn in data["edges"]))  # gardé mais pas dessiné d'emblée


class Merge(unittest.TestCase):
    """Doublons regroupés : les citations faites à une fiche écartée comptent pour celle gardée."""

    def test_citations_of_a_duplicate_count(self):
        def p(i, rel, title, arxiv=None):
            return {"id": i, "arxivId": arxiv, "doi": None, "title": title, "year": 1993, "citations": 10, "authors": ["M. Bordemann"], "rel": rel}
        origin = {"id": "arXiv:1", "arxivId": "1", "doi": None, "title": "Origin paper about something", "year": 2008, "citations": 2,
                  "authors": [], "refs": ["B", "Bdup"]}
        candidates = {"B": p("B", "ref", "Toeplitz quantization of Kahler manifolds and gl(N)", "hep-th/9309134"),
                      "Bdup": p("Bdup", "second", "Toeplitz quantization of Kahler manifolds anggl(N)"),
                      "C": p("C", "cite", "A paper citing the origin and the duplicate"), "D": p("D", "cite", "Another paper citing the origin")}
        refs = {"B": [], "Bdup": [], "C": ["Bdup"], "D": ["B", "Bdup"]}
        data = related.compute(origin, candidates, refs)
        b = next(n for n in data["nodes"] if n["id"] == "B")
        self.assertEqual((b["cocited"], b["rel"]), (2, "ref"))  # C cite la fiche écartée, D les deux : comptés une fois chacun
        self.assertNotIn("Bdup", [n["id"] for n in data["nodes"]])


class Duplicates(unittest.TestCase):
    """Deux fiches du même article sont fusionnées, mais pas les parties I et II d'un article."""

    def test_same(self):
        a = {"title": "Toeplitz operators on symplectic manifolds I", "year": 2008, "arxivId": None, "doi": None}
        self.assertFalse(related._same(a, {**a, "title": "Toeplitz operators on symplectic manifolds II"}))
        self.assertFalse(related._same({**a, "title": "Bergman kernels on singular spaces, part 1"}, {**a, "title": "Bergman kernels on singular spaces, part 2"}))
        self.assertTrue(related._same({**a, "title": "Toeplitz quantization of Kahler manifolds and gl(N)"},
                                      {**a, "title": "Toeplitz quantization of Kahler manifolds anggl(N)"}))
        self.assertFalse(related._same({**a, "arxivId": "0801.0001"}, {**a, "arxivId": "0801.0002"}))
        self.assertFalse(related._same({**a, "title": "Toeplitz operators on symplectic manifolds"}, {**a, "title": "Toeplitz operators on symplectic manifolds: part II"}))
        self.assertFalse(related._same({**a, "title": "Quantization of Kähler manifolds. III"}, {**a, "title": "Quantization of Kähler manifolds. IV"}))
        # même titre suivi d'un complément, ou numéro qui n'en est pas un (« N ! 1 », mal lu)
        book = {**a, "authors": ["L. Boutet de Monvel", "V. Guillemin"], "year": 1981}
        self.assertTrue(related._same({**book, "title": "The Spectral Theory of Toeplitz Operators."},
                                      {**book, "title": "The Spectral Theory of Toeplitz Operators. (AM-99), Volume 99"}))
        # pas des doublons : autre dimension ou autre groupe, parties en minuscules, erratum, titres courts ou réduits à rien,
        # même titre d'autres auteurs
        for x, y in (("The 2-dimensional Ising model and its phases", "The 3-dimensional Ising model and its phases"),
                     ("Gauge theories with gauge group SU(2) on the lattice", "Gauge theories with gauge group SU(3) on the lattice"),
                     ("Bergman kernels on singular spaces, part ii", "Bergman kernels on singular spaces, part iii"),
                     ("Bergman kernels on singular spaces: erratum", "Bergman kernels on singular spaces"),
                     ("Preface", "Preface"), ("Формула следа для магнитного лапласиана", "Квантование по Березину")):
            self.assertFalse(related._same({**a, "title": x}, {**a, "title": y}), (x, y))
        self.assertFalse(related._same({**a, "title": "Introduction to Berezin-Toeplitz quantization", "authors": ["X. Ma"]},
                                       {**a, "title": "Introduction to Berezin-Toeplitz quantization", "authors": ["Other Person"]}))
        self.assertTrue(related._same({**a, "title": "Toeplitz quantization of Kähler manifolds and $gl(N)$, $N\\to\\infty$ limits"},
                                      {**a, "title": "Toeplitz quantization of Kähler manifolds anggl(N), N→∞ limits"}))
        self.assertTrue(related._same({**a, "title": "Toeplitz quantization of Kähler manifolds anggl(N), N→∞ limits"},
                                      {**a, "title": "Toeplitz Quantization of K Ahler Manifolds and Gl(n), N ! 1 Limits"}))
        self.assertEqual(related.normalize("math.DG/0404494"), "math/0404494")
        self.assertEqual(related.normalize("0806.2370"), "0806.2370")


class ArxivTitles(unittest.TestCase):
    """Titres d'arXiv : identifiants anciens et nouveaux, version retirée, entrées d'erreur ignorées."""

    def test_parse(self):
        from app import arxiv
        feed = """<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><id>http://arxiv.org/abs/hep-th/9309134v1</id><title>Toeplitz quantization
            of K\u00e4hler manifolds</title><author><name>Martin Bordemann</name></author></entry>
          <entry><id>http://arxiv.org/abs/1003.2523v3</id><title>A review</title><author><name>M. S.</name></author></entry>
          <entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_x</id><title>Error</title></entry>
        </feed>""".replace("\\u00e4", "ä")
        client = mock.MagicMock()
        client.__enter__.return_value.get.return_value = mock.Mock(status_code=200, text=feed, raise_for_status=lambda: None)
        with mock.patch.object(arxiv, "_client", return_value=client), mock.patch.object(arxiv.time, "sleep"):
            found = arxiv.fetch_titles(["hep-th/9309134", "1003.2523", "x"])
        self.assertEqual(found, {"hep-th/9309134": ("Toeplitz quantization of Kähler manifolds", ["Martin Bordemann"]), "1003.2523": ("A review", ["M. S."])})


if __name__ == "__main__":
    unittest.main()
