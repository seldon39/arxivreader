"""Versions d'un article arXiv : archivage à la mise à jour, chaque version avec ses notes (bibliothèque temporaire,
aucune requête réelle)."""

import io
import pathlib
import shutil
import sqlite3
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

from app import ai, annotations, arxiv, bookjob, crossrefs, db, export, feed, fulltext, library, main, notesync, profile, related, search, zotero
from app.arxiv import ArxivError, Metadata

BASE = f"http://{main.HOST}:{main.PORT}"
ID = "2404.15983"
PNG = b"\x89PNG\r\n\x1a\nx"

PAGE = """<html><body><article class="ltx_document"><h1 class="ltx_title ltx_title_document">T</h1>
<div id="S1.Thm1" class="ltx_theorem ltx_theorem_theorem"><h6 class="ltx_title ltx_title_theorem">
<span class="ltx_tag ltx_tag_theorem">Theorem 1</span>.</h6><div class="ltx_para"><p class="ltx_p">Text of %s.</p></div></div>
</article></body></html>"""


def metadata(version, arxiv_id=ID):
    return Metadata(arxiv_id=arxiv_id, version=version, title=f"Title v{version}", authors=["A. B"], abstract="", published="2024-01-01",
                    updated=f"2024-0{version}-01", categories=["math.DG"], primary_category="math.DG")


def html(arxiv_id, version, ar5iv=True):
    return PAGE % f"v{version}", f"https://arxiv.org/html/{arxiv_id}v{version}/", "arxiv-html"


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.tmp / "test.db", self.tmp, self.tmp / "papers"
        db.init()
        library._latest.clear()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tearDown(self):
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.saved

    def installed(self, version):
        """Import réel de l'article, le réseau étant simulé."""
        with mock.patch.object(library, "fetch_metadata", return_value=metadata(version)), \
             mock.patch.object(library, "fetch_html", side_effect=html), \
             mock.patch.object(library, "fetch_source", return_value=None):
            return library.import_paper(ID)

    def text(self, paper_id):
        return (library.paper_dir(paper_id) / "content.html").read_text(encoding="utf-8")

    def noted(self, paper_id=ID):
        annotations.create(paper_id, "highlight", {"start": {"id": "S1.Thm1", "offset": 0}, "end": {"id": "S1.Thm1", "offset": 4}}, "green", "Text")
        annotations.create(paper_id, "block", {"id": "S1.Thm1"}, note="Voir la preuve")
        ai.create(paper_id, "ask", None, None, "Pourquoi ?", None, "model")

    def leftovers(self):
        return [p.name for p in library.PAPERS.iterdir() if p.name.startswith(".")]

    def wait(self, paper_id):
        for _ in range(500):
            if not library.importing(paper_id):
                return
            time.sleep(0.01)
        self.fail("import toujours en cours")


