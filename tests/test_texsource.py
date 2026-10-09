"""Lecture de la source TeX (en mémoire) : classe, numéros d'équation, police, étiquettes de listes."""

import gzip
import io
import tarfile
import unittest

from app.texsource import _label_format, analyze_source

MAIN = r"""
\documentclass[11pt,reqno]{amsart}
\usepackage{charter}
% \usepackage{times}
\begin{document}
\input{sections/intro}
\begin{enumerate}[(i)]\item a\end{enumerate}
\begin{enumerate}[label=\textup{(\alph*)}]\item b\end{enumerate}
\end{document}
"""
INTRO = r"\begin{enumerate}\item x\end{enumerate} % \begin{enumerate}[(A)]"


def tar_gz(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class TexSource(unittest.TestCase):
    def test_archive(self):
        result = analyze_source(tar_gz({"paper.tex": MAIN, "sections/intro.tex": INTRO, "../evil.tex": "x"}))
        self.assertEqual(result["documentclass"], "amsart")
        self.assertEqual(result["eqno"], "right")
        self.assertEqual(result["font"], "charter")
        # liste de \input en premier, commentaires ignorés
        self.assertEqual(result["enumerate"], [None, ["(", "i", ")"], ["(", "a", ")"]])

    def test_single_file_and_defaults(self):
        result = analyze_source(gzip.compress(rb"\documentclass{amsart}\begin{document}x\end{document}"))
        self.assertEqual((result["eqno"], result["font"]), ("left", "lm"))
        result = analyze_source(gzip.compress(rb"\documentclass{article}\usepackage{mathpazo}\begin{document}\end{document}"))
        self.assertEqual((result["eqno"], result["font"]), ("right", "palatino"))

    def test_not_tex(self):
        self.assertIsNone(analyze_source(gzip.compress(b"%PDF-1.5 ...")))

    def test_label_formats(self):
        self.assertEqual(_label_format("(i)"), ["(", "i", ")"])
        self.assertEqual(_label_format("a)"), ["", "a", ")"])
        self.assertEqual(_label_format(r"label=\arabic*."), ["", "1", "."])
        self.assertIsNone(_label_format("leftmargin=*"))
        self.assertIsNone(_label_format(None))


if __name__ == "__main__":
    unittest.main()
