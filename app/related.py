"""Papiers liés à un article (à la manière de Connected Papers), d'après Semantic Scholar.

Candidats : les références de l'article et les articles qui le citent. Proximité de chacun avec lui :
références communes (couplage bibliographique) et articles qui les citent ensemble (co-citation), plus un
peu pour le lien direct. Les plus proches (30) forment le graphe ; deux d'entre eux sont reliés selon leurs
références communes (ou si l'un cite l'autre). Le graphe est gardé quinze jours.

Requêtes : ses références, ses citations, puis les références des candidats par lots de 500 (l'article
lui-même est décrit d'après arXiv : une requête de moins à Semantic Scholar, dont le quota sans clé est partagé).
"""

import difflib
import itertools
import json
import re
import threading
import time
import unicodedata

import httpx

from . import db
from .arxiv import fetch_metadata, with_retry
from .config import USER_AGENT

S2 = "https://api.semanticscholar.org/graph/v1"
FIELDS = "paperId,title,year,citationCount,externalIds,authors"
NODES = 30
CANDIDATES = 1200     # au-delà (article très cité), seules les 1000 citations rendues par S2 et les références comptent
KEEP_DAYS = 15
SCHEMA = """CREATE TABLE IF NOT EXISTS related_graphs (
    arxiv_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    built_at TEXT DEFAULT (datetime('now'))
)"""
_lock = threading.Lock()  # un graphe construit à la fois (Semantic Scholar limite les requêtes)


class RelatedError(Exception):
    pass


def _ensure():
    with db.connect() as connection:
        connection.execute(SCHEMA)


RETRIES = (3, 6, 12, 20)  # sans clé, le quota de Semantic Scholar est partagé : on patiente plus longtemps


def _get(client, url, **params):
    response = with_retry(lambda: client.get(url, params=params), retry_on=(429,), delays=RETRIES)
    if response.status_code == 404:
        raise RelatedError("not-found")
    if response.status_code == 429:
        raise RelatedError("rate-limited")
    response.raise_for_status()
    return response.json()


def _paper(raw):
    """Article réduit à ce qu'affiche la page."""
    ids = raw.get("externalIds") if isinstance(raw.get("externalIds"), dict) else {}
    authors = [str(a.get("name") or "") for a in raw.get("authors") or [] if isinstance(a, dict)]
    return {"id": raw["paperId"], "arxivId": ids.get("ArXiv") or None, "doi": ids.get("DOI") or None,
            "title": " ".join(str(raw.get("title") or "").split()), "year": raw.get("year"),
            "citations": raw.get("citationCount") or 0, "authors": [a for a in authors if a][:12]}


def _norm(title):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower())


def _same(a, b):
    """Doublons de Semantic Scholar (même article sous deux fiches, titre à peine différent : « anggl(N) » et
    « and gl(N) ») : même année et titres presque identiques."""
    if (a["year"] or 0) != (b["year"] or 0):
        return False
    x, y = _norm(a["title"]), _norm(b["title"])
    return x == y or (min(len(x), len(y)) > 20 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.9)


def build(arxiv_id, api_key=None):
    """Graphe des papiers liés à l'article arXiv `arxiv_id` (sans version)."""
    if not _lock.acquire(timeout=120):
        raise RelatedError("busy")
    try:
        return _build(arxiv_id, api_key)
    finally:
        _lock.release()