class Update(Base):
    def test_new_version_archives_the_old_one_with_its_notes(self):
        self.installed(1)
        self.noted()
        db.update_paper(ID, position="S1.Thm1")
        zone = annotations.create(ID, "highlight", {"pdf": {"page": 1, "rect": [0, 0, 1, 1]}}, "green", image=b"\x89PNG\r\n\x1a\nx")
        (library.paper_dir(ID) / "zones").exists() or self.fail("image de zone absente")

        paper = self.installed(2)

        archive_id = f"{ID}v1"
        self.assertEqual((paper["id"], paper["version"], paper["in_library"], paper["archived_of"]), (ID, 2, 1, None))
        self.assertIn("v2", self.text(ID))
        self.assertIn("v1", self.text(archive_id))
        archive = db.get_paper(archive_id)
        self.assertEqual((archive["arxiv_id"], archive["version"], archive["in_library"], archive["archived_of"], archive["position"], archive["title"]),
                         (ID, 1, 0, ID, "S1.Thm1", "Title v1"))
        self.assertEqual(annotations.list_for(ID), [])  # la nouvelle version commence sans notes
        self.assertEqual(ai.list_for(ID), [])
        self.assertEqual(sorted(a["kind"] for a in annotations.list_for(archive_id)), ["block", "highlight", "highlight"])
        self.assertEqual(len(ai.list_for(archive_id)), 1)
        self.assertTrue(annotations.image_path(annotations.get(zone["id"])).exists())  # images de zone avec leur version
        self.assertEqual(self.leftovers(), [])

    def test_old_identifiers_with_slash(self):
        old = "math/0405128"
        with mock.patch.object(library, "fetch_html", side_effect=html), mock.patch.object(library, "fetch_source", return_value=None):
            with mock.patch.object(library, "fetch_metadata", return_value=metadata(2, old)):
                library.import_paper(old)
            with mock.patch.object(library, "fetch_metadata", return_value=metadata(3, old)):
                library.import_paper(old)
        self.assertEqual(db.get_paper("math_0405128v2")["archived_of"], "math_0405128")

    def test_same_version_changes_nothing(self):
        self.installed(1)
        self.noted()
        annotations.set_proof(ID, "S1.Thm1", "immediate")
        with mock.patch.object(library, "extract_structure", wraps=library.extract_structure) as extract:
            self.installed(1)
        self.assertIn("S1.Thm1", extract.call_args.args[1])  # emplacement de preuve appliqué, comme avant
        self.assertEqual(len(annotations.list_for(ID)), 3)
        self.assertEqual(db.archives_of(ID), [])

    def test_same_version_keeps_the_images_of_notes_and_answers(self):
        self.installed(1)
        zone = annotations.create(ID, "highlight", {"pdf": {"page": 1, "rect": [0, 0, 1, 1]}}, "green", image=PNG)
        exchange = ai.create(ID, "zone", None, None, "Quoi ?", None, "model", PNG)
        self.installed(1)
        self.assertTrue(annotations.image_path(annotations.get(zone["id"])).exists())
        self.assertTrue(ai.image_path(ai.get(exchange["id"])).exists())
        self.assertEqual(self.leftovers(), [])

    def test_running_book_job_stops_with_the_old_version(self):
        self.installed(1)
        with mock.patch.object(bookjob, "forget") as forget:
            self.installed(1)
            forget.assert_not_called()
            self.installed(2)
        forget.assert_called_once_with(ID)

    def test_new_text_ignores_old_proof_locations(self):
        self.installed(1)
        annotations.set_proof(ID, "S1.Thm1", "immediate")
        with mock.patch.object(library, "extract_structure", wraps=library.extract_structure) as extract:
            self.installed(2)
        self.assertEqual(extract.call_args.args[1], {})
        self.assertEqual([a["kind"] for a in annotations.list_for(f"{ID}v1")], ["proof"])

    def test_pending_paper_is_not_archived(self):
        with mock.patch.object(library, "_start_import"):
            library.quick_add(ID, {"title": "T"})
        annotations.create(ID, "general", note="Notes")
        self.installed(3)
        self.assertEqual(db.archives_of(ID), [])
        self.assertEqual(len(annotations.list_for(ID)), 1)

    def test_version_of_a_pending_paper_written_during_the_download(self):
        """Import de notes prises sur la v1 pendant l'import de la v3 d'une fiche en attente : elles restent sur la v1."""
        with mock.patch.object(library, "_start_import"):
            library.quick_add(ID, {"title": "T"})

        def download(arxiv_id, version, ar5iv=True):
            db.update_paper(ID, version=1)  # notesync._version_of_notes
            annotations.create(ID, "general", note="Prise sur la v1")
            return html(arxiv_id, version)
        with mock.patch.object(library, "fetch_metadata", return_value=metadata(3)), \
             mock.patch.object(library, "fetch_html", side_effect=download), \
             mock.patch.object(library, "fetch_source", return_value=None), \
             mock.patch.object(library, "import_version"):
            library.import_paper(ID)
            self.wait(f"{ID}v1")
        self.assertEqual(db.get_paper(ID)["version"], 3)
        self.assertEqual(annotations.list_for(ID), [])
        self.assertEqual([a["note"] for a in annotations.list_for(f"{ID}v1")], ["Prise sur la v1"])

    def assert_intact(self):
        paper = db.get_paper(ID)
        self.assertEqual((paper["version"], paper["title"]), (1, "Title v1"))
        self.assertIn("v1", self.text(ID))
        self.assertEqual(len(annotations.list_for(ID)), 2)
        self.assertEqual(len(ai.list_for(ID)), 1)
        self.assertIsNone(db.get_paper(f"{ID}v1"))
        self.assertFalse(library.paper_dir(f"{ID}v1").exists())
        self.assertEqual(self.leftovers(), [])

    def test_failed_download_keeps_paper_and_notes(self):
        self.installed(1)
        self.noted()
        with mock.patch.object(library, "fetch_metadata", return_value=metadata(2)), \
             mock.patch.object(library, "fetch_html", side_effect=OSError("network")), \
             mock.patch.object(library, "fetch_source", return_value=None), self.assertRaises(OSError):
            library.import_paper(ID)
        self.assert_intact()

    def test_failed_rename_keeps_paper_and_notes(self):
        self.installed(1)
        self.noted()
        real = library._rename

        def rename(source, target, attempts=6):
            if target == library.paper_dir(ID) and source.name != f"{ID}v1":  # dossier préparé mis en place
                raise OSError("busy")
            real(source, target, attempts)
        with mock.patch.object(library, "_rename", side_effect=rename), self.assertRaises(OSError):
            self.installed(2)
        self.assert_intact()

    def test_failed_save_keeps_paper_and_notes(self):
        self.installed(1)
        self.noted()
        real = db.write_paper

        def write(connection, paper):
            if paper["id"] == ID:
                raise sqlite3.OperationalError("disk full")
            real(connection, paper)
        with mock.patch.object(db, "write_paper", side_effect=write), self.assertRaises(sqlite3.OperationalError):
            self.installed(2)
        self.assert_intact()

    def test_existing_archive_keeps_both_sets_of_notes(self):
        self.installed(2)
        annotations.create(ID, "general", note="Fiche de l'article")
        annotations.create(ID, "block", {"id": "S1.Thm1"}, note="Note de l'article")
        annotations.create(ID, "bookmark", {"id": "S1"}, quote="Marque")
        kept = annotations.create(ID, "highlight", {"pdf": {"page": 1, "rect": [0, 0, 1, 1]}}, "green", image=b"\x89PNG\r\n\x1a\nx")
        archive_id = f"{ID}v2"
        db.save_paper({**db.get_paper(ID), "id": archive_id, "in_library": 0, "archived_of": ID})
        shutil.copytree(library.paper_dir(ID), library.paper_dir(archive_id), ignore=shutil.ignore_patterns("zones"))
        annotations.create(archive_id, "general", note="Fiche de l'archive")
        annotations.create(archive_id, "block", {"id": "S1.Thm1"}, note="Note de l'archive", state="review")

        self.installed(3)

        notes = {a["kind"]: a for a in annotations.list_for(archive_id)}
        self.assertEqual(len(annotations.list_for(archive_id)), 4)  # une seule fiche, une seule note d'énoncé
        self.assertEqual(notes["general"]["note"], "Fiche de l'archive\n\nFiche de l'article")
        self.assertEqual((notes["block"]["note"], notes["block"]["state"]), ("Note de l'archive\n\nNote de l'article", "review"))
        self.assertTrue(annotations.image_path(annotations.get(kept["id"])).exists())
        self.assertEqual(annotations.list_for(ID), [])
        self.assertEqual(db.get_paper(ID)["version"], 3)
        self.assertEqual(self.leftovers(), [])

    def archived_ahead(self, version):
        """Version archivée plus récente que l'article principal, annotée (notes importées d'un autre ordinateur)."""
        archive_id = f"{ID}v{version}"
        db.save_paper({**db.get_paper(ID), "id": archive_id, "version": version, "in_library": 0, "archived_of": ID, "position": "S1.Thm1"})
        shutil.copytree(library.paper_dir(ID), library.paper_dir(archive_id))
        annotations.create(archive_id, "general", note=f"Fiche de la v{version}")
        kept = annotations.create(archive_id, "highlight", {"pdf": {"page": 1, "rect": [0, 0, 1, 1]}}, "green", image=PNG)
        ai.create(archive_id, "ask", None, None, "Pourquoi ?", None, "model")
        return archive_id, kept

    def test_archive_of_the_new_version_becomes_the_main_paper(self):
        self.installed(1)
        annotations.create(ID, "general", note="Fiche de la v1")
        archive_id, kept = self.archived_ahead(2)

        self.installed(2)

        self.assertIsNone(db.get_paper(archive_id))
        self.assertFalse(library.paper_dir(archive_id).exists())
        paper = db.get_paper(ID)
        self.assertEqual((paper["version"], paper["position"]), (2, "S1.Thm1"))
        self.assertIn("v2", self.text(ID))
        self.assertEqual(sorted(a["kind"] for a in annotations.list_for(ID)), ["general", "highlight"])
        self.assertEqual([a["note"] for a in annotations.list_for(ID) if a["kind"] == "general"], ["Fiche de la v2"])
        self.assertTrue(annotations.image_path(annotations.get(kept["id"])).exists())
        self.assertEqual(len(ai.list_for(ID)), 1)
        self.assertEqual([a["note"] for a in annotations.list_for(f"{ID}v1")], ["Fiche de la v1"])
        self.assertEqual(library.open_version(db.get_paper(ID), 2)["id"], ID)
        self.assertEqual(self.leftovers(), [])

    def test_archive_stays_if_the_update_fails(self):
        self.installed(1)
        archive_id, _ = self.archived_ahead(2)
        real = db.write_paper

        def write(connection, paper):
            if paper["id"] == ID:
                raise sqlite3.OperationalError("disk full")
            real(connection, paper)
        with mock.patch.object(db, "write_paper", side_effect=write), self.assertRaises(sqlite3.OperationalError):
            self.installed(2)
        self.assertEqual(db.get_paper(archive_id)["archived_of"], ID)
        self.assertEqual(len(annotations.list_for(archive_id)), 2)
        self.assertEqual(annotations.list_for(ID), [])

    def test_archive_of_the_version_of_a_pending_paper_becomes_the_main_paper(self):
        with mock.patch.object(library, "_start_import"):
            library.quick_add(ID, {"title": "T"})
        archive_id = f"{ID}v2"
        db.save_paper({**db.get_paper(ID), "id": archive_id, "version": 2, "in_library": 0, "archived_of": ID})
        annotations.create(archive_id, "general", note="Fiche de la v2")

        self.installed(2)

        self.assertIsNone(db.get_paper(archive_id))
        self.assertEqual([a["note"] for a in annotations.list_for(ID)], ["Fiche de la v2"])
        self.assertEqual(db.archives_of(ID), [])

    def test_migration_adds_the_column(self):
        connection = sqlite3.connect(db.DB_PATH)
        connection.executescript("DROP TABLE papers; CREATE TABLE papers (id TEXT PRIMARY KEY, arxiv_id TEXT NOT NULL, version INTEGER, in_library INTEGER DEFAULT 1);")
        connection.close()
        db.init()
        with db.connect() as connection:
            self.assertIn("archived_of", {row["name"] for row in connection.execute("PRAGMA table_info(papers)")})


