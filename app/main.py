"""Serveur web local d'arXivReader (127.0.0.1 uniquement)."""

import asyncio
import json
import os
import re
import signal
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, alerts, annotations, citations, crossrefs, db, feed, folders, library, profile, providers, relevance, search, updates
from .arxiv import ArxivError, fetch_metadata, parse_arxiv_id
from .config import HOST, PORT, STATIC

ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
PAPER_ID = re.compile(r"[A-Za-z0-9.\-_]+")          # comparés en entier (fullmatch)
ASSET_NAME = re.compile(r"[0-9a-f]{16}(\.[a-z]+)?")

PDF_CSP = "default-src 'self'; frame-ancestors 'self'"
# lecteur PDF (pdf.js, dans un cadre de la page du lecteur) : WebAssembly pour certaines images, polices
# et images construites en mémoire, travailleur pour l'analyse du PDF ; rien hors de 127.0.0.1
PDFJS = "/static/vendor/pdfjs/"
PDFJS_CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'none'"
)
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


@asynccontextmanager
async def lifespan(_app):
    db.init()
    ai.stop_running()
    library.clean_cache()
    # vérification périodique des auteurs suivis (ARXIVREADER_NO_ALERTS=1 pour la couper, par ex. en test)
    alerts.stop.clear()
    task = None if os.environ.get("ARXIVREADER_NO_ALERTS") else asyncio.create_task(alerts.loop())
    if os.environ.get("ARXIVREADER_WATCH_PARENT"):
        threading.Thread(target=_watch_parent, args=(os.getppid(),), daemon=True).start()
    yield
    if task:
        alerts.stop.set()  # la vérification en cours s'arrête à l'auteur suivant
        task.cancel()
        try:
            await asyncio.wait_for(task, 5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


def _watch_parent(parent):
    """Application Mac fermée sans avoir pu arrêter le serveur (plantage, arrêt forcé) : il s'arrête aussi."""
    while os.getppid() == parent:
        time.sleep(2)
    os.kill(os.getpid(), signal.SIGTERM)


app = FastAPI(title="arXivReader", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Refuse les noms d'hôte étrangers (attaque par « DNS rebinding ») et les requêtes
    # d'écriture venant d'autres sites.
    if request.headers.get("host", "").lower() not in ALLOWED_HOSTS:
        return PlainTextResponse("Forbidden host", status_code=403)
    # Une page d'un autre site ne peut pas non plus déclencher l'API (même en GET : <img src=…>) ;
    # les navigateurs récents indiquent l'origine de la requête dans Sec-Fetch-Site.
    if request.url.path.startswith("/api/") and request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        return PlainTextResponse("Forbidden site", status_code=403)
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            return PlainTextResponse("Forbidden origin", status_code=403)
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if request.method != "DELETE" and content_type != "application/json":
            return PlainTextResponse("JSON expected", status_code=415)
    response = await call_next(request)
    # le PDF d'un article s'affiche dans un cadre de la page du lecteur (même origine)
    path = request.url.path
    response.headers["Content-Security-Policy"] = PDF_CSP if path.endswith("/pdf") else PDFJS_CSP if path.startswith(PDFJS) else CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def checked_paper(paper_id):
    if not PAPER_ID.fullmatch(paper_id):
        raise HTTPException(400, "Bad paper id")
    paper = db.get_paper(paper_id)
    if paper is None:
        raise HTTPException(404, "Unknown paper")
    return paper


class ImportRequest(BaseModel):
    query: str
    library: bool = True  # False : « Lire » depuis le feed, sans ajouter à la bibliothèque


class LibraryRequest(BaseModel):
    in_library: bool


class PositionRequest(BaseModel):
    anchor: str


class AnnotationRequest(BaseModel):
    kind: str
    target: dict | None = None
    color: str | None = None
    quote: str | None = None
    note: str | None = None
    state: str | None = None
    author: str | None = None  # « claude » : proposée par Claude, acceptée par vous


class SetupRequest(BaseModel):
    provider: str
    step: str  # login (connexion) ou missing (installation)


class AppliedRequest(BaseModel):
    applied: bool


class AnnotationUpdate(BaseModel):
    color: str | None = None
    note: str | None = None
    state: str | None = None
    clear_state: bool = False


class ProofChoice(BaseModel):
    mode: str
    ref: str | None = None
    start: str | None = None
    end: str | None = None


class AIRequest(BaseModel):
    task: str
    target: str | None = None
    quote: str | None = None
    question: str | None = None
    parent_id: int | None = None


class FindArxivRequest(BaseModel):
    key: str
    query: str  # lien ou identifiant arXiv collé


class RefreshRequest(BaseModel):
    view: str


class StatusRequest(BaseModel):
    key: str
    status: str | None = None


class LookupRequest(BaseModel):
    text: str


class ProfileItem(BaseModel):
    kind: str
    label: str
    ref: str | None = None
    authors: list[str] | None = None
    year: str | None = None
    text: str | None = None


class ResolveRequest(BaseModel):
    kind: str
    query: str


class PrefillRequest(BaseModel):
    owner: str


@app.get("/api/papers")
def papers():
    # avec, pour chaque article, où en est la lecture (notes, énoncés compris, réponses de Claude)
    notes, answers = annotations.stats(), ai.counts()
    empty = {"highlights": 0, "notes": 0, "done": 0}
    return [{**paper, "reading": {**notes.get(paper["id"], empty), "answers": answers.get(paper["id"], 0)}}
            for paper in db.list_papers()]



@app.get("/api/papers/relevance")
async def papers_relevance():
    """Pertinence de chaque article de la bibliothèque pour le profil (tri des dossiers)."""
    return await run_in_threadpool(relevance.library_relevance, db.get_settings().get("s2ApiKey") or None)

async def _import(query, add_to_library):
    try:
        return await run_in_threadpool(library.import_paper, query, add_to_library)
    except ArxivError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception as error:  # réseau, page inattendue…
        return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)


@app.post("/api/papers")
async def add_paper(body: ImportRequest):
    return await _import(body.query, body.library)


@app.get("/api/papers/{paper_id}")
def paper(paper_id: str):
    paper = checked_paper(paper_id)
    db.update_paper(paper_id, opened_at=_now())
    return {"paper": paper, "structure": library.load_structure(paper_id)}


@app.get("/api/papers/{paper_id}/content")
def content(paper_id: str):
    checked_paper(paper_id)
    path = library.paper_dir(paper_id) / "content.html"
    if not path.exists():
        raise HTTPException(404, "No HTML version")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@app.get("/api/papers/{paper_id}/assets/{name}")
def asset(paper_id: str, name: str):
    checked_paper(paper_id)
    if not ASSET_NAME.fullmatch(name):
        raise HTTPException(400, "Bad asset name")
    path = library.paper_dir(paper_id) / "assets" / name
    if not path.exists():
        raise HTTPException(404, "Missing asset")
    return FileResponse(path)


# ---------- Dossiers de la bibliothèque ----------

class FolderRequest(BaseModel):
    name: str
    papers: list[str] = []


class RenameRequest(BaseModel):
    name: str


def checked_folder(folder_id):
    folder = folders.get(folder_id)
    if folder is None:
        raise HTTPException(404, "Unknown folder")
    return folder


@app.get("/api/folders")
def list_folders():
    return folders.list_folders()


@app.post("/api/folders")
def create_folder(body: FolderRequest):
    for paper_id in body.papers:
        checked_paper(paper_id)
    try:
        return folders.create(body.name, body.papers)
    except folders.FolderError as error:
        raise HTTPException(409 if "déjà" in str(error) else 400, str(error))


@app.put("/api/folders/{folder_id}")
def rename_folder(folder_id: int, body: RenameRequest):
    checked_folder(folder_id)
    try:
        return folders.rename(folder_id, body.name)
    except folders.FolderError as error:
        raise HTTPException(409 if "déjà" in str(error) else 400, str(error))


@app.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: int):
    checked_folder(folder_id)
    return folders.delete(folder_id)


