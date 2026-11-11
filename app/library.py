"""Import d'un article dans la bibliothèque : métadonnées, HTML nettoyé, images et structure."""

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

from pathlib import Path

from bs4 import BeautifulSoup

from . import annotations, db, texstructure
from .arxiv import ArxivError, fetch_asset, fetch_html, fetch_latest, fetch_metadata, fetch_pdf, fetch_source, open_client, paper_key, parse_arxiv_id
from .config import PAPERS
from .sanitize import sanitize_document
from .structure import STRUCTURE_VERSION, extract_structure
from .texsource import analyze_source

ALLOWED_ASSET_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")
ASSET_WORKERS = 6  # images téléchargées en même temps (un article peut en avoir des centaines ; au-delà, pas plus vite)
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
    """Écrit à côté puis remplace : un lecteur voit l'ancien fichier ou le nouveau, jamais un fichier à moitié écrit.
    Fichier temporaire au nom unique (deux écritures simultanées ne se gênent pas ; jamais un lien existant suivi)."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_atomic(path, text):
    write_atomic_bytes(path, text.encode("utf-8"))


def stored_key(arxiv_id):
    """Clé de l'article : celle de l'identifiant normalisé, ou celle d'une fiche gardée sous l'ancienne forme à classe
    (math.DG/0211159), enregistrée avant que la classe soit retirée à l'entrée."""
    key = paper_key(arxiv_id)
    if "/" in arxiv_id and not db.get_paper(key):
        subject, number = arxiv_id.split("/", 1)
        with db.connect() as connection:
            # d'abord la fiche dont l'identifiant a déjà été normalisé (une réimportation le réécrit « math/0211159 » en
            # gardant la clé d'origine), puis celle qui l'a encore avec sa classe
            row = connection.execute("SELECT id FROM papers WHERE arxiv_id = ? AND archived_of IS NULL", (arxiv_id,)).fetchone() \
                or connection.execute("SELECT id FROM papers WHERE arxiv_id GLOB ? AND archived_of IS NULL",
                                      (f"{subject}.[A-Z][A-Z]/{number}",)).fetchone()
        if row:
            return row["id"]
    return key


def import_paper(query, add_to_library=True, must_exist=False):
    arxiv_id, _ = parse_arxiv_id(query)
    # source TeX (souvent plusieurs Mo, l'étape la plus longue) demandée tout de suite, sans attendre la fiche
    # d'arXiv ni la page et ses figures, qui se font pendant ce temps : le résultat est le même, en moins de temps
    with ThreadPoolExecutor(1) as background:
        pending_source = background.submit(_source, arxiv_id, None)
        meta = fetch_metadata(arxiv_id)
        paper_id = stored_key(arxiv_id)
        _latest[arxiv_id] = (time.monotonic(), meta.version)
        with paper_lock(paper_id):
            return _import(paper_id, arxiv_id, meta, add_to_library, pending_source, must_exist=must_exist)