class Interrupted(Base):
    """Arrêt brutal pendant une mise à jour : états que laissent les renommages, faits hors de la transaction."""

    def setUp(self):
        super().setUp()
        self.installed(1)
        self.noted()
        self.zone = annotations.create(ID, "highlight", {"pdf": {"page": 1, "rect": [0, 0, 1, 1]}}, "green", image=PNG)
        self.staging = library.PAPERS / f".{ID}.abc12345"

    def new_text(self):
        self.staging.mkdir()
        (self.staging / "content.html").write_text("texte v2", encoding="utf-8")

    def assert_restored(self):
        self.assertEqual(db.get_paper(ID)["version"], 1)
        self.assertIn("v1", self.text(ID))
        self.assertTrue(annotations.image_path(annotations.get(self.zone["id"])).exists())
        self.assertFalse(library.paper_dir(f"{ID}v1").exists())
        self.assertEqual(self.leftovers(), [])

    def test_between_the_two_renames(self):
        self.new_text()
        library.paper_dir(ID).rename(library.paper_dir(f"{ID}v1"))
        library.recover_interrupted()
        self.assert_restored()

    def test_before_the_commit(self):
        self.new_text()
        library.paper_dir(ID).rename(library.paper_dir(f"{ID}v1"))
        self.staging.rename(library.paper_dir(ID))
        library.recover_interrupted()
        self.assert_restored()
        self.installed(2)  # la mise à jour suivante archive le vrai texte de la v1, avec ses images
        self.assertIn("v1", self.text(f"{ID}v1"))
        self.assertTrue(annotations.image_path(annotations.get(self.zone["id"])).exists())

    def test_after_the_commit_nothing_is_undone(self):
        self.installed(2)
        self.staging.mkdir()
        library.recover_interrupted()
        self.assertIn("v2", self.text(ID))
        self.assertIn("v1", self.text(f"{ID}v1"))
        self.assertEqual(self.leftovers(), [])

    def test_same_version_reimport_between_the_renames(self):
        library.paper_dir(ID).rename(library.PAPERS / f".{ID}.old")
        library.recover_interrupted()
        self.assert_restored()

    def test_same_version_reimport_finished(self):
        library.PAPERS.joinpath(f".{ID}.old").mkdir()
        library.recover_interrupted()
        self.assert_restored()

    def merged(self):
        """Fusion avec une archive de la même version : l'ancien dossier est écarté sous le nom de l'archive."""
        db.save_paper({**db.get_paper(ID), "id": f"{ID}v1", "in_library": 0, "archived_of": ID})
        shutil.copytree(library.paper_dir(ID), library.paper_dir(f"{ID}v1"))
        parked = library.PAPERS / f".{ID}v1.old"
        library.paper_dir(ID).rename(parked)
        self.new_text()
        self.staging.rename(library.paper_dir(ID))
        return parked

    def test_merge_before_the_commit(self):
        self.merged()
        library.recover_interrupted()
        self.assertIn("v1", self.text(ID))
        self.assertEqual(self.leftovers(), [])

    def test_merge_after_the_commit(self):
        self.merged()
        db.update_paper(ID, version=2)
        library.recover_interrupted()
        self.assertEqual(self.text(ID), "texte v2")
        self.assertEqual(self.leftovers(), [])

    def test_library_without_papers_folder(self):
        shutil.rmtree(library.PAPERS)
        library.recover_interrupted()


