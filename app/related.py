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

La page ne l'attend pas dans une seule requête : start() lance la construction en arrière-plan, status() dit où
elle en est (étape, lot), cancel() l'arrête (aussitôt pour la page ; pour le serveur, au prochain point de contrôle,
entre deux requêtes ou pendant une attente — rien n'est gardé).
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


class Cancelled(Exception):
    """Construction annulée depuis la page."""


class Job:
    """Construction d'un graphe en arrière-plan : état et étape lus par la page, annulation."""

    def __init__(self, arxiv_id):
        self.arxiv_id = arxiv_id
        self.state = "building"  # building, done, error, cancelled
        self.step, self.done, self.total = "waiting", 0, 0
        self.result = None
        self.error = None
        self.cancel = threading.Event()

    def view(self):
        return {"id": self.arxiv_id, "state": self.state, "step": self.step, "done": self.done, "total": self.total,
                "code": self.error, "graph": self.result if self.state == "done" else None}


_jobs = {}                      # identifiant arXiv → dernière construction
_jobs_lock = threading.Lock()
_current = threading.local()    # construction menée par ce fil (aucune : appel direct, tests)


def _step(name, done=0, total=0):
    """Étape de la construction en cours, vue par la page ; point de contrôle de l'annulation."""
    job = getattr(_current, "job", None)
    if job is None:
        return
    if job.cancel.is_set():
        raise Cancelled()
    job.step, job.done, job.total = name, done, total


def _wait(seconds):
    """Attente entre deux requêtes (quota de Semantic Scholar) ; une annulation l'interrompt aussitôt."""
    job = getattr(_current, "job", None)
    if job is None:
        time.sleep(seconds)
    elif job.cancel.wait(seconds):
        raise Cancelled()


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
    data = _answer(with_retry(lambda: client.get(url, params=params), retry_on=(429, 503, 504), delays=RETRIES, sleep=_wait))
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
    """Titre réduit à ses lettres et chiffres, sans les commandes TeX (le titre d'arXiv d'un article se compare
    ainsi à celui de Semantic Scholar)."""
    title = re.sub(r"\\[a-zA-Z]+", "", title).replace("$", "")
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower())


# numéros : nombres, chiffres romains (en capitales, ou de toute casse après « part » ou en fin de titre) ;
# « N ! 1 » est un « N → ∞ » mal lu par Semantic Scholar, pas un numéro
NUMBERS = re.compile(r"\b\d+\b|\b[IVX]+\b|(?i:(?<=\bpart )[ivx]+\b|\b[ivx]+\s*$)")
GARBLED = re.compile(r"(?:!|→)\s*1\b")
PART = re.compile(r"\b(?:[IVX]+|[Pp]art|[Pp]artie|[Tt]eil)\b")
# complément qui annonce un autre texte que l'article (« X: erratum », « X: the singular case »)
OTHER_TEXT = re.compile(r"(?i)\b(?:errat\w*|addend\w*|corrigend\w*|correction|reply|comment\w*|appendix|supplement\w*|case|cases)\b")


def _numbers(title):
    return [n.lower() for n in NUMBERS.findall(GARBLED.sub("→∞", title))]


def _surnames(paper):
    return {_norm(name.split()[-1]) for name in paper.get("authors") or [] if name.split()}


def _completed(a, b):
    """Un titre et le même suivi d'un complément (« The spectral theory of Toeplitz operators. (AM-99), Volume 99 ») :
    même article si les auteurs se recoupent et si le complément n'annonce pas une autre partie (« : part II ») ou
    un autre texte (« : erratum »)."""
    x, y = sorted((" ".join(a["title"].split()), " ".join(b["title"].split())), key=len)
    x = x.rstrip(" .")
    if len(x.split()) < 4 or not y.lower().startswith(x.lower()):
        return False
    rest = y[len(x):]
    if not re.match(r"\s*[.(:;,–—-]", rest) or PART.search(rest) or OTHER_TEXT.search(rest):
        return False
    return bool(_surnames(a) & _surnames(b))


