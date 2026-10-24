"""Nettoyage du HTML d'arXiv : on ne garde que le contenu de l'article et des balises sûres.

Le navigateur bloque de toute façon l'exécution de scripts (en-tête Content-Security-Policy,
voir main.py) ; ce filtre est une seconde barrière et retire la décoration de la page arXiv.
"""

import hashlib
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment


def arxiv_host(url):
    """Adresse d'arXiv (arxiv.org et ses sous-domaines : ar5iv.labs.arxiv.org…) ? Les images d'un article ne sont
    téléchargées que de là : une adresse absolue ailleurs (réseau local, autre site) n'est jamais demandée."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower().rstrip(".")
    return parts.scheme in ("http", "https") and (host == "arxiv.org" or host.endswith(".arxiv.org"))

HTML_TAGS = {
    "a", "abbr", "article", "b", "blockquote", "br", "caption", "cite", "code", "col", "colgroup",
    "dd", "del", "div", "dl", "dt", "em", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "i", "img", "ins", "li", "mark", "ol", "p", "pre", "q", "s",
    "section", "small", "span", "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "tt", "u", "ul", "var",
}
MATHML_TAGS = {
    "math", "semantics", "mrow", "mi", "mo", "mn", "ms", "mtext", "mspace", "msub", "msup",
    "msubsup", "mfrac", "msqrt", "mroot", "mover", "munder", "munderover", "mtable", "mtr",
    "mlabeledtr", "mtd", "mstyle", "mpadded", "mphantom", "menclose", "merror", "mmultiscripts",
    "mprescripts", "none", "maction",
}
SVG_TAGS = {
    "svg", "g", "path", "line", "rect", "circle", "ellipse", "polygon", "polyline", "text",
    "tspan", "foreignobject", "defs", "marker", "clippath", "lineargradient", "radialgradient",
    "stop", "pattern",
}
ALLOWED_TAGS = HTML_TAGS | MATHML_TAGS | SVG_TAGS

# Balises supprimées avec tout leur contenu (les autres balises inconnues sont « déballées »).
DROP_TAGS = {
    "script", "style", "iframe", "frame", "object", "embed", "form", "input", "button",
    "textarea", "select", "link", "meta", "noscript", "template", "audio", "video", "canvas",
    "base", "annotation", "annotation-xml", "nav", "use", "image", "animate", "set",
}

ALLOWED_ATTRS = {
    "id", "class", "title", "href", "src", "alt", "colspan", "rowspan", "style", "width",
    "height", "align", "valign", "lang", "dir",
    # MathML
    "display", "mathvariant", "stretchy", "fence", "separator", "lspace", "rspace", "largeop",
    "movablelimits", "accent", "accentunder", "columnalign", "rowalign", "rowspacing",
    "columnspacing", "columnlines", "rowlines", "frame", "linethickness", "displaystyle",
    "scriptlevel", "depth", "voffset", "minsize", "maxsize", "symmetric", "form", "alttext",
    "notation", "mathsize", "mathcolor", "mathbackground", "columnspan", "framespacing",
    # SVG
    "viewbox", "d", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "points",
    "transform", "fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
    "stroke-linejoin", "fill-opacity", "stroke-opacity", "opacity", "font-size", "font-family",
    "font-weight", "font-style", "text-anchor", "dominant-baseline", "overflow", "version",
    "xmlns", "preserveaspectratio", "marker-end", "marker-start", "offset", "stop-color", "patternunits",
    "patterncontentunits", "patterntransform",
}

UNSAFE_STYLE = re.compile(r"url\s*\(|expression|javascript|@import|behavior", re.I)
# position fixe ou collante : un élément de l'article pourrait couvrir l'interface (et ses liens s'ouvrir dehors) ;
# position relative ou absolue (texte remonté, \raisebox de LaTeXML) : gardée, elle reste dans l'article
PINNED = re.compile(r"position\s*:\s*(fixed|sticky)\s*(!important)?\s*;?", re.I)
DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|gif|webp|svg\+xml)[;,]", re.I)

# Éléments de la page LaTeXML qui ne font pas partie de l'article.
NOISE_SELECTORS = [
    ".ltx_page_logo", ".ltx_page_navbar", ".ltx_page_footer", ".ltx_TOC", ".ltx_role_footer",
    ".package-alerts", ".ltx_ERROR",
]


def _safe_href(href):
    if href.startswith("#"):
        return href
    scheme = urlparse(href).scheme.lower()
    return href if scheme in ("http", "https", "mailto") else None


def _repair_patterns(article):
    """Hachures TikZ des pages d'arXiv (LaTeXML de développement) : la définition du motif (<symbol> de sa tuile) ne
    se referme qu'à la fin du dessin. Tout ce qui suit la première hachure se trouve alors dans <defs>, invisible,
    sur arXiv aussi (étiquettes, traits) ; et la surface hachurée est remplie en noir, son groupe à motif restant vide.
    Le dessin est remis à sa place, la tuile copiée dans chaque motif (<use> n'est pas gardé), la surface rendue à son
    motif."""
    tiles = {}
    for pattern in article.find_all("pattern"):
        symbol = pattern.find("symbol", recursive=False)
        if symbol is None:
            continue
        children = [child for child in symbol.children if getattr(child, "name", None)]
        if not children:
            continue
        tile, rest = children[0], children[1:]
        anchor = pattern.find_parent("defs") or pattern
        for element in rest:  # remis après la définition, dans l'ordre
            anchor.insert_after(element.extract())
            anchor = element
        tiles[symbol.get("id")] = tile
        symbol.replace_with(tile.extract())
    definitions = {pattern.get("id"): pattern for pattern in article.find_all("pattern") if pattern.get("id")}
    for pattern in article.find_all("pattern"):
        base = definitions.get((pattern.get("href") or "").lstrip("#"))
        uses = pattern.find_all("use")
        if base is None or not uses:
            continue
        for use in uses:
            tile = tiles.get((use.get("href") or use.get("xlink:href") or "").lstrip("#"))
            if tile is None:
                use.decompose()
                continue
            use.replace_with(BeautifulSoup(str(tile), "html.parser"))
        for key in ("width", "height", "patternunits"):
            if base.get(key) and not pattern.get(key):
                pattern[key] = base[key]
        del pattern["href"]
    # surface hachurée : tracé dans le groupe noir qui précède un groupe à motif resté vide
    for group in article.find_all("g", attrs={"fill": re.compile(r"^url\(#pgfupat")}):
        if group.find(True):
            continue
        before = group.find_previous_sibling()
        paths = before.find_all(True, recursive=False) if before is not None and before.name == "g" else []
        if len(paths) == 1 and paths[0].name == "path":
            group.append(paths[0].extract())
            if not before.find(True):
                before.decompose()
    # motif que rien n'emploie (réemploi d'un motif déjà défini) : la surface est le tracé qui suit sa définition
    used = {match for group in article.find_all("g", attrs={"fill": True})
            for match in re.findall(r"url\(#([^)]+)\)", group["fill"])}
    for pattern in article.find_all("pattern", id=re.compile(r"^pgfupat")):
        defs = pattern.find_parent("defs")
        after = defs.find_next_sibling() if defs is not None else None
        shapes = after.find_all(True, recursive=False) if after is not None and after.name == "g" else []
        if pattern["id"] not in used and len(shapes) == 1 and shapes[0].name == "path":
            after["fill"] = f"url(#{pattern['id']})"


def sanitize_document(html, base_url):
    """Renvoie (soupe nettoyée de l'article, liste des images [(nom local, url distante)])."""
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.ltx_document") or soup.select_one(".ltx_page_content")
    if article is None:
        raise ValueError("Pas de document LaTeXML dans la page")
    article = article.extract()
    fragment = BeautifulSoup("", "html.parser")
    fragment.append(article)

    for comment in fragment.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for selector in NOISE_SELECTORS:
        for element in fragment.select(selector):
            element.decompose()
    _repair_patterns(fragment)

    assets = {}
    for element in list(fragment.find_all(True)):
        if element.decomposed:
            continue
        name = element.name.lower()
        if name in DROP_TAGS:
            element.decompose()
            continue
        if name not in ALLOWED_TAGS:
            element.unwrap()
            continue
        for attr in list(element.attrs):
            value = element.attrs[attr]
            key = attr.lower()
            if key not in ALLOWED_ATTRS:
                del element.attrs[attr]
            elif key == "style" and UNSAFE_STYLE.search(str(value)):
                del element.attrs[attr]
            elif key == "style" and PINNED.search(str(value)):
                element.attrs[attr] = PINNED.sub("", str(value))
            elif key == "href":
                safe = _safe_href(str(value).strip())
                if safe is None:
                    del element.attrs[attr]
                else:
                    element.attrs[attr] = safe
                    if not safe.startswith("#"):
                        element.attrs["target"] = "_blank"
                        element.attrs["rel"] = "noopener noreferrer"
            elif key == "src":
                if name != "img":
                    del element.attrs[attr]
                    continue
                src = str(value).strip()
                if DATA_IMAGE.match(src):
                    continue
                remote = urljoin(base_url, _relative(src, base_url))
                if not arxiv_host(remote):
                    del element.attrs[attr]
                    continue
                local = hashlib.sha1(remote.encode()).hexdigest()[:16] + _extension(remote)
                assets[local] = remote
                element.attrs[attr] = "assets/" + local
    return fragment, sorted(assets.items())


def _relative(src, base_url):
    """Chemin d'image relatif à la page. arXiv écrit parfois « 2609.40232v1/figure.png » pour une page servie sans
    « / » final (…/html/2609.40232v1) : relatif au dossier /html/. L'adresse de base finit toujours par « / » (les
    pages qui écrivent « x1.png ») : sans ce préfixe, les deux formes mènent à la même image."""
    segment = urlparse(base_url).path.rstrip("/").rsplit("/", 1)[-1]
    if segment and src.startswith(segment + "/") and "://" not in src:
        return src[len(segment) + 1:]
    return src


def _extension(url):
    match = re.search(r"\.(png|jpe?g|gif|svg|webp)$", urlparse(url).path, re.I)
    return "." + match.group(1).lower() if match else ""