@app.put("/api/folders/{folder_id}/papers/{paper_id}")
def add_to_folder(folder_id: int, paper_id: str):
    checked_folder(folder_id)
    checked_paper(paper_id)
    return folders.set_member(folder_id, paper_id, True)


@app.delete("/api/folders/{folder_id}/papers/{paper_id}")
def remove_from_folder(folder_id: int, paper_id: str):
    checked_folder(folder_id)
    checked_paper(paper_id)
    return folders.set_member(folder_id, paper_id, False)


@app.put("/api/papers/{paper_id}/library")
def set_library(paper_id: str, body: LibraryRequest):
    checked_paper(paper_id)
    return library.set_in_library(paper_id, body.in_library)


@app.get("/api/papers/{paper_id}/pdf")
def paper_pdf(paper_id: str):
    """PDF de l'article (affiché dans le lecteur) ; téléchargé depuis arXiv la première fois."""
    path = library.ensure_pdf(checked_paper(paper_id))
    if path is None:
        raise HTTPException(404, "No PDF")
    return FileResponse(path, media_type="application/pdf")


@app.get("/api/papers/{paper_id}/pages")
def paper_pages(paper_id: str):
    """Endroit du PDF de chaque énoncé et section : {"pages": {id: page}, "tops": {id: hauteur}}."""
    pages = library.pdf_pages(checked_paper(paper_id))
    if pages is None:
        raise HTTPException(404, "No PDF")
    return pages