class NeverTheCurrentPaper(Base):
    def setUp(self):
        super().setUp()
        self.installed(1)
        self.noted()
        self.installed(2)

    def test_not_in_library_or_lists(self):
        self.assertEqual([p["id"] for p in db.list_papers()], [ID])
        self.assertEqual([p["id"] for p in db.list_all_papers()], [ID])
        self.assertEqual([p["id"] for p in crossrefs.library_index()], [ID])

    def test_arxiv_id_maps_to_the_main_paper(self):
        db.update_paper(ID, in_library=0)  # seulement lu : la table « cached », où une archive prendrait sa place
        item = {"key": ID, "arxiv_id": ID}
        self.assertEqual(search.annotate_status([item])[0]["readId"], ID)
        origin = {"arxivId": ID}
        self.assertEqual(related.annotate({"origin": origin, "nodes": []})["origin"]["readId"], ID)
        with db.connect() as connection:
            connection.execute("INSERT INTO feed_items (key, arxiv_id, title, authors, categories, announced, in_arxiv, announce_type) "
                               "VALUES (?, ?, 'T', '[]', '[]', '2024-01-01', 1, 'new')", (ID, ID))
        self.assertEqual([i["readId"] for i in feed.list_items("arxiv", day="2024-01-01")], [ID])

    def test_followed_author_count_ignores_archives(self):
        db.save_paper({**db.get_paper(ID), "id": "other", "arxiv_id": "2404.00001", "categories": ["math.AG"]})
        with db.connect() as connection:
            item = connection.execute("INSERT INTO profile_items (kind, label) VALUES ('author', 'A. B')").lastrowid
        counts = {c["category"]: c["count"] for c in profile.author_categories(item)["kept"]}
        self.assertEqual(counts["math.DG"], 1)

    def test_never_added_to_library(self):
        archive_id = f"{ID}v1"
        self.assertEqual(library.set_in_library(archive_id, True)["in_library"], 0)
        client = TestClient(main.app, base_url=BASE)
        self.assertEqual(client.put(f"/api/papers/{archive_id}/library", json={"in_library": True}).json()["in_library"], 0)
        self.assertEqual(client.post(f"/api/papers/{archive_id}/refresh", json={}).status_code, 400)

    def test_zotero_sees_only_the_main_paper(self):
        self.assertEqual(set(zotero._local()), {ID})

    def test_notes_of_an_archive_are_found_by_the_search(self):
        annotations.create(f"{ID}v1", "general", note="Courbure holomorphe")
        found = fulltext.search("holomorphe")["results"]
        self.assertEqual([hit["paperId"] for hit in found], [f"{ID}v1"])

    def test_figure_repair_of_an_archive_never_imports_the_latest_version(self):
        folder = library.paper_dir(f"{ID}v1")
        (folder / "content.html").write_text('<img src="assets/a.png"/>', encoding="utf-8")
        (folder / "assets.json").unlink()
        with mock.patch.object(library, "import_paper") as reimport:
            library.repair_assets(f"{ID}v1")
        reimport.assert_not_called()

    def old_import_without_addresses(self):
        folder = library.paper_dir(ID)
        (folder / "content.html").write_text('<img src="assets/a.png"/>', encoding="utf-8")
        (folder / "assets.json").unlink()

    def test_figure_repair_keeps_the_installed_version(self):
        annotations.create(ID, "general", note="Fiche de la v2")
        self.old_import_without_addresses()
        with mock.patch.object(library, "fetch_metadata", side_effect=lambda name: metadata(2 if name.endswith("v2") else 3)) as fetch, \
             mock.patch.object(library, "fetch_html", side_effect=html), \
             mock.patch.object(library, "fetch_source", return_value=None):
            library.repair_assets(ID)
        fetch.assert_called_once_with(f"{ID}v2")
        self.assertEqual((db.get_paper(ID)["version"], len(annotations.list_for(ID))), (2, 1))
        self.assertIsNone(db.get_paper(f"{ID}v2"))
        self.assertEqual(db.get_paper(f"{ID}v1")["archived_of"], ID)

    def test_figure_repair_after_an_update_changes_nothing(self):
        self.old_import_without_addresses()
        with mock.patch.object(library, "fetch_metadata", return_value=metadata(1)), \
             mock.patch.object(library, "_source", return_value=None), mock.patch.object(library, "_import") as reimport:
            library.repair_assets(ID)
        reimport.assert_not_called()

    def test_pdf_of_the_version_installed_while_waiting(self):
        old = {**db.get_paper(ID), "version": 1}  # fiche lue avant d'attendre le verrou d'une mise à jour
        with mock.patch.object(library, "fetch_pdf", return_value=b"PDF") as fetch:
            library.ensure_pdf(old)
        fetch.assert_called_once_with(ID, 2)

    def test_reader_gets_the_main_version(self):
        answer = TestClient(main.app, base_url=BASE).get(f"/api/papers/{ID}v1").json()
        self.assertEqual((answer["paper"]["archived_of"], answer["paper"]["mainVersion"]), (ID, 2))


