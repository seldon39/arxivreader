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
import sys
import tarfile
import tempfile
import time
import gzip
import io
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from . import annotations, db, texfile, texstructure
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
    for key in ("source", "tex", "book", "compiledAt", "macros"):
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
            if _lost_bibliography(html, cwd) and (_biblatex_bibliography(paper, cwd, main)
                                                  or not use_bbl and _bbl(paper, cwd, main)):
                again = work / "out-bbl"  # bibliographie d'après le .bbl, comme sur arXiv
                again.mkdir()
                try:
                    retry = _run(program, cwd, main, paths, again, True)
                except LatexmlError:  # la première conversion reste bonne
                    retry = ""
                if "ltx_bibitem" in retry:
                    html, out = retry, again
            _store(pid, html, out)
        except LatexmlError as error:
            _save_state(pid, {"ok": False, "stamp": stamp, "error": str(error)[:400]})
            raise
        _save_state(pid, {"ok": True, "stamp": stamp, "structureVersion": STRUCTURE_VERSION,
                          "at": datetime.now().isoformat(timespec="seconds")})
        return {"state": state(db.get_paper(pid))}  # « stale » si la source a été recompilée entre-temps
    finally:
        shutil.rmtree(work, ignore_errors=True)


# Bibliographie lue dans le .bib par LaTeXML : il recharge pour cela tout le préambule, et un paquet qu'il ne connaît
# pas (pdfx, CJK…) peut l'arrêter ; la bibliographie sort vide. Seconde conversion d'après un .bbl : celui de la
# compilation (fichier importé) ou celui que BibTeX fait des citations de l'article (source d'arXiv sans .bbl).
BIBDATA = re.compile(r"\\bibliography\s*\{([^{}]+)\}")
BIBLATEX = re.compile(r"\\(?:addbibresource|printbibliography|bibliography)\b")
PRINTBIBLIOGRAPHY = re.compile(r"\\printbibliography\s*(?:\[[^\]]*\])?")
TEX_SPECIAL = re.compile(r"([&%#_$])")
BIBSTYLE = re.compile(r"\\bibliographystyle\s*\{([^{}]+)\}")
CITE = re.compile(r"\\(?:no)?cite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^{}]*)\}")


def _texts(cwd):
    return "\n".join(re.sub(r"(?<!\\)%[^\n]*", "", path.read_bytes().decode("utf-8", errors="replace"))
                     for path in sorted(cwd.rglob("*")) if path.suffix.lower() in (".tex", ".ltx") and path.is_file())


def _lost_bibliography(html, cwd):
    return "ltx_bibitem" not in html and bool(BIBLATEX.search(_texts(cwd)))


def _bib_text(text):
    """Texte d'une entrée pour thebibliography : formules $…$ gardées si leurs accolades s'équilibrent, le reste
    échappé (caractères spéciaux de TeX)."""
    parts = re.split(r"(\$[^$]+\$)", text)
    out = []
    for part in parts:
        if part.startswith("$") and part.endswith("$") and len(part) > 1 and _balanced(part):
            out.append(part)
        else:
            out.append(re.sub(r"[\\{}$&%#_^~]", lambda m: {"\\": r"\textbackslash{}", "^": r"\^{}", "~": r"\~{}"}
                              .get(m.group(0), "\\" + m.group(0)), part))
    return "".join(out)


def _balanced(text):
    depth = 0
    for index, char in enumerate(text):
        if char in "{}" and index and text[index - 1] == "\\":
            continue
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth < 0:
            return False
    return depth == 0