def _import(paper_id, arxiv_id, meta, add_to_library, pending_source, ar5iv=True, same_source=None, must_exist=False):
    """`same_source` (réparation) : rien n'est remplacé si le texte obtenu ne vient pas de là. `must_exist` (import de
    fond d'une fiche déjà inscrite) : article retiré pendant le téléchargement, rien n'est recréé."""
    from . import bookjob
    installed = db.get_paper(paper_id)
    # les emplacements de preuve indiqués à la main ne valent que pour le même texte
    overrides = annotations.proof_overrides(paper_id) if installed and installed["version"] == meta.version else {}
    staging, source = _staged(paper_id, arxiv_id, meta, pending_source, overrides, ar5iv)  # arxiv-html, ar5iv ou none
    if same_source and source != same_source:
        log.info("réparation de %s refusée : texte obtenu de %s au lieu de %s", paper_id, source, same_source)
        _repair_refused.add(paper_id)
        shutil.rmtree(staging, ignore_errors=True)
        return installed
    if same_source:  # figures qu'une réparation n'a pas pu retélécharger : celles qu'on avait restent
        old = paper_dir(paper_id) / "assets"
        for image in old.iterdir() if old.is_dir() else ():
            if image.is_file() and not (staging / "assets" / image.name).exists():
                (staging / "assets").mkdir(exist_ok=True)
                shutil.copy2(image, staging / "assets" / image.name)
    # relue après le téléchargement : place dans la bibliothèque, et version d'une fiche en attente, écrite entre-temps
    # par un import de notes (notesync._version_of_notes)
    previous = db.get_paper(paper_id)
    if must_exist and previous is None:
        shutil.rmtree(staging, ignore_errors=True)
        return None
    # autre version que celle installée : l'ancienne est archivée avec ses notes
    new_version = previous and previous["version"] not in (None, meta.version)
    row = {
        "id": paper_id,
        **_fields(arxiv_id, meta, source),
        "position": previous["position"] if previous else None,
        # déjà dans la bibliothèque : on l'y laisse ; sinon selon la demande (« Lire » = cache seulement)
        "in_library": 1 if add_to_library or (previous and previous.get("in_library")) else 0,
    }
    # cette version était archivée ici (notes importées d'un autre ordinateur) : elle redevient l'article principal
    back = archive_key(paper_id, meta.version)
    with paper_lock(back):
        returning = db.get_paper(back)
        if returning:
            bookjob.forget(back)
            _keep_images(paper_dir(back), staging)
            if paper_dir(back).exists():
                (paper_dir(back) / ABSORBED).touch()  # pour recover_interrupted : à ne pas confondre avec un dossier mis de côté
            row["position"] = returning["position"] or row["position"]
        if new_version:
            bookjob.forget(paper_id)  # relecture ou résumé en cours : ni le texte ni les échanges suivants ne seraient les siens
            _replace_version(previous, staging, row, returning)
        else:
            _install(paper_id, staging)
            with db.connect() as connection:
                db.write_paper(connection, row)
                if returning:
                    _absorb(connection, back, paper_id)
        if returning:
            _discard(paper_dir(back))
    return db.get_paper(paper_id)


def _fields(arxiv_id, meta, source):
    return {"arxiv_id": arxiv_id, "version": meta.version, "title": meta.title, "authors": meta.authors, "abstract": meta.abstract,
            "categories": meta.categories, "primary_category": meta.primary_category, "published": meta.published,
            "updated": meta.updated, "doi": meta.doi, "journal_ref": meta.journal_ref, "comment": meta.comment, "source": source}


def _staged(name, arxiv_id, meta, pending_source, overrides, ar5iv=True):
    """(dossier de préparation rempli, propre à cet import, source) ; effacé en cas d'échec."""
    PAPERS.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=PAPERS, prefix=f".{name}."))
    try:
        return staging, _fill(staging, arxiv_id, meta, pending_source, overrides, ar5iv)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _install(paper_id, staging):
    """Remplacement par un nouvel import de la même version : l'article reste lisible jusqu'au dernier moment."""
    folder = paper_dir(paper_id)
    old = folder.with_name(f".{paper_id}.old")
    shutil.rmtree(old, ignore_errors=True)
    try:
        _keep_images(folder, staging)
        if folder.exists():
            _rename(folder, old)
        _rename(staging, folder)
    except OSError:
        if old.exists() and not folder.exists():
            _rename(old, folder)  # l'ancienne version reprend sa place : jamais d'article sans dossier
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)


def _keep_images(source, target):
    """Images des notes et des échanges (une par annotation ou par échange) : arXiv ne les redonnerait pas."""
    for images in ("zones", "ai-images"):
        if (source / images).exists():
            shutil.copytree(source / images, target / images, dirs_exist_ok=True)


def _discard(folder):
    """Dossier effacé sans risque en cas d'arrêt brutal : d'abord caché (renommage atomique), recover_interrupted efface
    les dossiers cachés restés. Renommage refusé : il reste en place, et sera effacé au prochain lancement."""
    hidden = folder.with_name(f".{folder.name}.discarded")
    try:
        _rename(folder, hidden)
    except OSError:
        return
    shutil.rmtree(hidden, ignore_errors=True)


