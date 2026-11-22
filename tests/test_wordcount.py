"""Nombre de mots d'un article (densité d'énoncés de l'avertissement « IA » du graphe) : formules, bibliographie et
commandes à part, comme dans l'étude qui a fixé les seuils."""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import db, library, main, wordcount

TEX = r"""\documentclass{amsart}
\newtheorem{theorem}{Theorem}
\title{Ignored preamble words}
\begin{document}
Every compact manifold carries a metric. % un commentaire ignoré
\begin{theorem}\label{thm:main} Let $X$ be compact, see \cite[Thm 2]{Hor} and \eqref{eq:a}. \end{theorem}
\begin{equation}\label{eq:a} a = bc + \mathrm{dd} \end{equation}
\[ \int_X \omega \]
\begin{align*} x &= yy \end{align*}
Thus \emph{all} is well.
\begin{thebibliography}{9} \bibitem{Hor} Hormander, Notions of convexity. \end{thebibliography}
\end{document}
"""

HTML = ('<section class="ltx_section"><p>Every compact manifold carries a metric '
        '<math><mi>X</mi><mtext>texte</mtext></math> here.</p><script>var ignored = 1;</script>'
        '<style>.hidden { color: red }</style></section>'
        '<section id="bib" class="ltx_bibliography"><ul><li>Hormander, Notions of convexity.</li></ul></section>')


class Counts(unittest.TestCase):
    def test_tex_body_without_formulas_bibliography_references_or_commands(self):
        # Every compact manifold carries metric / Let be compact see and / Thus all is well, et les noms d'environnement
        # laissés par \begin et \end (theorem ×2, document) : compte de l'étude, gardé tel quel pour ses seuils
        self.assertEqual(wordcount.tex_words(TEX), 17)

    def test_html_without_math_scripts_styles_or_bibliography(self):
        self.assertEqual(wordcount.html_words(HTML), 6)  # Every compact manifold carries metric here


class PaperWords(unittest.TestCase):
    def setUp(self):
        self.saved = (library.PAPERS, db.DB_PATH, db.LIBRARY)
        library.PAPERS = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = library.PAPERS / "test.db", library.PAPERS
        db.init()

    def tearDown(self):
        library.PAPERS, db.DB_PATH, db.LIBRARY = self.saved

    def test_tex_source_first_then_html_then_nothing(self):
        folder = library.paper_dir("p")
        folder.mkdir(parents=True)
        self.assertIsNone(wordcount.words("p"))  # PDF seul
        (folder / "content.html").write_text(HTML, encoding="utf-8")
        self.assertEqual(wordcount.words("p"), 6)
        (folder / "source.tex").write_text(TEX, encoding="utf-8")
        self.assertEqual(wordcount.words("p"), 17)  # même texte que l'étude : la source TeX

    def test_html_made_by_latexml(self):
        folder = library.paper_dir("q") / "html"
        folder.mkdir(parents=True)
        (folder / "content.html").write_text(HTML, encoding="utf-8")
        self.assertEqual(wordcount.words("q"), 6)

    def test_sent_with_the_paper(self):
        db.save_paper({"id": "2101.00003", "arxiv_id": "2101.00003", "version": 1, "title": "T", "authors": [],
                       "abstract": "", "categories": []})
        folder = library.paper_dir("2101.00003")
        folder.mkdir(parents=True)
        (folder / "source.tex").write_text(TEX, encoding="utf-8")
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        with mock.patch.object(library, "ensure_ready", return_value={**db.get_paper("2101.00003"), "source": "tex"}), \
                mock.patch.object(library, "load_structure", return_value={"nodes": []}):
            answer = client.get("/api/papers/2101.00003").json()
        self.assertEqual(answer["paper"]["words"], 17)


if __name__ == "__main__":
    unittest.main()
