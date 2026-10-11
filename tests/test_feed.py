"""Feed : lecture du flux RSS d'arXiv, nettoyage du TeX hors formules."""

import unittest

from app.feed import parse_rss, tex_text

RSS = """<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
<channel><title>t</title>
<item>
  <title>$L^2$ bounds on K\\"ahler--Ricci flows</title>
  <link>https://arxiv.org/abs/2610.10575</link>
  <description>arXiv:2610.10575v2 Announce Type: replace
Abstract: We prove $p&gt; n/2$ estimates.</description>
  <category>math.DG</category><category>math.AP</category>
  <pubDate>Fri, 09 Oct 2026 00:00:00 -0400</pubDate>
  <arxiv:announce_type>replace</arxiv:announce_type>
  <dc:creator>Ann Author, Bob Writer</dc:creator>
</item>
<item><title>No id</title><description>Something else</description></item>
</channel></rss>"""


class Feed(unittest.TestCase):
    def test_parse_rss(self):
        (item,) = parse_rss(RSS)
        self.assertEqual((item["arxiv_id"], item["version"], item["announce_type"]), ("2610.10575", 2, "replace"))
        self.assertEqual(item["title"], "$L^2$ bounds on Kähler–Ricci flows")
        self.assertEqual(item["abstract"], "We prove $p> n/2$ estimates.")
        self.assertEqual(item["authors"], ["Ann Author", "Bob Writer"])
        self.assertEqual((item["categories"], item["primary_category"]), (["math.DG", "math.AP"], "math.DG"))
        self.assertEqual(item["announced"], "2026-10-09")

    def test_tex_text_keeps_environments(self):
        text = r"then \begin{equation*} \frac{n}{4} \mathrm{Vol} \end{equation*} and \[ a^{2} \], \(b_{1}\), Bohr--Sommerfeld"
        self.assertEqual(tex_text(text), r"then \begin{equation*} \frac{n}{4} \mathrm{Vol} \end{equation*} and \[ a^{2} \], \(b_{1}\), Bohr–Sommerfeld")

    def test_tex_text_keeps_math(self):
        self.assertEqual(tex_text(r"Poincar\'e--Lelong, $\"a--b$ and {\em see}"), r'Poincaré–Lelong, $\"a--b$ and see')


if __name__ == "__main__":
    unittest.main()


class OaiDay(unittest.TestCase):
    def test_classification(self):
        from app.feed import parse_oai_day
        xml = """<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>
        <record><metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>2610.00001</id>
          <version version="v1"><date>Wed, 07 Oct 2026 10:00:00 GMT</date></version>
          <title>New in DG</title><authors>A. One, B. Two and C. Three</authors><categories>math.DG math.AP</categories>
          <abstract>x</abstract></arXivRaw></metadata></record>
        <record><metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>2610.00002</id>
          <version version="v1"><date>Wed, 07 Oct 2026 10:00:00 GMT</date></version>
          <title>Cross</title><authors>D. Four</authors><categories>math.AP math.SP</categories><abstract>x</abstract></arXivRaw></metadata></record>
        <record><metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>2401.00003</id>
          <version version="v1"><date>Mon, 01 Jan 2024 10:00:00 GMT</date></version>
          <version version="v2"><date>Wed, 07 Oct 2026 10:00:00 GMT</date></version>
          <title>Replaced</title><authors>E. Five</authors><categories>math.CV</categories><abstract>x</abstract></arXivRaw></metadata></record>
        <record><metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>2401.00004</id>
          <version version="v1"><date>Mon, 01 Jan 2024 10:00:00 GMT</date></version>
          <version version="v2"><date>Mon, 01 Apr 2024 10:00:00 GMT</date></version>
          <title>Metadata only</title><authors>F. Six</authors><categories>math.DG</categories><abstract>x</abstract></arXivRaw></metadata></record>
        <record><metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>2610.00005</id>
          <version version="v1"><date>Wed, 07 Oct 2026 10:00:00 GMT</date></version>
          <title>Elsewhere</title><authors>G. Seven</authors><categories>math.NT</categories><abstract>x</abstract></arXivRaw></metadata></record>
        </ListRecords></OAI-PMH>"""
        items, token = parse_oai_day(xml, "2026-10-08", ["math.DG", "math.CV", "math.SP"])
        self.assertEqual({i["arxiv_id"]: i["announce_type"] for i in items},
                         {"2610.00001": "new", "2610.00002": "cross", "2401.00003": "replace"})
        self.assertEqual(items[0]["authors"], ["A. One", "B. Two", "C. Three"])
        self.assertIsNone(token)