def _rename(source, target, attempts=6):
    """Renommage d'un dossier ; sous Windows, refusé tant qu'un de ses fichiers est ouvert (antivirus qui examine
    les fichiers tout juste écrits, indexation, image en cours d'envoi) : quelques essais espacés."""
    for attempt in range(attempts):
        try:
            source.rename(target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.25 * (attempt + 1))


def _source(arxiv_id, version):
    try:
        return fetch_source(arxiv_id, version)
    except Exception:
        return None


def _fill(staging, arxiv_id, meta, pending_source, overrides, ar5iv=True):
    (staging / "assets").mkdir()
    source = "none"
    structure = {"toc": [], "nodes": [], "equations": {}, "bibliography": [], "hiddenText": 0}
    fetched = fetch_html(arxiv_id, meta.version, ar5iv=ar5iv)
    if fetched is not None:
        html, base_url, source = fetched
        fragment, assets = sanitize_document(html, base_url)
        structure = extract_structure(fragment, overrides)
        (staging / "content.html").write_text(str(fragment), encoding="utf-8")
        # adresse d'origine de chaque image : une image qui n'a pas pu venir sera redemandée plus tard
        (staging / "assets.json").write_text(json.dumps(dict(assets)), encoding="utf-8")
        _fetch_assets(staging / "assets", assets)
    # Source TeX : options que la version HTML perd (numéros d'équation, police, étiquettes de listes)
    data = pending_source.result()
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



# ---------- Ajout immédiat (signet) : l'article entre dans la bibliothèque tout de suite, d'après ce que la page
# en sait déjà (feed, recherche) ; le texte, les figures et la source arrivent en arrière-plan. Ouvert avant la fin,
# il attend son import (ensure_ready). ----------

PENDING = "pending"
_imports = {}  # article → événement de fin de son import en arrière-plan
_imports_guard = threading.Lock()


def quick_add(arxiv_id, known=None, in_library=True):
    """Article ajouté sans attendre son import (à la bibliothèque, ou seulement pour être lu : `in_library` faux) ;
    renvoie sa fiche. Déjà connu : rien n'est retéléchargé."""
    arxiv_id, _ = parse_arxiv_id(arxiv_id)
    paper_id = stored_key(arxiv_id)
    if db.get_paper(paper_id):
        return set_in_library(paper_id, True) if in_library else db.get_paper(paper_id)
    known = known or {}
    if not known.get("title"):
        meta = fetch_metadata(arxiv_id)  # rien de connu : la fiche d'arXiv (une requête, pas tout l'import)
        known = {"title": meta.title, "authors": meta.authors, "abstract": meta.abstract, "categories": meta.categories,
                 "primary_category": meta.primary_category, "published": meta.published, "doi": meta.doi, "journal_ref": meta.journal_ref}
    db.save_paper({"id": paper_id, "arxiv_id": arxiv_id, "version": None, "title": known["title"], "authors": known.get("authors") or [],
                   "abstract": known.get("abstract") or "", "categories": known.get("categories") or [],
                   "primary_category": known.get("primary_category") or "", "published": known.get("published") or "",
                   "updated": "", "doi": known.get("doi"), "journal_ref": known.get("journal_ref"), "comment": None,
                   "source": PENDING, "position": None, "in_library": 1 if in_library else 0})
    _start_import(arxiv_id, paper_id)
    return db.get_paper(paper_id)


_import_slots = threading.BoundedSemaphore(3)


