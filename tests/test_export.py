"""Export des notes en Markdown : fiche, énoncés et preuves dans l'ordre du texte, passages par section ; archive."""

import io
import json
import pathlib
import tempfile
import unittest
import zipfile
from unittest import mock

from app import annotations, db, export, library
from app.structure import STRUCTURE_VERSION

CONTENT = """<article><h2 id="S1">1. Introduction</h2><div id="S1.Thmthm1">Theorem</div><div id="S1.p1">Some text</div>
<div id="S1.pf1">Proof</div><h2 id="S2">2. Bergman kernels</h2><div id="S2.p1">More text $x$</div></article>"""
STRUCTURE = {"version": STRUCTURE_VERSION, "toc": [{"id": "S1", "level": 1, "title": "1. Introduction"},
                                                  {"id": "S2", "level": 1, "title": "2. Bergman kernels"}],
             "nodes": [{"id": "S1.Thmthm1", "kind": "theorem", "label": "Theorem 1.1", "name": "Main", "proofs": ["S1.pf1"]}]}


class Export(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = tmp / "test.db", tmp, tmp / "papers"
        db.init()
        db.save_paper({"id": "0806.2370", "arxiv_id": "0806.2370", "version": 2, "title": "Toeplitz operators on $\\mathbb{C}^n$",
                       "authors": ["Xiaonan Ma", "George Marinescu"], "abstract": "", "categories": []})
        folder = library.PAPERS / "0806.2370"
        folder.mkdir(parents=True)
        (folder / "content.html").write_text(CONTENT)
        (folder / "structure.json").write_text(json.dumps(STRUCTURE))

    def tearDown(self):
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.saved

    def test_markdown(self):
        pid = "0806.2370"
        annotations.create(pid, "highlight", target={"start": {"id": "S2.p1", "offset": 0}, "end": {"id": "S2.p1", "offset": 4}},
                           color="yellow", quote="More text $x$", note="See *Dai–Liu–Ma*.")
        annotations.create(pid, "highlight", target={"start": {"id": "S1.p1", "offset": 0}, "end": {"id": "S1.p1", "offset": 4}},
                           color="green", quote="Some text")
        annotations.create(pid, "block", target={"id": "S1.pf1"}, note="Uses the off-diagonal expansion.")
        annotations.create(pid, "block", target={"id": "S1.Thmthm1"}, state="understood")
        annotations.create(pid, "general", note="A key paper.")
        text = export.paper_markdown(pid, "fr")
        self.assertTrue(text.startswith("# Toeplitz operators on $\\mathbb{C}^n$"))
        self.assertIn("https://arxiv.org/abs/0806.2370v2", text)
        order = [text.index(s) for s in ("## Fiche de lecture", "A key paper.", "### Theorem 1.1 (Main) — *compris*",
                                         "### Preuve de Theorem 1.1", "## Passages surlignés", "### 1. Introduction",
                                         "> Some text", "### 2. Bergman kernels", "> More text $x$", "See *Dai–Liu–Ma*.")]
        self.assertEqual(order, sorted(order))  # dans l'ordre du texte, passages regroupés par section
        self.assertIn("### Theorem 1.1 (Main) — *understood*", export.paper_markdown(pid, "en"))
        self.assertEqual(export.filename(db.get_paper(pid)), "0806.2370 Toeplitz operators on.md")

    def test_bookmarks(self):
        pid = "0806.2370"
        annotations.create(pid, "bookmark", {"pdf": {"page": 7, "top": 300}}, quote="Calcul à vérifier")
        annotations.create(pid, "bookmark", {"id": "S2.p1"}, quote="Noyau de Bergman")
        text = export.paper_markdown(pid, "fr")
        self.assertIn("## Marque-pages", text)
        self.assertLess(text.index("**Noyau de Bergman** — 2. Bergman kernels"), text.index("**Calcul à vérifier** — PDF, page 7"))
        self.assertTrue(export.has_notes(annotations.list_for(pid)))

    def test_order_within_a_paragraph_and_ai_notes(self):
        pid = "0806.2370"
        annotations.create(pid, "highlight", target={"start": {"id": "S1.p1", "offset": 5}, "end": {"id": "S1.p1", "offset": 9}}, quote="text")
        annotations.create(pid, "highlight", target={"start": {"id": "S1.p1", "offset": 0}, "end": {"id": "S1.p1", "offset": 4}}, quote="Some",
                           note="Proposed.", author="claude")
        text = export.paper_markdown(pid, "fr")
        self.assertLess(text.index("> Some"), text.index("> text"))
        self.assertIn("Proposed. *(proposée par Claude, acceptée)*", text)

    def test_zip_skips_papers_without_notes(self):
        annotations.create("0806.2370", "general", note="Note")
        annotations.create("0806.2370", "block", target={"id": "S1.Thmthm1"})  # vide : pas une note
        db.save_paper({"id": "1003.2523", "arxiv_id": "1003.2523", "title": "Review", "authors": [], "abstract": "", "categories": []})
        annotations.create("1003.2523", "general", note="   ")  # fiche vide : rien à exporter
        with mock.patch.object(annotations, "annotated_papers", return_value={"0806.2370", "1003.2523"}):
            data = export.library_zip()
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertEqual(names, ["0806.2370 Toeplitz operators on.md"])
        self.assertEqual(export.annotated_count(), 1)


if __name__ == "__main__":
    unittest.main()
