"""Papiers liés : proximité (références communes, co-citations), doublons, mise en cache — Semantic Scholar simulé."""

import pathlib
import tempfile
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

    def run_build(self):
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
             mock.patch.object(related, "fetch_metadata", return_value=meta), mock.patch.object(related.time, "sleep"):
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
        self.assertTrue(any({a, b} == {"C1", "C2"} for a, b, _ in data["edges"]))  # C2 cite C1
        self.assertEqual(self.batches[1], ["S1"])   # second niveau : seul S1 est cité par trois voisins (X par deux)
        s1 = next(n for n in data["nodes"] if n["id"] == "S1")
        self.assertEqual((s1["rel"], s1["coupling"], s1["cocited"]), ("second", 1, 2))
        n = len(self.calls)
        again = self.run_build()                    # gardé : pas de nouvelle requête
        self.assertEqual(len(self.calls), n)
        self.assertEqual(again["nodes"][0]["id"], "C1")

    def test_old_format_rebuilt(self):
        related._ensure()
        with db.connect() as connection:  # graphe gardé par la version 1.6.0 (sans travaux antérieurs ni dérivés)
            connection.execute("INSERT INTO related_graphs (arxiv_id, data) VALUES (?, ?)", ("0806.2370", '{"origin": {}, "nodes": [], "edges": []}'))
        self.assertEqual(self.run_build()["format"], related.FORMAT)

    def test_route(self):
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        self.assertEqual(client.post("/api/related", json={"query": "pas un identifiant"}).status_code, 400)
        with mock.patch.object(related, "graph", side_effect=related.RelatedError("rate-limited")):
            response = client.post("/api/related", json={"query": "arXiv:0806.2370"})
        self.assertEqual((response.status_code, response.json()["code"]), (429, "rate-limited"))


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
        self.assertTrue(related._same({**a, "title": "Toeplitz quantization of Kähler manifolds anggl(N), N→∞ limits"},
                                      {**a, "title": "Toeplitz Quantization of K Ahler Manifolds and Gl(n), N ! 1 Limits"}))
        self.assertEqual(related.normalize("math.DG/0404494"), "math/0404494")
        self.assertEqual(related.normalize("0806.2370"), "0806.2370")


if __name__ == "__main__":
    unittest.main()
