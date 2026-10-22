"""Annotations d'un article : surlignages, notes d'énoncés et de preuves, fiche de lecture, état de lecture."""

import base64
import json
import re
import uuid

from . import db

KINDS = ("highlight", "block", "general", "bookmark", "note")  # + « proof » : emplacement de preuve indiqué à la main
# marque-page : nom dans `quote` ; cible {"id": paragraphe du HTML} ou {"pdf": {"page", "top"}} (hauteur en points du
# PDF), et "version" : celle de l'article quand il a été posé (une nouvelle version peut le déplacer)
# note libre (sans passage sélectionné) : texte dans `note` ; cible {"id": paragraphe} ou {"pdf": {"page", "top", "left"}}
PROOF_MODES = ("section", "passage", "immediate", "admitted")
COLORS = ("yellow", "green", "blue", "pink")
STATES = ("understood", "review", "verified")
MAX_IMAGE = 4_000_000  # image d'une zone du PDF (PNG)


def png_bytes(value):
    """Image PNG envoyée par la page (base64, ou « data:image/png;base64,… ») ; None si absente. ValueError si ce
    n'est pas un PNG ou s'il est trop gros."""
    if not value:
        return None
    data = value.split(",", 1)[1] if value.startswith("data:") else value
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        raise ValueError("bad image")
    if len(raw) > MAX_IMAGE or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("bad image")
    return raw


def zone_of(target):
    """Zone d'une page du PDF ({"pdf": {"page", "rect": [x0, y0, x1, y1]}}, fractions de la page) ou None."""
    pdf = (target or {}).get("pdf") or {}
    return pdf if "rect" in pdf else None


def _check_zone(zone):
    rect = zone.get("rect")
    ok = isinstance(zone.get("page"), int) and zone["page"] >= 1 and isinstance(rect, list) and len(rect) == 4 \
        and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in rect) and rect[0] < rect[2] and rect[1] < rect[3]
    if not ok:
        raise ValueError("bad zone")


def _check_bookmark(target):
    pdf = target.get("pdf")
    if pdf is not None:
        ok = isinstance(pdf, dict) and isinstance(pdf.get("page"), int) and pdf["page"] >= 1 \
            and all(pdf.get(k) is None or isinstance(pdf[k], (int, float)) for k in ("top", "left"))
    else:
        ok = isinstance(target.get("id"), str) and 0 < len(target["id"]) <= 200
    if not ok:
        raise ValueError("bad bookmark")


def image_path(annotation):
    from .library import paper_dir  # (library importe ce module)
    return paper_dir(annotation["paper_id"]) / "zones" / f"{annotation['id']}.png"


def _row(row):
    item = dict(row)
    item["target"] = json.loads(item["target"]) if item["target"] else None
    return item


def list_for(paper_id):
    with db.connect() as connection:
        rows = connection.execute("SELECT * FROM annotations WHERE paper_id = ? ORDER BY id", (paper_id,)).fetchall()
    items = [_row(r) for r in rows]
    for item in items:
        if zone_of(item["target"]):
            item["image"] = image_path(item).exists()
    return items


def get(annotation_id):
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM annotations WHERE id = ?", (annotation_id,)).fetchone()
    return _row(row) if row else None


AUTHORS = (None, "claude", "codex", "gemini", "local")  # annotation proposée par l'IA, acceptée


