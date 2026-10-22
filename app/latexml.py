"""Version HTML expérimentale, produite sur l'ordinateur par LaTeXML (s'il est installé), pour les documents lus dans
leur PDF faute de version HTML : fichiers TeX importés (« local-tex ») et articles d'arXiv lus depuis leur source
(« tex »). Faite à la demande seulement (clic sur « HTML β ») : elle peut prendre quelques minutes et contenir des
erreurs ; le PDF reste la référence.

C'est l'outil qu'arXiv utilise pour ses propres pages HTML : la sortie est nettoyée et analysée comme elles
(sanitize, structure). Les identifiants des énoncés et des sections y sont remplacés par ceux de la structure tirée de
la source (texstructure : « tex-lbl-… », « tex-sec-… ») : notes et pages du PDF sont les mêmes dans les deux vues.

Rangement : dossier « html » de l'article (content.html, assets/, structure.json, state.json) ; content.html à la
racine de l'article reste réservé aux pages d'arXiv (sa présence change la lecture et l'analyse de l'article).
"""

import json
import os
import posixpath
import re
import shutil
import tarfile
import tempfile
import gzip
import io
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from . import annotations, db, texfile
from .library import paper_dir, write_atomic
from .sanitize import sanitize_document
from .structure import STRUCTURE_VERSION, classify, extract_structure
from .texsource import MAX_FILE_BYTES, MAX_MEMBERS, MAX_TOTAL_BYTES, choose_main

SOURCES = ("local-tex", "tex")
TIMEOUT = 600  # secondes : un livre peut demander plusieurs minutes
GUIDE = "https://math.nist.gov/~BMiller/LaTeXML/get.html"  # installation (Windows : Perl, puis LaTeXML)
DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "~/perl5/bin",
        "C:/Strawberry/perl/site/bin", "C:/Strawberry/perl/bin"]
BASE = "https://arxiv.org/html/latexml-local/"  # adresse fictive des images de la sortie (copiées, jamais demandées)
SECTION_CLASSES = ("ltx_part", "ltx_chapter", "ltx_section", "ltx_subsection", "ltx_subsubsection", "ltx_appendix")


class LatexmlError(ValueError):
    pass


def find():
    """Chemin de latexmlc, ou None."""
    found = shutil.which("latexmlc")
    if found:
        return found
    for folder in DIRS:
        for name in ("latexmlc", "latexmlc.bat"):
            candidate = Path(os.path.expanduser(folder)) / name
            if candidate.is_file():
                return str(candidate)
    return None


def html_dir(pid):
    return paper_dir(pid) / "html"


def _stamp(paper):
    """Ce dont la version HTML a été faite : compilation du fichier TeX, version de l'article d'arXiv."""
    if paper["source"] == "local-tex":
        return (texfile.report(paper["id"]) or {}).get("compiledAt") or ""
    return f"v{paper.get('version')}"


