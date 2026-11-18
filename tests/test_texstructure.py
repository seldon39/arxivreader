"""Structure lue dans la source TeX (articles sans version HTML)."""

import unittest

from app import texstructure
from app.texstructure import extract

SOURCE = r"""
\documentclass{amsart}
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{introthm}{Theorem}
\renewcommand{\theintrothm}{\Alph{introthm}}
\theoremstyle{remark}
\newtheorem{remark}[theorem]{Remark}
\newcommand{\RR}{\mathbb{R}}
\DeclareMathOperator{\Ker}{Ker}
\begin{document}
\begin{introthm}\label{thm:A} Main result, see Theorem~\ref{thm:main}. \end{introthm}
\section{Setting}
\begin{lemma}\label{lem:one} An identity \begin{equation}\label{eq:id} 1=1 \end{equation} \end{lemma}
\begin{proof} Easy, see \cite[Theorem 3]{Ma}. \end{proof}
\begin{theorem}\label{thm:main} Statement. \end{theorem}
\begin{remark} A comment. \end{remark}
\begin{proof} By \eqref{eq:id} and \cref{lem:one}. \end{proof}
\begin{proof} Second argument, by Lemma~\ref{lem:two}. \end{proof}
\begin{comment}
\begin{lemma} Never printed. \end{lemma}
\end{comment}
\begin{lemma}\label[lemma]{lem:two} Another. \end{lemma}
\proof Old-style proof using Lemma \ref{lem:one}. \endproof
\appendix
\section{Proof of Theorem \ref{thm:A}}
We use Lemma~\ref{lem:two}.
\begin{lemma}\label{lem:inner} Inner step. \end{lemma}
\noindent\textbf{Proof of Lemma \ref{lem:inner}.} It follows from Theorem \ref{thm:main}. \qed
\begin{thebibliography}{9}
\bibitem{Ma} X. Ma and G. Marinescu, Holomorphic Morse inequalities and Bergman kernels, 2007. doi:10.1007/978-3-7643-8115-8
\end{thebibliography}
\end{document}
"""


class TexStructure(unittest.TestCase):
    def setUp(self):
        self.structure = extract(SOURCE)
        self.nodes = {n["label"]: n for n in self.structure["nodes"]}
        self.ids = {n["id"]: n["label"] for n in self.structure["nodes"]}

    def uses(self, label):
        return sorted(self.ids[u] for u in self.nodes[label]["uses"])

    def test_numbering(self):
        self.assertEqual(list(self.nodes), ["Theorem A", "Lemma 1.1", "Theorem 1.2", "Remark 1.3", "Lemma 1.4", "Lemma A.1"])
        self.assertEqual(self.nodes["Lemma 1.1"]["anchors"][:1], ["theorem.1.1"])  # ancre de hyperref (compteur partagé)

    def test_proofs_and_dependencies(self):
        self.assertEqual(len(self.nodes["Theorem 1.2"]["proofs"]), 2)  # après la remarque, puis deuxième preuve
        self.assertEqual(self.nodes["Remark 1.3"]["proofs"], [])
        self.assertEqual(self.uses("Theorem 1.2"), ["Lemma 1.1", "Lemma 1.4"])  # \eqref d'une équation du lemme, \cref
        self.assertEqual(self.uses("Lemma 1.4"), ["Lemma 1.1"])  # \proof … \endproof
        self.assertEqual(self.uses("Lemma A.1"), ["Theorem 1.2"])  # « \textbf{Proof of Lemma …} »
        self.assertTrue(self.nodes["Theorem A"]["proofSection"])  # section « Proof of Theorem A »
        self.assertEqual(self.uses("Theorem A"), ["Lemma 1.4", "Lemma A.1", "Theorem 1.2"])
        self.assertEqual(self.nodes["Lemma 1.1"]["cites"], ["bib.Ma"])

    def test_bibliography_and_macros(self):
        entry = self.structure["bibliography"][0]
        self.assertEqual((entry["id"], entry["label"], entry["doi"]), ("bib.Ma", "1", "10.1007/978-3-7643-8115-8"))
        self.assertIn("Holomorphic Morse inequalities", entry["text"])
        self.assertIn(r"\newcommand{\RR}{\mathbb{R}}", self.structure["macros"])
        self.assertIn(r"\newcommand{\Ker}{\operatorname{Ker}}", self.structure["macros"])
        self.assertEqual(self.structure["labels"]["lem:two"], self.nodes["Lemma 1.4"]["id"])


