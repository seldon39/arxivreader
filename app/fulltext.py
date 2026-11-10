"""Recherche dans le texte et les notes de la bibliothèque. Pas d'index : les fichiers sont parcourus à chaque demande,
leur texte extrait restant en mémoire tant qu'ils ne changent pas."""

import html
import json
import re
import unicodedata
from functools import lru_cache

from . import db, library

MIN_QUERY = 3
MAX_RESULTS = 200
MAX_PER_PAPER = 3      # occurrences dans le texte d'un même article
SNIPPET_SIDE = 100     # caractères de part et d'autre de l'occurrence
CACHED_FILES = 5000    # parcours séquentiel : un cache plus petit que la bibliothèque ne servirait à rien

# Annotations prises en compte : celles de la bibliothèque, y compris sur les versions archivées de ses articles
ANNOTATED_PAPERS = "p.in_library = 1 OR p.archived_of IN (SELECT id FROM papers WHERE in_library = 1)"

# Dollar du texte courant (pas une formule) : affiché ainsi, pour que tout « $ » restant délimite une formule
PLAIN_DOLLAR = "＄"
# Pas de décomposition Unicode, ou variantes qu'on ne tape pas au clavier
_SAME = {"ł": "l", "ø": "o", "đ": "d", "ð": "d", "ı": "i", PLAIN_DOLLAR: "$", "‐": "-", "‑": "-", "‒": "-", "–": "-",
         "—": "-", "−": "-", "‘": "'", "’": "'", "“": '"', "”": '"'}


class _Folding(dict):
    """Table de str.translate remplie à la demande : un caractère donne toujours un seul caractère (les positions
    du texte replié valent pour le texte d'origine)."""

    def __missing__(self, code):
        char = chr(code)
        parts = unicodedata.normalize("NFD", char)
        base = parts[0] if all(unicodedata.combining(mark) for mark in parts[1:]) else char
        folded = next((form for form in (base.casefold(), base.lower()) if len(form) == 1), base)
        self[code] = _SAME.get(folded, folded)
        return self[code]


_FOLDING = _Folding()


def fold(text):
    """Sans accents ni majuscules, de même longueur."""
    return text.translate(_FOLDING)


# Avant la première ancre (titre, auteurs, résumé : sans identifiant) : le lecteur ouvre l'article en haut
TOP = "top"

# Formule (remplacée par son TeX, le MathML n'est que du bruit), contenu sans texte, balise de bloc (sépare des mots),
# autre balise. Le fichier vient de BeautifulSoup : « > » est échappé dans les attributs.
_TOKEN = re.compile(
    r"""<math\b[^>]*?\salttext=(?:"(?P<quoted>[^"]*)"|'(?P<apostrophed>[^']*)')[^>]*>.*?</math>"""
    r"""|<(?P<skipped>math|svg|style|script)\b.*?</(?P=skipped)>"""
    r"""|(?P<block></?(?:p|div|section|article|aside|header|footer|h[1-6]|ul|ol|li|dl|dt|dd|table|thead|tbody|tfoot|"""
    r"""tr|td|th|caption|figure|figcaption|blockquote|pre|br|hr)\b[^>]*>)"""
    r"""|<[^>]*>""", re.S)
_ID = re.compile(r'\sid="([^"]+)"')
_THEOREM = re.compile(r"\bltx_theorem\b")
# Éléments où l'on peut se rendre : paragraphes et énoncés, et ce qui n'est dans aucun paragraphe (titres de section,
# entrées de la bibliographie), que le paragraphe précédent situerait mal
_ANCHOR = re.compile(r"\bltx_(?:para|theorem|proof|abstract|chapter|appendix|section|subsection|subsubsection|paragraph"
                     r"|bibliography|bibitem|equation|equationgroup|figure|table)\b")


def _plain(chunk):
    return unicodedata.normalize("NFC", html.unescape(chunk)).replace("$", PLAIN_DOLLAR)


def _formula(tex):
    # « $ » dans le TeX (\text{si $x>0$}) : la formule resterait coupée en deux à l'affichage
    tex = html.unescape(tex).replace("\\$", PLAIN_DOLLAR).replace("$", "").strip()
    return f" ${tex}$ " if tex else " "