class Cleaning(Base):
    def old(self, paper_id):
        db.update_paper(paper_id, opened_at="2020-01-01 00:00:00")

    def test_archive_without_notes_is_cleaned(self):
        self.installed(1)
        self.installed(2)
        self.old(f"{ID}v1")
        self.assertEqual(library.clean_cache(), 1)
        self.assertIsNone(db.get_paper(f"{ID}v1"))
        self.assertFalse(library.paper_dir(f"{ID}v1").exists())
        self.assertIsNotNone(db.get_paper(ID))

    def test_archive_with_notes_keeps_itself_and_its_main_paper(self):
        self.installed(1)
        self.noted()
        self.installed(2)
        db.update_paper(ID, in_library=0)
        self.old(ID)
        self.old(f"{ID}v1")
        self.assertEqual(library.clean_cache(), 0)
        self.assertIsNotNone(db.get_paper(ID))
        self.assertEqual(len(annotations.list_for(f"{ID}v1")), 2)

    def test_main_paper_without_annotated_archive_goes_with_its_archives(self):
        self.installed(1)
        self.installed(2)
        db.update_paper(ID, in_library=0)
        self.old(ID)
        library.clean_cache()
        self.assertIsNone(db.get_paper(ID))
        self.assertIsNone(db.get_paper(f"{ID}v1"))


class Routes(Base):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, base_url=BASE)
        self.installed(1)
        self.noted()
        self.installed(2)
        library._latest.clear()

    def versions(self, latest=3, paper_id=ID):
        with mock.patch.object(library, "fetch_metadata", return_value=metadata(latest)) as fetch:
            return self.client.get(f"/api/papers/{paper_id}/versions").json(), fetch

    def test_writes_of_a_reader_left_on_a_replaced_version_are_refused(self):
        stale = {"X-ArXivReader-Version": "1"}
        requests = [("post", f"/api/papers/{ID}/annotations", {"kind": "block", "target": {"id": "S1.Thm1"}, "note": "Corrigée"}),
                    ("put", f"/api/papers/{ID}/proofs/S1.Thm1", {"mode": "immediate"}),
                    ("delete", f"/api/papers/{ID}/proofs/S1.Thm1", None),
                    ("put", f"/api/papers/{ID}/position", {"anchor": "S1.Thm1"}),
                    ("post", f"/api/papers/{ID}/ai", {"task": "ask", "question": "Pourquoi ?"}),
                    ("post", f"/api/papers/{ID}/ai/book", {"task": "summary"})]
        for method, url, body in requests:
            answer = self.client.request(method, url, headers=stale, **({"json": body} if body else {}))
            self.assertEqual((answer.status_code, answer.json().get("detail")), (409, "stale_version"), url)
        self.assertEqual(annotations.list_for(ID), [])
        self.assertEqual(ai.list_for(ID), [])
        self.assertIsNone(db.get_paper(ID)["position"])

    def test_writes_with_the_current_or_no_version_are_accepted(self):
        for headers in ({"X-ArXivReader-Version": "2"}, {}):
            answer = self.client.post(f"/api/papers/{ID}/annotations", headers=headers, json={"kind": "general", "note": "Fiche"})
            self.assertEqual(answer.status_code, 200)
        answer = self.client.post(f"/api/papers/{ID}v1/annotations", headers={"X-ArXivReader-Version": "1"}, json={"kind": "general", "note": "Fiche v1"})
        self.assertEqual(answer.status_code, 200)

    def test_note_edited_by_its_id_stays_with_its_text(self):
        note = next(a for a in annotations.list_for(f"{ID}v1") if a["kind"] == "block")
        self.client.put(f"/api/annotations/{note['id']}", json={"note": "Corrigée"})
        self.assertEqual(annotations.get(note["id"])["paper_id"], f"{ID}v1")

    def test_list_from_main_and_archive(self):
        answer, fetch = self.versions()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual((answer["latest"], answer["offline"], answer["main"]), (3, False, ID))
        rows = {row["version"]: row for row in answer["versions"]}
        self.assertEqual(list(rows), [1, 2, 3])
        self.assertEqual({k: rows[1][k] for k in ("id", "notes", "answers", "current", "main")}, {"id": f"{ID}v1", "notes": 2, "answers": 1, "current": False, "main": False})
        self.assertEqual({k: rows[2][k] for k in ("id", "notes", "answers", "current", "main")}, {"id": ID, "notes": 0, "answers": 0, "current": True, "main": True})
        self.assertIsNone(rows[3]["id"])
        again, fetch = self.versions(paper_id=f"{ID}v1")  # gardée en mémoire : pas de seconde requête
        self.assertEqual(fetch.call_count, 0)
        self.assertEqual([row["current"] for row in again["versions"]], [True, False, False])

    def test_offline_lists_local_versions_only(self):
        with mock.patch.object(library, "fetch_metadata", side_effect=OSError("offline")):
            answer = self.client.get(f"/api/papers/{ID}/versions").json()
        self.assertTrue(answer["offline"])
        self.assertEqual([row["version"] for row in answer["versions"]], [1, 2])

    def post(self, version, paper_id=ID, latest=3):
        with mock.patch.object(library, "fetch_metadata", return_value=metadata(latest)):
            return self.client.post(f"/api/papers/{paper_id}/versions/{version}", json={})

    def test_open_local_versions_at_once(self):
        with mock.patch.object(library, "fetch_metadata", side_effect=OSError("offline")) as fetch:
            self.assertEqual(self.client.post(f"/api/papers/{ID}/versions/2", json={}).json(), {"id": ID, "running": False})
            self.assertEqual(self.client.post(f"/api/papers/{ID}v1/versions/1", json={}).json(), {"id": f"{ID}v1", "running": False})
        fetch.assert_not_called()

    def test_latest_version_starts_the_update(self):
        with mock.patch.object(library, "update_in_background") as update:
            self.assertEqual(self.post(3, paper_id=f"{ID}v1").json(), {"id": ID, "running": True})
        update.assert_called_once_with(ID)

    def test_missing_version_starts_the_download(self):
        with mock.patch.object(library, "import_version") as download:
            answer = self.post(3, latest=5).json()
            self.assertEqual(answer["id"], f"{ID}v3")
            self.assertTrue(answer["running"])
            self.wait(f"{ID}v3")
        download.assert_called_once_with(ID, 3)
        self.assertEqual(db.get_paper(ID)["version"], 2)  # rien de changé sur l'article principal

    def test_bad_versions_refused(self):
        self.assertEqual(self.post(0).status_code, 400)
        self.assertEqual(self.post(-1).status_code, 400)
        self.assertEqual(self.post(9).status_code, 400)
        self.assertEqual(self.client.post(f"/api/papers/{ID}/versions/x", json={}).status_code, 422)

    def test_offline_download_is_an_error(self):
        with mock.patch.object(library, "fetch_metadata", side_effect=OSError("offline")):
            answer = self.client.post(f"/api/papers/{ID}/versions/3", json={})
        self.assertEqual((answer.status_code, answer.json()["code"]), (502, "offline"))

    def test_papers_without_arxiv_id_refused(self):
        db.save_paper({"id": "local", "arxiv_id": "", "version": None, "title": "T", "authors": [], "categories": [], "source": "local-pdf"})
        self.assertEqual(self.client.get("/api/papers/local/versions").status_code, 400)
        self.assertEqual(self.client.post("/api/papers/local/versions/1", json={}).status_code, 400)


