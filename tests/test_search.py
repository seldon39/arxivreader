"""Recherche arXiv : construction de la requête (champs, ET / OU / SAUF, catégories, dates) et lecture des résultats."""

import unittest
from datetime import date

from app.search import SearchError, build_query, parse

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>137</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2609.24258v2</id>
    <published>2026-09-21T10:00:00Z</published>
    <title>Parameter Dependence of
      Weighted Bergman Kernels</title>
    <summary>We study $L^2$ estimates and K\\"ahler--Einstein metrics.</summary>
    <author><name>George Marinescu</name></author><author><name>Xu Xing</name></author>
    <arxiv:primary_category term="math.CV"/>
    <category term="math.CV"/><category term="math.DG"/>
  </entry>
</feed>"""


class Query(unittest.TestCase):
    def test_fields_and_operators(self):
        expression, ids = build_query([
            {"field": "all", "value": '"Bergman kernel"', "op": "and"},
            {"field": "author", "value": "Marinescu", "op": "and"},
            {"field": "title", "value": "survey notes", "op": "not"},
        ])
        self.assertEqual(expression, '((all:"Bergman kernel") AND au:Marinescu) ANDNOT (ti:survey AND ti:notes)')
        self.assertEqual(ids, [])

    def test_categories_dates_and_ids(self):
        expression, ids = build_query([{"field": "id", "value": "2609.24258v2, math.DG/0101001"}],
                                      ["math.DG", "math.CV", "bad cat"], {"mode": "year", "year": 2024})
        self.assertEqual(ids, ["2609.24258", "math.DG/0101001"])
        self.assertEqual(expression, "((cat:math.DG OR cat:math.CV)) AND submittedDate:[202401010000 TO 202412312359]")

    def test_syntax_characters_and_empty(self):
        expression, _ = build_query([{"field": "title", "value": "a:(b) [c]"}])
        self.assertEqual(expression, "(ti:a AND ti:b AND ti:c)")
        with self.assertRaises(SearchError):
            build_query([{"field": "all", "value": "  "}])
        with self.assertRaises(SearchError):
            build_query([{"field": "nope", "value": "x"}])
        with self.assertRaises(SearchError):
            build_query([{"field": "all", "value": "x"}], dates={"mode": "year", "year": date.today().year + 1})

    def test_parse(self):
        items, total = parse(ATOM)
        self.assertEqual(total, 137)
        item = items[0]
        self.assertEqual((item["arxiv_id"], item["version"], item["primary_category"]), ("2609.24258", 2, "math.CV"))
        self.assertEqual(item["title"], "Parameter Dependence of Weighted Bergman Kernels")
        self.assertEqual(item["abstract"], "We study $L^2$ estimates and Kähler–Einstein metrics.")
        self.assertEqual(item["authors"], ["George Marinescu", "Xu Xing"])
        self.assertEqual(item["announced"], "2026-09-21")


if __name__ == "__main__":
    unittest.main()


class ProfileSort(unittest.TestCase):
    """« Pour vous » : un lot de résultats d'arXiv reclassé selon le profil, puis découpé en pages sans nouvelle requête."""

    def setUp(self):
        import pathlib, tempfile
        from app import db, search
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        search._pools.clear()

    def tearDown(self):
        from app import db
        db.DB_PATH, db.LIBRARY = self.saved

    def test_rank_and_pages(self):
        from unittest import mock
        from app import search
        items = [{"key": f"2601.{n:05d}", "arxiv_id": f"2601.{n:05d}", "version": 1, "title": f"Paper {n}", "authors": [],
                  "abstract": "", "categories": ["math.DG"], "primary_category": "math.DG", "announced": "2026-01-01",
                  "link": ""} for n in range(60)]
        scored = lambda batch: [{**i, "relevance": int(i["arxiv_id"][-2:]) / 100} for i in batch]
        with mock.patch.object(search, "_fetch", return_value=(items, 506)) as fetch, \
                mock.patch.object(search.relevance, "score_items", side_effect=scored), \
                mock.patch.object(search.relevance, "fetch_embeddings"):
            first = search.search([{"field": "all", "value": "x"}], sort="profile", page=0)
            second = search.search([{"field": "all", "value": "x"}], sort="profile", page=1)
        self.assertEqual(fetch.call_count, 1)  # deuxième page : même lot, sans requête
        self.assertEqual((first["total"], first["arxivTotal"], len(first["items"])), (60, 506, 25))
        self.assertEqual(first["items"][0]["arxiv_id"], "2601.00059")
        self.assertEqual(second["items"][0]["arxiv_id"], "2601.00034")
