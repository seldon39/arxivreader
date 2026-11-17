"""Vérification des services extérieurs dont dépend l'application : un appel réel et minimal à chacun, et la
forme de sa réponse. Les tests (tests/) simulent ces services ; cette vérification dit s'ils ont changé.

    python -m app.check            tableau des résultats (code de sortie 1 si un service a changé ou ne répond pas)
    python -m app.check --notify   en plus, une notification macOS en cas de problème (tâche hebdomadaire)

Elle signale aussi un changement de macOS ou de WebKit depuis la vérification précédente (rien à appeler : la
liste des points à revoir à la main est dans le message), et un changement des assistants IA : version du CLI de
Claude et de Codex, modèles proposés par l'API Gemini. Résultat gardé dans la bibliothèque (check.json) ; un clic
sur la notification ouvre ce rapport dans l'application (#/check, texte de `text`, à copier).
"""

import importlib.metadata
import json
import platform
import plistlib
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from . import alerts, db, feed, keystore, onboarding, providers, search, updates
from .arxiv import fetch_metadata, with_retry
from .config import LIBRARY, USER_AGENT
from .version import VERSION

REFERENCE = "0806.2370"  # article de référence (arXiv, Semantic Scholar) : stable depuis 2008
AUTHOR_FEED = "ginsparg_p_1"  # flux d'auteur pris en exemple par l'aide d'arXiv (identifiants d'auteur)
S2 = "https://api.semanticscholar.org"
# WebKit de macOS : la copie des mises à jour de sécurité rapides (Cryptex) d'abord, sinon celle du système
WEBKIT_PLISTS = (Path("/System/Cryptexes/OS/System/Library/Frameworks/WebKit.framework/Resources/Info.plist"),
                 Path("/System/Library/Frameworks/WebKit.framework/Resources/Info.plist"))
REPORT = LIBRARY / "check.json"
# à revoir à la main après un changement de WebKit (résume la liste de MAINTENANCE.md, section 7)
WEBKIT_POINTS = ("glisser un article d'une fenêtre à l'autre, sur un onglet et sur « Bibliothèque » ou « Papiers liés »",
                 "couleurs du graphe des papiers liés (color-mix)", "réglages et graphe agrandi (fenêtre de dialogue, inert)",
                 "lecteur PDF", "formules (MathJax)", "export des notes (téléchargement)",
                 "défilement d'un long article (sections masquées, mémoire)")


# Pas de réseau (Mac qui sort de veille) : aucun service joint, chaque échec une erreur de connexion. Rien n'a changé ;
# la tâche hebdomadaire réessaie (RETRIES fois, toutes les RETRY_WAIT secondes), puis le dit sans notification
NETWORK_ERRORS = ("ConnectError", "ConnectTimeout")
CLAUDE, CODEX, GEMINI, LOCAL_MODEL = "Assistant IA : Claude", "Assistant IA : Codex", "Assistant IA : Gemini", "Assistant IA : modèle local"
LOCAL = {CLAUDE, CODEX, LOCAL_MODEL}  # vérifiés sans réseau (modèle local : sur cet ordinateur)
RETRIES, RETRY_WAIT = 3, 120
MARKS = {"ok": "✓", "skipped": "–", "changed": "✗", "failed": "✗", "offline": "·"}


# à revoir à la main quand un assistant change (résume MAINTENANCE.md, tableau des services)
ASSISTANT_POINTS = {
    "claude": "Claude : options du SDK, liste des modèles, connexion",
    "codex": "Codex : options du CLI (-m, model_reasoning_effort, --image=), liste des modèles (~/.codex/models_cache.json), "
             "connexion ; une question avec une zone du PDF",
    "gemini": "Gemini : modèle choisi encore proposé (onglet IA), niveau de réflexion (thinkingLevel)",
}


class Skip(Exception):
    """Service non vérifié (pas de clé, quota atteint, assistant pas lancé…) : ni bon ni mauvais. `assistant` : la
    version vue malgré tout (CLI installé mais pas connecté)."""

    def __init__(self, message="", assistant=None):
        super().__init__(message)
        self.assistant = assistant


class Changed(Exception):
    """Le service répond, mais pas comme l'application l'attend : à corriger dans le code."""


def expect(condition, message):
    if not condition:
        raise Changed(message)


def _client(headers=None):
    return httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT, **(headers or {})})


