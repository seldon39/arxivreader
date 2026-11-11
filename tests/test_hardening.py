"""Défauts corrigés de la bibliothèque, du feed, de la structure et du serveur (bibliothèque temporaire, réseau simulé)."""

import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from app import annotations, arxiv, citations, crossrefs, db, feed, feedprune, library, main, relevance, texstructure
from app.arxiv import Metadata, parse_arxiv_id
from app.structure import STRUCTURE_VERSION, classify

BASE = f"http://{main.HOST}:{main.PORT}"


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.tmp / "test.db", self.tmp, self.tmp / "papers"
        db.init()
        library._latest.clear()
        crossrefs._nodes_cache.clear()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tearDown(self):
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.saved

    def add(self, paper_id, arxiv_id, **extra):
        db.save_paper({"id": paper_id, "arxiv_id": arxiv_id, "version": 1, "title": "T", "authors": [], "abstract": "",
                       "categories": [], "primary_category": "", "published": "", "updated": "", "doi": None,
                       "journal_ref": None, "comment": None, "source": "arxiv-html", "position": None, "in_library": 1, **extra})

    def structure(self, paper_id, **content):
        folder = library.paper_dir(paper_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "structure.json").write_text(json.dumps(content))
        return folder / "structure.json"


class Library(Base):
    def test_versions_without_local_copy_when_arxiv_answers(self):
        self.add("2401.00001", "2401.00001", version=None, source="pending")
        with mock.patch.object(library, "latest_version", return_value=2):
            info = library.versions_of(db.get_paper("2401.00001"))
        self.assertEqual([v["version"] for v in info["versions"]], [1, 2])

    def test_old_style_ids_lose_their_subject_class(self):
        self.assertEqual(parse_arxiv_id("math.DG/0211159v2"), ("math/0211159", 2))
        self.assertEqual(parse_arxiv_id("https://arxiv.org/abs/hep-th/9901001"), ("hep-th/9901001", None))
        self.assertEqual(arxiv.extract_ids("see math.DG/0211159 and math/0211159"), ["math/0211159"])

    def test_stored_old_paper_is_still_found(self):
        self.add("math.DG_0211159", "math.DG/0211159")
        self.assertEqual(library.stored_key("math/0211159"), "math.DG_0211159")
        self.assertEqual(library.stored_key("math/0000001"), "math_0000001")
        self.assertEqual(library.stored_key("2401.00001"), "2401.00001")
        with mock.patch.object(library, "_start_import") as start:  # déjà connu : ni nouvelle fiche ni import
            paper = library.quick_add("math.DG/0211159")
        self.assertEqual(paper["id"], "math.DG_0211159")
        start.assert_not_called()
        self.assertEqual(len(db.list_all_papers()), 1)

    def test_background_import_does_not_resurrect_a_removed_paper(self):
        meta = Metadata(arxiv_id="2401.00002", version=1, title="T", authors=[], abstract="", published="2024-01-01",
                        updated="2024-01-01", categories=["math.DG"], primary_category="math.DG")
        staging = self.tmp / "stage"
        staging.mkdir()
        # fiche retirée pendant le téléchargement : l'import de fond ne recrée ni la fiche ni le dossier
        with mock.patch.object(library, "_staged", return_value=(staging, "arxiv-html")):
            self.assertIsNone(library._import("2401.00002", "2401.00002", meta, False, None, must_exist=True))
        self.assertIsNone(db.get_paper("2401.00002"))
        self.assertFalse(staging.exists())
        self.assertFalse(library.paper_dir("2401.00002").exists())


class Feed(Base):
    def test_oai_old_single_version_record_is_not_new(self):
        xml = """<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>
        <record><metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>1901.00001</id>
          <version version="v1"><date>Tue, 01 Jan 2019 10:00:00 GMT</date></version>
          <title>Old, DOI added today</title><authors>A. One</authors><categories>math.DG</categories><abstract>x</abstract>
          <doi>10.1/x</doi></arXivRaw></metadata></record>
        <record><metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>2610.00001</id>
          <version version="v1"><date>Wed, 07 Oct 2026 10:00:00 GMT</date></version>
          <title>New</title><authors>A. One</authors><categories>math.DG</categories><abstract>x</abstract></arXivRaw></metadata></record>
        </ListRecords></OAI-PMH>"""
        items, _ = feed.parse_oai_day(xml, "2026-10-08", ["math.DG"])
        self.assertEqual([i["arxiv_id"] for i in items], ["2610.00001"])


