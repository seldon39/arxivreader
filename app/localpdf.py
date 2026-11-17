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
PAINTS = FILLS | {b"S", b"s", b"n"}
SHOWS = {b"Tj", b"TJ", b"'", b'"'}
MIN_VISIBLE_PT = 1  # corps de texte en deçà : invisible à l'écran comme à l'impression
DEVICE_SPACES = {"/DeviceGray", "/DeviceRGB", "/DeviceCMYK", "/CalGray", "/CalRGB", "/ICCBased", "/G", "/RGB", "/CMYK"}
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


def _luminance(values, known=True):
    """Luminance d'une couleur de remplissage (gris, RVB, CMJN selon le nombre de composantes), None si on ne sait pas
    la lire (espace de couleur spécial, motif)."""
    if not known or not values or len(values) not in (1, 3, 4):
        return None
    if len(values) == 1:
        return luminance(values * 3)
    if len(values) == 3:
        return luminance(values)
    c, m, y, k = values
    return luminance([(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)])


def _known_space(name, resources):
    """Espace de couleur dont les composantes se lisent comme gris / RVB / CMJN."""
    if name in DEVICE_SPACES:
        return True
    try:
        space = resources["/ColorSpace"][name].get_object()
        return str(space[0] if isinstance(space, list) else space) in DEVICE_SPACES
    except Exception:
        return False


def _raw(value):
    """Octets d'une chaîne affichée (Tj) ou de celles d'un tableau (TJ)."""
    if isinstance(value, list):
        return b"".join(_raw(item) for item in value if isinstance(item, (str, bytes)))
    if hasattr(value, "get_original_bytes"):
        return value.get_original_bytes()
    return bytes(value) if isinstance(value, bytes) else str(value).encode("latin-1", "replace")


def _has_images(page, contents):
    def walk(resources, depth):
        try:
            xobjects = resources.get("/XObject") or {}
            for name in xobjects:
                xobject = xobjects[name].get_object()
                if xobject.get("/Subtype") == "/Image" or depth < 3 and xobject.get("/Subtype") == "/Form" and walk(
                        xobject.get("/Resources") or {}, depth + 1):
                    return True
        except Exception:
            pass
        return False
    try:
        return walk(page.get("/Resources") or {}, 0) or any(op == b"INLINE IMAGE" for _, op in contents.operations)
    except Exception:
        return False


