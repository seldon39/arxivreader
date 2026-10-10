"""Pertinence : mots, noms d'auteurs, calibration relative au lot du jour."""

import unittest

from app.profile import _initials_only
from app.relevance import _robust_z, author_key, cosine_sparse, tfidf_vectors, tokens


class Relevance(unittest.TestCase):
    def test_tokens(self):
        words = tokens("Toeplitz operators and the Bergman kernels of the $\\bar\\partial$ operator")
        self.assertIn("toeplitz operator", words)
        self.assertIn("bergman kernel", words)
        self.assertNotIn("the", words)

    def test_author_key(self):
        self.assertEqual(author_key("G. Marinescu"), author_key("George Marinescu"))
        self.assertEqual(author_key("Siegfried Hörmann"), ("hormann", "s"))
        self.assertNotEqual(author_key("Xiaonan Ma"), author_key("Y. Ma"))
        self.assertTrue(_initials_only("X. Ma") and _initials_only("J.-M. Bismut") is not None)
        self.assertFalse(_initials_only("Xiaonan Ma"))

    def test_tfidf_ranks_shared_vocabulary(self):
        profile, close, far = tfidf_vectors([
            "Asymptotics of Toeplitz operators and Bergman kernels on symplectic manifolds",
            "Bergman kernels and Toeplitz operators with singular symbols",
            "Ricci flow with bounded scalar curvature in dimension seven",
        ])
        self.assertGreater(cosine_sparse(profile, close), cosine_sparse(profile, far))

    def test_robust_z(self):
        z = _robust_z([0.80, 0.81, 0.82, 0.83, 0.84, 0.95])
        self.assertGreater(z[-1], 3)
        self.assertAlmostEqual(sorted(z)[len(z) // 2], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
