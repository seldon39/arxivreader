"""Corrections de la revue de la bêta : élagage du feed, clé d'un ancien identifiant, renvois par id/DOI/titre."""

import unittest

from app import crossrefs, db, feedprune, library
from tests.test_crossrefs import INDEX
from tests.test_hardening import Base


class BiblioFixes(Base):
    def test_prune_keeps_recently_reannounced_item(self):
        old = "2020-01-01 00:00:00"
        with db.connect() as connection:
            # stocké il y a longtemps (fetched_at), mais ré-annoncé hier (announced récent) : à garder
            connection.execute("INSERT INTO feed_items (key, arxiv_id, title, authors, categories, announced, in_arxiv, fetched_at) "
                               "VALUES ('2001.00001', '2001.00001', 't', '[]', '[]', '2999-01-01', 1, ?)", (old,))
            connection.execute("INSERT INTO feed_items (key, arxiv_id, title, authors, categories, announced, in_arxiv, fetched_at) "
                               "VALUES ('2001.00002', '2001.00002', 't', '[]', '[]', '2020-01-02', 1, ?)", (old,))
        feedprune.prune(days=60)
        with db.connect() as connection:
            items = {r["key"] for r in connection.execute("SELECT key FROM feed_items")}
        self.assertEqual(items, {"2001.00001"})

    def test_stored_key_finds_a_row_whose_arxiv_id_was_normalised(self):
        self.add("math.DG_0211159", "math/0211159")  # réimportée : arxiv_id normalisé, clé d'origine gardée
        self.assertEqual(library.stored_key("math/0211159"), "math.DG_0211159")

    def test_crossref_legacy_id_matches(self):
        index = [{**INDEX[1], "id": "math.DG_0211159", "arxiv": crossrefs._arxiv("math.DG/0211159v2")}]
        self.assertEqual(index[0]["arxiv"], "math/0211159")
        entry = {"text": "…", "arxiv": ["math/0211159"], "doi": ""}
        self.assertEqual(crossrefs.match_by(entry, index)[1], "id")

    def test_match_says_how(self):
        self.assertEqual(crossrefs.match_by({"text": "", "arxiv": [], "doi": "10.1007/S00220-019-03625-Y"}, INDEX)[1], "doi")
        title = {"text": "X. Ma, Toeplitz operators on symplectic manifolds, J. 2008", "arxiv": [], "doi": ""}
        self.assertEqual(crossrefs.match_by(title, INDEX)[1], "title")
        self.assertEqual(crossrefs.match_by({"text": "rien", "arxiv": [], "doi": ""}, INDEX), (None, None))


if __name__ == "__main__":
    unittest.main()