def _start_import(arxiv_id, paper_id, task=None):
    """`task` : autre travail que l'import de la dernière version (version archivée)."""
    with _imports_guard:
        if paper_id in _imports:
            return
        done = _imports[paper_id] = threading.Event()

    def run():
        try:
            with _import_slots:  # quelques imports à la fois (import de notes : des centaines d'articles d'un coup)
                if task:
                    task()
                else:
                    import_paper(arxiv_id, add_to_library=False, must_exist=True)  # garde la place dans la bibliothèque telle qu'elle est alors
        except Exception as error:
            log.warning("import de %s en arrière-plan impossible : %s", arxiv_id, error)
        finally:
            with _imports_guard:
                _imports.pop(paper_id, None)
            done.set()
    threading.Thread(target=run, daemon=True).start()


def update_in_background(paper_id):
    """Nouvel import d'un article (dernière version), en arrière-plan ; importing() dit s'il tourne encore."""
    paper = db.get_paper(paper_id)
    if paper:
        _start_import(paper["arxiv_id"], paper_id)


def importing(paper_id):
    with _imports_guard:
        return paper_id in _imports


def ensure_ready(paper_id, timeout=180):
    """Article ajouté d'un clic dont l'import n'est pas fini : on l'attend (ou on le fait, s'il avait échoué)."""
    paper = db.get_paper(paper_id)
    if not paper or paper.get("source") != PENDING:
        return paper
    with _imports_guard:
        running = paper_id in _imports
    if not running:  # import de fond échoué (réseau) ou interrompu (application quittée) : relancé
        if paper.get("archived_of"):
            _start_version(paper["archived_of"], paper)
        else:
            _start_import(paper["arxiv_id"], paper_id)
    with _imports_guard:
        done = _imports.get(paper_id)
    if done:
        done.wait(timeout)  # borné : la page redemande tant que l'import n'est pas fini
    return db.get_paper(paper_id)


# ---------- Versions d'un article arXiv : une version archivée est un article à part entière (fiche, dossier, notes,
# échanges) d'identifiant « <article>v<k> », hors bibliothèque (in_library = 0, archived_of = l'article principal, qui
# porte la dernière version installée). ----------

_repair_refused = set()  # articles dont le nouvel import de réparation n'a rien donné de sûr : pas retenté à chaque ouverture
ABSORBED = ".absorbed"  # dans le dossier d'une archive qui redevient l'article principal
LATEST_KEEP = 3600  # dernière version connue, gardée une heure
LATEST_WAIT = 20    # arXiv lent : réponse sans attendre davantage (la requête continue et sert à la demande suivante)
_latest = {}        # identifiant arXiv → (instant, dernière version)


def archive_key(paper_id, version):
    return f"{paper_id}v{version}"


def main_of(paper):
    return db.get_paper(paper["archived_of"]) if paper.get("archived_of") else paper


def latest_version(arxiv_id):
    """Dernière version de l'article sur arXiv (une requête, gardée une heure) ; None hors ligne ou si arXiv tarde."""
    def fresh():
        known = _latest.get(arxiv_id)
        return known[1] if known and time.monotonic() - known[0] < LATEST_KEEP else None

    def ask():
        try:
            _latest[arxiv_id] = (time.monotonic(), fetch_metadata(arxiv_id).version)
        except Exception as error:
            log.info("dernière version de %s inconnue : %s", arxiv_id, error)
    if fresh() is None:
        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        thread.join(LATEST_WAIT)
    return fresh()


NEW_VERSIONS_DAYS = 45
NEW_VERSIONS_KEEP = 6 * 3600  # arXiv interrogé au plus toutes les six heures ; le bouton de mise à jour du feed force la requête
_new_versions_guard = threading.Lock()