def _build(arxiv_id, api_key):
    headers = {"User-Agent": USER_AGENT, **({"x-api-key": api_key} if api_key else {})}
    try:
        meta = fetch_metadata(arxiv_id)  # titre, auteurs, année : d'après arXiv
        origin = {"id": f"arXiv:{arxiv_id}", "arxivId": arxiv_id, "doi": meta.doi or None, "title": meta.title,
                  "year": int(meta.published[:4]) if meta.published[:4].isdigit() else None, "citations": 0, "authors": meta.authors[:12]}
    except Exception:
        origin = {"id": f"arXiv:{arxiv_id}", "arxivId": arxiv_id, "doi": None, "title": f"arXiv:{arxiv_id}", "year": None, "citations": 0, "authors": []}
    with httpx.Client(headers=headers, timeout=60) as client:
        pid = f"arXiv:{arxiv_id}"
        refs = _get(client, f"{S2}/paper/{pid}/references", limit=1000, fields=FIELDS).get("data") or []
        time.sleep(0 if api_key else 1.2)  # sans clé : au plus une requête par seconde
        cites = _get(client, f"{S2}/paper/{pid}/citations", limit=1000, fields=FIELDS).get("data") or []
        origin["citations"] = len(cites)
        candidates = {}
        for rel, rows, key in (("ref", refs, "citedPaper"), ("cite", cites, "citingPaper")):
            for row in rows:
                raw = row.get(key) if isinstance(row, dict) else None
                if isinstance(raw, dict) and raw.get("paperId") and raw["paperId"] not in candidates:
                    candidates[raw["paperId"]] = {**_paper(raw), "rel": rel}
                if len(candidates) >= CANDIDATES:
                    break
        # références de chaque candidat (pour les références communes et les co-citations)
        refs_of = {}
        ids = list(candidates)
        for start in range(0, len(ids), 500):
            time.sleep(0 if api_key else 1.2)
            batch = ids[start:start + 500]
            response = with_retry(lambda: client.post(f"{S2}/paper/batch", params={"fields": "paperId,references.paperId"}, json={"ids": batch}),
                                  retry_on=(429,), delays=RETRIES)
            if response.status_code == 429:
                raise RelatedError("rate-limited")
            response.raise_for_status()
            for key, item in zip(batch, response.json()):
                refs_of[key] = {r["paperId"] for r in (item or {}).get("references") or [] if isinstance(r, dict) and r.get("paperId")}
    origin_refs = {c for c in candidates if candidates[c]["rel"] == "ref"}
    citers = [c for c in candidates if candidates[c]["rel"] == "cite"]
    for key, paper in candidates.items():
        own = refs_of.get(key, set())
        coupling = len(own & origin_refs)                                # références communes avec l'article
        cocited = sum(1 for citer in citers if key in refs_of.get(citer, ()))  # cités ensemble
        paper["score"] = round(coupling + cocited + 0.5, 1)
        paper["coupling"], paper["cocited"] = coupling, cocited
    ranked = sorted(candidates.values(), key=lambda p: (-p["score"], -p["citations"]))
    nodes = []
    for paper in ranked:
        if any(_same(paper, kept) for kept in nodes):
            continue  # doublon d'une fiche déjà retenue (la plus proche)
        nodes.append(paper)
        if len(nodes) == NODES:
            break
    edges = []
    for a, b in itertools.combinations(nodes, 2):
        ra, rb = refs_of.get(a["id"], set()), refs_of.get(b["id"], set())
        strength = len(ra & rb) + (3 if b["id"] in ra or a["id"] in rb else 0)
        if strength >= 3:
            edges.append([a["id"], b["id"], strength])
    return {"origin": origin, "nodes": nodes, "edges": edges,
            "candidates": len(candidates), "withReferences": sum(1 for k in candidates if refs_of.get(k))}


def graph(arxiv_id, api_key=None, refresh=False):
    """Graphe gardé (moins de 15 jours), sinon construit et gardé."""
    _ensure()
    if not refresh:
        with db.connect() as connection:
            row = connection.execute(
                f"SELECT data, built_at FROM related_graphs WHERE arxiv_id = ? AND built_at > datetime('now', '-{KEEP_DAYS} days')",
                (arxiv_id,)).fetchone()
        if row:
            return {**json.loads(row["data"]), "builtAt": row["built_at"]}
    data = build(arxiv_id, api_key)
    with db.connect() as connection:
        connection.execute("INSERT INTO related_graphs (arxiv_id, data, built_at) VALUES (?, ?, datetime('now')) "
                           "ON CONFLICT(arxiv_id) DO UPDATE SET data = excluded.data, built_at = excluded.built_at",
                           (arxiv_id, json.dumps(data, ensure_ascii=False)))
        built = connection.execute("SELECT built_at FROM related_graphs WHERE arxiv_id = ?", (arxiv_id,)).fetchone()["built_at"]
    return {**data, "builtAt": built}


def annotate(data):
    """Articles déjà dans la bibliothèque (ou seulement lus) : marqués, avec leur identifiant."""
    with db.connect() as connection:
        papers = connection.execute("SELECT id, arxiv_id, in_library FROM papers").fetchall()
    library = {r["arxiv_id"]: r["id"] for r in papers if r["in_library"]}
    read = {r["arxiv_id"]: r["id"] for r in papers}
    for paper in [data["origin"], *data["nodes"]]:
        arxiv = paper.get("arxivId")
        paper["libraryId"] = library.get(arxiv) if arxiv else None
        paper["readId"] = read.get(arxiv) if arxiv else None
    return data
