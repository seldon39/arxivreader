"""Serveur web local d'arXivReader (127.0.0.1 uniquement)."""

import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, feed, library
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


class PositionRequest(BaseModel):
    anchor: str


class RefreshRequest(BaseModel):
    view: str


class StatusRequest(BaseModel):
    key: str
    status: str | None = None


class LookupRequest(BaseModel):
    text: str


@app.get("/api/papers")
def papers():
    return db.list_papers()


@app.post("/api/papers")
async def add_paper(body: ImportRequest):
    try:
        return await run_in_threadpool(library.import_paper, body.query)
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
def feed_items(view: str = "arxiv"):
    settings = db.get_settings()
    return {
        "items": feed.list_items(view, ["new", "cross", "replace", "replace-cross"]),
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
            count = await run_in_threadpool(feed.refresh_arxiv, settings.get("feedCategories") or None)
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


@app.get("/api/settings")
def settings():
    return db.get_settings()


@app.put("/api/settings")
def update_settings(values: dict):
    allowed = {"language", "theme", "fontSize", "readingWidth", "proofsCollapsed", "tocOpen", "panelOpen", "paperFont", "marginMarks", "mathRenderer",
               "feedCategories", "feedTypes", "s2ApiKey"}
    db.save_settings({k: v for k, v in values.items() if k in allowed})
    return db.get_settings()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
