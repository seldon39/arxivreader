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
                              r"\node{\textup{(\arxivreaderref{e}{2.1})}}; \node{\ref{x}};\end{tikzpicture}")

    def test_arxiv_patterns_repaired(self):
        # LaTeXML d'arXiv : le <symbol> de la tuile enferme la suite du dessin (invisible dans <defs>), la surface est
        # remplie en noir ; au réemploi du motif, plus de groupe à motif du tout
        page = ('<article class="ltx_document"><svg class="ltx_picture" width="10" height="10"><g>'
                '<defs><pattern height="4" id="pgfpat3" patternUnits="userSpaceOnUse" width="4"><symbol id="pgfsym3">'
                '<g stroke-width="0.4pt"><path d="M 0 0 L 4.29 4.29" style="fill:none"></path></g>'
                '<defs><pattern id="pgfupat1" href="#pgfpat3"><g fill="#000" stroke="#000"><use href="#pgfsym3"></use>'
                '</g></pattern></defs><g fill="#000000"><path d="M -39 37 L 39 41 Z"></path></g>'
                '<g fill="url(#pgfupat1)"></g><g id="label"><path d="M 1 1 L 2 2"></path></g>'
                '</symbol></pattern></defs>'
                '<defs><pattern id="pgfupat2" href="#pgfpat3"><g fill="#000" stroke="#000"><use href="#pgfsym3"></use>'
                '</g></pattern></defs><g fill="#000000"><path d="M -9 7 L 9 11 Z"></path></g></g></svg></article>')
        fragment, _ = sanitize_document(page, latexml.BASE)
        self.assertIsNone(fragment.find(id="label").find_parent("defs"))  # le reste du dessin est visible
        for pattern_id in ("pgfupat1", "pgfupat2"):
            pattern = fragment.find(id=pattern_id)
            self.assertEqual((pattern.get("width"), pattern.path["d"]), ("4", "M 0 0 L 4.29 4.29"))
            self.assertEqual(len(fragment.select(f'g[fill="url(#{pattern_id})"] > path')), 1)

    def test_arxiv_pattern_variants(self):
        # surface après le groupe à motif (qui ne contient qu'une redéfinition du motif pour sa couleur) ; réemploi
        # d'un motif défini seul dans un <g>, la surface suivant ce <g> (2610.29950, 2610.31099)
        page = ('<article class="ltx_document"><svg class="ltx_picture" width="10" height="10"><g>'
                '<defs><pattern height="4" id="pgfpat3" patternUnits="userSpaceOnUse" width="4"><symbol id="pgfsym3">'
                '<g stroke-width="0.4pt"><path d="M 0 0 L 4.29 4.29"></path></g>'
                '<defs><pattern id="pgfupat1" href="#pgfpat3"><g><use href="#pgfsym3"></use></g></pattern></defs>'
                '<g fill="url(#pgfupat1)"><defs><pattern id="pgfupat2" href="#pgfpat3"><g><use href="#pgfsym3"></use></g>'
                '</pattern></defs></g><g fill="#000000"><path id="a" d="M 1 1 h 2 v 2 h -2 Z"></path></g>'
                '<g><defs><pattern id="pgfupat3" href="#pgfpat3"><g><use href="#pgfsym3"></use></g></pattern></defs></g>'
                '<g fill="#000000"><path id="b" d="M 5 5 h 2 v 2 h -2 Z"></path></g>'
                '</symbol></pattern></defs></g></svg></article>')
        fragment, _ = sanitize_document(page, latexml.BASE)
        self.assertEqual(fragment.find(id="a").parent["fill"], "url(#pgfupat1)")
        self.assertEqual(fragment.find(id="b").parent["fill"], "url(#pgfupat3)")

    def test_pattern_tile_inside_a_group(self):
        # pattern color=… : la tuile isolée est dans son propre <g stroke-width>, avant celui qui porte la définition
        page = ('<article class="ltx_document"><svg class="ltx_picture" width="10" height="10"><g>'
                '<g stroke-width="0.4pt"><path d="M 0 0 L 4.29 4.29" style="fill:none"></path></g><g stroke-width="0.8pt">'
                '<defs><pattern id="pgfupat1" href="#pgfpat3"><g fill="#B3B3B3" stroke="#B3B3B3"></g></pattern></defs>'
                '<g fill="url(#pgfupat1)"><path d="M 39 0 C 39 21 21 39 0 39 Z"></path></g></g></g></svg></article>')
        fragment, _ = sanitize_document(page, latexml.BASE)
        latexml.fix_patterns(fragment)
        self.assertEqual(fragment.find("pattern").path["stroke"], "#B3B3B3")
        self.assertEqual(len(fragment.find_all("path")), 2)

    def test_unknown_pattern_step_left_empty(self):
        # lignes horizontales : tuile de 100pt, pas inconnu ; la tuile quitte quand même le dessin
        page = ('<article class="ltx_document"><svg class="ltx_picture" width="10" height="10"><g>'
                '<path d="M 0 0.69 L 138.37 0.69" style="fill:none"></path>'
                '<defs><pattern id="pgfupat1" href="#pgfpat3"><g fill="#000000" stroke="#000000"></g></pattern></defs>'
                '<g fill="url(#pgfupat1)"><path d="M -39 37 L 39 41 Z"></path></g></g></svg></article>')
        fragment, _ = sanitize_document(page, latexml.BASE)
        latexml.fix_patterns(fragment)
        self.assertIsNone(fragment.find("pattern").find("path"))
        self.assertEqual(len(fragment.find_all("path")), 1)

    def test_refs_in_code_and_comments_untouched(self):
        text = ("\\begin{verbatim}\\begin{tikzpicture}\\node{\\ref{a}};\\end{tikzpicture}\\end{verbatim}\n"
                "% \\begin{tikzpicture}\n\\ref{a}\n\\begin{tikzpicture}\\node{\\ref{a}};\\end{tikzpicture}")
        out = latexml._figure_refs(text, {"a": ("A3", None)})
        self.assertEqual(out.count("\\arxivreaderref"), 1)
        self.assertTrue(out.startswith("\\begin{verbatim}\\begin{tikzpicture}\\node{\\ref{a}}"))

    def test_tiny_images_lose_their_size(self):
        # « 0.86\normaltextwidth » : longueur de la classe, inconnue de LaTeXML (0) → image de 2 pixels
        fragment = BeautifulSoup('<img class="ltx_graphics" width="2" height="2" style="aspect-ratio:2/2;" src="a.png">'
                                 '<img class="ltx_graphics" width="300" height="200" src="b.png">', "html.parser")
        latexml.fix_tiny_images(fragment)
        first, second = fragment.find_all("img")
        self.assertEqual((first.get("width"), first.get("style")), (None, None))
        self.assertEqual(second["width"], "300")

    def test_figure_width_from_pdf(self):
        self.assertEqual(latexml._with_width("scale=0.5", 400), "width=200.00pt")
        self.assertEqual(latexml._with_width("width=3cm", 400), "width=3cm")
        self.assertEqual(latexml._with_width(None, 300.5), "width=300.50pt")

    @unittest.skipUnless(latexml._rasterizer(), "ni sips ni Ghostscript")
    def test_pdf_figures_become_png(self):
        from pypdf import PdfWriter
        copy = self.tmp / "src"
        (copy / "figs").mkdir(parents=True)
        writer = PdfWriter()
        writer.add_blank_page(width=144, height=72)
        with open(copy / "figs" / "plot.pdf", "wb") as handle:
            writer.write(handle)
        (copy / "main.tex").write_text("\\graphicspath{{figs/}}\\includegraphics[scale=0.5]{plot}", encoding="utf-8")
        latexml._raster_figures(copy, [copy])
        self.assertEqual((copy / "main.tex").read_text(), "\\graphicspath{{figs/}}\\includegraphics[width=72.00pt]"
                                                          "{arxivreader-figures/1.png}")
        self.assertTrue((copy / "arxivreader-figures" / "1.png").stat().st_size > 0)

    def test_biblatex_bbl_becomes_thebibliography(self):
        cwd = self.tmp / "bl"
        cwd.mkdir()
        (cwd / "main.tex").write_text("\\addbibresource{b.bib}\\begin{document}\\cite{k}\\printbibliography[title=R]"
                                      "\\end{document}", encoding="utf-8")
        (cwd / "main.bbl").write_text("\\refsection{0}\\entry{k}{article}{}\\name{author}{1}{}{{{\\hash{x}\\family={Doe}"
                                      "\\given={Jane}}}}\\field{title}{A & B}\\field{journaltitle}{J}\\field{year}{2020}"
                                      "\\endentry\\endrefsection", encoding="utf-8")
        paper = {"id": "x", "source": "tex"}
        self.assertTrue(latexml._lost_bibliography("<p></p>", cwd))
        self.assertTrue(latexml._biblatex_bibliography(paper, cwd, "main.tex"))
        self.assertIn("\\input{arxivreader-bibliography}", (cwd / "main.tex").read_text())
        self.assertIn("\\bibitem{k} Jane Doe, A \\& B, J, 2020.", (cwd / "arxivreader-bibliography.tex").read_text())

    def test_biblatex_text_and_single_list(self):
        self.assertEqual(latexml._bib_text(r"On $L^2$ for $\bar\partial$ & A_B"), r"On $L^2$ for $\bar\partial$ \& A\_B")
        self.assertIn(r"\$\textbackslash{}left\{x", latexml._bib_text(r"$\left{x\right.$"))  # formule déséquilibrée
        cwd = self.tmp / "two"
        cwd.mkdir()
        (cwd / "main.tex").write_text("% \\printbibliography\n\\printbibliography[keyword=a]\n\\printbibliography[keyword=b]"
                                      "\n\\end{document}", encoding="utf-8")
        (cwd / "main.bbl").write_text("\\entry{k}{article}{}\\field{title}{T}\\endentry", encoding="utf-8")
        self.assertTrue(latexml._biblatex_bibliography({"id": "x", "source": "tex"}, cwd, "main.tex"))
        self.assertEqual((cwd / "main.tex").read_text(),
                         "% \\printbibliography\n\\input{arxivreader-bibliography}\n\n\\end{document}")

    def test_figures_only_real_pdfs_inside_the_folder(self):
        root = self.tmp / "proj"
        (root / "src").mkdir(parents=True)
        (root / "src.pdf").write_bytes(b"%PDF-1.4 outside")  # frère du dossier : jamais lu
        (root / "src" / "ps.pdf").write_bytes(b"%!PS-Adobe-3.0\n(x) = flush")  # PostScript nommé .pdf : refusé
        (root / "src" / "main.tex").write_text("\\includegraphics{.} \\includegraphics{ps}\n"
                                               "\\begin{verbatim}\\includegraphics{ps}\\end{verbatim}", encoding="utf-8")
        with mock.patch.object(latexml, "_rasterizer", return_value="gs"), \
             mock.patch.object(latexml, "_rasterize", side_effect=AssertionError("no conversion expected")):
            latexml._raster_figures(root / "src", [root / "src"])
        self.assertNotIn("arxivreader-figures", (root / "src" / "main.tex").read_text())

    def test_page_option_gets_its_own_image(self):
        from pypdf import PdfWriter
        copy = self.tmp / "pages"
        copy.mkdir()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=50)
        writer.add_blank_page(width=200, height=50)
        with open(copy / "slides.pdf", "wb") as handle:
            writer.write(handle)
        (copy / "main.tex").write_text("\\includegraphics[page=2]{slides}\\includegraphics{slides}", encoding="utf-8")
        calls = []

        def fake(tool, source, target, size, page, timeout):
            calls.append((size, page))
            target.write_bytes(b"png")
            return True
        with mock.patch.object(latexml, "_rasterizer", return_value="gs"), mock.patch.object(latexml, "_rasterize", fake):
            latexml._raster_figures(copy, [copy])
        self.assertEqual(calls, [((200.0, 50.0), 2), ((100.0, 50.0), 1)])
        self.assertEqual((copy / "main.tex").read_text(), "\\includegraphics[width=200.00pt]{arxivreader-figures/1.png}"
                                                          "\\includegraphics[width=100.00pt]{arxivreader-figures/2.png}")

    def test_failed_bibliography_retry_keeps_first_conversion(self):
        db.save_paper({"id": "tex-r", "arxiv_id": "", "version": None, "title": "T", "authors": [], "abstract": "",
                       "categories": [], "primary_category": "", "published": None, "updated": None, "doi": None,
                       "journal_ref": None, "comment": "", "source": "local-tex", "in_library": 1})
        work = self.tmp / "w"
        work.mkdir()
        (work / "main.tex").write_text("\\bibliography{refs}", encoding="utf-8")
        page = '<article class="ltx_document"><p>ok</p></article>'
        runs = iter([page, latexml.LatexmlError("timeout")])

        def run(*args, **kwargs):
            value = next(runs)
            if isinstance(value, Exception):
                raise value
            return value
        (latexml.paper_dir("tex-r")).mkdir(parents=True, exist_ok=True)
        (latexml.paper_dir("tex-r") / "structure.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(latexml, "find", return_value="/bin/latexmlc"), \
             mock.patch.object(latexml, "_source", return_value=(work, "main.tex", [], False)), \
             mock.patch.object(latexml, "_run", run), mock.patch.object(latexml, "_bbl", return_value=True), \
             mock.patch.object(texfile, "report", return_value={"compiledAt": "A"}):
            self.assertEqual(latexml.convert("tex-r")["state"], "ready")
        self.assertIn("ok", (latexml.html_dir("tex-r") / "content.html").read_text())


if __name__ == "__main__":
    unittest.main()
