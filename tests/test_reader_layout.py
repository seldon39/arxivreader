"""Mise en page du lecteur : bandeau du texte caché collé sous la barre.

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


if __name__ == "__main__":
    unittest.main()