class FakeResponse:
    status_code = 404
    text = ""


class Download(Base):
    def setUp(self):
        super().setUp()
        self.installed(3)
        self.noted()

    def started(self, version):
        library.ensure_version(db.get_paper(ID), version)

    def test_only_arxiv_html_of_that_version(self):
        urls = []
        client = mock.MagicMock()
        client.__enter__.return_value = client
        client.get.side_effect = lambda url, **kwargs: urls.append(url) or FakeResponse()
        with mock.patch.object(arxiv, "_client", return_value=client), \
             mock.patch.object(library, "fetch_metadata", return_value=metadata(1)) as fetch, \
             mock.patch.object(library, "fetch_source", return_value=None) as source:
            self.started(1)
            self.wait(f"{ID}v1")
        self.assertEqual(urls, [f"https://arxiv.org/html/{ID}v1"])  # jamais ar5iv : il sert une autre version
        fetch.assert_called_once_with(f"{ID}v1")
        source.assert_called_once_with(ID, 1)
        archive = db.get_paper(f"{ID}v1")
        self.assertEqual((archive["version"], archive["title"], archive["source"], archive["in_library"], archive["archived_of"]), (1, "Title v1", "none", 0, ID))
        self.assertEqual((db.get_paper(ID)["version"], len(annotations.list_for(ID))), (3, 2))  # article principal intact

    def test_html_kept_in_its_own_folder(self):
        with mock.patch.object(library, "fetch_metadata", return_value=metadata(2)), \
             mock.patch.object(library, "fetch_html", side_effect=html) as fetch, \
             mock.patch.object(library, "fetch_source", return_value=None):
            self.started(2)
            self.wait(f"{ID}v2")
        self.assertEqual(fetch.call_args.kwargs, {"ar5iv": False})
        self.assertIn("v2", self.text(f"{ID}v2"))
        self.assertIn("v3", self.text(ID))
        self.assertEqual(self.leftovers(), [])

    def test_failed_download_leaves_nothing(self):
        with mock.patch.object(library, "fetch_metadata", side_effect=ArxivError("absent")), \
             mock.patch.object(library, "fetch_source", return_value=None):
            self.started(1)
            self.wait(f"{ID}v1")
        self.assertIsNone(db.get_paper(f"{ID}v1"))
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(TestClient(main.app, base_url=BASE).get(f"/api/papers/{ID}v1/importing").status_code, 404)

    def test_wrong_version_from_arxiv_is_refused(self):
        with mock.patch.object(library, "fetch_metadata", return_value=metadata(3)), mock.patch.object(library, "fetch_source", return_value=None):
            self.started(1)
            self.wait(f"{ID}v1")
        self.assertIsNone(db.get_paper(f"{ID}v1"))

    def test_removing_the_main_paper_removes_its_archives(self):
        with mock.patch.object(library, "fetch_metadata", return_value=metadata(1)), \
             mock.patch.object(library, "fetch_html", side_effect=html), mock.patch.object(library, "fetch_source", return_value=None):
            self.started(1)
            self.wait(f"{ID}v1")
        library.remove_paper(ID)
        self.assertIsNone(db.get_paper(f"{ID}v1"))
        self.assertFalse(library.paper_dir(f"{ID}v1").exists())