def new_versions(force=False):
    """Section du feed : versions parues depuis NEW_VERSIONS_DAYS jours, plus récentes que celle installée, des articles
    arXiv de la bibliothèque. Réponse d'arXiv gardée en base : affichée aussitôt, et hors ligne. Une version installée
    depuis, ou un article retiré, sort de la liste sans nouvelle requête."""
    def since():
        return (datetime.now(timezone.utc) - timedelta(days=NEW_VERSIONS_DAYS)).strftime("%Y-%m-%d")

    def bare(arxiv_id):  # arXiv répond math/0601001 pour math.DG/0601001
        return re.sub(r"^([a-z-]+)\.[A-Z]{2}/", r"\1/", arxiv_id)

    papers = [p for p in db.list_papers() if p["arxiv_id"] and p["version"]]
    with _new_versions_guard:
        kept = db.get_settings().get("feed.newVersions") or {}
        if papers and (force or time.time() - kept.get("checked", 0) > NEW_VERSIONS_KEEP):
            try:
                found = fetch_latest([p["arxiv_id"] for p in papers])
            except Exception as error:
                log.info("nouvelles versions inconnues : %s", error)
            else:
                if found:  # réponse vide (arXiv en donne parfois) : rien n'est effacé, redemandé à la prochaine visite
                    asked = {bare(p["arxiv_id"]) for p in papers}
                    latest = {key: entry for key, entry in (kept.get("latest") or {}).items()  # article absent d'un lot : gardé
                              if key not in found and key in asked and entry["updated"] >= since()}
                    latest.update({key: {"version": m.version, "updated": m.updated[:10], "title": m.title, "authors": m.authors,
                                         "categories": m.categories, "primary_category": m.primary_category, "comment": m.comment}
                                   for key, m in found.items() if m.updated[:10] >= since()})
                    kept = {"checked": time.time(), "latest": latest}
                    db.save_settings({"feed.newVersions": kept})
    latest = kept.get("latest") or {}
    items = [{**latest[bare(p["arxiv_id"])], "id": p["id"], "arxiv_id": p["arxiv_id"], "installed": p["version"], "abstract": ""} for p in papers
             if bare(p["arxiv_id"]) in latest and latest[bare(p["arxiv_id"])]["version"] > p["version"] and latest[bare(p["arxiv_id"])]["updated"] >= since()]
    return sorted(items, key=lambda item: item["updated"], reverse=True)


def _move_notes(connection, source, target):
    """Annotations et échanges de `source` passent à `target`. Si elle en a déjà (rare) : fiche de lecture, note d'un
    énoncé et emplacement de preuve étant uniques par article, une seule ligne reste, avec les deux textes."""
    def unique(owner):
        return {(row["kind"], row["place"]): row for row in connection.execute(
            "SELECT id, kind, json_extract(target, '$.id') AS place, note, state FROM annotations "
            "WHERE paper_id = ? AND kind IN ('general', 'block', 'proof')", (owner,))}
    kept = unique(target)
    for key, row in unique(source).items():
        if key in kept:
            note = "\n\n".join(dict.fromkeys(filter(None, (kept[key]["note"], row["note"])))) or None
            connection.execute("UPDATE annotations SET note = ?, state = COALESCE(state, ?), updated_at = datetime('now') WHERE id = ?",
                               (note, row["state"], kept[key]["id"]))
            connection.execute("DELETE FROM annotations WHERE id = ?", (row["id"],))
    connection.execute("UPDATE annotations SET paper_id = ? WHERE paper_id = ?", (target, source))
    connection.execute("UPDATE ai_exchanges SET paper_id = ? WHERE paper_id = ?", (target, source))


def _absorb(connection, archive_id, paper_id):
    """Version archivée qui redevient l'article principal : ses notes et ses échanges le rejoignent, sa fiche disparaît."""
    _move_notes(connection, archive_id, paper_id)
    connection.execute("DELETE FROM papers WHERE id = ?", (archive_id,))


