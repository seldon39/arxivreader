"""Import d'un article dans la bibliothèque : métadonnées, HTML nettoyé, images et structure."""

import json
import shutil
import time

from . import db
from .arxiv import fetch_asset, fetch_html, fetch_metadata, fetch_source, paper_key, parse_arxiv_id
from .config import PAPERS
from .sanitize import sanitize_document
from .structure import extract_structure
from .texsource import analyze_source

ALLOWED_ASSET_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")


def paper_dir(paper_id):
    return PAPERS / paper_id


def import_paper(query, add_to_library=True):
    arxiv_id, _ = parse_arxiv_id(query)
    meta = fetch_metadata(arxiv_id)
    paper_id = paper_key(arxiv_id)
    folder = paper_dir(paper_id)
    staging = folder.with_name(folder.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "assets").mkdir(parents=True)

    source = "none"
    structure = {"toc": [], "nodes": [], "equations": {}, "bibliography": [], "hiddenText": 0}
    fetched = fetch_html(arxiv_id, meta.version)
    if fetched is not None:
        html, base_url, source = fetched
        fragment, assets = sanitize_document(html, base_url)
        structure = extract_structure(fragment)
        (staging / "content.html").write_text(str(fragment), encoding="utf-8")
        for local_name, remote in assets:
            try:
                data, content_type = fetch_asset(remote)
            except Exception:
                continue
            if content_type.split(";")[0].strip() in ALLOWED_ASSET_TYPES:
                (staging / "assets" / local_name).write_bytes(data)
            time.sleep(0.2)
    structure["source"] = source
    # Source TeX : options que la version HTML perd (numéros d'équation, police, étiquettes de listes)
    try:
        data = fetch_source(arxiv_id, meta.version)
        structure["tex"] = analyze_source(data) if data else None
    except Exception:
        structure["tex"] = None
    (staging / "structure.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")

    shutil.rmtree(folder, ignore_errors=True)
    staging.rename(folder)
    previous = db.get_paper(paper_id)
    db.save_paper({
        "id": paper_id,
        "arxiv_id": arxiv_id,
        "version": meta.version,
        "title": meta.title,
        "authors": meta.authors,
        "abstract": meta.abstract,
        "categories": meta.categories,
        "primary_category": meta.primary_category,
        "published": meta.published,
        "updated": meta.updated,
        "doi": meta.doi,
        "journal_ref": meta.journal_ref,
        "comment": meta.comment,
        "source": source,
        "position": previous["position"] if previous else None,
        # déjà dans la bibliothèque : on l'y laisse ; sinon selon la demande (« Lire » = cache seulement)
        "in_library": 1 if add_to_library or (previous and previous.get("in_library")) else 0,
    })
    return db.get_paper(paper_id)


def load_structure(paper_id):
    path = paper_dir(paper_id) / "structure.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def set_in_library(paper_id, value):
    db.update_paper(paper_id, in_library=1 if value else 0)
    return db.get_paper(paper_id)


def clean_cache(days=30):
    """Articles lus sans être ajoutés à la bibliothèque, pas rouverts depuis `days` jours : effacés."""
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT id FROM papers WHERE in_library = 0 AND COALESCE(opened_at, added_at) < datetime('now', ?)",
            (f"-{days} days",)).fetchall()
    for row in rows:
        remove_paper(row["id"])
    return len(rows)


def remove_paper(paper_id):
    shutil.rmtree(paper_dir(paper_id), ignore_errors=True)
    db.delete_paper(paper_id)
