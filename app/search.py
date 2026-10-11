"""Recherche dans tout arXiv (interface publique export.arxiv.org/api), avec les options de la recherche
avancée d'arXiv qu'elle permet : champs, ET / OU / SAUF, catégories (articles croisés compris ou non),
période de soumission, tri, pages de résultats."""

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import httpx

from . import db, relevance
from .arxiv import ARXIV_NS, ATOM, with_retry
from .config import USER_AGENT
from .feed import tex_text

API = "https://export.arxiv.org/api/query"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
PAGE_SIZE = 25
MAX_START = 2000            # arXiv ne renvoie pas au-delà de quelques milliers de résultats par requête

FIELDS = {"all": "all", "title": "ti", "author": "au", "abstract": "abs", "comments": "co",
          "journal": "jr", "category": "cat", "report": "rn", "id": "id"}
OPERATORS = {"and": "AND", "or": "OR", "not": "ANDNOT"}
SORTS = {"relevance": ("relevance", "descending"), "newest": ("submittedDate", "descending"),
         "oldest": ("submittedDate", "ascending")}
CATEGORY = re.compile(r"[a-z-]+(\.[A-Za-z-]+)?")
ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5}|[a-z-]+(\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)


class SearchError(ValueError):
    pass


# arXiv demande au plus une requête toutes les 3 secondes : les recherches rapprochées attendent leur tour
_lock = threading.Lock()
_last = 0.0


def _wait_turn():
    global _last
    with _lock:
        delay = _last + 3 - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _last = time.monotonic()


def _words(value):
    """Mots d'un critère ; « entre guillemets » : expression exacte. Caractères de la syntaxe d'arXiv retirés."""
    value = value.replace("“", '"').replace("”", '"')
    parts = re.findall(r'"([^"]+)"|(\S+)', value)
    clean = lambda text: re.sub(r'[()":\[\]]', " ", text).strip()
    phrases = [clean(p) for p, _ in parts if p and clean(p)]
    words = [clean(w) for _, w in parts if w and clean(w)]
    return phrases, [w for word in words for w in word.split()]


def _term(field, value):
    prefix = FIELDS[field]
    phrases, words = _words(value)
    pieces = [f'{prefix}:"{p}"' for p in phrases] + [f"{prefix}:{w}" for w in words]
    if not pieces:
        return None
    return pieces[0] if len(pieces) == 1 else "(" + " AND ".join(pieces) + ")"


def build_query(terms, categories=(), dates=None):
    """Requête arXiv (search_query) et identifiants (id_list) d'après les critères ; SearchError si vide."""
    expression = None
    ids = []
    for term in terms[:10]:
        field, value, op = term.get("field", "all"), str(term.get("value", ""))[:300], term.get("op", "and")
        if field not in FIELDS or op not in OPERATORS:
            raise SearchError("Critère invalide")
        if field == "id":  # identifiants : liste à part, combinée par arXiv avec le reste (filtre)
            ids += [m.group(1) for m in ARXIV_ID.finditer(value)]
            continue
        piece = _term(field, value)
        if piece is None:
            continue
        expression = piece if expression is None else f"({expression}) {OPERATORS[op]} {piece}"
    wanted = [c for c in categories if CATEGORY.fullmatch(c)][:20]
    if wanted:
        cats = " OR ".join(f"cat:{c}" for c in wanted)
        cats = f"({cats})" if len(wanted) > 1 else cats
        expression = cats if expression is None else f"({expression}) AND {cats}"
    span = _date_span(dates)
    if span:
        clause = f"submittedDate:[{span[0]}0000 TO {span[1]}2359]"
        expression = clause if expression is None else f"({expression}) AND {clause}"
    if expression is None and not ids:
        raise SearchError("Recherche vide")
    return expression, ids


def _date_span(dates):
    """(AAAAMMJJ, AAAAMMJJ) de la période demandée, ou None (toutes les dates)."""
    dates = dates or {}
    mode = dates.get("mode", "all")
    today = date.today()
    try:
        if mode == "year":
            year = int(dates["year"])
            if not 1991 <= year <= today.year:
                raise ValueError
            return f"{year}0101", f"{year}1231"
        if mode == "months":
            return (today - timedelta(days=365)).strftime("%Y%m%d"), today.strftime("%Y%m%d")
        if mode == "range":
            start = date.fromisoformat(dates.get("from") or "1991-01-01")
            end = date.fromisoformat(dates.get("to") or today.isoformat())
            if end < start:
                start, end = end, start
            return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    except (KeyError, ValueError, TypeError):
        raise SearchError("Période invalide")
    return None