@app.get("/api/papers/{paper_id}/crosslinks")
def crosslinks(paper_id: str):
    """Références de la bibliographie qui sont des articles lus ici (avec leurs énoncés)."""
    checked_paper(paper_id)
    return crossrefs.links(paper_id)


@app.get("/api/papers/{paper_id}/statements/{node_id}")
def statement(paper_id: str, node_id: str):
    paper = checked_paper(paper_id)
    found = crossrefs.statement(paper_id, node_id[:200])
    if found is None:
        raise HTTPException(404, "Unknown statement")
    return {**found, "paper": paper_id, "title": paper["title"]}


@app.get("/api/papers/{paper_id}/annotations")
def list_annotations(paper_id: str):
    checked_paper(paper_id)
    return annotations.list_for(paper_id)


@app.post("/api/papers/{paper_id}/annotations")
def add_annotation(paper_id: str, body: AnnotationRequest):
    checked_paper(paper_id)
    try:
        return annotations.create(paper_id, body.kind, body.target, body.color, (body.quote or "")[:5000] or None,
                                  (body.note or "")[:100000] if body.note is not None else None, body.state, body.author)
    except ValueError:
        raise HTTPException(400, "Bad annotation")


@app.put("/api/annotations/{annotation_id}")
def edit_annotation(annotation_id: int, body: AnnotationUpdate):
    if not annotations.get(annotation_id):
        raise HTTPException(404, "Unknown annotation")
    try:
        return annotations.update(annotation_id, body.color, body.note[:100000] if body.note is not None else None,
                                  body.state, clear_state=body.clear_state)
    except ValueError:
        raise HTTPException(400, "Bad annotation")


@app.delete("/api/annotations/{annotation_id}")
def remove_annotation(annotation_id: int):
    annotations.delete(annotation_id)
    return {"ok": True}


@app.put("/api/papers/{paper_id}/proofs/{node_id}")
def set_proof_location(paper_id: str, node_id: str, body: ProofChoice):
    """Emplacement de la preuve d'un énoncé, indiqué à la main ; renvoie la structure mise à jour."""
    structure = _checked_node(paper_id, node_id)
    ids = [body.ref, body.start, body.end]
    if any(i is not None and not PAPER_ID.fullmatch(i) for i in ids):
        raise HTTPException(400, "Bad element id")
    try:
        annotations.set_proof(paper_id, node_id, body.mode, body.ref, body.start, body.end)
    except ValueError:
        raise HTTPException(400, "Bad proof location")
    return library.reanalyze(paper_id, structure)


@app.delete("/api/papers/{paper_id}/proofs/{node_id}")
def clear_proof_location(paper_id: str, node_id: str):
    structure = _checked_node(paper_id, node_id)
    annotations.clear_proof(paper_id, node_id)
    return library.reanalyze(paper_id, structure)


def _checked_node(paper_id, node_id):
    checked_paper(paper_id)
    structure = library.load_structure(paper_id)
    if not structure or not any(n["id"] == node_id for n in structure["nodes"]):
        raise HTTPException(404, "Unknown statement")
    return structure


@app.get("/api/ai/status")
async def ai_status():
    return await run_in_threadpool(ai.status, db.get_settings())


@app.post("/api/ai/setup")
def ai_setup(body: SetupRequest):
    """Installation ou connexion de l'assistant : commande prévue d'avance, lancée dans le Terminal."""
    try:
        providers.open_setup(body.provider, body.step, ai.cli_path())
    except ValueError:
        raise HTTPException(400, "Unknown setup step")
    except OSError as error:
        return JSONResponse({"error": str(error)}, status_code=500)
    return {"ok": True}


@app.get("/api/papers/{paper_id}/ai")
def list_ai(paper_id: str):
    checked_paper(paper_id)
    return ai.list_for(paper_id)


