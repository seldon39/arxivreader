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
MAX_MEMBERS = 5_000          # entrées parcourues dans l'archive (une archive piégée peut en compter des millions)
MAX_WALKED_BYTES = 400_000_000  # octets décompressés parcourus, fichiers ignorés compris

COMMENT = re.compile(r"(?<!\\)%.*")
DOCUMENTCLASS = re.compile(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^}]+)\}")
USEPACKAGE = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
# \input{fichier}, et \input fichier (sans accolades, à la manière de TeX) ; cible : input_target(match)
INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}|\\input\s+([^\s{}%\\]+)")


def input_target(match):
    return (match.group(1) or match.group(2)).strip()
ENUMERATE = re.compile(r"\\begin\s*\{enumerate\}(?:\s*\[([^\]]*)\])?")

# Paquets de police → police du lecteur
FONT_PACKAGES = [
    ("charter", ("charter", "XCharter", "charterbt", "mathdesign")),
    ("palatino", ("mathpazo", "palatino", "newpxtext", "pxfonts", "palatinox", "qpalatin", "tgpagella")),
    ("times", ("times", "mathptmx", "newtxtext", "txfonts", "mathptm", "tgtermes", "stix", "stix2")),
]
AMS_CLASSES = {"amsart", "amsbook", "amsproc"}


def _read_archive(data, extensions=(".tex", ".ltx")):
    """Fichiers .tex de l'archive {chemin: texte} ; None si la source n'est pas du TeX (PDF seul…)."""
    files = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            total = walked = 0
            # au plus MAX_MEMBERS entrées lues (tarfile garde chacune en mémoire) et MAX_WALKED_BYTES parcourus
            for _ in range(MAX_MEMBERS):
                member = archive.next()
                if member is None or len(files) >= MAX_FILES:
                    break
                walked += member.size
                if walked > MAX_WALKED_BYTES:
                    break
                if not member.isfile() or not member.name.lower().endswith(extensions):
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
        target = input_target(match)
        for candidate in (target, target + ".tex", posixpath.join(base, target), posixpath.join(base, target + ".tex")):
            candidate = posixpath.normpath(candidate)
            if candidate in files:
                return _expand(files, candidate, depth + 1, seen)
        return ""

    return INPUT.sub(replace, text)


# ---------- Texte développé avec l'origine de chaque caractère ----------

# fins de ligne de str.splitlines() (celles que _strip_comments coupe), « \r\n » compté comme une seule
SPLITLINES = re.compile("\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85  ]")


def _line_spans(text):
    """(début, fin) de chaque ligne de `text`, exactement comme text.splitlines() (sans ligne vide finale)."""
    spans, pos = [], 0
    for match in SPLITLINES.finditer(text):
        spans.append((pos, match.start()))
        pos = match.end()
    if pos < len(text):
        spans.append((pos, len(text)))
    return spans


class Origins:
    """Origine des caractères d'un texte développé : morceaux (début dans le texte développé, fichier, position dans
    le texte du fichier, longueur), dans l'ordre. Chaque caractère vient d'un caractère d'un fichier (le saut de ligne
    remis par _strip_comments : la fin de ligne d'origine)."""

    def __init__(self):
        self.starts, self.names, self.offsets, self.lengths = [], [], [], []
        self.size = 0

    def add(self, name, offset, length):
        if length <= 0:
            return
        if self.starts and self.names[-1] == name and self.offsets[-1] + self.lengths[-1] == offset \
                and self.starts[-1] + self.lengths[-1] == self.size:
            self.lengths[-1] += length  # suite du morceau précédent
        else:
            self.starts.append(self.size)
            self.names.append(name)
            self.offsets.append(offset)
            self.lengths.append(length)
        self.size += length

    def at(self, pos):
        """(fichier, position dans son texte) du caractère `pos` du texte développé ; None hors du texte."""
        import bisect
        if not self.starts or pos < 0 or pos >= self.size:
            return None
        index = bisect.bisect_right(self.starts, pos) - 1
        return self.names[index], self.offsets[index] + pos - self.starts[index]

    def first(self, name):
        """Position dans le texte développé du premier caractère venu du fichier `name` (None s'il n'y est pas)."""
        return next((start for start, other in zip(self.starts, self.names) if other == name), None)