def _replace_version(previous, staging, row, returning=None):
    """Nouvelle version d'un article déjà importé : l'ancienne devient une version archivée (dossier, fiche, notes,
    échanges), la nouvelle commence sans notes, sauf `returning` : sa fiche archivée. Tout est validé en une transaction,
    une fois le nouveau dossier en place ; le moindre échec remet l'ancien dossier et ne valide rien."""
    paper_id, folder = previous["id"], paper_dir(previous["id"])
    archive_id = archive_key(paper_id, previous["version"])
    archive_folder = paper_dir(archive_id)
    with paper_lock(archive_id):
        existing = db.get_paper(archive_id)
        # déjà archivée (rare) : son dossier et sa fiche restent, celui de l'article est écarté, les notes sont réunies
        merging = existing and existing["source"] != PENDING
        parked = folder.with_name(f".{archive_id}.old") if merging else archive_folder
        moved = False
        try:
            if folder.exists():
                _discard(parked)  # reste inutile (au démarrage, recover_interrupted a remis l'utile)
                if merging:
                    _keep_images(folder, archive_folder)
                _rename(folder, parked)
                moved = True
            with db.connect() as connection:
                if not merging:
                    db.write_paper(connection, {**previous, "id": archive_id, "in_library": 0, "archived_of": paper_id})
                _move_notes(connection, paper_id, archive_id)
                if returning:
                    _absorb(connection, returning["id"], paper_id)
                _rename(staging, folder)
                db.write_paper(connection, row)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            if moved:
                shutil.rmtree(folder, ignore_errors=True)  # le nouveau dossier, si le renommage a eu lieu
                _rename(parked, folder)
            raise
    if merging:
        shutil.rmtree(parked, ignore_errors=True)
    if previous["source"] == PENDING and not merging:  # notes importées pour cette version, encore à télécharger
        _start_version(paper_id, db.get_paper(archive_id))


def recover_interrupted():
    """Au démarrage : les renommages d'une mise à jour sont hors de la transaction SQLite. Après un arrêt brutal entre
    les deux, la fiche fait foi : tant qu'elle porte encore l'ancienne version, l'ancien dossier reprend sa place (aucun
    autre import ne tourne encore). Les dossiers de préparation restés sont effacés."""
    def restore(parked, folder):
        try:
            shutil.rmtree(folder, ignore_errors=True)
            _rename(parked, folder)
        except OSError as error:
            log.warning("mise à jour interrompue de %s : dossier non remis en place : %s", folder.name, error)

    if not PAPERS.exists():
        return
    for paper in db.list_all_papers():
        if paper["version"] and paper["source"] != PENDING:
            parked = paper_dir(archive_key(paper["id"], paper["version"]))
            if parked.exists() and not db.get_paper(parked.name):
                if (parked / ABSORBED).exists():  # archive redevenue l'article principal, c'est validé : reste à l'effacer
                    _discard(parked)
                else:
                    restore(parked, paper_dir(paper["id"]))
    for hidden in PAPERS.glob(".*"):
        if not hidden.is_dir():
            continue
        owner = db.get_paper(hidden.name[1:].removesuffix(".old")) if hidden.name.endswith(".old") else None
        main = main_of(owner) if owner else None
        # .<article>.old (_install) : remis s'il n'y a plus de dossier ; .<archive>.old (fusion dans _replace_version) :
        # remis si l'article a encore cette version, donc si la fusion n'a pas été validée
        if main and (not paper_dir(main["id"]).exists() or (owner.get("archived_of") and owner["version"] == main["version"])):
            restore(hidden, paper_dir(main["id"]))
        else:
            shutil.rmtree(hidden, ignore_errors=True)


def _with_notes():
    from .ai import papers_with_exchanges
    return annotations.annotated_papers() | papers_with_exchanges()


def ensure_version(main, version):
    """Version archivée de l'article `main` : fiche inscrite tout de suite (en attente), téléchargée en arrière-plan si
    elle n'est pas déjà là ; renvoie son identifiant."""
    archive_id = archive_key(main["id"], version)
    if not db.get_paper(archive_id):
        db.save_paper({**{k: v for k, v in main.items() if k not in ("added_at", "opened_at", "position")},
                       "id": archive_id, "version": version, "source": PENDING, "in_library": 0, "archived_of": main["id"]})
    archive = db.get_paper(archive_id)
    if archive["source"] == PENDING:
        _start_version(main["id"], archive)
    return archive_id


def _start_version(paper_id, archive):
    _start_import(archive["arxiv_id"], archive["id"], lambda: import_version(paper_id, archive["version"]))