class Citations(unittest.TestCase):
    def test_every_page_of_citations_is_read(self):
        pages = {0: {"data": [{"citingPaper": {"paperId": str(i)}} for i in range(100)], "next": 100},
                 100: {"data": [{"citingPaper": {"paperId": "a"}}, {"citingPaper": {}}]}}
        calls = []

        def get(path, params, api_key=None):
            calls.append(params["offset"])
            return pages[params["offset"]]
        with mock.patch.object(citations.profile, "_get_s2", side_effect=get):
            found = citations.fetch("k")
        self.assertEqual((calls, len(found)), ([0, 100], 101))

    def test_pagination_is_bounded(self):
        with mock.patch.object(citations.profile, "_get_s2", return_value={"data": [], "next": 5}) as get:
            citations.fetch("k")
        self.assertEqual(get.call_count, citations.MAX_PAGES)


class CrossRefs(Base):
    def test_sequel_is_not_the_first_part(self):
        index = [{"id": "a", "title": "x", "arxiv": "", "doi": "", "nodes": [],
                  "words": "bergman kernels and the geometry of polarized manifolds"},
                 {"id": "b", "title": "x", "arxiv": "", "doi": "", "nodes": [],
                  "words": "bergman kernels and the geometry of polarized manifolds ii"}]
        first = {"text": "A. B, Bergman kernels and the geometry of polarized manifolds, J. Geom. 3 (2001)"}
        second = {"text": "A. B, Bergman kernels and the geometry of polarized manifolds, II, J. Geom. 4 (2002)"}
        self.assertEqual(crossrefs.match(first, index)["id"], "a")
        self.assertEqual(crossrefs.match(second, index)["id"], "b")  # le plus long, pas la première partie
        self.assertIsNone(crossrefs.match(second, index[:1]))  # seule la première partie est ici : pas de faux renvoi

    def test_index_never_reanalyses_other_papers(self):
        self.add("p1", "2401.00001")
        self.structure("p1", version=0, nodes=[{"id": "T1", "label": "Lemma 1", "kind": "lemma"}])
        with mock.patch.object(library, "reanalyze") as reanalyze:
            index = crossrefs.library_index()
        reanalyze.assert_not_called()
        self.assertEqual(index[0]["nodes"][0]["label"], "Lemma 1")

    def test_index_is_cached_until_the_file_changes(self):
        self.add("p1", "2401.00001")
        path = self.structure("p1", nodes=[{"id": "T1", "label": "Lemma 1", "kind": "lemma"}])
        crossrefs.library_index()
        with mock.patch.object(pathlib.Path, "read_text", side_effect=AssertionError("relu")):
            crossrefs.library_index()  # inchangé : pas relu
        path.write_text(json.dumps({"nodes": [{"id": "T1", "label": "Lemma 1", "kind": "lemma"},
                                              {"id": "T2", "label": "Theorem 2", "kind": "theorem"}]}))
        self.assertEqual(len(crossrefs.library_index()[0]["nodes"]), 2)


class Server(Base):
    def test_pdf_and_pages_report_offline_instead_of_a_500(self):
        self.add("2401.00001", "2401.00001")
        client = TestClient(main.app, base_url=BASE, raise_server_exceptions=False)
        with mock.patch.object(library, "fetch_pdf", side_effect=httpx.ConnectError("down")):
            for route in ("pdf", "pages"):
                answer = client.get(f"/api/papers/2401.00001/{route}")
                self.assertEqual((answer.status_code, answer.json()["code"]), (502, "offline"), route)


class Structure(unittest.TestCase):
    def test_group_line_break_before_closing_brace(self):
        self.assertEqual(texstructure._group(r"{a \\} b} tail", 0, "{"), (r"a \\", 6))
        self.assertEqual(texstructure._group(r"{a \} b} tail", 0, "{"), (r"a \} b", 8))  # accolade protégée
        self.assertEqual(texstructure._group(r"{a {b \\} c} d", 0, "{"), (r"a {b \\} c", 12))
        self.assertEqual(texstructure._group(r"[x \\] y", 0, "["), (r"x \\", 6))

    def test_classify_foreign_and_abbreviated_labels(self):
        for label, kind in [("Satz 3.1", "theorem"), ("Teorema 2", "theorem"), ("Lema 4", "lemma"), ("Axiom 1", "notation"),
                            ("Property 2.1", "proposition"), ("Prop. 2", "proposition"), ("Cor. 3", "corollary"),
                            ("Thm 1.2", "theorem"), ("Def 1", "definition"), ("Main Theorem", "theorem"),
                            ("Corner 1", "other"), ("Defect 2", "other"), ("Algorithm 1", "other")]:
            self.assertEqual(classify(label, []), kind, label)

    def test_structure_version_was_bumped(self):
        self.assertGreaterEqual(STRUCTURE_VERSION, 16)


class Stats(Base):
    def test_only_existing_statements_count_as_done(self):
        self.structure("p", nodes=[{"id": "T1", "label": "Theorem 1", "kind": "theorem"},
                                   {"id": "R1", "label": "Remark 1", "kind": "remark"}])
        for target in ("T1", "R1", "gone"):
            annotations.create("p", "block", {"id": target}, state="understood")
        self.assertEqual(annotations.stats()["p"]["done"], 1)  # ni la remarque ni l'énoncé disparu


