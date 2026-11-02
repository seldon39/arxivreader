"""Jeton de session de l'API, clés d'API dans le trousseau (simulé ici : jamais celui de l'utilisateur), restes de
l'ancienne connexion à Gemini."""

import json
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

from fastapi.testclient import TestClient

from app import db, keystore, main, providers


class Token(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = self.tmp / "test.db", self.tmp
        db.init()
        patch = unittest.mock.patch.object(main, "TOKEN", "s3cret")
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def test_api_needs_the_session_token(self):
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        self.assertEqual(client.get("/api/settings").status_code, 403)  # autre programme de l'ordinateur
        self.assertEqual(client.get("/").status_code, 200)  # page et fichiers statiques : rien de privé
        self.assertEqual(client.get("/?t=wrong", follow_redirects=False).status_code, 403)
        self.assertEqual(client.get("/?t=fauxé", follow_redirects=False).status_code, 403)  # non ASCII : refusé, pas d'erreur
        response = client.get("/?t=s3cret", follow_redirects=False)
        self.assertEqual((response.status_code, response.headers["location"]), (303, "/"))
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(client.get("/api/settings").status_code, 200)  # cookie gardé par le client, comme la page
        bare = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        self.assertEqual(bare.get("/api/settings", headers={"X-ArXivReader-Token": "s3cret"}).status_code, 200)  # fenêtres
        self.assertEqual(bare.get("/api/settings", headers={"X-ArXivReader-Token": "nope"}).status_code, 403)


class Keychain(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = self.tmp / "test.db", self.tmp
        self.store = {}
        self.refuse = False

        def put(key, value):
            if self.refuse:
                raise keystore.KeystoreError("locked")
            if value:
                self.store[key] = value
            else:
                self.store.pop(key, None)
        for patch in (unittest.mock.patch.object(keystore, "ENABLED", True),
                      unittest.mock.patch.object(keystore, "get", lambda key: self.store.get(key)),
                      unittest.mock.patch.object(keystore, "put", put),
                      unittest.mock.patch.object(db, "_cache", None)):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def stored_in_db(self):
        with db.connect() as connection:
            return {row["key"]: json.loads(row["value"]) for row in connection.execute("SELECT key, value FROM settings")}

    def test_keys_live_in_the_keychain(self):
        db.init()
        db.save_settings({"geminiApiKey": "AIza-key", "language": "fr"})
        self.assertEqual(self.store, {"geminiApiKey": "AIza-key"})
        self.assertEqual(self.stored_in_db(), {"language": "fr"})  # la base ne la garde pas
        self.assertEqual(db.get_settings()["geminiApiKey"], "AIza-key")
        db.save_settings({"geminiApiKey": ""})  # clé retirée
        self.assertEqual(self.store, {})
        self.assertNotIn("geminiApiKey", db.get_settings())

    def test_keys_of_an_older_version_are_moved(self):
        db.init()
        with db.connect() as connection:  # base d'une version précédente : clés en clair
            connection.executemany("INSERT INTO settings (key, value) VALUES (?, ?)",
                                   [("s2ApiKey", json.dumps("s2-key")), ("zoteroApiKey", json.dumps("")), ("theme", json.dumps("dark"))])
        db.init()
        self.assertEqual(self.store, {"s2ApiKey": "s2-key"})
        self.assertEqual(self.stored_in_db(), {"theme": "dark"})
        self.assertEqual(db.get_settings()["s2ApiKey"], "s2-key")

    def test_database_when_the_keychain_refuses(self):
        db.init()
        self.refuse = True
        db.save_settings({"zoteroApiKey": "zot-key"})
        self.assertEqual(self.stored_in_db(), {"zoteroApiKey": "zot-key"})  # rien de perdu
        self.assertEqual(db.get_settings()["zoteroApiKey"], "zot-key")
        self.refuse = False
        db.save_settings({"geminiApiKey": "old"})  # dans le trousseau (et en mémoire)
        self.refuse = True
        db.save_settings({"geminiApiKey": "new"})  # refusée par le trousseau : la nouvelle, de la base, sert
        self.assertEqual(db.get_settings()["geminiApiKey"], "new")
        self.refuse = False
        db.init()  # trousseau de nouveau disponible : les clés y passent au démarrage suivant
        self.assertEqual(self.store, {"zoteroApiKey": "zot-key", "geminiApiKey": "new"})
        self.assertEqual(self.stored_in_db(), {})

    @unittest.skipUnless(sys.platform == "win32", "gestionnaire d'identifiants de Windows")
    def test_windows_credential_manager(self):
        with unittest.mock.patch.object(keystore, "LIBRARY", self.tmp):  # entrée propre à ce dossier temporaire
            unittest.mock.patch.stopall()  # vrai gestionnaire, pas la simulation
            try:
                keystore.put("geminiApiKey", "test-value-é")
                self.assertEqual(keystore.get("geminiApiKey"), "test-value-é")
                keystore.put("geminiApiKey", "")
                self.assertIsNone(keystore.get("geminiApiKey"))
            finally:
                keystore.put("geminiApiKey", "")


class GeminiLeftovers(unittest.TestCase):
    def test_old_cli_files_are_removed(self):
        library = pathlib.Path(tempfile.mkdtemp())
        folder = library / "ai"
        (folder / "gemini").mkdir(parents=True)
        for name in ("setup-gemini.command", "gemini-settings.json", "gemini-system.md", "setup-codex.command"):
            (folder / name).write_text("x")
        with unittest.mock.patch.object(providers, "LIBRARY", library):
            providers.forget_gemini_cli()
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["setup-codex.command"])  # le reste est gardé
