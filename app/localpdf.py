"""PDF importés depuis l'ordinateur (hors arXiv) : un article de la bibliothèque comme les autres, lu dans le
lecteur PDF, sans identifiant arXiv (« pdf-<empreinte> », source « local-pdf »).

À l'import : titre et auteurs tirés des métadonnées du PDF (sinon de sa première page, sinon du nom du fichier),
texte de chaque page extrait pour l'assistant (text.json), structure vide (pas de source TeX). La version arXiv de
l'article est cherchée (mention « arXiv:… » de la première page, sinon DOI ou titre via Semantic Scholar) : elle
offre toutes les fonctions (HTML, énoncés, dépendances), l'application la propose.
"""

import difflib
import hashlib
import io
import json
import logging
import math
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

import httpx

from . import db, s2
from .library import paper_dir, paper_lock, write_atomic, write_atomic_bytes
from .structure import DARK, STRUCTURE_VERSION, WHITE, luminance

SOURCE = "local-pdf"
log = logging.getLogger(__name__)
logging.getLogger("pypdf").setLevel(logging.ERROR)  # polices incomplètes, etc. : sans effet sur le texte utile
MAX_BYTES = 150_000_000
MAX_PAGES = 2000
PAGE_CHARS = 200_000   # texte gardé par page, et en tout (l'assistant en lit 400 000) : un PDF piégé (flux énorme)
TOTAL_CHARS = 800_000  # ne remplit ni la mémoire ni text.json
S2 = s2.GRAPH
ARXIV_STAMP = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I)
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;:)\]])")
LINE_NOISE = re.compile(r"\d{4}|©|doi|arxiv|page|journal|vol\.|issn", re.I)
FOOTNOTE = re.compile(r"(?<=[^\W\d_.])\d+|[∗*†‡§¶¹²³⁴⁵⁶⁷⁸⁹⁰]+")  # appels de note collés aux noms (« Bismut1 », « Gromov∗ »)
PARTICLES = {"de", "van", "von", "der", "den", "di", "da", "du", "le", "la", "dos", "del", "des", "ter", "zu", "y", "e"}
CONTINUES = re.compile(r"\b(?:and|of|in|the|for|on|with|to|a|an|from|over|at|via|by)$|[(,:–-]$", re.I)  # titre coupé
SUBTITLE = re.compile(r"(?:[IVX]+\.|Part\s+[IVX\d]+\b)\s")  # « I. Bott-Chern Forms… » : sous-titre de série
META_VERSION = 2  # repérage du titre et des auteurs : un PDF importé avant une amélioration est relu (redetect_all)
DATE_LINE = re.compile(r"\b(19|20)\d{2}\b|january|february|march|april|may|june|july|august|september|october|november|december", re.I)
NOISY_TITLE = re.compile(r"(\.(pdf|dvi|tex|docx?)$)|^(microsoft word|untitled|document\d*)\b|^\s*$", re.I)


# pas de source TeX : ni table des matières, ni énoncés, ni bibliographie (le lecteur attend ces clés)
EMPTY_STRUCTURE = {"version": STRUCTURE_VERSION, "source": SOURCE, "toc": [], "nodes": [], "bibliography": [], "labels": {},
                   "hiddenText": 0}


class PdfError(ValueError):
    pass


def paper_id(data):
    return "pdf-" + hashlib.sha1(data).hexdigest()[:12]


def is_local(paper):
    return bool(paper) and paper.get("source") == SOURCE


def _clean(value):
    return " ".join(str(value or "").replace("\x00", "").split())


FILLS = {b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
STROKES = {b"S", b"s", b"B", b"B*", b"b", b"b*"}
PAINTS = FILLS | STROKES | {b"n"}
SHOWS = {b"Tj", b"TJ", b"'", b'"'}
MIN_VISIBLE_PT = 1      # corps de texte en deçà : invisible à l'écran comme à l'impression
MIN_BACKGROUND_ALPHA = 0.5  # fond foncé plus transparent : le papier se voit au travers, ce n'est pas un fond
MIN_BACKGROUND_PT = 4   # fond plus petit (point, filet) : un texte blanc n'y tient pas
SCAN_SHARE = 0.5        # image qui couvre au moins cette part de la page : page scannée
MAX_FORM_DEPTH = 12     # formulaires imbriqués plus profond : vidés (aucun article n'en a)
MAX_STEPS = 2_000_000   # opérations parcourues par page, formulaires compris ; au-delà, formulaires laissés tels quels
FORM_COST = 200         # un formulaire affiché compte pour autant d'opérations (le relire coûte, même s'il est court)
MAX_LOOKUPS = 5_000_000 # fonds examinés par page pour juger le texte blanc ; au-delà, le texte blanc est gardé
GRID = 64               # côté des cases de l'index des fonds (points)
DEVICE_SPACES = {"/DeviceGray", "/DeviceRGB", "/DeviceCMYK", "/CalGray", "/CalRGB", "/ICCBased", "/G", "/RGB", "/CMYK"}
COMPONENTS = {"/DeviceGray": 1, "/CalGray": 1, "/G": 1, "/DeviceRGB": 3, "/CalRGB": 3, "/RGB": 3, "/DeviceCMYK": 4, "/CMYK": 4}
IDENTITY = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]


def _numbers(args):
    try:
        return [float(a) for a in args]
    except (TypeError, ValueError):
        return None


def _mult(a, b):
    """Produit de deux matrices PDF [a b c d e f] (a appliquée d'abord)."""
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


def _point(m, x, y):
    return x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]