def create(paper_id, kind, target=None, color=None, quote=None, note=None, state=None, author=None, image=None):
    """Nouvelle annotation ; pour un énoncé, une preuve ou la fiche de lecture, met à jour l'existante. `image` (PNG) :
    celle d'une zone du PDF, montrée dans l'onglet Notes."""
    if kind not in KINDS or (color and color not in COLORS) or (state and state not in STATES):
        raise ValueError("bad annotation")
    if zone_of(target):
        _check_zone(zone_of(target))
    elif image:
        raise ValueError("image without zone")
    if author not in AUTHORS:
        raise ValueError("bad author")
    if kind != "highlight":
        author = None  # notes d'énoncé et fiche de lecture : ce sont les vôtres, même complétées par Claude
    if kind == "block" and not (target or {}).get("id"):
        raise ValueError("block without target")  # sinon une nouvelle ligne à chaque enregistrement
    if kind in ("bookmark", "note"):
        _check_bookmark(target or {})
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
                "INSERT INTO annotations (paper_id, kind, target, color, quote, note, state, author, uid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (paper_id, kind, json.dumps(target) if target else None, color, quote, note, state, author, uuid.uuid4().hex))
            created = cursor.lastrowid
    if not existing:
        item = get(created)  # relu après l'enregistrement (fin du bloc « with »)
        if image:
            from .library import write_atomic_bytes
            path = image_path(item)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic_bytes(path, image)
            item["image"] = True
        return item
    return update(existing["id"], color=color, note=note, state=state)


def update(annotation_id, color=None, note=None, state=None, clear_state=False, quote=None, version=None, span=None):
    fields = {}
    if span is not None:  # passage surligné du PDF retrouvé ailleurs (document recompilé) : sa nouvelle place
        keys = ("page", "start", "endPage", "end")
        if set(span) != set(keys) or not all(isinstance(span[k], int) and not isinstance(span[k], bool) and 0 <= span[k] < 10_000_000 for k in keys):
            raise ValueError("bad span")
        with db.connect() as connection:
            connection.execute("UPDATE annotations SET target = json_set(target, '$.pdf', json(?)), updated_at = datetime('now') "
                               "WHERE id = ? AND kind = 'highlight' AND json_extract(target, '$.pdf.rect') IS NULL",
                               (json.dumps({k: span[k] for k in keys}), annotation_id))
    if version is not None:  # marque-page vérifié sur la version actuelle de l'article
        with db.connect() as connection:
            connection.execute("UPDATE annotations SET target = json_set(target, '$.version', ?) WHERE id = ? AND kind = 'bookmark'",
                               (int(version), annotation_id))
    if quote is not None:  # nom d'un marque-page
        fields["quote"] = quote
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
    item = get(annotation_id)
    if item and zone_of(item["target"]):
        image_path(item).unlink(missing_ok=True)
    with db.connect() as connection:
        connection.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))


def delete_for(paper_id):
    with db.connect() as connection:
        connection.execute("DELETE FROM annotations WHERE paper_id = ?", (paper_id,))


def annotated_papers():
    with db.connect() as connection:
        return {r["paper_id"] for r in connection.execute("SELECT DISTINCT paper_id FROM annotations")}


# ---- Emplacement de preuve indiqué à la main (remplace la détection automatique pour cet énoncé)

def _proof_row(connection, paper_id, node_id):
    return connection.execute(
        "SELECT id FROM annotations WHERE paper_id = ? AND kind = 'proof' AND json_extract(target, '$.id') = ?",
        (paper_id, node_id)).fetchone()


def set_proof(paper_id, node_id, mode, ref=None, start=None, end=None):
    if mode not in PROOF_MODES or (mode == "section" and not ref) or (mode == "passage" and not start):
        raise ValueError("bad proof location")
    target = {"id": node_id, "mode": mode}
    if mode == "section":
        target["ref"] = ref
    if mode == "passage":
        target.update(start=start, end=end or start)
    with db.connect() as connection:
        existing = _proof_row(connection, paper_id, node_id)
        if existing:
            connection.execute("UPDATE annotations SET target = ?, updated_at = datetime('now') WHERE id = ?",
                               (json.dumps(target), existing["id"]))
        else:
            connection.execute("INSERT INTO annotations (paper_id, kind, target, uid) VALUES (?, 'proof', ?, ?)",
                               (paper_id, json.dumps(target), uuid.uuid4().hex))


