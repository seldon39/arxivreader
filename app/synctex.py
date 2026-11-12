"""SyncTeX : lien entre les lignes de la source TeX et les pages du PDF compilé (fichier .synctex.gz écrit par
pdfLaTeX, XeLaTeX ou LuaLaTeX avec -synctex=1), lu ici sans le programme synctex.

- `pages_of(fichier, ligne)` : pages où cette ligne de la source a produit quelque chose ;
- `line_at(page, x, y)` : ligne de la source du texte le plus proche d'un point de la page (en points du PDF, depuis
  le coin en haut à gauche), comme « synctex edit ».

Les fichiers sont nommés par leur chemin relatif au dossier du projet (celui où la compilation a été lancée) ; ceux
d'ailleurs (classes, paquets, .aux du dossier de travail) n'ont pas de nom et ne sont jamais rendus. Lecture bornée :
au plus MAX_BYTES décompressés, lignes mal formées ignorées.
"""

import gzip
import os
import posixpath
import re
from pathlib import Path

MAX_BYTES = 200_000_000
SP_PER_BP = 65536 * 72.27 / 72  # unités de TeX (sp) par point du PDF (bp)
TALL = 60  # points : boîte plus haute qu'une ligne de texte (colonne, page)
# enregistrement : type, fichier (numéro), ligne[, colonne] : h, v [: largeur, hauteur, profondeur]
RECORD = re.compile(rb"([\[(vhxkg$r])(\d+),(\d+)(?:,-?\d+)?:(-?\d+),(-?\d+)(?::(-?\d+)(?:,(-?\d+),(-?\d+))?)?")
HEADER = re.compile(rb"(Input|Magnification|Unit|X Offset|Y Offset):(.*)")


class SyncTeX:
    def __init__(self):
        self.inputs = {}       # numéro → chemin relatif au projet (None : hors du projet)
        self.lines = {}        # (fichier, ligne) → pages
        self.boxes = {}        # page détaillée → boîtes horizontales [h, v, largeur, hauteur, profondeur, tag, ligne, feuilles]
        self.unit, self.magnification, self.x_offset, self.y_offset = 1.0, 1000.0, 0.0, 0.0
        self.pages = 0

    def _sp(self, value_bp):
        """Point du PDF (bp) → unités du fichier."""
        return value_bp * SP_PER_BP / (self.unit * self.magnification / 1000)

    def pages_of(self, file, line):
        """Pages (à partir de 1) où la ligne `line` (à partir de 1) du fichier `file` a produit quelque chose."""
        return set(self.lines.get((file, line), ()))

    def first_line(self, page):
        """(fichier, ligne) du premier texte du projet en haut de la page (lue en détail), ou None."""
        found = [(box[1], box[0], leaf) for box in self.boxes.get(page, []) for leaf in box[7] if self.inputs.get(leaf[0])]
        if not found:
            return None
        _, _, leaf = min(found, key=lambda item: (item[0], item[1]))
        return self.inputs[leaf[0]], leaf[1]

    def line_at(self, page, x, y):
        """(fichier, ligne) de la source au point (x, y) de la page `page` — points du PDF, origine en haut à gauche ;
        None si la page n'a pas été lue en détail ou n'a rien du projet. Comme « synctex edit » : parmi les lignes de
        texte (boîtes horizontales) qui couvrent le point horizontalement, la plus proche verticalement (sinon la plus
        proche) ; dans la boîte, le dernier élément à gauche du point (sinon le premier)."""
        boxes = [box for box in self.boxes.get(page, []) if box[7]]
        if not boxes:
            return None
        h, v = self._sp(x) - self.x_offset, self._sp(y) - self.y_offset
        # boîtes hautes (une colonne entière, faite de colle et de crénages) : seulement s'il n'y a rien d'autre
        lines = [box for box in boxes if box[3] + box[4] <= self._sp(TALL)]
        boxes = lines or boxes

        def distance(box):
            left, base, width, height, depth = box[:5]
            dx = max(0, left - h, h - (left + width))
            dy = max(0, (base - height) - v, v - (base + depth))
            # d'abord les lignes qui couvrent le point horizontalement (sa colonne), puis la plus proche verticalement
            return (dx > 0, dy, dx, width * (height + depth))

        ordered = sorted(boxes, key=distance)
        for box in ordered:  # boîte la plus proche d'abord ; ses feuilles du projet seulement
            leaves = [leaf for leaf in box[7] if self.inputs.get(leaf[0])]
            if not leaves:
                continue
            # suite d'un paragraphe : son premier élément porte la ligne de la fin du paragraphe (celle de la boîte),
            # pas celle du mot qu'il suit
            if len(leaves) > 1 and leaves[0][:2] == (box[5], box[6]) and leaves[1][:2] != leaves[0][:2]:
                leaves = leaves[1:]
            before = [(leaf[2], rank, leaf) for rank, leaf in enumerate(leaves) if leaf[2] <= h]
            # à égalité de position (crénages de largeur nulle en fin de ligne) : le dernier écrit, comme synctex
            leaf = max(before)[2] if before else min(leaves, key=lambda item: item[2])
            return self.inputs[leaf[0]], leaf[1]
        return None