def _biblatex_bibliography(paper, cwd, main):
    """.bbl de biblatex (biber) : LaTeXML ne le lit pas. Ses entrées (auteurs, titre, revue, année) deviennent une
    liste thebibliography, mise à la place du premier \\printbibliography (les suivants sont retirés : une seule liste ;
    ceux des commentaires ne comptent pas). True si fait."""
    stem = Path(main).stem
    candidates = [cwd / Path(main).parent / f"{stem}.bbl", *sorted(cwd.rglob("*.bbl"))]
    if paper["source"] == "local-tex":
        candidates.insert(0, texfile.build_dir(paper["id"]) / f"{stem}.bbl")
    text = next((path.read_text(encoding="utf-8", errors="replace") for path in candidates if path.is_file()), "")
    entries = texstructure._biblatex(text) if "\\entry{" in text else []
    if not entries:
        return False
    main_path = cwd / main
    main_text = main_path.read_bytes()
    try:
        main_text.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:  # document en latin-1 (inputenc) : la liste dans le même codage
        encoding = "latin-1"
    items = "".join(f"\\bibitem{{{entry['id'].removeprefix('bib.')}}} {_bib_text(entry['text'])}.\n" for entry in entries)
    (cwd / "arxivreader-bibliography.tex").write_bytes(
        ("\\begin{thebibliography}{99}\n" + items + "\\end{thebibliography}\n").encode(encoding, errors="replace"))
    files = [main_path] + [path for path in sorted(cwd.rglob("*")) if path != main_path and path.is_file()
                           and path.suffix.lower() in (".tex", ".ltx") and path.name != "arxivreader-bibliography.tex"]
    placed = False
    for path in files:
        source = path.read_bytes().decode("utf-8", errors="surrogateescape")
        found = list(PRINTBIBLIOGRAPHY.finditer(_masked(source)))
        for match in reversed(found):
            value = "\\input{arxivreader-bibliography}" if not placed and match is found[0] else ""
            source = source[:match.start()] + value + source[match.end():]
        if found:
            placed = True
            path.write_bytes(source.encode("utf-8", errors="surrogateescape"))
    if not placed:
        source = main_path.read_bytes().decode("utf-8", errors="surrogateescape")
        source = source.replace("\\end{document}", "\\input{arxivreader-bibliography}\n\\end{document}", 1)
        main_path.write_bytes(source.encode("utf-8", errors="surrogateescape"))
    return True


