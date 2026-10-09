"""Feed : nouveautés arXiv (flux RSS des catégories suivies), recommandations Semantic Scholar,
et repérage d'articles arXiv dans un texte collé (e-mail Scholar Inbox, export BibTeX…)."""

import json
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from . import db
from .arxiv import ATOM, ARXIV_NS, extract_ids
from .config import FEED_CATEGORIES, USER_AGENT

RSS_URL = "https://rss.arxiv.org/rss/{categories}"
S2_RECOMMEND = "https://api.semanticscholar.org/recommendations/v1/papers"
S2_FIELDS = "title,externalIds,authors,abstract,publicationDate,year,url"
DC = "{http://purl.org/dc/elements/1.1/}"
RSS_ARXIV = "{http://arxiv.org/schemas/atom}"


class FeedError(Exception):
    pass


# Accents et tirets TeX hors formules (« K\\"ahler », « Bohr--Sommerfeld ») → caractères ordinaires
ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "c": "\u0327", "v": "\u030c", "=": "\u0304"}
ACCENT = re.compile(r"""\\(["'`^~=]|[cv](?=[\s{]))\s*(?:\{\s*\\?([A-Za-z])\s*\}|\\?([A-Za-z]))""")


def tex_text(text):
    parts = re.split(r"(\$[^$]*\$)", text or "")
    for i in range(0, len(parts), 2):  # hors des formules $…$ seulement
        part = ACCENT.sub(lambda m: unicodedata.normalize("NFC", (m.group(2) or m.group(3)) + ACCENTS[m.group(1)]), parts[i])
        part = part.replace("---", "—").replace("--", "–").replace("\\ ", " ").replace("\\&", "&")
        part = re.sub(r"\{\\(?:em|it|bf)\s+([^{}]*)\}", r"\1", part).replace("{", "").replace("}", "")
        parts[i] = part
    return " ".join("".join(parts).split())


def _client(headers=None):
    return httpx.Client(headers={"User-Agent": USER_AGENT, **(headers or {})}, follow_redirects=True, timeout=40)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------- Nouveautés arXiv ----------