class _Visible:
    """Parcours d'un flux de contenu qui retire les affichages de texte invisibles (`ops`) et les compte
    (`passages` : suites de texte invisible avec des lettres). État graphique suivi : q/Q, cm, couleurs de remplissage
    et de trait, Tf, Tz, Tr, TL, matrices de texte ; fonds foncés (ou d'image) déjà peints, avec leur place sur la page.
    Formulaires (Do) parcourus de même, une fois chacun."""

    def __init__(self, reader, scan):
        self.reader, self.scan = reader, scan
        self.backgrounds, self.passages, self.running, self.forms = [], 0, False, set()

    def run(self, operations, resources, state, depth=0):
        """(opérations gardées, flux changé) ; `state` (dict) : état graphique au départ, mis à jour."""
        kept, stack, path, changed = [], [], [], False
        tm = tlm = IDENTITY
        for args, operator in operations:
            try:
                self._step(args, operator, resources, state, stack, path, depth)
                if operator == b"BT":
                    tm = tlm = IDENTITY
                elif operator == b"Tm" and _numbers(args[:6]) and len(args) >= 6:
                    tm = tlm = _numbers(args[:6])
                elif operator in (b"Td", b"TD") and _numbers(args[:2]) and len(args) >= 2:
                    tx, ty = _numbers(args[:2])
                    state["leading"] = -ty if operator == b"TD" else state["leading"]
                    tm = tlm = _mult([1, 0, 0, 1, tx, ty], tlm)
                elif operator in (b"T*", b"'", b'"'):
                    tm = tlm = _mult([1, 0, 0, 1, 0, -state["leading"]], tlm)
                if operator in SHOWS and self._hidden(args, operator, resources, state, tm):
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

    def _step(self, args, operator, resources, state, stack, path, depth):
        if operator == b"q":
            stack.append(dict(state))
        elif operator == b"Q" and stack:
            state.clear()
            state.update(stack.pop())
        elif operator == b"cm" and len(args) >= 6:
            state["ctm"] = _mult(_numbers(args[:6]), state["ctm"])
        elif operator in (b"g", b"rg", b"k", b"sc", b"scn"):
            state["fill"] = _luminance(_numbers(args), state["fillKnown"] if operator in (b"sc", b"scn") else True)
        elif operator in (b"G", b"RG", b"K", b"SC", b"SCN"):
            state["stroke"] = _luminance(_numbers(args), state["strokeKnown"] if operator in (b"SC", b"SCN") else True)
        elif operator in (b"cs", b"CS") and args:
            known = _known_space(str(args[0]), resources)
            side = "fill" if operator == b"cs" else "stroke"
            state[side + "Known"], state[side] = known, 0.0 if known else None
        elif operator == b"Tf" and len(args) >= 2:
            state["size"] = abs(float(args[1]))
            try:
                state["cid"] = resources["/Font"][args[0]].get_object().get("/Subtype") == "/Type0"
            except Exception:
                state["cid"] = False
        elif operator == b"Tz" and args:
            state["scale"] = float(args[0]) / 100
        elif operator == b"Tr" and args:
            state["render"] = int(args[0])
        elif operator == b"TL" and args:
            state["leading"] = float(args[0])
        elif operator in (b"m", b"l", b"c", b"v", b"y") and _numbers(args):
            numbers = _numbers(args)
            path += [_point(state["ctm"], numbers[i], numbers[i + 1]) for i in range(0, len(numbers) - 1, 2)]
        elif operator == b"re" and len(args) >= 4:
            x, y, w, h = _numbers(args[:4])
            path += [_point(state["ctm"], x + dx, y + dy) for dx in (0, w) for dy in (0, h)]
        elif operator in PAINTS:
            if operator in FILLS and path and (state["fill"] is None or state["fill"] < DARK):
                self._background(path)
            path.clear()
        elif operator == b"INLINE IMAGE":
            self._background([_point(state["ctm"], x, y) for x in (0, 1) for y in (0, 1)])
        elif operator == b"Do" and args:
            self._draw(args[0], resources, state, depth)

    def _background(self, points):
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        self.backgrounds.append((min(xs), min(ys), max(xs), max(ys)))

    def _draw(self, name, resources, state, depth):
        xobject = resources["/XObject"][name]
        target = xobject.get_object()
        if target.get("/Subtype") == "/Image":
            self._background([_point(state["ctm"], x, y) for x in (0, 1) for y in (0, 1)])
            return
        key = getattr(xobject, "idnum", None) or id(target)
        if target.get("/Subtype") != "/Form" or depth >= 5 or key in self.forms:
            return
        self.forms.add(key)
        from pypdf.generic import ContentStream
        content = ContentStream(target, self.reader)
        inner = dict(state, ctm=_mult(_numbers(target.get("/Matrix", IDENTITY)) or IDENTITY, state["ctm"]))
        kept, changed = self.run(content.operations, target.get("/Resources") or resources, inner, depth + 1)
        if changed:
            content.operations = kept
            try:
                target.set_data(content.get_data())
            except Exception:  # filtre autre que Flate : copie décodée à la place du formulaire, dans ces ressources
                from pypdf.generic import DecodedStreamObject, NameObject
                copy = DecodedStreamObject()
                copy.update({k: v for k, v in target.items() if k not in ("/Filter", "/DecodeParms", "/Length")})
                copy.set_data(content.get_data())
                resources["/XObject"][NameObject(name)] = copy

    def _hidden(self, args, operator, resources, state, tm):
        raw = _raw(args[-1] if args else b"")
        blank = not raw.strip(b"\x00 ") if state.get("cid") else not raw.strip()
        letters = bool(raw.strip(b"\x00")) if state.get("cid") else bool(re.search(rb"[A-Za-z\x80-\xff]", raw))
        m = _mult(tm, state["ctm"])
        size = state.get("size")
        tiny = size is not None and (size * math.hypot(m[2], m[3]) < MIN_VISIBLE_PT
                                     or size * state["scale"] * math.hypot(m[0], m[1]) < MIN_VISIBLE_PT)
        render = state["render"] % 8
        color = state["stroke"] if render in (1, 5) else state["fill"]
        white = render in (0, 1, 2, 4, 5, 6) and color is not None and color >= WHITE and not self._on_dark(m, size or 10)
        hidden = not blank and (tiny or white or render in (3, 7) and not self.scan)
        if hidden and letters:
            self.passages += not self.running
            self.running = True
        elif not blank and not hidden:
            self.running = False
        return hidden

    def _on_dark(self, m, size):
        points = [_point(m, 0, 0), _point(m, 0, size * 0.3)]
        return any(x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1 for x, y in points for x0, y0, x1, y1 in self.backgrounds)


def _page_text(page, hidden=None):
    """Texte de la page, sans le texte invisible, comme dans la version HTML (structure.mark_hidden) : il peut porter
    une injection d'invite et n'est jamais transmis à l'IA. Invisible : blanc ou presque (luminance ≥ WHITE) hors d'un
    fond foncé ou d'une image peints dessous, corps de moins de MIN_VISIBLE_PT, mode de rendu invisible (Tr 3) sauf
    sur une page avec image (couche de texte d'un scan reconnu : le texte de la page). `hidden` (liste) : y ajoute le
    nombre de passages invisibles avec des lettres."""
    from pypdf.generic import ContentStream, NameObject
    passages = 0
    try:
        contents = page.get_contents()
        if contents is not None:
            walk = _Visible(page.pdf, _has_images(page, contents))
            state = {"ctm": IDENTITY, "fill": 0.0, "stroke": 0.0, "fillKnown": True, "strokeKnown": True, "size": None,
                     "scale": 1.0, "render": 0, "leading": 0.0, "cid": False}
            kept, changed = walk.run(contents.operations, page.get("/Resources") or {}, state)
            passages = walk.passages
            if changed:
                contents.operations = kept
                page[NameObject("/Contents")] = contents
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


TEXT_VERSION = 2  # 2 : texte invisible retiré ; un text.json plus ancien est refait à sa première lecture par l'IA


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
    """Au démarrage : titre et auteurs des PDF importés avant la dernière amélioration du repérage, relus du PDF
    (sauf ceux corrigés à la main). Résumé repris seulement s'il manquait."""
    with db.connect() as connection:
        rows = connection.execute("SELECT id FROM papers WHERE source = ?", (SOURCE,)).fetchall()
    for row in rows:
        folder = paper_dir(row["id"])
        try:
            stored = json.loads((folder / "text.json").read_text(encoding="utf-8"))
            if stored.get("metaVersion", 1) >= META_VERSION or (folder / "meta-edited").exists():
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

