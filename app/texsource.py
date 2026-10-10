"""Lecture de la source TeX d'un article arXiv, pour retrouver ce que la version HTML perd :
options de \\documentclass (numéros d'équation à gauche ou à droite), police du texte,
étiquettes des listes \\begin{enumerate}[(i)].

La source n'est jamais extraite sur le disque ni compilée : les fichiers .tex sont lus en mémoire.
"""

import gzip
import io
import posixpath
import re
import tarfile

MAX_FILES = 400
MAX_FILE_BYTES = 5_000_000
MAX_TOTAL_BYTES = 40_000_000

COMMENT = re.compile(r"(?<!\\)%.*")
DOCUMENTCLASS = re.compile(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^}]+)\}")
USEPACKAGE = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")
ENUMERATE = re.compile(r"\\begin\s*\{enumerate\}(?:\s*\[([^\]]*)\])?")

# Paquets de police → police du lecteur
FONT_PACKAGES = [
    ("charter", ("charter", "XCharter", "charterbt", "mathdesign")),
    ("palatino", ("mathpazo", "palatino", "newpxtext", "pxfonts", "palatinox", "qpalatin", "tgpagella")),
    ("times", ("times", "mathptmx", "newtxtext", "txfonts", "mathptm", "tgtermes", "stix", "stix2")),
]
AMS_CLASSES = {"amsart", "amsbook", "amsproc"}


def _read_archive(data):
    """Fichiers .tex de l'archive {chemin: texte} ; None si la source n'est pas du TeX (PDF seul…)."""
    files = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            total = 0
            for member in archive:
                if len(files) >= MAX_FILES:
                    break
                if not member.isfile() or not member.name.lower().endswith((".tex", ".ltx")):
                    continue
                if member.size > MAX_FILE_BYTES or total + member.size > MAX_TOTAL_BYTES:
                    continue
                total += member.size
                raw = archive.extractfile(member).read()
                files[posixpath.normpath(member.name.lstrip("/"))] = raw.decode("utf-8", errors="replace")
        return files or None
    except tarfile.TarError:
        pass
    try:  # décompression bornée : une archive piégée ne peut pas remplir la mémoire
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            raw = compressed.read(MAX_FILE_BYTES + 1)
    except (OSError, EOFError):
        raw = data
    if raw[:4] == b"%PDF" or len(raw) > MAX_FILE_BYTES:
        return None
    text = raw.decode("utf-8", errors="replace")
    return {"main.tex": text} if "\\documentclass" in text else None


def _strip_comments(text):
    return "\n".join(COMMENT.sub("", line) for line in text.splitlines())


def _expand(files, name, depth=0, seen=None):
    """Texte du fichier `name` avec ses \\input/\\include remplacés (profondeur limitée)."""
    seen = seen if seen is not None else set()
    if depth > 6 or name in seen:
        return ""
    seen.add(name)
    text = _strip_comments(files[name])
    base = posixpath.dirname(name)

    def replace(match):
        target = match.group(1).strip()
        for candidate in (target, target + ".tex", posixpath.join(base, target), posixpath.join(base, target + ".tex")):
            candidate = posixpath.normpath(candidate)
            if candidate in files:
                return _expand(files, candidate, depth + 1, seen)
        return ""

    return INPUT.sub(replace, text)


def _label_format(option):
    """Gabarit d'étiquette d'enumerate : « (i) » → ("(", "i", ")") ; None si étiquettes par défaut."""
    if option is None:
        return None
    option = option.strip()
    label = re.search(r"label\s*=\s*(\{.*\}|[^,]+)", option)
    if label:
        text = label.group(1).strip()
        if text.startswith("{") and text.endswith("}"):
            text = text[1:-1]
        kinds = {"\\roman*": "i", "\\Roman*": "I", "\\alph*": "a", "\\Alph*": "A", "\\arabic*": "1"}
        for macro, kind in kinds.items():
            if macro in text:
                before, after = text.split(macro, 1)
                return [_plain(before), kind, _plain(after)]
        return None
    if "=" in option:
        return None  # autres clés d'enumitem (leftmargin=…) : étiquettes par défaut
    text = _plain(option)
    match = re.search(r"[iIaA1]", text)
    if not match:
        return None
    return [text[: match.start()], match.group(0), text[match.end():]]


def _plain(text):
    text = re.sub(r"\\[A-Za-z]+\*?", "", text)
    return text.replace("{", "").replace("}", "").strip()


MACRO_HEAD = re.compile(r"\\(?:(?:re|provide)?newcommand\*?\s*\{?\\([A-Za-z]+)\}?\s*(?:\[\d\])?\s*(?:\[[^\]]*\])?"
                        r"|[egx]?def\s*\\([A-Za-z]+)[^{]*)\{")
PAIRED = re.compile(r"\\DeclarePairedDelimiter[XPP]*\*?\s*\{?\\([A-Za-z]+)")


def _braced(text, start):
    """Contenu entre l'accolade ouvrante qui précède `start` et sa fermante."""
    depth = 1
    for index in range(start, min(len(text), start + 5000)):
        char = text[index]
        if char == "{" and text[index - 1] != "\\":
            depth += 1
        elif char == "}" and text[index - 1] != "\\":
            depth -= 1
            if depth == 0:
                return text[start:index]
    return ""


def stretchy_macros(text):
    """Macros de l'auteur qui produisent des délimiteurs extensibles (\\left…\\right, \\middle,
    \\DeclarePairedDelimiter) : leurs délimiteurs doivent grandir, même si la formule ne montre que la macro."""
    names = set(PAIRED.findall(text))
    for match in MACRO_HEAD.finditer(text):
        if re.search(r"\\(left|right|middle)\b", _braced(text, match.end())):
            names.add(match.group(1) or match.group(2))
    return sorted(names)


def analyze_source(data):
    files = _read_archive(data)
    if not files:
        return None
    candidates = [name for name, text in files.items() if DOCUMENTCLASS.search(_strip_comments(text))]
    if not candidates:
        return None
    main = next((n for n in candidates if "\\begin{document}" in files[n]), candidates[0])
    text = _expand(files, main)
    match = DOCUMENTCLASS.search(text)
    options = [o.strip() for o in (match.group(1) or "").split(",") if o.strip()]
    documentclass = match.group(2).strip()
    if "leqno" in options:
        eqno = "left"
    elif "reqno" in options:
        eqno = "right"
    else:
        eqno = "left" if documentclass in AMS_CLASSES else "right"
    packages = {p.strip() for group in USEPACKAGE.findall(text) for p in group.split(",")}
    font = next((font for font, names in FONT_PACKAGES if packages & set(names)), "lm")
    body = text.split("\\begin{document}", 1)[-1]
    enumerate_labels = [_label_format(m.group(1)) for m in ENUMERATE.finditer(body)]
    return {
        "documentclass": documentclass,
        "options": options,
        "eqno": eqno,
        "font": font,
        "enumerate": enumerate_labels,
        "stretchyMacros": stretchy_macros(text.split("\\begin{document}", 1)[0]),
    }
