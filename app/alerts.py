"""Alertes : nouveaux articles des auteurs suivis.

Le serveur vérifie toutes les 3 heures (et une minute après le lancement) ; chaque article jamais vu
déclenche une notification macOS (réglage « notifyFollowed »), et compte dans la pastille du feed
jusqu'à ce que la liste des nouveautés soit affichée.
"""

import asyncio
import subprocess

from fastapi.concurrency import run_in_threadpool

from . import db, feed, profile
from .config import FEED_CATEGORIES
from .relevance import author_key

CHECK_EVERY = 3 * 3600
RECENT = "date('now', '-60 days')"


def _keys(name):
    return set(db.get_settings().get(name) or [])


def recent_followed():
    with db.connect() as connection:
        return [dict(r) for r in connection.execute(
            f"SELECT key, title, authors FROM feed_items WHERE followed = 1 AND announced >= {RECENT}")]


def check(categories=None):
    """Articles récents des auteurs suivis ; renvoie ceux qui n'avaient encore jamais été vus."""
    items = profile.followed_author_papers(categories or FEED_CATEGORIES)
    with db.connect() as connection:
        for item in items:
            item["title"], item["abstract"] = feed.tex_text(item["title"]), feed.tex_text(item["abstract"])
            feed._store(connection, item, followed=True)
    settings = db.get_settings()
    first = "alerts.known" not in settings  # premier passage : on retient l'existant, sans rien annoncer
    known = set(settings.get("alerts.known") or []) | set(settings.get("alerts.seen") or [])
    new = [item for item in items if item["key"] not in known]
    db.save_settings({"alerts.known": sorted(known | {item["key"] for item in items})})
    if first:
        return []
    if new and settings.get("notifyFollowed", True):
        notify(new)
    return new


def _who(item):
    """Auteur suivi parmi les auteurs de l'article (pour le texte de la notification)."""
    followed = [i["label"] for i in profile.list_items() if i["kind"] == "author"]
    keys = {author_key(name): name for name in item.get("authors", [])}
    for name in followed:
        if author_key(name) in keys:
            return keys[author_key(name)]
    return (item.get("authors") or ["?"])[0]


def _quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(items):
    """Notification macOS (Centre de notifications) ; au-delà de trois articles, un seul résumé."""
    if len(items) > 3:
        messages = [("arXivReader", f"{len(items)} nouveaux articles de vos auteurs suivis",
                     ", ".join(sorted({_who(i) for i in items})))]
    else:
        messages = [("arXivReader", f"{_who(i)} — nouvel article", i["title"]) for i in items]
    for title, subtitle, body in messages:
        script = f"display notification {_quote(body[:240])} with title {_quote(title)} subtitle {_quote(subtitle)}"
        try:
            subprocess.run(["osascript", "-e", script], timeout=10, check=False, capture_output=True)
        except (OSError, subprocess.TimeoutExpired):
            pass


def unseen():
    """Clés des articles récents d'auteurs suivis pas encore vus dans le feed."""
    settings = db.get_settings()
    keys = [item["key"] for item in recent_followed()]
    if "alerts.seen" not in settings:  # première fois : tout l'existant compte comme vu
        db.save_settings({"alerts.seen": keys})
        return []
    seen = set(settings["alerts.seen"])
    return [key for key in keys if key not in seen]


def mark_seen(keys=None):
    """Articles vus (affichés dans le feed) ; sans liste, tous les articles récents d'auteurs suivis."""
    keys = {item["key"] for item in recent_followed()} if keys is None else set(keys)
    db.save_settings({"alerts.seen": sorted(_keys("alerts.seen") | keys)})


async def loop():
    await asyncio.sleep(60)
    while True:
        try:
            await run_in_threadpool(check, db.get_settings().get("feedCategories") or None)
        except Exception:
            pass  # réseau indisponible : nouvel essai au prochain passage
        await asyncio.sleep(CHECK_EVERY)
