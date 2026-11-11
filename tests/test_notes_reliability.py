"""Notes d'un ordinateur à l'autre sans perte : images des zones du PDF dans l'archive, suppressions portées par
l'export (la modification la plus récente l'emporte), une seule ligne par énoncé, texte des notes sans faux titres."""

import io
import json
import pathlib
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

from app import annotations, db, export, library, notesync

PNG = b"\x89PNG\r\n\x1a\nzone"
PID = "0806.2370"
SPAN = {"start": {"id": "S1.p1", "offset": 0}, "end": {"id": "S1.p1", "offset": 3}}


def new_library(test):
    """Un « ordinateur » : base, dossier et clé de signature à lui (les précédents sont remis à la fin du test)."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    db.DB_PATH, db.LIBRARY, library.PAPERS = tmp / "test.db", tmp, tmp / "papers"
    db.init()
    db.save_paper({"id": PID, "arxiv_id": PID, "version": 2, "title": "Toeplitz operators",
                   "authors": ["Xiaonan Ma"], "abstract": "", "categories": []})
    test.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    return tmp


class Reliability(unittest.TestCase):
    def setUp(self):
        saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.addCleanup(lambda: (setattr(db, "DB_PATH", saved[0]), setattr(db, "LIBRARY", saved[1]), setattr(library, "PAPERS", saved[2])))
        new_library(self)

    def send(self, data, name="notes.md"):
        """Import sur l'ordinateur courant (appareil accepté d'emblée)."""
        first = notesync.import_files([{"name": name, "data": data}])
        if first["untrusted"]:
            return notesync.import_files([{"name": name, "data": data}], accept=set(first["untrusted"]))
        return first

    def export_md(self):
        return export.paper_markdown(PID, "fr").encode()

    def test_zone_image_survives_the_archive(self):
        zone = annotations.create(PID, "highlight", target={"pdf": {"page": 2, "rect": [0.1, 0.2, 0.5, 0.4]}}, note="Schéma", image=PNG)
        content, name, media = export.paper_export(PID, "fr")
        self.assertEqual(media, "application/zip")
        self.assertIn(f"-zone-{zone['uid']}.png", " ".join(zipfile.ZipFile(io.BytesIO(content)).namelist()))
        new_library(self)  # autre ordinateur
        self.assertEqual(self.send(content, name)["added"], 1)
        item = annotations.list_for(PID)[0]
        self.assertTrue(item["image"])
        self.assertEqual(annotations.image_path(item).read_bytes(), PNG)
        # image manquante : remise même si la note est déjà là
        annotations.image_path(item).unlink()
        self.assertEqual(self.send(content, name)["unchanged"], 1)
        self.assertTrue(annotations.list_for(PID)[0]["image"])

    def test_bad_or_old_images_are_ignored(self):
        annotations.create(PID, "highlight", target={"pdf": {"page": 2, "rect": [0.1, 0.2, 0.5, 0.4]}}, image=PNG)
        md = self.export_md()
        new_library(self)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("n.md", md)
            archive.writestr("images/0806.2370-zone-3.png", PNG)  # ancien nom (numéro local) : ignoré
            archive.writestr(f"images/x-zone-{'a' * 32}.png", b"not a png")
        self.assertEqual(self.send(buffer.getvalue(), "n.zip")["added"], 1)
        self.assertFalse(annotations.list_for(PID)[0]["image"])

    def test_deletion_travels_and_newer_edit_wins(self):
        keep = annotations.create(PID, "highlight", SPAN, "yellow", "abc")
        gone = annotations.create(PID, "highlight", {"start": {"id": "S1.p2", "offset": 0}, "end": {"id": "S1.p2", "offset": 3}}, "green", "def")
        edited = annotations.create(PID, "block", {"id": "S1.Thm1"}, note="v1")
        before = self.export_md()  # A avant toute suppression
        new_library(self)  # B reçoit, puis supprime
        self.send(before)
        for note in annotations.list_for(PID):
            if note["uid"] in (gone["uid"], edited["uid"]):
                annotations.delete(note["id"])
        after = self.export_md()
        self.assertIn('"deleted"', after.decode())
        self.assertEqual(self.send(before)["added"], 0)  # B reprend l'ancien export de A : rien ne revient
        self.assertEqual({a["uid"] for a in annotations.list_for(PID)}, {keep["uid"]})
        # retour à A, qui a l'ancien état ; « edited » y a été modifiée après la suppression faite sur B
        new_library(self)
        self.send(before)
        with db.connect() as connection:
            connection.execute("UPDATE annotations SET note = 'v2', updated_at = '2999-01-01 00:00:00' WHERE kind = 'block'")
        self.assertEqual(self.send(after)["deleted"], 1)
        uids = {a["uid"]: a for a in annotations.list_for(PID)}
        self.assertNotIn(gone["uid"], uids)
        self.assertEqual(uids[edited["uid"]]["note"], "v2")
        self.assertIn(keep["uid"], uids)

    def test_note_edited_after_deletion_comes_back(self):
        note = annotations.create(PID, "block", {"id": "S1.Thm1"}, note="v1")
        old = self.export_md()
        annotations.delete(note["id"])
        self.assertEqual(self.send(old)["added"], 0)  # plus ancienne que la suppression : ne revient pas
        item = {"uid": note["uid"], "kind": "block", "target": {"id": "S1.Thm1"}, "note": "v2", "updated_at": "2999-01-01 00:00:00"}
        self.assertEqual(annotations.import_one(PID, item), "added")  # modifiée ailleurs après : elle revient
        self.assertEqual(annotations.deletions_for(PID), [])

    def test_undo_removes_the_trace(self):
        note = annotations.create(PID, "highlight", SPAN, "yellow", "abc")
        annotations.delete(note["id"])
        self.assertEqual([d["uid"] for d in annotations.deletions_for(PID)], [note["uid"]])
        annotations.create(PID, "highlight", SPAN, "yellow", "abc", uid=note["uid"])
        self.assertEqual(annotations.deletions_for(PID), [])

    def test_deletions_only_export_and_cleanup(self):
        note = annotations.create(PID, "block", {"id": "S1.Thm1"}, note="x")
        annotations.delete(note["id"])
        self.assertIn("arxivreader-data", export.paper_markdown(PID, "fr"))  # plus aucune note, mais la trace part
        with db.connect() as connection:
            connection.execute("UPDATE annotation_deletions SET deleted_at = '2000-01-01 00:00:00'")
        other = annotations.create(PID, "block", {"id": "S1.Thm2"}, note="y")
        annotations.delete(other["id"])
        self.assertEqual([d["uid"] for d in annotations.deletions_for(PID)], [other["uid"]])  # la trace de l'an 2000 est oubliée
        annotations.delete_for(PID)
        self.assertEqual(annotations.deletions_for(PID), [])

    def test_uid_of_another_paper_is_left_alone(self):
        db.save_paper({"id": "1111.1111", "arxiv_id": "1111.1111", "version": 1, "title": "Autre", "authors": [], "abstract": "", "categories": []})
        mine = annotations.create(PID, "highlight", SPAN, "yellow", "abc")
        item = {"uid": mine["uid"], "kind": "highlight", "target": mine["target"], "quote": "zzz", "note": "autre",
                "updated_at": "2999-01-01 00:00:00"}
        self.assertEqual(annotations.import_one("1111.1111", item), "unchanged")
        self.assertEqual(annotations.get(mine["id"])["quote"], "abc")
        self.assertEqual(annotations.list_for("1111.1111"), [])

    def test_one_row_per_statement(self):
        target = {"id": "S1.Thm1"}
        first = annotations.create(PID, "block", target, note="a")
        second = annotations.create(PID, "block", target, state="review")
        self.assertEqual(first["id"], second["id"])
        with db.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):  # deux requêtes simultanées : la seconde est refusée
                connection.execute("INSERT INTO annotations (paper_id, kind, target, uid) VALUES (?, 'block', ?, 'u2')", (PID, json.dumps(target)))
        annotations.create(PID, "general", note="fiche")
        with db.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO annotations (paper_id, kind, uid) VALUES (?, 'general', 'u3')", (PID,))
        annotations.set_proof(PID, "S1.Thm1", "section", ref="S3")
        annotations.set_proof(PID, "S1.Thm1", "section", ref="S4")
        self.assertEqual(len([a for a in annotations.list_for(PID) if a["kind"] == "proof"]), 1)

    def test_duplicates_merged_once_keeping_the_note(self):
        with db.connect() as connection:
            connection.execute("DROP INDEX annotations_single")
            connection.execute("DROP INDEX annotations_general")
            for uid, note, when in (("d1", None, "2026-02-01 00:00:00"), ("d2", "la note", "2026-01-01 00:00:00"), ("d3", None, "2026-03-01 00:00:00")):
                connection.execute("INSERT INTO annotations (paper_id, kind, target, note, uid, updated_at) VALUES (?, 'block', ?, ?, ?, ?)",
                                   (PID, json.dumps({"id": "S1.Thm1"}), note, uid, when))
            connection.execute("INSERT INTO annotations (paper_id, kind, note, uid) VALUES (?, 'general', 'fiche', 'g1')", (PID,))
            connection.execute("INSERT INTO annotations (paper_id, kind, note, uid) VALUES (?, 'general', NULL, 'g2')", (PID,))
            # deux lignes remplies pour un même énoncé : aucune des deux notes ni l'état ne doit disparaître
            connection.execute("INSERT INTO annotations (paper_id, kind, target, note, state, uid, created_at, updated_at) "
                               "VALUES (?, 'block', ?, 'note A', 'understood', 'e1', '2026-01-01 00:00:00', '2026-01-02 00:00:00')",
                               (PID, json.dumps({"id": "S1.Thm2"})))
            connection.execute("INSERT INTO annotations (paper_id, kind, target, note, uid, created_at, updated_at) "
                               "VALUES (?, 'block', ?, 'note B', 'e2', '2026-02-01 00:00:00', '2026-02-02 00:00:00')",
                               (PID, json.dumps({"id": "S1.Thm2"})))
        db.init()
        left = {a["uid"]: a for a in annotations.list_for(PID)}
        self.assertEqual(sorted(left), ["d2", "e2", "g1"])
        self.assertEqual(left["e2"]["note"], "note B\n\nnote A")  # la plus récente gardée, l'autre note à la suite
        self.assertEqual(left["e2"]["state"], "understood")
        self.assertEqual(left["e2"]["created_at"], "2026-01-01 00:00:00")
        db.init()  # deuxième démarrage : rien à refaire
        self.assertEqual(len(annotations.list_for(PID)), 3)

    def test_markdown_does_not_get_fake_headings(self):
        note = "Intro\n# Titre\n  ## Autre\nsous-titre\n===\n```\n# code\n```\nFin"
        annotations.create(PID, "general", note=note)
        text = export.paper_markdown(PID, "fr")
        body = text.split("<!-- arxivreader-data")[0]
        self.assertIn("\\# Titre", body)
        self.assertIn("  \\## Autre", body)
        self.assertIn("\\===", body)
        self.assertIn("\n# code\n", body)  # dans un bloc de code : intact
        self.assertEqual([line for line in body.splitlines() if line.startswith("# ")], ["# Toeplitz operators", "# code"])
        # l'aller-retour est exact : l'import lit le bloc de données, qui garde le texte tel quel
        new_library(self)
        self.send(text.encode())
        self.assertEqual(annotations.list_for(PID)[0]["note"], note)

    def test_deleted_key_is_signed_and_optional(self):
        note = annotations.create(PID, "block", {"id": "S1.Thm1"}, note="x")
        annotations.delete(note["id"])
        text = export.paper_markdown(PID, "fr")
        payload = notesync._verify(text)[0]
        self.assertEqual(([d["uid"] for d in payload["deleted"]], payload["format"]), ([note["uid"]], 1))
        with self.assertRaises(notesync.ImportError_):  # clé modifiée à la main : signature refusée
            notesync._verify(text.replace('"deleted"', '"deletedx"'))
        annotations.create(PID, "general", note="fiche")  # sans suppression : pas de clé (même format qu'avant)
        with db.connect() as connection:
            connection.execute("DELETE FROM annotation_deletions")
        self.assertNotIn("deleted", notesync._verify(export.paper_markdown(PID, "fr"))[0])


if __name__ == "__main__":
    unittest.main()
