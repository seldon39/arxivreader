"""Vérification des services : classement des résultats, changement de macOS/WebKit, notification (sans réseau)."""

import pathlib
import tempfile
import unittest
from unittest import mock

from app import check


class Check(unittest.TestCase):
    def setUp(self):
        self.report = pathlib.Path(tempfile.mkdtemp()) / "check.json"

    def run_with(self, checks, system):
        with mock.patch.object(check, "CHECKS", checks), mock.patch.object(check, "REPORT", self.report), \
             mock.patch.object(check, "system", return_value=system), mock.patch.object(check.alerts, "display") as display:
            code = check.main(["--notify"])
        return code, display

    def test_results_and_notifications(self):
        def changed():
            raise AssertionError("classes LaTeXML absentes : ltx_para")
        def skipped():
            raise check.Skip("pas de clé")
        def failed():
            raise OSError("réseau")
        checks = [("bon", lambda: "ok"), ("changé", changed), ("sans clé", skipped), ("en panne", failed)]
        code, display = self.run_with(checks, {"macos": "27.0", "webkit": "1"})
        self.assertEqual(code, 1)
        self.assertEqual(display.call_count, 1)  # un service : une notification ; premier passage : pas de « système changé »
        self.assertIn("ltx_para", display.call_args.args[2])
        # tout va bien, mais WebKit a changé : une notification, code 0
        code, display = self.run_with([("bon", lambda: "ok"), ("sans clé", skipped)], {"macos": "27.1", "webkit": "2"})
        self.assertEqual((code, display.call_count), (0, 1))
        self.assertIn("WebKit", display.call_args.args[1])
        # rien de changé : rien
        code, display = self.run_with([("bon", lambda: "ok")], {"macos": "27.1", "webkit": "2"})
        self.assertEqual((code, display.call_count), (0, 0))


if __name__ == "__main__":
    unittest.main()
