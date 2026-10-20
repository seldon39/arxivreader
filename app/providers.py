"""Autres assistants que Claude : Codex (OpenAI), Gemini (Google), modèle local (Ollama, LM Studio…).

Comme pour Claude, chacun ne reçoit que le texte de l'article et la demande, et répond en texte :
- Codex : son outil en ligne de commande, avec le compte ChatGPT de l'utilisateur, sans clé d'API. C'est
  un agent : ses outils (commandes, fichiers, web, MCP) sont désactivés par ses réglages, dans un dossier
  de travail vide, sans session enregistrée.
- Gemini : l'API Gemini de Google, avec la clé de l'utilisateur (Google AI Studio) ; une simple requête,
  sans aucun outil. (Gemini CLI n'accepte plus les comptes Google gratuits depuis juin 2026.)
- Modèle local : serveur compatible OpenAI sur cet ordinateur (127.0.0.1) ; rien ne sort de la machine.
"""

import asyncio
import base64
import contextlib
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx

from .config import LIBRARY

PROVIDERS = ("claude", "codex", "gemini", "local")
WINDOWS = sys.platform == "win32"
NAMES = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}
LOCAL_URL = "http://127.0.0.1:11434/v1"  # Ollama ; LM Studio : http://127.0.0.1:1234/v1
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOCAL_LIMIT = 120_000  # caractères d'article (~33 000 jetons) : contexte des modèles locaux plus court


# Niveaux de réflexion (« effort ») ; "" : celui de l'assistant par défaut
EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
GEMINI_EFFORTS = ("low", "medium", "high")  # thinkingLevel de l'API Gemini
MODEL_NAME = re.compile(r"[a-z0-9][a-z0-9.\-]{0,99}")


def valid_model_name(name):
    return isinstance(name, str) and bool(MODEL_NAME.fullmatch(name))


def valid_effort(effort):
    return effort == "" or effort in EFFORTS


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
    if not isinstance(url, str) or len(url) > 200 or not url.isprintable() or " " in url:
        return False
    try:
        parts = urlsplit(url)
        parts.port  # port non numérique ou hors limites : ValueError
    except ValueError:
        return False
    return (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS and not parts.username
            and not parts.query and not parts.fragment)


# ---------- Programmes en ligne de commande ----------

NODE_MIN = 20  # codex et gemini demandent Node 20 ou plus


def _version_key(path):
    found = re.search(r"v?(\d+)\.(\d+)\.(\d+)", path)
    return tuple(int(n) for n in found.groups()) if found else (0, 0, 0)


