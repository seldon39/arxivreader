"""Mises à jour : comparaison des versions, vérification au plus une fois par jour (GitHub simulé)."""

import pathlib
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from app import db, main, updates


class Updates(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, updates.fetch, updates.VERSION)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = self.tmp / "test.db", self.tmp
        db.init()
        self.calls = 0

        def fake():
            self.calls += 1
            return {"version": "1.2.0", "url": "https://github.com/x/releases/tag/v1.2.0", "download": "https://x/arXivReader.dmg",
                    "notes": "", "published": None}
        updates.fetch = fake
        updates.VERSION = "1.0.0"

    def tearDown(self):
        db.DB_PATH, db.LIBRARY, updates.fetch, updates.VERSION = self.saved

    def test_versions(self):
        self.assertEqual(updates.parse("v1.10.2"), (1, 10, 2))
        self.assertEqual(updates.parse("2.1"), (2, 1, 0))
        self.assertIsNone(updates.parse("latest"))
        self.assertTrue(updates.newer("1.10.0", "1.9.3"))  # pas un tri alphabétique
        self.assertFalse(updates.newer("1.0.0", "1.0.0"))
        self.assertFalse(updates.newer("nightly", "1.0.0"))

    def test_daily_check_and_dismiss(self):
        state = updates.state()
        self.assertTrue(state["available"])
        self.assertEqual(state["latest"]["version"], "1.2.0")
        updates.state()
        self.assertEqual(self.calls, 1)  # une seule requête par jour
        updates.state(force=True)
        self.assertEqual(self.calls, 2)
        db.save_settings({"updates.checked": time.time() - 2 * 86400, "checkUpdates": False})
        updates.state()
        self.assertEqual(self.calls, 2)  # réglage désactivé : pas de requête automatique
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        self.assertTrue(client.post("/api/updates/dismiss", json={"version": "1.2.0"}).json()["dismissed"])
        self.assertEqual(client.post("/api/updates/dismiss", json={"version": "rm -rf"}).status_code, 400)
        updates.VERSION = "1.2.0"
        self.assertFalse(updates.state()["available"])  # déjà à jour



class Releases(unittest.TestCase):
    """Choix de la version publiée : le Mac lit « latest », Windows la plus récente des préversions windows-v…"""

    def setUp(self):
        self.saved = updates.httpx.get

    def tearDown(self):
        updates.httpx.get = self.saved

    def answer(self, body):
        class Response:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return body
        self.urls = []
        updates.httpx.get = lambda url, **kwargs: self.urls.append(url) or Response()

    def test_mac(self):
        self.answer({"tag_name": "v1.9.14", "html_url": "https://x/v1.9.14",
                     "assets": [{"name": "arXivReader.dmg", "browser_download_url": "https://x/arXivReader.dmg"}]})
        latest = updates.fetch(windows=False)
        self.assertEqual((latest["version"], latest["download"]), ("1.9.14", "https://x/arXivReader.dmg"))
        self.assertTrue(self.urls[0].endswith("/releases/latest"))
        self.answer({"tag_name": "windows-v1.9.20", "assets": []})  # préversion Windows promue par erreur : ignorée
        self.assertIsNone(updates.fetch(windows=False))

    def test_windows(self):
        self.answer([{"tag_name": "v1.9.15", "assets": []},
                     {"tag_name": "windows-v1.9.15", "draft": True, "assets": []},
                     {"tag_name": "windows-v1.9.14", "html_url": "https://x/windows-v1.9.14",
                      "assets": [{"name": "arXivReader-windows.zip", "browser_download_url": "https://x/arXivReader-windows.zip"}]}])
        latest = updates.fetch(windows=True)
        self.assertEqual((latest["version"], latest["download"]), ("1.9.14", "https://x/arXivReader-windows.zip"))
        self.answer([{"tag_name": "v1.9.15", "assets": []}])
        self.assertIsNone(updates.fetch(windows=True))


if __name__ == "__main__":
    unittest.main()