def parse_rss(xml_text):
    items = []
    channel = ET.fromstring(xml_text).find("channel")
    for item in channel.findall("item") if channel is not None else []:
        description = item.findtext("description") or ""
        match = re.match(r"\s*arXiv:(\S+?)(v(\d+))?\s+Announce Type:\s*(\S+)", description)
        if not match:
            continue
        abstract = description.split("Abstract:", 1)[-1].strip()
        try:
            announced = parsedate_to_datetime(item.findtext("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            announced = None
        categories = [c.text for c in item.findall("category") if c.text]
        authors = [a.strip() for a in (item.findtext(f"{DC}creator") or "").split(",") if a.strip()]
        items.append({
            "key": match.group(1),
            "arxiv_id": match.group(1),
            "version": int(match.group(3) or 1),
            "title": tex_text(item.findtext("title")),
            "authors": authors,
            "abstract": tex_text(abstract),
            "categories": categories,
            "primary_category": categories[0] if categories else "",
            "announce_type": item.findtext(f"{RSS_ARXIV}announce_type") or match.group(4),
            "announced": announced,
            "link": item.findtext("link") or f"https://arxiv.org/abs/{match.group(1)}",
        })
    return items


def refresh_arxiv(categories=None):
    categories = categories or FEED_CATEGORIES
    with _client() as client:
        response = client.get(RSS_URL.format(categories="+".join(categories)))
    response.raise_for_status()
    items = parse_rss(response.text)
    with db.connect() as connection:
        for item in items:
            connection.execute(
                """INSERT INTO feed_items (key, arxiv_id, version, title, authors, abstract, categories,
                       primary_category, announce_type, announced, in_arxiv, link, fetched_at)
                   VALUES (:key, :arxiv_id, :version, :title, :authors, :abstract, :categories,
                       :primary_category, :announce_type, :announced, 1, :link, :fetched_at)
                   ON CONFLICT(key) DO UPDATE SET version = excluded.version, title = excluded.title,
                       authors = excluded.authors, abstract = excluded.abstract, categories = excluded.categories,
                       primary_category = excluded.primary_category, announce_type = excluded.announce_type,
                       announced = excluded.announced, in_arxiv = 1, link = excluded.link""",
                {**item, "authors": json.dumps(item["authors"], ensure_ascii=False),
                 "categories": json.dumps(item["categories"]), "fetched_at": _now()},
            )
    db.save_settings({"feed.arxivRefreshed": _now()})
    return len(items)


# ---------- Recommandations Semantic Scholar ----------

def _positive_negative():
    with db.connect() as connection:
        library = [r["arxiv_id"] for r in connection.execute("SELECT arxiv_id FROM papers ORDER BY added_at DESC")]
        rows = connection.execute(
            "SELECT s.key, s.status, f.arxiv_id FROM feed_status s LEFT JOIN feed_items f ON f.key = s.key"
        ).fetchall()
    liked = [r["arxiv_id"] or r["key"] for r in rows if r["status"] == "liked"]
    disliked = [r["arxiv_id"] or r["key"] for r in rows if r["status"] == "disliked"]

    def s2_id(key):
        return key[3:] if key.startswith("s2:") else f"ArXiv:{key}"

    positives = list(dict.fromkeys(s2_id(k) for k in liked + library))[:100]
    negatives = list(dict.fromkeys(s2_id(k) for k in disliked))[:100]
    return positives, negatives


def refresh_recommendations(api_key=None, limit=60):
    positives, negatives = _positive_negative()
    if not positives:
        raise FeedError("no-seed")
    headers = {"x-api-key": api_key} if api_key else {}
    body = {"positivePaperIds": positives, "negativePaperIds": negatives}
    with _client(headers) as client:
        for attempt in range(4):
            response = client.post(S2_RECOMMEND, params={"fields": S2_FIELDS, "limit": limit}, json=body)
            if response.status_code != 429:
                break
            time.sleep(2 * 2 ** attempt)  # 2, 4, 8, 16 s
    if response.status_code == 429:
        raise FeedError("rate-limited")
    if response.status_code == 403 and api_key:
        raise FeedError("bad-key")
    response.raise_for_status()
    papers = response.json().get("recommendedPapers", [])
    with db.connect() as connection:
        connection.execute("UPDATE feed_items SET s2_rank = NULL")
        for rank, paper in enumerate(papers):
            arxiv_id = (paper.get("externalIds") or {}).get("ArXiv")
            key = arxiv_id or f"s2:{paper['paperId']}"
            connection.execute(
                """INSERT INTO feed_items (key, arxiv_id, title, authors, abstract, categories, announced,
                       s2_rank, link, fetched_at)
                   VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET s2_rank = excluded.s2_rank,
                       title = CASE WHEN feed_items.in_arxiv = 1 THEN feed_items.title ELSE excluded.title END,
                       authors = CASE WHEN feed_items.in_arxiv = 1 THEN feed_items.authors ELSE excluded.authors END,
                       abstract = CASE WHEN feed_items.in_arxiv = 1 THEN feed_items.abstract ELSE excluded.abstract END""",
                (key, arxiv_id, tex_text(paper.get("title")),
                 json.dumps([a.get("name", "") for a in paper.get("authors") or []], ensure_ascii=False),
                 tex_text(paper.get("abstract")), paper.get("publicationDate") or str(paper.get("year") or ""),
                 rank, f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else paper.get("url"), _now()),
            )
    db.save_settings({"feed.s2Refreshed": _now(), "feed.s2Seeds": [len(positives), len(negatives)]})
    return len(papers)


# ---------- Lecture du feed ----------

def list_items(view, announce_types=None):
    with db.connect() as connection:
        library = {r["arxiv_id"]: r["id"] for r in connection.execute("SELECT id, arxiv_id FROM papers")}
        status = {r["key"]: r["status"] for r in connection.execute("SELECT key, status FROM feed_status")}
        if view == "recommended":
            rows = connection.execute("SELECT * FROM feed_items WHERE s2_rank IS NOT NULL ORDER BY s2_rank").fetchall()
        else:
            types = announce_types or ["new", "cross"]
            marks = ",".join("?" * len(types))
            rows = connection.execute(
                f"""SELECT * FROM feed_items WHERE in_arxiv = 1 AND announce_type IN ({marks})
                    ORDER BY announced DESC, CASE announce_type WHEN 'new' THEN 0 WHEN 'cross' THEN 1 ELSE 2 END,
                    arxiv_id DESC LIMIT 1500""",
                types,
            ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["authors"] = json.loads(item["authors"] or "[]")
        item["categories"] = json.loads(item["categories"] or "[]")
        item["status"] = status.get(item["key"])
        item["libraryId"] = library.get(item["arxiv_id"])
        items.append(item)
    return items


def set_status(key, status):
    with db.connect() as connection:
        if status in ("liked", "disliked"):
            connection.execute(
                "INSERT INTO feed_status (key, status, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                (key, status),
            )
        else:
            connection.execute("DELETE FROM feed_status WHERE key = ?", (key,))


# ---------- Repérage d'articles dans un texte collé ----------

def lookup_ids(text, limit=200):
    """Identifiants arXiv trouvés dans un texte, avec titre et auteurs (API arXiv, par lots de 50)."""
    ids = extract_ids(text)[:limit]
    found = {}
    with _client() as client:
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            for attempt in range(4):
                response = client.get("https://export.arxiv.org/api/query",
                                      params={"id_list": ",".join(batch), "max_results": len(batch)})
                if response.status_code not in (429, 503):
                    break
                time.sleep(3 * (attempt + 1))
            response.raise_for_status()
            for entry in ET.fromstring(response.text).findall(f"{ATOM}entry"):
                entry_id = entry.findtext(f"{ATOM}id") or ""
                match = re.search(r"abs/(.+?)(v\d+)?$", entry_id)
                title = tex_text(entry.findtext(f"{ATOM}title"))
                if not match or not title:
                    continue
                primary = entry.find(f"{ARXIV_NS}primary_category")
                found[match.group(1)] = {
                    "arxiv_id": match.group(1),
                    "title": title,
                    "authors": [" ".join((a.findtext(f"{ATOM}name") or "").split()) for a in entry.findall(f"{ATOM}author")],
                    "primary_category": primary.get("term") if primary is not None else "",
                    "published": (entry.findtext(f"{ATOM}published") or "")[:10],
                }
            if start + 50 < len(ids):
                time.sleep(3)
    with db.connect() as connection:
        library = {r["arxiv_id"]: r["id"] for r in connection.execute("SELECT id, arxiv_id FROM papers")}
    papers = [dict(found[i], libraryId=library.get(i)) for i in ids if i in found]
    missing = [i for i in ids if i not in found]
    return {"papers": papers, "missing": missing}