def _box(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _luminance(values):
    """Luminance d'une couleur (gris, RVB, CMJN selon le nombre de composantes), None si on ne sait pas la lire."""
    if not values or len(values) not in (1, 3, 4):
        return None
    if len(values) == 1:
        return luminance(values * 3)
    if len(values) == 3:
        return luminance(values)
    c, m, y, k = values
    return luminance([(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)])


def _space(name, resources):
    """Espace de couleur : ("device", None) (composantes lues comme gris / RVB / CMJN), ("indexed", (n, palette)),
    ("other", None) (séparation, motif… : couleur inconnue)."""
    name = str(name)
    if name in DEVICE_SPACES:
        return "device", None
    try:
        space = resources["/ColorSpace"][name].get_object()
        kind = str(space[0]) if isinstance(space, list) else str(space)
        if kind in DEVICE_SPACES:
            return "device", None
        if kind == "/Indexed":
            base = space[1].get_object()
            base_name = str(base[0]) if isinstance(base, list) else str(base)
            count = int(base[1].get_object().get("/N", 0)) if base_name == "/ICCBased" else COMPONENTS.get(base_name, 0)
            lookup = space[3].get_object()
            data = lookup.get_data() if hasattr(lookup, "get_data") else _raw(lookup)
            if count:
                return "indexed", (count, data)
    except Exception:
        pass
    return "other", None


def _color(args, space):
    """Luminance de la couleur posée par sc/scn dans `space` (voir _space), None si inconnue."""
    kind, data = space
    numbers = _numbers(args)
    if kind == "device":
        return _luminance(numbers)
    if kind == "indexed" and numbers and len(numbers) == 1:
        count, palette = data
        entry = palette[int(numbers[0]) * count:(int(numbers[0]) + 1) * count]
        return _luminance([byte / 255 for byte in entry]) if len(entry) == count else None
    return None


def _raw(value):
    """Octets d'une chaîne affichée (Tj) ou de celles d'un tableau (TJ)."""
    if isinstance(value, list):
        return b"".join(_raw(item) for item in value if isinstance(item, (str, bytes)))
    if hasattr(value, "original_bytes"):  # octets lus dans le fichier (get_original_bytes les réencoderait)
        return value.original_bytes
    return bytes(value) if isinstance(value, bytes) else str(value).encode("latin-1", "replace")


def _off_groups(reader):
    """Calques (groupes de contenu optionnel) masqués par défaut : leurs numéros d'objet."""
    try:
        properties = reader.trailer["/Root"].get("/OCProperties")
        if not properties:
            return set()
        default = properties.get_object()["/D"].get_object()
        every = {getattr(ref, "idnum", None) for ref in properties.get_object().get("/OCGs", [])}
        on = {getattr(ref, "idnum", None) for ref in default.get("/ON", [])}
        off = {getattr(ref, "idnum", None) for ref in default.get("/OFF", [])}
        return (every - on if default.get("/BaseState") == "/OFF" else off) - {None}
    except Exception:
        return set()


class _Visible:
    """Parcours d'un flux de contenu qui retire les affichages de texte invisibles et les compte (`passages` : suites
    de texte invisible avec des lettres). État graphique suivi : q/Q, cm, couleurs de remplissage et de trait (espaces
    de couleur, palettes), opacité (gs), découpe (W), calques masqués (BDC /OC), Tf, Tz, Tr, TL, matrices de texte ;
    fonds foncés (formes, traits épais, bandes fines à la suite, dégradés, images) et aplats clairs, dans l'ordre où
    ils sont peints : la dernière forme sous un point dit s'il est sur un fond foncé. Formulaires (Do) jugés à chaque affichage, avec l'état du moment : copie nettoyée sous un autre nom
    s'il faut, l'original (partagé) n'est jamais modifié."""

    def __init__(self, reader, scan, page_box):
        self.reader, self.scan, self.page_box = reader, scan, page_box
        self.shapes, self.grid, self.big, self.strip = [], {}, [], None
        self.passages, self.running, self.scanned, self.steps, self.lookups, self.covered = 0, False, False, 0, 0, 0.0
        self.walking, self.copies, self.off, self.tables = set(), {}, _off_groups(reader), {}

    @staticmethod
    def start_state():
        return {"ctm": IDENTITY, "fill": 0.0, "stroke": 0.0, "fillSpace": ("device", None), "strokeSpace": ("device", None),
                "size": None, "scale": 1.0, "render": 0, "leading": 0.0, "cid": False, "alpha": 1.0, "strokeAlpha": 1.0,
                "clip": None, "hiddenLayer": False, "lineWidth": 1.0, "plain": True, "glyph": 1.0, "font": None}

    def run(self, operations, resources, state, depth=0):
        """(opérations gardées, flux changé) ; `state` (dict) : état graphique au départ, mis à jour."""
        kept, stack, path, layers, changed = [], [], {"points": [], "rect": True, "clip": False}, [], False
        tm = tlm = IDENTITY
        for args, operator in operations:
            self.steps += 1
            try:
                if operator == b"Do" and args:
                    replacement = self._draw(args[0], resources, state, depth)
                    if replacement is not None:
                        kept.append(([replacement], b"Do"))
                        changed = True
                        continue
                elif operator in (b"BDC", b"BMC"):
                    layers.append(state["hiddenLayer"])
                    if operator == b"BDC" and len(args) >= 2 and str(args[0]) == "/OC":
                        state["hiddenLayer"] = state["hiddenLayer"] or self._layer_off(args[1], resources)
                elif operator == b"EMC" and layers:
                    state["hiddenLayer"] = layers.pop()
                else:
                    self._step(args, operator, resources, state, stack, path)
                if operator == b"BT":
                    tm = tlm = IDENTITY
                elif operator == b"Tm" and len(args) >= 6 and _numbers(args[:6]):
                    tm = tlm = _numbers(args[:6])
                elif operator in (b"Td", b"TD") and len(args) >= 2 and _numbers(args[:2]):
                    tx, ty = _numbers(args[:2])
                    state["leading"] = -ty if operator == b"TD" else state["leading"]
                    tm = tlm = _mult([1, 0, 0, 1, tx, ty], tlm)
                elif operator in (b"T*", b"'", b'"'):
                    tm = tlm = _mult([1, 0, 0, 1, 0, -state["leading"]], tlm)
                if operator in SHOWS and self._hidden(args, state, tm):
                    if operator == b'"' and len(args) >= 3:
                        kept += [([args[0]], b"Tw"), ([args[1]], b"Tc")]
                    if operator in (b"'", b'"'):
                        kept.append(([], b"T*"))
                    changed = True
                    continue
            except Exception:  # opérande inattendu : l'opération est gardée telle quelle
                pass
            kept.append((args, operator))
        return kept, changed

    def _step(self, args, operator, resources, state, stack, path):
        if operator == b"q":
            stack.append(dict(state))
        elif operator == b"Q" and stack:
            state.clear()
            state.update(stack.pop())
        elif operator == b"cm" and len(args) >= 6:
            state["ctm"] = _mult(_numbers(args[:6]), state["ctm"])
        elif operator in (b"g", b"rg", b"k"):
            state["fillSpace"], state["fill"] = ("device", None), _luminance(_numbers(args))
        elif operator in (b"G", b"RG", b"K"):
            state["strokeSpace"], state["stroke"] = ("device", None), _luminance(_numbers(args))
        elif operator in (b"sc", b"scn"):
            state["fill"] = _color(args, state["fillSpace"])
        elif operator in (b"SC", b"SCN"):
            state["stroke"] = _color(args, state["strokeSpace"])
        elif operator in (b"cs", b"CS") and args:
            side = "fill" if operator == b"cs" else "stroke"
            space = _space(args[0], resources)
            state[side + "Space"] = space
            state[side] = 0.0 if space[0] == "device" else _color([0], space)  # couleur initiale : composantes nulles
        elif operator == b"gs" and args:
            extended = resources["/ExtGState"][args[0]].get_object()
            if "/ca" in extended:
                state["alpha"] = float(extended["/ca"])
            if "/CA" in extended:
                state["strokeAlpha"] = float(extended["/CA"])
            if "/BM" in extended or "/SMask" in extended:  # fusion (Multiply…) ou masque : un aplat clair ne couvre plus
                blend = extended.get("/BM", "/Normal")
                blend = blend[0] if isinstance(blend, list) and blend else blend
                state["plain"] = str(blend) in ("/Normal", "/Compatible") and str(extended.get("/SMask", "/None")) == "/None"
        elif operator == b"Tf" and len(args) >= 2:
            state["size"] = abs(float(args[1]))
            try:
                font = resources["/Font"][args[0]].get_object()
                state["cid"], state["font"] = font.get("/Subtype") == "/Type0", font
                # Type3 : le corps vaut size × FontMatrix (un glyphe de 1000 unités avec 0,001 : le corps tel quel) ;
                # polices bitmap de dvips + Ghostscript : matrice unité et corps 1, pixels mis à l'échelle par cm
                matrix = _numbers(font.get("/FontMatrix") or []) if font.get("/Subtype") == "/Type3" else None
                state["glyph"] = 1000 * math.hypot(matrix[2], matrix[3]) if matrix and len(matrix) >= 4 else 1.0
            except Exception:
                state["cid"], state["font"], state["glyph"] = False, None, 1.0
        elif operator == b"Tz" and args:
            state["scale"] = float(args[0]) / 100
        elif operator == b"Tr" and args:
            state["render"] = int(args[0])
        elif operator == b"TL" and args:
            state["leading"] = float(args[0])
        elif operator == b"w" and args:
            state["lineWidth"] = abs(float(args[0]))
        elif operator in (b"m", b"l", b"c", b"v", b"y") and _numbers(args):
            numbers = _numbers(args)
            path["points"] += [_point(state["ctm"], numbers[i], numbers[i + 1]) for i in range(0, len(numbers) - 1, 2)]
            path["rect"] = False
        elif operator == b"re" and len(args) >= 4:
            x, y, w, h = _numbers(args[:4])
            path["points"] += [_point(state["ctm"], x + dx, y + dy) for dx in (0, w) for dy in (0, h)]
        elif operator in (b"W", b"W*"):
            path["clip"] = True
        elif operator in PAINTS:
            points = path["points"]
            if points and path["clip"]:
                x0, y0, x1, y1 = _box(points)
                if state["clip"]:
                    c0, d0, c1, d1 = state["clip"]
                    x0, y0, x1, y1 = max(x0, c0), max(y0, d0), min(x1, c1), min(y1, d1)
                state["clip"] = (x0, y0, x1, y1)
            clip = state["clip"]
            if operator in FILLS and points:
                box = _clipped(_box(points), clip)
                if box is None or state["hiddenLayer"]:  # rien de peint dans un calque masqué
                    pass
                elif state["fill"] is None or state["fill"] < DARK:
                    if state["alpha"] >= MIN_BACKGROUND_ALPHA:
                        self._background(box)
                elif state["alpha"] >= 0.95 and state["plain"] and (path["rect"] or _upright(points)):
                    self._paint(box, False)  # aplat clair par-dessus : ce qu'il couvre n'est plus un fond
            if (operator in STROKES and points and (state["stroke"] is None or state["stroke"] < DARK)
                    and state["strokeAlpha"] >= MIN_BACKGROUND_ALPHA and not state["hiddenLayer"]):
                m = state["ctm"]
                half = state["lineWidth"] * math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) / 2
                if 2 * half >= MIN_BACKGROUND_PT:  # trait épais : un bandeau (segment droit), ou les côtés d'un cadre
                    if len(points) == 2 and (abs(points[0][0] - points[1][0]) < 0.01 or abs(points[0][1] - points[1][1]) < 0.01):
                        sides = [_box(points)]
                    elif path["rect"] and len(points) == 4:
                        x0, y0, x1, y1 = _box(points)
                        sides = [(x0, y0, x0, y1), (x1, y0, x1, y1), (x0, y0, x1, y0), (x0, y1, x1, y1)]
                    else:
                        sides = []
                    for x0, y0, x1, y1 in sides:
                        box = _clipped((x0 - half, y0 - half, x1 + half, y1 + half), clip)
                        if box is not None:
                            self._background(box)
            path.update(points=[], rect=True, clip=False)
        elif operator == b"sh":
            if state["alpha"] >= MIN_BACKGROUND_ALPHA and not state["hiddenLayer"]:
                self._background(state["clip"] or self.page_box)
        elif operator == b"INLINE IMAGE":
            self._image(state)

    def _layer_off(self, ref, resources):
        """Calque masqué : `ref` est un nom de /Properties, ou la référence (ou le dictionnaire) du calque. Appartenance
        (OCMD) : selon sa règle /P (AnyOn par défaut) ; une expression /VE n'est pas lue (visible)."""
        if isinstance(ref, str):
            ref = resources["/Properties"].get_object().raw_get(ref)
        target = ref.get_object()
        if target.get("/Type") == "/OCMD":
            members = target.get("/OCGs") or []
            members = members if isinstance(members, list) else [members]
            if not members or "/VE" in target:
                return False
            off = [getattr(member, "idnum", None) in self.off for member in members]
            policy = str(target.get("/P", "/AnyOn"))
            visible = {"/AllOn": not any(off), "/AnyOff": any(off), "/AllOff": all(off)}.get(policy, not all(off))
            return not visible
        return getattr(ref, "idnum", None) in self.off

    def _image(self, state):
        """Image : un fond ; les images qui couvrent ensemble au moins SCAN_SHARE de la page (scan d'un seul tenant
        ou en bandes) en font une page scannée."""
        if state["alpha"] < MIN_BACKGROUND_ALPHA or state["hiddenLayer"]:
            return
        box = _box([_point(state["ctm"], x, y) for x in (0, 1) for y in (0, 1)])
        self._background(box)
        x0, y0, x1, y1 = self.page_box
        width = max(0.0, min(box[2], x1) - max(box[0], x0))
        height = max(0.0, min(box[3], y1) - max(box[1], y0))
        self.covered += width * height
        if self.covered >= SCAN_SHARE * (x1 - x0) * (y1 - y0):
            self.scanned = True

    def _background(self, box):
        """Fond foncé (ou de couleur inconnue, ou image) peint sur `box` : un texte blanc posé dessus se lit. Bandes
        fines peintes à la suite (dégradé découpé en tranches) : réunies en un fond."""
        thin = box[2] - box[0] < MIN_BACKGROUND_PT or box[3] - box[1] < MIN_BACKGROUND_PT
        if thin:
            last, self.strip = self.strip, box
            if last is None:
                return
            same_x = abs(last[0] - box[0]) <= 1 and abs(last[2] - box[2]) <= 1 and box[1] <= last[3] + 1 and last[1] <= box[3] + 1
            same_y = abs(last[1] - box[1]) <= 1 and abs(last[3] - box[3]) <= 1 and box[0] <= last[2] + 1 and last[0] <= box[2] + 1
            if not (same_x or same_y):
                return
            box = self.strip = (min(last[0], box[0]), min(last[1], box[1]), max(last[2], box[2]), max(last[3], box[3]))
            if box[2] - box[0] < MIN_BACKGROUND_PT or box[3] - box[1] < MIN_BACKGROUND_PT:
                return
        self._paint(box, True)

    def _paint(self, box, dark):
        """Forme peinte sur `box`, dans l'ordre du flux : la dernière qui recouvre un point dit s'il est sur un fond
        foncé (`dark`) ou clair (aplat clair peint par-dessus)."""
        if box[2] - box[0] < MIN_BACKGROUND_PT or box[3] - box[1] < MIN_BACKGROUND_PT:
            return
        self.shapes.append((box, dark))
        index = len(self.shapes) - 1
        x0, y0, x1, y1 = (int(math.floor(v / GRID)) for v in box)
        if (x1 - x0 + 1) * (y1 - y0 + 1) > 400:
            self.big.append(index)
        else:
            for cell in ((x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)):
                self.grid.setdefault(cell, []).append(index)

    def _top(self, indices, x, y):
        """Indice de la dernière forme de `indices` (croissants) qui recouvre (x, y), ou -1 ; au-delà de MAX_LOOKUPS
        formes examinées sur la page, seules les 64 dernières de la case le sont encore."""
        for index in reversed(indices if self.lookups <= MAX_LOOKUPS else indices[-64:]):
            self.lookups += 1
            (x0, y0, x1, y1), dark = self.shapes[index]
            margin = 1 if dark else 0
            if x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin:
                return index
        return -1

    def _on_dark(self, points):
        for x, y in points:
            cell = (int(math.floor(x / GRID)), int(math.floor(y / GRID)))
            top = max(self._top(self.grid.get(cell, ()), x, y), self._top(self.big, x, y))
            if top < 0 or not self.shapes[top][1]:
                return False
        return True

    def _draw(self, name, resources, state, depth):
        """Do d'une image (fond, page scannée) ou d'un formulaire : nom d'une copie nettoyée à afficher à sa place,
        ou None (rien à changer)."""
        from pypdf.generic import ContentStream
        xobjects = resources["/XObject"]
        reference = xobjects[name]
        target = reference.get_object()
        if target.get("/Subtype") == "/Image":
            self._image(state)
            return None
        if target.get("/Subtype") != "/Form":
            return None
        key = getattr(reference, "idnum", None) or id(target)
        if key in self.walking:  # formulaire qui s'affiche lui-même : l'appel est vidé (l'original garde son texte)
            return self._copy(target, [], xobjects)
        if self.steps > MAX_STEPS:
            return None
        self.steps += FORM_COST
        inner = dict(state, ctm=_mult(_numbers(target.get("/Matrix", IDENTITY)) or IDENTITY, state["ctm"]))
        bbox = _numbers(target.get("/BBox") or [])
        if bbox and len(bbox) == 4:  # le cadre du formulaire découpe ce qu'il affiche
            x0, y0, x1, y1 = _box([_point(inner["ctm"], x, y) for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])])
            if state["clip"]:
                c0, d0, c1, d1 = state["clip"]
                x0, y0, x1, y1 = max(x0, c0), max(y0, d0), min(x1, c1), min(y1, d1)
            inner["clip"] = (x0, y0, x1, y1)
        if "/OC" in target:
            inner["hiddenLayer"] = state["hiddenLayer"] or self._layer_off(target.raw_get("/OC"), resources)
        if depth >= MAX_FORM_DEPTH:
            kept, changed = [], True
        else:
            self.walking.add(key)
            try:
                kept, changed = self.run(ContentStream(target, self.reader).operations,
                                         target.get("/Resources") or resources, inner, depth + 1)
            finally:
                self.walking.discard(key)
        if not changed:
            return None
        return self._copy(target, kept, xobjects)

    def _copy(self, target, kept, xobjects):
        """Nom d'une copie du formulaire `target` qui affiche `kept`, rangée dans `xobjects` (ressources du flux)."""
        from pypdf.generic import ContentStream, DecodedStreamObject, NameObject
        copy = DecodedStreamObject()
        copy.update({k: v for k, v in target.items() if k not in ("/Filter", "/DecodeParms", "/Length")})
        content = ContentStream(None, self.reader)
        content.operations = kept
        copy.set_data(content.get_data())
        fresh = NameObject(f"/ArxivReaderVisible{id(copy)}")
        owner = xobjects.get_object()
        self.copies[fresh] = owner
        owner[fresh] = copy
        return fresh

    def _letters(self, raw, font):
        """Le texte affiché contient-il des lettres ? Lu avec le codage de sa police (un sous-ensemble peut mettre les
        lettres sur des codes de ponctuation) ; police illisible : les octets eux-mêmes."""
        try:
            if id(font) not in self.tables:
                self.tables[id(font)] = (font, _decoding(font))
            return any(ch.isalpha() for code in raw for ch in self.tables[id(font)][1].get(code, ""))
        except Exception:
            return bool(re.search(rb"[A-Za-z\x80-\xff]", raw))

    def _hidden(self, args, state, tm):
        raw = _raw(args[-1] if args else b"")
        blank = not raw.strip(b"\x00 ") if state.get("cid") else not raw.strip()
        letters = bool(raw.strip(b"\x00")) if state.get("cid") else self._letters(raw, state["font"])
        m = _mult(tm, state["ctm"])
        size = state.get("size")
        body = None if size is None else size * state["glyph"]
        tiny = body is not None and (body * math.hypot(m[2], m[3]) < MIN_VISIBLE_PT
                                     or body * state["scale"] * math.hypot(m[0], m[1]) < MIN_VISIBLE_PT)
        render = state["render"] % 8
        sides = {0: ("fill",), 4: ("fill",), 1: ("stroke",), 5: ("stroke",), 2: ("fill", "stroke"), 6: ("fill", "stroke")}.get(render, ())
        alphas = {"fill": state["alpha"], "stroke": state["strokeAlpha"]}
        height = (size or 10) * math.hypot(m[2], m[3]) or 10
        em = size or 10
        middle = 0.25 * em * state["scale"] * (len(raw) / 2 if state.get("cid") else len(raw))  # milieu estimé du texte
        points = [_point(m, 0, 0), _point(m, 0, em * 0.3), _point(m, em * 0.5, em * 0.3), _point(m, max(middle, em * 0.5), em * 0.3)]
        light = all(state[side] is not None and state[side] >= WHITE for side in sides)  # rempli et tracé (Tr 2) : les deux
        white = bool(sides) and light and not self._on_dark(points)
        clear = bool(sides) and all(alphas[side] < 0.05 for side in sides)
        clip = state["clip"]
        clipped = clip is not None and all(not (clip[0] - height <= x <= clip[2] + height and clip[1] - height <= y <= clip[3] + height)
                                           for x, y in points)
        hidden = not blank and (tiny or white or clear or clipped or state["hiddenLayer"]
                                or render in (3, 7) and not (self.scan or self.scanned))
        if hidden and letters:
            self.passages += not self.running
            self.running = True
        elif not blank and not hidden:
            self.running = False
        return hidden


