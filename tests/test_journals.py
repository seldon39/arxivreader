"""Revue où un article est paru : arXiv (Journal-ref, DOI) puis Semantic Scholar (simulé, pas de réseau)."""

import pathlib
import tempfile
import unittest
from unittest import mock

from app import db, journals


class Journals(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def test_semantic_scholar_answer(self):
        self.assertEqual(journals._from_s2({"journal": {"name": "J. Reine Angew. Math.", "volume": "662", "pages": " 1 - 56"},
                                            "externalIds": {"DOI": "10.1515/crelle.2011.133"}}),
                         ("J. Reine Angew. Math. 662, 1 - 56", "10.1515/crelle.2011.133"))
        # prépublication : « ArXiv » n'est pas une revue, et le DOI attribué par arXiv non plus
        self.assertEqual(journals._from_s2({"journal": {"name": "ArXiv", "volume": "abs/2601.00001"},
                                            "externalIds": {"DOI": "10.48550/arXiv.2601.00001"}}), (None, None))
        self.assertEqual(journals._from_s2(None), (None, None))
        self.assertEqual(journals._from_s2({"journal": {"name": "arXiv: Symplectic Geometry"}}), (None, None))
        self.assertEqual(journals._from_s2({"journal": "odd", "externalIds": []}), (None, None))

    def test_published_in(self):
        self.assertEqual(journals.published_in("Ann. Math. 1 (2020)", None, ("Other", None)),
                         {"text": "Ann. Math. 1 (2020)", "source": "arxiv"})  # les auteurs d'abord
        self.assertEqual(journals.published_in(None, None, ("Duke Math. J. 170", None)), {"text": "Duke Math. J. 170", "source": "s2"})
        self.assertEqual(journals.published_in(None, "10.1007/xyz"), {"text": "DOI 10.1007/xyz", "source": "arxiv"})
        self.assertIsNone(journals.published_in(None, "10.48550/arXiv.2601.00001"))
        self.assertIsNone(journals.published_in("  ", None, (None, None)))

    def test_fetch_once_and_annotate(self):
        answers = [{"journal": {"name": "Math. Ann.", "volume": "380"}, "externalIds": {}}, None]
        response = mock.Mock(status_code=200, json=lambda: answers)
        with mock.patch.object(journals, "with_retry", return_value=response) as sent:
            journals.fetch(["ArXiv:2601.00001", "ArXiv:2601.00002"])
            journals.fetch(["ArXiv:2601.00001", "ArXiv:2601.00002"])  # déjà connus : pas de nouvelle requête
        self.assertEqual(sent.call_count, 1)
        items = journals.annotate([{"arxiv_id": "2601.00001"}, {"arxiv_id": "2601.00002"}, {"arxiv_id": "2601.00003", "journal_ref": "X"}])
        self.assertEqual([i["publishedIn"] for i in items],
                         [{"text": "Math. Ann. 380", "source": "s2"}, None, {"text": "X", "source": "arxiv"}])

    def test_refused_or_odd_reply(self):
        for response in (mock.Mock(status_code=429), mock.Mock(status_code=200, json=lambda: {"error": "x"})):
            with mock.patch.object(journals, "with_retry", return_value=response):
                journals.fetch(["ArXiv:2601.00009"])  # rien d'enregistré, pas d'erreur
        self.assertEqual(journals.known(["ArXiv:2601.00009"]), {})

    def test_feed_parsers_keep_the_journal(self):
        from app import feed
        rss = """<rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
          <title>T</title><description>arXiv:2601.00001v2 Announce Type: replace Abstract: A</description>
          <pubDate>Mon, 05 Oct 2026 00:00:00 -0400</pubDate><category>math.DG</category><dc:creator>X</dc:creator>
          <arxiv:announce_type>replace</arxiv:announce_type><arxiv:journal_reference>Math. Ann. 380 (2026)</arxiv:journal_reference>
          <arxiv:DOI>10.1007/abc</arxiv:DOI></item></channel></rss>"""
        item = feed.parse_rss(rss)[0]
        self.assertEqual((item["journal_ref"], item["doi"]), ("Math. Ann. 380 (2026)", "10.1007/abc"))
        with db.connect() as connection:
            feed._store(connection, item)
            feed._store(connection, {**item, "journal_ref": None, "doi": None})  # source sans revue : gardée
            row = connection.execute("SELECT journal_ref, doi FROM feed_items WHERE key = '2601.00001'").fetchone()
        self.assertEqual((row["journal_ref"], row["doi"]), ("Math. Ann. 380 (2026)", "10.1007/abc"))


if __name__ == "__main__":
    unittest.main()