@app.post("/api/papers/{paper_id}/ai")
async def ask_ai(paper_id: str, body: AIRequest):
    """Réponse de Claude en direct : une ligne JSON par morceau de texte, puis l'échange enregistré."""
    paper = checked_paper(paper_id)
    structure = await run_in_threadpool(library.load_structure, paper_id) or {}  # ré-analyse éventuelle hors de la boucle
    settings = db.get_settings()
    parent = ai.get(body.parent_id) if body.parent_id else None
    if parent and parent["paper_id"] != paper_id:
        parent = None
    quote = (body.quote or "")[:20000] or None
    question = (body.question or "")[:20000] or None
    target = (body.target or "")[:200] or None
    try:
        model = ai.current_model(settings)
        system, prompt = await run_in_threadpool(ai.build_prompt, paper, structure, body.task, target, quote, question,
                                                 parent, settings.get("language", "fr"), ai.paper_limit(model))
    except ai.AIError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    exchange = ai.create(paper_id, body.task, target, quote, question, parent["id"] if parent else None, model)

    async def lines():
        answer = []
        status = "stopped"  # si la page interrompt la réponse
        try:
            yield json.dumps({"exchange": exchange}) + "\n"
            async for text in ai.stream_answer(system, prompt, model):
                answer.append(text)
                yield json.dumps({"delta": text}) + "\n"
            status = "done"
        except ai.AIError as error:
            status = "error"
            yield json.dumps({"error": str(error), "code": error.code}) + "\n"
        finally:
            final = ai.finish(exchange["id"], "".join(answer), status)
        yield json.dumps({"done": True, "exchange": final}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


@app.get("/api/ai/{exchange_id}/export")
def export_ai(exchange_id: int):
    exchange = ai.get(exchange_id)
    if not exchange:
        raise HTTPException(404, "Unknown exchange")
    paper = checked_paper(exchange["paper_id"])
    structure = library.load_structure(paper["id"]) or {}
    return {"text": ai.export_text(exchange, paper, structure, db.get_settings().get("language", "fr"))}


@app.put("/api/ai/{exchange_id}/applied")
def set_applied(exchange_id: int, body: AppliedRequest):
    if not ai.get(exchange_id):
        raise HTTPException(404, "Unknown exchange")
    return ai.set_applied(exchange_id, body.applied)


@app.delete("/api/ai/{exchange_id}")
def remove_ai(exchange_id: int):
    ai.delete(exchange_id)
    return {"ok": True}


@app.put("/api/papers/{paper_id}/position")
def save_position(paper_id: str, body: PositionRequest):
    checked_paper(paper_id)
    db.update_paper(paper_id, position=body.anchor[:200])
    return {"ok": True}


@app.post("/api/papers/{paper_id}/refresh")
async def refresh(paper_id: str):
    paper = checked_paper(paper_id)
    return await _import(paper["arxiv_id"], bool(paper["in_library"]))  # un article seulement lu le reste


@app.delete("/api/papers/{paper_id}")
def remove(paper_id: str):
    checked_paper(paper_id)
    library.remove_paper(paper_id)
    return {"ok": True}


@app.get("/api/feed")
async def feed_items(view: str = "arxiv", day: str | None = None):
    settings = db.get_settings()
    days = feed.known_days()
    if view == "arxiv" and day:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise ValueError(day)
            date.fromisoformat(day)
        except ValueError:
            raise HTTPException(400, "Bad day")
        # jour pas encore chargé, ou chargé sans les nouvelles versions alors que l'OAI-PMH peut les donner
        stale = day in (settings.get("feed.partialDays") or []) and feed.oai_available(day)
        if (day not in days or stale) and day <= _now()[:10]:
            try:
                await run_in_threadpool(feed.fetch_day, day, settings.get("feedCategories") or None,
                                        settings.get("s2ApiKey") or None)
            except Exception as error:
                return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)
            days = feed.known_days()
    latest = max((d for d, n in days.items() if n), default=None)
    settings = db.get_settings()
    return {
        "day": day or latest,
        # jour lu par l'API de recherche d'arXiv : sans les nouvelles versions
        "partial": (day or latest) in (settings.get("feed.partialDays") or []),
        "latestDay": latest,
        "days": days,
        "items": await run_in_threadpool(feed.list_items, view, ["new", "cross", "replace", "replace-cross"], day),
        "refreshed": {"arxiv": settings.get("feed.arxivRefreshed"), "recommended": settings.get("feed.s2Refreshed")},
        "seeds": settings.get("feed.s2Seeds"),
        "categories": settings.get("feedCategories") or feed.FEED_CATEGORIES,
    }


