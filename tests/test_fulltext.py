"""Recherche dans le texte et les notes de la bibliothèque (bibliothèque temporaire)."""

import json
import os
import pathlib
import tempfile
import unittest
import unittest.mock

from fastapi.testclient import TestClient

from app import annotations, db, fulltext, library, main

BASE = f"http://{main.HOST}:{main.PORT}"


def math(tex):
    return f'<math alttext="{tex}" class="ltx_Math" display="inline" id="m"><semantics><mi>noisemathml</mi><annotation>{tex}x</annotation></semantics></math>'


class FullText(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.tmp / "test.db", self.tmp, self.tmp / "papers"
        db.init()

    def tearDown(self):
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.saved

    def paper(self, paper_id, content=None, in_library=1, structure=None, pages=None):
        with db.connect() as connection:
            connection.execute("INSERT INTO papers (id, arxiv_id, version, title, in_library) VALUES (?, ?, 2, ?, ?)",
                               (paper_id, paper_id, f"Title of {paper_id}", in_library))
        folder = library.PAPERS / paper_id
        folder.mkdir(parents=True)
        if content is not None:
            (folder / "content.html").write_text(content, encoding="utf-8")
        if structure is not None:
            (folder / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
        if pages is not None:
            (folder / "text.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")
        return folder

    def found(self, query):
        return fulltext.search(query)["results"]

    def test_abstract_opens_the_top_of_the_paper(self):
        self.paper("a", '<div class="ltx_abstract"><p class="ltx_p">A needle here</p></div>'
                        '<section id="S1" class="ltx_section"><div id="S1.p1" class="ltx_para"><p>elsewhere</p></div></section>')
        self.assertEqual(self.found("needle")[0]["target"], fulltext.TOP)

    def test_pdf_stops_at_the_last_kept_page(self):
        self.paper("a", pages=[f"needle page {n}" for n in range(1, 6)])
        with unittest.mock.patch.object(fulltext, "_hit", wraps=fulltext._hit) as hit:
            found = self.found("needle")
        self.assertEqual(hit.call_count, fulltext.MAX_PER_PAPER)
        self.assertEqual([r["label"] for r in found], ["p. 1", "p. 2", "p. 3"])

    def test_pdf_pages_counted_across_occurrences(self):
        self.paper("a", pages=["needle needle", "other", "x", "needle"])
        self.assertEqual([r["label"] for r in self.found("needle")], ["p. 1", "p. 4"])

    def test_archived_results_are_marked(self):
        self.paper("a", "<p>x</p>")
        db.update_paper("a", archived_of=None)
        self.paper("av1", "<p>x</p>", in_library=0)
        db.update_paper("av1", archived_of="a", version=1)
        annotations.create("a", "general", note="Holomorphe ici")
        annotations.create("av1", "general", note="Holomorphe là")
        self.assertEqual({(r["paperId"], r["archived"], r["version"]) for r in self.found("holomorphe")}, {("a", False, 2), ("av1", True, 1)})

    def test_folding_keeps_length(self):
        text = "Kähler ß ﬁn İstanbul Łojasiewicz 한글 Kähler – ς"
        self.assertEqual(len(fulltext.fold(text)), len(text))
        self.assertEqual(fulltext.fold("KÄHLER Łoś Ø"), "kahler los o")

    def test_accents_and_case(self):
        self.paper("a", '<p class="ltx_p">On a K&auml;hler manifold</p>')
        self.paper("b", '<p class="ltx_p">On a KAHLER variety</p>')
        self.assertEqual({r["paperId"] for r in self.found("kahler")}, {"a", "b"})
        self.assertEqual({r["paperId"] for r in self.found("KÄHLER")}, {"a", "b"})
        self.assertIn("Kähler", next(r for r in self.found("kahler") if r["paperId"] == "a")["snippet"])

    def test_dashes(self):
        self.paper("a", "<p>the Hodge–Riemann relations</p>")
        self.assertEqual(len(self.found("hodge-riemann")), 1)

    def test_whitespace_and_tags(self):
        self.paper("a", "<p>a K&auml;hler\n   <span>mani</span><span>fold</span> and</p><p>another one</p>")
        self.assertEqual(len(self.found("kahler  manifold")), 1)
        self.assertEqual(len(self.found("and another")), 1)  # deux blocs : les mots ne se collent pas
        self.assertEqual(self.found("andanother"), [])

    def test_formulas_by_tex(self):
        self.paper("a", f"<p>Let {math(chr(92) + 'omega_{1}')} and {math('a&lt;b')} be</p>"
                        "<p><math alttext='K\\\"a' class='m'><mi>z</mi></math></p>")
        self.assertEqual(len(self.found("\\omega_{1}")), 1)
        self.assertEqual(len(self.found("$a<b$ be")), 1)
        self.assertEqual(len(self.found('K\\"a')), 1)
        self.assertEqual(self.found("noisemathml"), [])
        self.assertEqual(self.found("semantics"), [])

    def test_math_without_tex_and_svg_ignored(self):
        self.paper("a", "<p>x<math><mi>noisemathml</mi></math></p><svg><text>noisefigure</text></svg>")
        self.assertEqual(self.found("noisemathml"), [])
        self.assertEqual(self.found("noisefigure"), [])

    def test_text_anchor(self):
        self.paper("a", '<section class="ltx_section" id="S1"><h2 class="ltx_title">Intro needle</h2>'
                        '<div class="ltx_para" id="S1.p1"><p class="ltx_p" id="S1.p1.1">first</p></div>'
                        '<div class="ltx_para" id="S1.p2"><p class="ltx_p" id="S1.p2.1">the haystack here</p></div></section>',
                   structure={"toc": [{"id": "S1", "title": "1. Intro"}], "nodes": []})
        hit = self.found("haystack")[0]
        self.assertEqual((hit["kind"], hit["target"], hit["label"], hit["version"]), ("text", "S1.p2", "1. Intro", 2))
        self.assertEqual(self.found("needle")[0]["target"], "S1")

    def test_statement_anchor(self):
        self.paper("a", '<div class="ltx_para" id="S1.p1"><p>before</p></div>'
                        '<div class="ltx_theorem ltx_theorem_thm" id="S1.Thm1"><h6>Theorem 1.1</h6>'
                        '<div class="ltx_para" id="S1.Thm1.p1"><div><p>inside words</p></div></div></div>'
                        '<div class="ltx_para" id="S1.p2"><p>outside words</p></div>',
                   structure={"toc": [], "nodes": [{"id": "S1.Thm1", "label": "Theorem 1.1"}]})
        hit = self.found("inside")[0]
        self.assertEqual((hit["kind"], hit["target"], hit["label"]), ("statement", "S1.Thm1", "Theorem 1.1"))
        hit = self.found("outside")[0]
        self.assertEqual((hit["kind"], hit["target"]), ("text", "S1.p2"))  # l'énoncé est refermé

    def test_annotations(self):
        self.paper("a", structure={"toc": [], "nodes": [{"id": "S1.Thm1", "label": "Theorem 1.1"}]})
        annotations.create("a", "block", {"id": "S1.Thm1"}, note="Idée de K\\\"ahler : needle")
        annotations.create("a", "highlight", {"start": {"id": "S1.Thm1.p1", "offset": 0}, "end": {"id": "S1.Thm1.p1", "offset": 3}},
                           "yellow", "highlighted needle")
        annotations.create("a", "general", note="general needle")
        annotations.create("a", "highlight", {"pdf": {"page": 1, "rect": [0, 0, .5, .5]}}, "yellow", "needle in the pdf")
        hits = self.found("needle")
        self.assertEqual([r["kind"] for r in hits], ["note", "note", "highlight", "highlight"])
        self.assertEqual({(r["target"], r["label"]) for r in hits},
                         {("S1.Thm1", "Theorem 1.1"), ("S1.Thm1.p1", "Theorem 1.1"), (None, "")})
        self.assertIsNone(next(r for r in hits if "pdf" in r["snippet"])["target"])

    def test_note_and_quote_give_one_line(self):
        self.paper("a")
        annotations.create("a", "highlight", {"start": {"id": "p", "offset": 0}, "end": {"id": "p", "offset": 3}}, "yellow",
                           "needle quoted", "needle noted")
        hits = self.found("needle")
        self.assertEqual([(r["kind"], r["snippet"]) for r in hits], [("note", "needle noted")])

    def test_proof_location_not_searched(self):
        self.paper("a")
        annotations.set_proof("a", "needle", "passage", start="needle")
        self.assertEqual(self.found("needle"), [])

    def test_snippet_balance(self):
        formula = "x_{1}+" * 30
        words = " ".join(f"word{i}" for i in range(40))
        self.paper("a", f"<p>{words} {math(formula)} {words} {math(formula)} needle {math(formula)} {words} {math(formula)}</p>")
        snippet = self.found("needle")[0]["snippet"]
        self.assertEqual(snippet.count("$") % 2, 0)
        self.assertIn("needle", snippet)
        self.assertLess(len(snippet), 300)

    def test_snippet_keeps_formula_with_match(self):
        formula = "a+" * 200 + "needle"
        self.paper("a", f"<p>{'word ' * 60}{math(formula + '+b')}{' word' * 60}</p>")
        snippet = self.found("needle")[0]["snippet"]
        self.assertEqual(snippet.count("$"), 2)
        self.assertIn(formula, snippet)

    def test_dollars_in_text_are_not_formulas(self):
        self.paper("a", f"<p>costs $5 and {math('x')} needle $6 {math('text{if $y$}')} end</p>")
        snippet = self.found("needle")[0]["snippet"]
        self.assertEqual(snippet.count("$"), 4)
        self.assertEqual(len(self.found("costs $5")), 1)

    def test_note_delimiters(self):
        self.paper("a")
        annotations.create("a", "general", note="price $5 and needle")
        self.assertEqual(self.found("needle")[0]["snippet"].count("$"), 0)
        annotations.create("a", "block", {"id": "x"}, note=r"see \(a\) and $$b$$ needly")
        self.assertEqual(self.found("needly")[0]["snippet"], "see $a$ and $b$ needly")
        annotations.create("a", "block", {"id": "y"}, note="un **point** clé")
        self.assertEqual(self.found("un point cle")[0]["snippet"], "un point clé")

    def test_three_text_hits_per_paper(self):
        self.paper("a", "".join(f'<div class="ltx_para" id="p{i}"><p>needle</p></div>' for i in range(6)))
        self.assertEqual([r["target"] for r in self.found("needle")], ["p0", "p1", "p2"])

    def test_one_hit_per_paragraph(self):
        self.paper("a", '<div class="ltx_para" id="p0"><p>needle needle needle</p></div><div class="ltx_para" id="p1"><p>needle</p></div>')
        self.assertEqual([r["target"] for r in self.found("needle")], ["p0", "p1"])

    def test_overall_limit_and_notes_first(self):
        for i in range(fulltext.MAX_RESULTS + 5):
            self.paper(f"t{i}", "<p>needle</p>")
        self.paper("n")
        annotations.create("n", "general", note="needle")
        result = fulltext.search("needle")
        self.assertEqual(len(result["results"]), fulltext.MAX_RESULTS)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["results"][0]["kind"], "note")

    def test_not_truncated_at_the_limit(self):
        for i in range(fulltext.MAX_RESULTS):
            self.paper(f"t{i}", "<p>needle</p>")
        result = fulltext.search("needle")
        self.assertEqual((len(result["results"]), result["truncated"]), (fulltext.MAX_RESULTS, False))

    def test_minimum_length(self):
        self.paper("a", "<p>ab abc</p>")
        self.assertEqual(self.found("ab"), [])
        self.assertEqual(self.found(" a  "), [])
        self.assertEqual(len(self.found("abc")), 1)

    def test_library_scope(self):
        self.paper("in", "<p>needle</p>")
        self.paper("out", "<p>needle</p>", in_library=0)
        annotations.create("out", "general", note="needle")
        self.assertEqual({r["paperId"] for r in self.found("needle")}, {"in"})

    def test_pdf_documents(self):
        self.paper("a", pages=["nothing", "a ﬁnite needle", "no"])
        hit = self.found("finite needle")[0]
        self.assertEqual((hit["kind"], hit["target"], hit["label"]), ("text", None, "p. 2"))

    def test_scan_and_tex_source_give_nothing(self):
        folder = self.paper("a", pages=["", ""])
        (folder / "source.tex").write_text("needle", encoding="utf-8")
        self.assertEqual(self.found("needle"), [])

    def test_changed_file_is_read_again(self):
        folder = self.paper("a", "<p>needle</p>")
        self.assertEqual(len(self.found("needle")), 1)
        (folder / "content.html").write_text("<p>other</p>", encoding="utf-8")
        os.utime(folder / "content.html", (1, 1))
        self.assertEqual(self.found("needle"), [])

    def test_route(self):
        self.paper("a", "<p>K&auml;hler</p>")
        client = TestClient(main.app, base_url=BASE)
        body = client.get("/api/library/search", params={"q": "kahler"}).json()
        self.assertEqual(body["truncated"], False)
        self.assertEqual(set(body["results"][0]), {"paperId", "title", "kind", "label", "snippet", "target", "version", "archived"})
        self.assertEqual(client.get("/api/library/search").json(), {"results": [], "truncated": False})


if __name__ == "__main__":
    unittest.main()
