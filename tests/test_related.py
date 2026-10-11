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


# l'article cite R1, R2, R3 ; il est cité par C1, C2 ; R1 et R1bis sont deux fiches du même article
REFS = [{"citedPaper": paper("R1", "Toeplitz quantization of Kahler manifolds and gl(N)", 1993, 300, "hep-th/9309134")},
        {"citedPaper": paper("R1bis", "Toeplitz quantization of Kahler manifolds anggl(N)", 1993, 200)},
        {"citedPaper": paper("R2", "Bergman kernels", 2004, 150, "math/0404494")},
        {"citedPaper": paper("R3", "Unrelated topic", 1970, 5)}]
CITES = [{"citingPaper": paper("C1", "Berezin-Toeplitz quantization", 2010, 50, "1003.2523")},
         {"citingPaper": paper("C2", "Later work", 2020, 3)}]
BATCH = {"R1": ["X"], "R1bis": ["X"], "R2": ["R1"], "R3": [], "C1": ["R1", "R2", "R1bis"], "C2": ["R1", "R2", "C1"]}


class Related(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        self.calls = []

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
        return mock.Mock(status_code=200, json=lambda: [{"paperId": i, "references": [{"paperId": r} for r in BATCH[i]]} for i in body["ids"]],
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
                self.last = (url, "post", json)

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
        self.assertTrue(any({a, b} == {"C1", "C2"} or {a, b} == {"C2", "C1"} for a, b, _ in data["edges"]))  # C2 cite C1
        n = len(self.calls)
        again = self.run_build()                    # gardé : pas de nouvelle requête
        self.assertEqual(len(self.calls), n)
        self.assertEqual(again["nodes"][0]["id"], "C1")

    def test_route(self):
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        self.assertEqual(client.post("/api/related", json={"query": "pas un identifiant"}).status_code, 400)
        with mock.patch.object(related, "graph", side_effect=related.RelatedError("rate-limited")):
            response = client.post("/api/related", json={"query": "arXiv:0806.2370"})
        self.assertEqual((response.status_code, response.json()["code"]), (429, "rate-limited"))


if __name__ == "__main__":
    unittest.main()