@app.post("/api/feed/refresh")
async def feed_refresh(body: RefreshRequest):
    settings = db.get_settings()
    try:
        if body.view == "recommended":
            count = await run_in_threadpool(feed.refresh_recommendations, settings.get("s2ApiKey") or None)
        else:
            count = await run_in_threadpool(feed.refresh_arxiv, settings.get("feedCategories") or None,
                                            settings.get("s2ApiKey") or None)
            try:  # citations de vos articles (une fois par jour au plus)
                await run_in_threadpool(citations.check, settings.get("s2ApiKey") or None)
            except Exception:
                pass
    except feed.FeedError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception as error:  # réseau
        return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)
    return {"count": count}


class SearchTerm(BaseModel):
    field: str = "all"
    value: str = ""
    op: str = "and"


class SearchRequest(BaseModel):
    terms: list[SearchTerm] = []
    categories: list[str] = []
    cross: bool = True
    dates: dict = {}
    sort: str = "relevance"
    page: int = 0


@app.post("/api/search")
async def search_arxiv(body: SearchRequest):
    """Recherche dans tout arXiv (pas seulement la bibliothèque) : résultats présentés comme le feed."""
    try:
        return await run_in_threadpool(search.search, [t.model_dump() for t in body.terms[:10]], body.categories[:20],
                                       body.cross, body.dates, body.sort, body.page, db.get_settings().get("s2ApiKey") or None)
    except search.SearchError as error:
        raise HTTPException(400, str(error))
    except Exception as error:  # réseau, arXiv indisponible
        return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)


@app.put("/api/feed/status")
def feed_status(body: StatusRequest):
    feed.set_status(body.key[:100], body.status)
    return {"ok": True}


@app.post("/api/feed/arxiv")
async def find_arxiv(body: FindArxivRequest):
    """Version arXiv d'une recommandation qui n'en a pas, d'après le lien donné."""
    item = feed.get_item(body.key[:100])
    if item is None or not item["key"].startswith("s2:"):
        raise HTTPException(404, "Unknown recommendation")
    try:
        arxiv_id, _ = parse_arxiv_id(body.query[:500])
        meta = await run_in_threadpool(fetch_metadata, arxiv_id)  # l'article existe bien
    except ArxivError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception as error:  # réseau
        return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)
    feed.link_arxiv(item["key"], arxiv_id)
    return {"arxiv_id": arxiv_id, "title": meta.title}  # titre montré pour vérifier qu'on a relié le bon article


@app.get("/api/alerts")
def get_alerts():
    return {"unseen": alerts.unseen()}


class SeenRequest(BaseModel):
    keys: list[str]


@app.get("/api/updates")
def get_updates():
    return updates.state()


@app.post("/api/updates/check")
def check_updates():
    return updates.state(force=True)


class DismissRequest(BaseModel):
    version: str


@app.post("/api/updates/dismiss")
def dismiss_update(body: DismissRequest):
    if not updates.parse(body.version):
        raise HTTPException(400, "Bad version")
    updates.dismiss(body.version)
    return updates.state()


@app.post("/api/alerts/seen")
def alerts_seen(body: SeenRequest):
    alerts.mark_seen([key[:100] for key in body.keys[:500]])
    return {"unseen": alerts.unseen()}


@app.post("/api/lookup")
async def lookup(body: LookupRequest):
    try:
        return await run_in_threadpool(feed.lookup_ids, body.text[:2_000_000])
    except Exception as error:
        return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)


@app.get("/api/profile")
def get_profile():
    positives, *_ = relevance.profile_documents()
    return {"items": profile.list_items(), "owner": db.get_settings().get("profileOwner", ""),
            "references": sum(1 for d in positives if d.get("reference"))}


@app.post("/api/profile/items")
async def add_profile_item(body: ProfileItem):
    ref = (body.ref or "")[:200] or None
    try:
        profile.add_item(body.kind, body.label[:400], ref, body.authors, body.year, (body.text or "")[:20000] or None)
    except ValueError:
        raise HTTPException(400, "Bad profile item")
    key = relevance.s2_key(None, ref) if ref else None
    if key:  # vecteur de sens tout de suite, pour que le tri en tienne compte
        try:
            await run_in_threadpool(relevance.fetch_embeddings, [key], db.get_settings().get("s2ApiKey") or None)
        except Exception:
            pass
    return await run_in_threadpool(get_profile)


