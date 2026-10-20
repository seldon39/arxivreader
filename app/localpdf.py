"""PDF importés depuis l'ordinateur (hors arXiv) : un article de la bibliothèque comme les autres, lu dans le
lecteur PDF, sans identifiant arXiv (« pdf-<empreinte> », source « local-pdf »).

À l'import : titre et auteurs tirés des métadonnées du PDF (sinon de sa première page, sinon du nom du fichier),
texte de chaque page extrait pour l'assistant (text.json), structure vide (pas de source TeX). La version arXiv de
l'article est cherchée (mention « arXiv:… » de la première page, sinon DOI ou titre via Semantic Scholar) : elle
offre toutes les fonctions (HTML, énoncés, dépendances), l'application la propose.
"""

import difflib
import hashlib
import io
import json
import logging
import re
import unicodedata
from pathlib import Path

from . import db
from .library import paper_dir, write_atomic, write_atomic_bytes
from .structure import STRUCTURE_VERSION

SOURCE = "local-pdf"
logging.getLogger("pypdf").setLevel(logging.ERROR)  # polices incomplètes, etc. : sans effet sur le texte utile
MAX_BYTES = 150_000_000
MAX_PAGES = 2000
ARXIV_STAMP = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I)
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;:)\]])")
LINE_NOISE = re.compile(r"\d{4}|©|doi|arxiv|page|journal|vol\.|issn", re.I)
NAMES = re.compile(r"(?:[A-Z][\w'’-]*\.?(?:\s+|$)){2,5}(?:(?:,|\s+and|\s+&)\s+(?:[A-Z][\w'’-]*\.?(?:\s+|$)){2,5})*")
DATE_LINE = re.compile(r"\b(19|20)\d{2}\b|january|february|march|april|may|june|july|august|september|october|november|december", re.I)
NOISY_TITLE = re.compile(r"(\.(pdf|dvi|tex|docx?)$)|^(microsoft word|untitled|document\d*)\b|^\s*$", re.I)


# pas de source TeX : ni table des matières, ni énoncés, ni bibliographie (le lecteur attend ces clés)
EMPTY_STRUCTURE = {"version": STRUCTURE_VERSION, "source": SOURCE, "toc": [], "nodes": [], "bibliography": [], "labels": {},
                   "hiddenText": 0}


class PdfError(ValueError):
    pass


def paper_id(data):
    return "pdf-" + hashlib.sha1(data).hexdigest()[:12]


def is_local(paper):
    return bool(paper) and paper.get("source") == SOURCE


def _clean(value):
    return " ".join(str(value or "").replace("\x00", "").split())