def _node_major(folder):
    try:
        out = subprocess.run([str(Path(folder) / ("node.exe" if WINDOWS else "node")), "--version"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    found = re.match(r"v(\d+)", out.strip())
    return int(found.group(1)) if found else 0


_node_folder = None


def _recent_node():
    """Dossier d'un Node assez récent : celui des gestionnaires de versions (fnm, nvm, volta, mise, asdf)
    d'abord, car un vieux Node installé à part dans /usr/local/bin ferait échouer codex et gemini."""
    global _node_folder
    if _node_folder is not None:
        return _node_folder
    home = Path.home()
    fnm = [os.environ.get("FNM_DIR", ""), str(home / ".local/share/fnm"), str(home / "Library/Application Support/fnm"), str(home / ".fnm")]
    candidates = [*(f"{d}/aliases/default/bin" for d in fnm if d),
                  *sorted((f for d in fnm if d for f in glob.glob(f"{d}/node-versions/*/installation/bin")), key=_version_key, reverse=True),
                  *sorted(glob.glob(str(home / ".nvm/versions/node/*/bin")), key=_version_key, reverse=True),
                  str(home / ".volta/bin"), str(home / ".local/share/mise/shims"), str(home / ".asdf/shims"),
                  "/opt/homebrew/bin", "/usr/local/bin", *os.environ.get("PATH", "").split(os.pathsep)]
    node = "node.exe" if WINDOWS else "node"
    found = next((d for d in dict.fromkeys(candidates) if d and (Path(d) / node).exists() and _node_major(d) >= NODE_MIN), "")
    if found:  # pas trouvé : recherché de nouveau la prochaine fois (Node installé entre-temps)
        _node_folder = found
    return found


def _search_path():
    """PATH du serveur, complété des dossiers usuels (lancé depuis le Finder, il est très court) ; un Node
    assez récent passe en tête."""
    home = Path.home()
    extra = ["/opt/homebrew/bin", "/usr/local/bin", str(home / ".local/bin"), str(home / ".npm-global/bin"),
             str(home / ".volta/bin"), str(home / ".bun/bin"), *sorted(glob.glob(str(home / ".nvm/versions/node/*/bin")))]
    return os.pathsep.join(d for d in [_recent_node(), os.environ.get("PATH", ""), *extra] if d)


def _environment():
    env = dict(os.environ)
    env["PATH"] = _search_path()  # codex et gemini sont des scripts Node : node doit être trouvé aussi
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        env.pop(key, None)  # le compte de l'utilisateur, pas une clé d'API qui traînerait dans l'environnement
    return env


def find(command):
    return shutil.which(command, path=_search_path())


def _write(path, text):
    """Fichier de consignes ou de réglages partagé par les demandes : réécrit seulement s'il change, d'un
    coup (fichier temporaire puis remplacement), jamais lu à moitié écrit par une demande voisine."""
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        file.write(text)
    os.replace(temporary, path)


def _workdir(name):
    folder = LIBRARY / "ai" / name  # vide : rien à lire, même si un outil restait actif
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# Installation et connexion, dans une fenêtre du Terminal (ces commandes demandent parfois une saisie) :
# commandes fixées ici, jamais reçues de la page
SETUP = {
    ("codex", "login"): "codex login",
    ("codex", "missing"): "npm install -g @openai/codex && codex login",
    ("local", "login"): "ollama pull qwen3",
    ("local", "missing"): "OLLAMA_CONTEXT_LENGTH=32768 ollama serve",
}
WINDOWS_SETUP = {("local", "missing"): "set OLLAMA_CONTEXT_LENGTH=32768 && ollama serve"}  # syntaxe de cmd.exe


def _open_setup_windows(provider, step, claude_cli):
    """Comme open_setup, dans une invite de commandes (cmd.exe) qui reste ouverte à la fin."""
    if provider == "claude" and step in ("login", "missing"):
        if not claude_cli:
            raise OSError("claude-agent-sdk is missing")
        command = f'"{claude_cli}" auth login --claudeai && "{claude_cli}" auth status --text'
    elif (provider, step) in SETUP:
        command = WINDOWS_SETUP.get((provider, step), SETUP[provider, step])
        # npm, codex… sont des scripts .cmd : appelés sans « call », ils termineraient le script avant la suite
        command = " && ".join(part if part.startswith("set ") else f"call {part}" for part in command.split(" && "))
    else:
        raise ValueError(provider)
    folder = LIBRARY / "ai"
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / f"setup-{provider}.cmd"
    shown = "claude auth login" if provider == "claude" else command
    # fins de ligne \r\n : ajoutées par l'écriture en mode texte de Windows
    _write(script, "@echo off\nchcp 65001 >nul\n"
                   f'set "PATH={_search_path()}"\n'
                   # le compte de l'utilisateur, pas une clé d'API qui traînerait dans l'environnement
                   "set ANTHROPIC_API_KEY=\nset OPENAI_API_KEY=\nset CODEX_API_KEY=\n"
                   f"echo $ {shown.replace('&', '^&')}\n"  # ^& : affiché, pas exécuté
                   f"{command}\n"
                   "echo.\necho Terminé : revenez dans arXivReader et cliquez sur « Vérifier à nouveau ».\n")
    # console à part, et hors du « job » de la fenêtre : « ollama serve », « npm install » continuent si on la ferme
    subprocess.Popen(["cmd.exe", "/k", str(script)],
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10) | 0x01000000)  # CREATE_BREAKAWAY_FROM_JOB


def open_setup(provider, step, claude_cli=None):
    if WINDOWS:
        return _open_setup_windows(provider, step, claude_cli)
    if provider == "claude" and step in ("login", "missing"):
        if not claude_cli:
            raise OSError("claude-agent-sdk is missing")
        command = (f"env -u ANTHROPIC_API_KEY {shlex.quote(claude_cli)} auth login --claudeai && "
                   f"env -u ANTHROPIC_API_KEY {shlex.quote(claude_cli)} auth status --text")
    elif (provider, step) in SETUP:
        command = SETUP[provider, step]
    else:
        raise ValueError(provider)
    folder = LIBRARY / "ai"
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / f"setup-{provider}.command"
    shown = "claude auth login" if provider == "claude" else command
    # codex : sans Node assez récent, un message clair plutôt qu'une erreur de JavaScript
    node_check = ("" if provider != "codex" else
                  "major=$(node -p 'process.versions.node.split(\".\")[0]' 2>/dev/null || echo 0)\n"
                  f"if [ \"$major\" -lt {NODE_MIN} ]; then\n"
                  f"  echo \"Node.js {NODE_MIN} ou plus est nécessaire (trouvé : $(node --version 2>/dev/null || echo aucun)).\"\n"
                  "  echo \"Installez la version LTS depuis https://nodejs.org, puis recommencez.\"\n"
                  "  exit 1\nfi\n")
    _write(script, "#!/bin/bash\n"
                   f"export PATH={shlex.quote(_search_path())}\n"
                   "unset OPENAI_API_KEY CODEX_API_KEY\n"  # le compte de l'utilisateur, pas une clé de l'environnement
                   + node_check +
                   f"echo {shlex.quote('$ ' + shown)}\n"
                   f"{command}\n"
                   "echo\necho \"Terminé : revenez dans arXivReader et cliquez sur « Vérifier à nouveau ».\"\n")
    script.chmod(0o700)
    subprocess.run(["open", "-a", "Terminal", str(script)], check=True, timeout=20)


def _effort(settings, key, choices, chosen=None):
    """Niveau enregistré, s'il est proposé pour le modèle choisi ; sinon "" (celui de l'assistant)."""
    effort = settings.get(key) or ""
    levels = next((c["levels"] for c in choices if c["value"] == chosen), None) if chosen else None
    if levels is None:
        levels = sorted({level for c in choices for level in c["levels"]})
    return effort if effort in levels else ""


def status(provider, settings):
    if provider == "codex":
        cli = find("codex")
        if not cli:
            return {"available": False, "loggedIn": False}
        try:
            result = subprocess.run([cli, "login", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=20, env=_environment())
            output = (result.stdout + result.stderr).lower()
            logged = result.returncode == 0 and "not logged in" not in output
        except (OSError, subprocess.TimeoutExpired):
            logged = False
        choices, chosen, effort = codex_choice(settings)
        return {"available": True, "loggedIn": logged, "choices": choices, "chosen": chosen, "effort": effort}
    if provider == "gemini":
        key = settings.get("geminiApiKey") or ""
        if not key:
            return {"available": False, "loggedIn": False, "models": []}  # « missing » : pas de clé
        try:
            models = gemini_models(key)
        except ProviderError as error:
            return {"available": True, "loggedIn": False, "models": [], "error": str(error)}
        except (httpx.HTTPError, ValueError):
            return {"available": True, "loggedIn": False, "models": [], "error": "network"}
        choices = [{"value": m, "label": m, "levels": list(GEMINI_EFFORTS)} for m in models]
        return {"available": True, "loggedIn": bool(models), "models": models, "geminiModel": gemini_model(settings, models),
                "choices": choices, "chosen": gemini_model(settings, models), "effort": _effort(settings, "aiGeminiEffort", choices)}
    if provider == "local":
        url = settings.get("aiLocalUrl") or LOCAL_URL
        try:
            with httpx.Client(timeout=3, trust_env=False) as client:
                response = client.get(f"{url.rstrip('/')}/models")
                response.raise_for_status()
                models = sorted(m["id"] for m in response.json().get("data", []) if isinstance(m.get("id"), str))
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, KeyError, TypeError, AttributeError):
            return {"available": False, "loggedIn": False, "models": []}
        return {"available": True, "loggedIn": bool(models), "models": models}
    raise ValueError(provider)


async def _run(argv, stdin_text, cwd, parse, env=None):
    """Lance un programme, lui passe la demande sur l'entrée standard, lit ses événements JSON (une ligne
    chacun) et renvoie au fil de l'eau le texte que `parse` en extrait. Interrompu : le programme est arrêté."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env=env or _environment(), limit=32 * 1024 * 1024,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)  # groupe à part : arrêté avec ses éventuels sous-processus
    except OSError as error:
        raise ProviderError(str(error), "missing")
    errors = asyncio.create_task(process.stderr.read())
    state = {"error": None, "text": False}
    try:
        try:
            process.stdin.write(stdin_text.encode())
            await process.stdin.drain()
            process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # programme arrêté avant d'avoir tout lu (option inconnue, pas connecté…) : son message suit
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
            raise ProviderError(message, _code(message))
    finally:
        errors.cancel()
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if WINDOWS:  # pas de groupe de processus : le programme et ce qu'il a lancé (codex.cmd → node)
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            with anyio.CancelScope(shield=True):  # page fermée : l'attente n'est pas interrompue à son tour
                await process.wait()


def _code(message):
    """Nature d'une erreur, d'après le seul message retenu (pas toute la sortie d'erreur, trop bavarde)."""
    lowered = message.lower()
    if re.search(r"\b429\b|quota|usage limit|rate limit|too many requests", lowered):
        return "limit"
    if re.search(r"not logged in|\blog ?in\b|authenticat|unauthori[sz]ed|\b401\b", lowered):
        return "login"
    return "other"


# ---------- Codex (OpenAI) ----------

def codex_models():
    """Modèles que Codex propose au compte connecté (sa liste en cache, tenue à jour par Codex lui-même),
    du plus recommandé au moins recommandé : [{value, label, description, levels}]."""
    try:
        cache = json.loads((Path.home() / ".codex" / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    listed = cache.get("models") if isinstance(cache, dict) else None
    models = []
    for model in sorted((m for m in (listed if isinstance(listed, list) else []) if isinstance(m, dict) and m.get("visibility") == "list"),
                        key=lambda m: m.get("priority") if isinstance(m.get("priority"), (int, float)) else 999):
        slug = model.get("slug")
        if not valid_model_name(slug):
            continue
        raw = model.get("supported_reasoning_levels")
        levels = [r.get("effort") if isinstance(r, dict) else r for r in (raw if isinstance(raw, list) else [])]
        models.append({"value": slug, "label": str(model.get("display_name") or slug)[:60],
                       "description": str(model.get("description") or "")[:200],
                       "levels": [level for level in levels if level in EFFORTS]})
    return models


def codex_choice(settings):
    """Modèles proposés, modèle choisi (sinon le plus recommandé) et niveau permis pour lui : ce que la page
    affiche et ce que reçoit Codex, calculés de la même façon."""
    choices = codex_models()
    chosen = settings.get("aiCodexModel") if any(c["value"] == settings.get("aiCodexModel") for c in choices) \
        else (choices[0]["value"] if choices else "")
    return choices, chosen, _effort(settings, "aiCodexEffort", choices, chosen)


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


async def stream_codex(system, prompt, model=None, effort=""):
    cli = find("codex")
    if not cli:
        raise ProviderError("codex is not installed", "missing")
    workdir = _workdir("codex")
    instructions = workdir.parent / "codex-instructions.md"  # hors du dossier de travail
    _write(instructions, system)
    argv = [cli, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
            "--sandbox", "read-only", "-C", str(workdir),
            "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",  # aucune commande
            "-c", 'web_search="disabled"',                                         # pas de recherche web
            "-c", 'approval_policy="never"',
            "-c", "project_doc_max_bytes=0",                                       # pas d'AGENTS.md
            "-c", f"model_instructions_file={json.dumps(str(instructions))}",      # consignes d'arXivReader
            *(["-m", model] if valid_model_name(model) else []),
            *(["-c", f"model_reasoning_effort={json.dumps(effort)}"] if effort in EFFORTS else []),
            "-"]
    env = await asyncio.to_thread(_environment)  # recherche de Node (programmes lancés) hors de la boucle
    async for text in _run(argv, prompt, workdir, _codex_events, env=env):
        yield text


# ---------- Gemini (Google), par l'API ----------

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_DEFAULT = ("gemini-flash-latest", "gemini-pro-latest")  # préférés, s'ils sont proposés à la clé


def valid_gemini_key(key):
    return isinstance(key, str) and len(key) <= 200 and key.isascii() and key.isprintable() and " " not in key


def valid_gemini_model(model):
    return isinstance(model, str) and bool(re.fullmatch(r"[a-z0-9][a-z0-9.\-]{0,99}", model))


def _gemini_error(status, body):
    """Message et nature d'une erreur de l'API Gemini (clé refusée, quota, autre)."""
    try:
        error = json.loads(body).get("error") or {}
    except (ValueError, AttributeError):
        error = {}
    message = error.get("message") if isinstance(error, dict) else None
    message = message or f"HTTP {status}"
    text = f"{message} {json.dumps(error)[:2000]}" if isinstance(error, dict) else message
    if status == 429 or "RESOURCE_EXHAUSTED" in text:
        return ProviderError(message, "limit")
    if status in (401, 403) or "API_KEY_INVALID" in text or "API key not valid" in text:
        return ProviderError(message, "login")
    return ProviderError(message)


def gemini_models(key):
    """Modèles Gemini que la clé peut utiliser pour répondre (generateContent)."""
    with httpx.Client(timeout=10) as client:
        response = client.get(f"{GEMINI_API}/models", params={"pageSize": 1000}, headers={"x-goog-api-key": key})
    if response.status_code != 200:
        raise _gemini_error(response.status_code, response.text)
    data = response.json()
    listed = data.get("models") if isinstance(data, dict) else None
    models = []
    for model in (listed if isinstance(listed, list) else []):
        if not isinstance(model, dict):
            continue
        name = str(model.get("name") or "").removeprefix("models/")
        if name.startswith("gemini") and "generateContent" in (model.get("supportedGenerationMethods") or []) \
                and valid_gemini_model(name):
            models.append(name)
    return sorted(set(models))


def gemini_model(settings, models):
    """Modèle choisi dans les réglages s'il est proposé, sinon le préféré, sinon le premier « flash »."""
    chosen = settings.get("aiGeminiModel")
    if chosen in models:
        return chosen
    return next((m for m in GEMINI_DEFAULT if m in models), None) or next((m for m in models if "flash" in m), None) \
        or (models[0] if models else GEMINI_DEFAULT[0])


async def stream_gemini(system, prompt, model, key, effort="", image=None):
    if not key:
        raise ProviderError("No Gemini API key (Settings → AI)", "missing")
    if not valid_gemini_model(model):
        raise ProviderError("Unknown Gemini model")
    # aucun outil déclaré : Gemini ne peut que répondre par du texte
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": ([{"inline_data": {"mime_type": "image/png",
                                                                       "data": base64.b64encode(image).decode()}}] if image else [])
                          + [{"text": prompt}]}]}
    if effort in GEMINI_EFFORTS:
        body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": effort}}
    received = False
    try:
        async for text in _gemini_request(model, key, body):
            received = True
            yield text
    except ProviderError as error:
        # modèle qui n'accepte pas ce niveau (anciens modèles, niveau propre à Flash) : redemandé sans
        if received or "generationConfig" not in body or "thinking" not in str(error).lower():
            raise
        del body["generationConfig"]
        async for text in _gemini_request(model, key, body):
            yield text