class SubmissionWindow(unittest.TestCase):
    """Jour d'annonce → fenêtre de dépôt (14 h à New York, jours ouvrés)."""

    def test_weekdays(self):
        from app.feed import submission_window
        start, end = submission_window("2026-10-07")  # mercredi : du lundi au mardi (heure d'été, UTC-4)
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-10-05T18:00:00+00:00", "2026-10-06T18:00:00+00:00"))
        start, end = submission_window("2026-10-06")  # mardi : du vendredi au lundi
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-10-02T18:00:00+00:00", "2026-10-05T18:00:00+00:00"))
        start, end = submission_window("2026-12-07")  # lundi, heure d'hiver (UTC-5) : du jeudi au vendredi
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-12-03T19:00:00+00:00", "2026-12-04T19:00:00+00:00"))


class PastDayFallback(unittest.TestCase):
    """Jour ancien : l'API de recherche remplace l'OAI-PMH, sans les nouvelles versions."""

    def test_old_day_uses_search_api(self):
        from unittest import mock
        from app import feed, search
        page = [{"key": "2610.00001", "arxiv_id": "2610.00001", "version": 1, "title": "A", "authors": ["X"], "abstract": "",
                 "categories": ["math.DG"], "primary_category": "math.DG", "announced": "2026-09-30", "link": ""},
                {"key": "2610.00002", "arxiv_id": "2610.00002", "version": 2, "title": "B", "authors": ["Y"], "abstract": "",
                 "categories": ["math.AP", "math.SP"], "primary_category": "math.AP", "announced": "2026-09-30", "link": ""}]
        stored = []
        with mock.patch.object(feed, "_fetch_day_oai", side_effect=AssertionError("OAI")), \
             mock.patch.object(search, "_fetch", return_value=(page, 2)) as fetch, \
             mock.patch.object(feed, "_store", side_effect=lambda c, item: stored.append(item)), \
             mock.patch.object(feed.relevance, "fetch_embeddings"), \
             mock.patch.object(feed.db, "connect"), mock.patch.object(feed.db, "get_settings", return_value={}), \
             mock.patch.object(feed.db, "save_settings") as save:
            self.assertEqual(feed.fetch_day("2020-10-01", ["math.DG", "math.SP"]), 2)
        self.assertIn("submittedDate:[202009291800 TO 202009301800]", fetch.call_args[0][0])
        self.assertEqual([(i["announce_type"], i["announced"]) for i in stored], [("new", "2020-10-01"), ("cross", "2020-10-01")])
        self.assertEqual(save.call_args[0][0]["feed.partialDays"], ["2020-10-01"])

    def test_slow_oai_falls_back(self):
        from datetime import date
        from unittest import mock
        import httpx
        from app import feed, search
        day = date.today().isoformat()
        with mock.patch.object(feed, "_oai_failed", 0.0), \
             mock.patch.object(feed, "_fetch_day_oai", side_effect=httpx.ReadTimeout("slow")) as oai, \
             mock.patch.object(search, "_fetch", return_value=([], 0)) as fetch, \
             mock.patch.object(feed.relevance, "fetch_embeddings"), mock.patch.object(feed.db, "connect"), \
             mock.patch.object(feed.db, "get_settings", return_value={}), mock.patch.object(feed.db, "save_settings"):
            feed.fetch_day(day, ["math.DG"])
            self.assertTrue(oai.called and fetch.called)
            self.assertGreater(feed._oai_failed, 0)
            oai.reset_mock()
            feed.fetch_day(day, ["math.DG"])  # échec récent : l'OAI-PMH n'est pas réessayé
            self.assertFalse(oai.called)
