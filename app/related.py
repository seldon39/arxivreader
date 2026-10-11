"""Papiers liés à un article (à la manière de Connected Papers), d'après Semantic Scholar.

Candidats : les références de l'article, les articles qui le citent, et les articles souvent cités par ces
voisins directs (second niveau). Proximité de chacun avec lui : références communes (couplage bibliographique)
et articles qui les citent ensemble (co-citation), rapportées à la taille des listes, plus un peu pour le lien
direct. Les plus proches (40) forment le graphe ; deux d'entre eux sont reliés selon leurs références communes
(ou si l'un cite l'autre). À côté : les travaux antérieurs (les plus cités par le graphe) et dérivés (ceux qui
citent le plus d'articles du graphe). Le graphe est gardé quinze jours.

Requêtes : ses références, ses citations, les références des candidats par lots de 500, puis les fiches du
second niveau (l'article lui-même est décrit d'après arXiv : une requête de moins à Semantic Scholar, dont le
quota sans clé est partagé).
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
from .arxiv import fetch_metadata, fetch_titles, with_retry
from .config import USER_AGENT
from .feed import tex_text

S2 = "https://api.semanticscholar.org/graph/v1"
FIELDS = "paperId,title,year,citationCount,externalIds,authors"
NODES = 40
SECOND = 300        # voisins de second niveau examinés (une requête de plus)
EDGES = 4           # liens gardés par article (les plus forts)
SIDE = 15           # travaux antérieurs et dérivés
DIRECT = 0.05       # proximité ajoutée pour un lien direct (référence de l'article, ou article qui le cite)
COCITED = 0.35      # co-citations rapportées à (citations · citations de l'article) ** COCITED
CANDIDATES = 1200     # au-delà (article très cité), seules les 1000 citations rendues par S2 et les références comptent
KEEP_DAYS = 15
FORMAT = 4           # forme des graphes gardés (2 : second niveau, travaux antérieurs et dérivés ; 3 : tous les liens ; 4 : titres d'arXiv)
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


def _answer(response):
    """Réponse JSON de Semantic Scholar, ou une erreur dont la page dit la cause."""
    if response.status_code == 404:
        raise RelatedError("not-found")
    if response.status_code == 429:
        raise RelatedError("rate-limited")
    if response.status_code in (500, 502, 503, 504):
        raise RelatedError("unavailable")
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:  # page HTML (proxy, panne) au lieu du JSON attendu
        raise RelatedError("bad-response")


def _get(client, url, **params):
    data = _answer(with_retry(lambda: client.get(url, params=params), retry_on=(429, 503, 504), delays=RETRIES))
    if not isinstance(data, dict):
        raise RelatedError("bad-response")
    return data


def _paper(raw):
    """Article réduit à ce qu'affiche la page."""
    ids = raw.get("externalIds") if isinstance(raw.get("externalIds"), dict) else {}
    authors = [str(a.get("name") or "") for a in raw.get("authors") or [] if isinstance(a, dict)]
    return {"id": raw["paperId"], "arxivId": ids.get("ArXiv") or None, "doi": ids.get("DOI") or None,
            "title": " ".join(str(raw.get("title") or "").split()), "year": raw.get("year"),
            "citations": raw.get("citationCount") or 0, "authors": [a for a in authors if a][:12]}


def _norm(title):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower())


# numéros de partie : chiffres romains, ou nombre en fin de titre ou après « part », « vol. »… (pas « N → 1 »)
NUMBERS = re.compile(r"\b[IVX]+\b|(?:\b(?:[Pp]art|[Pp]artie|[Tt]eil|[Vv]ol(?:ume)?|[Nn]o)\.?\s*)\d+\b|\b\d+\s*$")
PART = re.compile(r"\b(?:[IVX]+|[Pp]art|[Pp]artie|[Tt]eil)\b")


def _surnames(paper):
    return {_norm(name.split()[-1]) for name in paper.get("authors") or [] if name.split()}


