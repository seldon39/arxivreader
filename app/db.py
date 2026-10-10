"""Base SQLite de la bibliothèque (une seule base, dans library/)."""

import json
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH, LIBRARY

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    arxiv_id TEXT NOT NULL,
    version INTEGER,
    title TEXT,
    authors TEXT,
    abstract TEXT,
    categories TEXT,
    primary_category TEXT,
    published TEXT,
    updated TEXT,
    doi TEXT,
    journal_ref TEXT,
    comment TEXT,
    source TEXT,
    added_at TEXT DEFAULT (datetime('now')),
    opened_at TEXT,
    position TEXT,
    in_library INTEGER DEFAULT 1     -- 0 : article seulement lu (cache), pas ajouté à la bibliothèque
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
-- Feed : articles vus dans les nouveautés arXiv ou recommandés par Semantic Scholar
CREATE TABLE IF NOT EXISTS feed_items (
    key TEXT PRIMARY KEY,            -- identifiant arXiv, ou « s2:<paperId> » sans version arXiv
    arxiv_id TEXT,
    version INTEGER,
    title TEXT,
    authors TEXT,
    abstract TEXT,
    categories TEXT,
    primary_category TEXT,
    announce_type TEXT,              -- new, cross, replace, replace-cross
    announced TEXT,                  -- date d'annonce sur arXiv
    in_arxiv INTEGER DEFAULT 0,
    followed INTEGER DEFAULT 0,      -- article récent d'un auteur suivi (requête arXiv par auteur)
    s2_rank INTEGER,                 -- rang dans la dernière recommandation, sinon NULL
    link TEXT,
    fetched_at TEXT DEFAULT (datetime('now'))
);
-- Profil de recherche : articles, livres, auteurs suivis, mots-clés
CREATE TABLE IF NOT EXISTS profile_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- mine, paper, book, author, keyword
    label TEXT NOT NULL,             -- titre, nom ou mot-clé
    ref TEXT,                        -- identifiant arXiv, « s2:<paperId> », DOI…
    authors TEXT,
    year TEXT,
    text TEXT,                       -- résumé ou description (sert au calcul de pertinence)
    source TEXT DEFAULT 'manual',    -- manual, suggested
    added_at TEXT DEFAULT (datetime('now'))
);
-- Vecteurs SPECTER de Semantic Scholar (proximité de sens entre articles), mis en cache
CREATE TABLE IF NOT EXISTS embeddings (
    key TEXT PRIMARY KEY,            -- identifiant demandé à Semantic Scholar (ArXiv:…, DOI:…, paperId)
    vector TEXT,                     -- JSON, ou NULL si inconnu de Semantic Scholar
    fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS feed_status (
    key TEXT PRIMARY KEY,
    status TEXT,                     -- liked, disliked
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

JSON_COLUMNS = ("authors", "categories")


def init():
    LIBRARY.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript(SCHEMA)
        # Colonnes ajoutées après la création de la base
        columns = {row["name"] for row in db.execute("PRAGMA table_info(feed_items)")}
        if "followed" not in columns:
            db.execute("ALTER TABLE feed_items ADD COLUMN followed INTEGER DEFAULT 0")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(papers)")}
        if "in_library" not in columns:
            db.execute("ALTER TABLE papers ADD COLUMN in_library INTEGER DEFAULT 1")


@contextmanager
def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def row_to_paper(row):
    paper = dict(row)
    for column in JSON_COLUMNS:
        paper[column] = json.loads(paper[column] or "[]")
    return paper


def list_papers():
    with connect() as db:
        rows = db.execute("SELECT * FROM papers WHERE in_library = 1 ORDER BY COALESCE(opened_at, added_at) DESC").fetchall()
    return [row_to_paper(r) for r in rows]


def get_paper(paper_id):
    with connect() as db:
        row = db.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return row_to_paper(row) if row else None


def save_paper(paper):
    values = dict(paper)
    for column in JSON_COLUMNS:
        values[column] = json.dumps(values[column], ensure_ascii=False)
    columns = ", ".join(values)
    placeholders = ", ".join(f":{c}" for c in values)
    updates = ", ".join(f"{c} = excluded.{c}" for c in values if c != "id")
    with connect() as db:
        db.execute(
            f"INSERT INTO papers ({columns}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}",
            values,
        )


def update_paper(paper_id, **fields):
    assignments = ", ".join(f"{c} = ?" for c in fields)
    with connect() as db:
        db.execute(f"UPDATE papers SET {assignments} WHERE id = ?", (*fields.values(), paper_id))


def delete_paper(paper_id):
    with connect() as db:
        db.execute("DELETE FROM papers WHERE id = ?", (paper_id,))


def get_settings():
    with connect() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: json.loads(r["value"]) for r in rows}


def save_settings(values):
    with connect() as db:
        db.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, json.dumps(v)) for k, v in values.items()],
        )
