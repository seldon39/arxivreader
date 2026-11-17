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


    def test_offline_is_not_a_change_and_is_retried(self):
        """Pas de réseau (Mac qui sort de veille) : nouveaux essais, puis aucune notification « service » ; le réseau
        revenu entre deux essais : vérification normale."""
        import httpx
        def down():
            raise httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")
        checks = [("arXiv", down), ("Zotero", down), ("Assistant IA", lambda: "claude (opus)")]
        with mock.patch.object(check, "RETRY_WAIT", 0), mock.patch.object(check.time, "sleep") as sleep:
            code, display = self.run_with(checks, {"macos": "27.2", "webkit": "1"})
        self.assertEqual((code, display.call_count, sleep.call_count), (0, 0, check.RETRIES))
        report = check.json.loads(self.report.read_text(encoding="utf-8"))
        self.assertTrue(report["offline"])
        self.assertEqual([r["status"] for r in report["results"]], ["offline", "offline", "ok"])
        self.assertIn("Pas de réseau", check.text(report))
        # une vraie panne (réponse d'erreur) n'est pas « pas de réseau » ; à la main : pas de nouvel essai
        def broken():
            raise httpx.HTTPStatusError("503", request=httpx.Request("GET", "https://x"), response=httpx.Response(503))
        with mock.patch.object(check.time, "sleep") as sleep:
            code, display = self.run_with([("arXiv", down), ("Zotero", broken)], {"macos": "27.2", "webkit": "1"})
        self.assertEqual((code, display.call_count, sleep.call_count), (1, 1, 0))
        self.assertEqual(display.call_args.kwargs.get("route"), "#/check")
        # réseau revenu au deuxième essai
        calls = []
        def flaky():
            calls.append(1)
            if len(calls) == 1:
                down()
            return "ok"
        with mock.patch.object(check.time, "sleep") as sleep:
            code, display = self.run_with([("arXiv", flaky)], {"macos": "27.2", "webkit": "1"})
        self.assertEqual((code, display.call_count, sleep.call_count, len(calls)), (0, 0, 1, 2))
        self.assertFalse(check.json.loads(self.report.read_text(encoding="utf-8"))["offline"])

    def test_report_route(self):
        """Page #/check : le rapport en texte ; aucun rapport, ou abîmé : texte vide."""
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        with mock.patch.object(check, "REPORT", self.report):
            self.assertEqual(client.get("/api/check").json(), {"checkedAt": None, "text": ""})
            self.report.write_text("[1]")
            self.assertEqual(client.get("/api/check").json()["text"], "")
            self.run_with([("bon", lambda: "ok")], {"macos": "27.2", "webkit": "1"})
            data = client.get("/api/check").json()
        self.assertTrue(data["checkedAt"])
        self.assertIn("✓ bon", data["text"])

    def test_system_change_says_what_moved(self):
        self.run_with([("bon", lambda: "ok")], {"macos": "27.2", "webkit": "22625"})
        code, display = self.run_with([("bon", lambda: "ok")], {"macos": "27.2.1", "webkit": "22625"})
        self.assertEqual(display.call_args.args[1], "macOS a changé (WebKit inchangé)")
        self.assertIn("macOS 27.2 → 27.2.1, WebKit inchangé (22625)", display.call_args.args[2])
        self.assertEqual(display.call_args.kwargs.get("route"), "#/check")
        report = check.json.loads(self.report.read_text(encoding="utf-8"))
        self.assertIn("À revoir :", check.text(report))
        code, display = self.run_with([("bon", lambda: "ok")], {"macos": "27.2.1", "webkit": "22626"})
        self.assertEqual(display.call_args.args[1], "WebKit a changé")
        self.assertEqual(check.text({"results": [None, {"status": "?"}]}).count("\n"), 2)  # rapport abîmé : lisible

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
