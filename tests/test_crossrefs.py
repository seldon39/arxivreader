"""Renvois d'un article à l'autre : rapprochement d'une référence avec un article lu ici."""

import unittest

from app import crossrefs

INDEX = [
    {"id": "1803.03149", "title": "Entanglement entropy and Berezin-Toeplitz operators", "arxiv": "1803.03149",
     "doi": "10.1007/s00220-019-03625-y", "words": "entanglement entropy and berezin toeplitz operators", "nodes": []},
    {"id": "2401.00001", "title": "Bergman kernels", "arxiv": "2401.00001", "doi": "", "words": "bergman kernels", "nodes": []},
    {"id": "2402.00002", "title": "Toeplitz operators on symplectic manifolds", "arxiv": "2402.00002", "doi": "",
     "words": "toeplitz operators on symplectic manifolds", "nodes": []},
]


class CrossRefs(unittest.TestCase):
    def test_match(self):
        by_doi = {"text": "L. Charles and B. Estienne, Comm. Math. Phys.", "arxiv": [], "doi": "10.1007/S00220-019-03625-Y"}
        self.assertEqual(crossrefs.match(by_doi, INDEX)["id"], "1803.03149")
        by_arxiv = {"text": "…", "arxiv": ["2401.00001v3"], "doi": ""}
        self.assertEqual(crossrefs.match(by_arxiv, INDEX)["id"], "2401.00001")
        by_title = {"text": "X. Ma and G. Marinescu, Toeplitz operators on symplectic manifolds, J. Geom. Anal. 18 (2008)", "arxiv": [], "doi": ""}
        self.assertEqual(crossrefs.match(by_title, INDEX)["id"], "2402.00002")
        short = {"text": "B. Berndtsson, Bergman kernels related to Hermitian line bundles", "arxiv": [], "doi": ""}
        self.assertIsNone(crossrefs.match(short, INDEX))  # titre trop court pour un rapprochement par le texte

    def test_words(self):
        self.assertEqual(crossrefs._words(r"Weighted $L^2$ Kähler–Einstein \emph{metrics}"), ["weighted", "l", "2", "kahler", "einstein", "metrics"])


if __name__ == "__main__":
    unittest.main()