def _decoding(font):
    """{code: texte} d'une police à un octet : chacun des 256 codes affiché seul (sa hauteur le repère) dans une page vide,
    lu par pypdf avec le codage et le ToUnicode de la police."""
    from pypdf import PageObject
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    page = PageObject.create_blank_page(None, 100, 100)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b" ".join(b"BT /F 12 Tf 0 %d Td (\\%03o) Tj ET" % (code * 20, code) for code in range(256)))
    page[NameObject("/Contents")] = stream
    table = {}
    page.extract_text(visitor_text=lambda text, cm, tm, font_dict, size: text not in ("", "\n") and table.update({round(tm[5] / 20): text}))
    return table


def _clipped(box, clip):
    """Partie de `box` dans la découpe `clip` (ou toute la boîte sans découpe), None si elle est vide."""
    if clip is None:
        return box
    box = (max(box[0], clip[0]), max(box[1], clip[1]), min(box[2], clip[2]), min(box[3], clip[3]))
    return box if box[0] < box[2] and box[1] < box[3] else None


def _upright(points):
    """Contour fait de côtés horizontaux et verticaux (rectangle tracé à la main)."""
    pairs = zip(points, points[1:] + points[:1])
    return len(points) >= 4 and all(abs(a[0] - b[0]) < 0.01 or abs(a[1] - b[1]) < 0.01 for a, b in pairs)