def _same(a, b):
    """Doublons de Semantic Scholar (même article sous deux fiches, titre à peine différent : « anggl(N) » et
    « and gl(N) ») : même année et titres presque identiques, avec les mêmes numéros (« I » et « II », « part 1 »
    et « part 2 », « SU(2) » et « SU(3) » restent deux articles), ou un titre suivi d'un complément ; jamais deux
    identifiants arXiv ou DOI différents, ni deux titres trop courts pour être comparés (« Preface »)."""
    if (a["year"] or 0) != (b["year"] or 0):
        return False
    for key in ("arxivId", "doi"):
        if a.get(key) and b.get(key) and a[key].lower() != b[key].lower():
            return False
    x, y = _norm(a["title"]), _norm(b["title"])
    if min(len(x), len(y)) < 12:
        return False  # titre court, ou en cyrillique, en chinois… (réduit à presque rien)
    if _completed(a, b):
        return True
    if _numbers(a["title"]) != _numbers(b["title"]):
        return False
    if x == y:  # même titre : mêmes auteurs (sauf fiche sans auteurs)
        return bool(_surnames(a) & _surnames(b)) or not (a.get("authors") and b.get("authors"))
    return min(len(x), len(y)) > 20 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.9


def build(arxiv_id, api_key=None):
    """Graphe des papiers liés à l'article arXiv `arxiv_id` (sans version). Les articles affichés qui sont sur
    arXiv y prennent leur titre et leurs auteurs (Semantic Scholar en abîme parfois : « anggl(N) »), puis le
    graphe est recalculé (les doublons se reconnaissent mieux)."""
    origin, candidates, refs_of = fetch(arxiv_id, api_key)
    _step("compute")
    data = compute(origin, candidates, refs_of)
    asked = set()
    for _ in range(2):  # un second tour pour les articles qu'aurait fait apparaître le premier
        shown = {p["arxivId"] for p in [*data["nodes"], *data["prior"], *data["derivative"]] if p.get("arxivId")} - asked
        if not shown:
            break
        _step("titles")
        try:
            titles = fetch_titles(sorted(shown))
        except Exception:
            return {**data, "degraded": True}  # arXiv injoignable : titres de Semantic Scholar (graphe non gardé)
        asked |= shown
        for paper in candidates.values():
            if paper.get("arxivId") in titles:
                title, authors = titles[paper["arxivId"]]
                paper["title"], paper["authors"] = tex_text(title), [a for a in authors if a][:12] or paper["authors"]  # « -- » → « – »…
        _step("compute")
        data = compute(origin, candidates, refs_of)
    if origin.get("unknown"):
        data["degraded"] = True  # arXiv n'a pas décrit l'article : ses autres fiches ne sont pas reconnues
    return data


def _origin(arxiv_id):
    try:
        meta = fetch_metadata(arxiv_id)  # titre, auteurs, année : d'après arXiv
        return {"id": f"arXiv:{arxiv_id}", "arxivId": arxiv_id, "doi": meta.doi or None, "title": tex_text(meta.title),
                "year": int(meta.published[:4]) if meta.published[:4].isdigit() else None, "citations": 0, "authors": meta.authors[:12]}
    except Exception:
        return {"id": f"arXiv:{arxiv_id}", "arxivId": arxiv_id, "doi": None, "title": f"arXiv:{arxiv_id}", "year": None, "citations": 0, "authors": [],
                "unknown": True}