def import_version(paper_id, version):
    """Téléchargement d'une version archivée, sans toucher à l'article principal. Cette version seulement :
    arxiv.org/html/<id>v<k> (ar5iv en sert une autre), sinon sa source TeX et son PDF."""
    archive_id = archive_key(paper_id, version)
    arxiv_id = db.get_paper(archive_id)["arxiv_id"]
    staging = None
    try:
        with ThreadPoolExecutor(1) as background:
            pending_source = background.submit(_source, arxiv_id, version)
            meta = fetch_metadata(f"{arxiv_id}v{version}")
            if meta.version != version:
                raise ArxivError(f"Version {version} de {arxiv_id} introuvable sur arXiv")
            staging, source = _staged(archive_id, arxiv_id, meta, pending_source, {}, ar5iv=False)
        with paper_lock(archive_id):
            waiting = db.get_paper(archive_id)
            if waiting and waiting["source"] == PENDING:  # sinon : article retiré, ou version archivée par une mise à jour
                shutil.rmtree(paper_dir(archive_id), ignore_errors=True)
                _rename(staging, paper_dir(archive_id))
                db.save_paper({"id": archive_id, **_fields(arxiv_id, meta, source)})
    except Exception:
        # téléchargement impossible : plus de fiche en attente, sauf si des notes importées y sont déjà (réessai à l'ouverture)
        archive = db.get_paper(archive_id)
        if archive and archive["source"] == PENDING and archive_id not in _with_notes():
            db.delete_paper(archive_id)
        raise
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)


def versions_of(paper):
    """Versions d'un article arXiv, de 1 à la dernière (hors ligne : celles de cet ordinateur seulement), avec leurs
    notes ; `paper` : l'article principal ou une version archivée."""
    from . import ai
    main = main_of(paper)
    local = {row["version"]: row for row in db.archives_of(main["id"])}
    if main["version"]:
        local[main["version"]] = main
    latest = latest_version(main["arxiv_id"])
    if latest:
        latest = max([latest, *local])  # `local` peut être vide (import raté, hors ligne)
    notes, answers = annotations.counts(), ai.counts()

    def entry(number):
        row = local.get(number)
        key = row["id"] if row else archive_key(main["id"], number)
        return {"version": number, "id": key if row and row["source"] != PENDING else None,
                "notes": notes.get(key, 0), "answers": answers.get(key, 0),
                "current": key == paper["id"], "main": key == main["id"], "running": importing(key)}
    return {"main": main["id"], "latest": latest, "offline": latest is None,
            "versions": [entry(number) for number in (range(1, latest + 1) if latest else sorted(local))]}


def open_version(paper, version):
    """Version demandée dans le menu : {"id", "running"} ; `running` : téléchargement ou mise à jour en arrière-plan, à
    suivre par importing(id). ValueError : pas de telle version ; ArxivError : arXiv injoignable."""
    main = main_of(paper)
    if main["version"] == version:
        return {"id": main["id"], "running": importing(main["id"])}
    archive = db.get_paper(archive_key(main["id"], version))
    if archive and archive["source"] != PENDING:
        return {"id": archive["id"], "running": False}
    latest = latest_version(main["arxiv_id"])
    if latest is None:
        raise ArxivError("arXiv unreachable")
    if version > max(latest, main["version"] or 0):
        raise ValueError("No such version")
    if version == latest and version > (main["version"] or 0):
        update_in_background(main["id"])
        return {"id": main["id"], "running": True}
    return {"id": ensure_version(main, version), "running": True}


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


def repairing(paper_id):
    with _repairing_guard:
        return paper_id in _repairing


