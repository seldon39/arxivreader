"""Vérification des services extérieurs dont dépend l'application : un appel réel et minimal à chacun, et la
forme de sa réponse. Les tests (tests/) simulent ces services ; cette vérification dit s'ils ont changé.

    python -m app.check            tableau des résultats (code de sortie 1 si un service a changé ou ne répond pas)
    python -m app.check --notify   en plus, une notification macOS en cas de problème (tâche hebdomadaire)

Elle signale aussi un changement de macOS ou de WebKit depuis la vérification précédente (rien à appeler : la
liste des points à revoir à la main est dans le message). Résultat gardé dans la bibliothèque (check.json) ; un clic
sur la notification ouvre ce rapport dans l'application (#/check, texte de `text`, à copier).
"""

import json
import platform
import plistlib
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from . import alerts, db, feed, keystore, onboarding, search, updates
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
LOCAL = {"Assistant IA"}  # vérifié sans réseau
RETRIES, RETRY_WAIT = 3, 120
MARKS = {"ok": "✓", "skipped": "–", "changed": "✗", "failed": "✗", "offline": "·"}


class Skip(Exception):
    """Service non vérifié (pas de clé, quota atteint, assistant pas lancé…) : ni bon ni mauvais."""


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


def ai_assistant():
    """Assistant choisi : installé et connecté. Pas lancé (modèle local), pas connecté, hors ligne : non vérifié (ce
    n'est pas un changement du service)."""
    if not db.DB_PATH.exists():
        raise Skip("pas de bibliothèque")
    from . import ai
    info = ai.status(db.get_settings())
    provider = info.get("provider") or "?"
    if not info.get("available"):
        raise Skip(f"{provider} introuvable ou pas lancé")
    if info.get("loggedIn") is False:
        raise Skip(f"{provider} non connecté")
    return f"{provider} ({info.get('model') or 'modèle par défaut'})"


def github_updates():
    latest = updates.fetch()
    if latest is None:
        raise Skip("aucune version publiée")
    expect(latest.get("version"), "dernière version illisible")
    return f"dernière version {latest['version']} (installée : {VERSION})"


CHECKS = [("arXiv : fiches (API)", arxiv_export), ("arXiv : pages HTML (LaTeXML)", arxiv_html), ("arXiv : annonces (RSS)", arxiv_rss),
          ("arXiv : jours passés (OAI-PMH)", arxiv_oai), ("arXiv : flux d'auteur", arxiv_author), ("Semantic Scholar", semantic_scholar),
          ("Zotero", zotero_api),
          ("Assistant IA", ai_assistant), ("Mises à jour (GitHub)", github_updates)]


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
        try:
            detail, status = check(), "ok"
        except Skip as error:
            status, detail = "skipped", str(error)
        except Changed as error:
            status, detail = "changed", str(error) or "réponse inattendue"
        except Exception as error:
            status, detail = "failed", f"{type(error).__name__}: {error}"[:300]
        results.append({"name": name, "status": status, "detail": detail or "", "seconds": round(time.time() - started, 1)})
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
    return {"checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "version": VERSION, "system": now,
            "baseline": now if baseline or not reference else reference,
            "systemChanged": moved, "previousSystem": reference, "results": results}


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
    return "\n".join(lines)


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
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
