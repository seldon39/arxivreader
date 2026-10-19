"""Mises à jour : la dernière version publiée sur GitHub est-elle plus récente que celle-ci ?

Une requête par jour au plus (réglage « checkUpdates », activé par défaut), à l'API publique de GitHub ;
rien n'est envoyé d'autre que la requête elle-même. Le résultat est gardé dans les réglages.
"""

import re
import sys
import threading
import time

import httpx

from . import db
from .config import ROOT, USER_AGENT
from .version import VERSION

REPOSITORY = "seldon39/arxivreader"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
# Version Windows : publiée à part, en préversion (« windows-v1.9.14 », un .zip), que « latest » ignore
WINDOWS = sys.platform == "win32"
ALL_RELEASES = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100"
WINDOWS_TAG = "windows-v"


def install_kind(root=ROOT, frozen=None):
    """Comment arXivReader est installé : application Windows (empaquetée), application Mac (dans un .app), ou code
    source (./arxivreader, sous Linux ou sur un Mac de développement) — mis à jour par « git pull »."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        return "windows"
    return "mac-app" if ".app/Contents/" in str(root) else "source"


INSTALL = install_kind()
EVERY = 24 * 3600
_lock = threading.Lock()


def parse(version):
    """« v1.2.0 » → (1, 2, 0) ; None si ce n'est pas un numéro de version."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", (version or "").strip())
    return tuple(int(part or 0) for part in match.groups()) if match else None


def newer(latest, current=None):
    current = current or VERSION
    return bool(parse(latest) and parse(current) and parse(latest) > parse(current))


def fetch(windows=WINDOWS):
    response = httpx.get(ALL_RELEASES if windows else LATEST, timeout=10, follow_redirects=True,
                         headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    if response.status_code == 404:  # aucune version publiée
        return None
    response.raise_for_status()
    if windows:  # la plus récente des versions Windows (liste de la plus récente à la plus ancienne)
        release = next((r for r in response.json() if not r.get("draft")
                        and (r.get("tag_name") or "").startswith(WINDOWS_TAG)), None)
        if release is None:
            return None
        version, suffix = release["tag_name"][len(WINDOWS_TAG):], ".zip"
    else:
        release = response.json()
        if not re.match(r"v\d", release.get("tag_name") or ""):
            return None  # pas une version Mac (vX.Y.Z) : jamais une version Windows promue par erreur
        version, suffix = release["tag_name"][1:], ".dmg"
    package = next((a["browser_download_url"] for a in release.get("assets", []) if a.get("name", "").endswith(suffix)), None)
    return {"version": version, "url": release.get("html_url") or RELEASES,
            "download": package, "notes": (release.get("body") or "")[:4000], "published": release.get("published_at")}


def state(force=False):
    """Version installée, dernière version connue, et s'il faut la signaler. Vérifie auprès de GitHub
    si `force`, ou si la dernière vérification date de plus d'un jour (et que le réglage le permet)."""
    with _lock:
        settings = db.get_settings()
        enabled = settings.get("checkUpdates", True) is not False
        due = time.time() - (settings.get("updates.checked") or 0) > EVERY
        error = None
        if force or (enabled and due):
            try:
                latest = fetch(windows=INSTALL == "windows")  # code source : mêmes numéros que la version Mac
                db.save_settings({"updates.latest": latest, "updates.checked": time.time()})
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as failure:
                latest = settings.get("updates.latest")
                error = f"{type(failure).__name__}: {failure}"
        else:
            latest = settings.get("updates.latest")
        settings = db.get_settings()
    available = bool(latest) and newer(latest.get("version"))
    if available and INSTALL == "source":
        latest = {**latest, "download": None}  # pas de .dmg à proposer : « git pull »
    return {"current": VERSION, "latest": latest if available else None, "available": available, "install": INSTALL,
            "dismissed": available and settings.get("updates.dismissed") == latest["version"],
            "checked": settings.get("updates.checked"), "enabled": enabled, "error": error, "releases": RELEASES}


def dismiss(version):
    db.save_settings({"updates.dismissed": version})
