"""Serveur : protections des requêtes, ré-analyse des articles enregistrés, petites fonctions sensibles."""

import gzip
import json
import pathlib
import tempfile
import unittest

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app import db, library, main, relevance, texsource
from app.structure import STRUCTURE_VERSION, extract_structure

BASE = f"http://{main.HOST}:{main.PORT}"


class Requests(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        self.client = TestClient(main.app, base_url=BASE)

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def test_local_only(self):
        self.assertEqual(self.client.get("/api/settings").status_code, 200)
        self.assertEqual(TestClient(main.app, base_url="http://evil.example:8710").get("/api/settings").status_code, 403)
        self.assertEqual(self.client.get("/api/settings", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.get("/api/settings", headers={"Sec-Fetch-Site": "same-origin"}).status_code, 200)
        put = lambda **kw: self.client.put("/api/settings", **kw).status_code
        self.assertEqual(put(json={"theme": "dark"}, headers={"Origin": "http://evil.example"}), 403)
        self.assertEqual(put(content='{"theme": "dark"}', headers={"Content-Type": 'text/plain; x="application/json"'}), 415)
        self.assertEqual(put(json={"theme": "dark"}), 200)

    def test_validation(self):
        self.assertEqual(self.client.put("/api/settings", json={"feedCategories": "math.DG"}).status_code, 400)
        self.assertEqual(self.client.put("/api/settings", json={"feedCategories": ["math.DG", "math-ph"]}).status_code, 200)
        self.assertEqual(self.client.get("/api/feed?day=2026-13-45").status_code, 400)
        self.assertEqual(self.client.get("/api/papers/bad%0A").status_code, 400)
        self.assertEqual(self.client.put("/api/papers/none/proofs/x", json={"mode": "immediate"}).status_code, 404)


class Settings(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        self.client = TestClient(main.app, base_url=BASE)

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def test_values_are_checked(self):
        put = lambda values: self.client.put("/api/settings", json=values).status_code  # noqa: E731
        self.assertEqual(put({"notifyFollowed": "false"}), 400)
        self.assertEqual(put({"notifyFollowed": False, "theme": "dark", "fontSize": 18, "panelWidth": None}), 200)
        self.assertEqual(put({"theme": "purple"}), 400)
        self.assertEqual(put({"s2ApiKey": "clé"}), 400)

    def test_link_only_recommendations(self):
        from app import feed
        with db.connect() as connection:
            connection.execute("INSERT INTO feed_items (key, arxiv_id, title, authors) VALUES ('2601.00001', '2601.00001', 't', '[]')")
        self.assertEqual(self.client.post("/api/feed/arxiv", json={"key": "2601.00001", "query": "2601.00002"}).status_code, 404)
        self.assertEqual(self.client.post("/api/feed/arxiv", json={"key": "s2:none", "query": "2601.00002"}).status_code, 404)
        with self.assertRaises(ValueError):
            feed.link_arxiv("2601.00001", "2601.00002")

    def test_migration_of_an_old_database(self):
        import sqlite3
        with sqlite3.connect(db.DB_PATH) as connection:  # schéma d'avant : sans author ni applied
            connection.execute("DROP TABLE annotations")
            connection.execute("CREATE TABLE annotations (id INTEGER PRIMARY KEY, paper_id TEXT, kind TEXT, target TEXT, "
                               "color TEXT, quote TEXT, note TEXT, state TEXT, created_at TEXT, updated_at TEXT)")
        db.init()
        db.init()
        with db.connect() as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(annotations)")}
        self.assertIn("author", columns)


class Reanalysis(unittest.TestCase):
    def setUp(self):
        self.saved = library.PAPERS
        library.PAPERS = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        library.PAPERS = self.saved

    def test_old_structure_is_reanalysed_with_stable_ids(self):
        html = ('<section id="S1" class="ltx_section"><h2>1. A</h2>'
                '<div id="T1" class="ltx_theorem ltx_theorem_lemma"><h6 class="ltx_title ltx_title_theorem">'
                '<span class="ltx_tag ltx_tag_theorem">Lemma 1.1</span>.</h6><div class="ltx_para"><p>x</p></div></div>'
                '<div class="ltx_proof"><h6 class="ltx_title ltx_title_proof">Proof.</h6><p>y</p></div>'
                '<p id="arx-proof-1">ancien identifiant</p></section>')
        folder = library.paper_dir("p")
        folder.mkdir(parents=True)
        (folder / "content.html").write_text(html, encoding="utf-8")
        (folder / "structure.json").write_text(json.dumps({"version": 0, "nodes": [], "source": "arxiv-html"}), encoding="utf-8")
        structure = library.load_structure("p")
        self.assertEqual(structure["version"], STRUCTURE_VERSION)
        self.assertEqual(structure["source"], "arxiv-html")
        proof_id = structure["nodes"][0]["proofs"][0]
        self.assertNotEqual(proof_id, "arx-proof-1")  # identifiant neuf, jamais un identifiant déjà présent
        saved = (folder / "content.html").read_text(encoding="utf-8")
        self.assertIn(f'id="{proof_id}"', saved)
        again = extract_structure(BeautifulSoup(saved, "html.parser"))
        self.assertEqual(again["nodes"], structure["nodes"])  # seconde analyse : mêmes identifiants
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["content.html", "structure.json"])  # pas de .part


class Helpers(unittest.TestCase):
    def test_s2_key(self):
        self.assertEqual(relevance.s2_key(None, "math/0601001"), "ArXiv:math/0601001")
        self.assertEqual(relevance.s2_key(None, "2606.24503"), "ArXiv:2606.24503")
        self.assertEqual(relevance.s2_key(None, "10.1063/1.4870869"), "DOI:10.1063/1.4870869")
        self.assertEqual(relevance.s2_key(None, "s2:abc"), "abc")
        self.assertIsNone(relevance.s2_key(None, "a book title"))

    def test_bounded_decompression(self):
        bomb = gzip.compress(b"\\documentclass{article}" + b" " * (texsource.MAX_FILE_BYTES + 10))
        self.assertIsNone(texsource.analyze_source(bomb))


if __name__ == "__main__":
    unittest.main()
