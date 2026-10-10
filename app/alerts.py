"""Alertes : nouveaux articles des auteurs suivis.

Le serveur vérifie toutes les 3 heures (et une minute après le lancement) ; chaque article jamais vu et
récent (moins de NOTIFY_DAYS jours) déclenche une notification macOS (réglage « notifyFollowed »), et
compte dans la pastille du feed jusqu'à ce qu'il ait été affiché.
"""

import asyncio
import logging
import subprocess
import threading
import time

from fastapi.concurrency import run_in_threadpool

from . import db, feed, profile
from .config import FEED_CATEGORIES
from .relevance import author_key

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
            f"""SELECT key, title, authors, announced FROM feed_items WHERE (followed = 1 AND announced >= {WINDOW})
                   OR (citing IS NOT NULL AND cited_found >= {WINDOW})""")]


def _followed_names():
    return [i["label"] for i in profile.list_items() if i["kind"] == "author"]


def _who(item, names=None):
    """Auteur suivi parmi les auteurs de l'article, tel qu'écrit dans l'article (ou None)."""
    keys = {author_key(name): name for name in item.get("authors", [])}
    for name in names if names is not None else _followed_names():
        if author_key(name) in keys:
            return keys[author_key(name)]
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
    _forget_unfollowed()
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


def _forget_unfollowed():
    """Auteur retiré du profil : ses articles quittent la section « auteurs suivis » et la pastille."""
    import json
    names = _followed_names()
    stale = [row["key"] for row in recent_followed()
             if not _who({"authors": json.loads(row["authors"] or "[]")}, names)]
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


def display(title, subtitle, body):
    """Notification macOS (Centre de notifications)."""
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
