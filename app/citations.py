"""Alertes de citation : nouveaux articles qui citent les vôtres (Semantic Scholar).

Une fois par jour au plus (en même temps que la vérification des auteurs suivis), les articles qui
citent ceux du profil (« Mes articles ») sont demandés à Semantic Scholar. Un article jamais vu est
ajouté au feed (section « Citent vos articles »), compte dans la pastille du feed jusqu'à ce qu'il ait
été affiché, et déclenche une notification macOS (réglage « notifyCitations »). Au tout premier passage,
les citations déjà connues sont retenues sans être annoncées.
"""

import json
import logging
import time

from . import alerts, db, feed, profile
from .relevance import s2_key

EVERY = 20 * 3600
FIELDS = "title,authors,externalIds,abstract,publicationDate,year,url"
log = logging.getLogger("arxivreader.citations")


def my_papers():
    """Articles du profil marqués « Mes articles », avec leur identifiant Semantic Scholar."""
    papers = []
    for item in profile.list_items():
        if item["kind"] != "mine":
            continue
        key = s2_key(ref=item.get("ref"))
        if key:
            papers.append({"label": item["label"], "key": key})
    return papers


def fetch(key, api_key=None):
    data = profile._get_s2(f"/paper/{key}/citations", {"fields": FIELDS, "limit": 100}, api_key) or {}
    return [entry["citingPaper"] for entry in data.get("data") or [] if (entry.get("citingPaper") or {}).get("paperId")]


def _store(connection, paper, cites):
    """Article citant ajouté au feed (ou complété : il peut citer plusieurs de vos articles)."""
    arxiv_id = (paper.get("externalIds") or {}).get("ArXiv")
    key = arxiv_id or f"s2:{paper['paperId']}"
    row = connection.execute("SELECT citing FROM feed_items WHERE key = ?", (key,)).fetchone()
    if row is None:
        connection.execute(
            """INSERT INTO feed_items (key, arxiv_id, title, authors, abstract, categories, announced, link,
                   fetched_at, citing, cited_found) VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?)""",
            (key, arxiv_id, feed.tex_text(paper.get("title")),
             json.dumps([a.get("name", "") for a in paper.get("authors") or []], ensure_ascii=False),
             feed.tex_text(paper.get("abstract")), paper.get("publicationDate") or str(paper.get("year") or ""),
             f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else feed.web_link(paper.get("url")), feed._now(),
             json.dumps([cites], ensure_ascii=False), feed._now()))
    else:
        known = json.loads(row["citing"] or "[]")
        connection.execute("UPDATE feed_items SET citing = ?, cited_found = COALESCE(cited_found, ?) WHERE key = ?",
                           (json.dumps(sorted(set(known) | {cites}), ensure_ascii=False), feed._now(), key))
    return key


def check(api_key=None, force=False):
    """Citations de vos articles ; renvoie les articles citants jamais vus (et les signale)."""
    settings = db.get_settings()
    if not force and time.time() - (settings.get("citations.checked") or 0) < EVERY:
        return []
    mine = my_papers()
    found = {}  # clé → (titre, auteurs, vos articles cités)
    for i, paper in enumerate(mine):
        if alerts.stop.is_set():
            return []
        if i:
            time.sleep(1.2)  # Semantic Scholar sans clé : environ une requête par seconde
        try:
            citing = fetch(paper["key"], api_key)
        except Exception:  # réseau, limite de requêtes : nouvel essai au prochain passage
            log.warning("citations de %s indisponibles", paper["key"])
            return []
        with db.connect() as connection:
            for other in citing:
                key = _store(connection, other, paper["label"])
                entry = found.setdefault(key, {"key": key, "title": feed.tex_text(other.get("title")), "cites": [],
                                               "authors": [a.get("name", "") for a in other.get("authors") or []]})
                entry["cites"].append(paper["label"])
    with alerts._lock:
        settings = db.get_settings()
        first = "citations.known" not in settings
        known = set(settings.get("citations.known") or [])
        new = [entry for key, entry in found.items() if key not in known]
        db.save_settings({"citations.known": sorted(known | set(found)), "citations.checked": time.time()})
    if first:
        # premier passage : l'existant est affiché dans le feed, mais ni annoncé ni compté comme nouveau
        alerts.mark_seen(list(found))
        return []
    if new and settings.get("notifyCitations", True) is not False:
        notify(new)
    return new


def notify(items):
    if len(items) > 3:
        messages = [("arXivReader", f"{len(items)} nouveaux articles citent les vôtres", ", ".join(sorted({i["cites"][0] for i in items}))[:200])]
    else:
        messages = [("arXivReader", f"{(i['authors'] or ['?'])[0]} cite « {i['cites'][0][:60]} »", i["title"]) for i in items]
    for title, subtitle, body in messages:
        alerts.display(title, subtitle, body)