def _bbl(paper, cwd, main):
    """Un .bbl à côté du fichier principal (True), d'après la compilation ou BibTeX."""
    stem = Path(main).stem
    target = cwd / Path(main).parent / f"{stem}.bbl"
    if paper["source"] == "local-tex":
        built = texfile.build_dir(paper["id"]) / f"{stem}.bbl"
        if built.is_file():
            shutil.copyfile(built, target)
            return True
        return False
    bibtex = texfile.find_tool("bibtex")
    text = _texts(cwd)
    data = BIBDATA.search(text)
    if not bibtex or not data:
        return False
    keys = list(dict.fromkeys(key.strip() for found in CITE.finditer(text) for key in found.group(1).split(",") if key.strip()))
    style = BIBSTYLE.search(text)
    for name in dict.fromkeys([style.group(1).strip() if style else "plain", "plain"]):
        (target.with_suffix(".aux")).write_text(
            "\\relax\n" + "".join(f"\\citation{{{key}}}\n" for key in keys)
            + f"\\bibstyle{{{name}}}\n\\bibdata{{{data.group(1)}}}\n", encoding="utf-8")
        try:
            texfile._run([bibtex, stem], str(target.parent), texfile._env(bibtex), 120)
        except texfile.TexError:
            return False
        if target.is_file() and "\\bibitem" in target.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


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
        labels = texfile.aux_labels(build)
        copy = work / "source"
        for name in texfile.read_project(main):
            if ".." in name:
                continue
            target = copy / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_without_plots_bytes((main.parent / name).read_bytes(), labels))
        # tous les .bib trouvés : LaTeXML les lit (un .bbl au format amsrefs, par exemple, lui échappe) ; sinon le
        # .bbl de la compilation
        report = texfile.report(paper["id"]) or {}
        use_bbl = not report.get("bibs") or bool(report.get("missingBibs"))
        bbl = build / f"{main.stem}.bbl"
        if use_bbl and bbl.is_file():
            shutil.copyfile(bbl, copy / bbl.name)
        macros = _simple_macros(paper["id"])
        for bib in report.get("bibs", []):  # copiés à côté du fichier, macros de l'auteur développées
            target = copy / bib["name"]
            if Path(bib["path"]).is_file() and ".." not in bib["name"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(_expand_macros(Path(bib["path"]).read_bytes(), macros))
        folders = dict.fromkeys([main.parent, build, *(Path(b["path"]).parent for b in report.get("bibs", []))])
        _raster_figures(copy, [copy, main.parent])
        return copy, main.name, [_ascii(folder, work, index) for index, folder in enumerate(folders)], use_bbl
    from .arxiv import fetch_source
    data = fetch_source(paper["arxiv_id"], paper.get("version"))
    if not data:
        raise LatexmlError("source not available from arXiv")
    source = work / "source"
    source.mkdir()
    main = _extract(data, source)
    if not main:
        raise LatexmlError("no main TeX file in the source")
    macros = _simple_macros(paper["id"])
    for path in source.rglob("*"):
        if path.suffix.lower() in (".tex", ".ltx") and path.is_file():
            path.write_bytes(_without_plots_bytes(path.read_bytes()))
        elif path.suffix.lower() == ".bib" and path.is_file():
            path.write_bytes(_expand_macros(path.read_bytes(), macros))
    _raster_figures(source, [source])
    # .bbl déposé avec la source (le cas habituel sur arXiv) : lui ; sinon le .bib
    return source, main, [], any(source.rglob("*.bbl"))


# Figures en PDF : LaTeXML ne les convertit qu'avec le module Perl Image::Magick, absent des installations courantes
# (Homebrew, Windows) ; elles restaient vides (« ltx_missing_image »). Converties ici en PNG (sips de macOS, sinon le
# Ghostscript de TeX), dans le dossier de travail ; \includegraphics vise alors le PNG, à la largeur de la figure
# d'origine (LaTeXML prend sinon la taille en pixels de l'image).
INCLUDE = re.compile(r"\\includegraphics\s*(\*?)\s*(?:\[([^\]]*)\])?\s*\{([^{}]+)\}")
GRAPHICSPATH = re.compile(r"\\graphicspath\s*\{((?:\s*\{[^{}]*\})*)\s*\}")
RASTER = (".png", ".jpg", ".jpeg", ".gif", ".svg")
MAX_FIGURES = 150
FIGURES_SECONDS = 180  # pour toutes les figures d'un document ; au-delà, les suivantes restent manquantes
FIGURE_DPI = 200
MAX_PIXELS = 2400  # côté le plus long de l'image produite
UNSIZED = ("width", "height", "totalheight", "angle", "trim", "viewport", "bb", "natwidth", "natheight")


def _rasterizer():
    if sys.platform == "darwin" and shutil.which("sips"):
        return "sips"
    for name in ("gswin64c", "gswin32c", "gs", "rungs", "mgs"):
        found = texfile.find_tool(name)
        if found:
            return found
    return None


def _pdf_size(path, page):
    """(largeur, hauteur) de la page `page` d'un PDF, en points (None si illisible ou absente)."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        box = reader.pages[page - 1].cropbox
        return abs(float(box.width)) or None, abs(float(box.height)) or None
    except Exception:
        return None, None


def _rasterize(tool, source, target, size, page, timeout):
    width, height = size
    longest = max(width or 300, height or 0)
    if tool == "sips":
        if page != 1:
            return False  # sips ne rend que la première page
        pixels = str(max(64, min(MAX_PIXELS, round(longest * FIGURE_DPI / 72))))
        command = ["sips", "-s", "format", "png", "-Z", pixels, str(source), "--out", str(target)]
    else:
        dpi = max(10, min(FIGURE_DPI, int(MAX_PIXELS * 72 / longest)))
        command = [tool, "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m", f"-r{dpi}", "-dUseCropBox",
                   f"-dFirstPage={page}", f"-dLastPage={page}", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
                   f"-sOutputFile={target}", str(source)]
    try:
        texfile._run(command, str(target.parent), dict(os.environ), max(1, timeout))
    except texfile.TexError:
        return False
    return target.is_file() and target.stat().st_size > 0


def _option(keys, name):
    found = next((key for key in keys if re.match(name + r"\s*=", key)), None)
    return found.split("=", 1)[1].strip() if found else None


def _with_width(options, width):
    """Options de \\includegraphics : largeur de la figure d'origine (échelle comprise) si aucune taille n'est donnée
    (ni rotation ni découpe, que la largeur imposée fausserait). « page= » retiré : le PNG est cette page."""
    keys = [part.strip() for part in (options or "").split(",") if part.strip()]
    kept = [key for key in keys if not re.match(r"page\s*=", key)]
    if not width or any(re.match(name + r"\s*=", key) for key in kept for name in UNSIZED):
        return ",".join(kept)
    scale = next((key for key in kept if re.match(r"scale\s*=", key)), None)
    try:
        factor = float(scale.split("=", 1)[1]) if scale else 1.0
    except ValueError:
        return ",".join(kept)
    return ",".join([key for key in kept if key != scale] + [f"width={width * factor:.2f}pt"])


def _is_pdf(path):
    """Vrai PDF (pas du PostScript nommé .pdf, que Ghostscript exécuterait)."""
    try:
        with open(path, "rb") as handle:
            return b"%PDF-" in handle.read(1024)
    except OSError:
        return False


def _raster_figures(copy, folders):
    """Figures PDF des fichiers TeX de `copy` converties en PNG (dossier « arxivreader-figures » de `copy`)."""
    tool = _rasterizer()
    if not tool:
        return
    texts = {path: path.read_bytes().decode("utf-8", errors="surrogateescape")
             for path in copy.rglob("*") if path.suffix.lower() in (".tex", ".ltx") and path.is_file()}
    prefixes = [""]
    for text in texts.values():
        for found in GRAPHICSPATH.finditer(_masked(text)):
            prefixes += re.findall(r"\{([^{}]*)\}", found.group(1))
    out = copy / "arxivreader-figures"
    done = {}
    deadline = time.monotonic() + FIGURES_SECONDS

    def locate(name):
        name = name.strip()
        stem = name[:-4] if name.lower().endswith(".pdf") else name
        for folder in folders:
            root = folder.resolve()
            for prefix in prefixes:
                base = (folder / prefix / stem).resolve()
                if name == stem and any(base.with_name(base.name + ext).is_file() for ext in RASTER
                                        if base.with_name(base.name + ext).resolve().is_relative_to(root)):
                    return None  # image déjà lisible par LaTeXML
                pdf = base.with_name(base.name + ".pdf").resolve()
                if pdf.is_relative_to(root) and pdf.is_file() and pdf.stat().st_size < 50_000_000 and _is_pdf(pdf):
                    return pdf
        return None

    def replace(match):
        star, options, name = match.groups()
        pdf = locate(name)
        if pdf is None:
            return None
        try:
            page = int(_option([part.strip() for part in (options or "").split(",")], "page") or 1)
        except ValueError:
            return None
        if (pdf, page) not in done:
            remaining = deadline - time.monotonic()
            if len(done) >= MAX_FIGURES or remaining <= 0:
                return None
            out.mkdir(exist_ok=True)
            target = out / f"{len(done) + 1}.png"
            size = _pdf_size(pdf, page)
            ok = size != (None, None) and _rasterize(tool, pdf, target, size, page, min(60, remaining))
            done[(pdf, page)] = (target.relative_to(copy).as_posix(), size[0]) if ok else None
        if done[(pdf, page)] is None:
            return None
        png, width = done[(pdf, page)]
        options = _with_width(options, width)
        return f"\\includegraphics{star}{f'[{options}]' if options else ''}{{{png}}}"

    for path, text in texts.items():
        masked = _masked(text)  # commentaires et code affiché : laissés tels quels
        edits = [(m.start(), m.end(), value) for m in INCLUDE.finditer(masked)
                 if (value := replace(INCLUDE.match(text, m.start()))) is not None]
        for start_at, end_at, value in reversed(edits):
            text = text[:start_at] + value + text[end_at:]
        if edits:
            path.write_bytes(text.encode("utf-8", errors="surrogateescape"))


# Paquets de mise en page (KOMA-Script) que LaTeXML 0.8.8 ne connaît pas : il charge alors le vrai paquet, échoue
# (\FamilyProcessOptions), et la suite s'en ressent (bibliographie vide). Remplacés par des paquets vides, sans
# objet en HTML ; écrits dans le dossier de la conversion (rien à installer).
LAYOUT_ONLY = ("typearea", "scrlayer-scrpage", "scrlayer", "scrextend", "scrbase", "scrkbase")


# Étiquettes des dessins TikZ : LaTeXML 0.8.8 ne note que la hauteur totale de chaque étiquette et la pose une
# interligne plus haut. Celui d'arXiv note hauteur, profondeur et corps (--ltx-fo-…) et la pose sur sa ligne de base :
# figures.js s'en sert pour la placer comme dans le PDF. Même chose ici, ajoutée après coup (rien si déjà fait).
# Étiquette faite de texte seul (deuxième ligne d'un nœud « align=center ») : LaTeXML en fait un <text> SVG sans corps
# ni place ; enveloppée, elle reste une étiquette comme les autres. \arxivreaderref : renvoi dont le numéro est déjà
# connu (_figure_refs).
# Matrices TikZ (diagrammes) : 0.8.8 estime la ligne de base de la dernière ligne (moitié de sa hauteur) ; elle est
# connue, c'est la position de cette ligne (comme dans la version suivante de LaTeXML). Classe « ltx_tikzmatrix_base » :
# figures.js ne leur applique pas la correction faite pour les pages d'arXiv.
FIGURE_LABELS = r"""package LaTeXML::Package::Pool;
use strict;
use LaTeXML::Package;
Tag('svg:foreignObject', 'afterClose:early' => sub {
    my ($document, $node) = @_;
    my @children = $node->childNodes;
    return unless @children && !grep { $_->nodeType != 3 } @children;
    return unless grep { $_->textContent =~ /\S/ } @children;
    $document->wrapNodes('ltx:text', @children); });
DefConstructor('\\arxivreaderref Semiverbatim {}', "<ltx:ref labelref='#label'>#2</ltx:ref>",
  mode => 'text', bounded => 1, sizer => '#2', properties => sub { (label => CleanLabel($_[1])); });
my @version = split(/\./, $LaTeXML::VERSION || '0');
if (($version[0] || 0) == 0 && (($version[1] || 0) < 8 || (($version[1] || 0) == 8 && ($version[2] || 0) <= 8))) {
  Tag('svg:g', afterClose => sub {
      my ($document, $node) = @_;
      return unless ($node->getAttribute('class') || '') eq 'ltx_tikzmatrix';
      my @rows = grep { $_->nodeType == 1 && ($_->getAttribute('class') || '') =~ /^ltx_tikzmatrix_row\b/ } $node->childNodes;
      return unless @rows;
      my ($y) = ($rows[-1]->getAttribute('transform') || '') =~ /^matrix\(1 0 0 1 0 (-?[\d.]+)\)$/;
      my ($x) = ($node->getAttribute('transform') || '') =~ /^matrix\(1 0 0 -1 (-?[\d.]+) -?[\d.]+\)$/;
      return unless defined $x && defined $y;
      $node->setAttribute(transform => "matrix(1 0 0 -1 $x $y)");
      $node->setAttribute(class => 'ltx_tikzmatrix ltx_tikzmatrix_base'); }); }
Tag('svg:foreignObject', afterClose => sub {
    my ($document, $node, $whatsit) = @_;
    return unless $whatsit && $node->parentNode && $document->getNodeQName($node) eq 'svg:foreignObject';
    my $style = $node->getAttribute('style') || '';
    return if $style =~ /--ltx-fo-height/;
    my ($w, $h, $d) = $whatsit->getSize;
    return unless $w && $h && $d;
    my $font = $whatsit->getFont;
    my $size = ($font && $font->getSize) || 10;
    my $em   = sub { sprintf('%.2f', $_[0]->ptValue / $size); };
    $node->setAttribute(transform => 'matrix(1 0 0 -1 0 ' . $h->pxValue . ')');
    $node->setAttribute(style => '--ltx-fo-width:' . &$em($w) . 'em;--ltx-fo-height:' . &$em($h)
        . 'em;--ltx-fo-depth:' . &$em($d) . 'em;font-size:' . $size . 'pt;' . $style); });
1;
"""


def _stubs(work):
    folder = work / "stubs"
    folder.mkdir(exist_ok=True)
    for name in LAYOUT_ONLY:
        (folder / f"{name}.sty.ltxml").write_text(
            "package LaTeXML::Package::Pool;\nuse strict;\nuse LaTeXML::Package;\n1;\n", encoding="utf-8")
    (folder / "arxivreader-figures.sty.ltxml").write_text(FIGURE_LABELS, encoding="utf-8")
    return folder


# Figures pgfplots (courbes, surfaces) : LaTeXML les calcule point par point, des dizaines de minutes pour une seule
# surface 3D ; remplacées par une mention (le PDF les montre). Les autres figures TikZ et tikz-cd sont gardées.
PGFPLOTS = re.compile(r"\\begin\{(?:axis|semilogxaxis|semilogyaxis|loglogaxis|polaraxis|groupplot)\}|\\addplot")
TIKZPICTURE = re.compile(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", re.S)


def _without_plots_bytes(data, labels=None):
    """Même remplacement sur les octets du fichier (encodage d'origine gardé : latin-1 aussi), et numéros des renvois
    des dessins (`labels` : ceux du .aux de la compilation)."""
    text = data.decode("utf-8", errors="surrogateescape")
    changed = _figure_refs(text, labels) if labels else text
    if PGFPLOTS.search(changed):
        changed = _without_plots(changed)
    return changed.encode("utf-8", errors="surrogateescape") if changed != text else data


FIGURE_ENV = re.compile(r"\\begin\{(tikzpicture|tikzcd)\}.*?\\end\{\1\}", re.S)
FIGURE_REF = re.compile(r"\\(eq)?ref\s*\{([^{}]+)\}")
# commentaires et code affiché : masqués (même longueur) avant la recherche des dessins et des renvois
HIDDEN = re.compile(r"(?<!\\)%[^\n]*|\\begin\{(verbatim\*?|lstlisting|minted|comment)\}.*?\\end\{\1\}"
                    r"|\\lstinline\*?(?:\[[^\]]*\])?\{[^{}\n]*\}"
                    r"|\\(?:verb|lstinline)\*?([^\sa-zA-Z{]).*?\2", re.S)


def _masked(text):
    """Texte où commentaires et code affiché sont remplacés par des espaces (positions inchangées)."""
    return HIDDEN.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _figure_refs(text, labels):
    """Renvois (\\ref, \\eqref) dans un dessin TikZ : LaTeXML leur donne une largeur fixe (le numéro n'est connu
    qu'après), et les boîtes des nœuds sont dessinées trop étroites (« A3 » réduit dans une boîte faite pour un
    chiffre). Numéro imprimé par LaTeX à la place, toujours lien vers sa cible (\\arxivreaderref, FIGURE_LABELS)."""
    masked = _masked(text)
    edits = []
    for figure in FIGURE_ENV.finditer(masked):
        for match in FIGURE_REF.finditer(masked, figure.start(), figure.end()):
            key = match.group(2).strip()
            number = labels.get(key, (None,))[0]
            if not number or re.search(r"[\\{}$&#^_%~]", number):
                continue
            link = f"\\arxivreaderref{{{key}}}{{{number}}}"
            edits.append((match.start(), match.end(), f"\\textup{{({link})}}" if match.group(1) else link))
    for start, end, value in reversed(edits):
        text = text[:start] + value + text[end:]
    return text


def _without_plots(text):
    note = "Figure omise dans la version HTML (pgfplots) : voir le PDF." if db.get_settings().get("language") == "fr" \
        else "Figure omitted in the HTML version (pgfplots): see the PDF."
    return TIKZPICTURE.sub(lambda m: f"\\fbox{{{note}}}" if PGFPLOTS.search(m.group(0)) else m.group(0), text)


def _simple_macros(pid):
    """Macros sans argument de l'auteur ({nom: définition}), d'après la structure tirée de la source."""
    try:
        listed = json.loads((paper_dir(pid) / "structure.json").read_text(encoding="utf-8")).get("macros") or []
    except (OSError, ValueError):
        return {}
    found = {}
    for macro in listed:
        match = re.fullmatch(r"\\newcommand\{\\([A-Za-z]+)\}\{(.*)\}", macro, re.S)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def _expand_macros(data, macros):
    """.bib dont les formules emploient les macros de l'auteur (« $\\mC^k$ ») : LaTeXML lit la bibliographie sans le
    préambule du document ; ces macros y sont remplacées par leur définition (encodage d'origine gardé)."""
    if not macros:
        return data
    text = data.decode("utf-8", errors="surrogateescape")
    pattern = re.compile(r"\\(" + "|".join(map(re.escape, sorted(macros, key=len, reverse=True))) + r")(?![A-Za-z])")
    return pattern.sub(lambda m: "{" + macros[m.group(1)] + "}", text).encode("utf-8", errors="surrogateescape")


def _ascii(folder, work, index):
    """Chemin de recherche que LaTeXML sait ouvrir : un dossier dont le chemin a des accents (« Thèse ») lui échappe
    (.bib et images introuvables) ; il passe alors par un lien symbolique au nom simple, dans le dossier de la
    conversion. Sans lien possible (Windows sans le droit d'en créer) : le chemin tel quel."""
    folder = Path(folder)
    if str(folder).isascii():
        return folder
    link = work / f"path{index}"
    try:
        link.symlink_to(folder, target_is_directory=True)
        return link
    except OSError:
        return folder


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
               *(["--preload=[nobibtex]latexml.sty"] if use_bbl else []), "--preload=arxivreader-figures.sty",
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
    fix_patterns(fragment)
    fix_tiny_images(fragment)
    tex = json.loads((paper_dir(pid) / "structure.json").read_text(encoding="utf-8"))
    align_ids(fragment, tex)
    structure = extract_structure(fragment, annotations.proof_overrides(pid))
    structure["source"] = "latexml"
    structure["tex"] = tex.get("tex")
    structure["book"] = tex.get("book", False)
    structure["compiledAt"] = tex.get("compiledAt")  # adresse du PDF de cette compilation
    structure["macros"] = tex.get("macros", [])  # macros de l'auteur, pour MathJax (comme la vue PDF)
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


def fix_tiny_images(fragment):
    """Image réduite à quelques pixels : largeur donnée par une longueur que LaTeXML ne connaît pas (longueur d'une
    classe, « 0.86\\normaltextwidth » vaut 0). Sans dimensions, elle prend sa taille, bornée par la colonne."""
    for image in fragment.select("img.ltx_graphics"):
        try:
            tiny = float(image.get("width", 100)) < 8 and float(image.get("height", 100)) < 8
        except ValueError:
            continue
        if tiny:
            for attribute in ("width", "height"):
                image.attrs.pop(attribute, None)
            style = re.sub(r"aspect-ratio\s*:[^;]*;?", "", image.get("style", "")).strip()
            if style:
                image["style"] = style
            else:
                image.attrs.pop("style", None)


PATTERN_STEP = 4.15  # 3pt en px de LaTeXML : pas des motifs de pgf en traits fins (lignes obliques, grilles, points)
SMALL_TILE = 4.5  # tuile de ces motifs (3,1pt au plus) ; au-delà (lignes horizontales, damier, étoiles…), pas inconnu
NEAR_ORIGIN = 12


def _tile(path):
    """Tuile isolée d'un motif : chemin commencé près de l'origine (sa place dans la définition perdue)."""
    if path is None or path.name != "path":
        return None
    start = re.match(r"\s*M\s*(-?[\d.]+)\s+(-?[\d.]+)", path.get("d") or "")
    return path if start and abs(float(start.group(1))) <= NEAR_ORIGIN and abs(float(start.group(2))) <= NEAR_ORIGIN else None


def _small(path):
    """Tuile des motifs au pas de 3pt : coordonnées absolues (M, L, C, Z) toutes proches de l'origine."""
    d = path.get("d") or ""
    numbers = re.findall(r"-?\d+(?:\.\d+)?", d)
    return not re.search(r"[A-BD-KN-Ya-z]", d) and numbers and all(abs(float(n)) <= SMALL_TILE for n in numbers)


def fix_patterns(fragment):
    """Hachures TikZ (pattern=north east lines…) : LaTeXML 0.8.8 perd la définition du motif et dessine sa tuile
    seule, à l'origine (un petit trait isolé) ; la surface hachurée restait vide. La tuile quitte le dessin ; le motif
    est refait avec elle quand son pas est connu (traits fins au pas de 3pt), sinon la surface reste vide."""
    ids = {element["id"] for element in fragment.find_all(id=True)}
    tiles = {}
    for pattern in fragment.find_all("pattern"):
        target = (pattern.get("href") or "").lstrip("#")
        if not target or target in ids:
            continue
        if target not in tiles:  # tuile dessinée juste avant la première utilisation du motif
            defs = pattern.find_parent("defs")  # tuile : dernier tracé avant la définition (parfois dans un <g>)
            # dans le même dessin, hors d'un motif ; retirée seulement si c'est bien une tuile connue (pas un axe
            # qui commencerait à l'origine)
            candidate = defs.find_previous("path") if defs else None
            if candidate is not None and (candidate.find_parent("svg") is not defs.find_parent("svg") or candidate.find_parent("pattern")):
                candidate = None
            loose = _tile(candidate)
            tiles[target] = loose.extract() if loose is not None and _small(loose) else None
        color = (pattern.find("g") or {}).get("stroke") or "#000000"
        pattern.clear()
        del pattern["href"]
        if tiles[target] is None or not _small(tiles[target]):
            continue
        tile = BeautifulSoup(str(tiles[target]), "html.parser").path
        tile.attrs.update(stroke=color, fill=color)
        tile["stroke-width"] = "0.4pt"
        pattern.attrs.update(patternunits="userSpaceOnUse", width=f"{PATTERN_STEP:g}", height=f"{PATTERN_STEP:g}")
        pattern.append(tile)


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
