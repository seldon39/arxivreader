"""Serveur web local d'arXivReader (127.0.0.1 uniquement)."""

import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import annotations, db, feed, library, profile, relevance
from .arxiv import ArxivError
from .config import HOST, PORT, STATIC

ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
PAPER_ID = re.compile(r"^[A-Za-z0-9.\-_]+$")
ASSET_NAME = re.compile(r"^[0-9a-f]{16}(\.[a-z]+)?$")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


@asynccontextmanager
async def lifespan(_app):
    db.init()
    library.clean_cache()
    yield


app = FastAPI(title="arXivReader", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Refuse les noms d'hôte étrangers (attaque par « DNS rebinding ») et les requêtes
    # d'écriture venant d'autres sites.
    if request.headers.get("host", "").lower() not in ALLOWED_HOSTS:
        return PlainTextResponse("Forbidden host", status_code=403)
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            return PlainTextResponse("Forbidden origin", status_code=403)
        if request.method != "DELETE" and "application/json" not in request.headers.get("content-type", ""):
            return PlainTextResponse("JSON expected", status_code=415)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def checked_paper(paper_id):
    if not PAPER_ID.match(paper_id):
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


class AnnotationUpdate(BaseModel):
    color: str | None = None
    note: str | None = None
    state: str | None = None
    clear_state: bool = False


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
    return db.list_papers()


@app.post("/api/papers")
async def add_paper(body: ImportRequest):
    try:
        return await run_in_threadpool(library.import_paper, body.query, body.library)
    except ArxivError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception as error:  # réseau, page inattendue…
        return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)


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
    if not ASSET_NAME.match(name):
        raise HTTPException(400, "Bad asset name")
    path = library.paper_dir(paper_id) / "assets" / name
    if not path.exists():
        raise HTTPException(404, "Missing asset")
    return FileResponse(path)


@app.put("/api/papers/{paper_id}/library")
def set_library(paper_id: str, body: LibraryRequest):
    checked_paper(paper_id)
    return library.set_in_library(paper_id, body.in_library)


@app.get("/api/papers/{paper_id}/annotations")
def list_annotations(paper_id: str):
    checked_paper(paper_id)
    return annotations.list_for(paper_id)


@app.post("/api/papers/{paper_id}/annotations")
def add_annotation(paper_id: str, body: AnnotationRequest):
    checked_paper(paper_id)
    try:
        return annotations.create(paper_id, body.kind, body.target, body.color, (body.quote or "")[:5000] or None,
                                  (body.note or "")[:100000] if body.note is not None else None, body.state)
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


@app.put("/api/papers/{paper_id}/position")
def save_position(paper_id: str, body: PositionRequest):
    checked_paper(paper_id)
    db.update_paper(paper_id, position=body.anchor[:200])
    return {"ok": True}


@app.post("/api/papers/{paper_id}/refresh")
async def refresh(paper_id: str):
    paper = checked_paper(paper_id)
    return await run_in_threadpool(library.import_paper, paper["arxiv_id"])


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
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            raise HTTPException(400, "Bad day")
        if day not in days and day <= _now()[:10]:  # jour pas encore chargé : demandé à l'OAI-PMH d'arXiv
            try:
                await run_in_threadpool(feed.fetch_day, day, settings.get("feedCategories") or None,
                                        settings.get("s2ApiKey") or None)
            except Exception as error:
                return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)
            days = feed.known_days()
    latest = max((d for d, n in days.items() if n), default=None)
    return {
        "day": day or latest,
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
    except feed.FeedError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception as error:  # réseau
        return JSONResponse({"error": f"{type(error).__name__}: {error}"}, status_code=502)
    return {"count": count}


@app.put("/api/feed/status")
def feed_status(body: StatusRequest):
    feed.set_status(body.key[:100], body.status)
    return {"ok": True}


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
    return get_profile()


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
    allowed = {"language", "theme", "fontSize", "readingWidth", "proofsCollapsed", "tocOpen", "panelOpen", "paperFont", "marginMarks", "mathRenderer",
               "feedCategories", "feedTypes", "s2ApiKey", "feedSort"}
    db.save_settings({k: v for k, v in values.items() if k in allowed})
    return db.get_settings()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
