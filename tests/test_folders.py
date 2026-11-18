"""Dossiers de la bibliothèque : création, noms, rangement, suppression et annulation, articles effacés."""

import pathlib
import tempfile
import threading
import unittest

from fastapi.testclient import TestClient

from app import db, folders, library, main

BASE = f"http://{main.HOST}:{main.PORT}"


def paper(paper_id):
    return {"id": paper_id, "arxiv_id": paper_id, "version": 1, "title": f"Paper {paper_id}", "authors": ["A. Author"],
            "abstract": "", "categories": ["math.DG"], "primary_category": "math.DG", "published": "2026-01-01"}


class Folders(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        for paper_id in ("2601.00001", "2601.00002"):
            db.save_paper(paper(paper_id))
        self.client = TestClient(main.app, base_url=BASE)

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def test_create_rename_and_names(self):
        created = self.client.post("/api/folders", json={"name": "  Noyaux   de Bergman "}).json()
        self.assertEqual((created["name"], created["papers"]), ("Noyaux de Bergman", []))
        self.assertEqual(self.client.post("/api/folders", json={"name": "noyaux de bergman"}).status_code, 409)
        self.assertEqual(self.client.post("/api/folders", json={"name": "  "}).status_code, 400)
        self.assertEqual(self.client.post("/api/folders", json={"name": "x" * 81}).status_code, 400)
        self.client.post("/api/folders", json={"name": "À relire"})
        renamed = self.client.put(f"/api/folders/{created['id']}", json={"name": "Bergman"}).json()
        self.assertEqual(renamed["name"], "Bergman")
        self.assertEqual([f["name"] for f in self.client.get("/api/folders").json()], ["À relire", "Bergman"])
        self.assertEqual(self.client.put("/api/folders/999", json={"name": "Autre"}).status_code, 404)

    def test_members_delete_and_undo(self):
        folder = self.client.post("/api/folders", json={"name": "Toeplitz"}).json()
        url = f"/api/folders/{folder['id']}/papers"
        self.assertEqual(self.client.put(f"{url}/2601.00001", json={}).json()["papers"], ["2601.00001"])
        self.client.put(f"{url}/2601.00002", json={})
        self.client.put(f"{url}/2601.00002", json={})  # deux fois : une seule entrée
        self.assertEqual(self.client.delete(f"{url}/2601.00001").json()["papers"], ["2601.00002"])
        self.assertEqual(self.client.put(f"{url}/2699.99999", json={}).status_code, 404)
        removed = self.client.delete(f"/api/folders/{folder['id']}").json()
        self.assertEqual(self.client.get("/api/folders").json(), [])
        self.assertIsNotNone(db.get_paper("2601.00002"))  # l'article reste
        restored = self.client.post("/api/folders", json={"name": removed["name"], "papers": removed["papers"]}).json()
        self.assertEqual(restored["papers"], ["2601.00002"])

    def test_erasing_waits_for_the_paper_lock(self):
        done = threading.Event()
        with library.paper_lock("2601.00001"):  # un import de cet article est en cours
            thread = threading.Thread(target=lambda: (library.remove_paper("2601.00001"), done.set()))
            thread.start()
            self.assertFalse(done.wait(0.3))
            self.assertIsNotNone(db.get_paper("2601.00001"))
        thread.join(5)
        self.assertTrue(done.is_set())
        self.assertIsNone(db.get_paper("2601.00001"))

    def test_erased_paper_leaves_folders(self):
        folder = folders.create("Toeplitz", ["2601.00001", "2601.00002"])
        library.remove_paper("2601.00001")
        self.assertEqual(folders.get(folder["id"])["papers"], ["2601.00002"])


if __name__ == "__main__":
    unittest.main()
