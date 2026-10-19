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

        def fake(windows=False):
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



class Install(unittest.TestCase):
    """Mac (.app), Windows (empaquetée) ou code source : la pastille ne propose pas de .dmg à qui fait « git pull »."""

    def test_kinds(self):
        self.assertEqual(updates.install_kind("/Applications/arXivReader.app/Contents/Resources/arxivreader", False), "mac-app")
        self.assertEqual(updates.install_kind(r"C:\arXivReader\server\_internal", True), "windows")
        self.assertEqual(updates.install_kind("/home/me/arxivreader", False), "source")

    def test_source_install_gets_no_download(self):
        saved = (db.DB_PATH, db.LIBRARY, updates.fetch, updates.VERSION, updates.INSTALL)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        asked = []
        updates.fetch = lambda windows=False: asked.append(windows) or {"version": "9.0.0", "url": "https://x/v9.0.0",
                                                                        "download": "https://x/arXivReader.dmg", "notes": "", "published": None}
        updates.VERSION, updates.INSTALL = "1.0.0", "source"
        try:
            state = updates.state(force=True)
            self.assertEqual((state["install"], state["latest"]["download"], asked), ("source", None, [False]))
        finally:
            db.DB_PATH, db.LIBRARY, updates.fetch, updates.VERSION, updates.INSTALL = saved


class Releases(unittest.TestCase):
    """Une version GitHub par numéro (« latest ») : le Mac y prend le .dmg, Windows le .zip (joint après le .dmg)."""

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
        release = {"tag_name": "v1.9.22", "html_url": "https://x/v1.9.22",
                   "assets": [{"name": "arXivReader.dmg", "browser_download_url": "https://x/arXivReader.dmg"},
                              {"name": "arXivReader-windows.zip", "browser_download_url": "https://x/arXivReader-windows.zip"}]}
        self.answer(release)
        latest = updates.fetch(windows=True)
        self.assertEqual((latest["version"], latest["download"]), ("1.9.22", "https://x/arXivReader-windows.zip"))
        self.assertTrue(self.urls[0].endswith("/releases/latest"))
        self.answer({**release, "assets": release["assets"][:1]})  # zip pas encore joint : rien d'annoncé pour l'instant
        self.assertIsNone(updates.fetch(windows=True))
        self.assertEqual(updates.fetch(windows=False)["download"], "https://x/arXivReader.dmg")  # le Mac, déjà oui

if __name__ == "__main__":
    unittest.main()