def _relative(path, root):
    """Chemin d'un fichier lu par TeX relatif au dossier du projet (« sections/intro.tex »), ou None hors du projet."""
    path = path.strip()
    if not path:
        return None
    full = path if os.path.isabs(path) else os.path.join(root, path)
    full = os.path.realpath(os.path.normpath(full))
    base = os.path.realpath(root)
    try:
        relative = os.path.relpath(full, base)
    except ValueError:  # autre disque (Windows)
        return None
    relative = relative.replace(os.sep, "/")
    if relative == ".." or relative.startswith("../") or os.path.isabs(relative):
        return None
    return posixpath.normpath(relative)


def load(path, root, detail_pages=()):
    """SyncTeX de `path` (.synctex.gz, ou non compressé) ; `root` : dossier du projet (où la compilation a été
    lancée) ; `detail_pages` : pages dont les boîtes sont gardées pour line_at / first_line. None si le fichier est
    absent ou illisible."""
    root = str(root)
    detail = set(detail_pages)
    sync = SyncTeX()
    try:
        raw = open(path, "rb")
    except OSError:
        return None
    try:
        with raw:
            stream = gzip.GzipFile(fileobj=raw) if raw.read(2) == b"\x1f\x8b" else raw
            raw.seek(0)
            read, page, stack, content = 0, 0, [], False
            for line in stream:
                read += len(line)
                if read > MAX_BYTES:
                    break
                line = line.rstrip(b"\r\n")
                if not line:
                    continue
                first = line[:1]
                if first == b"{":
                    page = int(line[1:]) if line[1:].isdigit() else page + 1
                    sync.pages = max(sync.pages, page)
                    stack = []
                    content = True
                    continue
                if first == b"}":
                    stack = []
                    continue
                if not content:
                    header = HEADER.match(line)
                    if header:
                        _header(sync, header.group(1), header.group(2).decode("utf-8", errors="replace"), root)
                    elif line.startswith(b"Content:"):
                        content = True
                    continue
                if first in (b")", b"]"):
                    if stack:
                        stack.pop()
                    continue
                record = RECORD.match(line)
                if not record:
                    if line.startswith(b"Input:"):  # fichier ouvert en cours de route
                        header = HEADER.match(line)
                        _header(sync, header.group(1), header.group(2).decode("utf-8", errors="replace"), root)
                    continue
                kind, tag, number = record.group(1), int(record.group(2)), int(record.group(3))
                if page and number > 0 and sync.inputs.get(tag):
                    sync.lines.setdefault((sync.inputs[tag], number), set()).add(page)
                if page not in detail:
                    if kind in (b"[", b"("):
                        stack.append(None)
                    continue
                h, v = int(record.group(4)), int(record.group(5))
                if kind in (b"(", b"["):
                    box = None
                    if kind == b"(" and record.group(8) is not None:
                        box = [h, v, int(record.group(6)), int(record.group(7)), int(record.group(8)), tag, number, []]
                        sync.boxes.setdefault(page, []).append(box)
                    stack.append(box)
                    continue
                owner = next((box for box in reversed(stack) if box is not None), None)
                if kind == b"h" and record.group(8) is not None and owner is None:
                    continue
                if owner is not None and number > 0:
                    owner[7].append((tag, number, h))
    except (OSError, EOFError, ValueError, gzip.BadGzipFile):
        if not sync.lines:
            return None
    return sync


def _header(sync, key, value, root):
    try:
        if key == b"Input":
            tag, _, name = value.partition(":")
            sync.inputs[int(tag)] = _relative(name, root)
        elif key == b"Magnification":
            sync.magnification = float(value) or 1000.0
        elif key == b"Unit":
            sync.unit = float(value) or 1.0
        elif key == b"X Offset":
            sync.x_offset = float(value)
        elif key == b"Y Offset":
            sync.y_offset = float(value)
    except ValueError:
        pass


def cli_line_at(pdf, page, x, y, root):
    """Même chose que SyncTeX.line_at, par le programme « synctex edit » (contrôle dans les tests) : (fichier, ligne)
    ou None (programme absent, rien trouvé)."""
    from . import texfile
    tool = texfile.find_tool("synctex")
    if not tool:
        return None
    try:
        code, output = texfile._run([tool, "edit", "-o", f"{page}:{x}:{y}:{Path(pdf).name}"], str(Path(pdf).parent),
                                    texfile._env(tool), 10)
    except (OSError, texfile.TexError):
        return None
    found = re.search(r"^Input:(.*)$", output, re.M)
    line = re.search(r"^Line:(-?\d+)$", output, re.M)
    if code != 0 or not found or not line:
        return None
    relative = _relative(found.group(1), root)
    return (relative, int(line.group(1))) if relative else None
