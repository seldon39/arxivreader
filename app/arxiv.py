"""Accès à arXiv : identifiants, métadonnées et version HTML des articles."""

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

from .config import USER_AGENT

# 2607.17203, 2607.17203v2, 0704.0001 ; anciens identifiants : math.DG/0601001, hep-th/9901001v3
NEW_ID = r"\d{4}\.\d{4,5}"
OLD_ID = r"[a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7}"
ID_RE = re.compile(rf"(?<![\w.])({NEW_ID}|{OLD_ID})(v\d+)?(?![\w])")

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class ArxivError(Exception):
    pass


def parse_arxiv_id(text):
    """Renvoie (identifiant sans version, version ou None) à partir d'un identifiant ou d'une URL."""
    text = text.strip()
    text = re.sub(r"^arxiv:\s*", "", text, flags=re.I)
    match = ID_RE.search(text)
    if not match:
        raise ArxivError(f"Identifiant arXiv introuvable dans « {text} »")
    version = int(match.group(2)[1:]) if match.group(2) else None
    return match.group(1), version


def extract_ids(text):
    """Tous les identifiants arXiv distincts d'un texte, dans l'ordre d'apparition."""
    seen = []
    for match in ID_RE.finditer(text):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def paper_key(arxiv_id):
    """Nom de dossier et clé de base de données pour un identifiant."""
    return arxiv_id.replace("/", "_")


@dataclass
class Metadata:
    arxiv_id: str
    version: int
    title: str
    authors: list
    abstract: str
    published: str
    updated: str
    categories: list
    primary_category: str
    doi: str = ""
    journal_ref: str = ""
    comment: str = ""
    links: dict = field(default_factory=dict)


def _client():
    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30)


open_client = _client  # un seul client (connexion réutilisée) pour toutes les images d'un article


def with_retry(send, retry_on=(429, 503), delays=(3, 6, 9)):
    """Requête répétée tant que le serveur répond « trop de requêtes » ou « indisponible ».
    Pas d'attente après la dernière tentative ; Retry-After respecté s'il donne des secondes (au plus 60)."""
    for delay in (*delays, None):
        response = send()
        if response.status_code not in retry_on or delay is None:
            return response
        wait = response.headers.get("Retry-After", "").strip()
        time.sleep(min(60, max(delay, int(wait) + 1)) if wait.isdigit() else delay)


def _text(node, path):
    found = node.find(path)
    return " ".join(found.text.split()) if found is not None and found.text else ""


def fetch_metadata(arxiv_id):
    # arXiv demande au plus une requête toutes les 3 s : nouvelle tentative si elle répond 429/503
    with _client() as client:
        response = with_retry(lambda: client.get("https://export.arxiv.org/api/query", params={"id_list": arxiv_id}))
    response.raise_for_status()
    entry = ET.fromstring(response.text).find(f"{ATOM}entry")
    if entry is None or not _text(entry, f"{ATOM}title"):
        raise ArxivError(f"Article {arxiv_id} introuvable sur arXiv")
    entry_id = _text(entry, f"{ATOM}id")
    version = re.search(r"v(\d+)$", entry_id)
    primary = entry.find(f"{ARXIV_NS}primary_category")
    return Metadata(
        arxiv_id=arxiv_id,
        version=int(version.group(1)) if version else 1,
        title=_text(entry, f"{ATOM}title"),
        authors=[_text(a, f"{ATOM}name") for a in entry.findall(f"{ATOM}author")],
        abstract=_text(entry, f"{ATOM}summary"),
        published=_text(entry, f"{ATOM}published"),
        updated=_text(entry, f"{ATOM}updated"),
        categories=[c.get("term") for c in entry.findall(f"{ATOM}category")],
        primary_category=primary.get("term") if primary is not None else "",
        doi=_text(entry, f"{ARXIV_NS}doi"),
        journal_ref=_text(entry, f"{ARXIV_NS}journal_ref"),
        comment=_text(entry, f"{ARXIV_NS}comment"),
    )


def fetch_html(arxiv_id, version):
    """Version HTML (LaTeXML) de l'article : arxiv.org/html, sinon ar5iv.

    Renvoie (html, url de base pour les images, nom de la source) ou None.
    """
    candidates = [
        (f"https://arxiv.org/html/{arxiv_id}v{version}", "arxiv-html"),
        (f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}", "ar5iv"),
    ]
    with _client() as client:
        for url, source in candidates:
            response = client.get(url)
            if response.status_code == 200 and "ltx_document" in response.text:
                base = str(response.url)
                return response.text, base if base.endswith("/") else base + "/", source
    return None


MAX_SOURCE_BYTES = 60_000_000


def fetch_source(arxiv_id, version):
    """Source déposée sur arXiv (archive tar.gz ou .tex compressé), ou None si trop grosse."""
    with _client() as client:
        with client.stream("GET", f"https://arxiv.org/e-print/{arxiv_id}v{version}") as response:
            if response.status_code != 200:
                return None
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    return None
                chunks.append(chunk)
    return b"".join(chunks)


def fetch_pdf(arxiv_id, version):
    """PDF de l'article (articles sans version HTML : lus dans le PDF), ou None."""
    with _client() as client:
        with client.stream("GET", f"https://arxiv.org/pdf/{arxiv_id}v{version}") as response:
            if response.status_code != 200:
                return None
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    return None
                chunks.append(chunk)
    data = b"".join(chunks)
    return data if data[:5] == b"%PDF-" else None


MAX_ASSET_BYTES = 20_000_000


def fetch_asset(url, client):
    """Image de l'article (au plus 20 Mo), avec le client de l'import en cours."""
    with client.stream("GET", url) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_ASSET_BYTES:
                raise ArxivError(f"image trop grosse : {url}")
            chunks.append(chunk)
        return b"".join(chunks), response.headers.get("content-type", "")
