"""Nettoyage du HTML d'arXiv : on ne garde que le contenu de l'article et des balises sûres.

Le navigateur bloque de toute façon l'exécution de scripts (en-tête Content-Security-Policy,
voir main.py) ; ce filtre est une seconde barrière et retire la décoration de la page arXiv.
"""

import hashlib
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment

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
    "stop",
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
    "xmlns", "preserveaspectratio", "marker-end", "marker-start", "offset", "stop-color",
}

UNSAFE_STYLE = re.compile(r"url\s*\(|expression|javascript|@import|behavior", re.I)
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
                if urlparse(remote).scheme not in ("http", "https"):
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
