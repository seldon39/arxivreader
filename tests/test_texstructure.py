"""Structure lue dans la source TeX (articles sans version HTML)."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