def _completed(a, b):
    """Un titre et le même suivi d'un complément (« The spectral theory of Toeplitz operators. (AM-99), Volume 99 ») :
    même article si les auteurs se recoupent et si le complément n'annonce pas une autre partie (« : part II »)."""
    x, y = sorted((" ".join(a["title"].split()), " ".join(b["title"].split())), key=len)
    x = x.rstrip(" .")
    if len(x.split()) < 4 or not y.lower().startswith(x.lower()):
        return False
    rest = y[len(x):]
    if not re.match(r"\s*[.(:;,\u2013\u2014-]", rest) or PART.search(rest):
        return False
    return bool(_surnames(a) & _surnames(b)) or not (a.get("authors") and b.get("authors"))


def _same(a, b):
    """Doublons de Semantic Scholar (même article sous deux fiches, titre à peine différent : « anggl(N) » et
    « and gl(N) ») : même année et titres presque identiques, avec les mêmes numéros (« I » et « II », « part 1 »
    et « part 2 » restent deux articles), ou un titre suivi d'un complément ; jamais deux identifiants arXiv ou
    DOI différents."""
    if (a["year"] or 0) != (b["year"] or 0):
        return False
    for key in ("arxivId", "doi"):
        if a.get(key) and b.get(key) and a[key].lower() != b[key].lower():
            return False
    if _completed(a, b):
        return True
    if NUMBERS.findall(a["title"]) != NUMBERS.findall(b["title"]):
        return False
    x, y = _norm(a["title"]), _norm(b["title"])
    return x == y or (min(len(x), len(y)) > 20 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.9)


def build(arxiv_id, api_key=None):
    """Graphe des papiers liés à l'article arXiv `arxiv_id` (sans version). Les articles affichés qui sont sur
    arXiv y prennent leur titre et leurs auteurs (Semantic Scholar en abîme parfois : « anggl(N) »), puis le
    graphe est recalculé (les doublons se reconnaissent mieux)."""
    origin, candidates, refs_of = fetch(arxiv_id, api_key)
    first = compute(origin, candidates, refs_of)
    shown = {p["arxivId"] for p in [*first["nodes"], *first["prior"], *first["derivative"]] if p.get("arxivId")}
    try:
        titles = fetch_titles(sorted(shown)) if shown else {}
    except Exception:
        return first  # arXiv injoignable : titres de Semantic Scholar
    for paper in candidates.values():
        if paper.get("arxivId") in titles:
            title, authors = titles[paper["arxivId"]]
            paper["title"], paper["authors"] = tex_text(title), [a for a in authors if a][:12] or paper["authors"]  # « -- » → « – »…
    return compute(origin, candidates, refs_of)


def _origin(arxiv_id):
    try:
        meta = fetch_metadata(arxiv_id)  # titre, auteurs, année : d'après arXiv
        return {"id": f"arXiv:{arxiv_id}", "arxivId": arxiv_id, "doi": meta.doi or None, "title": tex_text(meta.title),
                "year": int(meta.published[:4]) if meta.published[:4].isdigit() else None, "citations": 0, "authors": meta.authors[:12]}
    except Exception:
        return {"id": f"arXiv:{arxiv_id}", "arxivId": arxiv_id, "doi": None, "title": f"arXiv:{arxiv_id}", "year": None, "citations": 0, "authors": []}


def _batch(client, ids, fields, api_key):
    """Fiches de Semantic Scholar pour `ids`, par lots de 500 (dans l'ordre ; None pour une fiche inconnue)."""
    found = []
    for start in range(0, len(ids), 500):
        time.sleep(0 if api_key else 1.2)  # sans clé : au plus une requête par seconde
        batch = ids[start:start + 500]
        answer = _answer(with_retry(lambda: client.post(f"{S2}/paper/batch", params={"fields": fields}, json={"ids": batch}),
                                    retry_on=(429, 503, 504), delays=RETRIES))
        if not isinstance(answer, list):
            raise RelatedError("bad-response")
        found += [item if isinstance(item, dict) else None for item in answer[:len(batch)]]
    return found


def _ids(item):
    found = item.get("references") if isinstance(item, dict) else None
    return [r["paperId"] for r in found or [] if isinstance(r, dict) and r.get("paperId")]


