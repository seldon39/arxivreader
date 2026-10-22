"""Fichiers TeX de l'ordinateur (article ou livre en cours d'écriture, pas sur arXiv) : un article de la bibliothèque
lu dans le lecteur PDF, avec énoncés, preuves et dépendances tirés de la source (« tex-<empreinte du chemin> »,
source « local-tex »).

Le fichier principal reste où il est : l'application garde son chemin et relit la source à chaque « Recompiler ».
Le PDF est compilé par latexmk (sinon pdflatex, BibTeX ou Biber, puis pdflatex), dans un dossier de travail à
part : rien n'est écrit dans le dossier de l'utilisateur. Sans TeX, le PDF déjà compilé à côté du fichier
principal (main.pdf pour main.tex) est lu tel quel. La bibliographie (\\bibliography, \\addbibresource) est cherchée
dans le dossier du fichier, puis dans l'installation TeX (kpsewhich) ; un .bib introuvable est signalé et peut être
choisi à la main (retenu pour les compilations suivantes).

Les énoncés qui portent un \\label gardent le même identifiant d'une compilation à l'autre : leurs notes les
suivent quand le texte change.
"""

import glob
import hashlib
import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from . import db, texstructure
from .library import paper_dir, paper_lock, write_atomic, write_atomic_bytes
from .structure import NEEDS_PROOF
from .texsource import DOCUMENTCLASS, INPUT, input_target, MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, USEPACKAGE, _strip_comments, analyze_text

SOURCE = "local-tex"
SUFFIXES = (".tex", ".ltx")
MAX_PDF_BYTES = 150_000_000  # comme un PDF importé
COMPILE_TIMEOUT = 300  # secondes : un livre se compile en une ou deux minutes ; au-delà, compilation bloquée
BIBLIOGRAPHY = re.compile(r"\\bibliography\s*\{([^}]*)\}|\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
CITE = re.compile(r"\\[A-Za-z]*cite[A-Za-z]*\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]*)\}")
MAGIC = re.compile(r"^\s*%\s*!\s*TEX\s+(?:TS-)?program\s*=\s*([A-Za-z]+)", re.I | re.M)
ERROR = re.compile(r"^(.+?\.(?:tex|ltx|sty|cls|bbl|aux)):(\d+): (.+)$", re.M)
UNDEFINED = re.compile(r"(Reference|Citation) [`']([^']+)' on page \d+ undefined")
ENGINES = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex", "latex": "-pdfdvi"}
# où sont les programmes de TeX quand l'application n'hérite pas du PATH du terminal (lancée depuis le Finder…)
TEX_DIRS = ["/Library/TeX/texbin", "/usr/local/texlive/*/bin/*", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
            "~/bin", "C:/texlive/*/bin/windows", "C:/texlive/*/bin/win64", "C:/texlive/*/bin/win32",
            "%LOCALAPPDATA%/Programs/MiKTeX/miktex/bin/x64", "%PROGRAMFILES%/MiKTeX/miktex/bin/x64"]


class TexError(ValueError):
    pass


def paper_id(path):
    return "tex-" + hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


def is_tex_file(paper):
    return bool(paper) and paper.get("source") == SOURCE


# ---------- Programmes de TeX ----------

def find_tool(name):
    """Chemin d'un programme de TeX (latexmk, pdflatex, kpsewhich…), ou None."""
    found = shutil.which(name)
    if found:
        return found
    for pattern in TEX_DIRS:
        for folder in sorted(glob.glob(os.path.expandvars(os.path.expanduser(pattern))), reverse=True):  # dernière TeX Live
            for candidate in (Path(folder) / name, Path(folder) / f"{name}.exe"):
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
    return None


def tools():
    """Programmes trouvés (Réglages > Lecture) : {"latexmk": chemin ou None, "pdflatex": …}."""
    return {name: find_tool(name) for name in ("latexmk", "pdflatex")}


def _env(tool, bib_dirs=()):
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(Path(tool).parent), env.get("PATH", "")])
    env["max_print_line"] = "10000"  # journal sans lignes coupées à 79 colonnes : clés longues des renvois lisibles
    if bib_dirs:  # .bib choisis ailleurs ; séparateur final : chemins habituels de TeX ensuite
        env["BIBINPUTS"] = os.pathsep.join([*bib_dirs, env.get("BIBINPUTS", "")])
    return env