def clear_proof(paper_id, node_id):
    with db.connect() as connection:
        connection.execute(
            "DELETE FROM annotations WHERE paper_id = ? AND kind = 'proof' AND json_extract(target, '$.id') = ?",
            (paper_id, node_id))


def proof_overrides(paper_id):
    with db.connect() as connection:
        rows = connection.execute("SELECT target FROM annotations WHERE paper_id = ? AND kind = 'proof'", (paper_id,)).fetchall()
    return {t["id"]: t for t in (json.loads(r["target"]) for r in rows)}


def stats():
    """Par article : passages surlignés, notes écrites, énoncés compris ou vérifiés (cartes de la bibliothèque)."""
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT paper_id, SUM(kind = 'highlight') AS highlights, "
            "SUM(note IS NOT NULL AND TRIM(note) != '') AS notes, "
            "SUM(state IN ('understood', 'verified')) AS done FROM annotations GROUP BY paper_id").fetchall()
    return {r["paper_id"]: {"highlights": r["highlights"], "notes": r["notes"], "done": r["done"]} for r in rows}


# ---- Import (notes exportées par arXivReader, bloc de données signé : notesync.py) ----

def import_one(paper_id, item):
    """Annotation importée : ajoutée, remplacée si elle est plus récente que la nôtre, sinon laissée. Rapprochement
    par identifiant stable ; énoncé, preuve, fiche de lecture et emplacement de preuve : par leur cible (un seul par
    article). Renvoie "added", "updated" ou "unchanged"."""
    kind = item.get("kind")
    target = item.get("target") if isinstance(item.get("target"), dict) else None
    if kind not in KINDS + ("proof",) or not isinstance(item.get("uid"), str) or not 8 <= len(item["uid"]) <= 64:
        return "unchanged"
    if kind in ("proof", "block") and not isinstance((target or {}).get("id"), str):
        return "unchanged"  # (anciennes notes d'énoncé sans cible : rien à quoi les rattacher)
    if not re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", str(item.get("updated_at") or "")):
        return "unchanged"  # date au format de la base, sinon une entrée pourrait l'emporter sur toutes les suivantes
    try:
        if zone_of(target):
            _check_zone(zone_of(target))
        if kind in ("bookmark", "note"):
            _check_bookmark(target or {})
    except ValueError:
        return "unchanged"
    clean = lambda value, size: value[:size] if isinstance(value, str) else None  # noqa: E731
    values = {"target": json.dumps(target) if target else None,
              "color": item.get("color") if item.get("color") in COLORS else None,
              "quote": clean(item.get("quote"), 5000), "note": clean(item.get("note"), 100000),
              "state": item.get("state") if item.get("state") in STATES else None,
              "author": item.get("author") if item.get("author") in AUTHORS else None,
              "updated_at": clean(item.get("updated_at"), 30) or "1970-01-01 00:00:00"}
    with db.connect() as connection:
        existing = connection.execute("SELECT id, updated_at FROM annotations WHERE uid = ?", (item["uid"],)).fetchone()
        if not existing and kind == "general":
            existing = connection.execute("SELECT id, updated_at FROM annotations WHERE paper_id = ? AND kind = 'general'",
                                          (paper_id,)).fetchone()
        elif not existing and kind in ("block", "proof"):
            existing = connection.execute(
                "SELECT id, updated_at FROM annotations WHERE paper_id = ? AND kind = ? AND json_extract(target, '$.id') = ?",
                (paper_id, kind, target["id"])).fetchone()
        if existing:
            if values["updated_at"] <= (existing["updated_at"] or ""):
                return "unchanged"
            assignments = ", ".join(f"{k} = ?" for k in values)
            connection.execute(f"UPDATE annotations SET {assignments} WHERE id = ?", (*values.values(), existing["id"]))
            return "updated"
        connection.execute(
            "INSERT INTO annotations (paper_id, kind, uid, created_at, target, color, quote, note, state, author, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (paper_id, kind, item["uid"], clean(item.get("created_at"), 30) or values["updated_at"], *values.values()))
        return "added"
