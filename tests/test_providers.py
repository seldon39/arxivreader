"""Autres assistants : Codex (programme simulé), Gemini et modèle local (serveurs simulés), réglages."""

import asyncio
import json
import os
import pathlib
import plistlib
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from app import ai, db, main, providers

# Programme simulé : note ses arguments, son entrée et son environnement, puis écrit les événements donnés
FAKE = """#!/usr/bin/env python3
import json, os, sys
open(os.environ["FAKE_LOG"], "w").write(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read(),
    "system": os.environ.get("GEMINI_SYSTEM_MD"), "settings": os.environ.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH"),
    "key": os.environ.get("OPENAI_API_KEY"), "cwd": os.getcwd()}))
sys.stdout.write(os.environ["FAKE_OUT"])
sys.stderr.write(os.environ.get("FAKE_ERR", ""))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


async def collect(stream):
    return [text async for text in stream]


@unittest.skipIf(sys.platform == "win32", "programmes simulés : scripts Unix")
class Providers(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.saved = (providers.find, providers.LIBRARY, dict(os.environ), db.DB_PATH, db.LIBRARY)
        db.DB_PATH, db.LIBRARY = self.tmp / "test.db", self.tmp
        db.init()
        providers.LIBRARY = self.tmp / "library"
        script = self.tmp / "fake"
        script.write_text(FAKE)
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        providers.find = lambda command: str(script)
        os.environ["FAKE_LOG"] = str(self.tmp / "log.json")
        os.environ["OPENAI_API_KEY"] = "sk-should-not-pass"

    def tearDown(self):
        providers.find, providers.LIBRARY = self.saved[:2]
        db.DB_PATH, db.LIBRARY = self.saved[3:]
        os.environ.clear()
        os.environ.update(self.saved[2])

    def log(self):
        return json.loads((self.tmp / "log.json").read_text())

    def events(self, *events, exit=0, err=""):
        os.environ["FAKE_OUT"] = "".join(json.dumps(e) + "\n" for e in events) + "not json\n"
        os.environ["FAKE_EXIT"], os.environ["FAKE_ERR"] = str(exit), err

    def test_codex(self):
        self.events({"type": "thread.started"}, {"type": "item.completed", "item": {"type": "reasoning", "text": "hmm"}},
                    {"type": "item.completed", "item": {"type": "agent_message", "text": "Le lemme dit $P=1$."}},
                    {"type": "turn.completed"})
        chunks = asyncio.run(collect(providers.stream_codex("SYSTEM", "PROMPT")))
        self.assertEqual(chunks, ["Le lemme dit $P=1$."])
        log = self.log()
        self.assertEqual(log["stdin"], "PROMPT")
        self.assertIn("features.shell_tool=false", log["argv"])  # aucune commande
        self.assertIn('web_search="disabled"', log["argv"])
        self.assertIn("--ignore-user-config", log["argv"])        # ni serveurs MCP ni réglages de l'utilisateur
        self.assertEqual(pathlib.Path(log["cwd"]).resolve(), (providers.LIBRARY / "ai" / "codex").resolve())
        self.assertIsNone(log["key"])                             # compte ChatGPT, pas une clé d'API
        instructions = next(a for a in log["argv"] if a.startswith("model_instructions_file="))
        self.assertEqual(pathlib.Path(json.loads(instructions.split("=", 1)[1])).read_text(), "SYSTEM")

    def test_codex_images(self):
        # zone du PDF et pages d'un scan : fichiers joints après la question, effacés ensuite
        self.events({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})
        asyncio.run(collect(providers.stream_codex("S", "P", images=[("image/jpeg", b"\xff\xd8\xffpage"), ("image/png", b"png")])))
        argv = self.log()["argv"]
        images = [a.split("=", 1)[1] for a in argv if a.startswith("--image=")]
        self.assertEqual([pathlib.Path(p).suffix for p in images], [".jpg", ".png"])
        self.assertLess(argv.index("-"), argv.index(f"--image={images[0]}"))
        self.assertFalse(pathlib.Path(images[0]).parent.exists())
        self.assertNotEqual(pathlib.Path(images[0]).parent.parent.resolve(), (providers.LIBRARY / "ai" / "codex").resolve())

    def test_codex_model_and_effort(self):
        self.events({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})
        asyncio.run(collect(providers.stream_codex("S", "P", "gpt-6-luna", "high")))
        argv = self.log()["argv"]
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-6-luna")
        self.assertIn('model_reasoning_effort="high"', argv)
        asyncio.run(collect(providers.stream_codex("S", "P", "bad model; rm", "turbo")))  # refusés : réglages de Codex
        argv = self.log()["argv"]
        self.assertNotIn("-m", argv)
        self.assertFalse(any("model_reasoning_effort" in a for a in argv))

    def test_codex_models_from_its_cache(self):
        from unittest import mock
        home = self.tmp / "home"
        (home / ".codex").mkdir(parents=True)
        (home / ".codex" / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-old", "display_name": "Old", "visibility": "list", "priority": 9,
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "ultra"}]},
            {"slug": "gpt-new", "display_name": "New", "visibility": "list", "priority": 4,
             "supported_reasoning_levels": [{"effort": "medium"}, {"effort": "weird"}]},
            {"slug": "gpt-hidden", "visibility": "hide", "priority": 1}]}))
        with mock.patch.object(providers.Path, "home", return_value=home):
            models = providers.codex_models()
        self.assertEqual([(m["value"], m["levels"]) for m in models], [("gpt-new", ["medium"]), ("gpt-old", ["low", "ultra"])])
        for broken in ("[]", "null", json.dumps({"models": [{"slug": "x", "visibility": "list", "supported_reasoning_levels": 5}]})):
            (home / ".codex" / "models_cache.json").write_text(broken)  # cache mal formé : pas d'erreur 500
            with mock.patch.object(providers.Path, "home", return_value=home):
                self.assertIsInstance(providers.codex_models(), list)
        # niveau enregistré que le modèle choisi ne propose pas : celui de Codex par défaut
        (home / ".codex" / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-new", "visibility": "list", "priority": 1, "supported_reasoning_levels": [{"effort": "medium"}]}]}))
        with mock.patch.object(providers.Path, "home", return_value=home):
            self.assertEqual(providers.codex_choice({"aiCodexEffort": "xhigh"}), (mock.ANY, "gpt-new", ""))
            self.assertEqual(providers.codex_choice({"aiCodexEffort": "medium"})[1:], ("gpt-new", "medium"))

    def test_codex_errors(self):
        self.events({"type": "error", "message": "Not logged in"}, exit=1)
        with self.assertRaises(providers.ProviderError) as caught:
            asyncio.run(collect(providers.stream_codex("S", "P")))
        self.assertEqual(caught.exception.code, "login")
        self.events({"type": "turn.failed", "error": {"message": "usage limit reached"}})
        with self.assertRaises(providers.ProviderError) as caught:
            asyncio.run(collect(providers.stream_codex("S", "P")))
        self.assertEqual(caught.exception.code, "limit")
        self.events(exit=1, err="Loaded cached credentials.\nError: 429 Too Many Requests\n")  # pas « login »
        with self.assertRaises(providers.ProviderError) as caught:
            asyncio.run(collect(providers.stream_codex("S", "P")))
        self.assertEqual(caught.exception.code, "limit")

    def test_program_that_exits_at_once(self):
        # programme qui s'arrête sans lire la demande (option refusée) : son message, pas une erreur de tuyau
        script = self.tmp / "quick"
        script.write_text("#!/bin/sh\necho 'error: unexpected argument --ignore-user-config' >&2\nexit 2\n")
        script.chmod(0o755)
        providers.find = lambda command: str(script)
        with self.assertRaises(providers.ProviderError) as caught:
            asyncio.run(collect(providers.stream_codex("S", "P" * 2_000_000)))
        self.assertIn("unexpected argument", str(caught.exception))

    def test_dispatch_and_model(self):
        self.events({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})
        self.assertEqual(asyncio.run(collect(ai.stream_answer("S", "P", "codex"))), ["ok"])
        self.events(exit=2, err="boom\n")
        with self.assertRaises(ai.AIError):
            asyncio.run(collect(ai.stream_answer("S", "P", "codex")))
        self.assertEqual(ai.current_model({"aiProvider": "local", "aiLocalModel": "qwen3"}), "local:qwen3")
        self.assertEqual(ai.current_model({"aiProvider": "claude", "aiModel": "sonnet"}), "sonnet")
        self.assertEqual(ai.current_model({}), "opus")
        self.assertEqual((ai.assistant_name("local:qwen3"), ai.assistant_name("gemini"), ai.assistant_name("opus")),
                         ("qwen3", "Gemini", "Claude"))
        self.assertEqual(ai.paper_limit("local:qwen3"), providers.LOCAL_LIMIT)


class Status(unittest.TestCase):
    def test_failed_check_is_inconclusive(self):
        with unittest.mock.patch.object(providers, "find", return_value="/bin/codex"), \
                unittest.mock.patch.object(providers, "codex_choice", return_value=([], None, "")):
            with unittest.mock.patch.object(providers.subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 20)):
                self.assertTrue(providers.status("codex", {})["inconclusive"])
            with unittest.mock.patch.object(providers.subprocess, "run", return_value=unittest.mock.Mock(returncode=1, stdout="Not logged in", stderr="")):
                self.assertFalse(providers.status("codex", {})["inconclusive"])  # déconnecté pour de bon
            with unittest.mock.patch.object(providers.subprocess, "run", return_value=unittest.mock.Mock(returncode=2, stdout="", stderr="boom")):
                self.assertTrue(providers.status("codex", {})["inconclusive"])

    def test_gemini_refused_key_is_the_only_clear_answer(self):
        for code, unclear in (("login", False), ("limit", True), ("other", True)):
            with unittest.mock.patch.object(providers, "gemini_models", side_effect=providers.ProviderError("no", code)):
                self.assertEqual(providers.status("gemini", {"geminiApiKey": "k"})["inconclusive"], unclear, code)

    def test_local_server_off_or_busy(self):
        with unittest.mock.patch.object(providers.httpx.Client, "get", side_effect=providers.httpx.ConnectError("off")):
            self.assertNotIn("inconclusive", providers.status("local", {}))
        with unittest.mock.patch.object(providers.httpx.Client, "get", side_effect=providers.httpx.ReadTimeout("busy")):
            self.assertTrue(providers.status("local", {})["inconclusive"])

    def test_claude_needs_a_clear_answer(self):
        for stdout, unclear in (('{"loggedIn": false}', False), ("", True), ("oops", True)):
            with unittest.mock.patch.object(ai, "cli_path", return_value="/bin/claude"), \
                    unittest.mock.patch.object(ai.subprocess, "run", return_value=unittest.mock.Mock(returncode=1, stdout=stdout, stderr="")):
                self.assertEqual(ai._claude_status()["inconclusive"], unclear, stdout)


class Setup(unittest.TestCase):
    @unittest.mock.patch.object(providers, "WINDOWS", False)
    def test_terminal_script(self):
        saved = (providers.LIBRARY, providers.subprocess.run)
        providers.LIBRARY = pathlib.Path(tempfile.mkdtemp())
        opened = []
        providers.subprocess.run = lambda argv, **kw: opened.append(argv)
        try:
            providers.open_setup("codex", "login")
            script = providers.LIBRARY / "ai" / "setup-codex.command"
            self.assertIn("codex login", script.read_text())
            self.assertEqual(opened[0][:3], ["open", "-a", "Terminal"])
            providers.open_setup("claude", "login", "/path with space/claude")
            self.assertIn("'/path with space/claude' auth login --claudeai", (providers.LIBRARY / "ai" / "setup-claude.command").read_text())
            # changer de compte : déconnexion, puis connexion
            # (connexion dans une fenêtre privée : le compte du navigateur serait repris) ; la simple connexion n'y touche pas
            self.assertNotIn("BROWSER", (providers.LIBRARY / "ai" / "setup-claude.command").read_text())
            helper = providers.LIBRARY / "ai" / "setup-browser.sh"
            with unittest.mock.patch.object(providers, "_default_browser", return_value="com.google.chrome"):
                providers.open_setup("claude", "switch", "/bin/claude")
            text = (providers.LIBRARY / "ai" / "setup-claude.command").read_text()
            self.assertIn("/bin/claude auth logout && echo", text)
            self.assertIn(f"&& BROWSER={shlex.quote(str(helper))} env -u ANTHROPIC_API_KEY /bin/claude auth login --claudeai", text)
            self.assertIn("A private window", text)
            self.assertIn("/usr/bin/open -na 'Google Chrome' --args --incognito \"$1\"", helper.read_text())
            self.assertTrue(os.access(helper, os.X_OK))
            with unittest.mock.patch.object(providers, "_default_browser", return_value=""):  # Safari : pas d'option
                providers.open_setup("claude", "switch", "/bin/claude")
            self.assertIn("will be copied", (providers.LIBRARY / "ai" / "setup-claude.command").read_text())
            self.assertIn("pbcopy", helper.read_text())
            self.assertNotIn("open", helper.read_text())
            providers.open_setup("codex", "switch")
            self.assertIn("codex logout && codex login", (providers.LIBRARY / "ai" / "setup-codex.command").read_text())
            with self.assertRaises(ValueError):  # commandes fixées d'avance, rien d'autre
                providers.open_setup("codex", "rm -rf ~")
        finally:
            providers.LIBRARY, providers.subprocess.run = saved

    def test_default_browser(self):
        home = pathlib.Path(tempfile.mkdtemp())
        with unittest.mock.patch.object(providers.Path, "home", return_value=home):
            self.assertEqual(providers._default_browser(), "")  # pas de fichier : Safari
            path = home / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"pas un plist")
            self.assertEqual(providers._default_browser(), "")
            path.write_bytes(plistlib.dumps({"LSHandlers": [{"LSHandlerURLScheme": "mailto", "LSHandlerRoleAll": "x"},
                                                            {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "com.Google.Chrome"}]}))
            self.assertEqual(providers._default_browser(), "com.google.chrome")

    def test_logout(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 1 if "fail" in argv[0] else 0, "", "not logged in")
        ai._claude_models.update(email="a@b", models=[{"value": "opus"}], at=1.0)
        # environnement fixé : sinon la recherche de Node (Windows : « node --version ») passerait aussi par ce run
        with unittest.mock.patch.object(ai.subprocess, "run", run), unittest.mock.patch.object(ai, "cli_path", return_value="/bin/claude"), \
             unittest.mock.patch.object(providers, "find", return_value="/bin/codex"), unittest.mock.patch.object(providers, "_environment", return_value={}):
            ai.logout("claude")
            ai.logout("codex")
            self.assertEqual(calls, [["/bin/claude", "auth", "logout"], ["/bin/codex", "logout"]])
            self.assertIsNone(ai._claude_models["models"])  # liste des modèles du compte relue ensuite
            with self.assertRaises(ValueError):
                ai.logout("gemini")
        with unittest.mock.patch.object(ai.subprocess, "run", run), unittest.mock.patch.object(ai, "cli_path", return_value="/fail/claude"):
            with self.assertRaises(ai.AIError):
                ai.logout("claude")

    @unittest.mock.patch.object(providers, "WINDOWS", True)
    def test_command_prompt_script(self):
        library = pathlib.Path(tempfile.mkdtemp())
        with unittest.mock.patch.object(providers, "LIBRARY", library), \
             unittest.mock.patch.object(providers, "_recent_node", return_value=""), \
             unittest.mock.patch.object(providers.subprocess, "Popen") as popen:
            providers.open_setup("claude", "login", r"C:\Program Files\arXivReader\claude.exe")
            script = library / "ai" / "setup-claude.cmd"
            text = script.read_text(encoding="utf-8")
            self.assertIn('"C:\\Program Files\\arXivReader\\claude.exe" auth login --claudeai', text)
            self.assertIn("set ANTHROPIC_API_KEY=", text)
            self.assertEqual(popen.call_args[0][0], ["cmd.exe", "/k", str(script)])
            providers.open_setup("local", "missing")
            text = (library / "ai" / "setup-local.cmd").read_text(encoding="utf-8")
            self.assertIn("set OLLAMA_CONTEXT_LENGTH=32768 && call ollama serve", text)
            self.assertIn("echo $ set OLLAMA_CONTEXT_LENGTH=32768 ^&^& call ollama serve", text)  # affichée, pas exécutée
            providers.open_setup("codex", "missing")  # npm et codex sont des .cmd : « call », sinon le script s'arrête
            self.assertIn("call npm install -g @openai/codex && call codex login", (library / "ai" / "setup-codex.cmd").read_text(encoding="utf-8"))
            self.assertIn("set OPENAI_API_KEY=", text)
            providers.open_setup("codex", "switch")
            self.assertIn("call codex logout && call codex login", (library / "ai" / "setup-codex.cmd").read_text(encoding="utf-8"))
            providers.open_setup("claude", "switch", r"C:\claude.exe")
            text = (library / "ai" / "setup-claude.cmd").read_text(encoding="utf-8")
            self.assertIn('"C:\\claude.exe" auth logout && echo', text)
            self.assertIn(f'set "BROWSER={providers.NO_BROWSER}" && "C:\\claude.exe" auth login --claudeai', text)
            with self.assertRaises(ValueError):
                providers.open_setup("codex", "rm -rf ~")


class Local(unittest.TestCase):
    def setUp(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                payload = json.dumps({"data": [{"id": "qwen3"}, {"id": "llama3"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                Handler.received = body
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for text in ("<think>", "réfléchir…", "</think>\n\n", "La ", "réponse."):
                    chunk = {"choices": [{"delta": {"content": text}}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        self.handler = Handler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def test_stream_and_status(self):
        chunks = asyncio.run(collect(providers.stream_local("SYSTEM", "PROMPT", "qwen3", self.url)))
        self.assertEqual("".join(chunks), "La réponse.")  # raisonnement <think> non affiché
        self.assertEqual(self.handler.received["messages"][0], {"role": "system", "content": "SYSTEM"})
        self.assertEqual(self.handler.received["model"], "qwen3")
        info = providers.status("local", {"aiLocalUrl": self.url})
        self.assertEqual((info["loggedIn"], info["models"]), (True, ["llama3", "qwen3"]))

    def test_refusals(self):
        with self.assertRaises(providers.ProviderError):  # serveur hors de cet ordinateur
            asyncio.run(collect(providers.stream_local("S", "P", "m", "http://example.com/v1")))
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaises(providers.ProviderError) as caught:
            asyncio.run(collect(providers.stream_local("S", "P", "m", self.url)))
        self.assertEqual(caught.exception.code, "offline")
        self.server = None
        self.assertFalse(providers.status("local", {"aiLocalUrl": self.url})["available"])


class Gemini(unittest.TestCase):
    """API Gemini (serveur simulé) : clé dans l'en-tête, aucun outil, réponse au fil de l'eau, erreurs."""

    def setUp(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, payload):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def do_GET(self):
                Handler.key = self.headers.get("x-goog-api-key")
                if Handler.key != "good":
                    return self.reply(400, {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT",
                                                      "details": [{"reason": "API_KEY_INVALID"}]}})
                self.reply(200, {"models": [
                    {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent", "countTokens"]},
                    {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]}]})

            def do_POST(self):
                if "blocked" in self.path:
                    self.rfile.read(int(self.headers["Content-Length"]))  # demande lue : pas de connexion coupée
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b'data: {"promptFeedback": {"blockReason": "SAFETY"}}\r\n\r\n')
                    return
                Handler.path_seen, Handler.key = self.path, self.headers.get("x-goog-api-key")
                Handler.body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                Handler.bodies = getattr(Handler, "bodies", []) + [Handler.body]
                if "generationConfig" in Handler.body and "old" in self.path:
                    return self.reply(400, {"error": {"code": 400, "message": "Thinking level is not supported for this model.",
                                                      "status": "INVALID_ARGUMENT"}})
                if Handler.key == "tired":
                    return self.reply(429, {"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for part in ({"text": "pensée", "thought": True}, {"text": "La "}, {"text": "réponse."}):
                    chunk = {"candidates": [{"content": {"role": "model", "parts": [part]}}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\r\n\r\n".encode())

        self.handler = Handler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.saved = providers.GEMINI_API
        providers.GEMINI_API = f"http://127.0.0.1:{self.server.server_port}/v1beta"

    def tearDown(self):
        providers.GEMINI_API = self.saved
        self.server.shutdown()
        self.server.server_close()

    def test_stream(self):
        chunks = asyncio.run(collect(providers.stream_gemini("SYSTEM", "PROMPT", "gemini-flash-latest", "good")))
        self.assertEqual("".join(chunks), "La réponse.")  # raisonnement non affiché
        self.assertEqual(self.handler.key, "good")         # clé dans l'en-tête, pas dans l'adresse
        self.assertNotIn("good", self.handler.path_seen)
        self.assertIn("/models/gemini-flash-latest:streamGenerateContent", self.handler.path_seen)
        self.assertEqual(self.handler.body["systemInstruction"]["parts"][0]["text"], "SYSTEM")
        self.assertNotIn("tools", self.handler.body)       # aucun outil

    def test_blocked_answer(self):
        with self.assertRaises(providers.ProviderError) as caught:  # pas une réponse vide enregistrée comme terminée
            asyncio.run(collect(providers.stream_gemini("S", "P", "gemini-blocked", "good")))
        self.assertIn("SAFETY", str(caught.exception))

    def test_thinking_level(self):
        asyncio.run(collect(providers.stream_gemini("S", "P", "gemini-flash-latest", "good", "high")))
        self.assertEqual(self.handler.body["generationConfig"], {"thinkingConfig": {"thinkingLevel": "high"}})
        self.handler.bodies = []
        chunks = asyncio.run(collect(providers.stream_gemini("S", "P", "gemini-old", "good", "low")))
        self.assertEqual("".join(chunks), "La réponse.")  # niveau refusé : redemandé sans
        self.assertEqual(["generationConfig" in b for b in self.handler.bodies], [True, False])

    def test_errors(self):
        with self.assertRaises(providers.ProviderError) as caught:
            asyncio.run(collect(providers.stream_gemini("S", "P", "gemini-flash-latest", "tired")))
        self.assertEqual(caught.exception.code, "limit")
        with self.assertRaises(providers.ProviderError) as caught:
            asyncio.run(collect(providers.stream_gemini("S", "P", "gemini-flash-latest", "")))
        self.assertEqual(caught.exception.code, "missing")
        with self.assertRaises(providers.ProviderError):  # nom de modèle qui sortirait du chemin prévu
            asyncio.run(collect(providers.stream_gemini("S", "P", "../x", "good")))

    def test_status(self):
        self.assertEqual(providers.status("gemini", {})["available"], False)  # pas de clé
        bad = providers.status("gemini", {"geminiApiKey": "bad"})
        self.assertEqual((bad["available"], bad["loggedIn"]), (True, False))
        self.assertIn("API key not valid", bad["error"])
        good = providers.status("gemini", {"geminiApiKey": "good"})
        self.assertEqual((good["loggedIn"], good["models"], good["geminiModel"]),
                         (True, ["gemini-2.5-pro", "gemini-flash-latest"], "gemini-flash-latest"))
        self.assertEqual(providers.status("gemini", {"geminiApiKey": "good", "aiGeminiModel": "gemini-2.5-pro"})["geminiModel"],
                         "gemini-2.5-pro")


class Settings(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = self.tmp / "test.db", self.tmp
        db.init()
        self.client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def test_status_of_assistant_being_set_up(self):
        db.save_settings({"aiProvider": "claude"})
        with unittest.mock.patch.object(providers, "status", return_value={"available": True, "loggedIn": False}) as status:
            self.assertEqual(self.client.get("/api/ai/status", params={"provider": "gemini"}).json()["provider"], "gemini")
            self.assertEqual(status.call_args.args[0], "gemini")
        self.assertEqual(self.client.get("/api/ai/status", params={"provider": "skynet"}).status_code, 400)
        self.assertEqual(db.get_settings()["aiProvider"], "claude")  # le réglage de l'assistant utilisé n'a pas bougé

    def test_default_assistant_selected_at_launch(self):
        put = lambda values: self.client.put("/api/settings", json=values).status_code  # noqa: E731
        self.assertEqual(put({"aiDefault": "skynet"}), 400)
        self.assertEqual(put({"aiDefault": "", "aiProvider": "codex"}), 200)
        main.select_default_assistant()  # aucun assistant par défaut : le dernier choisi reste
        self.assertEqual(db.get_settings()["aiProvider"], "codex")
        self.assertEqual(put({"aiDefault": "gemini"}), 200)
        self.assertEqual(db.get_settings()["aiProvider"], "codex")  # le serveur n'y touche qu'au lancement
        main.select_default_assistant()
        self.assertEqual(db.get_settings()["aiProvider"], "gemini")

    def test_validation(self):
        put = lambda values: self.client.put("/api/settings", json=values).status_code  # noqa: E731
        self.assertEqual(put({"aiProvider": "codex"}), 200)
        self.assertEqual(put({"aiProvider": "skynet"}), 400)
        for width in ("narrow", "normal", "wide", "xwide", "full"):  # les cinq largeurs du lecteur (settings.js)
            self.assertEqual(put({"readingWidth": width}), 200, width)
        self.assertEqual(put({"readingWidth": "huge"}), 400)
        self.assertEqual(put({"aiLocalUrl": "http://localhost:1234/v1", "aiLocalModel": "qwen3:32b"}), 200)
        self.assertEqual(put({"aiLocalUrl": "http://192.168.1.5:11434/v1"}), 400)  # sur cet ordinateur seulement
        self.assertEqual(put({"aiLocalUrl": "https://api.openai.com/v1"}), 400)
        for url in ("http://127.0.0.1:abc/v1", "http://127.0.0.1:99999/v1", "http://127.0.0.1:11434/v1?x=1", "http://127.0.0.1/v1\n"):
            self.assertEqual(put({"aiLocalUrl": url}), 400, url)
        self.assertEqual(put({"aiProvider": ["codex"]}), 400)  # pas une erreur 500
        self.assertEqual(put({"geminiApiKey": "AIzaSyExample_123", "aiGeminiModel": "gemini-2.5-flash"}), 200)
        self.assertEqual(put({"geminiApiKey": "a b"}), 400)
        self.assertEqual(put({"aiGeminiModel": "../../x"}), 400)
        self.assertEqual(put({"aiModel": "fable", "aiClaudeEffort": "xhigh", "aiCodexModel": "gpt-6-luna", "aiCodexEffort": "ultra",
                              "aiGeminiEffort": ""}), 200)
        self.assertEqual(put({"aiModel": "codex"}), 400)
        self.assertEqual(put({"aiCodexEffort": "turbo"}), 400)
        self.assertEqual(put({"profileOpen": ["author", "mine"]}), 200)  # rubriques dépliées du profil
        self.assertEqual(put({"profileOpen": ["author", "../x"]}), 400)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(sys.platform == "win32", "Node simulé : script Unix")
class RecentNode(unittest.TestCase):
    """codex et gemini : le Node d'un gestionnaire de versions passe devant un vieux Node de /usr/local/bin."""

    def test_fnm_before_old_node(self):
        import pathlib, tempfile
        from unittest import mock
        from app import providers
        home = pathlib.Path(tempfile.mkdtemp())
        old, new = home / "old", home / ".local/share/fnm/aliases/default/bin"
        for folder, version in ((old, "v16.14.0"), (new, "v24.19.0")):
            folder.mkdir(parents=True)
            (folder / "node").write_text(f"#!/bin/sh\necho {version}\n")
            (folder / "node").chmod(0o755)
        with mock.patch.object(providers.Path, "home", return_value=home), mock.patch.object(providers, "_node_folder", None), \
             mock.patch.dict(providers.os.environ, {"PATH": str(old), "FNM_DIR": ""}):
            self.assertEqual(providers._recent_node(), str(new))
            self.assertTrue(providers._search_path().startswith(str(new)))
