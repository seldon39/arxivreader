"""Mise en page du lecteur : bandeau du texte caché collé sous la barre, tableaux et boîtes de LaTeXML (latexml.css).

Le rendu lui-même se vérifie dans WebKit et Chrome (MAINTENANCE, section 9) ; ces tests gardent les règles qui le font."""

import json
import pathlib
import re
import shutil
import subprocess
import unittest

from tests import jsfunction

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_CSS = (ROOT / "static/css/app.css").read_text(encoding="utf-8")
LATEXML_CSS = (ROOT / "static/css/latexml.css").read_text(encoding="utf-8")


def declarations(css, selector):
    """Déclarations (propriété → valeur) des règles dont la liste de sélecteurs contient exactement `selector`."""
    found = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        if selector in [s.strip() for s in re.split(r",(?![^()]*\))", selectors)]:  # virgules hors de :has(…), :where(…)
            for item in body.split(";"):
                if ":" in item:
                    name, value = item.split(":", 1)
                    found[name.strip()] = value.strip()
    return found


@unittest.skipUnless(shutil.which("node"), "Node absent")
class HiddenTextNext(unittest.TestCase):
    """« Suivant » : le passage s'arrête sous le bandeau resté en haut, pas derrière lui."""

    def scroll(self, call):
        code = """
let target = null;
const scrollTo = (options) => { target = options.top; };
const scrollY = 1000, innerHeight = 800;
class Reader {
%s
  viewTop() { return 100; }
}
const element = { getBoundingClientRect: () => ({ top: 600 }) };
new Reader().%s;
console.log(JSON.stringify(target));
""" % (jsfunction.method_source("reader.js", "scrollToElement"), call)
        return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)

    def test_margin_keeps_the_passage_below_the_banner(self):
        self.assertEqual(self.scroll("scrollToElement(element)"), 1000 + 600 - 100 - 12)
        self.assertEqual(self.scroll("scrollToElement(element, true, 60)"), 1000 + 600 - 100 - 12 - 60)

    def test_banner_passes_its_height(self):
        reader = (ROOT / "static/js/reader.js").read_text(encoding="utf-8")
        self.assertIn("margin: notice.offsetHeight", reader)


class HiddenTextBanner(unittest.TestCase):
    def test_banner_sticks_under_the_toolbar_while_shown(self):
        rule = declarations(APP_CSS, ".paper.reveal-hidden > .hidden-notice")
        self.assertEqual(rule.get("position"), "sticky")
        self.assertIn("--toolbar-h", rule.get("top", ""))


class LatexmlBoxes(unittest.TestCase):
    def test_span_tabular_is_a_table(self):
        """Tableau fait de <span> (bloc d'auteurs, \\resizebox) : sinon son texte coule et se coupe lettre par lettre."""
        for selector, display in (("span.ltx_tabular", "inline-table"), ("span.ltx_tr", "table-row"),
                                  ("span.ltx_td", "table-cell"), ("span.ltx_tbody", "table-row-group")):
            self.assertEqual(declarations(LATEXML_CSS, selector).get("display"), display, selector)

    def test_transformed_box_takes_its_natural_size(self):
        """\\resizebox : la boîte extérieure garde la taille transformée, que la transformation n'atteint pas."""
        rule = declarations(LATEXML_CSS, ".ltx_transformed_outer")
        self.assertEqual(rule.get("width"), "auto !important")
        self.assertEqual(rule.get("height"), "auto !important")

    def test_sidewaystable_is_read_upright(self):
        rule = declarations(LATEXML_CSS, ".ltx_table.ltx_transformed_outer > .ltx_transformed_inner")
        self.assertEqual(rule.get("transform"), "none !important")

    def test_zero_width_author_boxes_take_their_size(self):
        self.assertEqual(declarations(LATEXML_CSS, '.ltx_inline-block[style*="width:0.0pt"]').get("width"), "auto !important")
        self.assertEqual(declarations(LATEXML_CSS, "span.ltx_td").get("white-space"), "nowrap")
        self.assertEqual(declarations(LATEXML_CSS, '.ltx_tabular[style*="width"] .ltx_td').get("white-space"), "normal")

    def test_no_automatic_hyphenation_in_cells(self):
        self.assertEqual(declarations(LATEXML_CSS, ".ltx_td").get("hyphens"), "manual")


