"""Revue où un article est paru : d'après arXiv (champ « Journal-ref » ou DOI indiqués par les auteurs) et,
pour les articles que leurs auteurs n'ont pas mis à jour, d'après Semantic Scholar (revue et DOI connus).

Semantic Scholar est interrogé par lots (500 articles par requête) ; la réponse est gardée, et redemandée
après 30 jours pour un article qu'il ne connaît pas encore comme publié (la parution vient souvent plus tard).
"""

import re
import threading

import httpx

from . import db
from .arxiv import with_retry
from .config import USER_AGENT
from .relevance import S2_BATCH, s2_key

ARXIV_DOI = re.compile(r"^10\.48550/", re.I)    # DOI attribué par arXiv à toute prépublication : pas une revue
PREPRINT_VENUES = {"arxiv", "arxiv.org", "corr", "biorxiv", "medrxiv", "ssrn", "research square"}
STALE_DAYS = 30
SCHEMA = """CREATE TABLE IF NOT EXISTS journals (
    key TEXT PRIMARY KEY,            -- clé Semantic Scholar (ArXiv:2601.00001, DOI:…)
    journal TEXT,                    -- revue (et volume, pages) ; NULL : pas de revue connue
    doi TEXT,
    fetched_at TEXT DEFAULT (datetime('now'))
)"""
_lock = threading.Lock()  # une mise à jour à la fois (lancée aussi en arrière-plan)


def _ensure():
    with db.connect() as connection:
        connection.execute(SCHEMA)


def _from_s2(paper):
    """Revue (avec volume et pages) et DOI d'une réponse de Semantic Scholar ; None pour une prépublication."""
    if not isinstance(paper, dict):
        return None, None
    journal = paper.get("journal") if isinstance(paper.get("journal"), dict) else {}
    name = " ".join(str(journal.get("name") or "").split())
    text = None
    # « ArXiv », mais aussi « arXiv: Symplectic Geometry » (section d'arXiv) : pas une revue
    if name and name.lower() not in PREPRINT_VENUES and not name.lower().startswith("arxiv"):
        volume = str(journal.get("volume") or "").strip()
        pages = " ".join(str(journal.get("pages") or "").split())
        text = name + (f" {volume}" if volume and not volume.lower().startswith("abs/") else "") + (f", {pages}" if pages else "")
    ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
    doi = str(ids.get("DOI") or "").strip() or None
    if doi and ARXIV_DOI.match(doi):
        doi = None
    return (text[:300] if text else None), (doi[:200] if doi else None)


def fetch(keys, api_key=None, timeout=30):
    """Demande à Semantic Scholar la revue des articles pas encore connus (ou vus non publiés il y a plus de
    30 jours). Sans réseau ou refusé : on garde ce qu'on a, on réessaiera."""
    _ensure()
    keys = list(dict.fromkeys(k for k in keys if k))
    if not keys:
        return
    with _lock:
        with db.connect() as connection:
            fresh = {r["key"] for r in connection.execute(
                f"SELECT key FROM journals WHERE journal IS NOT NULL OR doi IS NOT NULL "
                f"OR fetched_at > datetime('now', '-{STALE_DAYS} days')")}
        missing = [k for k in keys if k not in fresh]
        headers = {"User-Agent": USER_AGENT, **({"x-api-key": api_key} if api_key else {})}
        with httpx.Client(headers=headers, timeout=timeout) as client:
            for start in range(0, len(missing), 500):
                batch = missing[start:start + 500]
                response = with_retry(lambda: client.post(S2_BATCH, params={"fields": "journal,externalIds"}, json={"ids": batch}),
                                      retry_on=(429,), delays=(2, 4, 8))
                if response.status_code != 200:
                    return
                answers = response.json()
                if not isinstance(answers, list):
                    return
                with db.connect() as connection:
                    for key, paper in zip(batch, answers):
                        journal, doi = _from_s2(paper)
                        connection.execute(
                            "INSERT INTO journals (key, journal, doi, fetched_at) VALUES (?, ?, ?, datetime('now')) "
                            "ON CONFLICT(key) DO UPDATE SET journal = excluded.journal, doi = excluded.doi, fetched_at = excluded.fetched_at",
                            (key, journal, doi))


def store(key, paper):
    """Revue d'un article reçu d'une autre requête à Semantic Scholar (recommandations), sans nouvelle requête."""
    _ensure()
    journal, doi = _from_s2(paper)
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO journals (key, journal, doi, fetched_at) VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET journal = excluded.journal, doi = excluded.doi, fetched_at = excluded.fetched_at",
            (key, journal, doi))


BACKGROUND = True  # les tests le coupent (pas de réseau)


def fetch_later(keys, api_key=None):
    """Même chose en arrière-plan (la page n'attend pas) ; ignoré si une mise à jour est déjà en cours."""
    if not BACKGROUND or not keys or _lock.locked():
        return

    def run():
        try:
            fetch(keys, api_key)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def known(keys):
    """Revue et DOI déjà connus de Semantic Scholar : clé → (revue, DOI)."""
    _ensure()
    keys = [k for k in dict.fromkeys(keys) if k]
    found = {}
    with db.connect() as connection:
        for start in range(0, len(keys), 900):
            part = keys[start:start + 900]
            for row in connection.execute(f"SELECT key, journal, doi FROM journals WHERE key IN ({','.join('?' * len(part))})", part):
                found[row["key"]] = (row["journal"], row["doi"])
    return found


def published_in(journal_ref=None, doi=None, s2=None):
    """Où l'article est paru, s'il l'est : {"text", "source": "arxiv" | "s2"} ; None sinon.
    Indication des auteurs sur arXiv d'abord, puis Semantic Scholar ; un DOI seul compte aussi (hors DOI d'arXiv)."""
    journal_ref = " ".join(str(journal_ref or "").split())
    if journal_ref:
        return {"text": journal_ref[:300], "source": "arxiv"}
    s2_journal, s2_doi = s2 or (None, None)
    if s2_journal:
        return {"text": s2_journal, "source": "s2"}
    doi = str(doi or "").strip()
    if doi and not ARXIV_DOI.match(doi):
        return {"text": f"DOI {doi[:200]}", "source": "arxiv"}
    if s2_doi:
        return {"text": f"DOI {s2_doi}", "source": "s2"}
    return None


def annotate(items, key=lambda item: s2_key(item.get("arxiv_id"), item.get("key"))):
    """Ajoute « publishedIn » à chaque article (bibliothèque, feed, recherche)."""
    found = known([key(item) for item in items])
    for item in items:
        item["publishedIn"] = published_in(item.get("journal_ref"), item.get("doi"), found.get(key(item)))
    return items