def _run(command, cwd, env, timeout):
    """Programme lancé sans fenêtre, arrêté (avec ceux qu'il a lancés) au-delà de `timeout` ; (code, sortie)."""
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, **options)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, **options)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise TexError("timeout")
    return process.returncode, output.decode("utf-8", errors="replace")


# ---------- Lecture de la source ----------

def read_project(main):
    """Fichiers de la source, à partir du fichier principal et de ses \\input/\\include : {chemin relatif: texte}."""
    root = main.parent
    files, queue, total = {}, [main.name], 0
    while queue and len(files) < MAX_FILES:
        name = queue.pop(0)
        if name in files:
            continue
        path = root / name
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                continue
            files[name] = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        total += size
        base = posixpath.dirname(name)
        for match in INPUT.finditer(_strip_comments(files[name])):
            target = input_target(match)
            for candidate in (target, target + ".tex", posixpath.join(base, target), posixpath.join(base, target + ".tex")):
                candidate = posixpath.normpath(candidate)
                if (root / candidate).is_file():
                    queue.append(candidate)
                    break
    return files


def _meta(text, fallback):
    """Titre, auteurs et résumé lus dans la source."""
    def argument(command):
        found = re.search(r"\\" + command + r"\s*(?:\[[^\]]*\])?\s*(?=\{)", text)
        group = found and texstructure._group(text, found.end(), "{")
        return group[0] if group else ""

    def without_notes(value):  # \thanks{…}, \footnote{…} : pas dans le nom
        while (found := re.search(r"\\(?:thanks|footnote|inst|orcidlink)\s*(?=\{)", value)):
            group = texstructure._group(value, found.end(), "{")
            value = value[:found.start()] + (value[group[1]:] if group else value[found.end():])
        return value

    title = texstructure.plain(without_notes(argument("title"))) or fallback
    authors = []
    for found in re.finditer(r"\\author\s*(?:\[[^\]]*\])?\s*(?=\{)", text):  # amsart : un \author par auteur
        group = texstructure._group(text, found.end(), "{")
        for name in re.split(r"\\and\b|\\\\|,|\band\b", without_notes(group[0]) if group else ""):
            name = texstructure.plain(name)
            if name and len(name) < 120:
                authors.append(name)
    abstract = re.search(r"\\begin\s*\{abstract\}(.*?)\\end\s*\{abstract\}", text, re.S)
    return title[:300], authors[:30], texstructure.plain(abstract.group(1))[:2000] if abstract else ""


def _bibliographies(text):
    names = []
    for match in BIBLIOGRAPHY.finditer(text):
        for name in (match.group(1) or match.group(2) or "").split(","):
            name = name.strip()
            if name and (name if name.endswith(".bib") else f"{name}.bib") not in names:
                names.append(name if name.endswith(".bib") else f"{name}.bib")
    return names


def _find_bib(name, root, chosen):
    """Chemin d'un .bib : dossier du fichier principal, sinon celui choisi à la main, sinon l'installation TeX."""
    if (root / name).is_file():
        return root / name
    if chosen.get(name) and Path(chosen[name]).is_file():
        return Path(chosen[name])
    kpsewhich = find_tool("kpsewhich")
    if kpsewhich:
        try:
            code, output = _run([kpsewhich, name], str(root), _env(kpsewhich), 15)
            if code == 0 and output.strip() and Path(output.strip().splitlines()[0]).is_file():
                return Path(output.strip().splitlines()[0])
        except (OSError, TexError):
            pass
    return None


def _entries(path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for kind in re.findall(r"@\s*(\w+)\s*[{(]", text) if kind.lower() not in ("string", "comment", "preamble"))


# ---------- Compilation ----------

def build_dir(pid):
    """Dossier de travail de la compilation (gardé d'une fois à l'autre : latexmk ne refait que le nécessaire) ;
    hors du dossier de l'utilisateur et sans espace dans le chemin (BibTeX s'y perd)."""
    folder = Path(tempfile.gettempdir()) / "arxivreader-tex" / pid
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _engine(raw, text):
    magic = MAGIC.search(raw[:3000])
    if magic and magic.group(1).lower() in ENGINES:
        return magic.group(1).lower()
    packages = {p.strip() for group in USEPACKAGE.findall(text) for p in group.split(",")}
    return "xelatex" if packages & {"fontspec", "unicode-math"} else "pdflatex"