def _has_forms(resources):
    try:
        xobjects = resources["/XObject"].get_object()
        return any(xobjects[name].get_object().get("/Subtype") == "/Form" for name in xobjects)
    except Exception:
        return False


def _has_images(resources, seen=None, depth=0):
    """Une image (XObject) dans ces ressources ou dans celles des formulaires qu'elles contiennent (sans lire leurs flux)."""
    seen = set() if seen is None else seen
    try:
        xobjects = resources["/XObject"].get_object()
        for name in xobjects:
            reference = xobjects[name]
            target = reference.get_object()
            if target.get("/Subtype") == "/Image":
                return True
            key = getattr(reference, "idnum", None) or id(target)
            if target.get("/Subtype") == "/Form" and key not in seen and depth < MAX_FORM_DEPTH:
                seen.add(key)
                if _has_images(target.get("/Resources") or {}, seen, depth + 1):
                    return True
    except Exception:
        return False
    return False


def _page_text(page, hidden=None):
    """Texte de la page, sans le texte invisible, comme dans la version HTML (structure.mark_hidden) : il peut porter
    une injection d'invite et n'est jamais transmis à l'IA. Invisible : blanc ou presque (luminance ≥ WHITE) hors d'un
    fond foncé, dégradé ou image peints dessous, corps de moins de MIN_VISIBLE_PT, transparent, découpé, calque masqué,
    mode de rendu invisible (Tr 3) sauf sur une page scannée (une image en couvre au moins la moitié : la couche de
    texte reconnu est le texte de la page). `hidden` (liste) : y ajoute le nombre de passages invisibles avec des
    lettres. Le contenu de la page est remplacé en mémoire (rien n'est écrit)."""
    from pypdf.generic import ContentStream, NameObject
    passages = 0
    try:
        contents = page.get_contents()
        if contents is not None:
            box = [float(v) for v in page.mediabox]
            box = (min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3]))
            resources = page.get("/Resources") or {}
            operations = contents.operations
            scan = False
            # la page est scannée si des images la couvrent : sans image (en ligne, ou dans les ressources, formulaires compris),
            # ce premier parcours ne peut rien changer ; il ne sert qu'au mode de rendu invisible, dans la page ou un formulaire
            images = any(operator == b"INLINE IMAGE" for args, operator in operations) or _has_images(resources)
            if images and (any(operator == b"Tr" and args and args[0] in (3, 7) for args, operator in operations) or _has_forms(resources)):
                first = _Visible(page.pdf, False, box)  # page scannée ? les images peuvent venir après le texte
                try:
                    first.run(operations, resources, _Visible.start_state())
                    scan = first.scanned
                finally:
                    for name, owner in first.copies.items():
                        owner.pop(name, None)
            walk = _Visible(page.pdf, scan, box)
            kept, changed = walk.run(operations, resources, _Visible.start_state())
            passages = walk.passages
            if walk.steps > MAX_STEPS:
                log.info("page très chargée : formulaires au-delà de %d opérations laissés tels quels", MAX_STEPS)
            if changed:
                # relu en octets : un flux déjà analysé serait repris tel quel par pypdf, chaînes décodées sans le
                # codage ni le ToUnicode des polices (ligatures, formules, polices CID : texte de toute la page faux)
                contents.operations = kept
                page[NameObject("/Contents")] = ContentStream(contents, page.pdf, "bytes")
    except Exception:  # flux illisible pour ce parcours : pypdf en tire ce qu'il peut
        log.debug("texte invisible non cherché", exc_info=True)
    try:
        full = page.extract_text() or ""
    except Exception:  # page abîmée, flux trop gros (garde de pypdf) : page sans texte, le reste est gardé
        return ""
    if hidden is not None:
        hidden.append(passages)
    return full[:PAGE_CHARS]