class NotesExport(Base):
    def file(self, text, name="notes.md"):
        return {"name": name, "data": text.encode("utf-8")}

    def test_archive_notes_round_trip(self):
        self.installed(1)
        annotations.create(ID, "general", note="Fiche de la v1")
        self.installed(2)
        annotations.create(ID, "general", note="Fiche de la v2")
        names = zipfile.ZipFile(io.BytesIO(export.library_zip("fr"))).namelist()
        self.assertEqual(len(set(names)), 3)  # bibliothèque + un fichier par version : pas d'écrasement
        main_file, archive_file = self.file(export.paper_markdown(ID, "fr")), self.file(export.paper_markdown(f"{ID}v1", "fr"))

        # même bibliothèque, autre ordinateur (même clé) où seule la v2 est installée : la v1 est retéléchargée
        library.remove_paper(ID)
        self.installed(2)
        with mock.patch.object(library, "import_version"):
            report = notesync.import_files([main_file, archive_file])
            self.wait(f"{ID}v1")
        self.assertEqual((report["added"], report["rejected"]), (2, []))
        self.assertEqual([a["note"] for a in annotations.list_for(ID)], ["Fiche de la v2"])
        self.assertEqual([a["note"] for a in annotations.list_for(f"{ID}v1")], ["Fiche de la v1"])
        self.assertEqual((db.get_paper(f"{ID}v1")["archived_of"], db.get_paper(f"{ID}v1")["in_library"]), (ID, 0))

    def test_notes_of_another_version_do_not_land_on_the_installed_text(self):
        self.installed(1)
        annotations.create(ID, "general", note="Fiche de la v1")
        exported = self.file(export.paper_markdown(ID, "fr"))
        library.remove_paper(ID)
        self.installed(2)  # ici, l'article est déjà en version 2
        with mock.patch.object(library, "import_version"):
            notesync.import_files([exported])
            self.wait(f"{ID}v1")
        self.assertEqual(annotations.list_for(ID), [])
        self.assertEqual([a["note"] for a in annotations.list_for(f"{ID}v1")], ["Fiche de la v1"])

    def test_paper_absent_here_and_updated_since(self):
        self.installed(1)
        annotations.create(ID, "general", note="Fiche de la v1")
        exported = self.file(export.paper_markdown(ID, "fr"))
        library.remove_paper(ID)
        with mock.patch.object(library, "_start_import"):
            notesync.import_files([exported])
            self.installed(2)  # l'import différé apporte une version plus récente
        self.assertEqual(annotations.list_for(ID), [])
        self.assertEqual([a["note"] for a in annotations.list_for(f"{ID}v1")], ["Fiche de la v1"])
        self.assertEqual((db.get_paper(ID)["version"], db.get_paper(f"{ID}v1")["source"]), (2, "pending"))

    def test_absent_here_same_version_keeps_notes_on_the_paper(self):
        self.installed(2)
        annotations.create(ID, "general", note="Fiche")
        exported = self.file(export.paper_markdown(ID, "fr"))
        library.remove_paper(ID)
        with mock.patch.object(library, "_start_import"):
            notesync.import_files([exported])
            self.installed(2)
        self.assertEqual(db.archives_of(ID), [])
        self.assertEqual([a["note"] for a in annotations.list_for(ID)], ["Fiche"])

    def test_report_counts_only_papers_really_added(self):
        report = {"papers": 0, "added": 0, "updated": 0, "unchanged": 0, "missing": []}
        self.installed(2)
        entry = {"id": f"{ID}v1", "arxiv_id": ID, "version": 1, "title": "T", "authors": [], "in_library": 0, "archived_of": ID}
        with mock.patch.object(library, "import_version"):
            notesync._import_paper({"paper": entry, "annotations": []}, report)
            self.wait(f"{ID}v1")
        self.assertEqual(report["papers"], 0)
        library.remove_paper(ID)
        with mock.patch.object(library, "_start_import"), mock.patch.object(library, "import_version"):
            notesync._import_paper({"paper": entry, "annotations": []}, report)
        self.assertEqual(report["papers"], 1)

    def test_archive_entry_with_bad_version_does_not_crash(self):
        self.installed(2)
        paper = {"id": f"{ID}v0", "arxiv_id": ID, "version": "x", "title": "T", "authors": [], "in_library": 0, "archived_of": ID}
        report = {"papers": 0, "added": 0, "updated": 0, "unchanged": 0, "missing": []}
        notesync._import_paper({"paper": paper, "annotations": []}, report)
        self.assertEqual(db.archives_of(ID), [])


if __name__ == "__main__":
    unittest.main()