def compile_pdf(main, out, engine, biblatex, bib_dirs, force=False):
    """PDF compilé dans `out` : (chemin ou None, journal de LaTeX, outil utilisé) ; (None, "", None) sans TeX."""
    target, log = out / f"{main.stem}.pdf", out / f"{main.stem}.log"
    target.unlink(missing_ok=True)
    log.unlink(missing_ok=True)
    latexmk, tool = find_tool("latexmk"), None
    if latexmk:
        # BibTeX lancé dans le dossier de travail (sinon il ne peut pas y écrire : openout_any = p)
        command = [latexmk, "-e", "$bibtex_fudge=1", ENGINES[engine], "-f", "-interaction=nonstopmode", "-file-line-error",
                   f"-outdir={out}", main.name]
        if force:  # état gardé par latexmk oublié (il retient qu'un .bib manquait et ne relance pas BibTeX)
            (out / f"{main.stem}.fdb_latexmk").unlink(missing_ok=True)
        _run(command, str(main.parent), _env(latexmk, bib_dirs), COMPILE_TIMEOUT)
        tool = "latexmk" if log.exists() else None  # latexmk qui ne démarre pas (MiKTeX sans Perl) : à la main
    if tool is None:  # sans latexmk (BasicTeX…) : LaTeX, bibliographie, puis LaTeX deux fois
        program = find_tool(engine)
        if not program:
            return None, "", None
        latex = [program, "-interaction=nonstopmode", "-file-line-error", f"-output-directory={out}", main.name]
        env = _env(program, [str(main.parent), *bib_dirs])
        started = time.monotonic()
        _run(latex, str(main.parent), env, COMPILE_TIMEOUT)
        bibber = find_tool("biber" if biblatex else "bibtex")
        if bibber and (out / f"{main.stem}.{'bcf' if biblatex else 'aux'}").exists():
            _run([bibber, main.stem], str(out), env, COMPILE_TIMEOUT)
        for _ in range(2):
            _run(latex, str(main.parent), env, max(10, COMPILE_TIMEOUT - (time.monotonic() - started)))
        tool = engine
    text = log.read_bytes().decode("utf-8", errors="replace") if log.exists() else ""
    return (target if target.exists() else None), text, tool


def _log_report(log):
    errors, seen = [], set()
    for match in ERROR.finditer(log):
        key = (match.group(1), match.group(2))
        if key not in seen:
            seen.add(key)
            errors.append({"file": match.group(1).removeprefix("./"), "line": int(match.group(2)), "message": match.group(3).strip()[:300]})
    refs, cites = [], []
    for kind, key in UNDEFINED.findall(log):
        found = refs if kind == "Reference" else cites
        if key not in found:
            found.append(key)
    return errors[:5], refs[:50], cites[:50]


# ---------- Ce que LaTeX a écrit (.aux) : numéros imprimés, ancres ----------

NEWLABEL = re.compile(r"\\newlabel\{([^}@]*)\}\{\{")


def aux_labels(out):
    """{clé de \\label: (numéro imprimé, ancre de hyperref ou None)} d'après les .aux de la compilation."""
    labels = {}
    for path in sorted(out.rglob("*.aux"))[:500]:
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        for match in NEWLABEL.finditer(text):
            values, pos = [], match.end() - 1
            for _ in range(5):  # {numéro}{page}{titre}{ancre}{}
                group = texstructure._group(text, pos, "{")
                if group is None:
                    break
                values.append(group[0])
                pos = group[1]
            if values:
                number = texstructure.plain(values[0]).strip()
                labels[match.group(1)] = (number, values[3].strip() if len(values) > 3 and values[3].strip() else None)
    return labels


