"""Import d'un article dans la bibliothèque : métadonnées, HTML nettoyé, images et structure."""

import json
import logging
import os
import re
import shutil
import tempfile
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from pathlib import Path

from bs4 import BeautifulSoup

from . import annotations, db, texstructure
from .arxiv import fetch_asset, fetch_html, fetch_metadata, fetch_pdf, fetch_source, open_client, paper_key, parse_arxiv_id
from .config import PAPERS
from .sanitize import sanitize_document
from .structure import STRUCTURE_VERSION, extract_structure
from .texsource import analyze_source

ALLOWED_ASSET_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")
ASSET_WORKERS = 4  # images téléchargées en même temps (un article peut en avoir des centaines)
ASSET_REF = re.compile(r'src="assets/([^"/]+)"')
log = logging.getLogger("arxivreader.library")


def paper_dir(paper_id):
    return PAPERS / paper_id


# Un verrou par article : import, mise à jour et ré-analyse d'un même article ne se chevauchent jamais
_locks = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def paper_lock(paper_id):
    with _locks_guard:
        return _locks[paper_id]


def write_atomic_bytes(path, data):
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write_atomic(path, text):
    """Écrit à côté puis remplace : un lecteur voit l'ancien fichier ou le nouveau, jamais un fichier à moitié écrit."""
    part = path.with_name(path.name + ".part")
    part.write_text(text, encoding="utf-8")
    os.replace(part, path)


def import_paper(query, add_to_library=True):
    arxiv_id, _ = parse_arxiv_id(query)
    meta = fetch_metadata(arxiv_id)
    paper_id = paper_key(arxiv_id)
    with paper_lock(paper_id):
        source = _download(paper_id, arxiv_id, meta)  # arxiv-html, ar5iv ou none
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


def _download(paper_id, arxiv_id, meta):
    folder = paper_dir(paper_id)
    PAPERS.mkdir(parents=True, exist_ok=True)
    # dossier de préparation propre à cet import, effacé en cas d'échec
    staging = Path(tempfile.mkdtemp(dir=PAPERS, prefix=f".{paper_id}."))
    try:
        source = _fill(staging, paper_id, arxiv_id, meta)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    # remplacement de l'ancienne version : l'article reste lisible jusqu'au dernier moment
    old = folder.with_name(f".{paper_id}.old")
    shutil.rmtree(old, ignore_errors=True)
    if folder.exists():
        folder.rename(old)
    staging.rename(folder)
    shutil.rmtree(old, ignore_errors=True)
    return source


