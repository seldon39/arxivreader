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
             mock.patch.object(check, "system", return_value=system), mock.patch.object(check.alerts, "display") as display, \
             mock.patch("builtins.print"):
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
        checks = [("arXiv", down), ("Zotero", down), (check.CLAUDE, lambda: "CLI 2.1.321, connecté (opus)")]
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
        # assistant pas lancé, pas installé, sans clé, aucune version publiée : non vérifié, pas « changé »
        with mock.patch("app.ai.status", return_value={"provider": "local", "available": False}), \
             mock.patch("app.ai.current_model", return_value="local:x"), \
             mock.patch.object(check.db, "DB_PATH", self.report), mock.patch.object(check.db, "get_settings", return_value={}):
            self.report.write_text("{}")
            with self.assertRaises(check.Skip):
                check.local_assistant()
            with mock.patch.object(check.providers, "find", return_value=None), self.assertRaises(check.Skip):
                check.codex_assistant()
            with self.assertRaises(check.Skip):
                check.gemini_assistant()
        with mock.patch.object(check.updates, "fetch", return_value=None), self.assertRaises(check.Skip):
            check.github_updates()

    def test_assistant_checks(self):
        """Claude, Codex, Gemini vérifiés qu'ils soient choisis ou non : version du CLI, connexion, modèles."""
        settings = {"aiProvider": "codex", "geminiApiKey": "k"}
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="codex-cli 0.162.1\n", stderr=""))
        with mock.patch.object(check.db, "DB_PATH", self.report), mock.patch.object(check.db, "get_settings", return_value=settings), \
             mock.patch.object(check.subprocess, "run", run):
            self.report.write_text("{}")
            with mock.patch.object(check.providers, "find", return_value="/x/codex"), \
                 mock.patch.object(check.providers, "status", return_value={"loggedIn": True, "chosen": "gpt-5"}):
                detail, seen = check.codex_assistant()
            self.assertEqual(detail, "CLI 0.162.1, connecté (gpt-5), assistant choisi")
            self.assertEqual(seen, {"id": "codex", "name": "Codex", "version": "0.162.1", "followed": True})
            self.assertEqual(run.call_args.args[0], ["/x/codex", "--version"])
            # Claude : CLI intégré au SDK (suivi avec arXivReader), pas connecté : non vérifié, version retenue
            run.return_value = mock.Mock(returncode=0, stdout="2.1.321 (Claude Code)\n", stderr="")
            with mock.patch("app.ai.cli_path", return_value="/sdk/_bundled/claude"), \
                 mock.patch("app.ai.status", return_value={"loggedIn": False}):
                with self.assertRaises(check.Skip) as skipped:
                    check.claude_assistant()
            self.assertIn("non connecté", str(skipped.exception))
            self.assertEqual(skipped.exception.assistant["version"], "2.1.321")
            self.assertFalse(skipped.exception.assistant["followed"])
            # CLI en échec (Node trop ancien) : le numéro du message n'est pas une version
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="SyntaxError … Node.js v16.20.2")
            with mock.patch.object(check.providers, "find", return_value="/x/codex"), self.assertRaises(RuntimeError):
                check.codex_assistant()
            # sortie illisible : changé
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="unknown option")
            with mock.patch.object(check.providers, "find", return_value="/x/codex"), self.assertRaises(check.Changed):
                check.codex_assistant()
            # Gemini : modèles ; clé refusée, quota : non vérifié ; aucun modèle : changé
            with mock.patch.object(check.providers, "gemini_models", return_value=["gemini-3-flash", "gemini-3-pro"]):
                detail, seen = check.gemini_assistant()
            self.assertEqual(seen["version"], ["gemini-3-flash", "gemini-3-pro"])
            self.assertTrue(detail.startswith("2 modèles"))
            for code in ("login", "limit"):
                with mock.patch.object(check.providers, "gemini_models", side_effect=check.providers.ProviderError("x", code)), \
                     self.assertRaises(check.Skip):
                    check.gemini_assistant()
            with mock.patch.object(check.providers, "gemini_models", return_value=[]), self.assertRaises(check.Changed):
                check.gemini_assistant()

    def test_assistant_change_is_reported(self):
        """Nouvelle version de Codex, modèle Gemini retiré : notification ; CLI intégré au SDK, modèle seulement ajouté :
        dans le rapport, sans notification ; un assistant pas vu (pas de réseau) garde sa référence."""
        def with_versions(codex, gemini, claude="2.1.321"):
            def codex_check():
                return "ok", {"id": "codex", "name": "Codex", "version": codex, "followed": True}
            def gemini_check():
                if gemini is None:
                    raise check.Skip("quota atteint")
                return "ok", {"id": "gemini", "name": "Gemini", "version": gemini, "followed": True}
            def claude_check():
                raise check.Skip("non connecté", {"id": "claude", "name": "Claude", "version": claude, "followed": False})
            return [(check.CODEX, codex_check), (check.GEMINI, gemini_check), (check.CLAUDE, claude_check)]
        system = {"macos": "27.2", "webkit": "1"}
        code, display = self.run_with(with_versions("0.162.1", ["g-flash", "g-pro"]), system)
        self.assertEqual(display.call_count, 0)  # première fois : la référence
        code, display = self.run_with(with_versions("0.162.1", ["g-flash", "g-pro", "g-ultra"], "2.1.400"), system)
        self.assertEqual((code, display.call_count), (0, 0))
        report = check.json.loads(self.report.read_text(encoding="utf-8"))
        self.assertIn("Gemini : modèles ajoutés : g-ultra", check.text(report))
        self.assertIn("Claude 2.1.321 → 2.1.400 (CLI intégré au SDK", check.text(report))
        code, display = self.run_with(with_versions("0.163.0", None, "2.1.400"), system)  # Gemini pas vu : gardé
        self.assertEqual(display.call_count, 1)
        self.assertEqual(display.call_args.args[1], "Un assistant IA a changé")
        self.assertIn("Codex 0.162.1 → 0.163.0", display.call_args.args[2])
        self.assertIn("model_reasoning_effort", display.call_args.args[2])
        self.assertEqual(display.call_args.kwargs.get("route"), "#/check")
        code, display = self.run_with(with_versions("0.163.0", ["g-flash", "g-ultra"], "2.1.400"), system)
        self.assertEqual(display.call_count, 1)
        self.assertIn("Gemini : modèles retirés : g-pro", display.call_args.args[2])
        self.assertIn("thinkingLevel", display.call_args.args[2])
        # à la main : signalé dans le rapport, référence gardée ; la tâche hebdomadaire le notifie ensuite
        self.run_with(with_versions("0.164.0", ["g-flash", "g-ultra"], "2.1.400"), system, argv=())
        code, display = self.run_with(with_versions("0.164.0", ["g-flash", "g-ultra"], "2.1.400"), system)
        self.assertIn("Codex 0.163.0 → 0.164.0", display.call_args.args[2])
        # rapport d'une version précédente (sans assistants) ou abîmé : aucune comparaison
        self.report.write_text('{"assistantsBaseline": {"codex": "0.1"}, "assistantChanges": [null]}')
        code, display = self.run_with(with_versions("0.165.0", ["g-flash"]), system)
        self.assertEqual(display.call_count, 0)
        self.assertIsInstance(check.text({"assistantChanges": [None, {"id": "x"}]}), str)

if __name__ == "__main__":
    unittest.main()
