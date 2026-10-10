"""Autres assistants que Claude : Codex (OpenAI), Gemini (Google), modèle local (Ollama, LM Studio…).

Comme pour Claude, chacun ne reçoit que le texte de l'article et la demande, et répond en texte :
- Codex et Gemini : leurs outils en ligne de commande, avec le compte de l'utilisateur (ChatGPT,
  Google), sans clé d'API. Ce sont des agents : leurs outils (commandes, fichiers, web, MCP) sont
  désactivés par leurs réglages, dans un dossier de travail vide, sans session enregistrée.
- Modèle local : serveur compatible OpenAI sur cet ordinateur (127.0.0.1) ; rien ne sort de la machine.
"""

import asyncio
import glob
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import LIBRARY

PROVIDERS = ("claude", "codex", "gemini", "local")
NAMES = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}
LOCAL_URL = "http://127.0.0.1:11434/v1"  # Ollama ; LM Studio : http://127.0.0.1:1234/v1
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOCAL_LIMIT = 120_000  # caractères d'article (~33 000 jetons) : contexte des modèles locaux plus court


class ProviderError(Exception):
    def __init__(self, message, code="other"):
        super().__init__(message)
        self.code = code


def provider_of(model):
    """Assistant d'un échange, d'après son modèle enregistré (opus, sonnet, codex, gemini, local:<nom>)."""
    if model in ("codex", "gemini"):
        return model
    if (model or "").startswith("local:"):
        return "local"
    return "claude"


def valid_local_url(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS and not parts.username and len(url) <= 200


# ---------- Programmes en ligne de commande ----------

def _search_path():
    """PATH du serveur, complété des dossiers usuels (lancé depuis le Finder, il est très court)."""
    home = Path.home()
    extra = ["/opt/homebrew/bin", "/usr/local/bin", str(home / ".local/bin"), str(home / ".npm-global/bin"),
             str(home / ".volta/bin"), str(home / ".bun/bin"), *sorted(glob.glob(str(home / ".nvm/versions/node/*/bin")))]
    return os.pathsep.join([os.environ.get("PATH", ""), *extra])


def _environment():
    env = dict(os.environ)
    env["PATH"] = _search_path()  # codex et gemini sont des scripts Node : node doit être trouvé aussi
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        env.pop(key, None)  # le compte de l'utilisateur, pas une clé d'API qui traînerait dans l'environnement
    return env


def find(command):
    return shutil.which(command, path=_search_path())


def _workdir(name):
    folder = LIBRARY / "ai" / name  # vide : rien à lire, même si un outil restait actif
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def status(provider, settings):
    if provider == "codex":
        cli = find("codex")
        if not cli:
            return {"available": False, "loggedIn": False}
        try:
            result = subprocess.run([cli, "login", "status"], capture_output=True, text=True, timeout=20, env=_environment())
            output = (result.stdout + result.stderr).lower()
            logged = result.returncode == 0 and "not logged in" not in output
        except (OSError, subprocess.TimeoutExpired):
            logged = False
        return {"available": True, "loggedIn": logged}
    if provider == "gemini":
        if not find("gemini"):
            return {"available": False, "loggedIn": False}
        return {"available": True, "loggedIn": (Path.home() / ".gemini" / "oauth_creds.json").exists()}
    if provider == "local":
        url = settings.get("aiLocalUrl") or LOCAL_URL
        try:
            with httpx.Client(timeout=3, trust_env=False) as client:
                response = client.get(f"{url.rstrip('/')}/models")
                response.raise_for_status()
                models = sorted(m["id"] for m in response.json().get("data", []) if isinstance(m.get("id"), str))
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"available": False, "loggedIn": False, "models": []}
        return {"available": True, "loggedIn": bool(models), "models": models}
    raise ValueError(provider)


