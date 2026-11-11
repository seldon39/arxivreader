"""Coque Mac : règle de validité des routes rouvertes (session) et clés de texte des réparations de l'interface."""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SWIFT = (ROOT / "macos" / "main.swift").read_text(encoding="utf8")
I18N = (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf8")


def route_pattern():
    found = re.search(r'static func validRoute.*?range\(of: "(.*?)", options', SWIFT, re.S)
    return re.compile(found.group(1).replace("\\\\", "\\"))


class Routes(unittest.TestCase):
    def test_app_routes_are_accepted(self):
        pattern = route_pattern()
        for route in ("#/", "#/paper/2401.01234", "#/paper/math%2FAP.0501?x=1&y=2", "#/feed"):
            self.assertTrue(pattern.match(route), route)

    def test_foreign_routes_are_refused(self):
        pattern = route_pattern()
        for route in ("", "http://example.com", "#paper", "#/a b", "#/<script>", "javascript:alert(1)"):
            self.assertFalse(pattern.match(route), route)

    def test_page_messages_use_the_same_rule(self):
        self.assertIn("AppDelegate.validRoute(route)", SWIFT)

    def test_no_session_in_test_runs(self):
        for variable in ("ARXIVREADER_START", "ARXIVREADER_SNAPSHOT", "ARXIVREADER_WINDOWSHOT", "ARXIVREADER_EVAL"):
            self.assertIn(f'"{variable}"', SWIFT[SWIFT.index("var sessionEnabled"):])


class Keys(unittest.TestCase):
    def test_new_keys_in_both_languages(self):
        for key in ("settings.saveFailed", "onboarding.error.local"):
            self.assertEqual(len(re.findall(rf'"{re.escape(key)}":', I18N)), 2, key)


if __name__ == "__main__":
    unittest.main()