def _state_file(pid):
    try:
        return json.loads((html_dir(pid) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def state(paper):
    """« ready » (à jour), « stale » (faite pour une compilation ou une version précédente), « failed » (dernier essai
    raté, pour ce même état de la source), ou « none »."""
    if not paper or paper.get("source") not in SOURCES:
        return "none"
    saved = _state_file(paper["id"])
    if not saved:
        return "none"
    fresh = saved.get("stamp") == _stamp(paper) and saved.get("structureVersion") == STRUCTURE_VERSION
    if saved.get("ok"):
        return "ready" if fresh and (html_dir(paper["id"]) / "content.html").exists() else "stale"
    return "failed" if saved.get("stamp") == _stamp(paper) else "none"


def status(paper):
    saved = _state_file(paper["id"])
    job = texfile.job(job_id(paper["id"]))
    return {"state": "running" if job.get("running") else state(paper), "installed": bool(find()),
            "error": saved.get("error") if not saved.get("ok") else None, "guide": GUIDE}


def job_id(pid):
    return f"{pid}.html"


def structure(pid):
    try:
        return json.loads((html_dir(pid) / "structure.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def reanalyze(pid):
    """Structure de la version HTML refaite sur la page déjà produite (emplacement de preuve indiqué à la main)."""
    folder = html_dir(pid)
    previous = structure(pid) or {}
    fragment = BeautifulSoup((folder / "content.html").read_text(encoding="utf-8"), "html.parser")
    result = extract_structure(fragment, annotations.proof_overrides(pid))
    for key in ("source", "tex", "book", "compiledAt"):
        result[key] = previous.get(key)
    write_atomic(folder / "content.html", str(fragment))
    write_atomic(folder / "structure.json", json.dumps(result, ensure_ascii=False))
    return result


# ---------- Conversion ----------

def convert(pid):
    """Version HTML d'un document (tâche en arrière-plan) : {"state": "ready"} ; LatexmlError si elle échoue (la
    raison est gardée : le clic suivant la montre et propose de réessayer)."""
    paper = db.get_paper(pid)
    if not paper or paper.get("source") not in SOURCES:
        raise LatexmlError("no TeX source")
    program = find()
    if not program:
        raise LatexmlError("LaTeXML is not installed")
    stamp = _stamp(paper)
    work = Path(tempfile.mkdtemp(prefix="arxivreader-latexml-"))
    try:
        cwd, main, paths, use_bbl = _source(paper, work)
        paths = [*paths, _stubs(work)]
        out = work / "out"
        out.mkdir()
        try:
            html = _run(program, cwd, main, paths, out, use_bbl)
            _store(pid, html, out)
        except LatexmlError as error:
            _save_state(pid, {"ok": False, "stamp": stamp, "error": str(error)[:400]})
            raise
        _save_state(pid, {"ok": True, "stamp": stamp, "structureVersion": STRUCTURE_VERSION,
                          "at": datetime.now().isoformat(timespec="seconds")})
        return {"state": state(db.get_paper(pid))}  # « stale » si la source a été recompilée entre-temps
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _save_state(pid, value):
    folder = html_dir(pid)
    folder.mkdir(parents=True, exist_ok=True)
    write_atomic(folder / "state.json", json.dumps(value, ensure_ascii=False))


def _source(paper, work):
    """(dossier où lancer LaTeXML, fichier principal relatif à ce dossier, chemins de recherche en plus)."""
    if paper["source"] == "local-tex":
        main = Path((texfile._settings(paper["id"]) or {}).get("main") or "")
        if not main.is_file():
            raise LatexmlError(f"missing: {main}")
        # copie des fichiers TeX (figures pgfplots remplacées) avec le .bbl de la dernière compilation : un ancien .bbl
        # resté dans le dossier (autre style, amsrefs…) passerait avant lui. Images, paquets et .bib du dossier par
        # --path ; .bib choisis à la main : dossier de travail de la compilation. Rien n'est écrit chez l'utilisateur.
        build = texfile.build_dir(paper["id"])
        copy = work / "source"
        for name in texfile.read_project(main):
            if ".." in name:
                continue
            target = copy / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_without_plots_bytes((main.parent / name).read_bytes()))
        # tous les .bib trouvés : LaTeXML les lit (un .bbl au format amsrefs, par exemple, lui échappe) ; sinon le
        # .bbl de la compilation
        report = texfile.report(paper["id"]) or {}
        use_bbl = not report.get("bibs") or bool(report.get("missingBibs"))
        bbl = build / f"{main.stem}.bbl"
        if use_bbl and bbl.is_file():
            shutil.copyfile(bbl, copy / bbl.name)
        bib_dirs = [str(Path(b["path"]).parent) for b in report.get("bibs", [])]
        return copy, main.name, [main.parent, build, *dict.fromkeys(bib_dirs)], use_bbl
    from .arxiv import fetch_source
    data = fetch_source(paper["arxiv_id"], paper.get("version"))
    if not data:
        raise LatexmlError("source not available from arXiv")
    source = work / "source"
    source.mkdir()
    main = _extract(data, source)
    if not main:
        raise LatexmlError("no main TeX file in the source")
    for path in source.rglob("*"):
        if path.suffix.lower() in (".tex", ".ltx") and path.is_file():
            path.write_bytes(_without_plots_bytes(path.read_bytes()))
    # .bbl déposé avec la source (le cas habituel sur arXiv) : lui ; sinon le .bib
    return source, main, [], any(source.rglob("*.bbl"))


# Paquets de mise en page (KOMA-Script) que LaTeXML 0.8.8 ne connaît pas : il charge alors le vrai paquet, échoue
# (\FamilyProcessOptions), et la suite s'en ressent (bibliographie vide). Remplacés par des paquets vides, sans
# objet en HTML ; écrits dans le dossier de la conversion (rien à installer).
LAYOUT_ONLY = ("typearea", "scrlayer-scrpage", "scrlayer", "scrextend", "scrbase", "scrkbase")


def _stubs(work):
    folder = work / "stubs"
    folder.mkdir(exist_ok=True)
    for name in LAYOUT_ONLY:
        (folder / f"{name}.sty.ltxml").write_text(
            "package LaTeXML::Package::Pool;\nuse strict;\nuse LaTeXML::Package;\n1;\n", encoding="utf-8")
    return folder


# Figures pgfplots (courbes, surfaces) : LaTeXML les calcule point par point, des dizaines de minutes pour une seule
# surface 3D ; remplacées par une mention (le PDF les montre). Les autres figures TikZ et tikz-cd sont gardées.
PGFPLOTS = re.compile(r"\\begin\{(?:axis|semilogxaxis|semilogyaxis|loglogaxis|polaraxis|groupplot)\}|\\addplot")
TIKZPICTURE = re.compile(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", re.S)


def _without_plots_bytes(data):
    """Même remplacement sur les octets du fichier (encodage d'origine gardé : latin-1 aussi)."""
    text = data.decode("utf-8", errors="surrogateescape")
    return _without_plots(text).encode("utf-8", errors="surrogateescape") if PGFPLOTS.search(text) else data


def _without_plots(text):
    note = "Figure omise dans la version HTML (pgfplots) : voir le PDF." if db.get_settings().get("language") == "fr" \
        else "Figure omitted in the HTML version (pgfplots): see the PDF."
    return TIKZPICTURE.sub(lambda m: f"\\fbox{{{note}}}" if PGFPLOTS.search(m.group(0)) else m.group(0), text)


def _extract(data, folder):
    """Source d'arXiv extraite dans `folder` (archive tar : fichiers ordinaires seulement, chemins qui restent dans le
    dossier, tailles bornées ; sinon un seul .tex compressé) ; renvoie le fichier principal, ou None."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            members, total = [], 0
            for index, member in enumerate(archive):
                if index >= MAX_MEMBERS:
                    break
                name = posixpath.normpath(member.name.lstrip("/"))
                if not member.isfile() or name.startswith("..") or member.size > MAX_FILE_BYTES or total + member.size > MAX_TOTAL_BYTES:
                    continue
                total += member.size
                members.append(member)
            archive.extractall(folder, members=members, filter="data")
    except tarfile.TarError:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                raw = compressed.read(MAX_FILE_BYTES + 1)
        except (OSError, EOFError):
            raw = data
        if len(raw) > MAX_FILE_BYTES or b"\\documentclass" not in raw:
            return None
        (folder / "main.tex").write_bytes(raw)
        return "main.tex"
    texts = {}
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() in (".tex", ".ltx") and path.is_file():
            texts[path.relative_to(folder).as_posix()] = path.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    readme = folder / "00README.json"
    return choose_main(texts, readme.read_text(encoding="utf-8", errors="replace") if readme.is_file() else None)


def _run(program, cwd, main, paths, out, use_bbl=False):
    """LaTeXML lancé dans le dossier du document, sur un chemin relatif (les espaces dans les chemins le gênent) ;
    renvoie la page produite. Bibliographie : le .bib (LaTeXML le lit lui-même, comme sur arXiv), sinon le .bbl
    (`use_bbl`)."""
    kpsewhich = texfile.find_tool("kpsewhich")  # LaTeXML cherche les paquets de TeX par kpsewhich
    env = texfile._env(kpsewhich or program)
    env["PATH"] = os.pathsep.join([str(Path(program).parent), env["PATH"]])
    command = [program, "--quiet", "--format=html5", "--nodefaultresources", "--pmml", "--mathtex", "--timestamp=0",
               *(["--preload=[nobibtex]latexml.sty"] if use_bbl else []),
               *(f"--path={p}" for p in paths), f"--dest={out / 'index.html'}", f"--log={out / 'log.txt'}", main]
    try:
        texfile._run(command, str(cwd), env, TIMEOUT)
    except texfile.TexError as error:
        raise LatexmlError("timeout" if str(error) == "timeout" else str(error))
    page = out / "index.html"
    if not page.is_file():
        log = (out / "log.txt").read_text(encoding="utf-8", errors="replace") if (out / "log.txt").is_file() else ""
        fatal = [line for line in log.splitlines() if line.startswith(("Fatal", "Error"))]
        raise LatexmlError(fatal[0][:300] if fatal else "no HTML produced")
    return page.read_text(encoding="utf-8", errors="replace")


def _store(pid, html, out):
    try:
        fragment, assets = sanitize_document(html, BASE)
    except ValueError as error:
        raise LatexmlError(str(error))
    add_viewboxes(fragment)
    tex = json.loads((paper_dir(pid) / "structure.json").read_text(encoding="utf-8"))
    align_ids(fragment, tex)
    structure = extract_structure(fragment, annotations.proof_overrides(pid))
    structure["source"] = "latexml"
    structure["tex"] = tex.get("tex")
    structure["book"] = tex.get("book", False)
    structure["compiledAt"] = tex.get("compiledAt")  # adresse du PDF de cette compilation
    folder = html_dir(pid)
    staging = Path(tempfile.mkdtemp(dir=paper_dir(pid), prefix=".html."))
    try:
        (staging / "assets").mkdir()
        for local, remote in assets:  # images de la sortie (dossier de LaTeXML), copiées sous leur nom local
            source = out / remote[len(BASE):] if remote.startswith(BASE) else None
            if source and source.is_file() and source.resolve().is_relative_to(out.resolve()):
                shutil.copyfile(source, staging / "assets" / local)
        (staging / "content.html").write_text(str(fragment), encoding="utf-8")
        (staging / "structure.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
        if folder.exists():  # state.json précédent gardé jusqu'au nouveau
            for name in ("content.html", "structure.json"):
                (folder / name).unlink(missing_ok=True)
            shutil.rmtree(folder / "assets", ignore_errors=True)
        folder.mkdir(exist_ok=True)
        for item in staging.iterdir():
            item.replace(folder / item.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def add_viewboxes(fragment):
    """Dessins TikZ sans viewBox (LaTeXML d'avant 0.8.9 ; celui d'arXiv l'écrit) : ajouté d'après leur taille, sinon
    le dessin ne peut pas être mis à l'échelle (rogné dans une colonne étroite, pas agrandi au clic)."""
    for svg in fragment.select("svg.ltx_picture"):
        width, height = svg.get("width"), svg.get("height")
        if svg.get("viewbox") or not width or not height:
            continue
        try:
            svg["viewbox"] = f"0 0 {float(width):g} {float(height):g}"
        except ValueError:
            continue


# ---------- Identifiants communs avec la structure tirée de la source ----------

def _title(element):
    title = element.select_one(".ltx_title")
    return re.sub(r"\s+", " ", title.get_text(" ", strip=True)) if title else ""


def _same_label(title, label):
    """« Lemma 2.2 . », « Lemma 2.2 (Name). » : l'énoncé « Lemma 2.2 » de la source."""
    title = re.sub(r"\s*([.:])", r"\1", title).strip()
    return title == label or title.startswith(label + ".") or title.startswith(label + " ") or title.startswith(label + ":")


def align_ids(fragment, tex):
    """Énoncés, sections, entrées de la bibliographie et équations numérotées de la page de LaTeXML renommés comme dans
    la structure tirée de la source ; liens internes mis à jour. Énoncés rapprochés dans l'ordre du document, type par
    type (les deux listes suivent la source ; un numéro simulé peut être décalé, l'ordre non) ; un énoncé que l'une
    des deux n'a pas garde son identifiant. Modifie `fragment` sur place."""
    from difflib import SequenceMatcher
    renamed = {}
    theorems = fragment.select(".ltx_theorem[id]")
    nodes = list(tex.get("nodes", []))
    kinds = [classify(_title(e), e.get("class") or []) for e in theorems]
    blocks = SequenceMatcher(None, kinds, [n["kind"] for n in nodes], autojunk=False).get_matching_blocks()
    for a, b, size in blocks:
        for offset in range(size):
            renamed[theorems[a + offset]["id"]] = nodes[b + offset]["id"]
    sections = [e for e in fragment.find_all("section", id=True) if set(e.get("class") or []) & set(SECTION_CLASSES)]
    toc = tex.get("toc", [])
    if len(sections) == len(toc):
        renamed.update({element["id"]: entry["id"] for element, entry in zip(sections, toc)})
    else:  # nombre différent (parties, annexes comptées autrement) : par le titre
        free = list(toc)
        for element in sections:
            title = _title(element).lower()
            entry = next((t for t in free if t["title"].lower() == title or t["title"].lower().endswith(" " + title.split(" ", 1)[-1])), None)
            if entry:
                renamed[element["id"]] = entry["id"]
                free.remove(entry)
    # bibliographie : même ordre (le .bbl de la compilation, ou thebibliography de la source)
    items = fragment.select(".ltx_bibitem[id]")
    bibliography = tex.get("bibliography", [])
    if items and len(items) == len(bibliography):
        renamed.update({element["id"]: entry["id"] for element, entry in zip(items, bibliography)})
    # équations numérotées : par leur numéro imprimé (connu par le .aux pour un fichier TeX)
    by_number = {eq["label"]: key for key, eq in tex.get("equations", {}).items() if eq.get("label")}
    for tag in fragment.select(".ltx_tag_equation"):
        holder = tag.find_parent(id=True)
        key = by_number.get(tag.get_text(strip=True))
        if holder is not None and key and key not in renamed.values():
            renamed[holder["id"]] = key
    taken = set(renamed.values())
    for element in fragment.find_all(id=True):
        if element["id"] in renamed:
            element["id"] = renamed[element["id"]]
        elif element["id"] in taken:  # identifiant déjà pris par un élément renommé (improbable) : écarté
            del element["id"]
    for link in fragment.find_all(href=True):
        href = link["href"]
        if href.startswith("#") and href[1:] in renamed:
            link["href"] = "#" + renamed[href[1:]]
    return renamed
