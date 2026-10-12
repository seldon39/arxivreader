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

from . import db, journals, relevance
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
         "oldest": ("submittedDate", "ascending"), "profile": ("relevance", "descending")}
# « Pertinence pour vous » : les POOL meilleurs résultats d'arXiv, reclassés selon le profil (comme le feed) ;
# articles croisés exclus : arXiv ne sait pas les écarter, l'app en lit FILTER_POOL d'un coup et les retire.
# Lots gardés quelques minutes pour passer d'une page à l'autre sans nouvelle requête
POOL = 200
FILTER_POOL = 500
POOL_TTL = 600
_pools = {}
_pools_lock = threading.Lock()
# catégorie arXiv : « archive.SOUS » (math.AG, cond-mat.mes-hall) ou une des catégories sans sous-catégorie
CATEGORY = re.compile(r"[a-z-]+\.[A-Za-z-]+")
STANDALONE = {"math-ph", "hep-th", "hep-ph", "hep-ex", "hep-lat", "quant-ph", "gr-qc", "nucl-th", "nucl-ex"}
BOOLEAN_WORDS = {"AND", "OR", "NOT", "ANDNOT"}
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


def normalize_category(category):
    """« math.ag » → « math.AG » (sous-catégories de deux lettres en majuscules) ; None si ce n'est pas une
    catégorie (une archive seule, « math », ne donne aucun résultat sur arXiv)."""
    category = str(category).strip()
    if category.lower() in STANDALONE:
        return category.lower()
    if not CATEGORY.fullmatch(category):
        return None
    archive, sub = category.split(".", 1)
    return f"{archive.lower()}.{sub.upper() if len(sub) == 2 else sub.lower()}"


def _words(value):
    """Mots d'un critère ; « entre guillemets » : expression exacte. Caractères de la syntaxe d'arXiv retirés,
    et mots AND / OR / NOT écartés (arXiv les prendrait pour des opérateurs et refuserait la requête)."""
    value = value.replace("“", '"').replace("”", '"')
    parts = re.findall(r'"([^"]+)"|(\S+)', value)
    clean = lambda text: re.sub(r'[()":\[\]\\{}~]', " ", text).strip()
    phrases = [clean(p) for p, _ in parts if p and clean(p)]
    words = [w for _, word in parts if word for w in clean(word).split()]
    return phrases, [w for w in words if w.upper() not in BOOLEAN_WORDS]


def _term(field, value):
    prefix = FIELDS[field]
    phrases, words = _words(value)
    pieces = [f'{prefix}:"{p}"' for p in phrases] + [f"{prefix}:{w}" for w in words]
    if not pieces:
        return None
    return pieces[0] if len(pieces) == 1 else "(" + " AND ".join(pieces) + ")"


def build_query(terms, categories=(), dates=None):
    """Requête arXiv (search_query) et identifiants (id_list) d'après les critères ; SearchError si vide.
    Les critères ET / OU se combinent dans l'ordre, puis catégories et période ; les SAUF viennent en dernier
    (une exclusion n'a de sens qu'après ce qu'on cherche, même si elle est sur la première ligne)."""
    expression = None
    ids = []
    excluded = []
    for term in terms[:10]:
        field, value, op = term.get("field", "all"), str(term.get("value", ""))[:300], term.get("op", "and")
        if field not in FIELDS or op not in OPERATORS:
            raise SearchError("Critère invalide")
        if field == "id":
            found = [m.group(1) for m in ARXIV_ID.finditer(value)]
            if op == "not":  # identifiants à écarter
                excluded += [f"id:{i}" for i in found]
            else:  # identifiants cherchés : liste à part, combinée par arXiv avec le reste (filtre)
                ids += found
            continue
        piece = _term(field, value)
        if piece is None:
            continue
        if op == "not":
            excluded.append(piece)
        else:
            expression = piece if expression is None else f"({expression}) {OPERATORS[op]} {piece}"
    wanted = list(dict.fromkeys(c for c in map(normalize_category, categories) if c))[:20]
    if wanted:
        cats = " OR ".join(f"cat:{c}" for c in wanted)
        cats = f"({cats})" if len(wanted) > 1 else cats
        expression = cats if expression is None else f"({expression}) AND {cats}"
    span = _date_span(dates)
    if span:
        clause = f"submittedDate:[{span[0]}0000 TO {span[1]}2359]"
        expression = clause if expression is None else f"({expression}) AND {clause}"
    if expression is None and not ids:
        raise SearchError("Recherche vide" if not excluded else "Une exclusion (SAUF) demande aussi un critère à chercher")
    if excluded and expression is None:  # seulement des identifiants cherchés : l'exclusion porte sur eux
        expression = "(" + " OR ".join(f"id:{i}" for i in ids) + ")"
    for piece in excluded:
        expression = f"({expression}) ANDNOT {piece}"
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
            "journal_ref": " ".join((entry.findtext(f"{ARXIV_NS}journal_ref") or "").split()) or None,
            "doi": (entry.findtext(f"{ARXIV_NS}doi") or "").strip() or None,
        })
    return items, total


def _fetch(expression, ids, sort, start, count):
    sort_by, order = SORTS.get(sort, SORTS["relevance"])
    params = {"start": start, "max_results": count, "sortBy": sort_by, "sortOrder": order}
    if expression:
        params["search_query"] = expression
    if ids:
        params["id_list"] = ",".join(ids[:50])
    _wait_turn()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True) as client:
        try:
            response = with_retry(lambda: client.get(API, params=params))
        except httpx.TimeoutException:  # API d'arXiv parfois très lente (lundi matin…) : un second essai, plus patient
            time.sleep(3)
            response = with_retry(lambda: client.get(API, params=params, timeout=120))
    if response.status_code == 400:  # requête refusée par arXiv (syntaxe) : erreur de l'utilisateur, pas du réseau
        raise SearchError("Recherche refusée par arXiv : vérifiez les mots et les champs")
    response.raise_for_status()
    return parse(response.text)