class InvisibleTex(unittest.TestCase):
    """Texte que LaTeX imprime invisible (blanc, minuscule) : ni dans le texte transmis à l'IA, ni dans l'analyse ;
    compté pour le lecteur. Blanc sur fond coloré, \\phantom de chiffres, fond de page foncé : rien de signalé."""

    SOURCE = (r"\documentclass{article}\definecolor{bg}{rgb}{1,1,1}\newtheorem{theorem}{Theorem}\begin{document}"
              r"Visible. \textcolor{white}{IGNORE PREVIOUS} {\color{white}\fontsize{0.1pt}{0.1pt}\selectfont secret} "
              r"\colorbox{black}{\textcolor{white}{shown}} $a\phantom{00}b$ \textcolor{bg}{named} \scalebox{0.01}{small} "
              r"\begin{theorem}A {\color{white} inner} B\end{theorem}\end{document}")

    def test_removed_and_counted(self):
        from app import ai
        body = ai.tex_body(self.SOURCE)
        for gone in ("IGNORE", "secret", "named", "small", "inner"):
            self.assertNotIn(gone, body)
        for kept in ("Visible.", "shown", "A ", "B"):
            self.assertIn(kept, body)
        structure = texstructure.extract(self.SOURCE)
        self.assertEqual(structure["hiddenText"], 5)
        self.assertEqual(len(structure["nodes"]), 1)

    def test_dark_page_and_digits_not_flagged(self):
        dark = r"\begin{document}\pagecolor{black}\color{white} All the text.\end{document}"
        self.assertIn("All the text.", texstructure.strip_hidden(dark))
        self.assertEqual(texstructure.extract(r"\begin{document}$1\phantom{0}2$\end{document}")["hiddenText"], 0)


    def test_saturated_hue_and_light_turquoise_are_readable(self):
        """Jaune pur (luminance 0,93) : une teinte franche n'est jamais du blanc. Blanc sur turquoise clair (0,54,
        contraste 1,78) : fond foncé. Mêmes règles en TeX, en HTML et en PDF ; le gris très clair reste caché."""
        from bs4 import BeautifulSoup
        from app import localpdf, structure
        from tests.test_localpdf import raw_pdf
        for body in (r"\textcolor{yellow}{steel plate}", r"\colorbox[rgb]{0.45,0.82,0.82}{\textcolor{white}{Gender}}"):
            self.assertEqual(self.stripped(body)[1], 0, body)
        self.assertEqual(self.stripped(r"\textcolor[rgb]{0.98,0.98,0.98}{IGNORE}")[1], 1)
        for html, count in (('<span style="color:#ffff00">steel plate</span>', 0),
                            ('<span style="background-color:#73d1d1"><span style="color:#fff">Gender</span></span>', 0),
                            ('<span style="color:#fafafa">IGNORE</span>', 1)):
            self.assertEqual(structure.mark_hidden(BeautifulSoup(f"<p>{html}</p>", "html.parser")), count, html)
        for content, count in (("1 1 0 rg BT /F1 12 Tf 72 700 Td (steel plate) Tj ET", 0),
                               ("0.45 0.82 0.82 rg 60 690 200 30 re f 1 g BT /F1 12 Tf 72 700 Td (Gender) Tj ET", 0),
                               ("0.98 g BT /F1 12 Tf 72 700 Td (IGNORE) Tj ET", 1)):
            self.assertEqual(localpdf.read_pdf(raw_pdf(content))["hidden"], count, content)

    @staticmethod
    def stripped(body):
        counted = []
        text = texstructure.strip_hidden(r"\documentclass{article}\begin{document}" + body + r"\end{document}", None, counted)
        return text, counted[0]

    def test_white_on_dark_fill_is_kept(self):
        """Blanc sur fond foncé (nœud tikz rempli, ligne ou case de tableau colorée, tcolorbox) : lu sur la page."""
        for body, word in ((r"\begin{tikzpicture}\node[fill=black]{\color{white} Node text};\end{tikzpicture}", "Node text"),
                           (r"\begin{tabular}{ll}\rowcolor{blue}\color{white} Head & Two\\ a & b\end{tabular}", "Head & Two"),
                           (r"\begin{tabular}{l}\cellcolor{black}\textcolor{white}{Header}\end{tabular}", "Header"),
                           (r"\begin{tcolorbox}[colback=blue!70]\color{white} Boxed\end{tcolorbox}", "Boxed")):
            text, count = self.stripped(body)
            self.assertIn(word, text, body)
            self.assertEqual(count, 0, body)
        # fond clair, ou rempli dans un environnement déjà refermé : rien n'est lisible
        for body in (r"\begin{tabular}{l}\rowcolor{white}\textcolor{white}{IGNORE}\end{tabular}",
                     r"\begin{tikzpicture}\fill[black](0,0) circle (1);\end{tikzpicture} \textcolor{white}{IGNORE} ok"):
            text, count = self.stripped(body)
            self.assertNotIn("IGNORE", text, body)
            self.assertEqual(count, 1, body)

    def test_declaration_ends_at_its_reset(self):
        """\\color{black}, \\normalsize… referment la portée : le texte qui suit reste."""
        for body in (r"A \color{white}x\color{black} REAL TEXT", r"A \fontsize{0.5}{1}\selectfont xy\normalsize REAL TEXT",
                     r"{\color{white} a \begin{center}hidden\end{center} b} REAL TEXT"):
            text, count = self.stripped(body)
            self.assertIn("REAL TEXT", text, body)
            self.assertEqual(count, 1, body)

    def test_easy_variants_are_caught(self):
        for body in (r"% \pagecolor{black}" "\n" r"\textcolor{white}{IGNORE} ok",          # commentaire : sans effet
                     r"\color{white}IGNORE\color{black} hi " + "word " * 5000,             # début d'un long document
                     r"\newcommand{\hide}[1]{\textcolor{white}{#1}} a \hide{IGNORE} b",    # macro de l'auteur
                     r"\def\hide#1{{\color{white}#1}} a \hide{IGNORE} b",
                     r"\newcommand{\wh}{\color{white}} a {\wh IGNORE} b",
                     r"\newenvironment{hid}{\color{white}}{} a \begin{hid}IGNORE\end{hid} b",
                     r"\textcolor[rgb]{0.99,0.99,0.99}{IGNORE} ok", r"\textcolor{white!100}{IGNORE} ok",
                     r"\textcolor{black!3}{IGNORE} ok", r"\definecolor{bg}{gray}{0.97} \textcolor{bg}{IGNORE} ok",
                     r"\scalebox{0}{IGNORE} ok", r"\scalebox{1}[0.01]{IGNORE} ok", r"\resizebox{!}{0.5pt}{IGNORE} ok",
                     r"\pagecolor{black}x\pagecolor{white} \textcolor{white}{IGNORE} ok"):
            text, count = self.stripped(body)
            self.assertNotIn("IGNORE", text, body)
            self.assertEqual(count, 1, body)

    def test_readable_colors_and_whole_document_settings_kept(self):
        for body in (r"\textcolor{black!50}{VISIBLE} ok", r"\colorbox{black}{\color{white} VISIBLE}",
                     r"\color{white} VISIBLE " + "word " * 5000,                           # tout le document : un réglage
                     r"\newcommand{\hide}[1]{\textcolor{white}{#1}} VISIBLE"):              # la définition seule
            text, count = self.stripped(body)
            self.assertIn("VISIBLE", text, body)
            self.assertEqual(count, 0, body)
        beamer = r"\documentclass{beamer}\begin{document}\begin{frame}\color{white} VISIBLE\end{frame}\end{document}"
        self.assertIn("VISIBLE", texstructure.strip_hidden(beamer))

    def test_second_review_cases(self):
        """Fonds foncés posés ailleurs (macro, préambule, style tikz, colonne) : gardé ; détours simples : retirés."""
        def run(preamble, body):
            counted = []
            text = texstructure.strip_hidden(r"\documentclass{article}" + preamble + r"\begin{document}" + "Opening words. " * 10
                                             + body + r"\end{document}", None, counted)
            return text, counted[0]
        kept = [(r"\newcommand{\wt}[1]{\textcolor{white}{#1}}", r"\colorbox{black}{\wt{Title}}", "Title"),
                (r"\newcommand{\hcell}[1]{\cellcolor{black}\textcolor{white}{#1}}",
                 r"\begin{tabular}{cc}\hcell{Name} & b\end{tabular}", "Name"),
                (r"\newcommand{\wh}{\color{white}}", r"\begin{tikzpicture}\node[fill=black]{\wh Label};\end{tikzpicture}", "Label"),
                (r"\definecolor{shadecolor}{gray}{0.2}", r"\begin{shaded}\color{white} Shaded\end{shaded}", "Shaded"),
                (r"\tcbset{colback=black}", r"\begin{tcolorbox}\color{white} Boxed\end{tcolorbox}", "Boxed"),
                (r"\newtcolorbox{mybox}{colback=blue!50!black}", r"\begin{mybox}\color{white} Mine\end{mybox}", "Mine"),
                (r"\tikzset{box/.style={draw, fill=black}}", r"\begin{tikzpicture}\node[box]{\color{white} Styled};\end{tikzpicture}", "Styled"),
                ("", r"\begin{tabular}{>{\color{white}\columncolor{black}}c}Head\end{tabular}", "Head"),
                (r"\definecolor{acc}{rgb}{0,0,1}", r"\textcolor{acc}{Blue} x\definecolor{acc}{rgb}{1,1,1}", "Blue")]
        for preamble, body, word in kept:
            self.assertEqual((word in run(preamble, body)[0], run(preamble, body)[1]), (True, 0), body)
        gone = [("", r"\newcommand{\x}{\pagecolor{black}} \textcolor{white}{SECRET}"), ("", r"\pagecolor{zzz}x \textcolor{white}{SECRET}"),
                ("", r"Hello fill=black \textcolor{white}{SECRET}"), ("", r"[fill=none] \textcolor{white}{SECRET}"),
                ("", r"\colorbox{white}{\textcolor{white}{SECRET}}"), (r"\definecolor{c}{rgb}{1,1,1}", r"\textcolor{c}{SECRET}\definecolor{c}{rgb}{0,0,0}"),
                (r"\definecolor{h}{hsb}{0,0,1}", r"\textcolor{h}{SECRET}"), ("", r"\textcolor[named]{White}{SECRET}"),
                ("", r"\textcolor{-black}{SECRET}"), ("", r"\textcolor{white}\bgroup SECRET\egroup"),
                ("", r"{\color{white} A \verb|\color| SECRET}"), ("", r"\color{white} SECRET " + "x " * 10100),
                (r"\NewDocumentCommand{\hide}{m}{\textcolor{white}{#1}}", r"\hide{SECRET}"),
                (r"\newcommand{\hide}[2]{\textcolor{white}{#2}}", r"\hide{a}{SECRET}"),
                ("", r"\newcommand{\p}{\textcolor{white}{SECRET}}\p"), ("", r"\tikz \node[text=white]{SECRET};"),
                ("", r"\tikz \node[opacity=0]{SECRET};"), ("", r"\texttransparent{0}{SECRET}"), ("", r"$\mathcolor{white}{SECRET}$"),
                ("", r"{\fontsize{0.5}{1}\rmfamily\selectfont SECRET}")]
        for preamble, body in gone:
            text, count = run(preamble, body)
            self.assertNotIn("SECRET", text, body)
            self.assertGreaterEqual(count, 1, body)

    def test_third_review_cases(self):
        """Titres de boîtes, figures sur image, macros qui dessinent le fond : gardés ; rangées, options, maths : retirés."""
        def run(preamble, body, opening="Opening words. " * 10):
            counted = []
            text = texstructure.strip_hidden(r"\documentclass{article}" + preamble + r"\begin{document}" + opening
                                             + body + r"\end{document}", None, counted)
            return text, counted[0]
        kept = [("", r"\begin{tcolorbox}[colframe=black,fonttitle=\bfseries\color{white},title=Theorem 1]Body text important.\end{tcolorbox}",
                 ("Theorem 1", "Body text important")),
                ("", r"\begin{tcolorbox}[colframe=blue!50!black,title={\textcolor{white}{Theorem 1}}]Body\end{tcolorbox}", ("Theorem 1",)),
                ("", r"\begin{mdframed}[frametitle={\textcolor{white}{Thm}},frametitlebackgroundcolor=black]Body\end{mdframed}", ("Thm",)),
                ("", r"\begin{tikzpicture}\node[inner sep=0]{\includegraphics{bg.png}};\node[text=white] at (0,0){Label};\end{tikzpicture}",
                 ("Label",)),
                ("", r"\begin{tikzpicture}\node[text=white](a) at(1,1){Hello};\begin{scope}[on background layer]"
                     r"\fill[black](a.south west) rectangle(a.north east);\end{scope}\end{tikzpicture}", ("Hello",)),
                (r"\tikzset{base/.style={fill=black},vertex/.style={base,circle}}",
                 r"\begin{tikzpicture}\node[vertex]{\textcolor{white}{Vertex}};\end{tikzpicture}", ("Vertex",)),
                (r"\newcommand{\wh}[1]{\textcolor{white}{#1}}\newcommand{\tagb}[1]{\colorbox{black}{#1}}", r"\tagb{\wh{Tagged}}", ("Tagged",)),
                (r"\newenvironment{darkbox}{\par\colorbox{black}\bgroup\color{white}}{\egroup}",
                 r"\begin{darkbox}Hello readable world\end{darkbox}", ("Hello readable world",)),
                ("", r"\begin{tikzcd}[background color=black]\color{white}Arrow\arrow[r]&B\end{tikzcd}", ("Arrow",)),
                ("", r"\todo[color=black]{\textcolor{white}{fix this}}", ("fix this",)),
                ("", r"\sethlcolor{black}\hl{\textcolor{white}{highlighted}}", ("highlighted",)),
                ("", r"\begin{tabular}{l}\rowcolor{black}\textcolor{white}{Dark row}\\ b\end{tabular}", ("Dark row",))]
        for preamble, body, words in kept:
            text, count = run(preamble, body)
            self.assertEqual(([word in text for word in words], count), ([True] * len(words), 0), body)
        text, count = run("", r"\begin{theorem}[\color{white} Name]Body text here\end{theorem}")
        self.assertIn("Body text here", text)  # le nom (blanc) seul est retiré, pas l'énoncé
        gone = [(r"\tcbset{colback=black}", r"\begin{tcolorbox}[colback=white]\color{white}SECRET\end{tcolorbox}"),
                (r"\newmdenv[backgroundcolor=black]{dk}", r"\begin{mdframed}[backgroundcolor=white]\color{white}SECRET\end{mdframed}"),
                ("", r"[fill=black] \textcolor{white}{SECRET}"), ("", r"\texttt{[fill=black]} \textcolor{white}{SECRET}"),
                ("", r"\begin{tabular}{l}\rowcolor{black}x\\ \color{white}SECRET\\\end{tabular}"),
                ("", r"\begin{tabular}{ll}\cellcolor{black}x & y\\ \textcolor{white}{SECRET}\end{tabular}"),
                ("", r"{\transparent{0}SECRET}"), ("", r"\begin{tikzpicture}[opacity=0]\node{SECRET};\end{tikzpicture}")]
        for preamble, body in gone:
            text, count = run(preamble, body)
            self.assertNotIn("SECRET", text, body)
            self.assertGreaterEqual(count, 1, body)
        visible = "Visible paper text. " * 20
        for body in (r"$\color{white}SECRET$ ", r"$$\color{white}SECRET$$ ", r"\begingroup\color{white}SECRET\endgroup ",
                     r"\(\color{white}SECRET\) "):
            text, count = run("", body + visible, opening="")
            self.assertNotIn("SECRET", text, body)
            self.assertIn("Visible paper text.", text, body)
        self.assertIn(r"\\[2pt] \color{white} Tail", run("", r"$x$ a\\[2pt] \color{white} Tail", opening="")[0])  # réglage d'ensemble

    def test_fourth_review_cases(self):
        """Boîte foncée autour d'une liste, cases et lignes d'alignement, \rowcolors avant le tableau : gardés."""
        def run(body):
            counted = []
            text = texstructure.strip_hidden(r"\documentclass{article}\begin{document}" + "Opening words. " * 10 + body
                                             + r"\end{document}", None, counted)
            return text, counted[0]
        kept = [(r"\begin{tcolorbox}[colback=black]\begin{itemize}\item \textcolor{white}{Item}\end{itemize}\end{tcolorbox}", "Item"),
                (r"\begin{mdframed}[backgroundcolor=black]\begin{center}\color{white} Centered\end{center}\end{mdframed}", "Centered"),
                (r"\begin{tcolorbox}[colback=black]" + "word " * 1500 + r"\textcolor{white}{Far}\end{tcolorbox}", "Far"),
                (r"\begin{tabular}{ll}\color{white} a & Next \\ Row & c\end{tabular}", "Next"),
                (r"\begin{tabular}{ll}\color{white} a & b \\ Row & c\end{tabular}", "Row"),
                (r"\begin{align} a &= b \color{white} \\ c &= Dee \end{align}", "Dee"),
                (r"\rowcolors{1}{black}{black}\begin{tabular}{ll}\textcolor{white}{Aa} & b\\\end{tabular}", "Aa"),
                (r"\begin{tabular}{ll}\rowcolor{black}\makecell{x\\y} & \textcolor{white}{Cee}\end{tabular}", "Cee")]
        for body, word in kept:
            self.assertIn(word, run(body)[0], body)
        self.assertNotIn("SECRET", run(r"\begin{tabular}{ll}\color{white} SECRET & b\end{tabular}")[0])

    def test_never_executed_text_is_ignored(self):
        """\verb, \lstinline, verbatim, branche fausse d'une conditionnelle : ni texte caché, ni réglage d'ensemble
        (beamer, \pagecolor) ; le reste de l'article est gardé."""
        visible = "Visible paper text that matters. " * 10
        def run(body):
            counted = []
            text = texstructure.strip_hidden(r"\documentclass{article}\begin{document}" + "Opening words. " * 10 + body
                                             + visible + r"\end{document}", None, counted)
            return "matters" in text, "SECRET" in text, counted[0]
        for body in (r"we write \verb|\color{white}| for labels ", r"\verb+\color{black!3}+ ", r"\lstinline|\color{white}| ",
                     r"\begin{verbatim}\color{white}\end{verbatim} ", r"\iffalse \color{white}\fi ", r"\ifnum0=1 \color{white}\fi "):
            self.assertEqual(run(body), (True, False, 0), body)
        for body in (r"\verb|\documentclass{beamer}| \textcolor{white}{SECRET} ", r"\ifnum0=1 \documentclass{beamer}\fi \textcolor{white}{SECRET} ",
                     r"\verb|\pagecolor{black}| \textcolor{white}{SECRET} ", r"\verb|%| \textcolor{white}{SECRET} ",
                     r"\ifnum0=1 x\else \textcolor{white}{SECRET}\fi ", r"\ifnum1=1 \textcolor{white}{SECRET}\fi "):
            self.assertEqual(run(body), (True, False, 1), body)

    def test_nested_declarations_stay_fast(self):
        import time
        for body in (r"{\color{white}" * 4000 + "x" + "}" * 4000, r"\color{white}{" * 4000 + "x" + "}" * 4000,
                     "a b $x$ \\color{white} z " * 40000, "[a] [ \\color{white} z ] " * 40000,
                     r"\begin{tabular}{ll}" + r"\cellcolor{black}\textcolor{white}{a} & b \\" * 20000 + r"\end{tabular}"):
            start = time.monotonic()
            texstructure.strip_hidden(r"\documentclass{article}\begin{document}" + "Words. " * 100 + body + r"\end{document}")
            self.assertLess(time.monotonic() - start, 5)

