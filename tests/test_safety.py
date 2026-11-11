import os
import pathlib
import sqlite3
import tempfile
import unittest

from app import db, safety


class Library(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = self.tmp / "test.db", self.tmp

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def note(self, text):
        with db.connect() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('note', ?)", (f'"{text}"',))

    def test_version_stored(self):
        db.init()
        with db.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], safety.DB_VERSION)

    def test_old_library_migrated(self):  # base d'avant le numéro de version : ouverte, numérotée
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO settings VALUES ('theme', '\"dark\"')")
        conn.commit(); conn.close()
        db.init()
        self.assertEqual(db.get_settings()["theme"], "dark")

    def test_newer_refused_untouched(self):
        db.init()
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute(f"PRAGMA user_version = {safety.DB_VERSION + 1}")
        conn.close()
        before = db.DB_PATH.read_bytes()
        with self.assertRaises(safety.NewerLibrary):
            db.init()
        self.assertEqual(db.DB_PATH.read_bytes(), before)
        self.assertFalse((self.tmp / "backups").exists())

    def test_snapshot_daily_rotation_and_embeddings(self):
        db.init()
        self.note("a")
        with db.connect() as conn:
            conn.execute("INSERT INTO embeddings (key, vector) VALUES ('k', '[1]')")
        first = safety.snapshot(db.DB_PATH, today="2026-01-01")
        self.assertTrue(first.exists())
        self.assertIsNone(safety.snapshot(db.DB_PATH, today="2026-01-01"))  # une seule par jour
        copy = sqlite3.connect(first)
        self.assertEqual(copy.execute("SELECT value FROM settings WHERE key='note'").fetchone()[0], '"a"')
        self.assertEqual(copy.execute("SELECT count(*) FROM sqlite_master WHERE name='embeddings'").fetchone()[0], 0)
        copy.close()
        for day in range(2, 11):
            safety.snapshot(db.DB_PATH, today=f"2026-01-{day:02d}")
        names = [p.name for p in safety._snapshots(db.DB_PATH)]
        self.assertEqual(len(names), 7)
        self.assertEqual(names[0], "arxivreader-2026-01-10.db")
        self.assertEqual(list((self.tmp / "backups").glob("*.tmp")), [])
        status = safety.status(db.DB_PATH)
        self.assertEqual((status["last"], status["count"]), ("2026-01-10", 7))

    def test_corrupt_restored_from_snapshot(self):
        db.init()
        self.note("precious")
        safety.snapshot(db.DB_PATH, today="2026-01-01")
        db.DB_PATH.write_bytes(b"not a database" * 100)
        db.init()
        self.assertEqual(db.get_settings()["note"], "precious")
        report = safety.status(db.DB_PATH)["recovery"]
        self.assertEqual(report["restored"], "arxivreader-2026-01-01.db")
        self.assertTrue(list((self.tmp / "backups").glob("corrupt-*.db")))  # l'abîmée est gardée

    def test_corrupt_without_snapshot_starts_fresh(self):
        db.DB_PATH.write_bytes(b"garbage" * 100)
        db.init()
        self.assertEqual(db.get_settings(), {})
        self.assertIsNone(safety.status(db.DB_PATH)["recovery"]["restored"])
        self.assertTrue(list((self.tmp / "backups").glob("corrupt-*.db")))

    def test_newer_snapshot_not_restored(self):
        db.init()
        safety.snapshot(db.DB_PATH, today="2026-01-01")
        snap = sqlite3.connect(safety._snapshots(db.DB_PATH)[0])
        snap.execute(f"PRAGMA user_version = {safety.DB_VERSION + 1}")
        snap.close()
        db.DB_PATH.write_bytes(b"garbage" * 100)
        db.init()
        self.assertIsNone(safety.status(db.DB_PATH)["recovery"]["restored"])