def _names(line):
    """Auteurs d'une ligne (« J.-M. Bismut1, H. Gillet2 and C. Soule3 », « XIAONAN MA AND WEIPING ZHANG »), ou []."""
    text = FOOTNOTE.sub("", line).strip(" ,;")
    # accents détachés par l'extraction (« VI ˆET -ANH NGUYˆEN » pour Việt-Anh Nguyễn) : retirés, mot recollé
    text = re.sub(r"\s+-", "-", re.sub(r"\s?[\u02c6\u02dc\u00a8\u00b4\u02d8\u02c7`]", "", text))
    if not text or len(text) > 200 or re.search(r"[\d@:()\[\]/]", text):
        return []
    parts = [part.strip() for part in re.split(r"\s*(?:,|;|&|\band\b)\s*", text, flags=re.I) if part.strip()]
    if not 1 <= len(parts) <= 12:
        return []
    names = []
    for part in parts:
        tokens = part.split()
        if not 2 <= len(tokens) <= 5:
            return []
        for token in tokens:
            letter = next((c for c in token if c.lower() != c.upper()), "")  # première lettre à casse
            if token.lower() not in PARTICLES and not letter.isupper():
                return []
        names.append(part.title() if part.isupper() else part)
    return names


def _title_block(lines, hint=""):
    """(titre, indice de la ligne qui le suit) : la ligne du titre (celle qui commence comme `hint`, titre des
    métadonnées, sinon la première qui ne ressemble ni à un en-tête de revue, ni à une date) et ses lignes de suite :
    titre coupé (« … of » / « …, » / parenthèse ouverte / suite en minuscule), sous-titre de série (« I. … »), titre
    en capitales sur deux lignes. S'arrête à la ligne des auteurs."""
    key = _norm(hint)[:25]
    if len(key) >= 8:
        start = next((i for i, line in enumerate(lines) if len(_norm(line)) >= 8 and key.startswith(_norm(line)[:25])), None)
    else:
        start = next((i for i, line in enumerate(lines) if len(line.split()) >= 3 and not LINE_NOISE.search(line)), None)
    if start is None:
        return "", None
    block, i = [lines[start]], start + 1
    while i < len(lines) and len(block) < 4 and len(lines[i]) <= 120:
        cur, nxt = block[-1], lines[i]
        if not (re.match(r"[a-z0-9(]", nxt) or CONTINUES.search(cur) or cur.count("(") > cur.count(")") or SUBTITLE.match(nxt)):
            # sinon, seule suite possible : titre en capitales coupé (pas une ligne de plusieurs auteurs)
            caps = cur.isupper() and nxt.isupper() and len(cur) > 40
            if not (caps and len(_names(nxt)) <= 1):
                break
        block.append(nxt)
        i += 1
    return FOOTNOTE.sub("", " ".join(block)).strip(), i


