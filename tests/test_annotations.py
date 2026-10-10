"""Annotations : création, mise à jour des notes d'énoncés et de la fiche de lecture (base temporaire)."""

import pathlib
import tempfile
import unittest

from app import annotations, db


class Annotations(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def test_highlight_and_block_upsert(self):
        h = annotations.create("p", "highlight", {"start": {"id": "a", "offset": 1}, "end": {"id": "a", "offset": 4}},
                               "yellow", "abc")
        self.assertEqual((h["kind"], h["color"], h["target"]["end"]["offset"]), ("highlight", "yellow", 4))
        first = annotations.create("p", "block", {"id": "S1.Thm1"}, state="review")
        second = annotations.create("p", "block", {"id": "S1.Thm1"}, note="Idée **clé**")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual((second["state"], second["note"]), ("review", "Idée **clé**"))
        self.assertIsNone(annotations.update(second["id"], clear_state=True)["state"])
        general = annotations.create("p", "general", note="v1")
        self.assertEqual(annotations.create("p", "general", note="v2")["id"], general["id"])
        self.assertEqual(len(annotations.list_for("p")), 3)

    def test_validation(self):
        with self.assertRaises(ValueError):
            annotations.create("p", "highlight", color="purple")
        with self.assertRaises(ValueError):
            annotations.create("p", "block", {"id": "x"}, state="maybe")


if __name__ == "__main__":
    unittest.main()
