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

    def test_proof_location(self):
        annotations.set_proof("p", "S1.Thm1", "section", ref="S2")
        annotations.set_proof("p", "S1.Thm1", "passage", start="S1.p1")  # remplace le choix précédent
        self.assertEqual(annotations.proof_overrides("p"),
                         {"S1.Thm1": {"id": "S1.Thm1", "mode": "passage", "start": "S1.p1", "end": "S1.p1"}})
        self.assertEqual(annotations.list_for("p")[0]["kind"], "proof")
        annotations.clear_proof("p", "S1.Thm1")
        self.assertEqual(annotations.proof_overrides("p"), {})
        with self.assertRaises(ValueError):
            annotations.set_proof("p", "S1.Thm1", "section")

    def test_author(self):
        mark = annotations.create("p", "highlight", {"start": {"id": "a", "offset": 0}, "end": {"id": "a", "offset": 3}},
                                  "pink", "abc", "point à vérifier", author="claude")
        self.assertEqual(mark["author"], "claude")
        with self.assertRaises(ValueError):
            annotations.create("p", "general", note="x", author="someone")

    def test_validation(self):
        with self.assertRaises(ValueError):
            annotations.create("p", "highlight", color="purple")
        with self.assertRaises(ValueError):
            annotations.create("p", "block", {"id": "x"}, state="maybe")


if __name__ == "__main__":
    unittest.main()

    def test_bookmarks(self):
        html = annotations.create("p", "bookmark", {"id": "S3.p2", "version": 1}, quote="Lemme clé")
        pdf = annotations.create("p", "bookmark", {"pdf": {"page": 4, "top": 512.5}, "version": 1}, quote="Page 4")
        self.assertNotEqual(html["id"], pdf["id"])  # plusieurs marque-pages : jamais fusionnés
        self.assertEqual(annotations.update(html["id"], quote="Lemme 3.2")["quote"], "Lemme 3.2")  # renommé
        self.assertEqual(annotations.update(pdf["id"], version=2)["target"], {"pdf": {"page": 4, "top": 512.5}, "version": 2})
        for bad in [{}, {"pdf": {"page": 0}}, {"pdf": {"page": "2"}}, {"id": ""}]:
            with self.assertRaises(ValueError):
                annotations.create("p", "bookmark", bad, quote="x")
        stats = annotations.stats()["p"]
        self.assertEqual((stats["highlights"], stats["notes"]), (0, 0))  # ni passage surligné ni note

    def test_free_notes(self):
        html = annotations.create("p", "note", {"id": "S2.p3", "version": 1}, note="À comparer avec [BGS2].")
        pdf = annotations.create("p", "note", {"pdf": {"page": 3, "top": 600.5, "left": 72}}, note="Signe ?")
        self.assertNotEqual(html["id"], pdf["id"])
        with self.assertRaises(ValueError):
            annotations.create("p", "note", {"pdf": {"page": 3, "left": "x"}}, note="?")
        self.assertEqual(annotations.stats()["p"]["notes"], 2)
