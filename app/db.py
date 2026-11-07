"""Base SQLite de la bibliothèque (une seule base, dans library/)."""

import json
import sqlite3
from contextlib import contextmanager

from . import keystore
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
-- Annotations : passages surlignés, notes et état de lecture des énoncés et preuves, fiche de lecture
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL,
    kind TEXT NOT NULL,              -- highlight, block (énoncé ou preuve), general
    target TEXT,                     -- JSON : {start:{id,offset}, end:{id,offset}} ou {id}
    color TEXT,
    quote TEXT,                      -- texte surligné (formules en $…$), pour retrouver le passage
    note TEXT,                       -- Markdown + LaTeX
    state TEXT,                      -- understood, review, verified (énoncés)
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS annotations_paper ON annotations (paper_id);
-- Questions posées à Claude sur un article, et ses réponses
CREATE TABLE IF NOT EXISTS ai_exchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL,
    task TEXT NOT NULL,              -- explain, detail, audit, passage, ask, summary, review
    target TEXT,                     -- id de l'énoncé ou de la preuve visés, sinon NULL
    quote TEXT,                      -- passage sélectionné (formules en $…$)
    question TEXT,
    parent_id INTEGER,               -- échange auquel celui-ci fait suite
    answer TEXT,
    model TEXT,
    status TEXT,                     -- done, stopped, error
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ai_exchanges_paper ON ai_exchanges (paper_id);
-- Dossiers de la bibliothèque ; un article peut être dans plusieurs dossiers (comme des étiquettes)
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS folder_papers (
    folder_id INTEGER NOT NULL,
    paper_id TEXT NOT NULL,
    added_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (folder_id, paper_id)
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
        if "journal_ref" not in columns:  # revue indiquée sur arXiv par les auteurs (Journal-ref), DOI
            db.execute("ALTER TABLE feed_items ADD COLUMN journal_ref TEXT")
            db.execute("ALTER TABLE feed_items ADD COLUMN doi TEXT")
        if "citing" not in columns:  # article qui cite les vôtres : titres de vos articles cités (JSON)
            db.execute("ALTER TABLE feed_items ADD COLUMN citing TEXT")
            db.execute("ALTER TABLE feed_items ADD COLUMN cited_found TEXT")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(profile_items)")}
        if "only_categories" not in columns:  # auteur suivi : homonymes écartés (catégories gardées, articles écartés)
            db.execute("ALTER TABLE profile_items ADD COLUMN only_categories TEXT")
            db.execute("ALTER TABLE profile_items ADD COLUMN not_papers TEXT")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(papers)")}
        if "in_library" not in columns:
            db.execute("ALTER TABLE papers ADD COLUMN in_library INTEGER DEFAULT 1")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(annotations)")}
        if "author" not in columns:  # « claude » : annotation proposée par Claude et acceptée
            db.execute("ALTER TABLE annotations ADD COLUMN author TEXT")
        if "uid" not in columns:  # identifiant stable (export / import des notes, d'un ordinateur à l'autre)
            db.execute("ALTER TABLE annotations ADD COLUMN uid TEXT")
        db.execute("UPDATE annotations SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS annotations_uid ON annotations (uid)")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(ai_exchanges)")}
        if "applied" not in columns:  # annotations proposées par la réponse déjà appliquées à l'article
            db.execute("ALTER TABLE ai_exchanges ADD COLUMN applied INTEGER DEFAULT 0")
        if "steps" not in columns:  # relecture croisée : étapes avant la synthèse (JSON)
            db.execute("ALTER TABLE ai_exchanges ADD COLUMN steps TEXT")
        if "card" not in columns:  # relecture d'un chapitre terminée par sa fiche (reprise par la relecture du livre)
            db.execute("ALTER TABLE ai_exchanges ADD COLUMN card INTEGER DEFAULT 0")
    _move_secrets()


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


def list_all_papers():
    """Articles de la bibliothèque et articles seulement ouverts (encore en cache)."""
    with connect() as db:
        rows = db.execute("SELECT * FROM papers").fetchall()
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
    return {**{r["key"]: json.loads(r["value"]) for r in rows}, **_secrets()}


def save_settings(values):
    values = dict(values)
    stored = []  # clés passées au trousseau : retirées de la base
    if keystore.ENABLED:
        for key in keystore.SECRET_KEYS:
            if key in values and _keep_secret(key, values[key]):
                stored.append(key)
                del values[key]
    with connect() as db:
        db.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, json.dumps(v)) for k, v in values.items()],
        )
        db.executemany("DELETE FROM settings WHERE key = ?", [(key,) for key in stored])


# ---------- Clés d'API dans le trousseau du système (keystore) ----------

_cache = None  # clés lues dans le trousseau, une fois


def _secrets():
    global _cache
    if _cache is None:
        _cache = {}
        for key in keystore.SECRET_KEYS if keystore.ENABLED else ():
            try:
                value = keystore.get(key)
            except keystore.KeystoreError:
                value = None  # trousseau illisible : celle de la base s'il y en a une
            if value:
                _cache[key] = value
    return _cache


def _keep_secret(key, value):
    """Clé enregistrée (vide : retirée) dans le trousseau ; False s'il refuse : elle reste dans la base."""
    try:
        keystore.put(key, value or "")
    except keystore.KeystoreError:
        _secrets().pop(key, None)  # celle de la base, nouvelle, et non l'ancienne du trousseau
        return False
    if value:
        _secrets()[key] = value
    else:
        _secrets().pop(key, None)
    return True


def _move_secrets():
    """Clés encore dans la base (versions précédentes, trousseau un moment indisponible) : passées au trousseau."""
    if not keystore.ENABLED:
        return
    with connect() as db:
        rows = db.execute("SELECT key, value FROM settings WHERE key IN (%s)" % ",".join("?" * len(keystore.SECRET_KEYS)),
                          keystore.SECRET_KEYS).fetchall()
    left = {row["key"]: json.loads(row["value"]) for row in rows}
    filled = {key: value for key, value in left.items() if value}
    if filled:
        save_settings(filled)  # plus récentes que celles du trousseau (sinon elles n'y seraient pas restées)
    with connect() as db:  # vides : rien à garder
        db.executemany("DELETE FROM settings WHERE key = ?", [(key,) for key, value in left.items() if not value])