def parse(xml_text):
    """Entrées de la réponse d'arXiv, au format des articles du feed, et nombre total de résultats."""
    root = ET.fromstring(xml_text)
    total = int(root.findtext(f"{OPENSEARCH}totalResults") or 0)
    items = []
    for entry in root.findall(f"{ATOM}entry"):
        found = re.search(r"abs/(.+?)(v(\d+))?$", entry.findtext(f"{ATOM}id") or "")
        if not found:
            continue
        arxiv_id = found.group(1)
        primary = entry.find(f"{ARXIV_NS}primary_category")
        categories = [c.get("term") for c in entry.findall(f"{ATOM}category") if c.get("term")]
        items.append({
            "key": arxiv_id, "arxiv_id": arxiv_id, "version": int(found.group(3) or 1),
            "title": tex_text(" ".join((entry.findtext(f"{ATOM}title") or "").split())),
            "authors": [" ".join((a.findtext(f"{ATOM}name") or "").split()) for a in entry.findall(f"{ATOM}author")],
            "abstract": tex_text(" ".join((entry.findtext(f"{ATOM}summary") or "").split())),
            "categories": categories,
            "primary_category": primary.get("term") if primary is not None else (categories[0] if categories else ""),
            "announced": (entry.findtext(f"{ATOM}published") or "")[:10],
            "link": f"https://arxiv.org/abs/{arxiv_id}",
        })
    return items, total


def search(terms, categories=(), cross=True, dates=None, sort="relevance", page=0):
    expression, ids = build_query(terms, categories, dates)
    sort_by, order = SORTS.get(sort, SORTS["relevance"])
    start = max(0, min(int(page), MAX_START // PAGE_SIZE)) * PAGE_SIZE
    params = {"start": start, "max_results": PAGE_SIZE, "sortBy": sort_by, "sortOrder": order}
    if expression:
        params["search_query"] = expression
    if ids:
        params["id_list"] = ",".join(ids[:50])
    _wait_turn()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=40, follow_redirects=True) as client:
        response = with_retry(lambda: client.get(API, params=params))
    response.raise_for_status()
    items, total = parse(response.text)
    if categories and not cross:  # articles croisés exclus : catégorie principale parmi celles demandées
        wanted = set(categories)
        items = [i for i in items if i["primary_category"] in wanted]
    remember(items)
    return {"items": annotate(items), "total": total, "start": start, "pageSize": PAGE_SIZE}


def remember(items):
    """Résultats gardés (hors du feed : in_arxiv = 0) : un pouce levé ou baissé compte alors dans le profil."""
    with db.connect() as connection:
        connection.executemany(
            """INSERT OR IGNORE INTO feed_items (key, arxiv_id, version, title, authors, abstract, categories,
                   primary_category, announced, in_arxiv, link) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            [(i["key"], i["arxiv_id"], i["version"], i["title"], json.dumps(i["authors"], ensure_ascii=False),
              i["abstract"], json.dumps(i["categories"]), i["primary_category"], i["announced"], i["link"]) for i in items])


def annotate(items):
    """Article déjà lu ou dans la bibliothèque, avis donné : comme dans le feed ; proximité avec le profil."""
    with db.connect() as connection:
        papers = connection.execute("SELECT id, arxiv_id, in_library FROM papers").fetchall()
        status = {r["key"]: r["status"] for r in connection.execute("SELECT key, status FROM feed_status")}
    library = {r["arxiv_id"]: r["id"] for r in papers if r["in_library"]}
    cached = {r["arxiv_id"]: r["id"] for r in papers}
    for item in items:
        item["status"] = status.get(item["key"])
        item["libraryId"] = library.get(item["arxiv_id"])
        item["readId"] = cached.get(item["arxiv_id"])
        item["citing"] = []
    try:
        return relevance.score_items(items)
    except Exception:  # vecteurs de sens indisponibles : résultats sans proximité
        return items