def fetch(arxiv_id, api_key=None):
    """Données de Semantic Scholar : l'article, ses voisins directs (ses références, les articles qui le citent)
    et les articles souvent cités par ces voisins (voisins de second niveau), avec les références de chacun."""
    origin = _origin(arxiv_id)
    headers = {"User-Agent": USER_AGENT, **({"x-api-key": api_key} if api_key else {})}
    with httpx.Client(headers=headers, timeout=60) as client:
        pid = f"arXiv:{arxiv_id}"
        refs = _get(client, f"{S2}/paper/{pid}/references", limit=1000, fields=FIELDS).get("data") or []
        time.sleep(0 if api_key else 1.2)
        cites = _get(client, f"{S2}/paper/{pid}/citations", limit=1000, fields=FIELDS).get("data") or []
        candidates = {}
        for rel, rows, key in (("ref", refs, "citedPaper"), ("cite", cites, "citingPaper")):
            for row in rows:
                raw = row.get(key) if isinstance(row, dict) else None
                if isinstance(raw, dict) and raw.get("paperId") and raw["paperId"] not in candidates:
                    candidates[raw["paperId"]] = {**_paper(raw), "rel": rel}
                if len(candidates) >= CANDIDATES:
                    break
        origin["refs"] = [c for c in candidates if candidates[c]["rel"] == "ref"]
        origin["citations"] = sum(1 for c in candidates.values() if c["rel"] == "cite")
        origin["citationsCapped"] = len(cites) >= 1000  # Semantic Scholar n'en rend que 1000 d'un coup
        ids = list(candidates)
        refs_of = {key: _ids(item) for key, item in zip(ids, _batch(client, ids, "paperId,references.paperId", api_key))}
        # second niveau : cités par au moins trois voisins directs (les plus cités d'entre eux)
        counts = {}
        for key in ids:
            for ref in set(refs_of[key]):
                if ref not in candidates:
                    counts[ref] = counts.get(ref, 0) + 1
        second = sorted((r for r, n in counts.items() if n >= 3), key=lambda r: -counts[r])[:SECOND]
        for key, item in zip(second, _batch(client, second, f"{FIELDS},references.paperId", api_key)):
            if item and item.get("paperId"):
                candidates[key] = {**_paper({**item, "paperId": key}), "rel": "second"}
                refs_of[key] = _ids(item)
    return origin, candidates, refs_of


def _share(shared, a, b, power=0.5):
    """Part commune rapportée aux deux tailles (indice de Salton pour power = 1/2)."""
    return shared / (a * b) ** power if shared and a and b else 0.0


