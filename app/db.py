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
    position TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

JSON_COLUMNS = ("authors", "categories")


def init():
    LIBRARY.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript(SCHEMA)


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
        rows = db.execute("SELECT * FROM papers ORDER BY COALESCE(opened_at, added_at) DESC").fetchall()
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