def _reimport_installed(paper):
    """Nouvel import de la version installée, même si arXiv en a une plus récente : rien n'est archivé ni déplacé."""
    arxiv_id, version = paper["arxiv_id"], paper["version"]
    ar5iv = paper["source"] == "ar5iv"
    if ar5iv and latest_version(arxiv_id) != version:  # ar5iv sert sa propre version de l'article : pas sous le numéro d'une plus ancienne
        return
    with ThreadPoolExecutor(1) as background:
        pending_source = background.submit(_source, arxiv_id, version)
        meta = fetch_metadata(f"{arxiv_id}v{version}")
        with paper_lock(paper["id"]):
            current = db.get_paper(paper["id"])
            if current and current["version"] == meta.version:  # sinon : mis à jour entre-temps
                _import(paper["id"], arxiv_id, meta, False, pending_source, ar5iv, paper["source"])


def repair_assets(paper_id):
    """Images manquantes redemandées (une fois à la fois par article) : d'après leurs adresses d'origine si l'import
    les a gardées, sinon par un nouvel import de la version installée (notes, place dans la bibliothèque et dossiers gardés)."""
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
            if paper and not paper.get("archived_of") and paper_id not in _repair_refused:
                _reimport_installed(paper)
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
            local = previous.get("stableIds")  # fichier TeX de l'ordinateur
            if local:  # numéros imprimés et ancres de sa dernière compilation (dossier de travail, s'il est encore là)
                from .texfile import apply_aux, build_dir
                apply_aux(structure, build_dir(paper_id))
            pdf = folder / "paper.pdf"
            if pdf.exists():
                texstructure.locate_pages(structure, pdf.read_bytes())
            structure["source"] = previous.get("source")
            structure["tex"] = previous.get("tex")
            if local:  # énoncés repérés par leur \label, sinon par leur texte
                from .texfile import stable_ids
                stable_ids(structure)
                structure["compiledAt"] = previous.get("compiledAt")  # adresse du PDF de cette compilation
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
            if not paper.get("arxiv_id"):  # PDF importé dont le fichier a disparu : rien à télécharger
                return None
            # version relue : une mise à jour a pu s'achever pendant l'attente du verrou
            data = fetch_pdf(paper["arxiv_id"], db.get_paper(paper["id"])["version"])
            if not data:
                return None
            write_atomic_bytes(path, data)
    return path


PAGES_VERSION = 3  # calcul des pages (texstructure.pages_for) changé : pages.json refaits


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
    stamp = f"{path.stat().st_mtime}:{STRUCTURE_VERSION}:{PAGES_VERSION}"
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("stamp") == stamp:
            return saved["located"]
    located = texstructure.pages_for(structure, path.read_bytes())
    write_atomic(cache, json.dumps({"stamp": stamp, "located": located}))
    return located


def set_in_library(paper_id, value):
    paper = db.get_paper(paper_id)
    if paper and paper.get("archived_of"):  # une version archivée n'entre jamais dans la bibliothèque
        return paper
    db.update_paper(paper_id, in_library=1 if value else 0)
    return db.get_paper(paper_id)


def clean_cache(days=30):
    """Articles lus sans être ajoutés à la bibliothèque, pas rouverts depuis `days` jours : effacés."""
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT id FROM papers WHERE in_library = 0 AND COALESCE(opened_at, added_at) < datetime('now', ?)",
            (f"-{days} days",)).fetchall()
        archives = connection.execute("SELECT id, archived_of FROM papers WHERE archived_of IS NOT NULL").fetchall()
    keep = _with_notes()  # article annoté ou discuté avec Claude : gardé
    keep |= {row["archived_of"] for row in archives if row["id"] in keep}  # ainsi que l'article d'une version archivée annotée
    removed = [row["id"] for row in rows if row["id"] not in keep]
    for paper_id in removed:
        remove_paper(paper_id)
    return len(removed)


def remove_paper(paper_id):
    from . import ai, annotations, bookjob, folders
    for archive in db.archives_of(paper_id):
        remove_paper(archive["id"])
    bookjob.forget(paper_id)  # livre : relecture ou résumé des chapitres en cours arrêtés
    shutil.rmtree(paper_dir(paper_id), ignore_errors=True)
    db.delete_paper(paper_id)
    folders.forget_paper(paper_id)
    annotations.delete_for(paper_id)
    ai.delete_for(paper_id)
