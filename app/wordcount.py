"""Nombre de mots d'un article, formules et bibliographie à part : densité d'énoncés de l'avertissement « IA » du graphe
(static/js/graph.js, aiProfile). Même compte que l'étude qui en a tiré les seuils (MAINTENANCE.md) : texte TeX de
l'article (source.tex) quand il est là, sinon sa page HTML."""

import re
from functools import lru_cache

from . import latexml, library

WORD = re.compile(r"[A-Za-zÀ-ÿ]{2,}")
DISPLAY = ("equation", "align", "gather", "multline", "eqnarray", "displaymath", "math", "tikzpicture", "tikzcd", "array",
           "flalign", "alignat")
DISPLAY_ENV = re.compile(r"\\begin\{(%s)\*?\}.*?\\end\{\1\*?\}" % "|".join(DISPLAY), re.S)
INLINE_MATH = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|\$[^$]*\$", re.S)
REFERENCE = re.compile(r"\\(ref|eqref|cite|citep|citet|label|cref|Cref|autoref|input|include|includegraphics|url|href)\*?"
                       r"(\[[^\]]*\])?\{[^}]*\}")
HTML_DROPPED = re.compile(r"<math\b.*?</math>|<(script|style)\b.*?</\1>|<section\b[^>]*\bltx_bibliography\b.*?</section>",
                          re.S | re.I)


def tex_words(text):
    """Mots du corps d'un document TeX : sans commentaires, formules, bibliographie, renvois ni commandes."""
    text = re.sub(r"(?<!\\)%.*", " ", text)
    text = text.split("\\begin{document}", 1)[-1]
    text = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", " ", text, flags=re.S)
    text = DISPLAY_ENV.sub(" ", text)
    text = INLINE_MATH.sub(" ", text)
    text = REFERENCE.sub(" ", text)
    text = re.sub(r"\\[A-Za-z@]+\*?", " ", text)
    return len(WORD.findall(text))


def html_words(html):
    """Mots d'une page d'article : sans formules (MathML), scripts, styles ni bibliographie."""
    text = re.sub(r"<[^>]+>", " ", HTML_DROPPED.sub(" ", html))
    return len(WORD.findall(text))


@lru_cache(maxsize=256)
def _count(path, stamp):
    text = path.read_text(encoding="utf-8", errors="replace")
    return tex_words(text) if path.suffix == ".tex" else html_words(text)


def words(paper_id):
    """Mots de l'article, ou None sans texte (PDF seul)."""
    folder = library.paper_dir(paper_id)
    for path in (folder / "source.tex", folder / "content.html", latexml.html_dir(paper_id) / "content.html"):
        try:
            stat = path.stat()
        except OSError:
            continue
        return _count(path, (stat.st_mtime_ns, stat.st_size))
    return None