async def _gemini_request(model, key, body):
    produced, blocked = False, None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as client:
            async with client.stream("POST", f"{GEMINI_API}/models/{model}:streamGenerateContent", params={"alt": "sse"},
                                     headers={"x-goog-api-key": key}, json=body) as response:
                if response.status_code != 200:
                    raise _gemini_error(response.status_code, (await response.aread()).decode(errors="replace"))
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        chunk = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("error"):
                        raise _gemini_error(200, json.dumps(chunk))
                    feedback = chunk.get("promptFeedback")
                    if isinstance(feedback, dict) and feedback.get("blockReason"):
                        blocked = str(feedback["blockReason"])
                    candidates = chunk.get("candidates")
                    for candidate in (candidates if isinstance(candidates, list) else [])[:1]:
                        if not isinstance(candidate, dict):
                            continue
                        if candidate.get("finishReason") in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
                            blocked = str(candidate["finishReason"])
                        content = candidate.get("content")
                        parts = content.get("parts") if isinstance(content, dict) else None
                        for part in (parts if isinstance(parts, list) else []):
                            if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought"):
                                produced = True
                                yield part["text"]
                if not produced:
                    raise ProviderError(f"Gemini did not answer ({blocked})" if blocked else "Gemini gave an empty answer")
    except httpx.ConnectError:
        raise ProviderError("Google's Gemini API cannot be reached (network?)", "offline")
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise ProviderError(f"{type(error).__name__}: {error}")


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
                    if not isinstance(chunk, dict):
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
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise ProviderError(f"{type(error).__name__}: {error}")