def search(terms, categories=(), cross=True, dates=None, sort="relevance", page=0, api_key=None):
    expression, ids = build_query(terms, categories, dates)
    page = max(0, min(int(page), MAX_START // PAGE_SIZE - 1))
    wanted = {c for c in map(normalize_category, categories) if c}
    only_primary = wanted if wanted and not cross else None  # articles croisés exclus
    if sort == "profile" or only_primary:
        return _pool_page(expression, ids, only_primary, sort, page, api_key)
    start = page * PAGE_SIZE
    items, total = _fetch(expression, ids, sort, start, PAGE_SIZE)
    remember(items)
    # arXiv ne va pas au-delà de MAX_START résultats : dernière page atteignable
    last_page = (min(total, MAX_START) - 1) // PAGE_SIZE if total else 0
    return {"items": annotate(items), "total": total, "start": start, "pageSize": PAGE_SIZE, "page": page, "lastPage": last_page}


def _pool_page(expression, ids, only_primary, sort, page, api_key):
    """Page d'un lot de résultats lu d'un coup : reclassé selon le profil, et/ou sans les articles croisés."""
    size = FILTER_POOL if only_primary else POOL
    key = json.dumps([expression, ids, sorted(only_primary or []), sort])
    with _pools_lock:  # plusieurs requêtes à la fois (double clic sur « Suivants ») : cache protégé
        now = time.monotonic()
        for old in [k for k, (stamp, _, _) in _pools.items() if now - stamp > POOL_TTL]:
            del _pools[old]
        entry = _pools.get(key)
        if entry:
            _pools[key] = entry = (now, entry[1], entry[2])  # lot consulté : gardé plus longtemps
    if entry is None:
        items, total = _fetch(expression, ids, "relevance" if sort == "profile" else sort, 0, size)
        if only_primary:
            items = [i for i in items if i["primary_category"] in only_primary]
        remember(items)
        if sort == "profile":
            try:  # vecteurs de sens (Semantic Scholar) : proximité de sens en plus de celle du vocabulaire
                relevance.fetch_embeddings([relevance.s2_key(i["arxiv_id"]) for i in items], api_key)
            except Exception:
                pass
        ranked = annotate(items)
        if sort == "profile":
            ranked = sorted(ranked, key=lambda i: i.get("relevance") or 0, reverse=True)
        entry = (time.monotonic(), ranked, total)
        with _pools_lock:
            while len(_pools) >= 5:  # quelques recherches gardées seulement
                del _pools[min(_pools, key=lambda k: _pools[k][0])]
            _pools[key] = entry
    _, ranked, total = entry
    page = min(page, max(0, (len(ranked) - 1) // PAGE_SIZE))
    start = page * PAGE_SIZE
    # avis donnés depuis : relus (le classement, lui, reste celui du premier calcul)
    items = annotate_status(ranked[start:start + PAGE_SIZE])
    # lot complet (moins de résultats sur arXiv que la taille du lot) : le total est exact, sans « parmi »
    complete = total <= size
    return {"items": items, "total": len(ranked), "arxivTotal": total, "start": start, "pageSize": PAGE_SIZE,
            "page": page, "lastPage": max(0, (len(ranked) - 1) // PAGE_SIZE), "pool": None if complete else size, "profile": sort == "profile", "crossExcluded": bool(only_primary)}


def remember(items):
    """Résultats gardés (hors du feed : in_arxiv = 0) : un pouce levé ou baissé compte alors dans le profil."""
    with db.connect() as connection:
        connection.executemany(
            """INSERT OR IGNORE INTO feed_items (key, arxiv_id, version, title, authors, abstract, categories,
                   primary_category, announced, in_arxiv, link, journal_ref, doi) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
            [(i["key"], i["arxiv_id"], i["version"], i["title"], json.dumps(i["authors"], ensure_ascii=False),
              i["abstract"], json.dumps(i["categories"]), i["primary_category"], i["announced"], i["link"],
              i.get("journal_ref"), i.get("doi")) for i in items])


def annotate_status(items):
    """Article déjà lu ou dans la bibliothèque, avis donné (comme dans le feed)."""
    with db.connect() as connection:
        papers = connection.execute("SELECT id, arxiv_id, in_library FROM papers").fetchall()
        status = {r["key"]: r["status"] for r in connection.execute("SELECT key, status FROM feed_status")}
    library = {r["arxiv_id"]: r["id"] for r in papers if r["in_library"]}
    cached = {r["arxiv_id"]: r["id"] for r in papers}
    return [{**item, "status": status.get(item["key"]), "libraryId": library.get(item["arxiv_id"]),
             "readId": cached.get(item["arxiv_id"])} for item in items]


def annotate(items):
    """Article déjà lu ou dans la bibliothèque, avis donné : comme dans le feed ; proximité avec le profil."""
    items = [{**item, "citing": []} for item in annotate_status(items)]
    try:
        journals.annotate(items)
    except Exception:  # revue inconnue : résultats sans elle
        pass
    try:
        return relevance.score_items(items)
    except Exception:  # vecteurs de sens indisponibles : résultats sans proximité
        return items