def apply_aux(structure, out):
    """Numéros que LaTeX a imprimés pour les énoncés et équations qui ont un \\label (la numérotation simulée peut se
    tromper : compteurs maison, macros de classe), ancres exactes pour leur page ; ancres « cite.<clé> » des entrées
    de la bibliographie."""
    labels = aux_labels(out)
    nodes = {n["id"]: n for n in structure.get("nodes", [])}
    for key, owner in structure.get("labels", {}).items():
        number, anchor = labels.get(key, (None, None))
        node = nodes.get(owner)
        if node is None or not number or not node.get("anchors"):
            continue  # énoncé sans numéro (\\newtheorem*) : le numéro du .aux serait celui d'un autre compteur
        name = node["label"].rsplit(" ", 1)[0]
        if number != node["label"].rsplit(" ", 1)[-1]:
            node["label"] = f"{name} {number}"
        if anchor:
            node["anchors"] = [anchor] + [a for a in node.get("anchors", []) if a != anchor]
    for key, equation in structure.get("equations", {}).items():
        number, anchor = labels.get(key, (None, None))
        if number:
            equation["label"] = f"({number})"
        if anchor:
            equation["anchors"] = [anchor]
    for entry in structure.get("bibliography", []):
        entry.setdefault("anchors", [f"cite.{entry['id'].removeprefix('bib.')}"])


# ---------- Identifiants stables ----------

def stable_ids(structure):
    """Identifiants qui ne dépendent pas du rang des énoncés, pour que leurs notes les suivent quand des énoncés sont
    ajoutés ou retirés avant eux : tiré du \\label (« tex-lbl-thm:main »), sinon du type et du texte de l'énoncé
    (« tex-txt-lemma-3f2a9c1b04 » : une note reste à son énoncé, et le quitte seulement si son texte change). Leurs
    preuves de même."""
    renamed, taken = {}, set()
    for key, owner in structure.get("labels", {}).items():
        if owner and owner not in renamed:
            new = "tex-lbl-" + re.sub(r"[^A-Za-z0-9_.:-]", "_", key)[:120]
            if new not in taken:
                renamed[owner] = new
                taken.add(new)
    for node in structure.get("nodes", []):
        if node["id"] in renamed:
            continue
        text = " ".join((node.get("tex") or node.get("name") or "").split())
        base = f"tex-txt-{node['kind']}-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]}"
        new, count = base, 1
        while new in taken:  # même texte deux fois (« Remark. Trivial. ») : rang parmi eux
            count += 1
            new = f"{base}-{count}"
        renamed[node["id"]] = new
        taken.add(new)
    counts = {}
    for proof in structure.get("proofBlocks", []):
        if proof.get("of") in renamed:
            counts[proof["of"]] = counts.get(proof["of"], 0) + 1
            renamed[proof["id"]] = f"{renamed[proof['of']]}-proof-{counts[proof['of']]}"
    rename = lambda value: renamed.get(value, value)  # noqa: E731
    for node in structure.get("nodes", []):
        node["id"] = rename(node["id"])
        for field in ("uses", "usedBy", "weakUses", "proofs"):
            node[field] = [rename(value) for value in node.get(field) or []]
        if node.get("justifiedBy"):
            node["justifiedBy"] = rename(node["justifiedBy"])
    for proof in structure.get("proofBlocks", []):
        proof["id"], proof["of"] = rename(proof["id"]), rename(proof.get("of"))
    structure["labels"] = {key: rename(owner) for key, owner in structure.get("labels", {}).items()}
    for equation in structure.get("equations", {}).values():
        equation["owner"] = rename(equation.get("owner"))
    structure["stableIds"] = True
    return structure


# ---------- Import et recompilation ----------

