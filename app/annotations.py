"""Annotations d'un article : surlignages, notes d'énoncés et de preuves, fiche de lecture, état de lecture."""

import json

from . import db

KINDS = ("highlight", "block", "general")
COLORS = ("yellow", "green", "blue", "pink")
STATES = ("understood", "review", "verified")


def _row(row):
    item = dict(row)
    item["target"] = json.loads(item["target"]) if item["target"] else None
    return item


def list_for(paper_id):
    with db.connect() as connection:
        rows = connection.execute("SELECT * FROM annotations WHERE paper_id = ? ORDER BY id", (paper_id,)).fetchall()
    return [_row(r) for r in rows]


def get(annotation_id):
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM annotations WHERE id = ?", (annotation_id,)).fetchone()
    return _row(row) if row else None


def create(paper_id, kind, target=None, color=None, quote=None, note=None, state=None):
    """Nouvelle annotation ; pour un énoncé, une preuve ou la fiche de lecture, met à jour l'existante."""
    if kind not in KINDS or (color and color not in COLORS) or (state and state not in STATES):
        raise ValueError("bad annotation")
    with db.connect() as connection:
        existing = None
        if kind == "general":
            existing = connection.execute(
                "SELECT id FROM annotations WHERE paper_id = ? AND kind = 'general'", (paper_id,)).fetchone()
        elif kind == "block":
            existing = connection.execute(
                "SELECT id FROM annotations WHERE paper_id = ? AND kind = 'block' AND json_extract(target, '$.id') = ?",
                (paper_id, (target or {}).get("id"))).fetchone()
        if not existing:
            cursor = connection.execute(
                "INSERT INTO annotations (paper_id, kind, target, color, quote, note, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (paper_id, kind, json.dumps(target) if target else None, color, quote, note, state))
            created = cursor.lastrowid
    if not existing:
        return get(created)  # relu après l'enregistrement (fin du bloc « with »)
    return update(existing["id"], color=color, note=note, state=state)


def update(annotation_id, color=None, note=None, state=None, clear_state=False):
    fields = {}
    if color is not None:
        if color not in COLORS:
            raise ValueError("bad color")
        fields["color"] = color
    if note is not None:
        fields["note"] = note
    if state is not None or clear_state:
        if state and state not in STATES:
            raise ValueError("bad state")
        fields["state"] = state or None
    if fields:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with db.connect() as connection:
            connection.execute(f"UPDATE annotations SET {assignments}, updated_at = datetime('now') WHERE id = ?",
                               (*fields.values(), annotation_id))
    return get(annotation_id)


def delete(annotation_id):
    with db.connect() as connection:
        connection.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))


def delete_for(paper_id):
    with db.connect() as connection:
        connection.execute("DELETE FROM annotations WHERE paper_id = ?", (paper_id,))


def annotated_papers():
    with db.connect() as connection:
        return {r["paper_id"] for r in connection.execute("SELECT DISTINCT paper_id FROM annotations")}
