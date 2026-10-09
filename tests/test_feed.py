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

    def test_tex_text_keeps_math(self):
        self.assertEqual(tex_text(r"Poincar\'e--Lelong, $\"a--b$ and {\em see}"), r'Poincaré–Lelong, $\"a--b$ and see')


if __name__ == "__main__":
    unittest.main()