async def _run(argv, stdin_text, cwd, parse, env=None):
    """Lance un programme, lui passe la demande sur l'entrée standard, lit ses événements JSON (une ligne
    chacun) et renvoie au fil de l'eau le texte que `parse` en extrait. Interrompu : le programme est arrêté."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env=env or _environment(), limit=32 * 1024 * 1024,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as error:
        raise ProviderError(str(error), "missing")
    errors = asyncio.create_task(process.stderr.read())
    state = {"error": None, "text": False}
    try:
        process.stdin.write(stdin_text.encode())
        await process.stdin.drain()
        process.stdin.close()
        async for raw in process.stdout:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if isinstance(event, dict):
                for text in parse(event, state):
                    state["text"] = True
                    yield text
        await process.wait()
        stderr = (await errors).decode(errors="replace").strip()
        if process.returncode and not state["text"]:
            message = state["error"] or (stderr.splitlines()[-1] if stderr else f"exit {process.returncode}")
            raise ProviderError(message, _code(f"{message}\n{stderr}"))
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        errors.cancel()


def _code(message):
    lowered = message.lower()
    if any(word in lowered for word in ("not logged in", "login", "log in", "authenticat", "unauthorized", "401", "credentials")):
        return "login"
    if "limit" in lowered or "quota" in lowered or "429" in lowered:
        return "limit"
    return "other"


# ---------- Codex (OpenAI) ----------

def _codex_events(event, state):
    kind = event.get("type")
    if kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
        text = event["item"].get("text") or ""
        if text:
            yield ("\n\n" if state["text"] else "") + text
    elif kind == "turn.failed":
        raise ProviderError((event.get("error") or {}).get("message") or "Codex error",
                            _code((event.get("error") or {}).get("message") or ""))
    elif kind == "error":
        state["error"] = event.get("message")  # parfois passager (reconnexion) : signalé seulement en cas d'échec


async def stream_codex(system, prompt):
    cli = find("codex")
    if not cli:
        raise ProviderError("codex is not installed", "missing")
    workdir = _workdir("codex")
    instructions = workdir.parent / "codex-instructions.md"  # hors du dossier de travail
    instructions.write_text(system, encoding="utf-8")
    argv = [cli, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
            "--sandbox", "read-only", "-C", str(workdir),
            "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",  # aucune commande
            "-c", 'web_search="disabled"',                                         # pas de recherche web
            "-c", 'approval_policy="never"',
            "-c", "project_doc_max_bytes=0",                                       # pas d'AGENTS.md
            "-c", f"model_instructions_file={json.dumps(str(instructions))}",      # consignes d'arXivReader
            "-"]
    async for text in _run(argv, prompt, workdir, _codex_events):
        yield text


# ---------- Gemini (Google) ----------

GEMINI_TOOLS = ["run_shell_command", "read_file", "read_many_files", "write_file", "replace", "edit", "glob",
                "list_directory", "search_file_content", "grep_search", "web_fetch", "google_web_search",
                "save_memory", "write_todos"]


def _gemini_events(event, state):
    kind = event.get("type")
    if kind == "message" and event.get("role") == "assistant":
        text = event.get("content") or ""
        if isinstance(text, str) and text:
            yield text
    elif kind == "result" and event.get("status") not in (None, "success"):
        error = event.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise ProviderError(message or "Gemini error", _code(message or ""))
    elif kind == "error":
        state["error"] = event.get("message")
    elif "response" in event and isinstance(event["response"], str):  # ancienne sortie --output-format json
        yield event["response"]


async def stream_gemini(system, prompt):
    cli = find("gemini")
    if not cli:
        raise ProviderError("gemini is not installed", "missing")
    workdir = _workdir("gemini")
    folder = workdir.parent
    (folder / "gemini-system.md").write_text(system, encoding="utf-8")
    # Réglages « système » (prioritaires sur ceux de l'utilisateur) : aucun outil, aucun serveur MCP,
    # pas de fichier de mémoire GEMINI.md
    settings = {"tools": {"core": ["__none__"], "exclude": GEMINI_TOOLS}, "mcpServers": {},
                "context": {"fileName": "__arxivreader_none__.md"}}
    (folder / "gemini-settings.json").write_text(json.dumps(settings), encoding="utf-8")
    env = _environment()
    env["GEMINI_SYSTEM_MD"] = str(folder / "gemini-system.md")  # remplace les consignes d'agent de Gemini
    env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(folder / "gemini-settings.json")
    argv = [cli, "--output-format", "stream-json", "--extensions", "none", "--allowed-mcp-server-names", "__none__"]
    async for text in _run(argv, prompt, workdir, _gemini_events, env=env):
        yield text


# ---------- Modèle local (serveur compatible OpenAI : Ollama, LM Studio, llama.cpp…) ----------

async def stream_local(system, prompt, model, url):
    if not valid_local_url(url):
        raise ProviderError("The local server must be on this computer (http://127.0.0.1…)")
    if not model:
        raise ProviderError("No local model chosen (Settings → AI)", "login")
    body = {"model": model, "stream": True,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    thinking = None  # modèles « à raisonnement » : le bloc <think>…</think> du début n'est pas affiché
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=5), trust_env=False) as client:
            async with client.stream("POST", f"{url.rstrip('/')}/chat/completions", json=body) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode(errors="replace")[:500]
                    raise ProviderError(f"{response.status_code}: {detail}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if chunk.get("error"):
                        raise ProviderError(str(chunk["error"].get("message") if isinstance(chunk["error"], dict) else chunk["error"]))
                    text = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                    if thinking is None and text.strip():
                        thinking = "" if text.lstrip().startswith("<think>") else False
                    if thinking is not False and thinking is not None:
                        thinking += text
                        if "</think>" not in thinking:
                            continue
                        text, thinking = thinking.split("</think>", 1)[1].lstrip(), False
                    if text:
                        yield text
    except httpx.ConnectError:
        raise ProviderError(f"No local server at {url}", "offline")
    except httpx.HTTPError as error:
        raise ProviderError(f"{type(error).__name__}: {error}")