def arxiv_export():
    meta = fetch_metadata(REFERENCE)
    expect("Toeplitz" in meta.title and meta.authors and meta.published, "fiche incomplète")
    return meta.title


def arxiv_html():
    """Page HTML (LaTeXML) d'un article de la bibliothèque qui en a une, sinon d'un article récent connu."""
    paper = next((p for p in db.list_papers() if p.get("source") == "arxiv-html"), None) if db.DB_PATH.exists() else None
    arxiv_id, version = (paper["arxiv_id"], paper["version"]) if paper else ("2401.00027", 1)
    with _client() as client:
        response = client.get(f"https://arxiv.org/html/{arxiv_id}v{version}")
    response.raise_for_status()
    missing = [c for c in ("ltx_document", "ltx_title", "ltx_para") if c not in response.text]
    expect(not missing, f"classes LaTeXML absentes : {', '.join(missing)} ({arxiv_id})")
    return f"{arxiv_id}v{version}"


def arxiv_rss():
    with _client() as client:
        response = client.get(feed.RSS_URL.format(categories="math.DG"))
    response.raise_for_status()
    expect("<channel>" in response.text, "flux RSS inattendu")
    try:
        items = feed.parse_rss(response.text)
    except ET.ParseError:
        raise Changed("flux RSS illisible")
    return f"{len(items)} annonces (aucune le week-end)"


def arxiv_oai():
    with _client() as client:
        response = client.get(feed.OAI_URL, params={"verb": "Identify"})
    response.raise_for_status()
    expect("<repositoryName>" in response.text, "réponse OAI-PMH inattendue")
    return "Identify"


def arxiv_author():
    """Flux des articles d'un auteur (assistant de premier lancement) : format Atom, nom dans le titre."""
    with _client() as client:
        response = client.get(f"https://arxiv.org/a/{AUTHOR_FEED}.atom2")
    response.raise_for_status()
    try:
        items, _ = search.parse(response.text)
    except ET.ParseError:
        raise Changed("flux d'auteur illisible")
    expect(items and all(i["arxiv_id"] and i["authors"] for i in items), "articles du flux d'auteur incomplets")
    expect(onboarding.FEED_OWNER.search(response.text), "nom de l'auteur absent du titre du flux")
    return f"{len(items)} articles"


def semantic_scholar():
    key = db.get_settings().get("s2ApiKey") if db.DB_PATH.exists() else None
    with _client({"x-api-key": key} if key else None) as client:
        paper = with_retry(lambda: client.get(f"{S2}/graph/v1/paper/arXiv:{REFERENCE}", params={"fields": "title,externalIds,citationCount,references.paperId"}),
                           retry_on=(429,), delays=(3, 6, 12))
        if paper.status_code == 429:
            raise Skip("quota partagé de Semantic Scholar atteint : non vérifié (une clé l'évite)")
        paper.raise_for_status()
        data = paper.json()
        expect(isinstance(data, dict) and data.get("title") and data.get("references")
               and (data.get("externalIds") or {}).get("ArXiv") == REFERENCE, "fiche incomplète")
        time.sleep(1.2)
        # lot : références des candidats (papiers liés) et leurs auteurs (premier lancement)
        batch = client.post(f"{S2}/graph/v1/paper/batch", params={"fields": "paperId,references.paperId,references.authors"},
                            json={"ids": [f"arXiv:{REFERENCE}"]})
        if batch.status_code != 429:
            batch.raise_for_status()
            rows = batch.json()
            expect(isinstance(rows, list) and rows and isinstance(rows[0], dict) and rows[0].get("references"), "lot inattendu")
            expect(any(r.get("authors") for r in rows[0]["references"] if isinstance(r, dict)), "auteurs des références absents du lot")
        time.sleep(1.2)
        recommended = client.get(f"{S2}/recommendations/v1/papers/forpaper/arXiv:{REFERENCE}", params={"limit": 1, "fields": "title"})
        if recommended.status_code != 429:
            recommended.raise_for_status()
            expect(isinstance(recommended.json(), dict) and "recommendedPapers" in recommended.json(), "recommandations inattendues")
    return f"{len(data['references'])} références, {data.get('citationCount', 0)} citations" + (" (avec clé)" if key else "")