def _fill(staging, paper_id, arxiv_id, meta):
    (staging / "assets").mkdir()

    source = "none"
    structure = {"toc": [], "nodes": [], "equations": {}, "bibliography": [], "hiddenText": 0}
    fetched = fetch_html(arxiv_id, meta.version)
    if fetched is not None:
        html, base_url, source = fetched
        fragment, assets = sanitize_document(html, base_url)
        structure = extract_structure(fragment, annotations.proof_overrides(paper_id))
        (staging / "content.html").write_text(str(fragment), encoding="utf-8")
        # adresse d'origine de chaque image : une image qui n'a pas pu venir sera redemandée plus tard
        (staging / "assets.json").write_text(json.dumps(dict(assets)), encoding="utf-8")
        _fetch_assets(staging / "assets", assets)
    # Source TeX : options que la version HTML perd (numéros d'équation, police, étiquettes de listes)
    try:
        data = fetch_source(arxiv_id, meta.version)
    except Exception:
        data = None
    try:
        tex_options = analyze_source(data) if data else None
    except Exception:
        tex_options = None
    if source == "none" and data:
        # Pas de version HTML : lecture dans le PDF, énoncés et dépendances lus dans la source TeX
        try:
            found = texstructure.from_source(data)
        except Exception:
            found = None
        if found:
            structure, text = found
            source = "tex"
            (staging / "source.tex").write_text(text, encoding="utf-8")
            pdf = fetch_pdf(arxiv_id, meta.version)
            if pdf:
                (staging / "paper.pdf").write_bytes(pdf)
                texstructure.locate_pages(structure, pdf)
    structure["source"] = source
    structure["tex"] = tex_options
    (staging / "structure.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
    return source



def _fetch_assets(folder, assets):
    """Images de l'article, quelques-unes à la fois (un même client) ; celles qui échouent sont laissées."""
    failed = []
    with open_client() as client:
        def one(item):
            local_name, remote = item
            try:
                data, content_type = fetch_asset(remote, client)
            except Exception as error:
                failed.append((remote, error))
                return
            if content_type.split(";")[0].strip() in ALLOWED_ASSET_TYPES:
                (folder / local_name).write_bytes(data)
        with ThreadPoolExecutor(ASSET_WORKERS) as pool:
            list(pool.map(one, assets))
    if failed:
        log.warning("%d image(s) sur %d non téléchargée(s), par ex. %s : %s", len(failed), len(assets), *failed[0])


def missing_assets(paper_id):
    """Images citées par le texte de l'article mais absentes du dossier (import interrompu, adresse fausse…)."""
    folder = paper_dir(paper_id)
    try:
        names = set(ASSET_REF.findall((folder / "content.html").read_text(encoding="utf-8")))
    except OSError:
        return []
    return sorted(name for name in names if not (folder / "assets" / name).exists())


_repairing = set()
_repairing_guard = threading.Lock()


def repair_assets(paper_id):
    """Images manquantes redemandées (une fois à la fois par article) : d'après leurs adresses d'origine si l'import
    les a gardées, sinon par un nouvel import de l'article (notes, place dans la bibliothèque et dossiers gardés)."""
    with _repairing_guard:
        if paper_id in _repairing:
            return
        _repairing.add(paper_id)
    try:
        missing = missing_assets(paper_id)
        if not missing:
            return
        try:
            remote = json.loads((paper_dir(paper_id) / "assets.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            remote = None
        if remote is None:  # importé avant que les adresses soient gardées : nouvel import
            paper = db.get_paper(paper_id)
            if paper:
                import_paper(paper["arxiv_id"], add_to_library=False)
            return
        with paper_lock(paper_id):
            (paper_dir(paper_id) / "assets").mkdir(exist_ok=True)
            _fetch_assets(paper_dir(paper_id) / "assets", [(name, remote[name]) for name in missing if name in remote])
    except Exception as error:
        log.warning("réparation des images de %s impossible : %s", paper_id, error)
    finally:
        with _repairing_guard:
            _repairing.discard(paper_id)


def load_structure(paper_id):
    path = paper_dir(paper_id) / "structure.json"
    if not path.exists():
        return None
    structure = json.loads(path.read_text(encoding="utf-8"))
    if structure.get("version") != STRUCTURE_VERSION:
        structure = reanalyze(paper_id, structure)
    return structure


def reanalyze(paper_id, previous):
    """Analyse refaite sur le HTML déjà enregistré (sans téléchargement) : quand l'analyse a changé,
    ou quand l'emplacement d'une preuve a été indiqué à la main.
    Les identifiants existants sont conservés : surlignages et notes restent en place."""
    folder = paper_dir(paper_id)
    content = folder / "content.html"
    with paper_lock(paper_id):
        if not content.exists() and (folder / "source.tex").exists():  # article lu dans le PDF : source TeX
            structure = texstructure.extract((folder / "source.tex").read_text(encoding="utf-8"))
            pdf = folder / "paper.pdf"
            if pdf.exists():
                texstructure.locate_pages(structure, pdf.read_bytes())
            structure["source"] = previous.get("source")
            structure["tex"] = previous.get("tex")
            write_atomic(folder / "structure.json", json.dumps(structure, ensure_ascii=False))
            return structure
        if not content.exists():
            return previous
        fragment = BeautifulSoup(content.read_text(encoding="utf-8"), "html.parser")
        structure = extract_structure(fragment, annotations.proof_overrides(paper_id))
        structure["source"] = previous.get("source")
        structure["tex"] = previous.get("tex")
        write_atomic(content, str(fragment))
        write_atomic(folder / "structure.json", json.dumps(structure, ensure_ascii=False))
    return structure


def ensure_pdf(paper):
    """Chemin du PDF de l'article, téléchargé depuis arXiv s'il manque ; None s'il est introuvable."""
    path = paper_dir(paper["id"]) / "paper.pdf"
    with paper_lock(paper["id"]):
        if not path.exists():
            data = fetch_pdf(paper["arxiv_id"], paper["version"])
            if not data:
                return None
            write_atomic_bytes(path, data)
    return path


def pdf_pages(paper):
    """{"pages": {id d'énoncé ou de section: page du PDF}, "tops": {id: hauteur dans la page}} ;
    calculé une fois par PDF et gardé (pages.json)."""
    structure = load_structure(paper["id"]) or {}
    if structure.get("source") == "tex":  # pages déjà trouvées à l'import
        items = [item for item in structure.get("nodes", []) + structure.get("toc", []) + structure.get("proofBlocks", [])
                 if item.get("page")]
        return {"pages": {item["id"]: item["page"] for item in items},
                "tops": {item["id"]: item["top"] for item in items if item.get("top") is not None}}
    path = ensure_pdf(paper)
    if path is None:
        return None
    cache = paper_dir(paper["id"]) / "pages.json"
    stamp = f"{path.stat().st_mtime}:{STRUCTURE_VERSION}:tops"
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("stamp") == stamp:
            return saved["located"]
    located = texstructure.pages_for(structure, path.read_bytes())
    write_atomic(cache, json.dumps({"stamp": stamp, "located": located}))
    return located


def set_in_library(paper_id, value):
    db.update_paper(paper_id, in_library=1 if value else 0)
    return db.get_paper(paper_id)


def clean_cache(days=30):
    """Articles lus sans être ajoutés à la bibliothèque, pas rouverts depuis `days` jours : effacés."""
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT id FROM papers WHERE in_library = 0 AND COALESCE(opened_at, added_at) < datetime('now', ?)",
            (f"-{days} days",)).fetchall()
    from .ai import papers_with_exchanges
    from .annotations import annotated_papers
    keep = annotated_papers() | papers_with_exchanges()  # article annoté ou discuté avec Claude : gardé
    removed = [row["id"] for row in rows if row["id"] not in keep]
    for paper_id in removed:
        remove_paper(paper_id)
    return len(removed)


def remove_paper(paper_id):
    from . import ai, annotations, folders
    shutil.rmtree(paper_dir(paper_id), ignore_errors=True)
    db.delete_paper(paper_id)
    folders.forget_paper(paper_id)
    annotations.delete_for(paper_id)
    ai.delete_for(paper_id)