def _strip_comments_tracked(text):
    """_strip_comments(text) et ses morceaux [(début dans le résultat, position dans `text`, longueur)]."""
    parts, pieces, out = [], [], 0
    spans = _line_spans(text)
    for index, (start, end) in enumerate(spans):
        if index:
            parts.append("\n")
            pieces.append((out, spans[index - 1][1], 1))  # fin de ligne d'origine (« \r\n » : son « \r »)
            out += 1
        line = text[start:end]
        comment = COMMENT.search(line)
        kept = line[:comment.start()] if comment else line
        parts.append(kept)
        if kept:
            pieces.append((out, start, len(kept)))
            out += len(kept)
    return "".join(parts), pieces


def expand_with_origins(files, name):
    """Même texte que _expand(files, name) (identique, caractère pour caractère) et l'origine de chacun de ses
    caractères (Origins) : de quel fichier il vient et où."""
    import bisect
    out, origins = [], Origins()

    def copy(pieces, starts, file, a, b):
        """Morceau [a, b) du texte sans commentaires de `file` recopié : ses origines."""
        index = max(0, bisect.bisect_right(starts, a) - 1)
        while index < len(pieces) and pieces[index][0] < b:
            start, offset, length = pieces[index]
            lo, hi = max(a, start), min(b, start + length)
            if lo < hi:
                origins.add(file, offset + lo - start, hi - lo)
            index += 1

    def expand(file, depth, seen):
        if depth > 6 or file in seen:
            return
        seen.add(file)
        text, pieces = _strip_comments_tracked(files[file])
        starts = [p[0] for p in pieces]
        base = posixpath.dirname(file)
        pos = 0
        for match in INPUT.finditer(text):  # comme INPUT.sub : de gauche à droite, chaque inclusion développée aussitôt
            out.append(text[pos:match.start()])
            copy(pieces, starts, file, pos, match.start())
            target = input_target(match)
            for candidate in (target, target + ".tex", posixpath.join(base, target), posixpath.join(base, target + ".tex")):
                candidate = posixpath.normpath(candidate)
                if candidate in files:
                    expand(candidate, depth + 1, seen)
                    break
            pos = match.end()
        out.append(text[pos:])
        copy(pieces, starts, file, pos, len(text))

    expand(name, 0, set())
    return "".join(out), origins


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
        kinds = {"\\roman*": "i", "\\Roman*": "I", "\\alph*": "a", "\\Alph*": "A", "\\arabic*": "1",
                 "\\greek*": "α", "\\Greek*": "Α"}  # moreenum
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


def choose_main(files, readme=None):
    """Fichier principal d'une source ({chemin: texte}) : celui qu'indique 00README.json d'arXiv (« toplevel »), sinon
    parmi ceux qui ont \\documentclass et \\begin{document}, le plus long (une lettre d'accompagnement est courte) ;
    None s'il n'y en a pas. Le même choix pour l'analyse et pour la version HTML (LaTeXML)."""
    if readme:
        try:
            import json
            listed = [s.get("filename") for s in json.loads(readme).get("sources", []) if s.get("usage") == "toplevel"]
            found = next((posixpath.normpath(f) for f in listed if f and posixpath.normpath(f) in files), None)
            if found:
                return found
        except (ValueError, AttributeError, TypeError):
            pass
    candidates = [name for name, text in files.items() if name.lower().endswith((".tex", ".ltx"))
                  and DOCUMENTCLASS.search(_strip_comments(text))]
    if not candidates:
        return None
    complete = [n for n in candidates if "\\begin{document}" in files[n]] or candidates
    return max(complete, key=lambda n: (len(files[n]), n))


def analyze_source(data):
    files = _read_archive(data, extensions=(".tex", ".ltx", ".json"))
    if not files:
        return None
    main = choose_main(files, files.get("00README.json"))
    if not main:
        return None
    return analyze_text(_expand(files, main))


def analyze_text(text):
    """Options de mise en page lues dans le texte développé du fichier principal."""
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