def read_pdf(data, filename=""):
    """Titre, auteurs, résumé probable, DOI et texte de chaque page."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfError("encrypted")
        pages = [(page.extract_text() or "") for page in reader.pages[:MAX_PAGES]]
        info = reader.metadata or {}
    except PdfError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as error:
        raise PdfError(f"unreadable: {type(error).__name__}") from error
    first = "\n".join(pages[:2])
    lines = [_clean(line) for line in (pages[0] if pages else "").splitlines()]
    lines = [line for line in lines if line]
    title = _clean(info.get("/Title"))
    start = None
    if NOISY_TITLE.search(title) or len(title) < 4:
        # première ligne qui ressemble à un titre (pas un en-tête de revue, une date, un numéro de page), et ses
        # lignes de suite (« The sphere packing problem in » / « dimension 8 »)
        start = next((i for i, line in enumerate(lines) if len(line.split()) >= 3 and not LINE_NOISE.search(line)), None)
        title = ""
        if start is not None:
            title, end = lines[start], start + 1
            while end < len(lines) and len(lines[end]) < 70 and re.match(r"[a-z0-9(]", lines[end]):
                title, end = f"{title} {lines[end]}", end + 1
            start = end
    title = title or Path(filename or "document.pdf").stem.replace("_", " ")
    authors = [a for a in re.split(r"\s*(?:;|,| and | & )\s*", _clean(info.get("/Author"))) if a]
    if not authors and start is not None and start < len(lines) and NAMES.fullmatch(lines[start]):
        authors = [a for a in re.split(r"\s*(?:,| and | & )\s*", lines[start]) if a]  # ligne des auteurs, sous le titre
        start += 1
    abstract = ""
    found = re.search(r"\bAbstract[.:\s—-]*(.{80,2500}?)(?=\n\s*(?:1\.?\s+Introduction|Introduction|Keywords|Contents)\b|\Z)",
                      first, re.I | re.S)
    if found:
        abstract = _clean(found.group(1))[:2000]
    elif start is not None:
        # pas de titre « Abstract » : le paragraphe entre les auteurs (et la date) et « Keywords » / l'introduction
        rest = [line for line in lines[start:start + 40]]
        while rest and DATE_LINE.search(rest[0]) and len(rest[0]) < 40:
            rest.pop(0)
        stop = next((i for i, line in enumerate(rest) if re.match(r"(keywords|ams subject|msc|1\.?\s+introduction|introduction|contents)\b", line, re.I)), None)
        if stop:
            candidate = " ".join(rest[:stop])
            if 80 <= len(candidate) <= 2500:
                abstract = candidate
    doi = DOI.search(first)
    return {"title": title[:300], "authors": authors[:30], "abstract": abstract, "doi": doi.group(1) if doi else None,
            "pages": pages}


def import_pdf(data, filename=""):
    """Article de la bibliothèque à partir d'un PDF ; déjà importé (même fichier) : celui-là, remis en bibliothèque."""
    if len(data) > MAX_BYTES:
        raise PdfError("too large")
    if not data.lstrip()[:5] == b"%PDF-":
        raise PdfError("not a PDF")
    pid = paper_id(data)
    existing = db.get_paper(pid)
    if existing:
        if not existing.get("in_library"):
            db.update_paper(pid, in_library=1)
        return db.get_paper(pid)
    found = read_pdf(data, filename)
    folder = paper_dir(pid)
    folder.mkdir(parents=True, exist_ok=True)
    write_atomic_bytes(folder / "paper.pdf", data)
    write_atomic(folder / "text.json", json.dumps({"pages": found["pages"]}, ensure_ascii=False))
    write_atomic(folder / "structure.json", json.dumps(EMPTY_STRUCTURE))
    db.save_paper({"id": pid, "arxiv_id": "", "version": None, "title": found["title"], "authors": found["authors"],
                   "abstract": found["abstract"], "categories": [], "primary_category": "", "published": None,
                   "updated": None, "doi": found["doi"], "journal_ref": None, "comment": _clean(filename)[:200],
                   "source": SOURCE, "in_library": 1})
    return db.get_paper(pid)


def text(pid):
    """Texte du PDF pour l'assistant, page par page (« [page 3] ») : il peut renvoyer à une page."""
    path = paper_dir(pid) / "text.json"
    try:
        pages = json.loads(path.read_text(encoding="utf-8"))["pages"]
    except (OSError, ValueError, KeyError):
        return ""
    return "\n\n".join(f"[page {n}]\n{page.strip()}" for n, page in enumerate(pages, 1) if page.strip())


def update_meta(pid, title, authors):
    paper = db.get_paper(pid)
    if not is_local(paper):
        raise PdfError("not an imported PDF")
    db.update_paper(pid, title=_clean(title)[:300] or paper["title"],
                    authors=json.dumps([_clean(a) for a in authors if _clean(a)][:30], ensure_ascii=False))
    return db.get_paper(pid)


def _norm(title):
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()).strip()


def arxiv_match(paper, api_key=None, get_s2=None):
    """Version arXiv du même article : {"arxivId", "title", "via"} ou None. Mention « arXiv:… » imprimée sur la
    première page (préprint), sinon DOI, sinon titre (Semantic Scholar ; titre presque identique exigé)."""
    pages = []
    try:
        pages = json.loads((paper_dir(paper["id"]) / "text.json").read_text(encoding="utf-8"))["pages"][:2]
    except (OSError, ValueError, KeyError):
        pass
    stamp = ARXIV_STAMP.search("\n".join(pages))
    if stamp:
        return {"arxivId": stamp.group(1), "title": paper["title"], "via": "stamp"}
    if get_s2 is None:
        from .profile import _get_s2 as get_s2
    fields = {"fields": "externalIds,title"}
    found = None
    if paper.get("doi"):
        found = get_s2(f"/paper/DOI:{paper['doi']}", fields, api_key)
        via = "doi"
    if not (found and (found.get("externalIds") or {}).get("ArXiv")) and len(_norm(paper["title"]).split()) >= 3:
        candidate = get_s2("/paper/search/match", {"query": paper["title"], **fields}, api_key)
        candidate = ((candidate or {}).get("data") or [None])[0] if "data" in (candidate or {}) else candidate
        if candidate and difflib.SequenceMatcher(None, _norm(candidate.get("title")), _norm(paper["title"])).ratio() > 0.9:
            found, via = candidate, "title"
    arxiv = ((found or {}).get("externalIds") or {}).get("ArXiv")
    return {"arxivId": arxiv, "title": found.get("title") or paper["title"], "via": via} if arxiv else None