def zotero_api():
    key = db.get_settings().get("zoteroApiKey") if db.DB_PATH.exists() else None
    if not key:
        raise Skip("pas de clé Zotero")
    from .zotero import Client
    client = Client(key)
    try:
        who = client.connect()
        collections, version = client.listing("/collections")
        expect(version, "pas de Last-Modified-Version")
    finally:
        client.close()
    return f"{who['username'] or who['userID']}, {len(collections)} collections"


def _settings():
    if not db.DB_PATH.exists():
        raise Skip("pas de bibliothèque")
    return db.get_settings()


def _chosen(settings):
    from . import ai
    return providers.provider_of(ai.current_model(settings))


def cli_version(cli, env):
    """Version d'un CLI (« 2.1.321 (Claude Code) », « codex-cli 0.162.1 » : le numéro). En échec : le CLI ne
    répond pas (✗, sans version retenue)."""
    result = subprocess.run([cli, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=30, env=env)
    if result.returncode != 0:  # message d'erreur (Node trop ancien…) : pas une version, la référence ne bouge pas
        raise RuntimeError(f"--version en échec (code {result.returncode}) : {(result.stderr or result.stdout or '').strip()[:150]}")
    found = re.search(r"\d+(?:\.\d+)+[\w.+-]*", result.stdout or result.stderr or "")
    expect(found, f"--version illisible : {(result.stdout or result.stderr or '').strip()[:100]!r}")
    return found.group(0)


def _assistant_result(provider, name, label, version, info, chosen, followed=True):
    """Détail d'un assistant (installé) : connecté ou non ; sa version retenue dans les deux cas."""
    seen = {"id": provider, "name": name, "version": version, "followed": followed}
    mark = ", assistant choisi" if chosen else ""
    if info.get("loggedIn"):
        model = info.get("chosen") or info.get("model")
        return f"{label}, connecté{f' ({model})' if model else ''}{mark}", seen
    raise Skip(f"{label}, {'connexion non vérifiée' if info.get('inconclusive') else 'non connecté'}{mark}", seen)


def claude_assistant():
    """CLI de Claude (celui du SDK, qui ne change qu'avec arXivReader ; sinon celui du PATH) : version et connexion."""
    from . import ai
    settings = _settings()
    cli = ai.cli_path()
    if not cli:
        raise Skip("pas installé")
    version = cli_version(cli, ai._environment())
    bundled = "_bundled" in Path(cli).parts
    if bundled:
        try:
            label = f"CLI {version} (intégré au SDK {importlib.metadata.version('claude-agent-sdk')})"
        except importlib.metadata.PackageNotFoundError:
            label = f"CLI {version} (intégré au SDK)"
    else:
        label = f"CLI {version} ({cli})"
    info = ai.status(settings, "claude")
    return _assistant_result("claude", "Claude", label, version, info, _chosen(settings) == "claude", followed=not bundled)


def codex_assistant():
    """CLI de Codex installé par l'utilisateur (il se met à jour sans l'application) : version et connexion."""
    settings = _settings()
    cli = providers.find("codex")
    if not cli:
        raise Skip("pas installé")
    version = cli_version(cli, providers._environment())
    info = providers.status("codex", settings)
    return _assistant_result("codex", "Codex", f"CLI {version}", version, info, _chosen(settings) == "codex")


def gemini_assistant():
    """API Gemini (avec la clé de l'utilisateur) : modèles proposés. Clé refusée, quota : non vérifié."""
    settings = _settings()
    key = settings.get("geminiApiKey") or ""
    if not key:
        raise Skip("pas de clé")
    try:
        models = providers.gemini_models(key)
    except providers.ProviderError as error:
        if error.code in ("login", "limit"):
            raise Skip("clé refusée" if error.code == "login" else "quota atteint") from error
        raise
    expect(models, "aucun modèle Gemini proposé")
    chosen = providers.gemini_model(settings, models)
    mark = ", assistant choisi" if _chosen(settings) == "gemini" else ""
    return (f"{len(models)} modèles ({chosen}){mark}",
            {"id": "gemini", "name": "Gemini", "version": models, "followed": True})


def local_assistant():
    """Modèle local : seulement s'il est l'assistant choisi (serveur lancé à la demande)."""
    from . import ai
    settings = _settings()
    if _chosen(settings) != "local":
        raise Skip("pas choisi")
    info = ai.status(settings, "local")
    if not info.get("available"):
        raise Skip("introuvable ou pas lancé")
    if info.get("loggedIn") is False:
        raise Skip("aucun modèle")
    return f"{info.get('model') or 'modèle par défaut'}, assistant choisi"


def github_updates():
    latest = updates.fetch()
    if latest is None:
        raise Skip("aucune version publiée")
    expect(latest.get("version"), "dernière version illisible")
    return f"dernière version {latest['version']} (installée : {VERSION})"


CHECKS = [("arXiv : fiches (API)", arxiv_export), ("arXiv : pages HTML (LaTeXML)", arxiv_html), ("arXiv : annonces (RSS)", arxiv_rss),
          ("arXiv : jours passés (OAI-PMH)", arxiv_oai), ("arXiv : flux d'auteur", arxiv_author), ("Semantic Scholar", semantic_scholar),
          ("Zotero", zotero_api),
          (CLAUDE, claude_assistant), (CODEX, codex_assistant), (GEMINI, gemini_assistant), (LOCAL_MODEL, local_assistant),
          ("Mises à jour (GitHub)", github_updates)]


def system():
    """macOS et WebKit installés (ceux de l'application)."""
    webkit = "?"
    for path in WEBKIT_PLISTS:
        try:
            webkit = plistlib.loads(path.read_bytes()).get("CFBundleVersion", "?")
            break
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
    return {"macos": platform.mac_ver()[0] or "?", "webkit": webkit}


def offline(results):
    """Aucun service du réseau joint et tous ses échecs dus à la connexion : pas de réseau."""
    network = [r for r in results if r["name"] not in LOCAL]
    failed = [r for r in network if r["status"] == "failed"]
    return (bool(failed) and not any(r["status"] in ("ok", "changed") for r in network)
            and all(r["detail"].startswith(NETWORK_ERRORS) for r in failed))


def run(baseline=True, retries=0):
    """Tous les services ; `baseline` : le macOS et le WebKit vus deviennent la référence des prochaines fois (la
    tâche hebdomadaire ; une vérification lancée à la main ne la déplace pas, pour ne pas taire un changement).
    `retries` : sans réseau, nouveaux essais (après RETRY_WAIT secondes chacun)."""
    results = _services()
    while retries > 0 and offline(results):
        retries -= 1
        time.sleep(RETRY_WAIT)
        results = _services()
    unreachable = offline(results)
    if unreachable:  # pas de réseau : pas un changement des services
        for r in results:
            if r["status"] == "failed":
                r["status"] = "offline"
    report = _with_system(results, baseline)
    report["offline"] = unreachable
    try:
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return report


def _services():
    results = []
    for name, check in CHECKS:
        started = time.time()
        seen = None  # assistant IA : sa version (comparée d'une fois à l'autre)
        try:
            detail, status = check(), "ok"
            if isinstance(detail, tuple):
                detail, seen = detail
        except Skip as error:
            status, detail, seen = "skipped", str(error), error.assistant
        except Changed as error:
            status, detail = "changed", str(error) or "réponse inattendue"
        except Exception as error:
            status, detail = "failed", f"{type(error).__name__}: {error}"[:300]
        result = {"name": name, "status": status, "detail": detail or "", "seconds": round(time.time() - started, 1)}
        if seen:
            result["assistant"] = seen
        results.append(result)
    return results


def _with_system(results, baseline):
    previous = {}
    try:
        previous = json.loads(REPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not isinstance(previous, dict):
        previous = {}
    now = system()
    reference = previous.get("baseline") or previous.get("system")
    moved = bool(reference) and reference != now
    # assistants : comparés à ceux de la référence ; un assistant pas vu cette fois (pas de réseau, désinstallé) y reste
    seen = {r["assistant"]["id"]: r["assistant"] for r in results if isinstance(r.get("assistant"), dict)}
    known = previous.get("assistantsBaseline")
    known = {k: v for k, v in known.items() if isinstance(v, dict)} if isinstance(known, dict) else {}
    changes = [assistant_change(known[k], a) for k, a in seen.items() if k in known and known[k].get("version") != a["version"]]
    return {"checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "version": VERSION, "system": now,
            "baseline": now if baseline or not reference else reference,
            "systemChanged": moved, "previousSystem": reference,
            "assistantsBaseline": {**known, **seen} if baseline else {**seen, **known},
            "assistantChanges": changes, "results": results}


def assistant_change(before, after):
    """Changement d'un assistant : « Codex 0.162.1 → 0.163.0 », « Gemini : modèles retirés … » ; `notify` : à
    signaler (pas le CLI intégré au SDK, qui change avec arXivReader ; pas un modèle Gemini seulement ajouté)."""
    old, new, name = before.get("version"), after["version"], after["name"]
    if isinstance(old, list) and isinstance(new, list):
        added, removed = [m for m in new if m not in old], [m for m in old if m not in new]
        parts = [f"retirés : {', '.join(removed)}" if removed else "", f"ajoutés : {', '.join(added)}" if added else ""]
        text, notable = f"{name} : modèles " + " ; ".join(p for p in parts if p), bool(removed)
    else:
        text, notable = f"{name} {old} → {new}", True
    if not after.get("followed", True):
        text += " (CLI intégré au SDK : mise à jour d'arXivReader)"
    return {"id": after["id"], "text": text, "notify": notable and after.get("followed", True) and before.get("followed", True)}


def problems(report):
    return [r for r in report["results"] if r["status"] in ("changed", "failed")]


def system_change(report):
    """« macOS 27.2 → 27.2.1, WebKit inchangé (…) » : ce qui a bougé depuis la référence."""
    now, before = report.get("system") or {}, report.get("previousSystem") or {}
    parts = []
    for key, name in (("macos", "macOS"), ("webkit", "WebKit")):
        old, new = before.get(key, "?"), now.get(key, "?")
        parts.append(f"{name} {old} → {new}" if old != new else f"{name} inchangé ({new})")
    return ", ".join(parts)


def text(report):
    """Rapport en texte (terminal, page #/check de l'application, à copier tel quel)."""
    system_now = report.get("system") or {}
    lines = [f"arXivReader {report.get('version', '?')}, vérification du {report.get('checkedAt', '?')}"]
    if report.get("offline"):
        lines.append("Pas de réseau pendant la vérification : services non vérifiés (rien n'indique un changement).")
    for r in report.get("results") or []:
        if isinstance(r, dict):
            lines.append(f"{MARKS.get(r.get('status'), '?')} {str(r.get('name', '?')):<34} {r.get('detail', '')}")
    lines.append(f"  macOS {system_now.get('macos', '?')}, WebKit {system_now.get('webkit', '?')}"
                 + ("  (changé depuis la dernière vérification)" if report.get("systemChanged") else ""))
    if report.get("systemChanged"):
        lines += [f"  {system_change(report)}", "À revoir : " + ", ".join(WEBKIT_POINTS) + "."]
    changes = [c for c in report.get("assistantChanges") or [] if isinstance(c, dict)]
    if changes:
        lines.append("  Assistants IA changés depuis la dernière vérification : " + " ; ".join(str(c.get("text")) for c in changes))
        points = assistant_points(changes)
        if points:
            lines.append(f"À revoir : {points}.")
    return "\n".join(lines)


def assistant_points(changes):
    return " ; ".join(dict.fromkeys(ASSISTANT_POINTS[c["id"]] for c in changes if c.get("id") in ASSISTANT_POINTS))


def main(argv):
    # lancée à la main ou par la tâche hebdomadaire, toujours sur la vraie bibliothèque : ses clés d'API sont dans le
    # trousseau (rien à changer dans une tâche installée par une version précédente)
    keystore.ENABLED = sys.platform in ("darwin", "win32")
    notify = "--notify" in argv
    report = run(baseline=notify, retries=RETRIES if notify else 0)
    print(text(report))
    bad = problems(report)
    # un clic ouvre le rapport entier dans l'application, à copier (la notification n'en montre que le début)
    if notify and bad:
        body = " ; ".join(f"{r['name']} : {r['detail']}" for r in bad)
        alerts.display("arXivReader", "Un service a changé ou ne répond pas", body, route="#/check")
    if notify and report["systemChanged"]:
        webkit = (report.get("previousSystem") or {}).get("webkit") != report["system"].get("webkit")
        alerts.display("arXivReader", "WebKit a changé" if webkit else "macOS a changé (WebKit inchangé)",
                       f"{system_change(report)}. À revoir : " + ", ".join(WEBKIT_POINTS) + ".", route="#/check")
    notable = [c for c in report["assistantChanges"] if c["notify"]]
    if notify and notable:
        alerts.display("arXivReader", "Un assistant IA a changé",
                       " ; ".join(c["text"] for c in notable) + f". À revoir : {assistant_points(notable)}.", route="#/check")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
