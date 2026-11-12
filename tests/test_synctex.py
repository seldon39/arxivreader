"""SyncTeX : lecture du fichier .synctex.gz (fichiers du projet, pages de chaque ligne, ligne à un point d'une page),
gardé par la compilation à côté du PDF ; comparé au programme « synctex » quand TeX est installé."""

import gzip
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from app import db, library, synctex, texfile
from tests.test_localpdf import make_pdf
from tests.test_texfile import HAS_TEX

SP = synctex.SP_PER_BP  # unités du fichier par point du PDF (Unit:1, Magnification:1000)


def records(root):
    """Petit SyncTeX écrit à la main : deux pages, main.tex et sec/a.tex du projet, une classe hors du projet."""
    return "\n".join([
        "SyncTeX Version:1",
        f"Input:1:{root}/./main.tex",
        "Input:2:/usr/local/texlive/2025/texmf-dist/tex/latex/base/article.cls",
        "Input:3:./sec/a.tex",
        f"Input:4:{root}/../outside/x.tex",
        "Output:pdf", "Magnification:1000", "Unit:1", "X Offset:0", "Y Offset:0", "Content:",
        "!100",
        "{1",
        "[1,12:4736286,46220574:26673152,41484288,0",
        f"(1,7:{int(100 * SP)},{int(200 * SP)}:{int(300 * SP)},{int(8 * SP)},{int(2 * SP)}",
        f"x1,5:{int(100 * SP)},{int(200 * SP)}",
        f"k1,5:{int(150 * SP)},{int(200 * SP)}:1000",
        f"x1,6:{int(250 * SP)},{int(200 * SP)}",
        ")",
        f"(3,2:{int(100 * SP)},{int(230 * SP)}:{int(300 * SP)},{int(8 * SP)},{int(2 * SP)}",
        f"x3,1:{int(100 * SP)},{int(230 * SP)}",
        f"g3,2:{int(200 * SP)},{int(230 * SP)}",
        ")",
        f"(2,40:{int(100 * SP)},{int(700 * SP)}:{int(300 * SP)},{int(8 * SP)},{int(2 * SP)}",
        f"x2,40:{int(300 * SP)},{int(700 * SP)}",
        ")",
        "this line is garbage",
        "x1,notanumber:1,2",
        "]",
        "}1",
        "{2",
        f"(1,13:{int(100 * SP)},{int(150 * SP)}:{int(300 * SP)},{int(8 * SP)},{int(2 * SP)}",
        f"x1,13:{int(100 * SP)},{int(150 * SP)}",
        f"x4,1:{int(200 * SP)},{int(150 * SP)}",
        ")",
        "}2",
        "Postamble:", "Count:20", "Post scriptum:", ""])