def _pieces(page):
    """(texte, balise de bloc qui le suit, ou "") dans l'ordre de la page."""
    last = 0
    for token in _TOKEN.finditer(page):
        text = _plain(page[last:token.start()]) if token.start() > last else ""
        last = token.end()
        tex = token["quoted"] if token["quoted"] is not None else token["apostrophed"]
        if tex is not None:
            yield text + _formula(tex), ""
        elif token["block"]:
            yield text + " ", token["block"]
        else:
            yield text, ""
    yield _plain(page[last:]), ""


def _stamp(path):
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


@lru_cache(maxsize=CACHED_FILES)
def _extracted(path, stamp):
    """(texte, texte replié) d'un content.html ou d'un text.json ; `stamp` : un fichier modifié est relu."""
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".html":
        text = "".join(piece for piece, _ in _pieces(raw))
    else:  # le texte d'un PDF a ses ligatures (ﬁ) et ses lettres mathématiques : NFKC ; \f sépare les pages
        text = "\f".join(unicodedata.normalize("NFKC", page).replace("$", PLAIN_DOLLAR) for page in json.loads(raw)["pages"])
    return text, fold(text)


@lru_cache(maxsize=CACHED_FILES)
def _read_labels(path, stamp):
    structure = json.loads(path.read_text(encoding="utf-8"))
    return {**{entry["id"]: entry["title"] for entry in structure.get("toc", [])},
            **{node["id"]: node["label"] for node in structure.get("nodes", [])}}


def _labels(paper_id):
    """{identifiant d'énoncé ou de section: son nom}."""
    path = library.paper_dir(paper_id) / "structure.json"
    stamp = _stamp(path)
    return {} if stamp is None else _read_labels(path, stamp)


def _enclosing(element_id, labels):
    """Nom de l'énoncé ou de la section qui contient l'élément (LaTeXML : « S1.Thm2.p1 » est dans « S1.Thm2 »)."""
    while element_id:
        if element_id in labels:
            return labels[element_id]
        element_id = element_id.rpartition(".")[0]
    return ""


def _pattern(query):
    words = fold(unicodedata.normalize("NFC", query)).split()
    if len(" ".join(words)) < MIN_QUERY:
        return None
    return re.compile(r"\s+".join(map(re.escape, words)))


def _snippet(text, start, end):
    """Environ 200 caractères autour de [start, end[, sans couper un mot ni une formule : une formule au bord est
    gardée entière si elle contient l'occurrence, retirée sinon. Dans `text`, les « $ » vont par paires."""
    left, right = max(0, start - SNIPPET_SIDE), min(len(text), end + SNIPPET_SIDE)
    if left:
        left = text.find(" ", left, start) + 1 or left
    if right < len(text):
        space = text.rfind(" ", end, right)
        right = space if space > 0 else right
    if text.count("$", 0, left) % 2:
        closing = text.find("$", left)
        left = text.rfind("$", 0, left) if closing >= start else closing + 1
    if text.count("$", 0, right) % 2:
        opening = text.rfind("$", 0, right)
        right = text.find("$", right) + 1 if opening < end else opening
    return ("…" if left else "") + " ".join(text[left:right].split()) + ("…" if right < len(text) else "")


def _hit(paper, kind, label, text, match, target):
    return {"paperId": paper["id"], "title": paper["title"], "kind": kind, "label": label,
            "snippet": _snippet(text, match.start(), match.end()), "target": target, "version": paper["version"],
            "archived": bool(paper["archived_of"])}


def _annotation_text(value):
    """Note ou passage surligné : sans le gras du Markdown, formules ramenées à $…$ et « $ » par paires, comme dans le
    texte des articles."""
    text = unicodedata.normalize("NFC", value).replace("**", "").replace("\\$", PLAIN_DOLLAR).replace("$$", "$")
    text = re.sub(r"(?<!\\)\\[()\[\]]", "$", text)
    return text.replace("$", PLAIN_DOLLAR) if text.count("$") % 2 else text


def _annotation_target(target):
    target = target or {}
    return target.get("id") or (target.get("start") or {}).get("id")