def _batch(client, ids, fields, api_key, step):
    """Fiches de Semantic Scholar pour `ids`, par lots de 500 (dans l'ordre ; None pour une fiche inconnue)."""
    found = []
    lots = (len(ids) + 499) // 500
    for start in range(0, len(ids), 500):
        _step(step, start // 500 + 1, lots)
        _wait(_pause(api_key))
        batch = ids[start:start + 500]
        answer = _answer(with_retry(lambda: client.post(f"{S2}/paper/batch", params={"fields": fields}, json={"ids": batch}),
                                    retry_on=(429, 503, 504), delays=RETRIES, sleep=_wait))
        if not isinstance(answer, list):
            raise RelatedError("bad-response")
        answer = answer[:len(batch)] + [None] * (len(batch) - len(answer))  # réponse écourtée : fiches inconnues
        found += [item if isinstance(item, dict) else None for item in answer]
    return found


def _pause(api_key):
    """Une requête par seconde au plus : quota partagé sans clé, et quota d'une clé nouvelle."""
    return 1.05 if api_key else 1.2


def _ids(item):
    found = item.get("references") if isinstance(item, dict) else None
    return [r["paperId"] for r in found or [] if isinstance(r, dict) and r.get("paperId")]


def fetch(arxiv_id, api_key=None):
    """Données de Semantic Scholar : l'article, ses voisins directs (ses références, les articles qui le citent)
    et les articles souvent cités par ces voisins (voisins de second niveau), avec les références de chacun."""
    _step("origin")
    origin = _origin(arxiv_id)
    headers = {"User-Agent": USER_AGENT, **({"x-api-key": api_key} if api_key else {})}
    with httpx.Client(headers=headers, timeout=60) as client:
        pid = f"arXiv:{arxiv_id}"
        _step("references")
        refs = _get(client, f"{S2}/paper/{pid}/references", limit=1000, fields=FIELDS).get("data") or []
        _wait(_pause(api_key))
        _step("citations")
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
        refs_of = {key: _ids(item) for key, item in zip(ids, _batch(client, ids, "paperId,references.paperId", api_key, "neighbours"))}
        # second niveau : cités par au moins trois voisins directs (les plus cités d'entre eux)
        counts = {}
        for key in ids:
            for ref in set(refs_of[key]):
                if ref not in candidates:
                    counts[ref] = counts.get(ref, 0) + 1
        second = sorted((r for r, n in counts.items() if n >= 3), key=lambda r: -counts[r])[:SECOND]
        for key, item in zip(second, _batch(client, second, f"{FIELDS},references.paperId", api_key, "second")):
            if item and item.get("paperId"):
                candidates[key] = {**_paper({**item, "paperId": key}), "rel": "second"}
                refs_of[key] = _ids(item)
    return origin, candidates, refs_of


def _share(shared, a, b, power=0.5):
    """Part commune rapportée aux deux tailles (indice de Salton pour power = 1/2)."""
    return shared / (a * b) ** power if shared and a and b else 0.0


def _merge(origin, candidates, refs_of):
    """Doublons regroupés avant le calcul : chaque groupe garde une fiche (de préférence celle d'arXiv, puis un
    voisin direct, puis la plus citée), qui reçoit les références de toutes et les citations faites à chacune ;
    les autres fiches de l'article lui-même disparaissent. Comparaisons par année (un doublon a la même)."""
    alias = {}  # fiche → fiche gardée (None : l'article lui-même)
    me = {**origin, "doi": None}
    for key, paper in candidates.items():
        if paper.get("arxivId") == origin["arxivId"] or _same({**paper, "arxivId": None, "doi": None}, me):
            alias[key] = None
    order = sorted((k for k in candidates if k not in alias), key=lambda k: (not candidates[k].get("arxivId"),
                   candidates[k]["rel"] == "second", -(candidates[k]["citations"] or 0)))
    kept, by_year = {}, {}
    for key in order:
        paper = candidates[key]
        bucket = by_year.setdefault(paper["year"] or 0, [])
        twin = next((k for k in bucket if _same(paper, kept[k])), None)
        if twin is None:
            kept[key] = {**paper}
            bucket.append(key)
            continue
        alias[key] = twin
        main = kept[twin]
        main["citations"] = max(main["citations"] or 0, paper["citations"] or 0)
        if main["rel"] == "second" and paper["rel"] != "second":
            main["rel"] = paper["rel"]  # lien direct avec l'article
        for field in ("arxivId", "doi"):
            main[field] = main.get(field) or paper.get(field)
    to = lambda k: alias.get(k, k)
    refs = {}
    for key, found in refs_of.items():
        target = to(key)
        if target is not None:
            refs.setdefault(target, set()).update(r for r in map(to, found) if r is not None and r != target)
    origin_refs = {r for r in map(to, origin["refs"]) if r is not None}
    return kept, refs, origin_refs


def compute(origin, candidates, refs_of):
    """Proximité de chaque candidat avec l'article, rapportée à la taille des listes (un classique cité partout
    n'écrase pas un travail proche) : références communes rapportées aux deux listes de références (indice de
    Salton), co-citations aux citations des deux articles (moins fortement : les classiques restent présents),
    un peu plus pour un lien direct ; poids réglés pour retrouver au mieux les graphes de Connected Papers.
    Les plus proches forment le graphe ; à côté, les travaux antérieurs (les plus cités par le graphe) et
    dérivés (ceux qui citent le plus d'articles du graphe)."""
    candidates, refs_of, origin_refs = _merge(origin, candidates, refs_of)
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
    if getattr(_current, "job", None) is None:
        if not _lock.acquire(timeout=90):
            raise RelatedError("busy")
    else:  # en arrière-plan : attente d'un autre graphe en construction, annulable
        while not _lock.acquire(timeout=0.5):
            _step("waiting")
    try:
        if found := _cached(arxiv_id, since=None if not refresh else asked):
            return found  # construit par une autre demande pendant l'attente
        data = build(arxiv_id, api_key)
        _step("compute")  # annulée entre-temps : rien n'est gardé
        if data.get("degraded") or not data["nodes"]:
            return {**data, "builtAt": None}  # incomplet (arXiv injoignable) ou vide (article trop récent) : pas gardé
        with db.connect() as connection:
            connection.execute("INSERT INTO related_graphs (arxiv_id, data, built_at) VALUES (?, ?, datetime('now')) "
                               "ON CONFLICT(arxiv_id) DO UPDATE SET data = excluded.data, built_at = excluded.built_at",
                               (arxiv_id, json.dumps(data, ensure_ascii=False)))
            built = connection.execute("SELECT built_at FROM related_graphs WHERE arxiv_id = ?", (arxiv_id,)).fetchone()["built_at"]
        return {**data, "builtAt": built}
    finally:
        _lock.release()


def start(arxiv_id, api_key=None, refresh=False):
    """Graphe gardé : rendu tout de suite ; sinon construit en arrière-plan (la page suit status()). Une demande
    pour un article déjà en construction suit celle-ci."""
    _ensure()
    arxiv_id = normalize(arxiv_id)
    if not refresh and (found := _cached(arxiv_id)):
        return {"id": arxiv_id, "state": "done", "graph": found}
    with _jobs_lock:
        job = _jobs.get(arxiv_id)
        if job and job.state == "building":
            return job.view()
        job = _jobs[arxiv_id] = Job(arxiv_id)
    threading.Thread(target=_run, args=(job, api_key, refresh), daemon=True, name=f"related {arxiv_id}").start()
    return job.view()


def _run(job, api_key, refresh):
    _current.job = job
    try:
        result = graph(job.arxiv_id, api_key, refresh)
        if not job.cancel.is_set():
            job.result, job.state = result, "done"
    except Cancelled:
        pass
    except Exception as error:
        if not job.cancel.is_set():  # annulée : l'erreur arrivée ensuite n'est plus montrée
            # imprévu : dit à la page plutôt qu'une construction sans fin
            job.error = str(error) if isinstance(error, RelatedError) else "network" if isinstance(error, httpx.HTTPError) \
                else f"{type(error).__name__}: {error}"[:300]
            job.state = "error"
    finally:
        _current.job = None


def status(arxiv_id):
    job = _jobs.get(normalize(arxiv_id))
    return job.view() if job else {"id": normalize(arxiv_id), "state": "none"}


def cancel(arxiv_id):
    """Annulée tout de suite pour la page ; le fil s'arrête au prochain point de contrôle, sans rien garder."""
    job = _jobs.get(normalize(arxiv_id))
    if job and job.state == "building":
        job.cancel.set()
        job.state = "cancelled"
    return job.view() if job else {"id": normalize(arxiv_id), "state": "none"}


def annotate(data):
    """Articles déjà dans la bibliothèque (ou seulement lus) : marqués, avec leur identifiant."""
    with db.connect() as connection:
        papers = connection.execute("SELECT id, arxiv_id, in_library FROM papers").fetchall()
    library = {r["arxiv_id"]: r["id"] for r in papers if r["in_library"]}
    read = {r["arxiv_id"]: r["id"] for r in papers}
    for paper in [data["origin"], *data["nodes"], *data.get("prior", []), *data.get("derivative", [])]:
        arxiv = paper.get("arxivId")
        paper["libraryId"] = library.get(arxiv) if arxiv else None
        paper["readId"] = read.get(arxiv) if arxiv else None
    return data