@app.delete("/api/profile/items/{item_id}")
def remove_profile_item(item_id: int):
    profile.remove_item(item_id)
    return get_profile()


@app.post("/api/profile/resolve")
async def resolve_profile_item(body: ResolveRequest):
    try:
        return await run_in_threadpool(profile.resolve, body.kind, body.query[:500], db.get_settings().get("s2ApiKey") or None)
    except Exception as error:
        return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)


@app.post("/api/profile/prefill")
async def prefill_profile(body: PrefillRequest):
    db.save_settings({"profileOwner": body.owner.strip()[:200]})
    try:
        await run_in_threadpool(profile.prefill, body.owner.strip(), db.get_settings().get("s2ApiKey") or None)
    except Exception as error:
        return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)
    return get_profile()


@app.get("/api/settings")
def settings():
    return db.get_settings()


@app.put("/api/settings")
def update_settings(values: dict):
    allowed = {"language", "theme", "fontSize", "readingWidth", "proofsCollapsed", "tocOpen", "panelOpen", "panelWidth", "tocWidth", "paperFont", "marginMarks", "mathRenderer",
               "feedCategories", "feedTypes", "s2ApiKey", "feedSort", "feedSortS2", "aiModel", "notifyFollowed",
               "aiProvider", "aiLocalUrl", "aiLocalModel", "checkUpdates", "aiFontSize", "notifyCitations", "readerView",
               "citingCollapsed", "followedCollapsed", "geminiApiKey", "aiGeminiModel",
               "aiClaudeEffort", "aiCodexModel", "aiCodexEffort", "aiGeminiEffort"}
    values = {k: v for k, v in values.items() if k in allowed}
    if not all(_valid_setting(k, v) for k, v in values.items()):
        raise HTTPException(400, "Bad setting")
    categories = values.get("feedCategories")
    if categories is not None and not (isinstance(categories, list) and all(
            isinstance(c, str) and re.fullmatch(r"[a-z-]+(\.[A-Za-z-]+)?", c) for c in categories)):
        raise HTTPException(400, "Bad categories")
    db.save_settings(values)
    return db.get_settings()


SETTING_CHOICES = {
    "language": {"fr", "en"}, "theme": {"auto", "light", "dark"}, "readingWidth": {"narrow", "normal", "wide"},
    "paperFont": {"auto", "charter", "lm"}, "mathRenderer": {"mathjax", "native"},
    "aiProvider": set(providers.PROVIDERS), "readerView": {"html", "pdf"},
    "feedSort": {"relevance", "arxiv"}, "feedSortS2": {"relevance", "s2"},
}
BOOLEAN_SETTINGS = {"proofsCollapsed", "tocOpen", "panelOpen", "marginMarks", "notifyFollowed", "checkUpdates", "notifyCitations",
                    "citingCollapsed", "followedCollapsed"}


def _valid_setting(key, value):
    if key in SETTING_CHOICES:
        return isinstance(value, str) and value in SETTING_CHOICES[key]
    if key in BOOLEAN_SETTINGS:
        return isinstance(value, bool)
    if key == "aiFontSize":
        return isinstance(value, int) and 11 <= value <= 26
    if key == "fontSize":
        return isinstance(value, int) and 10 <= value <= 32
    if key in ("panelWidth", "tocWidth"):
        return value is None or (isinstance(value, (int, float)) and 100 <= value <= 2000)
    if key == "aiLocalUrl":
        return isinstance(value, str) and (value == "" or providers.valid_local_url(value))
    if key == "aiLocalModel":
        return isinstance(value, str) and len(value) <= 200 and value.isprintable()
    if key == "geminiApiKey":
        return value == "" or providers.valid_gemini_key(value)
    if key in ("aiGeminiModel", "aiCodexModel", "aiModel"):
        return providers.valid_model_name(value) and value not in ("codex", "gemini")
    if key in ("aiClaudeEffort", "aiCodexEffort", "aiGeminiEffort"):
        return isinstance(value, str) and providers.valid_effort(value)
    if key == "s2ApiKey":
        return isinstance(value, str) and len(value) <= 200 and value.isascii() and value.isprintable()
    if key == "feedTypes":
        return isinstance(value, list) and all(v in ("new", "cross", "replace", "replace-cross") for v in value)
    return True  # feedCategories : vérifié à part


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
