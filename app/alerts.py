"""Alertes : nouveaux articles des auteurs suivis.

Le serveur vérifie toutes les 3 heures (et une minute après le lancement) ; chaque article jamais vu et
récent (moins de NOTIFY_DAYS jours) déclenche une notification macOS ou Windows (réglage « notifyFollowed »), et
compte dans la pastille du feed jusqu'à ce qu'il ait été affiché.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time

from fastapi.concurrency import run_in_threadpool

from . import db, feed, profile
from .config import FEED_CATEGORIES
from .relevance import followed_author, follows

CHECK_EVERY = 3 * 3600
NOTIFY_DAYS = 7  # un article plus ancien (auteur tout juste suivi…) est retenu sans notification
WINDOW = f"date('now', '-{feed.FOLLOWED_DAYS} days')"  # même fenêtre que la section du feed

log = logging.getLogger("arxivreader.alerts")
_lock = threading.Lock()  # alerts.known / alerts.seen : lus puis réécrits
stop = threading.Event()  # arrêt du serveur : la vérification en cours s'interrompt


def recent_followed():
    """Articles récents des auteurs suivis, et articles qui citent les vôtres trouvés récemment."""
    with db.connect() as connection:
        return [dict(r) for r in connection.execute(
            f"""SELECT key, title, authors, categories, announced FROM feed_items WHERE (followed = 1 AND announced >= {WINDOW})
                   OR (citing IS NOT NULL AND cited_found >= {WINDOW})""")]


def _followed_names():
    """Auteurs suivis, avec ce qui écarte leurs homonymes (catégories gardées, articles écartés)."""
    return [followed_author(i) for i in profile.list_items() if i["kind"] == "author"]


def _who(item, names=None):
    """Auteur suivi parmi les auteurs de l'article, tel qu'écrit dans l'article (ou None)."""
    categories = item.get("categories") or []
    for author in names if names is not None else _followed_names():
        # pas un homonyme de même initiale (Xin Ma pour Xiaonan Ma), ni un homonyme écarté à la main
        if follows(author, item.get("authors", []), categories, item.get("arxiv_id") or item.get("key")):
            return next((w for w in item.get("authors", []) if follows(author, [w], categories)), None)
    return None


def check(categories=None):
    """Articles récents des auteurs suivis ; renvoie ceux qui n'avaient encore jamais été vus."""
    items = profile.followed_author_papers(categories or FEED_CATEGORIES, days=feed.FOLLOWED_DAYS, stop=stop)
    if stop.is_set():
        return []
    with db.connect() as connection:
        for item in items:
            item["title"], item["abstract"] = feed.tex_text(item["title"]), feed.tex_text(item["abstract"])
            feed._store(connection, item, followed=True)
    forget_unfollowed()
    with _lock:
        settings = db.get_settings()
        first = "alerts.known" not in settings  # premier passage : on retient l'existant, sans rien annoncer
        known = set(settings.get("alerts.known") or []) | set(settings.get("alerts.seen") or [])
        new = [item for item in items if item["key"] not in known]
        current = {item["key"] for item in recent_followed()}
        db.save_settings({"alerts.known": sorted((known & current) | {item["key"] for item in items})})
    if first:
        return []
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - NOTIFY_DAYS * 86400))
    recent = [item for item in new if (item.get("announced") or "") >= since]
    if recent and settings.get("notifyFollowed", True) is not False:
        notify(recent)
    return new


def forget_unfollowed():
    """Auteur retiré du profil, ou homonyme écarté : ses articles quittent la section « auteurs suivis » et la pastille
    (tout de suite, sans attendre la vérification suivante)."""
    import json
    names = _followed_names()
    stale = [row["key"] for row in recent_followed()
             if not _who({"key": row["key"], "authors": json.loads(row["authors"] or "[]"),
                          "categories": json.loads(row.get("categories") or "[]")}, names)]
    if stale:
        with db.connect() as connection:
            connection.executemany("UPDATE feed_items SET followed = 0 WHERE key = ?", [(key,) for key in stale])


def _quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(items):
    """Notification macOS (Centre de notifications) ; au-delà de trois articles, un seul résumé."""
    names = _followed_names()
    who = lambda item: _who(item, names) or (item.get("authors") or ["?"])[0]  # noqa: E731
    if len(items) > 3:
        messages = [("arXivReader", f"{len(items)} nouveaux articles de vos auteurs suivis",
                     ", ".join(sorted({who(i) for i in items})))]
    else:
        messages = [("arXivReader", f"{who(i)} — nouvel article", i["title"]) for i in items]
    for title, subtitle, body in messages:
        display(title, subtitle, body)


# Notification Windows (centre de notifications), par Windows PowerShell : textes passés par une variable
# d'environnement, jamais insérés dans le script ; affichée au nom de PowerShell (pas d'identité d'application
# enregistrée pour la version portable)
TOAST = r"""
$d = $env:ARXIVREADER_TOAST | ConvertFrom-Json
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText04)
$t = $x.GetElementsByTagName('text')
$t.Item(0).AppendChild($x.CreateTextNode($d.title)) > $null
$t.Item(1).AppendChild($x.CreateTextNode($d.subtitle)) > $null
$t.Item(2).AppendChild($x.CreateTextNode($d.body)) > $null
$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""


def _display_windows(title, subtitle, body):
    env = dict(os.environ, ARXIVREADER_TOAST=json.dumps({"title": title, "subtitle": subtitle, "body": body[:240]}))
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", TOAST], env=env, timeout=20,
                       check=False, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        log.warning("notification Windows impossible")


def display(title, subtitle, body):
    """Notification macOS (Centre de notifications), ou Windows."""
    if sys.platform == "win32":
        return _display_windows(title, subtitle, body)
    script = f"display notification {_quote(body[:240])} with title {_quote(title)} subtitle {_quote(subtitle)}"
    try:
        subprocess.run(["osascript", "-e", script], timeout=10, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        log.warning("notification macOS impossible")


def unseen():
    """Clés des articles récents d'auteurs suivis pas encore vus dans le feed."""
    keys = [item["key"] for item in recent_followed()]
    with _lock:
        settings = db.get_settings()
        if "alerts.seen" not in settings:  # première fois : tout l'existant compte comme vu
            db.save_settings({"alerts.seen": keys})
            return []
        seen = set(settings["alerts.seen"])
    return [key for key in keys if key not in seen]


def mark_seen(keys=None):
    """Articles vus (affichés dans le feed) ; sans liste, tous les articles récents d'auteurs suivis.
    Seuls les articles récents d'auteurs suivis sont retenus (la liste ne grossit pas sans fin)."""
    current = {item["key"] for item in recent_followed()}
    keys = current if keys is None else set(keys) & current
    with _lock:
        seen = set(db.get_settings().get("alerts.seen") or [])
        db.save_settings({"alerts.seen": sorted((seen & current) | keys)})


async def loop():
    await asyncio.sleep(60)
    while not stop.is_set():
        try:
            await run_in_threadpool(check, db.get_settings().get("feedCategories") or None)
        except Exception:
            log.exception("vérification des auteurs suivis impossible")  # nouvel essai au prochain passage
        try:
            from . import citations
            await run_in_threadpool(citations.check, db.get_settings().get("s2ApiKey") or None)  # une fois par jour
        except Exception:
            log.exception("vérification des citations impossible")
        await asyncio.sleep(CHECK_EVERY)