class SyncTeXFile(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.root = self.tmp / "my project"
        (self.root / "sec").mkdir(parents=True)
        self.path = self.tmp / "paper.synctex.gz"
        with gzip.open(self.path, "wt", encoding="utf-8") as file:
            file.write(records(self.root))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inputs_and_pages(self):
        sync = synctex.load(self.path, self.root)
        self.assertEqual(sync.inputs, {1: "main.tex", 2: None, 3: "sec/a.tex", 4: None})
        self.assertEqual(sync.pages, 2)
        self.assertEqual(sync.pages_of("main.tex", 5), {1})
        self.assertEqual(sync.pages_of("main.tex", 13), {2})
        self.assertEqual(sync.pages_of("sec/a.tex", 2), {1})
        self.assertEqual(sync.pages_of("main.tex", 99), set())
        self.assertEqual(sync.pages_of("article.cls", 40), set())  # hors du projet : jamais nommé
        self.assertIsNone(sync.line_at(1, 120, 198))  # page non lue en détail

    def test_line_at_and_first_line(self):
        sync = synctex.load(self.path, self.root, detail_pages=(1, 2))
        self.assertEqual(sync.line_at(1, 120, 198), ("main.tex", 5))   # dans la ligne, après x1,5
        self.assertEqual(sync.line_at(1, 160, 198), ("main.tex", 5))   # après le crénage k1,5
        self.assertEqual(sync.line_at(1, 260, 198), ("main.tex", 6))
        self.assertEqual(sync.line_at(1, 50, 199), ("main.tex", 5))    # à gauche de la ligne : son premier élément
        self.assertEqual(sync.line_at(1, 210, 229), ("sec/a.tex", 2))
        self.assertEqual(sync.line_at(1, 120, 260), ("sec/a.tex", 1))  # sous les lignes : la plus proche
        self.assertEqual(sync.line_at(1, 300, 700), ("sec/a.tex", 2))  # ligne de la classe : la plus proche du projet
        self.assertEqual(sync.line_at(2, 250, 150), ("main.tex", 13))  # x4 hors du projet ignoré
        self.assertEqual(sync.first_line(1), ("main.tex", 5))
        self.assertEqual(sync.first_line(2), ("main.tex", 13))
        self.assertIsNone(sync.line_at(3, 100, 100))

    def test_uncompressed_bounded_and_missing(self):
        plain = self.tmp / "plain.synctex"
        plain.write_text(records(self.root), encoding="utf-8")
        self.assertEqual(synctex.load(plain, self.root).pages_of("main.tex", 5), {1})
        with mock.patch.object(synctex, "MAX_BYTES", 600):  # lecture arrêtée en route : la suite n'est pas lue
            self.assertEqual(synctex.load(self.path, self.root).pages_of("main.tex", 13), set())
        self.assertIsNone(synctex.load(self.tmp / "absent.synctex.gz", self.root))
        broken = self.tmp / "broken.synctex.gz"
        broken.write_bytes(gzip.compress(records(self.root).encode())[:-30])  # archive tronquée
        sync = synctex.load(broken, self.root)
        self.assertTrue(sync is None or sync.pages_of("main.tex", 5) == {1})


class SyncTeXKept(unittest.TestCase):
    """paper.synctex.gz : gardé quand le PDF est celui compilé ici, supprimé sinon."""

    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.tmp / "test.db", self.tmp, self.tmp / "papers"
        db.init()
        self.build = mock.patch.object(texfile, "build_dir", lambda pid: (self.tmp / "build" / pid).mkdir(parents=True, exist_ok=True)
                                       or self.tmp / "build" / pid)
        self.build.start()
        self.project = self.tmp / "my draft"
        (self.project / "sec").mkdir(parents=True)
        (self.project / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nFirst page words.\n\\input{sec/a}\n\\newpage\nSecond page words here.\n\\end{document}\n",
            encoding="utf-8")
        (self.project / "sec" / "a.tex").write_text("Included words on the first page.\n", encoding="utf-8")

    def tearDown(self):
        self.build.stop()
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_removed_when_pdf_not_compiled_here(self):
        (self.project / "main.pdf").write_bytes(make_pdf(["First page words."]))
        pid = texfile.paper_id(texfile.main_file(self.project / "main.tex"))
        folder = library.paper_dir(pid)
        folder.mkdir(parents=True)
        (folder / texfile.SYNCTEX).write_bytes(b"stale")
        with mock.patch.object(texfile, "find_tool", lambda name: None):
            texfile.import_tex(self.project / "main.tex")
        self.assertFalse((folder / texfile.SYNCTEX).exists())

    @unittest.skipUnless(HAS_TEX, "TeX n'est pas installé")
    def test_kept_on_import_and_recompile_and_calibrated(self):
        result = texfile.import_tex(self.project / "main.tex")
        pid = result["paper"]["id"]
        kept = library.paper_dir(pid) / texfile.SYNCTEX
        self.assertTrue(kept.is_file())
        self.assertNotIn("main.synctex.gz", os.listdir(self.project))  # rien d'écrit chez l'utilisateur
        sync = synctex.load(kept, self.project, detail_pages=(1, 2))
        self.assertEqual(sync.pages_of("main.tex", 3), {1})
        self.assertEqual(sync.pages_of("sec/a.tex", 1), {1})
        self.assertEqual(sync.pages_of("main.tex", 6), {2})
        # même ligne que le programme synctex aux points du texte (coordonnées : points du PDF, depuis le haut)
        pdf = self.tmp / "build" / pid / "main.pdf"
        same, compared = 0, 0
        for page, boxes in sync.boxes.items():
            for box in boxes:
                for leaf in box[7][:6]:
                    x, y = (leaf[2] + 20000) / SP, (box[1] - box[3] / 2) / SP
                    expected = synctex.cli_line_at(pdf, page, x, y, self.project)
                    if expected is None:
                        continue
                    found = sync.line_at(page, x, y)
                    compared += 1
                    same += found == expected
                    self.assertEqual(found[0], expected[0])
                    self.assertLessEqual(abs(found[1] - expected[1]), 2)
        if texfile.find_tool("synctex"):
            self.assertGreaterEqual(compared, 5)
            self.assertGreaterEqual(same, 0.9 * compared)
        # recompilation : gardé (nouveau), et supprimé si un PDF est choisi à la place
        (self.project / "sec" / "a.tex").write_text("Changed words.\n", encoding="utf-8")
        texfile.recompile(pid)
        self.assertTrue(kept.is_file())
        chosen = self.tmp / "chosen.pdf"
        chosen.write_bytes(make_pdf(["x"]))
        texfile.import_tex(self.project / "main.tex", pdf=chosen)
        self.assertFalse(kept.exists())


@unittest.skipUnless(HAS_TEX and texfile.find_tool("synctex"), "TeX (synctex) n'est pas installé")
class TwoColumns(unittest.TestCase):
    """Deux colonnes : un point entre deux lignes d'une colonne reste dans sa colonne (pas la ligne voisine de l'autre
    colonne, ni la boîte qui contient toute la colonne)."""

    def test_calibrated_against_cli(self):
        import random
        tmp = pathlib.Path(tempfile.mkdtemp())
        try:
            words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
            rng = random.Random(7)
            body = [" ".join(rng.choice(words) for _ in range(rng.randint(4, 14))) + ("\n" if rng.random() < 0.15 else "")
                    for _ in range(230)]
            (tmp / "main.tex").write_text("\\documentclass[letterpaper,twocolumn]{article}\n\\begin{document}\n"
                                          + "\n".join(body) + "\n\\end{document}\n", encoding="utf-8")
            out = tmp / "out"
            out.mkdir()
            pdf, log, _ = texfile.compile_pdf(tmp / "main.tex", out, "pdflatex", False, [])
            self.assertIsNotNone(pdf, log[-1000:])
            sync = synctex.load(out / "main.synctex.gz", tmp, detail_pages=(1,))
            same = compared = 0
            for _ in range(60):
                x, y = rng.uniform(60, 550), rng.uniform(80, 700)
                expected = synctex.cli_line_at(pdf, 1, x, y, tmp)
                if expected is None:
                    continue
                compared += 1
                found = sync.line_at(1, x, y)
                same += found is not None and found[0] == expected[0] and abs(found[1] - expected[1]) <= 1
            self.assertGreaterEqual(compared, 30)
            self.assertGreaterEqual(same, 0.9 * compared, (same, compared))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
