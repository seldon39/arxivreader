"""Vérification des services : classement des résultats, changement de macOS/WebKit, notification (sans réseau)."""

import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from app import check


@unittest.skipIf(sys.platform == "win32", "vérification hebdomadaire : outil du Mac (launchd)")
class Check(unittest.TestCase):
    def setUp(self):
        self.report = pathlib.Path(tempfile.mkdtemp()) / "check.json"

    def run_with(self, checks, system, argv=("--notify",)):
        with mock.patch.object(check, "CHECKS", checks), mock.patch.object(check, "REPORT", self.report), \
             mock.patch.object(check, "system", return_value=system), mock.patch.object(check.alerts, "display") as display:
            code = check.main(list(argv))
        return code, display

    def test_results_and_notifications(self):
        def changed():
            check.expect(False, "classes LaTeXML absentes : ltx_para")
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


    def test_manual_run_keeps_the_weekly_baseline(self):
        self.run_with([("bon", lambda: "ok")], {"macos": "27.0", "webkit": "1"})          # tâche hebdomadaire : référence 1
        self.run_with([("bon", lambda: "ok")], {"macos": "27.1", "webkit": "2"}, argv=())  # à la main : signalé, référence gardée
        code, display = self.run_with([("bon", lambda: "ok")], {"macos": "27.1", "webkit": "2"})
        self.assertIn("WebKit", display.call_args.args[1])  # la tâche hebdomadaire le signale encore

    def test_corrupt_report(self):
        self.report.write_text("[1, 2]")
        code, _ = self.run_with([("bon", lambda: "ok")], {"macos": "27.0", "webkit": "1"})
        self.assertEqual(code, 0)

    def test_not_a_change(self):
        # assistant pas lancé, aucune version publiée : non vérifié, pas « changé »
        with mock.patch("app.ai.status", return_value={"provider": "local", "available": False}), \
             mock.patch.object(check.db, "DB_PATH", self.report), mock.patch.object(check.db, "get_settings", return_value={}):
            self.report.write_text("{}")
            with self.assertRaises(check.Skip):
                check.ai_assistant()
        with mock.patch.object(check.updates, "fetch", return_value=None), self.assertRaises(check.Skip):
            check.github_updates()


if __name__ == "__main__":
    unittest.main()