class HiddenTextReanalysis(unittest.TestCase):
    def test_structure_before_hidden_text_is_redone(self):
        """Structure d'un article TeX analysée avant le retrait du texte invisible (version 18) : refaite au
        chargement, la consigne cachée ne reste pas dans l'énoncé envoyé à l'IA."""
        import json
        import pathlib
        import tempfile
        from app import db, library
        saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = tmp / "test.db", tmp, tmp / "papers"
        self.addCleanup(lambda: (setattr(db, "DB_PATH", saved[0]), setattr(db, "LIBRARY", saved[1]), setattr(library, "PAPERS", saved[2])))
        db.init()
        source = (r"\documentclass{article}\usepackage{xcolor}\newtheorem{thm}{Theorem}\begin{document}\section{A}"
                  r"\begin{thm}\label{t}Every group is abelian.\textcolor{white}{SECRETINJECTION}\end{thm}\end{document}")
        old = extract(source)
        old["nodes"][0]["tex"] += r"\textcolor{white}{SECRETINJECTION}"  # ce que gardait l'analyse d'avant
        old.update(version=18, hiddenText=0, source="tex")
        folder = library.paper_dir("2601.99999")
        folder.mkdir(parents=True)
        (folder / "structure.json").write_text(json.dumps(old), encoding="utf-8")
        (folder / "source.tex").write_text(source, encoding="utf-8")
        structure = library.load_structure("2601.99999")
        self.assertFalse(any("SECRETINJECTION" in (node.get("tex") or "") for node in structure["nodes"]))
        self.assertEqual(structure["hiddenText"], 1)