def _settings(pid):
    try:
        return json.loads((paper_dir(pid) / "texfile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main_file(path):
    """Fichier principal vérifié (chemin complet) : un .tex qui a son \\documentclass."""
    main = Path(path).expanduser()
    if main.suffix.lower() not in SUFFIXES or not main.is_file():
        raise TexError("not a .tex file")
    main = main.resolve()
    if not DOCUMENTCLASS.search(_strip_comments(main.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", errors="replace"))):
        raise TexError("not the main file")  # chapitre inclus par un autre fichier : il faut le fichier principal
    return main


def import_tex(path, pdf=None):
    """Article de la bibliothèque à partir du fichier principal d'un document TeX (compilé ici) ; déjà importé :
    recompilé. `pdf` : PDF déjà compilé à lire tel quel (sans compiler). Renvoie {"paper", "report"} ; paper None
    s'il n'y a pas de PDF (le bilan dit pourquoi)."""
    main = main_file(path)
    pid = paper_id(main)
    if pdf:
        pdf = Path(pdf).expanduser()
        if pdf.suffix.lower() != ".pdf" or not pdf.is_file() or pdf.stat().st_size > MAX_PDF_BYTES:
            raise TexError("not a PDF")
    with paper_lock(pid):
        settings = {**_settings(pid), "main": str(main)}
        settings.pop("pdf", None)  # importé de nouveau sans son PDF : compilé
        if pdf:
            settings["pdf"] = str(pdf.resolve())
        return _build(pid, settings)


def check_recompile(pid):
    settings = _settings(pid)
    if not settings.get("main"):
        raise TexError("not a TeX file")
    if not Path(settings["main"]).is_file():
        raise TexError(f"missing: {settings['main']}")


def recompile(pid):
    check_recompile(pid)
    with paper_lock(pid):
        return _build(pid, _settings(pid))


def check_bib(path):
    chosen = Path(path).expanduser()
    if chosen.suffix.lower() != ".bib" or not chosen.is_file():
        raise TexError("not a .bib file")


def choose_bib(pid, name, path):
    """.bib choisi à la main pour `name` (introuvable seul) : retenu, puis le document est recompilé."""
    check_bib(path)
    chosen = Path(path).expanduser()
    with paper_lock(pid):
        settings = _settings(pid)
        if not settings.get("main"):
            raise TexError("not a TeX file")
        settings["bibs"] = {**settings.get("bibs", {}), name: str(chosen.resolve())}
        return _build(pid, settings)


def report(pid):
    return _settings(pid).get("report")


# Compilation en arrière-plan (une minute ou plus pour un livre : la page ne garde pas une requête ouverte aussi
# longtemps) ; la page suit GET /api/tex/job/<id>
_jobs = {}
_jobs_guard = threading.Lock()


def start(pid, function, *args):
    """Compilation lancée en arrière-plan ; False si une autre est déjà en cours pour ce document (refusée : son
    résultat ne serait pas celui de cette demande)."""
    with _jobs_guard:
        if _jobs.get(pid, {}).get("running"):
            return False
        _jobs[pid] = {"running": True}

    def run():
        try:
            outcome = {"running": False, "result": function(*args)}
        except (ValueError, OSError) as error:  # TexError, LatexmlError
            outcome = {"running": False, "error": str(error)}
        except Exception as error:  # jamais une tâche « en cours » pour toujours
            outcome = {"running": False, "error": f"{type(error).__name__}: {error}"}
        with _jobs_guard:
            _jobs[pid] = outcome

    threading.Thread(target=run, daemon=True).start()
    return True


def job(pid):
    with _jobs_guard:
        return dict(_jobs.get(pid) or {"running": False})


def _build(pid, settings):
    main = Path(settings["main"])
    root = main.parent
    files = read_project(main)
    raw = files.get(main.name, "")
    text = texstructure._expand(files, main.name)
    bib_names = _bibliographies(text)
    found_bibs, missing = [], []
    for name in bib_names:
        where = _find_bib(name, root, settings.get("bibs", {}))
        if where:
            found_bibs.append({"name": name, "path": str(where), "entries": _entries(where)})
        else:
            missing.append(name)
    rep = {"main": str(main), "files": len(files), "figures": len(re.findall(r"\\includegraphics", text)),
           "bibs": found_bibs, "missingBibs": missing,
           "cited": len({k.strip() for m in CITE.finditer(text) for k in m.group(1).split(",") if k.strip() and k.strip() != "*"}),
           "errors": [], "undefinedRefs": [], "undefinedCites": [], "compiledAt": datetime.now().isoformat(timespec="seconds")}
    out = build_dir(pid)
    pdf_path, log = None, ""
    if settings.get("pdf"):  # PDF choisi avec le fichier : lu tel quel
        pdf_path = Path(settings["pdf"]) if Path(settings["pdf"]).is_file() else None
        rep["pdfFrom"] = "chosen"
    else:
        # .bib choisis à la main : copiés dans le dossier de travail sous le nom que la source demande (un fichier
        # « references.bib » choisi pour « refs.bib » est trouvé), relus à chaque compilation
        for name, chosen in settings.get("bibs", {}).items():
            target = out / name
            if Path(chosen).is_file() and target.resolve().is_relative_to(out.resolve()):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(chosen, target)
        bib_dirs = [str(out)] + [str(Path(p).parent) for p in settings.get("bibs", {}).values()]
        # \include{chapitres/ch1} : LaTeX écrit chapitres/ch1.aux dans le dossier de travail, qui doit exister
        for name in files:
            if ".." not in name:
                (out / posixpath.dirname(name)).mkdir(parents=True, exist_ok=True)
        failed = False
        try:
            # .bib manquant la dernière fois : tout est refait (latexmk ne relancerait pas BibTeX de lui-même)
            force = bool((settings.get("report") or {}).get("missingBibs"))
            pdf_path, log, tool = compile_pdf(main, out, _engine(raw, text), "\\addbibresource" in text, bib_dirs, force)
        except (OSError, TexError) as error:  # compilation trop longue, programme qui ne démarre pas : dit tel quel
            pdf_path, tool, failed = None, None, True
            rep["errors"] = [{"file": main.name, "line": 0, "message": f"compilation: {error}"}]
        rep["pdfFrom"] = tool or "none"
        if tool is None and not failed and main.with_suffix(".pdf").is_file():  # pas de TeX ici : PDF à côté du fichier
            pdf_path, rep["pdfFrom"] = main.with_suffix(".pdf"), "existing"
            rep["pdfDate"] = datetime.fromtimestamp(pdf_path.stat().st_mtime).isoformat(timespec="seconds")
    if log:
        errors, rep["undefinedRefs"], rep["undefinedCites"] = _log_report(log)
        rep["errors"] = rep["errors"] or errors
    previous = db.get_paper(pid)
    if not pdf_path:
        rep["pdf"] = False
        settings["report"] = rep
        if previous:  # recompilation ratée : l'article reste lisible (PDF précédent), le bilan dit pourquoi
            write_atomic(paper_dir(pid) / "texfile.json", json.dumps(settings, ensure_ascii=False))
        return {"paper": previous, "report": rep}
    pdf = pdf_path.read_bytes()
    bbl = next((p.read_text(encoding="utf-8", errors="replace") for p in (out / f"{main.stem}.bbl", root / f"{main.stem}.bbl")
                if p.is_file()), "")  # BibTeX ou biblatex (Biber)
    structure, source = texstructure.from_files(files, main.name, bbl)
    apply_aux(structure, out)
    texstructure.locate_pages(structure, pdf)
    stable_ids(structure)
    structure["source"] = "tex"
    structure["tex"] = analyze_text(text)
    structure["compiledAt"] = rep["compiledAt"]  # adresse du PDF changée à chaque compilation (pas d'ancien en cache)
    rep["pdf"] = True
    rep["pages"] = _pages(pdf)
    rep["statements"] = len(structure["nodes"])
    rep["withoutProof"] = sum(1 for n in structure["nodes"] if n["kind"] in NEEDS_PROOF and not n["proofs"] and not n["cited"])
    rep["book"] = structure.get("book", False)
    settings["report"] = rep
    folder = paper_dir(pid)
    folder.mkdir(parents=True, exist_ok=True)
    write_atomic_bytes(folder / "paper.pdf", pdf)
    write_atomic(folder / "source.tex", source)
    write_atomic(folder / "structure.json", json.dumps(structure, ensure_ascii=False))
    write_atomic(folder / "texfile.json", json.dumps(settings, ensure_ascii=False))
    title, authors, abstract = _meta(text, main.stem.replace("_", " "))
    if previous and (folder / "meta-edited").exists():  # titre et auteurs corrigés à la main : gardés
        db.update_paper(pid, abstract=abstract)
    elif previous:
        db.update_paper(pid, title=title, authors=json.dumps(authors, ensure_ascii=False), abstract=abstract, comment=str(main))
    else:
        db.save_paper({"id": pid, "arxiv_id": "", "version": None, "title": title, "authors": authors, "abstract": abstract,
                       "categories": [], "primary_category": "", "published": None, "updated": None, "doi": None,
                       "journal_ref": None, "comment": str(main), "source": SOURCE, "in_library": 1})
    if previous and not previous.get("in_library"):
        db.update_paper(pid, in_library=1)
    return {"paper": db.get_paper(pid), "report": rep}


def _pages(pdf):
    import io

    import pypdf
    try:
        return len(pypdf.PdfReader(io.BytesIO(pdf)).pages)
    except Exception:
        return None