class NewVersions(Base):
    """Section « nouvelles versions » du feed."""

    def meta(self, version, age, arxiv_id=ID):
        day = (datetime.now(timezone.utc) - timedelta(days=age)).strftime("%Y-%m-%d")
        return Metadata(arxiv_id=arxiv_id, version=version, title="New title", authors=["A. B"], abstract="", published="2024-01-01",
                        updated=f"{day}T10:00:00Z", categories=["math.DG"], primary_category="math.DG", comment="v2: gap fixed")

    def add(self, arxiv_id, version, **extra):
        paper = {"id": arxiv.paper_key(arxiv_id), "arxiv_id": arxiv_id, "version": version, "title": "T", "authors": ["A"], "abstract": "", "categories": [],
                 "primary_category": "math.DG", "published": "2024-01-01", "updated": "2024-01-01", "source": "arxiv-html", "in_library": 1, **extra}
        db.save_paper(paper)
        return paper

    def test_lists_only_recent_newer_versions_of_the_library(self):
        for arxiv_id, version, extra in ((ID, 1, {}), ("2401.00001", 1, {}), ("2401.00002", 3, {}), ("2401.00003", 1, {"in_library": 0}), ("math/0601001", 2, {})):
            self.add(arxiv_id, version, **extra)
        db.save_paper({**self.add("2401.00004", None), "version": None})
        found = {ID: self.meta(2, 10), "2401.00001": self.meta(2, 60, "2401.00001"), "2401.00002": self.meta(3, 1, "2401.00002"),
                 "math/0601001": self.meta(4, 44, "math/0601001")}
        with mock.patch.object(library, "fetch_latest", return_value=found) as ask:
            items = library.new_versions()
        self.assertEqual([item["arxiv_id"] for item in items], [ID, "math/0601001"])  # du plus récent au plus ancien
        self.assertEqual((items[0]["installed"], items[0]["version"], items[0]["comment"]), (1, 2, "v2: gap fixed"))
        self.assertEqual(items[1]["id"], "math_0601001")
        self.assertEqual(sorted(ask.call_args[0][0]), sorted([ID, "2401.00001", "2401.00002", "math/0601001"]))  # ni hors bibliothèque, ni sans version

    def test_archives_and_local_documents_are_not_asked(self):
        self.add(ID, 2)
        self.add(f"{ID}", 1, id=library.archive_key(ID, 1), in_library=0, archived_of=ID)
        db.save_paper({**self.add("2401.00009", 1), "id": "local-1", "arxiv_id": ""})
        with mock.patch.object(library, "fetch_latest", return_value={}) as ask:
            library.new_versions()
        self.assertIn(ID, ask.call_args[0][0])
        self.assertNotIn("", ask.call_args[0][0])

    def test_answer_is_kept_and_forced_by_the_refresh_button(self):
        self.add(ID, 1)
        with mock.patch.object(library, "fetch_latest", return_value={ID: self.meta(2, 3)}) as ask:
            self.assertEqual(len(library.new_versions()), 1)
            self.assertEqual(len(library.new_versions()), 1)
            self.assertEqual(ask.call_count, 1)
            library.new_versions(force=True)
            self.assertEqual(ask.call_count, 2)

    def test_offline_keeps_the_last_answer(self):
        self.add(ID, 1)
        with mock.patch.object(library, "fetch_latest", return_value={ID: self.meta(2, 3)}):
            library.new_versions()
        with mock.patch.object(library, "fetch_latest", side_effect=ArxivError("offline")):
            self.assertEqual(len(library.new_versions(force=True)), 1)

    def test_installed_version_leaves_the_list_without_asking_again(self):
        paper = self.add(ID, 1)
        with mock.patch.object(library, "fetch_latest", return_value={ID: self.meta(2, 3)}) as ask:
            self.assertEqual(len(library.new_versions()), 1)
            db.save_paper({**paper, "version": 2})
            self.assertEqual(library.new_versions(), [])
            self.assertEqual(ask.call_count, 1)

    def test_batches_of_one_hundred(self):
        answer = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
        <entry><id>http://arxiv.org/abs/2404.15983v3</id><title>A</title><updated>2026-11-01T00:00:00Z</updated><published>2024-01-01T00:00:00Z</published><summary>s</summary></entry>
        <entry><id>http://arxiv.org/abs/math/0601001v2</id><title>B</title><updated>2026-11-02T00:00:00Z</updated><published>2006-01-01T00:00:00Z</published><summary>s</summary></entry></feed>"""
        reply = mock.Mock(text=answer)
        ids = [f"2401.{n:05d}" for n in range(150)]
        with mock.patch.object(arxiv, "_paced", side_effect=lambda ask: reply) as paced, mock.patch.object(arxiv, "_client"):
            found = arxiv.fetch_latest(ids)
        self.assertEqual(paced.call_count, 2)
        self.assertEqual((found[ID].version, found["math/0601001"].version, found[ID].updated), (3, 2, "2026-11-01T00:00:00Z"))

    def test_route_and_settings(self):
        self.add(ID, 1)
        client = TestClient(main.app, base_url=BASE)
        with mock.patch.object(library, "fetch_latest", return_value={ID: self.meta(2, 3)}):
            self.assertEqual(client.get("/api/feed/new-versions").json()["items"][0]["version"], 2)
        self.assertFalse(client.put("/api/settings", json={"feedNewVersions": False}).json()["feedNewVersions"])
        self.assertEqual(client.put("/api/settings", json={"feedNewVersions": "no"}).status_code, 400)


class AbsorbedArchive(Base):
    def test_recovery_after_commit_discards_the_absorbed_archive(self):
        folder, leftover = library.paper_dir(ID), library.paper_dir(library.archive_key(ID, 3))
        folder.mkdir(parents=True)
        (folder / "content.html").write_text("new")
        leftover.mkdir()
        (leftover / library.ABSORBED).touch()
        (leftover / "content.html").write_text("old")
        db.save_paper({"id": ID, "arxiv_id": ID, "version": 3, "title": "T", "authors": [], "abstract": "", "categories": [], "primary_category": "",
                       "published": "", "updated": "", "source": "arxiv-html", "in_library": 1})
        library.recover_interrupted()
        self.assertEqual((folder / "content.html").read_text(), "new")
        self.assertFalse(leftover.exists())

    def test_repair_does_not_reimport_ar5iv_text_of_another_version(self):
        paper = {"id": ID, "arxiv_id": ID, "version": 1, "title": "T", "authors": [], "abstract": "", "categories": [], "primary_category": "",
                 "published": "", "updated": "", "source": "ar5iv", "in_library": 1}
        db.save_paper(paper)
        library.paper_dir(ID).mkdir(parents=True)
        with mock.patch.object(library, "missing_assets", return_value=["x.png"]), mock.patch.object(library, "latest_version", return_value=2), \
                mock.patch.object(library, "_reimport_installed") as again:
            library.repair_assets(ID)
        again.assert_not_called()