def read_pdf(data, filename=""):
    """Titre, auteurs, résumé probable, DOI et texte de chaque page."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfError("encrypted")
        pages, total, hidden = [], 0, []
        for page in reader.pages[:MAX_PAGES]:
            pages.append(_page_text(page, hidden) if total < TOTAL_CHARS else "")
            total += len(pages[-1])
        try:
            info = reader.metadata or {}
        except Exception:
            info = {}
    except PdfError:
        raise
    except Exception as error:  # pypdf lève bien des sortes d'erreurs sur un fichier abîmé ou piégé
        raise PdfError(f"unreadable: {type(error).__name__}") from error
    first = "\n".join(pages[:2])
    lines = [_clean(line) for line in (pages[0] if pages else "").splitlines()]
    lines = [line for line in lines if line]
    title = _clean(info.get("/Title"))
    usable = not (NOISY_TITLE.search(title) or len(title) < 4)
    # titre de la première page (sauf titre des métadonnées utilisable : il sert alors à retrouver sa ligne)
    found, start = _title_block(lines, title if usable else "")
    if not usable:
        title = found
    title = title or Path(filename or "document.pdf").stem.replace("_", " ")
    authors = [a for a in re.split(r"\s*(?:;|,| and | & )\s*", _clean(info.get("/Author"))) if a]
    # ligne des auteurs, sous le titre (une ligne plus bas au plus : date, mention de dédicace…)
    for index in ([start, start + 1] if start is not None else []):
        names = _names(lines[index]) if index < len(lines) else []
        if names:
            authors, start = authors or names, index + 1
            break
    abstract = ""
    found = re.search(r"\bAbstract[.:\s—-]*(.{80,2500}?)(?=\n\s*(?:1\.?\s+Introduction|Introduction|Keywords|Contents)\b|\Z)",
                      first, re.I | re.S)
    if found:
        abstract = _clean(found.group(1))[:2000]
    elif start is not None:
        # pas de titre « Abstract » : le paragraphe entre les auteurs (et la date) et « Keywords » / l'introduction
        rest = [line for line in lines[start:start + 40]]
        while rest and DATE_LINE.search(rest[0]) and len(rest[0]) < 40:
            rest.pop(0)
        stop = next((i for i, line in enumerate(rest) if re.match(r"(keywords|ams subject|msc|1\.?\s+introduction|introduction|contents)\b", line, re.I)), None)
        if stop:
            candidate = " ".join(rest[:stop])
            if 80 <= len(candidate) <= 2500:
                abstract = candidate
    doi = DOI.search(first)
    return {"title": title[:300], "authors": authors[:30], "abstract": abstract, "doi": doi.group(1) if doi else None,
            "pages": pages, "hidden": sum(hidden)}


def arxiv_stamp(data):
    """Identifiant arXiv imprimé sur les deux premières pages (prépublication téléchargée d'arXiv), ou None."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return None
        found = ARXIV_STAMP.search("\n".join(_page_text(page) for page in reader.pages[:2]))
    except Exception:  # fichier illisible : l'import dira pourquoi
        return None
    return found.group(1) if found else None


def import_pdf(data, filename="", check=False):
    """Article de la bibliothèque à partir d'un PDF ; déjà importé (même fichier) : celui-là, remis en bibliothèque.
    `check` : PDF venu d'arXiv (mention « arXiv:… ») et pas encore importé → rien n'est importé, l'identifiant est
    rendu ({"arxivId"}) pour que la page propose la version arXiv à la place (pas de double import)."""
    if len(data) > MAX_BYTES:
        raise PdfError("too large")
    if b"%PDF-" not in data[:1024]:  # la norme admet quelques octets avant l'en-tête
        raise PdfError("not a PDF")
    pid = paper_id(data)
    if check and not db.get_paper(pid):
        stamp = arxiv_stamp(data)
        if stamp:
            return {"arxivId": stamp}
    with paper_lock(pid):
        return _import(pid, data, filename)


def _import(pid, data, filename):
    existing = db.get_paper(pid)
    if existing:
        if not existing.get("in_library"):
            db.update_paper(pid, in_library=1)
        return db.get_paper(pid)
    found = read_pdf(data, filename)
    folder = paper_dir(pid)
    folder.mkdir(parents=True, exist_ok=True)
    write_atomic_bytes(folder / "paper.pdf", data)
    write_atomic(folder / "text.json", json.dumps({"pages": found["pages"], "metaVersion": META_VERSION, "textVersion": TEXT_VERSION},
                                                  ensure_ascii=False))
    write_atomic(folder / "structure.json", json.dumps({**EMPTY_STRUCTURE, "hiddenText": found["hidden"]}))
    db.save_paper({"id": pid, "arxiv_id": "", "version": None, "title": found["title"], "authors": found["authors"],
                   "abstract": found["abstract"], "categories": [], "primary_category": "", "published": None,
                   "updated": None, "doi": found["doi"], "journal_ref": None, "comment": _clean(filename)[:200],
                   "source": SOURCE, "in_library": 1})
    return db.get_paper(pid)


TEXT_VERSION = 5  # 2 : texte invisible retiré ; 3 : texte gardé lu avec le codage des polices ; 4 : jaune, turquoise ; 5 : Type3,
                  # passages comptés sur le texte décodé, fonds transparents ou en calque masqué, BBox des formulaires. Un text.json plus
                  # ancien est refait au démarrage (redetect_all), ou à sa première lecture par l'IA


def text(pid):
    """Texte du PDF pour l'assistant, page par page (« [page 3] ») : il peut renvoyer à une page."""
    path = paper_dir(pid) / "text.json"
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        pages = stored["pages"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    if stored.get("textVersion", 1) < TEXT_VERSION and (path.parent / "paper.pdf").exists():
        pages = _refresh_text(pid)
    return "\n\n".join(f"[page {n}]\n{page.strip()}" for n, page in enumerate(pages, 1) if page.strip())


def _refresh_text(pid):
    """text.json extrait avant le retrait du texte invisible : refait (le reste du fichier gardé), le nombre de
    passages invisibles signalé au lecteur (structure.json). Renvoie les pages."""
    folder = paper_dir(pid)
    with paper_lock(pid):
        stored = json.loads((folder / "text.json").read_text(encoding="utf-8"))
        if stored.get("textVersion", 1) >= TEXT_VERSION:
            return stored["pages"]
        try:
            found = read_pdf((folder / "paper.pdf").read_bytes())
            stored["pages"], hidden = found["pages"], found["hidden"]
        except (OSError, PdfError):
            hidden = 0  # illisible : texte gardé tel quel, pas relu à chaque fois
        write_atomic(folder / "text.json", json.dumps({**stored, "textVersion": TEXT_VERSION}, ensure_ascii=False))
        saved = folder / "structure.json"
        if hidden and saved.exists():
            structure = json.loads(saved.read_text(encoding="utf-8"))
            write_atomic(saved, json.dumps({**structure, "hiddenText": hidden}, ensure_ascii=False))
        return stored["pages"]


def update_meta(pid, title, authors):
    paper = db.get_paper(pid)
    if not is_local(paper) and paper.get("source") != "local-tex":
        raise PdfError("not an imported PDF")
    (paper_dir(pid) / "meta-edited").touch()  # corrigés à la main : jamais relus du PDF (redetect_all) ni de la source
    db.update_paper(pid, title=_clean(title)[:300] or paper["title"],
                    authors=json.dumps([_clean(a) for a in authors if _clean(a)][:30], ensure_ascii=False))
    return db.get_paper(pid)


def redetect_all():
    """Au démarrage : texte des PDF (importés, ou articles d'arXiv en PDF seul) extrait avant TEXT_VERSION, refait
    (la recherche plein texte et l'IA lisent text.json) ; titre et auteurs des PDF importés avant la dernière
    amélioration du repérage, relus du PDF (sauf ceux corrigés à la main). Résumé repris seulement s'il manquait."""
    with db.connect() as connection:
        rows = connection.execute("SELECT id, source FROM papers WHERE source IN (?, 'none')", (SOURCE,)).fetchall()
    for row in rows:
        folder = paper_dir(row["id"])
        try:
            stored = json.loads((folder / "text.json").read_text(encoding="utf-8"))
            if stored.get("textVersion", 1) < TEXT_VERSION and (folder / "paper.pdf").exists():
                _refresh_text(row["id"])
                stored = json.loads((folder / "text.json").read_text(encoding="utf-8"))
            if row["source"] != SOURCE or stored.get("metaVersion", 1) >= META_VERSION or (folder / "meta-edited").exists():
                continue
            paper = db.get_paper(row["id"])
            found = read_pdf((folder / "paper.pdf").read_bytes(), paper.get("comment") or "")
        except (OSError, ValueError, PdfError):
            continue
        fields = {"title": found["title"][:300], "authors": json.dumps(found["authors"], ensure_ascii=False)}
        if not paper.get("abstract") and found["abstract"]:
            fields["abstract"] = found["abstract"]
        db.update_paper(row["id"], **fields)
        write_atomic(folder / "text.json", json.dumps({**stored, "metaVersion": META_VERSION}, ensure_ascii=False))


def _norm(title):
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()).strip()


def _s2(path, params, api_key=None):
    """Une seule demande, courte (pas de nouvel essai) : la page l'attend après l'import."""
    headers = s2.headers(api_key)
    with httpx.Client(headers=headers, timeout=10) as client:
        response = s2.paced(lambda: client.get(f"{S2}{path}", params=params), api_key)()
    if response.status_code in (404, 429):
        return None
    response.raise_for_status()
    return response.json()


def arxiv_match(paper, api_key=None, get_s2=None):
    """Version arXiv du même article : {"arxivId", "title", "via"} ou None. Mention « arXiv:… » imprimée sur la
    première page (préprint), sinon DOI, sinon titre (Semantic Scholar ; titre presque identique exigé)."""
    pages = []
    try:
        pages = json.loads((paper_dir(paper["id"]) / "text.json").read_text(encoding="utf-8"))["pages"][:2]
    except (OSError, ValueError, KeyError):
        pass
    stamp = ARXIV_STAMP.search("\n".join(pages))
    if stamp:
        return {"arxivId": stamp.group(1), "title": paper["title"], "via": "stamp"}
    get_s2 = get_s2 or _s2
    fields = {"fields": "externalIds,title"}
    found = None
    if paper.get("doi"):
        found = get_s2(f"/paper/DOI:{quote(paper['doi'], safe='/')}", fields, api_key)
        via = "doi"
    if not (found and (found.get("externalIds") or {}).get("ArXiv")) and len(_norm(paper["title"]).split()) >= 3:
        candidate = get_s2("/paper/search/match", {"query": paper["title"], **fields}, api_key)
        candidate = ((candidate or {}).get("data") or [None])[0] if "data" in (candidate or {}) else candidate
        if candidate and difflib.SequenceMatcher(None, _norm(candidate.get("title")), _norm(paper["title"])).ratio() > 0.9:
            found, via = candidate, "title"
    arxiv = ((found or {}).get("externalIds") or {}).get("ArXiv")
    return {"arxivId": arxiv, "title": found.get("title") or paper["title"], "via": via} if arxiv else None


# ---------- Référence d'une bibliographie : sa version arXiv (« Chercher sur arXiv ») ----------

ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})$")
_INITIAL = re.compile(r"(?:^|[\s,.\-])[A-Z]\.?(?=[\s,.\-]|$)")
_PARTICLES = {"and", "de", "van", "von", "der", "den", "la", "le", "di", "da", "du", "del", "et"}