class SpanBlocks(unittest.TestCase):
    """Énoncé encadré (\\fbox, framed : théorèmes 3.1, 3.4, 4.1 de 2506.03074v1) : LaTeXML l'écrit tout en <span>."""

    def test_frame_is_a_box_not_a_border_on_each_line(self):
        rule = declarations(LATEXML_CSS, "span.ltx_framed:has(.ltx_theorem, .ltx_proof, .ltx_para, .ltx_eqn_table)")
        self.assertEqual(rule.get("display"), "block")
        self.assertNotIn("display", declarations(LATEXML_CSS, "span.ltx_framed"))  # \fbox{mot} : reste en ligne

    def test_statement_and_paragraphs_inside_are_blocks(self):
        """Spécificité nulle : le titre d'énoncé en tête de ligne (.ltx_runin + .ltx_para) reste en ligne."""
        rule = declarations(LATEXML_CSS, ":where(.ltx_inline-logical-block, span.ltx_framed) "
                                         ":where(span.ltx_theorem, span.ltx_proof, span.ltx_para, span.ltx_p)")
        self.assertEqual(rule.get("display"), "block")
        self.assertEqual(declarations(LATEXML_CSS, ".ltx_runin + .ltx_para").get("display"), "inline")

    def test_span_equation_is_a_table(self):
        for selector, display in (("span.ltx_eqn_table", "table"), ("span.ltx_eqn_table > span:not([class])", "table-row-group"),
                                  ("span.ltx_eqn_row", "table-row"), ("span.ltx_eqn_cell", "table-cell")):
            self.assertEqual(declarations(LATEXML_CSS, selector).get("display"), display, selector)



@unittest.skipUnless(shutil.which("node"), "Node absent")
class AlgorithmNumbers(unittest.TestCase):
    """algorithm2e : LaTeXML écrit le numéro d'une ligne au début de la suivante. Données réelles (numéros du HTML
    d'arXiv, ligne par ligne ; « c » commentaire /* */, « e » ligne vide) et numéros attendus, lus dans le PDF."""

    def numbers(self, html):
        lines = [{"tags": [] if tag in "-ce" else [int(n) for n in tag.split("+")], "numbered": tag not in "ce"}
                 for tag in html.split()]
        return jsfunction.run("amsfix.js", "algorithmLineNumbers", f"algorithmLineNumbers({json.dumps(lines)})")

    def test_shifted_by_one_line(self):
        # 2506.03074v1, algorithme 2 (ECaD) : PDF 1 Compute … 6 Return
        self.assertEqual(self.numbers("- 1 2 3 4 5 e"), [1, 2, 3, 4, 5, 6, None])
        # algorithme 1 : Input (1), commentaire, for (2) … ; Return (10)
        self.assertEqual(self.numbers("- c 1 2 3 c 4 5 6 7 8 9 e"),
                         [1, None, 2, 3, 4, None, 5, 6, 7, 8, 9, 10, None])

    def test_two_numbers_on_one_line(self):
        # 2506.03074v5, algorithme 1 : « 7 8 » sur la ligne Compute ; PDF : Compute 8, puis 9, 10, 11 (Return)
        self.assertEqual(self.numbers("- c 1 2 3 4 c 5 6 7+8 9 10 11 e"),
                         [1, None, 2, 3, 4, 5, None, 6, 7, 8, 9, 10, 11, None])

    def test_left_alone_when_right_or_unclear(self):
        self.assertIsNone(self.numbers("1 2 3 e"))           # déjà juste
        self.assertIsNone(self.numbers("- - - 1 2 e"))       # trois lignes sans numéro pour deux : on ne devine pas
        self.assertIsNone(self.numbers("- 1 3 e"))           # numéros qui sautent
        self.assertIsNone(self.numbers("- - e"))             # aucun numéro

    def test_frames_of_a_ruled_algorithm(self):
        self.assertEqual(declarations(LATEXML_CSS, ".ltx_framed_top").get("border-width"), "1px 0 0")
        self.assertEqual(declarations(LATEXML_CSS, ".ltx_framed_topbottom").get("border-width"), "1px 0")

@unittest.skipUnless(shutil.which("node"), "Node absent")
class GraphDeselect(unittest.TestCase):
    """Graphe des dépendances : un clic à côté des énoncés revient au graphe sans énoncé choisi."""

    def deselects(self, on_node, selected):
        target = "{ closest: (s) => (%s && s === '.node' ? {} : null) }" % ("true" if on_node else "false")
        return jsfunction.run("graph.js", "deselects", f"deselects({target}, {json.dumps(selected)})")

    def test_click_beside_a_statement_clears_the_choice(self):
        self.assertTrue(self.deselects(False, "thm1"))

    def test_click_on_a_statement_or_with_nothing_chosen_does_not(self):
        self.assertFalse(self.deselects(True, "thm1"))
        self.assertFalse(self.deselects(False, None))

    def test_wired_in_the_graph_and_the_panel(self):
        graph = (ROOT / "static/js/graph.js").read_text(encoding="utf-8")
        reader = (ROOT / "static/js/reader.js").read_text(encoding="utf-8")
        self.assertIn("if (deselects(event.target, selected)) onSelect(null);", graph)
        self.assertIn("onSelect: (id) => (id ? this.select(id, { scroll: true }) : this.select(null)),", reader)

if __name__ == "__main__":
    unittest.main()
