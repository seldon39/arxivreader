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


class LibraryRelevance(unittest.TestCase):
    """Tri des dossiers : une pertinence par article de la bibliothèque, calculée sans réseau si besoin."""

    def test_scores_by_paper_id(self):
        from unittest import mock
        from app import relevance
        papers = [{"id": "2601.00001", "arxiv_id": "2601.00001", "title": "Bergman kernels", "abstract": "", "authors": ["A"]},
                  {"id": "2601.00002", "arxiv_id": "2601.00002", "title": "Fluid dynamics", "abstract": None, "authors": []}]
        profile = ([{"label": "Bergman", "text": "Bergman kernels and Toeplitz operators", "s2": None, "weight": 1.0}], [], {}, [])
        with mock.patch.object(relevance.db, "list_papers", return_value=papers), \
             mock.patch.object(relevance, "fetch_embeddings", side_effect=OSError("offline")), \
             mock.patch.object(relevance, "profile_documents", return_value=profile), \
             mock.patch.object(relevance, "cached_embeddings", return_value={}):
            scores = relevance.library_relevance()
        self.assertEqual(set(scores), {"2601.00001", "2601.00002"})
        self.assertGreater(scores["2601.00001"], scores["2601.00002"])


class FollowedAuthors(unittest.TestCase):
    """Auteurs suivis : un homonyme d'un autre domaine (« Lei Wang » en physique) n'apparaît pas."""

    def test_homonym_from_another_field(self):
        import time
        from unittest import mock
        from app import profile
        today = time.strftime("%Y-%m-%d")
        entry = lambda arxiv, name, categories: (  # noqa: E731
            f"<entry><id>http://arxiv.org/abs/{arxiv}v1</id><published>{today}T00:00:00Z</published>"
            f"<title>T {arxiv}</title><summary>S</summary><author><name>{name}</name></author>"
            + "".join(f'<category term="{c}"/>' for c in categories) + "</entry>")
        atom = ('<feed xmlns="http://www.w3.org/2005/Atom">' + entry("2610.00001", "Lei Wang", ["hep-th"])
                + entry("2610.00002", "Lei Wang", ["math.AP"]) + entry("2610.00003", "Lei Wang", ["math-ph", "hep-th"])
                + entry("2610.00004", "Lin Wang", ["math.DG"]) + "</feed>")
        client = mock.MagicMock()
        client.__enter__.return_value.get.return_value = mock.Mock(status_code=200, text=atom)
        with mock.patch.object(profile, "list_items", return_value=[{"kind": "author", "label": "Lei Wang"}]), \
             mock.patch.object(profile.httpx, "Client", return_value=client):
            papers = profile.followed_author_papers(["math.DG", "math.CV"])
        self.assertEqual(sorted(p["arxiv_id"] for p in papers), ["2610.00002", "2610.00003"])