def _authorish(piece):
    """Morceau d'une liste d'auteurs (« J. Wang », « L. Boutet de Monvel and V. Guillemin », « [BG] »)."""
    if piece.startswith("["):
        return True
    words = [w for w in re.findall(r"[^\W\d_][^\s,]*", piece) if w.lower() not in _PARTICLES]
    return bool(words) and all(w[0].isupper() for w in words) and (bool(_INITIAL.search(piece)) or len(words) <= 1)


def _trim(part):
    """Morceau entre deux points : sans les auteurs en tête, ni la revue après (« , J. Math. Phys. 54 »)."""
    pieces = [p.strip() for p in part.split(",") if p.strip()]
    while pieces and _authorish(pieces[0]):
        pieces.pop(0)
    if not pieces:
        return ""
    kept = [pieces[0]]
    for piece in pieces[1:]:  # suite du titre : commence en minuscule (« the Heat Equation »), sans chiffres
        if not re.match(r"[a-z]", piece) or re.search(r"\d", piece):
            break
        kept.append(piece)
    return ", ".join(kept)


def guess_title(text):
    """Titre probable d'une référence écrite à la main : entre guillemets, sinon le plus long morceau fait de mots
    (entre points ou deux-points), sans auteurs ni revue ; formules ôtées (recherche par mots). Approximatif : il
    remplit la recherche, que l'on peut corriger."""
    text = re.sub(r"\s+", " ", re.sub(r"\$[^$]*\$", " ", text or "")).strip()
    text = re.sub(r"(?<![\w-])-(?=\w)", "", text)  # « $J$-twist » : « -twist »
    quoted = re.search(r"[“\"«]\s*([^”\"»]{12,}?)\s*[”\"»]", text)
    if quoted:
        return quoted.group(1).strip(" ,.")
    best, score = "", 0
    for part in re.split(r"(?<=[^\W\d_]{2})[.:;](?:\s+|$)|(?<=[^\W\d_]{2} [A-Z])\.\s+|\s[.:]\s|:\s+", text):
        part = _trim(part.strip(" ,.:;"))
        words = re.findall(r"[^\W\d_]{2,}", part)
        if len(words) < 2:
            continue
        value = len(words) - 0.5 * len(re.findall(r"\d", part))
        if value > score:
            best, score = part, value
    return best


