"""Version HTML expérimentale (LaTeXML) : identifiants communs avec la structure tirée de la source, figures pgfplots
remplacées, extraction sûre de la source d'arXiv, état (à jour, à refaire, échec), conversion réelle si installé."""

import io
import json
import pathlib
import shutil
import tarfile
import tempfile
import unittest
from unittest import mock

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app import db, latexml, library, main, texfile, texstructure
from app.sanitize import sanitize_document
from tests.test_texfile import ARTICLE, BIB, HAS_TEX

PAGE = """<article class="ltx_document">
<section class="ltx_section" id="S1"><h2 class="ltx_title">1 Results</h2>
<div class="ltx_theorem ltx_theorem_lemma" id="S1.Thmtheorem1"><h6 class="ltx_title"><span class="ltx_tag">Lemma 1.1</span>.</h6><p>A</p></div>
<div class="ltx_theorem ltx_theorem_remark" id="S1.Thmtheorem2"><h6 class="ltx_title">Remark.</h6><p>B</p></div>
<p>See <a class="ltx_ref" href="#S1.Thmtheorem1">1.1</a>.</p></section></article>"""


class Latexml(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.tmp / "test.db", self.tmp, self.tmp / "papers"
        db.init()

    def tearDown(self):
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_align_ids(self):
        # numéros simulés décalés (« Lemma 1.2 » ici, 1.1 chez LaTeXML) : rapprochés dans l'ordre, par type
        tex = {"nodes": [{"id": "tex-lbl-l", "label": "Lemma 1.2", "kind": "lemma"}, {"id": "tex-txt-remark-ab", "label": "Remark", "kind": "remark"}],
               "toc": [{"id": "tex-sec-1", "title": "1. Results"}]}
        fragment = BeautifulSoup(PAGE, "html.parser")
        renamed = latexml.align_ids(fragment, tex)
        self.assertEqual(renamed, {"S1.Thmtheorem1": "tex-lbl-l", "S1.Thmtheorem2": "tex-txt-remark-ab", "S1": "tex-sec-1"})
        self.assertEqual(fragment.select_one("a.ltx_ref")["href"], "#tex-lbl-l")
        self.assertIsNotNone(fragment.find(id="tex-sec-1"))

    def test_pgfplots_replaced_other_tikz_kept(self):
        text = (r"a \begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture} b "
                r"\begin{tikzpicture}\begin{axis}\addplot3[surf]{x*y};\end{axis}\end{tikzpicture} c")
        out = latexml._without_plots(text)
        self.assertIn(r"\draw (0,0)--(1,1);", out)
        self.assertNotIn("addplot3", out)
        self.assertIn("pgfplots", out)

    def test_extract_skips_paths_outside(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            for name, text in (("paper.tex", "\\documentclass{article}\\begin{document}x\\end{document}"),
                               ("../evil.tex", "x"), ("sub/part.tex", "y")):
                raw = text.encode()
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
        folder = self.tmp / "src"
        folder.mkdir()
        self.assertEqual(latexml._extract(data.getvalue(), folder), "paper.tex")
        self.assertTrue((folder / "sub" / "part.tex").exists())
        self.assertFalse((self.tmp / "evil.tex").exists())

    def test_state_and_routes(self):
        db.save_paper({"id": "tex-s", "arxiv_id": "", "version": None, "title": "T", "authors": [], "abstract": "", "categories": [],
                       "primary_category": "", "published": None, "updated": None, "doi": None, "journal_ref": None,
                       "comment": "", "source": "local-tex", "in_library": 1})
        paper = db.get_paper("tex-s")
        self.assertEqual(latexml.state(paper), "none")
        with mock.patch.object(texfile, "report", return_value={"compiledAt": "A"}):
            latexml._save_state("tex-s", {"ok": True, "stamp": "A", "structureVersion": texstructure.STRUCTURE_VERSION})
            (latexml.html_dir("tex-s") / "content.html").write_text("<article></article>")
            self.assertEqual(latexml.state(paper), "ready")
        with mock.patch.object(texfile, "report", return_value={"compiledAt": "B"}):  # recompilé depuis
            self.assertEqual(latexml.state(paper), "stale")
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        self.assertEqual(client.get("/api/papers/tex-s/content").text, "<article></article>")
        db.save_paper({**paper, "id": "pdf-x", "source": "local-pdf"})
        self.assertEqual(client.get("/api/papers/pdf-x/html").status_code, 400)

    @unittest.skipUnless(HAS_TEX and latexml.find(), "TeX ou LaTeXML n'est pas installé")
    def test_real_conversion(self):
        project = self.tmp / "draft"
        project.mkdir()
        (project / "main.tex").write_text(ARTICLE.replace("\\input{sections/intro}", ""), encoding="utf-8")
        (project / "refs.bib").write_text(BIB, encoding="utf-8")
        with mock.patch.object(texfile, "build_dir", lambda pid: (self.tmp / "build" / pid).mkdir(parents=True, exist_ok=True)
                               or self.tmp / "build" / pid):
            pid = texfile.import_tex(project / "main.tex")["paper"]["id"]
            self.assertEqual(latexml.convert(pid), {"state": "ready"})
        structure = latexml.structure(pid)
        self.assertEqual([n["id"] for n in structure["nodes"]][:2], ["tex-lbl-lem:a", "tex-lbl-lem:unused"])
        self.assertEqual(latexml.state(db.get_paper(pid)), "ready")
        self.assertEqual(sorted(p.name for p in project.iterdir()), ["main.tex", "refs.bib"])  # rien écrit chez l'utilisateur


    def test_bibliography_and_equations_renamed(self):
        page = """<article class="ltx_document"><section class="ltx_section" id="S1"><h2 class="ltx_title">1 A</h2>
<table class="ltx_equation" id="S1.E1"><tr><td><span class="ltx_tag ltx_tag_equation">(1.1)</span></td></tr></table>
<p><a href="#S1.E1">1.1</a> <a href="#bib.bib1">1</a></p></section>
<section class="ltx_bibliography" id="bib"><ul><li class="ltx_bibitem" id="bib.bib1">Knuth</li></ul></section></article>"""
        tex = {"nodes": [], "toc": [{"id": "tex-sec-1", "title": "1. A"}], "bibliography": [{"id": "bib.knuth"}],
               "equations": {"eq:main": {"label": "(1.1)"}}}
        fragment = BeautifulSoup(page, "html.parser")
        latexml.align_ids(fragment, tex)
        self.assertEqual([a["href"] for a in fragment.find_all("a")], ["#eq:main", "#bib.knuth"])

    def test_same_main_file_for_analysis_and_html(self):
        from app.texsource import choose_main
        files = {"cover.tex": "\\documentclass{letter}\\begin{document}Dear editor\\end{document}",
                 "main.tex": "\\documentclass{amsart}\\begin{document}" + "Long paper. " * 200 + "\\end{document}"}
        self.assertEqual(choose_main(files), "main.tex")
        readme = '{"sources": [{"filename": "cover.tex", "usage": "toplevel"}]}'
        self.assertEqual(choose_main(files, readme), "cover.tex")  # 00README.json d'arXiv : il fait foi

    def test_contents_page_needs_a_heading(self):
        toc = [{"title": "1. Introduction"}, {"title": "2. Preliminaries"}, {"title": "3. Main results"}]
        outline = ["In Section 2 (Preliminaries) we recall... Main results come next... Introduction ends here."]
        self.assertEqual(texstructure._contents_pages(outline, toc), set())
        self.assertEqual(texstructure._contents_pages(["Contents 1. Introduction 2. Preliminaries 3. Main results"], toc), {1})

    def test_no_conversion_during_compilation(self):
        db.save_paper({"id": "tex-c", "arxiv_id": "", "version": None, "title": "T", "authors": [], "abstract": "", "categories": [],
                       "primary_category": "", "published": None, "updated": None, "doi": None, "journal_ref": None,
                       "comment": "", "source": "local-tex", "in_library": 1})
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        with mock.patch.object(latexml, "find", return_value="/bin/latexmlc"), \
             mock.patch.object(texfile, "job", side_effect=lambda job: {"running": job == "tex-c"}):
            self.assertEqual(client.post("/api/papers/tex-c/html", json={}).status_code, 409)


    def test_viewbox_and_layout_stubs(self):
        fragment = BeautifulSoup('<svg class="ltx_picture" width="355" height="315.51"></svg>'
                                 '<svg class="ltx_picture" width="10" height="5" viewbox="0 0 20 10"></svg>', "html.parser")
        latexml.add_viewboxes(fragment)
        self.assertEqual([s.get("viewbox") for s in fragment.find_all("svg")], ["0 0 355 315.51", "0 0 20 10"])
        folder = latexml._stubs(self.tmp)  # typearea sans équivalent dans LaTeXML 0.8.8 : vide la bibliographie
        self.assertTrue((folder / "typearea.sty.ltxml").read_text().startswith("package LaTeXML::Package::Pool;"))


    def test_paths_with_accents_go_through_a_link(self):
        folder = self.tmp / "Thèse"  # LaTeXML 0.8.8 n'y trouve pas le .bib (bibliographie vide)
        folder.mkdir()
        (folder / "refs.bib").write_text("@book{k, title={T}}")
        work = self.tmp / "work"
        work.mkdir()
        path = latexml._ascii(folder, work, 0)
        self.assertTrue(str(path).isascii())
        self.assertEqual((path / "refs.bib").read_text(), "@book{k, title={T}}")
        self.assertEqual(latexml._ascii(work, work, 1), work)  # sans accent : tel quel


    def test_author_macros_expanded_in_bib(self):
        # LaTeXML lit le .bib sans le préambule : « $\mC^k$ » perdait son C
        macros = {"mC": r"\mathcal{C}", "cC": r"\mathscr{C}"}
        bib = "@article{k, title={With {$\\mC^k$} and $\\cC$ and \\mCx}}".encode("latin-1")
        self.assertEqual(latexml._expand_macros(bib, macros).decode(),
                         "@article{k, title={With {${\\mathcal{C}}^k$} and ${\\mathscr{C}}$ and \\mCx}}")

    def test_lost_pattern_rebuilt_from_its_tile(self):
        # LaTeXML 0.8.8 : définition du motif perdue, sa tuile dessinée seule juste avant
        page = ('<article class="ltx_document"><svg class="ltx_picture" width="10" height="10"><g>'
                '<path d="M 0 0 L 4.29 4.29" style="fill:none"></path>'
                '<defs><pattern id="pgfupat1" href="#pgfpat3"><g fill="#000000" stroke="#000000"><use href="#pgfsym3"></use>'
                '</g></pattern></defs><g fill="url(#pgfupat1)"><path d="M -39 37 L 39 41 Z"></path></g></g></svg></article>')
        fragment, _ = sanitize_document(page, latexml.BASE)
        latexml.fix_patterns(fragment)
        pattern = fragment.find("pattern")
        self.assertEqual((pattern.get("patternunits"), pattern.get("width"), pattern.get("href")), ("userSpaceOnUse", "4.15", None))
        self.assertEqual(pattern.path["d"], "M 0 0 L 4.29 4.29")
        self.assertEqual(len(fragment.find_all("path")), 2)  # la tuile isolée a disparu du dessin

    @unittest.skipUnless(latexml.find(), "LaTeXML n'est pas installé")
    def test_figure_labels_on_their_baseline(self):
        work = self.tmp / "fig"
        work.mkdir()
        (work / "main.tex").write_text(r"""\documentclass{article}\usepackage{tikz}\usetikzlibrary{matrix}\begin{document}
\begin{tikzpicture}\fill (0,1) circle (2pt) node [below] {$x_0$};\end{tikzpicture}
\begin{tikzpicture}\matrix (m) [matrix of math nodes,row sep=3em]{ A_1 \\ B^X \\};\end{tikzpicture}
\begin{tikzpicture}\node[draw,align=center] {first $x$\\ second line};\end{tikzpicture}
\end{document}""", encoding="utf-8")
        out = work / "out"
        out.mkdir()
        html = latexml._run(latexml.find(), work, "main.tex", [latexml._stubs(work)], out)
        labels = BeautifulSoup(html, "html.parser").find_all("foreignobject")
        self.assertTrue(labels and all("--ltx-fo-height" in fo.get("style", "") for fo in labels))
        self.assertIn("ltx_tikzmatrix_base", html)
        self.assertNotIn("<text", html)  # ligne de texte seul : une étiquette comme les autres

    def test_figure_refs_get_their_number(self):
        # LaTeXML donne aux \ref une largeur fixe : boîtes de nœuds trop étroites ; numéro du .aux, toujours un lien
        text = r"\ref{a} \begin{tikzpicture}\node{\ref{a}}; \node{\eqref{e}}; \node{\ref{x}};\end{tikzpicture}"
        out = latexml._figure_refs(text, {"a": ("A3", None), "e": ("2.1", "eq.1")})
        self.assertEqual(out, r"\ref{a} \begin{tikzpicture}\node{\arxivreaderref{a}{A3}}; "
                              r"\node{(\arxivreaderref{e}{2.1})}; \node{\ref{x}};\end{tikzpicture}")


if __name__ == "__main__":
    unittest.main()