class Prune(Base):
    def seed(self):
        old = "2020-01-01 00:00:00"
        with db.connect() as connection:
            for key, arxiv_id in [("2001.00001", "2001.00001"), ("2001.00002", "2001.00002"), ("2001.00003", "2001.00003"),
                                  ("2001.00004", "2001.00004"), ("2001.00005", "2001.00005"), ("s2:old", None)]:
                connection.execute("INSERT INTO feed_items (key, arxiv_id, title, authors, categories, announced, in_arxiv, fetched_at) "
                                   "VALUES (?, ?, 't', '[]', '[]', '2020-01-02', 1, ?)", (key, arxiv_id, old))
            connection.execute("INSERT INTO feed_items (key, arxiv_id, title, authors, categories, announced, in_arxiv) "
                               "VALUES ('2999.00001', '2999.00001', 't', '[]', '[]', '2999-01-01', 1)")
            connection.execute("INSERT INTO feed_status (key, status) VALUES ('2001.00002', 'liked')")
            connection.execute("UPDATE feed_items SET s2_rank = 1 WHERE key = '2001.00003'")
            connection.execute("UPDATE feed_items SET citing = '[\"mine\"]' WHERE key = '2001.00004'")
            for key in ("ArXiv:2001.00001", "ArXiv:2001.00002", "ArXiv:2001.00099", "ArXiv:2001.00098", "ArXiv:2001.00097"):
                connection.execute("INSERT INTO embeddings (key, vector, fetched_at) VALUES (?, NULL, ?)", (key, old))
            connection.execute("INSERT INTO embeddings (key, vector) VALUES ('ArXiv:2999.00001', NULL)")
            connection.execute("INSERT INTO profile_items (kind, label, ref, text) VALUES ('paper', 'P', '2001.00098', 'x')")
        db.save_settings({"feed.days": {"2020-01-02": 6, "2999-01-01": 1}, "feed.partialDays": ["2020-01-02"]})
        self.add("lib", "2001.00005")

    def test_prune_keeps_what_matters(self):
        self.seed()
        removed = feedprune.prune(days=60)
        with db.connect() as connection:
            items = {r["key"] for r in connection.execute("SELECT key FROM feed_items")}
            vectors = {r["key"] for r in connection.execute("SELECT key FROM embeddings")}
        # supprimés : vieux, ni noté, ni dans la bibliothèque, ni recommandé, ni citant
        self.assertEqual(items, {"2001.00002", "2001.00003", "2001.00004", "2001.00005", "2999.00001"})
        # vecteurs gardés : récents, d'un article gardé du feed, du profil
        self.assertEqual(vectors, {"ArXiv:2001.00002", "ArXiv:2001.00098", "ArXiv:2999.00001"})
        self.assertEqual(removed, (2, 3))
        self.assertIn("2020-01-02", db.get_settings()["feed.days"])  # des articles y restent : le jour reste connu

    def test_thinned_day_is_marked_partial(self):
        self.seed()
        db.save_settings({"feed.partialDays": []})
        feedprune.prune(days=60)
        # il ne reste de ce jour que les articles gardés : la page doit le redemander, pas le croire complet
        self.assertEqual(db.get_settings()["feed.partialDays"], ["2020-01-02"])

    def test_days_without_items_are_forgotten(self):
        self.seed()
        with db.connect() as connection:
            connection.execute("DELETE FROM feed_items WHERE announced = '2020-01-02'")
        feedprune.prune(days=60)
        self.assertEqual(list(db.get_settings()["feed.days"]), ["2999-01-01"])
        self.assertEqual(db.get_settings()["feed.partialDays"], [])

    def test_vector_codec(self):
        vector = [0.12345, -0.5, 1.0, 0.000123]
        for a, b in zip(feedprune.unpack(feedprune.pack(vector)), vector):
            self.assertAlmostEqual(a, b, places=6)
        self.assertEqual(feedprune.unpack(json.dumps(vector)), vector)  # ancienne ligne en JSON
        self.assertIsNone(feedprune.unpack(None))
        self.assertIsNone(feedprune.pack(None))

    def test_both_formats_are_read_back(self):
        with db.connect() as connection:
            connection.execute("INSERT INTO embeddings (key, vector) VALUES ('old', ?)", (json.dumps([0.5, 0.25]),))
            connection.execute("INSERT INTO embeddings (key, vector) VALUES ('new', ?)", (feedprune.pack([0.5, 0.25]),))
            connection.execute("INSERT INTO embeddings (key, vector) VALUES ('none', NULL)")
        found = relevance.cached_embeddings(["old", "new", "none"])
        self.assertEqual((found["old"], found["new"], found["none"]), ([0.5, 0.25], [0.5, 0.25], None))


if __name__ == "__main__":
    unittest.main()
