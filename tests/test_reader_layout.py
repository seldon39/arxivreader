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
        if selector in [s.strip() for s in selectors.split(",")]:
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


if __name__ == "__main__":
    unittest.main()
