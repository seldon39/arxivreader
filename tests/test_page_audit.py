"""Corrections de l'audit des pages (navigateur) : fonctions pures de static/js essayées dans Node (si présent)."""

import shutil
import unittest

from tests import jsfunction


@unittest.skipUnless(shutil.which("node"), "Node absent")
class Proposals(unittest.TestCase):
    ANSWER = 'Voici.\\n```annotations\\n[{\\"delete\\": 3}, {\\"quote\\": \\"x\\"}]\\n```'

    def parse(self, options=""):
        return jsfunction.run("ai.js", "parseProposals", f'parseProposals("{self.ANSWER}"{options}).items')

    def test_deletion_only_for_an_ask_exchange(self):
        self.assertEqual(self.parse(), [{"quote": "x"}])
        self.assertEqual(self.parse(", {deletions: false}"), [{"quote": "x"}])
        self.assertEqual(self.parse(", {deletions: true}"), [{"delete": 3}, {"quote": "x"}])


if __name__ == "__main__":
    unittest.main()