def first_author(text):
    """Nom du premier auteur d'une référence (« M. Wodzicki » → Wodzicki, « Ma, X., … » → Ma), ou ""."""
    head = re.sub(r"^\s*\[[^\]]*\]\s*", "", text or "")
    for word in re.findall(r"[^\W\d_][^\s,.:;]*", head):
        if len(word) >= 2 and word[0].isupper() and word.lower() not in _PARTICLES and not word.endswith("’s"):
            return word
    return ""


ZBMATH = "https://zbmath.org"
ZBMATH_SURE = 0.5  # probabilité donnée par zbMATH au-dessus de laquelle la référence est bien ce document
ZBL_ID = re.compile(r"^\d{4}\.\d{5}$")


def _zbmath_match(text):
    """Recherche de citations de zbMATH (son interface publique, en JSON) : une seule demande, courte."""
    with httpx.Client(headers={"User-Agent": "arXivReader"}, timeout=8) as client:
        response = client.get(f"{ZBMATH}/citationmatching/match", params={"q": text, "n": 1})
    response.raise_for_status()
    return response.json()


def zbmath_query(text, title=""):
    """Recherche zbMATH remplie pour une référence : premier auteur et titre deviné, en mots (sans formules)."""
    guessed = (title or "").strip() or guess_title(text)
    words = " ".join(filter(None, [first_author(text), re.sub(r"\$[^$]*\$", " ", guessed)]))
    return re.sub(r"\s+", " ", words).strip()[:200]


def zbmath_search(text, title=""):
    """Adresse de la recherche zbMATH remplie pour une référence."""
    query = zbmath_query(text, title)
    return f"{ZBMATH}/?q={quote(query)}" if query else f"{ZBMATH}/"


def reference_zbmath(text, title="", match=None):
    """Référence de bibliographie dans zbMATH : {"zbl", "url"}. La page du document que la recherche de citations de
    zbMATH trouve sûrement (probabilité ≥ ZBMATH_SURE), sinon sa recherche ordinaire, déjà remplie (zbl None). Sa page
    de recherche de citations ne se remplit pas depuis l'adresse : elle n'est plus ouverte."""
    fallback = {"zbl": None, "url": zbmath_search(text, title)}
    cited = re.sub(r"\s+", " ", re.sub(r"\$[^$]*\$", " ", text or "")).strip()[:1000]
    if not cited:
        return fallback
    best = (((match or _zbmath_match)(cited) or {}).get("results") or [None])[0] or {}
    zbl = str(best.get("zbl_id") or "")
    if ZBL_ID.match(zbl) and float(best.get("probability") or 0) >= ZBMATH_SURE:
        return {"zbl": zbl, "url": f"{ZBMATH}/?q={quote('an:' + zbl)}"}
    return fallback


def reference_arxiv(text, title="", doi="", api_key=None, get_s2=None):
    """Version arXiv d'une référence de bibliographie, pour la page Recherche : {"arxivId", "title", "author"}.
    Semantic Scholar : par DOI, sinon par titre (le titre trouvé doit figurer dans la référence) ; arxivId None si
    rien n'est trouvé — la recherche se fait alors par titre et premier auteur."""
    get_s2 = get_s2 or _s2
    fields = {"fields": "externalIds,title"}
    guessed = (title or "").strip() or guess_title(text)
    cited = f" {_norm(text)} "
    found = None
    if doi:
        found = get_s2(f"/paper/DOI:{quote(doi, safe='/')}", fields, api_key)
    if not (found and (found.get("externalIds") or {}).get("ArXiv")) and len(_norm(guessed).split()) >= 3:
        candidate = get_s2("/paper/search/match", {"query": guessed, **fields}, api_key)
        candidate = ((candidate or {}).get("data") or [None])[0] if "data" in (candidate or {}) else candidate
        known = _norm((candidate or {}).get("title"))
        if known and len(known.split()) >= 3 and f" {known} " in cited:  # le même article, pas un voisin
            found = candidate
    arxiv = ((found or {}).get("externalIds") or {}).get("ArXiv") or None
    if arxiv and not ARXIV_ID.match(arxiv):
        arxiv = None
    return {"arxivId": arxiv, "title": ((found or {}).get("title") or guessed)[:300], "author": first_author(text)[:100]}

