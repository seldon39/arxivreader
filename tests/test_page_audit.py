"""Corrections de l'audit des pages (navigateur) : fonctions pures de static/js essayées dans Node (si présent)."""

import json
import pathlib
import shutil
import subprocess
import unittest

from tests import jsfunction

ROOT = pathlib.Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "Node absent")
class Proposals(unittest.TestCase):
    ANSWER = 'Voici.\\n```annotations\\n[{\\"delete\\": 3}, {\\"quote\\": \\"x\\"}]\\n```'

    def parse(self, options=""):
        return jsfunction.run("ai.js", "parseProposals", f'parseProposals("{self.ANSWER}"{options}).items')

    def test_deletion_only_for_an_ask_exchange(self):
        self.assertEqual(self.parse(), [{"quote": "x"}])
        self.assertEqual(self.parse(", {deletions: false}"), [{"quote": "x"}])
        self.assertEqual(self.parse(", {deletions: true}"), [{"delete": 3}, {"quote": "x"}])


@unittest.skipUnless(shutil.which("node"), "Node absent")
class Markdown(unittest.TestCase):
    PRELUDE = ('globalThis.window = globalThis; globalThis.document = {addEventListener() {}, querySelectorAll: () => [],'
               ' getElementById: () => null, createElement: () => ({})}; globalThis.localStorage = {getItem: () => null, setItem() {}};\n')

    def render(self, source):
        code = self.PRELUDE + f'const {{ markdownToHtml }} = await import("{(ROOT / "static/js/markdown.js").as_uri()}");\n' \
            'const refs = (s) => s.replace(/\\[(\\d+)\\]/g, \'<a class="ai-ref" href="#r$1">[$1]</a>\');\n' \
            f"console.log(JSON.stringify(markdownToHtml({json.dumps(source)}, {{ refs }})));"
        done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    def test_link_address_is_left_alone_by_emphasis_and_references(self):
        out = self.render("[t](http://x/`c`[12])")
        self.assertIn('<a href="http://x/%60c%60', out)
        self.assertNotIn("<code>", out)
        out = self.render("[t](http://x/_a_/b)")
        self.assertNotIn("<em>", out)
        self.assertIn('href="http://x/%5Fa%5F/b"', out)
        self.assertIn('<a href="http://x/a" target="_blank"', self.render("[t](http://x/a) et *fin*"))


if __name__ == "__main__":
    unittest.main()