def _annotation_hits(rows, pattern):
    """Une ligne par annotation (sa note d'abord, sinon son passage), les notes avant les surlignages. Un marque-page
    n'a qu'un nom, dans `quote` : c'est un texte de l'utilisateur, compté comme note."""
    hits = []
    for row in rows:
        fields = (("note", "note"), ("quote", "highlight" if row["kind"] == "highlight" else "note"))
        for field, kind in fields:
            text = _annotation_text(row[field] or "")
            match = pattern.search(fold(text))
            if match:
                target = _annotation_target(json.loads(row["target"] or "null"))
                hits.append(_hit(row, kind, _enclosing(target, _labels(row["id"])), text, match, target))
                break
    hits.sort(key=lambda hit: hit["kind"] != "note")
    return hits


def _locate(page, matches):
    """Occurrences (dans l'ordre) avec leur place [(occurrence, ancre, énoncé)] : une seule par paragraphe ou énoncé,
    au plus MAX_PER_PAPER. La page n'est parcourue que jusqu'à la dernière retenue."""
    found, last, divs, position, anchor = [], None, [], 0, None
    match = next(matches, None)
    for text, tag in _pieces(page):
        position += len(text)
        while match and match.start() < position and len(found) < MAX_PER_PAPER:
            statement = next(filter(None, reversed(divs)), None)
            place = statement or anchor
            if place is None or place != last:
                found.append((match, anchor, statement))
                last = place
            match = next(matches, None)
        if match is None or len(found) == MAX_PER_PAPER:
            break
        if tag.startswith("</div"):
            del divs[-1:]
        elif tag:
            identifier = _ID.search(tag)
            if identifier and _ANCHOR.search(tag):
                anchor = identifier[1]
            if tag.startswith("<div"):  # LaTeXML : un énoncé est toujours un <div>
                divs.append(identifier[1] if identifier and _THEOREM.search(tag) else None)
    return found


def _html_hits(paper, path, stamp, pattern):
    text, folded = _extracted(path, stamp)
    if not pattern.search(folded):
        return []
    labels = _labels(paper["id"])
    hits = []
    for match, anchor, statement in _locate(path.read_text(encoding="utf-8"), pattern.finditer(folded)):
        if statement:
            hits.append(_hit(paper, "statement", labels.get(statement, ""), text, match, statement))
        else:
            hits.append(_hit(paper, "text", _enclosing(anchor, labels), text, match, anchor or TOP))
    return hits


def _pdf_hits(paper, path, stamp, pattern):
    text, folded = _extracted(path, stamp)
    hits, page, counted, last = [], 1, 0, None
    for match in pattern.finditer(folded):
        page += text.count("\f", counted, match.start())
        counted = match.start()
        if page != last:
            hits.append(_hit(paper, "text", f"p. {page}", text, match, None))
            last = page
            if len(hits) == MAX_PER_PAPER:
                break
    return hits


def _text_hits(paper, pattern):
    folder = library.paper_dir(paper["id"])  # identifiant lu dans la base
    html_file = folder / "content.html"
    stamp = _stamp(html_file)
    if stamp is not None:
        return _html_hits(paper, html_file, stamp, pattern)
    pdf_text = folder / "text.json"
    stamp = _stamp(pdf_text)
    return [] if stamp is None else _pdf_hits(paper, pdf_text, stamp, pattern)


def search(query):
    """{"results": [...], "truncated": bool} : notes et surlignages, puis texte des articles, les plus récemment
    ouverts d'abord. `truncated` : plus de MAX_RESULTS résultats."""
    pattern = _pattern(query)
    if pattern is None:
        return {"results": [], "truncated": False}
    recent = "COALESCE(p.opened_at, p.added_at) DESC"
    with db.connect() as connection:
        papers = connection.execute(f"SELECT p.id, p.title, p.version, p.archived_of FROM papers p WHERE p.in_library = 1 ORDER BY {recent}").fetchall()
        annotations = connection.execute(
            "SELECT p.id, p.title, p.version, p.archived_of, a.kind, a.target, a.quote, a.note FROM annotations a JOIN papers p ON p.id = a.paper_id "
            f"WHERE ({ANNOTATED_PAPERS}) AND a.kind != 'proof' ORDER BY {recent}, a.id").fetchall()
    results = _annotation_hits(annotations, pattern)
    for paper in papers:
        if len(results) > MAX_RESULTS:
            break
        results += _text_hits(paper, pattern)
    return {"results": results[:MAX_RESULTS], "truncated": len(results) > MAX_RESULTS}
