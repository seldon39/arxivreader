"""Notes exportées puis réimportées : bloc de données signé, refus des fichiers modifiés ou étrangers, appareils
acceptés, fusion par identifiant stable (la version la plus récente l'emporte), article absent importé."""

import io
import json
import pathlib
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from app import annotations, db, export, library, notesync


def setup_library(test):
    tmp = pathlib.Path(tempfile.mkdtemp())
    db.DB_PATH, db.LIBRARY, library.PAPERS = tmp / "test.db", tmp, tmp / "papers"
    db.init()
    db.save_paper({"id": "0806.2370", "arxiv_id": "0806.2370", "version": 2, "title": "Toeplitz operators",
                   "authors": ["Xiaonan Ma", "George Marinescu"], "abstract": "", "categories": []})
    test.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    return tmp


class NotesSync(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.addCleanup(lambda: setattr(db, "DB_PATH", self.saved[0]) or setattr(db, "LIBRARY", self.saved[1])
                        or setattr(library, "PAPERS", self.saved[2]))
        self.first = setup_library(self)
        pid = "0806.2370"
        annotations.create(pid, "highlight", {"start": {"id": "S2.p1", "offset": 0}, "end": {"id": "S2.p1", "offset": 9}},
                           "green", "More text", "Voir **Dai–Liu–Ma** -->")
        annotations.create(pid, "block", {"id": "S1.Thm1"}, state="review", note="À relire")
        annotations.create(pid, "general", note="Fiche de lecture")
        annotations.create(pid, "bookmark", {"pdf": {"page": 4, "top": 512.5}, "version": 2}, quote="Lemme clé")
        annotations.create(pid, "note", {"id": "S2.p3"}, note="Comparer avec [BGS2].")
        annotations.set_proof(pid, "S1.Thm1", "section", ref="S3")
        self.markdown = export.paper_markdown(pid, "fr")
        self.original = sorted((a["uid"], a["kind"], json.dumps(a["target"], sort_keys=True), a["note"], a["quote"], a["state"],
                                a["color"]) for a in annotations.list_for(pid))

    def file(self, text, name="notes.md"):
        return {"name": name, "data": text.encode("utf-8")}

    def test_round_trip_on_another_device(self):
        self.assertIn("<!-- arxivreader-data", self.markdown)
        self.assertNotIn("-->\n-->", self.markdown)  # « --> » d'une note échappé dans le bloc
        key = (self.first / "notes-signing.key").read_bytes()
        setup_library(self)  # autre ordinateur : autre clé, article déjà là
        first = notesync.import_files([self.file(self.markdown)])
        self.assertEqual(len(first["untrusted"]), 1)  # appareil inconnu : la page demande, rien d'importé
        self.assertEqual(annotations.list_for("0806.2370"), [])
        fingerprint = next(iter(first["untrusted"]))
        report = notesync.import_files([self.file(self.markdown)], accept={fingerprint})
        self.assertEqual((report["added"], report["rejected"], report["untrusted"]), (6, [], {}))
        imported = sorted((a["uid"], a["kind"], json.dumps(a["target"], sort_keys=True), a["note"], a["quote"], a["state"],
                           a["color"]) for a in annotations.list_for("0806.2370"))
        self.assertEqual(imported, self.original)  # à l'identique, identifiants compris
        again = notesync.import_files([self.file(self.markdown)])  # appareil désormais accepté
        self.assertEqual((again["added"], again["updated"], again["unchanged"]), (0, 0, 6))
        self.assertNotEqual((db.LIBRARY / "notes-signing.key").read_bytes(), key)

    def test_rejected_files(self):
        tampered = self.markdown.replace("Fiche de lecture", "Fiche falsifiée")
        report = notesync.import_files([self.file(tampered, "a.md"), self.file("# Mes notes\n\nÉcrites à la main.", "b.md")])
        self.assertEqual([r["reason"] for r in report["rejected"]], ["invalid", "unsigned"])
        self.assertEqual(report["added"] + report["updated"], 0)
        forged = self.markdown.replace(json.loads(notesync.DATA.search(self.markdown).group(1))["signer"]["key"],
                                       "A" * 43 + "=")  # autre clé publique : la signature ne correspond plus
        self.assertEqual(notesync.import_files([self.file(forged)])["rejected"][0]["reason"], "invalid")

    def test_newer_wins_and_zip(self):
        pid = "0806.2370"
        general = next(a for a in annotations.list_for(pid) if a["kind"] == "general")
        with db.connect() as connection:  # notre fiche est plus récente que celle du fichier : gardée
            connection.execute("UPDATE annotations SET note = 'Plus récente', updated_at = '2999-01-01 00:00:00' WHERE id = ?",
                               (general["id"],))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("0806.2370 Toeplitz operators.md", self.markdown)
            archive.writestr("readme.txt", "ignoré")
        report = notesync.import_files([{"name": "notes.zip", "data": buffer.getvalue()}])
        self.assertEqual((report["added"], report["updated"]), (0, 0))
        self.assertEqual(next(a for a in annotations.list_for(pid) if a["kind"] == "general")["note"], "Plus récente")

    def test_missing_paper_imported_from_arxiv(self):
        setup_library(self)
        with db.connect() as connection:
            connection.execute("DELETE FROM papers")
        notesync.trust({notesync.fingerprint(__import__("base64").b64decode(
            json.loads(notesync.DATA.search(self.markdown).group(1))["signer"]["key"])): "Mac"})
        added = []

        def quick_add(arxiv_id, known, in_library):
            added.append((arxiv_id, known["title"], in_library))
            db.save_paper({"id": "0806.2370", "arxiv_id": arxiv_id, "version": None, "title": known["title"],
                           "authors": known["authors"], "abstract": "", "categories": []})
            return db.get_paper("0806.2370")
        with mock.patch.object(library, "quick_add", side_effect=quick_add):
            report = notesync.import_files([self.file(self.markdown)])
        self.assertEqual((added, report["papers"], report["added"]), ([("0806.2370", "Toeplitz operators", True)], 1, 6))


if __name__ == "__main__":
    unittest.main()


class LibraryExport(unittest.TestCase):
    """Archive complète : toute la bibliothèque (même les articles sans notes) et ses dossiers, puis les notes."""

    def test_whole_library_round_trip(self):
        from app import folders
        saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.addCleanup(lambda: setattr(db, "DB_PATH", saved[0]) or setattr(db, "LIBRARY", saved[1]) or setattr(library, "PAPERS", saved[2]))
        setup_library(self)
        db.save_paper({"id": "1203.4201", "arxiv_id": "1203.4201", "version": 1, "title": "Unannotated paper",
                       "authors": ["A. B."], "abstract": "", "categories": []})
        db.save_paper({"id": "pdf-0123456789ab", "arxiv_id": "", "version": None, "title": "My scanned PDF",
                       "authors": [], "abstract": "", "categories": [], "source": "local-pdf"})
        folders.create("Toeplitz", ["0806.2370", "1203.4201"])
        annotations.create("0806.2370", "general", note="Fiche")
        exported = export.library_zip("en")
        archive = zipfile.ZipFile(io.BytesIO(exported))
        names = archive.namelist()
        self.assertIn(export.LIBRARY_FILE, names)
        manifest = archive.read(export.LIBRARY_FILE).decode()
        self.assertIn("**Unannotated paper**", manifest)
        self.assertIn("### Toeplitz", manifest)
        signer = json.loads(notesync.DATA.search(manifest).group(1))["signer"]["key"]

        setup_library(self)  # autre ordinateur, bibliothèque vide, appareil déjà accepté
        with db.connect() as connection:
            connection.execute("DELETE FROM papers")
        notesync.trust({notesync.fingerprint(__import__("base64").b64decode(signer)): "Mac"})

        def quick_add(arxiv_id, known, in_library):
            db.save_paper({"id": arxiv_id, "arxiv_id": arxiv_id, "version": None, "title": known["title"],
                           "authors": known["authors"], "abstract": "", "categories": []})
            return db.get_paper(arxiv_id)
        with mock.patch.object(library, "quick_add", side_effect=quick_add):
            report = notesync.import_files([{"name": "notes.zip", "data": exported}])
        self.assertEqual((report["papers"], report["folders"], report["added"], report["missing"]), (2, 1, 1, ["My scanned PDF"]))
        self.assertEqual({p["id"] for p in db.list_papers()}, {"0806.2370", "1203.4201"})
        self.assertEqual(folders.list_folders()[0]["papers"], ["0806.2370", "1203.4201"])
        again = notesync.import_files([{"name": "notes.zip", "data": exported}])
        self.assertEqual((again["papers"], again["folders"], again["added"]), (0, 0, 0))  # rien en double