class PdfSpotsTest(unittest.TestCase):
    """Page et hauteur des énoncés, lues dans les ancres hyperref du PDF."""

    def test_pages_and_tops(self):
        import io
        import pypdf
        from pypdf.generic import Destination, Fit
        from app.texstructure import pages_for
        writer = pypdf.PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=612, height=792)
        for name, top in (("lemma.2.3", 500), ("section.2", 700)):  # ancres /XYZ de hyperref, page 2
            writer.add_named_destination_object(Destination(name, writer.pages[1].indirect_reference, Fit.xyz(left=0, top=top)))
        buffer = io.BytesIO()
        writer.write(buffer)
        structure = {"nodes": [{"id": "S2.Thm3", "kind": "lemma", "label": "Lemma 2.3"}],
                     "toc": [{"id": "S2", "title": "2 Setting", "level": 1}]}
        located = pages_for(structure, buffer.getvalue())
        self.assertEqual(located["pages"], {"S2.Thm3": 2, "S2": 2})
        self.assertEqual(located["tops"], {"S2.Thm3": 500, "S2": 700})

    def test_book_numbered_by_chapter(self):
        """Livre lu dans le HTML d'arXiv : LaTeXML numérote sections et énoncés à la suite (« 3 », « 3.2 », annexe
        « 9.B.1 »), le PDF dans leur chapitre (« 2.1 », « 2.1.2 », annexe « A.1.1 »)."""
        import io
        import pypdf
        from pypdf.generic import Destination, Fit
        from app.texstructure import pages_for
        writer = pypdf.PdfWriter()
        for _ in range(4):
            writer.add_blank_page(width=612, height=792)
        for name, page in (("subsection.2.1.2", 1), ("section.2.1", 1), ("subsection.A.1.1", 3), ("appendix.A", 3)):
            writer.add_named_destination_object(Destination(name, writer.pages[page].indirect_reference, Fit.xyz(left=0, top=400)))
        buffer = io.BytesIO()
        writer.write(buffer)
        toc = [("chapter1", "Chapter 1 Calculus", "chapter", 0), ("S1", "1 Curves", "section", 1), ("S2", "2 Manifolds", "section", 1),
               ("chapter2", "Chapter 2 Mappings", "chapter", 0), ("S3", "3 Topology", "section", 1),
               ("chapter9", "Chapter 9 A primer", "chapter", 0), ("chapter9.A2", "Appendix 9.B Seminorms", "appendix", 1)]
        structure = {"book": True, "toc": [{"id": i, "title": t, "kind": k, "level": level} for i, t, k, level in toc],
                     "nodes": [{"id": "S3.SS2", "kind": "lemma", "label": "Lemma 3.2", "section": "S3"},
                               {"id": "chapter9.A2.SS1", "kind": "definition", "label": "Definition 9.B.1", "section": "chapter9.A2"}]}
        located = pages_for(structure, buffer.getvalue())["pages"]
        self.assertEqual((located["S3.SS2"], located["S3"]), (2, 2))
        self.assertEqual((located["chapter9.A2.SS1"], located["chapter9"]), (4, 4))

    def test_book_numbered_by_chapter_kept(self):
        """Livre dont les énoncés sont numérotés par chapitre (« Theorem 2.1 » en section 3 du HTML, 2.1 du PDF) :
        aucun numéro refait (subsection.2.1.1 est une autre page)."""
        import io
        import pypdf
        from pypdf.generic import Destination, Fit
        from app.texstructure import pages_for
        writer = pypdf.PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=612, height=792)
        for name, page in (("theorem.2.1", 1), ("subsection.2.1.1", 2)):
            writer.add_named_destination_object(Destination(name, writer.pages[page].indirect_reference, Fit.xyz(left=0, top=400)))
        buffer = io.BytesIO()
        writer.write(buffer)
        toc = [("chapter1", "Chapter 1 One", "chapter", 0), ("S1", "1 Start", "section", 1), ("S2", "2 More", "section", 1),
               ("chapter2", "Chapter 2 Two", "chapter", 0), ("S3", "3 Next", "section", 1)]
        structure = {"book": True, "toc": [{"id": i, "title": t, "kind": k, "level": level} for i, t, k, level in toc],
                     "nodes": [{"id": "S3.Thm1", "kind": "theorem", "label": "Theorem 2.1", "section": "S3"}]}
        self.assertEqual(pages_for(structure, buffer.getvalue())["pages"]["S3.Thm1"], 2)


if __name__ == "__main__":
    unittest.main()