def compute(origin, candidates, refs_of):
    """Proximité de chaque candidat avec l'article, rapportée à la taille des listes (un classique cité partout
    n'écrase pas un travail proche) : références communes rapportées aux deux listes de références (indice de
    Salton), co-citations aux citations des deux articles (moins fortement : les classiques restent présents),
    un peu plus pour un lien direct ; poids réglés pour retrouver au mieux les graphes de Connected Papers.
    Les plus proches forment le graphe ; à côté, les travaux antérieurs (les plus cités par le graphe) et
    dérivés (ceux qui citent le plus d'articles du graphe)."""
    refs_of = {key: set(found) for key, found in refs_of.items()}
    origin_refs = set(origin["refs"])
    citers = [c for c in candidates if candidates[c]["rel"] == "cite"]
    for key, paper in candidates.items():
        own = refs_of.get(key, set())
        coupling = len(own & origin_refs)                                   # références communes avec l'article
        cocited = sum(1 for citer in citers if key in refs_of.get(citer, ()))  # cités ensemble
        closeness = (_share(coupling, len(own), len(origin_refs)) + _share(cocited, max(paper["citations"], cocited), origin["citations"], COCITED)
                     + (DIRECT if paper["rel"] != "second" else 0))
        paper["score"] = round(100 * closeness, 1)
        paper["coupling"], paper["cocited"] = coupling, cocited
    ranked = sorted(candidates.values(), key=lambda p: (-p["score"], -p["citations"]))
    nodes, others = [], []
    for paper in ranked:
        if paper.get("arxivId") == origin["arxivId"] or _same({**paper, "arxivId": None, "doi": None}, {**origin, "doi": None}):
            continue  # une autre fiche de l'article lui-même (version publiée, doublon)
        if any(_same(paper, kept) for kept in nodes) or any(_same(paper, kept) for kept in others):
            continue  # doublon d'une fiche déjà retenue (la plus proche)
        (nodes if len(nodes) < NODES and paper["score"] > 0 else others).append(paper)
    # liens : tous, mais seuls les plus forts de chaque article (références communes rapportées aux deux listes,
    # ou l'un cite l'autre) sont dessinés d'emblée, pour que le graphe reste lisible ; les autres apparaissent quand
    # on choisit l'un des deux articles. `strength` (références communes, + 3 si citation) règle la distance
    pairs = {}
    for a, b in itertools.combinations(nodes, 2):
        ra, rb = refs_of.get(a["id"], set()), refs_of.get(b["id"], set())
        direct = b["id"] in ra or a["id"] in rb
        strength = len(ra & rb) + (3 if direct else 0)
        if strength >= 3:
            pairs[a["id"], b["id"]] = (_share(len(ra & rb), len(ra), len(rb)) + (0.2 if direct else 0), strength)
    in_graph = [n["id"] for n in nodes]
    kept = set()
    for node in in_graph:
        mine = sorted((pair for pair in pairs if node in pair), key=lambda pair: -pairs[pair][0])
        kept.update(mine[:EDGES])
    edges = [[a, b, pairs[a, b][1], int((a, b) in kept)] for a, b in pairs]
    # antérieurs : cités par le plus d'articles du graphe (l'article compris) ; dérivés : citent le plus d'articles du graphe
    cited_by = {p["id"]: [n for n in in_graph if p["id"] in refs_of.get(n, ())] + (["origin"] if p["id"] in origin_refs else []) for p in others}
    citing = {p["id"]: [n for n in in_graph if n in refs_of.get(p["id"], ())] + (["origin"] if p["rel"] == "cite" else []) for p in others}
    def side(links, keep):
        found = [{**p, "links": links[p["id"]]} for p in others if keep(p) and len(links[p["id"]]) >= 2]
        return sorted(found, key=lambda p: (-len(p["links"]), -p["citations"]))[:SIDE]
    prior = side(cited_by, lambda p: p["rel"] != "cite")
    derivative = side(citing, lambda p: p["rel"] == "cite")
    origin = {k: v for k, v in origin.items() if k != "refs"}
    return {"format": FORMAT, "origin": origin, "nodes": nodes, "edges": edges, "prior": prior, "derivative": derivative,
            "candidates": len(candidates), "withReferences": sum(1 for k in candidates if refs_of.get(k))}


def _cached(arxiv_id, since=None):
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT data, built_at FROM related_graphs WHERE arxiv_id = ? AND built_at > datetime('now', '-{KEEP_DAYS} days')"
            + (" AND built_at >= ?" if since else ""), (arxiv_id, *([since] if since else []))).fetchone()
    data = json.loads(row["data"]) if row else None
    if not data or data.get("format") != FORMAT:
        return None  # absent, ou construit par une version précédente (sans tous les liens, ou sans travaux antérieurs et dérivés)
    return {**data, "builtAt": row["built_at"]}


def normalize(arxiv_id):
    """Identifiant tel que Semantic Scholar et la bibliothèque l'écrivent : sans la sous-catégorie des anciens
    identifiants (math.DG/0404494 → math/0404494)."""
    return re.sub(r"^([a-z-]+)\.[A-Z]{2}/", r"\1/", arxiv_id)


def graph(arxiv_id, api_key=None, refresh=False):
    """Graphe gardé (moins de 15 jours), sinon construit et gardé ; un seul construit à la fois (une demande
    arrivée pendant ce temps pour le même article reprend celui qui vient d'être fait)."""
    _ensure()
    arxiv_id = normalize(arxiv_id)
    if not refresh and (found := _cached(arxiv_id)):
        return found
    asked = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    if not _lock.acquire(timeout=90):
        raise RelatedError("busy")
    try:
        if found := _cached(arxiv_id, since=None if not refresh else asked):
            return found  # construit par une autre demande pendant l'attente
        data = build(arxiv_id, api_key)
        with db.connect() as connection:
            connection.execute("INSERT INTO related_graphs (arxiv_id, data, built_at) VALUES (?, ?, datetime('now')) "
                               "ON CONFLICT(arxiv_id) DO UPDATE SET data = excluded.data, built_at = excluded.built_at",
                               (arxiv_id, json.dumps(data, ensure_ascii=False)))
            built = connection.execute("SELECT built_at FROM related_graphs WHERE arxiv_id = ?", (arxiv_id,)).fetchone()["built_at"]
        return {**data, "builtAt": built}
    finally:
        _lock.release()


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
