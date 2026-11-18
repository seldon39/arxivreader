"""Structure d'un article à partir de sa source TeX, pour les articles sans version HTML.

Mêmes informations que structure.py (sommaire, énoncés, preuves, dépendances, bibliographie), lues
dans la source : environnements déclarés par \\newtheorem (numérotation simulée : compteurs partagés,
remis à zéro par section, annexes), preuves rattachées comme dans la version HTML, renvois \\ref,
\\eqref, \\cref… Les pages de chaque énoncé dans le PDF viennent des ancres de hyperref (« thm.2.3 »),
sinon d'une recherche du texte « Theorem 2.3 » dans le PDF.

La source n'est ni extraite sur le disque ni compilée.
"""

import bisect
import colorsys
import functools
import io
import itertools
import posixpath
import re
import unicodedata

from .arxiv import extract_ids
from .structure import (defines_macro, ANNOUNCES_PROOF_BELOW, IMPLICIT_REF, JUSTIFICATION, JUSTIFIES_NEXT, JUSTIFIES_PREVIOUS, NEEDS_PROOF,
                        PROOF_HEADING, PROVING, STRUCTURE_VERSION, assign_chapters, classify, luminance, DARK, WHITE)
from .texsource import _expand, _read_archive, choose_main

SECTIONS = {"part": 0, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3}
EQUATION_ENVS = {"equation", "align", "gather", "multline", "eqnarray", "flalign", "alignat", "split", "dmath",
                 "subequations"}
# listes d'un énoncé (« \begin{enumerate} \item\label{One} … ») : leurs \label désignent un point, pas l'énoncé
ITEM_LISTS = re.compile(r"\\(begin|end)\s*\{(?:enumerate|itemize|description|compactenum|compactitem|compactdesc|"
                        r"asparaenum|asparaitem|inparaenum|inparaitem|list|trivlist)\*?\}")
REF_COMMANDS = {"ref", "eqref", "cref", "Cref", "autoref", "vref", "Vref", "cpageref", "nameref", "Autoref", "zcref",
                "thmref", "labelcref"}
CITE_COMMANDS = {"cite", "citep", "citet", "Cite", "citealp", "citealt", "parencite", "textcite", "autocite"}
TOKEN = re.compile(r"\\([A-Za-z]+)(\*?)")
MAX_TEX = 20_000  # caractères gardés par énoncé ou preuve (aperçus, IA)


# ---------- Lecture d'arguments ----------

def _group(text, pos, opening):
    """Groupe {…} ou […] commençant à `pos` (espaces sautés) : (contenu, fin) ou None."""
    while pos < len(text) and text[pos] in " \t\n":
        pos += 1
    if pos >= len(text) or text[pos] != opening:
        return None
    closing = "}" if opening == "{" else "]"
    depth = 0
    index, stop = pos, min(len(text), pos + 20000)
    while index < stop:
        char = text[index]
        if char == "\\":
            index += 2  # paire « \x » sautée d'un bloc : « \{ » est protégée, mais dans « \\} » seule la barre doublée l'est
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[pos + 1:index], index + 1
        index += 1
    return None


def _args(text, pos, pattern):
    """Arguments selon un motif (« {[{[ » : obligatoire, optionnel…) ; None pour un optionnel absent, et pour tous ceux qui
    suivent un obligatoire absent (« \\section and \\subsection » dans une phrase : autant de valeurs que le motif)."""
    values = []
    for kind in pattern:
        found = _group(text, pos, "{" if kind == "{" else "[")
        if found is None:
            if kind == "{":
                return values + [None] * (len(pattern) - len(values)), pos
            values.append(None)
            continue
        values.append(found[0])
        pos = found[1]
    return values, pos


def plain(text):
    """Texte lisible d'un fragment TeX (titres, noms d'énoncés) ; formules gardées entre $…$."""
    text = re.sub(r"\\(?:label|index)\{[^}]*\}", "", text or "")
    text = re.sub(r"\\(?:emph|textbf|textit|textrm|textsf|mathrm|text|textsc|mbox|uppercase)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:ref|eqref|cref|Cref|autoref)\*?\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\cite[a-z]*\*?(?:\[[^\]]*\])?\{([^}]*)\}", r"[\1]", text)
    text = re.sub(r"\\(?:[,;:!> ]|quad|qquad|newline|\\)", " ", text)
    text = re.sub(r"\\(?:em|bf|it|rm|sc|sf|tt|normalfont|upshape|bfseries|itshape)\b", "", text)
    text = re.sub(r"\\([&%$#_{}])", r"\1", text)
    text = text.replace("~", " ").replace("``", "“").replace("''", "”").replace("---", "—").replace("--", "–")
    text = re.sub(r"\\textendash\b\s*(?:\{\})?", "–", text)
    text = re.sub(r"\\textemdash\b\s*(?:\{\})?", "—", text)
    # accents courants : \'e → é…
    accents = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303", "c": "\u0327", "v": "\u030c"}
    text = re.sub(r"\\(['`^\"~cv])\s*\{?([A-Za-z])\}?", lambda m: m.group(2) + accents[m.group(1)], text)
    outside = re.split(r"(\$[^$]*\$)", text)
    text = "".join(part if part.startswith("$") else re.sub(r"\\[A-Za-z]+\*?|[{}]", "", part) for part in outside)
    import unicodedata
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


# ---------- Déclarations des énoncés ----------

def declarations(text):
    """{environnement: {name, counter, within, numbered}} et compteurs remis à zéro par section."""
    envs, aliases, within = {}, {}, {}
    for match in re.finditer(r"\\newaliascnt\s*\{(\w+)\}\s*\{(\w+)\}", text):
        aliases[match.group(1)] = match.group(2)
    for match in re.finditer(r"\\(newtheorem|spnewtheorem)(\*?)", text):
        values, _ = _args(text, match.end(), "{[{[")
        values += [None] * (4 - len(values))
        env, shared, name, inside = values
        if not env or name is None:
            continue
        counter = aliases.get(shared, shared) if shared else env
        envs[env.strip()] = {"name": plain(name), "counter": counter.strip(), "numbered": not match.group(2)}
        if inside and not shared:
            within[env.strip()] = inside.strip()
    for match in re.finditer(r"\\declaretheorem\s*(\[[^\]]*\])?\s*\{(\w+)\}\s*(\[[^\]]*\])?", text):
        options = (match.group(1) or match.group(3) or "")[1:-1] if (match.group(1) or match.group(3)) else ""
        env = match.group(2)
        keys = dict(re.findall(r"(\w+)\s*=\s*\{?([^,}\]]+)\}?", options))
        counter = keys.get("sibling") or keys.get("sharenumber") or env
        envs[env] = {"name": plain(keys.get("name", env.capitalize())), "counter": counter,
                     "numbered": keys.get("numbered", "yes") != "no"}
        if keys.get("numberwithin") or keys.get("parent"):
            within[env] = keys.get("numberwithin") or keys.get("parent")
    for match in re.finditer(r"\\numberwithin\s*\{(\w+)\}\s*\{(\w+)\}", text):
        within[match.group(1)] = match.group(2)
    # \renewcommand{\thetheorem}{\Alph{theorem}} : « Theorem A » ; {\thesection.\arabic{lemma}} : par section
    formats = {}
    for match in re.finditer(r"\\renewcommand\*?\s*\{?\\the(\w+)\}?", text):
        group = _group(text, match.end(), "{")
        if group is None:
            continue
        value, counter = group[0], match.group(1)
        style = re.search(r"\\(Alph|alph|Roman|roman|arabic)\s*\{", value)
        if style:
            formats[counter] = style.group(1)
        if "\\thesubsection" in value:
            within[counter] = "subsection"
        elif "\\thesection" in value:
            within[counter] = "section"
        elif style and counter in within and "\\the" not in value:
            within.pop(counter)  # numéro seul (« A ») : plus de préfixe de section
    return envs, within, formats


def _roman(n):
    values = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
              (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for value, letters in values:
        while n >= value:
            out, n = out + letters, n - value
    return out


def _format(n, style):
    if style == "Alph":
        return _letter(n)
    if style == "alph":
        return _letter(n).lower()
    if style == "Roman":
        return _roman(n).upper()
    if style == "roman":
        return _roman(n)
    return str(n)


PROOF_ENVS = {"proof", "pf", "Proof", "demo", "dem", "preuve", "prf", "myproof", "proofof", "pff", "Pf"}
PROOF_HEADER = re.compile(r"(?:\\noindent\s*)?(?:\\(?:textbf|textit|emph|textsc|underline)\s*\{|\{\s*\\(?:bf|it|em|sc|bfseries|itshape)\s+)"
                          r"\s*(?:Proof|Preuve|Démonstration|D\\'emonstration)\b")
PROOF_END = re.compile(r"\\qed\b|\\qedhere\b|\\(?:Box|square|blacksquare|qedsymbol|QED)\b|\\end\{document\}"
                       r"|\\(?:sub)*section\b|\\begin\s*\{proof\}")


class Shifts:
    """Positions d'un texte réécrit (morceaux recopiés ou remplacés) ramenées au texte d'avant : `start` pour un début,
    `end` pour une fin (exclue). Une position dans un morceau remplacé va au début (ou à la fin) du texte qu'il remplace."""

    def __init__(self, pieces, size, source_size):
        self.pieces = pieces  # (début dans le résultat, longueur, début dans le texte d'avant, longueur, recopié)
        self.starts = [p[0] for p in pieces]
        self.size, self.source_size = size, source_size

    def _piece(self, pos):
        import bisect
        return self.pieces[bisect.bisect_right(self.starts, pos) - 1]

    def start(self, pos):
        if not self.pieces or pos >= self.size:
            return self.source_size
        out, _, source, _, copied = self._piece(max(0, pos))
        return source + (pos - out if copied else 0)

    def end(self, pos):
        if not self.pieces or pos <= 0:
            return 0
        out, _, source, length, copied = self._piece(min(pos, self.size) - 1)
        return source + (min(pos, self.size) - out if copied else length)


def _edit(text, edits, shifts=None):
    """`text` où chaque (début, fin, remplacement) de `edits` (dans l'ordre, sans chevauchement) est remplacé ;
    `shifts` (liste) : y ajoute la correspondance des positions (Shifts)."""
    out, pieces, pos, size = [], [], 0, 0
    for start, end, replacement in edits:
        if start > pos:
            out.append(text[pos:start])
            pieces.append((size, start - pos, pos, start - pos, True))
            size += start - pos
        out.append(replacement)
        pieces.append((size, len(replacement), start, end - start, False))
        size += len(replacement)
        pos = end
    if pos < len(text):
        out.append(text[pos:])
        pieces.append((size, len(text) - pos, pos, len(text) - pos, True))
        size += len(text) - pos
    if shifts is not None:
        shifts.append(Shifts(pieces, size, len(text)))
    return "".join(out)


def _sub(pattern, template, text, shifts=None, flags=0):
    """re.sub(pattern, template, text) (même résultat), avec la correspondance des positions."""
    return _edit(text, [(m.start(), m.end(), m.expand(template)) for m in re.finditer(pattern, text, flags)], shifts)


def normalize_proofs(body, envs, shifts=None):
    """Preuves écrites autrement (\\proof…\\endproof, environnement « pf », titre « \\textbf{Proof of …} »)
    réécrites en \\begin{proof}[…]…\\end{proof}, la forme que reconnaît l'analyse. `shifts` (liste) : y ajoute la
    correspondance des positions de chaque réécriture (Shifts, dans l'ordre)."""
    body = _sub(r"\\proof\b(?![A-Za-z])", r"\\begin{proof}", body, shifts)
    body = _sub(r"\\endproof\b(?![A-Za-z])", r"\\end{proof}", body, shifts)
    names = "|".join(re.escape(n) for n in PROOF_ENVS - {"proof"} if n not in envs)
    body = _sub(rf"\\(begin|end)\s*\{{({names})\*?\}}", r"\\\1{proof}", body, shifts)
    starts = (rf"\\begin\s*\{{(?:{'|'.join(re.escape(e) for e in envs)})\*?\}}" if envs else None)
    edits, pos = [], 0
    for match in PROOF_HEADER.finditer(body):
        if match.start() < pos:
            continue
        # titre : jusqu'à l'accolade fermante du \textbf{…} (ou du {\bf …})
        opening = body.rfind("{", match.start(), match.end())
        closing = _group(body, opening, "{")
        if closing is None:
            continue
        title = closing[0]
        title = re.sub(r"^\s*\\(?:bf|it|em|sc|bfseries|itshape)\s+", "", title)
        rest = closing[1]
        end = PROOF_END.search(body, rest)
        stop = end.end() if end and end.group(0).startswith(("\\qed", "\\Box", "\\square", "\\blacksquare", "\\QED")) else (end.start() if end else len(body))
        if starts:
            next_statement = re.compile(starts).search(body, rest)
            if next_statement and next_statement.start() < stop:
                stop = next_statement.start()
        # « \begin{proof}[titre] » à la place du titre, le texte gardé, « \end{proof} » ajouté après
        edits += [(match.start(), rest, f"\\begin{{proof}}[{title}]"), (stop, stop, "\\end{proof}")]
        pos = stop
    return _edit(body, edits, shifts)


# ---------- Analyse ----------

def _letter(n):
    return chr(ord("A") + n - 1) if 1 <= n <= 26 else str(n)


# Texte imprimé mais invisible (blanc, minuscule) : retiré comme dans la version HTML (structure.mark_hidden), signalé
# au lecteur, jamais transmis à l'IA (il peut porter une injection d'invite). Heuristique : les détours de TeX sont
# sans fin ; on couvre les façons simples, sans jamais retirer du texte qu'on lit sur la page (fond foncé).
_XCOLORS = {"white": (1, 1, 1), "black": (0, 0, 0), "red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1),
            "cyan": (0, 1, 1), "magenta": (1, 0, 1), "yellow": (1, 1, 0), "gray": (.5, .5, .5), "lightgray": (.75, .75, .75),
            "darkgray": (.25, .25, .25), "brown": (.75, .5, .25), "lime": (.75, 1, 0), "olive": (.5, .5, 0),
            "orange": (1, .5, 0), "pink": (1, .75, .75), "purple": (.75, 0, .25), "teal": (0, .5, .5), "violet": (.5, 0, .5)}
_NO_FILL = {"", "none", "transparent"}
_UNITS = {"pt": 1, "bp": 1, "": 1, "mm": 2.85, "cm": 28.5, "in": 72.3, "ex": 4.3, "em": 10, "sp": 1 / 65536}
_SCAN = 200000            # portée d'une déclaration cherchée jusque-là au plus
_SCAN_BUDGET = 3_000_000  # caractères parcourus en tout pour les portées d'un texte ; au-delà, déclarations ouvertes
_COMMENT = re.compile(r"(?<!\\)%[^\n]*")
# jamais exécuté : commentaire, \verb, \lstinline, environnements verbatim (masqués avant de chercher le texte caché)
_UNEXECUTED = re.compile(r"(?<!\\)%[^\n]*|\\verb\*?([^A-Za-z\s*])[^\n]*?\1"
                         r"|\\lstinline\s*(?:\[[^\]\n]*\])?\s*(?:\{[^{}\n]*\}|([^A-Za-z\s{\[])[^\n]*?\2)"
                         r"|\\begin\s*\{\s*(verbatim\*?|Verbatim\*?|BVerbatim|lstlisting|minted|alltt)\s*\}.*?\\end\s*\{\s*\3\s*\}", re.S)
# \ifnum entre deux nombres écrits : le second se termine par une espace, \relax ou un caractère (une macro pourrait
# le prolonger : \ifnum9<1\@gobbleminus#1 n'est pas une constante) ; neuf chiffres au plus (TeX : 2^31 - 1)
_CONDITIONAL = re.compile(r"\\iffalse\b|\\ifnum\s*(-?\d{1,9})\s*([<=>])\s*(-?\d{1,9})(?=\s|\\relax(?![A-Za-z@])|[^\d.\\])")
# \if… nommé sans être exécuté : \newif\ifx, \let\ifx=\iffalse (les deux), \def\ifx…
_ASSIGNED = re.compile(r"\\(?:newif|let|[gex]?def|expandafter)\s*$|\\let\s*\\[A-Za-z@]+\s*=?\s*$")
_COLOR_USE = re.compile(r"\\(textcolor|mathcolor|color)\s*(?:\[\s*([A-Za-z]+)\s*\])?\s*\{([^{}]*)\}")
_COLOR_VALUE = re.compile(r"(?:background|rule|fill)color\s*=\s*$")
_COLOR_DEFINITION = re.compile(r"\\(?:definecolor|providecolor)\s*(?:\[[^\]]*\])?\s*\{\s*([^{}]+?)\s*\}\s*\{\s*([^{}]+?)\s*\}"
                               r"\s*\{([^{}]*)\}|\\colorlet\s*(?:\[[^\]]*\])?\s*\{\s*([^{}]+?)\s*\}\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
# \fontsize ne prend effet qu'au choix d'une police : \selectfont, ou tout changement de famille, de série, de forme
_FONTSIZE = re.compile(r"\\fontsize\s*\{([^{}]*)\}\s*\{[^{}]*\}(?:\s*\\(?!(?:selectfont|usefont)\b)[A-Za-z]+(?:\s*\{[^{}]*\}){0,4}){0,8}"
                       r"\s*(?:\\(?:selectfont|rmfamily|sffamily|ttfamily|normalfont|bfseries|mdseries|itshape|slshape|scshape|upshape)\b"
                       r"|\\usefont\b(?:\s*\{[^{}]*\}){4})")
_SIZES = "normalsize|tiny|scriptsize|footnotesize|small|large|Large|LARGE|huge|Huge|fontsize"
_ENV = re.compile(r"\\(begin|end)\s*\{([^{}]*)\}")
_TABLES = re.compile(r"tabular|tabu|array|longtable|tblr|NiceTabular")
_ALIGNMENTS = re.compile(r"tabular|tabu|array|longtable|tblr|NiceTabular|align|gather|matrix|cases|eqnarray|split")  # cases : & et \\
# remplissages : clé d'options [fill=…] (tikz, tcolorbox, mdframed…) ; cases de tableau ; formes tikz
_FILL_KEY = re.compile(r"[\[,{]\s*(?:fill|colback|backgroundcolor|bg|background\s+color)\s*=\s*(\{[^{}]*\}|[\w!.\-]+)")
_OPTIONS_HEAD = re.compile(r"(?:\\[A-Za-z@]+\*?|\\begin\s*\{[^{}]*\}|[)\]]|\bnode|\bcoordinate)\s*$")  # […] d'une commande
_COLORBOX = re.compile(r"\\(f?)colorbox\s*(?:\[\s*([A-Za-z]+)\s*\])?\s*\{([^{}]*)\}(?:\s*\{([^{}]*)\})?")
_CELL = re.compile(r"\\(?:row|cell|column)color\s*(?:\[\s*([A-Za-z]+)\s*\])?\s*\{([^{}]*)\}"
                   r"|\\rowcolors\*?\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SHAPE = re.compile(r"\\(?:fill|filldraw|shade|shadedraw)\b\s*(?:\[([^\]]*)\])?")
_PAGECOLOR = re.compile(r"\\pagecolor\s*(?:\[\s*([A-Za-z]+)\s*\])?\s*\{([^{}]*)\}")
_BOXES = {"tcolorbox", "mdframed", "shaded", "shaded*", "snugshade", "snugshade*"}
_BOX_SETUP = re.compile(r"\\(?:tcbset|mdfsetup|mdfdefinestyle|(?:re)?newtcolorbox|DeclareTColorBox|NewTColorBox|newtcbtheorem"
                        r"|tcolorboxenvironment|newmdenv|newmdtheoremenv|surroundwithmdframed)\b")
_DEFINITION = re.compile(
    r"\\(?:(?:re|provide)?newcommand|DeclareRobustCommand)\*?\s*\{?\s*\\([A-Za-z]+)\s*\}?\s*(?:\[\s*(\d)\s*\])?\s*(?:\[[^\]]*\])?\s*(?=\{)"
    r"|\\[gex]?def\s*\\([A-Za-z]+)\s*((?:#\d)*)\s*(?=\{)"
    r"|\\(?:New|Renew|Provide|Declare)DocumentCommand\s*\{?\s*\\([A-Za-z]+)\s*\}?\s*\{[^{}]*\}\s*(?=\{)"
    r"|\\(?:re)?newenvironment\s*\{\s*([^{}]+?)\s*\}\s*(?:\[\s*\d\s*\])?\s*(?:\[[^\]]*\])?\s*(?=\{)"
    r"|\\(?:New|Renew|Provide|Declare)DocumentEnvironment\s*\{\s*([^{}]+?)\s*\}\s*\{[^{}]*\}\s*(?=\{)")
_TIKZ_STYLE = re.compile(r"(?<![\w ])([\w][\w ]{0,40}?)\s*/\.style\s*=\s*\{([^{}]*)\}|\\tikzstyle\s*\{([^{}]+)\}\s*=\s*\[([^\]]*)\]")
_NODE = re.compile(r"\bnode\s*(?:\([^()]*\)\s*)?\[([^\[\]]*)\]\s*(?:\([^()]*\)\s*)?(?:at\s*\([^()]*\)\s*)?(?=\{)")


def _blank(found):
    return re.sub(r"[^\n]", " ", found.group())


def _dead_branches(text):
    """Branches que TeX n'exécute jamais, dans `text` (commentaires, \\verb, verbatim déjà blanchis) :
    [(début du \\if…, fin de la branche, vrai si elle s'arrête à un \\fi)]. Branche fausse de \\iffalse, de \\ifnum entre deux
    nombres et d'un drapeau \\newif\\ifnom jamais mis à vrai (ni \\nomtrue, ni \\csname nomtrue, ni \\let\\ifnom), jusqu'au \\else ou au \\fi de même
    niveau."""
    # mis à vrai : \nomtrue, \csname nomtrue\endcsname, \let\ifnom… (\let\ifnom\iftrue), \csname ifnom\endcsname
    set_true = {"".join(names) for names in re.findall(
        r"\\([A-Za-z@]+)true(?![A-Za-z@])|\\csname\s*([A-Za-z@]+)true\s*\\endcsname|\\let\s*\\if([A-Za-z@]+)|\\csname\s*if([A-Za-z@]+)\s*\\endcsname", text)}
    flags = {found.group(1) for found in re.finditer(r"\\newif\s*\\if([A-Za-z@]+)", text)} - set_true
    conditional = re.compile(_CONDITIONAL.pattern + (r"|\\if(" + "|".join(map(re.escape, flags)) + r")(?![A-Za-z@])" if flags else ""))
    # une seule passe : chaque \if… ouvert trouve son \else et son \fi (pile), sans borne de longueur
    ends = {}  # début d'un \if… ouvert : (place de son premier \else ou None, place de son \fi)
    stack = []
    for token in re.finditer(r"\\(if[A-Za-z@]*|fi|else)(?![A-Za-z@])", text):
        word = token.group(1)
        if word.startswith("if"):
            if not _ASSIGNED.search(text, max(0, token.start() - 60), token.start()):
                stack.append([token.start(), None])
        elif word == "else":
            if stack and stack[-1][1] is None:
                stack[-1][1] = token.start()
        elif stack:
            opened, other = stack.pop()
            ends[opened] = (other, token.start())
    branches, cursor = [], 0
    for found in conditional.finditer(text):
        if found.start() < cursor or found.start() not in ends or _ASSIGNED.search(text, max(0, found.start() - 60), found.start()):
            continue
        if found.group(1) is not None:
            a, op, b = int(found.group(1)), found.group(2), int(found.group(3))
            if (a < b, a == b, a > b)["<=>".index(op)]:
                continue
        other, fi = ends[found.start()]
        cursor = fi if other is None else other
        branches.append((found.start(), cursor, other is None))
    return branches


def _unexecuted(text):
    """`text` (même longueur) où ce que TeX n'exécute jamais est blanchi : commentaires, \\verb|…|, \\lstinline,
    verbatim / lstlisting / minted, branches mortes des conditionnelles (_dead_branches)."""
    text = _UNEXECUTED.sub(_blank, text)
    pieces, cursor = [], 0
    for start, stop, _ in _dead_branches(text):
        pieces += [text[cursor:start], re.sub(r"[^\n]", " ", text[start:stop])]
        cursor = stop
    return "".join(pieces) + text[cursor:]


def _channels(model, value):
    """(r, v, b) entre 0 et 1 d'une couleur donnée dans un modèle xcolor ([rgb]{1,1,1}, [HTML]{FFFFFF}, [hsb]…), ou None."""
    model, value = model.strip(), value.strip()
    if model == "named":
        return _XCOLORS.get(value.lower())
    parts = [part.strip() for part in value.split(",")]
    try:
        numbers = [float(part) for part in parts] if model.lower() != "html" else None
        if model == "rgb" and len(parts) == 3:
            return tuple(numbers)
        if model == "RGB" and len(parts) == 3:
            return tuple(n / 255 for n in numbers)
        if model in ("gray", "Gray") and len(parts) == 1:
            return (numbers[0] / (15 if model == "Gray" else 1),) * 3
        if model == "cmyk" and len(parts) == 4:
            c, m, y, k = numbers
            return (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
        if model == "cmy" and len(parts) == 3:
            return tuple(1 - n for n in numbers)
        if model in ("hsb", "Hsb", "HSB") and len(parts) == 3:
            hue = numbers[0] / {"hsb": 1, "Hsb": 360, "HSB": 240}[model]
            scale = 240 if model == "HSB" else 1
            return colorsys.hsv_to_rgb(hue % 1, numbers[1] / scale, numbers[2] / scale)
        if model.lower() == "html" and re.fullmatch(r"[0-9A-Fa-f]{6}", value):
            return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, ZeroDivisionError):
        pass
    return None


def _xcolor(expression, names):
    """(r, v, b) d'une expression xcolor : nom (défini par l'auteur ou de base), mélange « red!30!blue », « black!5 »
    (5 % de noir dans du blanc), complément « -black » ; None si on ne sait pas la lire."""
    expression = (expression or "").strip()
    mixed = re.fullmatch(r"(?:rgb|cmyk?|hsb|gray|Gray|RGB|HTML|Hsb|HSB):\s*([^;,]+,[^;,]+(?:;[^;,]+,[^;,]+)*)", expression)
    if mixed:  # « rgb:white,1;black,2 » : mélange pondéré de couleurs (poids ramenés à leur somme)
        parts = [part.split(",") for part in mixed.group(1).split(";")]
        try:
            weights = [float(weight) for _, weight in parts]
        except ValueError:
            return None
        colors = [_xcolor(name, names) for name, _ in parts]
        if None in colors or sum(weights) <= 0 or min(weights) < 0:
            return None
        return tuple(sum(w * color[i] for w, color in zip(weights, colors)) / sum(weights) for i in range(3))
    complement = expression.startswith("-")
    tokens = [token.strip() for token in expression.lstrip("-").split("!")]
    color = names.get(tokens[0], _XCOLORS.get(tokens[0].lower()))
    for i in range(1, len(tokens), 2):
        try:
            share = min(max(float(tokens[i]) / 100, 0), 1)
        except ValueError:
            return None
        other = names.get(tokens[i + 1], _XCOLORS.get(tokens[i + 1].lower())) if i + 1 < len(tokens) else (1, 1, 1)
        if color is None or other is None:
            return None
        color = tuple(share * a + (1 - share) * b for a, b in zip(color, other))
    return tuple(1 - c for c in color) if complement and color is not None else color


class _Colors:
    """Couleurs définies par l'auteur (\\definecolor, \\providecolor, \\colorlet) telles qu'en vigueur à une place
    du texte (une couleur redéfinie plus loin ne change pas ce qui précède) ; un nom pas encore défini prend sa
    première définition (texte partiel, corps de macro). Chaque nom garde ses définitions dans l'ordre."""

    def __init__(self, text):
        self.defs, current = {}, {}
        for found in _COLOR_DEFINITION.finditer(text):
            if found.group(1):
                color, name = _channels(found.group(2).split("/")[0], found.group(3)), found.group(1)
            else:
                color, name = _xcolor(found.group(5), current), found.group(4)
            current[name] = color
            if color is None:
                current.pop(name)
            places, colors = self.defs.setdefault(name, ([], []))
            places.append(found.start())
            colors.append(color)
        self.first = {name: next((c for c in colors if c is not None), None) for name, (_, colors) in self.defs.items()}

    def at(self, pos):
        return _ColorsAt(self, pos)


class _ColorsAt:
    """Couleurs de l'auteur vues d'une place (`get`, comme un dict)."""

    def __init__(self, colors, pos):
        self.colors, self.pos = colors, pos

    def get(self, name, default=None):
        found = self.colors.defs.get(name)
        if not found:
            return default
        index = bisect.bisect_left(found[0], self.pos)
        color = found[1][index - 1] if index else self.colors.first[name]
        return default if color is None else color


def _is_white(model, value, names):
    color = _channels(model, value) if model else _xcolor(value, names)
    return color is not None and luminance(color) >= WHITE


def _is_dark(model, value, names, unknown=True):
    """Fond foncé ; `unknown` : réponse pour une couleur qu'on ne sait pas lire (prudent : True, le texte blanc posé
    dessus est gardé). « none », « transparent » : pas de fond."""
    value = (value or "").strip("{} ")
    if not model and value.lower() in _NO_FILL:
        return False
    color = _channels(model, value) if model else _xcolor(value, names)
    return unknown if color is None else luminance(color) < DARK


def _length_pt(value):
    found = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([a-z]*)\s*", value or "")
    return float(found.group(1)) * _UNITS[found.group(2)] if found and found.group(2) in _UNITS else None


@functools.lru_cache(maxsize=16)
def _scope_tokens(resets):
    return re.compile(r"(\\verb\*?([^A-Za-z\s*]).*?\2|\\string\s*\\[A-Za-z]+)|\\(begin|end)\s*\{([^{}]*)\}"
                      r"|(\\(?:" + resets + r")(?![A-Za-z]))|(\\bgroup|\\egroup|\\begingroup|\\endgroup)(?![A-Za-z])"
                      r"|((?<!\\)\$\$?|\\[)\]])|(&|\\\\|\\cr(?![A-Za-z]))|\\.|[{}]", re.S)


class _Work:
    """Travail permis pour juger les passages d'un texte (caractères parcourus), et ce qu'on garde d'un passage à
    l'autre (maths ouvertes au fil du paragraphe)."""

    def __init__(self):
        self.left, self.math = _SCAN_BUDGET, None


_MATH = re.compile(r"\\\\|(?<!\\)\$\$?|\\[()\[\]]")


def _math_closer(text, pos, work=None):
    """Fermeture des maths ouvertes avant `pos` dans son paragraphe (« $ », « $$ », « \\) », « \\] »), ou None ;
    `work` : reprend là où le passage précédent du même paragraphe s'est arrêté."""
    start = max(text.rfind("\n\n", max(0, pos - 5000), pos), pos - 5000, 0)
    begin, closer = start, None
    if work is not None:
        if work.math and work.math[0] == start and work.math[1] <= pos:
            begin, closer = work.math[1], work.math[2]
        if work.left <= 0:
            return None
        work.left -= pos - begin
    for found in _MATH.finditer(text, begin, pos):
        token = found.group()
        if token == "\\\\":
            continue  # retour à la ligne (« \\\\[2pt] » n'ouvre pas de maths)
        if token.startswith("$"):
            closer = None if closer == token else token
        else:
            closer = {"\\(": "\\)", "\\[": "\\]"}.get(token)
    if work is not None:
        work.math = (start, pos, closer)
    return closer


def _scope_end(text, pos, resets, work=None, cells=False):
    """Fin de la portée d'une déclaration (\\color, \\fontsize…\\selectfont) placée en `pos` : (fin, fermée). Fermée
    par l'accolade de son groupe (\\bgroup, \\begingroup), le \\end de son environnement, la fin des maths où elle
    est, la fin de sa case (`cells` : dans un tableau, un align… : « & », « \\\\ »), ou une déclaration contraire au
    même niveau (`resets` : « color|normalcolor »… ; pas dans \\verb ni après \\string) ; sinon (jusqu'à
    \\end{document}, la fin du texte ou _SCAN caractères) : ouverte. `work` (_Work) : travail qu'il reste pour ce
    texte ; épuisé, la déclaration est tenue pour ouverte."""
    limit = min(len(text), pos + _SCAN)
    if work is not None and work.left <= 0:
        return limit, False
    depth = envs = 0
    closer = _math_closer(text, pos, work)
    result = (limit, False)
    for token in _scope_tokens(resets).finditer(text, pos, limit):
        char, group = token.group(), token.group(6)
        if token.group(1):
            continue
        if char == "{" or group in ("\\bgroup", "\\begingroup"):
            depth += 1
        elif char == "}" or group in ("\\egroup", "\\endgroup"):
            depth -= 1
            if depth < 0:
                result = token.start(), True
                break
        elif token.group(7):
            if depth == 0 and envs == 0 and char == closer:
                result = token.start(), True
                break
        elif token.group(8):
            if cells and depth == 0 and envs == 0:
                result = token.start(), True
                break
        elif depth == 0 and token.group(3) == "begin":
            envs += 1
        elif depth == 0 and token.group(3) == "end":
            envs -= 1
            if envs < 0:
                result = token.start(), token.group(4).strip() != "document"
                break
        elif depth == 0 and envs == 0 and token.group(5):
            result = token.start(), True
            break
    if work is not None:
        work.left -= result[0] - pos
    return result


def _option_list(text, pos, work=None):
    """`pos` dans la liste d'options […] d'une commande ou d'un environnement (\\begin{tcolorbox}[…], \\item[…]) :
    (début du « [ », fin du « ] », environnement ou None, directement dans la liste et pas dans un {…} de valeur) ;
    sinon None."""
    stop = max(0, pos - 600)
    if text.rfind("[", stop, pos) < 0 or work is not None and work.left <= 0:
        return None
    if work is not None:
        work.left -= pos - stop
    depth = brackets = 0
    nested, index = False, pos - 1
    while index >= stop:
        char = text[index]
        if index and text[index - 1] == "\\" and char in "{}[]":
            index -= 2
            continue
        if char == "}":
            depth += 1
        elif char == "{":
            if depth:
                depth -= 1
            else:
                nested, brackets = True, 0  # dans un {…} : on remonte d'un niveau
        elif depth == 0 and char == "]":
            brackets += 1
        elif depth == 0 and char == "[":
            if brackets:
                brackets -= 1
            else:
                head = _OPTIONS_HEAD.search(text[max(0, index - 80):index])
                if not head:
                    return None
                group = _group(text, index, "[")
                env = re.match(r"\\begin\s*\{([^{}]*)\}", head.group())
                return (index, group[1] - 1, env.group(1).strip() if env else None, not nested) if group and group[1] > pos else None
        elif char == "\n" and index and text[index - 1] == "\n":
            return None
        index -= 1
    return None


def _environments(text):
    """Environnements : [(début du \\begin, fin du \\end, nom)], triés par début."""
    found, stack = [], []
    for token in _ENV.finditer(text):
        name = token.group(2).strip()
        if token.group(1) == "begin":
            stack.append((token.start(), name))
        else:
            while stack:
                start, opened = stack.pop()
                if opened == name:
                    found.append((start, token.end(), name))
                    break
    return sorted(found)


def _innermost(envs, pos):
    """Nom de l'environnement le plus intérieur autour de `pos` (`envs` : _environments), ou None."""
    index = bisect.bisect_left(envs, (pos,))
    for begin, end, name in reversed(envs[max(0, index - 2000):index]):
        if end > pos:
            return name
    return None


def _last_top(window, token):
    """Dernière place de `token` hors des groupes {…} dans `window` (un \\\\ dans \\makecell{…} ne finit pas la
    ligne), ou -1."""
    place = len(window)
    while True:
        place = window.rfind(token, 0, place)
        if place < 0:
            return -1
        after = re.sub(r"\\.", "", window[place + len(token):])
        if after.count("}") <= after.count("{") and not (token == "&" and window[place - 1:place] == "\\"):
            return place


def _bgroup(text, pos):
    """Argument écrit \\bgroup…\\egroup à partir de `pos` : (début du contenu, fin) ou None."""
    found = re.match(r"\s*\\bgroup\b", text[pos:pos + 200])
    if not found:
        return None
    depth, start = 1, pos + found.end()
    for token in re.finditer(r"\\bgroup\b|\\egroup\b", text[start:start + _SCAN]):
        depth += 1 if token.group() == "\\bgroup" else -1
        if depth == 0:
            return start, start + token.end()
    return None


def _argument(text, pos):
    """Argument {…} (ou \\bgroup…\\egroup) à partir de `pos` : (début du contenu, fin) ou None."""
    group = _group(text, pos, "{")
    return (group[1] - len(group[0]) - 1, group[1]) if group else _bgroup(text, pos)


def _in_box(text, pos, names):
    """Dans le contenu d'un \\colorbox / \\fcolorbox de fond foncé (blanc dessus : visible) ; contenu ouvert par un
    \\bgroup qui ne se ferme pas ici (code d'ouverture d'un environnement) : jusqu'au bout."""
    for found in re.finditer(r"\\f?colorbox\b", text[max(0, pos - 3000):pos]):
        start = max(0, pos - 3000) + found.end()
        model = re.match(r"\s*\[\s*([A-Za-z]+)\s*\]", text[start:start + 40])
        start += model.end() if model else 0
        if text.startswith("\\fcolorbox", found.start() + max(0, pos - 3000)):
            group = _group(text, start, "{")  # couleur du cadre
            start = group[1] if group else start
        group = _group(text, start, "{")  # couleur du fond
        content = _argument(text, group[1]) if group else None
        if content is None and group and re.match(r"\s*\\bgroup\b", text[group[1]:group[1] + 50]):
            content = (group[1], len(text))
        if content and content[1] > pos and _is_dark(model.group(1) if model else None, group[0], names):
            return True
    return False


def _fill_dark(window, names, tables, tikz):
    """Un remplissage foncé (ou illisible) est déclaré dans `window` : clé d'options (fill=, colback=…), case de
    tableau (si `tables`), forme tikz \\fill, \\shade… (si `tikz` ; couleur par défaut : noir)."""
    for found in _FILL_KEY.finditer(window):
        if found.group().startswith("[") and found.start() and not _OPTIONS_HEAD.search(window[max(0, found.start() - 80):found.start()]):
            continue  # « [fill=black] » écrit dans le texte : pas une option
        if _is_dark(None, found.group(1), names):
            return True
    if tables:
        row = _last_top(window, "\\\\")
        cell = max(row, _last_top(window, "&"))
        for found in _CELL.finditer(window):
            kind = found.group()[:5]
            if kind == "\\rowc" and not found.group().startswith("\\rowcolors") and found.start() < row:
                continue  # couleur d'une autre ligne
            if kind == "\\cell" and found.start() < cell:
                continue  # couleur d'une autre case
            specs = [(found.group(1), found.group(2))] if found.group(2) is not None else [(None, found.group(3)), (None, found.group(4))]
            if any(_is_dark(model, value, names) for model, value in specs):
                return True
    if tikz:
        for found in _SHAPE.finditer(window):
            options = [option.strip() for option in (found.group(1) or "").split(",")]
            colors = [option for option in options if "=" not in option and _xcolor(option, names) is not None]
            if not colors or _is_dark(None, colors[0], names):
                return True
    return False


def _dark_colorbox(text, names):
    """`text` pose un \\colorbox / \\fcolorbox de fond foncé."""
    for found in _COLORBOX.finditer(text):
        fill = found.group(4) if found.group(1) else found.group(3)
        if fill is not None and _is_dark(found.group(2), fill, names):
            return True
    return False


class _Context:
    """Ce qu'il faut pour juger un texte blanc : environnements, fonds de page, boîtes colorées, styles tikz, macros
    et environnements de l'auteur qui dessinent un fond foncé, surlignages et notes de couleur foncée."""

    def __init__(self, text, colors, outside):
        self.text, self.colors, self.work = text, colors, _Work()
        self.work.left *= 10  # fenêtres de quelques milliers de caractères par passage blanc
        self.envs = _environments(text)
        self.pages = [(found.start(), _is_dark(found.group(1), found.group(2), colors.at(found.start()), unknown=False))
                      for found in _PAGECOLOR.finditer(text) if outside(found.start())]
        # boîtes : réglage d'ensemble foncé (\tcbset, \mdfsetup), boîte définie foncée (\newtcolorbox{nom}{colback=…})
        self.boxes, self.dark_boxes, self.dark_envs = set(_BOXES), False, set()
        for found in _BOX_SETUP.finditer(text):
            cursor, groups = found.end(), []
            for _ in range(5):  # arguments {…} et […] qui suivent : nom de l'environnement, options
                group = _group(text, cursor, "{")
                kind = "{" if group else "["
                group = group or _group(text, cursor, "[")
                if not group:
                    break
                groups.append((kind, group[0]))
                cursor = group[1]
            braces = [content for kind, content in groups if kind == "{"]
            named = braces and found.group() not in ("\\tcbset", "\\mdfsetup", "\\mdfdefinestyle")
            if named:
                self.boxes.add(braces[0].strip())  # environnement défini (ou entouré) par la commande
            if any(_fill_dark("[" + content, colors.at(found.start()), False, False) for _, content in groups):
                if named:
                    self.dark_envs.add(braces[0].strip())
                else:
                    self.dark_boxes = True
        # styles tikz à fond foncé (« box/.style={fill=black} », « every node/.style=… »), et ceux qui s'en servent
        styles = {(found.group(1) or found.group(3)).strip(): (found.group(2) if found.group(1) else found.group(4), found.start())
                  for found in _TIKZ_STYLE.finditer(text)}
        self.dark_styles = {name for name, (options, place) in styles.items()
                            if _fill_dark("[" + options, colors.at(place), False, False)}
        for _ in range(5):
            more = {name for name, (options, _) in styles.items() if name not in self.dark_styles
                    and any(re.search(r"(?:^|,)\s*" + re.escape(style) + r"\s*(?:,|$)", options) for style in self.dark_styles)}
            if not more:
                break
            self.dark_styles |= more
        # macros et environnements de l'auteur qui dessinent un fond foncé ; \\todo[color=…], \\hl après \\sethlcolor
        self.ranges, dark_macros = [], set()
        for found in _DEFINITION.finditer(text):
            body = _group(text, found.end(), "{")
            if body is None:
                continue
            names = colors.at(found.start())
            # ouvre une boîte foncée de l'auteur (\newtcolorbox{nom}{colback=black} puis \begin{nom}) : fond foncé aussi
            around = self.dark_envs and re.search(r"\\begin\s*\{(?:" + "|".join(map(re.escape, self.dark_envs)) + r")\}", body[0])
            # \colorbox posé dans le code de fermeture d'un environnement (\end{lrbox}\colorbox{black}{\usebox0})
            closing = _group(text, body[1], "{") if found.group(6) or found.group(7) else None
            if not (_dark_colorbox(body[0], names) or around or closing and _dark_colorbox(closing[0], names)
                    or _fill_dark(body[0], names, True, "tikz" in body[0])):
                continue
            if found.group(6) or found.group(7):
                self.dark_envs.add((found.group(6) or found.group(7)).strip())
            else:
                dark_macros.add(found.group(1) or found.group(3) or found.group(5))
        for name in dark_macros:
            for found in re.finditer(r"\\" + name + r"(?![A-Za-z])", text):
                cursor = found.end()
                for _ in range(9):
                    optional = re.match(r"\s*\[[^\]]*\]", text[cursor:cursor + 500])
                    cursor += optional.end() if optional else 0
                    content = _argument(text, cursor)
                    if not content:
                        break
                    cursor = content[1]
                if cursor > found.end():
                    self.ranges.append((found.start(), cursor))
        for found in re.finditer(r"\\todo\s*\[([^\]]*)\]", text):
            color = re.search(r"(?:^|,)\s*(?:back)?colou?r\s*=\s*(\{[^{}]*\}|[\w!.\-]+)", found.group(1))
            content = _argument(text, found.end())
            if color and content and _is_dark(None, color.group(1), colors.at(found.start())):
                self.ranges.append((found.start(), content[1]))
        highlight = [(found.start(), _is_dark(found.group(1), found.group(2), colors.at(found.start()), unknown=False))
                     for found in re.finditer(r"\\sethlcolor\s*(?:\[\s*([A-Za-z]+)\s*\])?\s*\{([^{}]*)\}", text)]
        for found in re.finditer(r"\\hl\s*(?=\{)", text):
            before = [dark for start, dark in highlight if start < found.start()]
            content = _argument(text, found.end())
            if before and before[-1] and content:
                self.ranges.append((found.start(), content[1]))
        self.ranges.sort()
        self.reach = list(itertools.accumulate((end for _, end in self.ranges), max))  # fin la plus lointaine des premières plages

    def _box_options(self, begin):
        """Options […] d'une boîte ouverte en `begin` (\\begin{tcolorbox}[…]), ou ""."""
        head = _ENV.match(self.text, begin)
        group = _group(self.text, head.end(), "[") if head else None
        return group[0] if group else ""

    def on_dark(self, pos, stop):
        """Texte blanc de `pos` à `stop` lisible : page foncée (dernier \\pagecolor avant lui), \\colorbox foncé,
        macro, environnement, note ou surlignage de l'auteur à fond foncé, titre d'une boîte (dans ses options : bandeau
        de titre), boîte colorée foncée (tcolorbox, mdframed, shaded… : ses propres options, sinon sa définition ou le
        réglage d'ensemble), figure tikz qui pose une image, un graphique ou un fond foncé (avant ou après lui), ou
        remplissage foncé déclaré avant lui (5000 caractères au plus) dans son environnement — hors des environnements
        déjà refermés —, juste avant lui hors de tout environnement (\\tikz…), ou couleur de sa case."""
        text, names = self.text, self.colors.at(pos)
        if self.work.left <= 0:
            return False  # des dizaines de milliers de passages blancs : jugés sans fond
        page = [dark for start, dark in self.pages if start < pos]
        if page and page[-1] or _in_box(text, pos, names):
            return True
        before = bisect.bisect_right(self.ranges, (pos, len(text)))
        if before and self.reach[before - 1] > pos:
            return True
        options = _option_list(text, pos, self.work)
        if options and options[2] in self.boxes:
            return True  # titre, police du titre… : posés sur le bandeau de titre, foncé d'ordinaire
        index = bisect.bisect_left(self.envs, (pos,))
        around = [(begin, end, name) for begin, end, name in reversed(self.envs[max(0, index - 2000):index]) if end > pos]
        names_around = {name for _, _, name in around}
        opened = {name for _, _, name in self.envs[index:bisect.bisect_left(self.envs, (pos + 1,))]}  # environnement ouvert à cette place
        if (names_around | opened) & self.dark_envs:
            return True
        for begin, _, name in around:
            if name not in self.boxes:
                continue
            own = "[" + self._box_options(begin)
            if _FILL_KEY.search(own):
                if _fill_dark(own, names, False, False):
                    return True  # la boîte pose son propre fond foncé, même loin ou sous un autre environnement
            elif self.dark_boxes or name in ("shaded", "shaded*", "snugshade", "snugshade*") and _is_dark(None, "shadecolor", names, unknown=False):
                return True
        picture = next(((begin, end) for begin, end, name in around if name in ("tikzpicture", "axis", "tikzcd")), None)
        if picture:
            whole = text[picture[0]:picture[1]]
            if re.search(r"\\includegraphics|\\addplot|background|\\pgfimage", whole) or _fill_dark(whole, names, False, True):
                return True
        inner = [(begin, name) for begin, _, name in around if name != "document"]
        start = max(inner[0][0], pos - 5000) if inner else max(0, pos - 300)
        self.work.left -= pos - start
        pieces, cursor = [], start
        for begin, end, _ in self.envs[bisect.bisect_right(self.envs, (start,)):index]:
            if end <= pos and begin >= cursor:
                pieces += [text[cursor:begin], " " * (end - begin)]
                cursor = end
        window = "".join(pieces) + text[cursor:pos]
        tables = any(_TABLES.search(name) for name in names_around)
        table = next((begin for begin, _, name in reversed(around) if _TABLES.search(name)), None)
        if table is not None:  # \\rowcolors posé juste avant le tableau
            for found in _CELL.finditer(text, max(0, table - 300), table):
                if found.group(3) is not None and any(_is_dark(None, value, names) for value in found.group(3, 4)):
                    return True
        tikz = "tikzpicture" in names_around or "\\tikz" in window
        if tikz and self.dark_styles and ("every node" in self.dark_styles or any(
                re.search(r"[\[,]\s*" + re.escape(style) + r"\s*[,\]]", window) for style in self.dark_styles)):
            return True
        return _fill_dark(window, names, tables, tikz) or tables and _fill_dark(text[pos:stop], names, True, False)


def _signature(found, header):
    """Paramètres d'une définition de macro, un caractère chacun : « m » obligatoire, « o » optionnel […], « s » étoile
    (`found` : _DEFINITION, `header` : son texte jusqu'au corps). #k est le k-ième caractère."""
    if found.group(1):  # \newcommand{\x}[n][défaut]
        count = re.search(r"\[\s*(\d)\s*\]\s*(\[[^\]]*\])?\s*$", header)
        return "o" * bool(count and count.group(2)) + "m" * (int(count.group(1)) - bool(count.group(2))) if count else ""
    if found.group(3):  # \def\x#1#2
        return "m" * len(re.findall(r"#\d", found.group(4) or ""))
    if found.group(5):  # \NewDocumentCommand{\x}{o m}
        return "".join({"m": "m", "s": "s"}.get(c, "o") for c in re.search(r"\{([^{}]*)\}\s*$", header).group(1) if c.isalpha())
    return ""


_GROUPING = re.compile(r"\\verb\*?([^A-Za-z\s*]).*?\1|\\(?:begin|end)\s*\{\s*document\s*\}|(\\begin\s*\{[^{}]*\}|\\bgroup(?![A-Za-z])"
                       r"|\\begingroup(?![A-Za-z])|\{)|(\\end\s*\{[^{}]*\}|\\egroup(?![A-Za-z])|\\endgroup(?![A-Za-z])|\})|\\.", re.S)


def _group_ends(text, positions):
    """Fin du groupe ({…}, \\begingroup, environnement) qui contient chacune des `positions` (triées) : liste ; la fin
    du texte hors de tout groupe."""
    ends, stack, index = [len(text)] * len(positions), [[]], 0
    for token in _GROUPING.finditer(text):
        while index < len(positions) and positions[index] < token.start():
            stack[-1].append(index)
            index += 1
        if token.group(2):
            stack.append([])
        elif token.group(3) and len(stack) > 1:
            for waiting in stack.pop():
                ends[waiting] = token.start()
    return ends


def _hiding_macros(text, colors):
    """Macros et environnements de l'auteur qui cachent : [(nom, genre, {numéro d'argument caché: (selon le fond, compté)}, par couleur, paramètres,
    début, fin)] — la règle vaut de la fin de la définition à la fin de son groupe (sauf \\gdef, \\global) ou à la
    définition suivante du même nom ;
    « argument » (« \\newcommand{\\hide}[1]{\\textcolor{white}{#1}} »), « declaration » (« \\newcommand{\\wh}
    {\\color{white}} »), « environment » (clé « \\begin{nom} ») ; et la place des définitions [(début, début du corps,
    fin)]. Une définition qui pose elle-même un fond foncé (\\cellcolor{black}\\textcolor{white}{#1}) ne cache rien."""
    definitions, found_all = [], list(_DEFINITION.finditer(text))
    group_ends = _group_ends(text, [found.start() for found in found_all])
    names_at = [found.group(1) or found.group(3) or found.group(5) or found.group(6) or found.group(7) for found in found_all]
    rules, later, seen = [], [None] * len(found_all), {}
    for index in reversed(range(len(found_all))):  # définition suivante du même nom
        later[index], seen[names_at[index]] = seen.get(names_at[index]), found_all[index].start()
    for index, found in enumerate(found_all):
        name = names_at[index]
        glob = re.match(r"\\[gx]def", found.group()) or re.search(r"\\global\s*$", text[max(0, found.start() - 30):found.start()])
        stop = min(len(text) if glob else group_ends[index], len(text) if later[index] is None else later[index])
        body = _group(text, found.end(), "{")
        if body is None:
            continue
        if found.group(6) or found.group(7):  # environnement : code d'ouverture
            definitions.append((found.start(), found.end(), body[1]))
            closing = _group(text, body[1], "{")
            definitions[-1] = (found.start(), found.end(), closing[1] if closing else body[1])
        else:
            definitions.append((found.start(), found.end(), body[1]))
        names = colors.at(found.start())
        spans = [span for span in _candidates(body[0], names, {})
                 if not (span[3] and (_fill_dark(body[0][:span[0]], names, True, True) or _in_box(body[0], span[0], names)))]
        if found.group(6) or found.group(7):
            if any(not closed for *_, closed in spans):
                rules.append(("\\begin{" + name + "}", "environment", (), all(span[3] for span in spans if not span[4]), "", definitions[-1][2], stop))
            continue
        # un argument n'est caché que si chacun de ses emplois l'est (\phantom{#1}\llap{#1} l'imprime) : (selon le fond, compté) ;
        # un argument que le corps n'emploie jamais (\newcommand{\ign}[2]{#2}) n'est jamais imprimé : (sans condition, pas compté)
        numbers = {int(k) for k in re.findall(r"#(\d)", body[0])}
        hidden = {}
        for k in sorted(numbers):
            uses = [place.start() for place in re.finditer("#" + str(k), body[0])]
            if all(any(start <= use < stop for start, stop, *_ in spans) for use in uses):
                hidden[k] = (all(span[3] for span in spans if any(span[0] <= use < span[1] for use in uses)), True)
        kinds = _signature(found, text[found.start():found.end()])
        if name not in SECTIONS and name not in ("paragraph", "subparagraph"):  # \renewcommand{\section}[1]{…} reste un titre pour l'analyse
            hidden.update({k: (False, False) for k, kind in enumerate(kinds, 1) if k not in numbers and kind != "s"})
        if hidden:
            rules.append((name, "argument", hidden, None, kinds, body[1], stop))
        elif not re.search(r"#\d", body[0]) and any(not closed for *_, closed in spans):
            rules.append((name, "declaration", (), all(span[3] for span in spans if not span[4]), "", body[1], stop))
    return rules, definitions


def _candidates(text, names, macros):
    """Passages qui seraient invisibles : [(début, fin, début du texte caché ou None, couleur, fermé)] ; `couleur` :
    caché par sa couleur (gardé s'il est posé sur un fond foncé) ; `fermé` : portée bornée (groupe, environnement…).
    `names` : couleurs de l'auteur (dict, ou _Colors pour les lire à la place de chaque usage)."""
    at = names.at if isinstance(names, _Colors) else (lambda pos: names)
    spans, work, envs = [], _Work(), _environments(text)
    cells = lambda pos: bool(_ALIGNMENTS.search(_innermost(envs, pos) or ""))  # noqa: E731
    for found in _COLOR_USE.finditer(text):
        if not _is_white(found.group(2), found.group(3), at(found.start())):
            continue
        if _COLOR_VALUE.search(text, max(0, found.start() - 20), found.start()):
            continue  # « backgroundcolor=\color{white} » (listings) : une couleur de fond, pas un texte blanc
        if found.group(1) in ("textcolor", "mathcolor"):
            content = _argument(text, found.end())
            if content:
                spans.append((found.start(), content[1], content[0], True, True))
        else:
            end, closed = _scope_end(text, found.end(), "color|normalcolor", work, cells(found.start()))
            options = _option_list(text, found.start(), work)
            if options and options[3] and options[1] < end:
                end, closed = options[1], True  # « fonttitle=\\color{white} » : jusqu'au « ] » de la liste d'options
            spans.append((found.start(), end, found.end(), True, closed))
    for found in _FONTSIZE.finditer(text):
        size = _length_pt(found.group(1))
        if size is not None and size < 1:
            end, closed = _scope_end(text, found.end(), _SIZES, work, cells(found.start()))
            spans.append((found.start(), end, found.end(), False, closed))
    boxes = []  # \scalebox : (début, fin, début du contenu, échelle horizontale, verticale), les échelles se multiplient en s'emboîtant
    for found in re.finditer(r"\\scalebox\s*\{([^{}]*)\}\s*(?:\[([^\]]*)\])?", text):
        scales = []
        for value in (found.group(1), found.group(2)):
            try:
                scales.append(abs(float(value)) if value is not None else None)
            except ValueError:
                scales.append(1.0)  # \scalebox{\f} : on ne sait pas
        content = _argument(text, found.end())
        if content:
            x = 1.0 if scales[0] is None else scales[0]
            boxes.append((found.start(), content[1], content[0], x, x if scales[1] is None else scales[1]))
    outer = []  # boîtes ouvertes autour de la place courante : (fin, échelles avant elle)
    for start, stop, first, x, y in boxes:
        while outer and outer[-1][0] <= start:
            outer.pop()
        before = outer[-1][1:] if outer else (1.0, 1.0)
        x, y = x * before[0], y * before[1]
        outer.append((stop, x, y))
        if min(x, y) < 0.1:
            spans.append((start, stop, first, False, True))
    for found in re.finditer(r"\\resizebox\*?\s*\{([^{}]*)\}\s*\{([^{}]*)\}", text):
        sizes = [_length_pt(value) for value in found.groups()]
        content = _argument(text, found.end())
        if content and any(size is not None and size < 1 for size in sizes):
            spans.append((found.start(), content[1], content[0], False, True))
        elif content and sizes[0] is not None:
            # largeur par lettre de la plus longue ligne : une lettre fait environ la moitié du corps, moins d'un demi-point = corps sous 1 pt
            lines = re.sub(r"\\[A-Za-z]+", "", text[content[0]:content[1] - 1]).split("\\\\")
            if max(len(re.findall(r"[^\W\d_]", line)) for line in lines) * 0.5 > sizes[0]:
                spans.append((found.start(), content[1], content[0], False, True))
    for found in re.finditer(r"\\(text)?transparent\s*\{\s*([0-9.]+)\s*\}", text):
        try:
            clear = float(found.group(2)) < 0.1
        except ValueError:
            clear = False
        if not clear:
            continue
        if found.group(1):
            content = _argument(text, found.end())
            if content:
                spans.append((found.start(), content[1], content[0], False, True))
        else:  # déclaration : jusqu'à la fin de son groupe
            end, closed = _scope_end(text, found.end(), "transparent", work, cells(found.start()))
            spans.append((found.start(), end, found.end(), False, closed))
    for found in re.finditer(r"\\begin\s*\{\s*(tikzpicture|scope)\s*\}\s*\[([^\]]*)\]", text):
        if re.search(r"(?:^|,)\s*(?:text\s+)?opacity\s*=\s*0*(?:\.0\d*)?\s*(?:,|$)", found.group(2)):
            depth = 0
            for token in re.finditer(r"\\(begin|end)\s*\{\s*" + found.group(1) + r"\s*\}", text[found.end():found.end() + _SCAN]):
                depth += 1 if token.group(1) == "begin" else -1
                if depth < 0:
                    spans.append((found.start(), found.end() + token.end(), found.end(), False, True))
                    break
    for found in _NODE.finditer(text):
        options = found.group(1)
        white = None  # couleur du texte : la dernière de « text=… », « color=… » ou d'une couleur seule (« [white] »)
        for option in options.split(","):
            key, equal, value = option.strip().partition("=")
            if key.strip() in ("text", "color") and equal:
                white = value.strip()
            elif not equal and _xcolor(key, at(found.start())) is not None:
                white = key.strip()
        clear = re.search(r"(?:^|,)\s*(?:text\s+)?opacity\s*=\s*0*(?:\.0\d*)?\s*(?:,|$)", options)
        content = _argument(text, found.end())
        if not content:
            continue
        if clear:
            spans.append((found.start(), content[1], content[0], False, True))
        elif white and _is_white(None, white, at(found.start())) and not _fill_dark("[" + options, at(found.start()), False, False):
            spans.append((found.start(), content[1], content[0], True, True))
    for found in re.finditer(r"\\[hv]?phantom\s*(?=\{)", text):
        content = _group(text, found.end(), "{")
        if content:
            spans.append((found.start(), content[1], None, False, True))  # jamais compté : aligne souvent des chiffres
    for name, kind, hidden, colored, params, reach, until in macros:
        if kind == "environment":
            close = re.compile(r"\\end\s*\{\s*" + re.escape(name[7:-1]) + r"\s*\}")
            for found in re.finditer(re.escape(name), text):
                if not reach <= found.start() < until:
                    continue
                end = close.search(text, found.end())
                if end:
                    spans.append((found.start(), end.end(), found.end(), colored, True))
            continue
        for found in re.finditer(r"\\" + name + r"(?![A-Za-z])\s*", text):
            if not reach <= found.start() < until:
                continue  # avant la définition, hors de son groupe, après une redéfinition : une autre macro
            if kind == "declaration":
                end, closed = _scope_end(text, found.end(), "color|normalcolor|" + _SIZES, work, cells(found.start()))
                spans.append((found.start(), end, found.end(), colored, closed))
                continue
            cursor, parts = found.end(), {}
            for number, param in enumerate(params[:max(hidden)], 1):
                optional = re.match(r"\s*\[[^\]]*\]", text[cursor:cursor + 500]) if param == "o" else None
                if param == "o" and optional:
                    parts[number] = (cursor + optional.start() + optional.group().index("[") + 1, cursor + optional.end())
                    cursor += optional.end()
                elif param == "s":
                    cursor += len(re.match(r"\s*\*?", text[cursor:cursor + 20]).group())
                elif param == "m":
                    content = _argument(text, cursor)
                    if not content:
                        break
                    parts[number] = content
                    cursor = content[1]
            runs = []  # arguments cachés qui se suivent, de même nature : [premier, dernier, (selon le fond, compté)]
            for number in sorted(hidden.keys() & parts.keys()):
                if runs and runs[-1][1] == number - 1 and runs[-1][2] == hidden[number]:
                    runs[-1][1] = number
                else:
                    runs.append([number, number, hidden[number]])
            for first, last, (by_color, counted) in runs:
                spans.append((found.start() if first == 1 else parts[first][0] - 1, parts[last][1],
                              parts[first][0] if counted else None, by_color, True))
    return spans


def invisible(text):
    """Passages imprimés mais invisibles : [(début, fin, avec_lettres)], dans l'ordre et sans chevauchement.
    Couleur blanche ou presque (luminance ≥ WHITE : white, white!95, black!3, -black, [rgb]{.97,.97,.97}, [hsb],
    couleur définie ainsi à cette place) par \\textcolor, \\mathcolor, \\color (portée jusqu'à la fin du groupe, de
    l'environnement ou au \\color suivant), nœud tikz [text=white], sauf sur un fond foncé (`_Context.on_dark`) et
    dans une présentation beamer ; \\fontsize{<1pt}…\\selectfont ; \\scalebox{<0.1} ; \\resizebox{<1pt} ;
    \\texttransparent{<0.1}, \\transparent{<0.1} ; nœud, figure ou scope tikz [opacity=0] ; \\phantom{…} (retiré, jamais compté) ; macros et environnements
    de l'auteur qui font de même (`_hiding_macros`), et ce qu'une définition cache sans paramètre. Une déclaration
    qui court jusqu'à \\end{document} sans texte lisible avant elle (préambule, début du document) règle tout le
    document : gardée. Ce que TeX n'exécute jamais est ignoré (`_unexecuted`)."""
    masked = _unexecuted(text)
    colors = _Colors(masked)
    macros, definitions = _hiding_macros(masked, colors)
    outside = lambda pos: not any(head <= pos < end for head, _, end in definitions)
    context = _Context(masked, colors, outside)
    begin = masked.find("\\begin{document}")
    beamer = re.search(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{\s*beamer\s*\}", masked[:max(begin, 0)])  # préambule
    body = begin + len("\\begin{document}") if begin >= 0 else 0
    readable = len(masked)  # place de la 80e lettre du document : une déclaration ouverte avant elle règle tout
    letters = (found for found in re.finditer(r"\\[A-Za-z]+|[^\W\d_]", masked[body:]) if not found.group().startswith("\\"))
    for count, found in enumerate(letters, 1):
        if count == 80:
            readable = body + found.start()
            break
    spans = []
    for start, stop, shown, colored, closed in _candidates(masked, colors, macros):
        definition = next(((head, inner, end) for head, inner, end in definitions if head <= start < end), None)
        if definition:
            # dans une définition : son en-tête (nom de la macro), ou un passage qui dépend des paramètres, est gardé
            if start < definition[1] or "#" in masked[start:stop]:
                continue
            names = colors.at(start)
            if colored and (_fill_dark(masked[definition[1]:start], names, True, True) or _in_box(masked, start, names)):
                continue
        elif colored and (beamer or context.on_dark(start, stop)):
            continue
        if not closed and start < readable:
            continue  # tout le document ainsi : un réglage d'ensemble, pas un passage caché
        spans.append((start, stop, shown))
    kept, end = [], -1
    for start, stop, shown in sorted(spans):  # `shown` : début du texte rendu invisible
        if start >= end:
            letters = shown is not None and bool(re.search(r"[^\W\d_]", re.sub(r"\\[A-Za-z]+", "", masked[shown:stop])))
            kept.append((start, stop, letters))
            end = stop
    return kept


def strip_hidden(text, shifts=None, counted=None):
    """Passages que LaTeX n'imprime pas : \\begin{comment}…\\end{comment}, branches mortes de conditionnelles (\\iffalse…\\fi), argument d'une macro
    qui ne l'imprime pas (« \\newcommand{\\comment}[1]{} ») ; puis ceux qu'il imprime invisibles (`invisible`).
    `shifts` (liste) : y ajoute la correspondance des positions de chaque étape (Shifts, dans l'ordre). `counted`
    (liste) : y ajoute le nombre de passages invisibles avec des lettres (signalés au lecteur)."""
    text = _sub(r"\\begin\s*\{comment\}.*?\\end\s*\{comment\}", " ", text, shifts, flags=re.S)
    for name in set(re.findall(r"\\(?:re)?newcommand\*?\s*\{?\\([A-Za-z]+)\}?\s*\[1\]\s*\{\s*\}", text)):
        edits, pos = [], 0
        for match in re.finditer(r"\\" + name + r"\s*(?=\{)", text):
            if match.start() < pos:
                continue
            group = _group(text, match.end(), "{")
            if group is None:
                continue
            edits.append((match.start(), group[1], ""))
            pos = group[1]
        text = _edit(text, edits, shifts)
    # branches mortes (\iffalse, \ifnum faux, drapeau \newif jamais vrai) : la branche, et son \fi quand elle s'arrête à lui
    edits = [(start, stop + 3 * closing, "") for start, stop, closing in _dead_branches(_UNEXECUTED.sub(_blank, text))]
    text = _edit(text, edits, shifts)
    spans = invisible(text)
    if counted is not None:
        counted.append(sum(1 for *_, letters in spans if letters))
    return _edit(text, [(start, stop, " ") for start, stop, _ in spans], shifts)


def extract(text, bbl="", positions=None):
    """Structure (même forme que structure.extract_structure) à partir du texte TeX développé. `positions` (dict) :
    y met la place des énoncés, preuves et sections dans `text` (positions de caractères, fin exclue) —
    {"nodes": {id: (début, fin)}, "proofs": {id: (début, fin)}, "sections": {id: début}} (identifiants d'avant
    texfile.stable_ids) ; la structure rendue est la même avec ou sans."""
    hidden = [] if positions is not None else None
    invisible_count = []
    text = strip_hidden(text, hidden, invisible_count)
    envs, within, formats = declarations(text)
    begin = text.find("\\begin{document}")
    body = text[begin + len("\\begin{document}"):] if begin >= 0 else text
    end = body.find("\\end{document}")
    body = body[:end] if end >= 0 else body
    rewritten = [] if positions is not None else None
    body = normalize_proofs(body, envs, rewritten)
    section_starts = {}      # section → position de sa commande dans le texte

    toc, nodes, order, proofs = [], {}, [], {}
    counters, sections = {}, [0, 0, 0, 0]
    appendix = False
    labels = {}              # clé \label → ("theorem", id) ou ("equation", id d'énoncé ou None)
    spans = {}               # énoncé → [début, fin] dans le texte (« \begin{lemma} » … « \end{lemma} »)
    text_equations = {}      # équation étiquetée du texte courant → position
    refs = []                # (clés, contexte) contexte = ("node", id) | ("proof", id) | ("text", id de section)
    cites = []
    stack = []               # cadres ouverts : {"type": theorem|proof|equation, "id", "start"}
    candidate = None         # énoncé à démontrer qui attend sa preuve
    last_proof_target = None
    since_proof = True       # rien depuis la dernière preuve (deuxième preuve à la suite)
    proof_sections = {}      # id de section → clés citées dans son titre (« Proof of Theorem \ref{…} »)
    section_of_level = {}
    # livre (chapitres) : sections et énoncés numérotés dans leur chapitre (« 3.2 », « 3.2.1 »), regroupés par chapitre
    book = bool(re.search(r"\\chapter\s*[\[{]", body))
    numbered_chapters = True  # \frontmatter, \backmatter : chapitres sans numéro (préface, bibliographie…)
    parts = 0

    def chapter():
        return _letter(sections[0]) if appendix else str(sections[0])

    def in_chapter(value):
        return f"{chapter()}.{value}" if book else value

    printed = {}  # dernier numéro imprimé de chaque compteur d'énoncés (« Claim 1.2.1 » numéroté dans le théorème 1.2)

    def number(env):
        counter = envs[env]["counter"]
        counters[counter] = counters.get(counter, 0) + 1
        for child, inside in within.items():  # compteurs numérotés dans celui-ci : repartent de zéro
            if envs.get(inside, {}).get("counter", inside) == counter:
                counters[envs.get(child, {}).get("counter", child)] = 0
        level = within.get(counter) or within.get(env)
        value = _format(counters[counter], formats.get(counter) or formats.get(env))
        section = in_chapter(str(sections[1]) if book or not appendix else _letter(sections[1]))
        parent = envs.get(level, {}).get("counter", level)
        if parent in printed:  # \newtheorem{claim}{Claim}[theorem]
            value = f"{printed[parent]}.{value}"
        elif level == "section":
            value = f"{section}.{value}"
        elif level == "subsection":
            value = f"{section}.{sections[2]}.{value}"
        elif level == "chapter":
            value = f"{chapter()}.{value}"
        printed[counter] = value
        return counter, value

    def current_section():
        for level in (3, 2, 1, 0):
            if level in section_of_level:
                return section_of_level[level]
        return None

    def chain():
        return tuple(section_of_level[level] for level in sorted(section_of_level))

    def context():
        for frame in reversed(stack):
            if frame["type"] == "theorem":
                return ("node", frame["id"])
            if frame["type"] == "proof":
                return ("proof", frame["id"])
        return ("text", chain())

    for match in TOKEN.finditer(body):
        command, star = match.group(1), match.group(2)
        pos = match.end()
        if command in SECTIONS:
            # \renewcommand\section{…} dans le corps d'un article : une définition, pas une section (ai._section_span compte
            # de même). Un livre n'est pas concerné : ses chapitres gardent leurs numéros, et ai.TEX_CHAPTER compte tout
            if not book and defines_macro(body, match.start()):
                continue
            (short, title), _ = _args(body, pos, "[{")
            if title is None:
                continue
            level = SECTIONS[command]
            unnumbered = star or (command == "chapter" and not numbered_chapters)
            if not unnumbered:
                if command == "chapter":
                    sections[0] += 1
                    sections[1:] = [0, 0, 0]
                elif level in (1, 2, 3):
                    sections[level] += 1
                    for deeper in range(level + 1, 4):
                        sections[deeper] = 0
                for counter, inside in within.items():
                    if (inside == "section" and level <= 1) or (inside == "subsection" and level <= 2) or (inside == "chapter" and level == 0):
                        counters[envs.get(counter, {}).get("counter", counter)] = 0
            section_id = f"tex-sec-{len(toc) + 1}"
            if unnumbered or command == "part":
                numbered = ""
            elif command == "chapter":
                numbered = chapter() + ". "
            else:
                numbered = in_chapter(".".join(
                    [str(sections[1]) if book or not appendix else _letter(sections[1])] + [str(sections[i]) for i in range(2, level + 1)])) + ". "
            anchors = [f"{command}.{numbered.rstrip('. ')}"] if numbered else []
            if numbered and appendix and command == ("chapter" if book else "section"):  # hyperref : « appendix.A »
                anchors.append(f"appendix.{numbered.rstrip('. ')}")
            if command == "part" and not star:
                parts += 1
                anchors = [f"part.{parts}"]
            toc.append({"id": section_id, "level": level, "kind": command, "title": numbered + plain(title), "anchors": anchors})
            section_starts[section_id] = match.start()
            section_of_level[level] = section_id
            for deeper in range(level + 1, 4):
                section_of_level.pop(deeper, None)
            if PROOF_HEADING.search(plain(title)):
                keys = [k.strip() for m in re.finditer(r"\\(?:ref|cref|Cref|autoref)\*?\{([^}]*)\}", title) for k in m.group(1).split(",")]
                if keys:
                    proof_sections[section_id] = keys
            candidate = None
            continue
        if command in ("frontmatter", "mainmatter", "backmatter"):
            numbered_chapters = command == "mainmatter"
            continue
        if command == "appendix":
            appendix = True
            sections[0 if book else 1] = 0
            continue
        if command in ("begin", "end"):
            group = _group(body, pos, "{")
            if group is None:
                continue
            name, after = group[0].strip(), group[1]
            base = name.rstrip("*")
            if command == "begin":
                if name in envs:
                    optional = _group(body, after, "[")
                    start = optional[1] if optional else after
                    node_id = f"tex-thm-{len(order) + 1}"
                    counter, value = number(name) if envs[name]["numbered"] else (None, "")
                    label = f"{envs[name]['name']} {value}".strip()
                    inside_proof = next((f for f in reversed(stack) if f["type"] == "proof"), None)
                    nodes[node_id] = {
                        "id": node_id, "kind": classify(label, [f"ltx_theorem_{name}"]), "label": label,
                        "name": plain(optional[0]) if optional else "", "section": current_section(),
                        "proofs": [], "detectedProof": False, "proofSection": False, "cited": [],
                        "justifiedBy": None, "manual": None, "proofPassage": None, "uses": [], "usesEquations": [],
                        "cites": [], "usedBy": [], "weakUses": [], "tex": "", "proofTex": "",
                        "anchors": [f"{counter}.{value}", f"{name}.{value}"] if value else [],
                        "insideProof": inside_proof["id"] if inside_proof else None, "chain": chain(),
                    }
                    if optional:  # « Theorem 3.2 ([12, Theorem 4.3]) »
                        nodes[node_id]["cited"] = [f"bib.{k.strip()}" for m in re.finditer(r"\\cite[a-z]*\*?(?:\[[^\]]*\])?\{([^}]*)\}", optional[0]) for k in m.group(1).split(",")]
                    order.append(node_id)
                    spans[node_id] = [match.start(), len(body)]
                    stack.append({"type": "theorem", "id": node_id, "start": start})
                    if not inside_proof:
                        if nodes[node_id]["kind"] in NEEDS_PROOF:
                            candidate = node_id
                        since_proof = False
                elif base == "proof" or name == "proof*":
                    optional = _group(body, after, "[")
                    start = optional[1] if optional else after
                    proof_id = f"tex-proof-{len(proofs) + 1}"
                    keys = [k.strip() for m in re.finditer(r"\\(?:ref|cref|Cref|autoref)\*?\{([^}]*)\}", optional[0] if optional else "")
                            for k in m.group(1).split(",")]
                    target = None
                    outer = next((f for f in reversed(stack) if f["type"] == "proof"), None)
                    taken = {p["of"] for p in proofs.values()}
                    inner = [n for n in order if outer and nodes[n]["insideProof"] == outer["id"] and n not in taken]
                    if not keys and inner:  # preuve dans une preuve : celle du dernier énoncé posé dans cette preuve
                        target = inner[-1]
                    elif not keys:
                        if candidate:
                            target = candidate
                        elif since_proof and last_proof_target:
                            target = last_proof_target  # deuxième preuve à la suite
                        elif order and not nodes[order[-1]]["proofs"] and not nodes[order[-1]]["insideProof"]:
                            target = order[-1]
                    proofs[proof_id] = {"id": proof_id, "of": target, "keys": keys, "start": start,
                                        "section": current_section(), "title": plain(optional[0]) if optional else ""}
                    proofs[proof_id]["span"] = [match.start(), len(body)]
                    stack.append({"type": "proof", "id": proof_id, "start": start})
                elif base in EQUATION_ENVS:
                    stack.append({"type": "equation", "id": None, "start": after})
            else:
                for index in range(len(stack) - 1, -1, -1):
                    frame = stack[index]
                    kind = frame["type"]
                    if (kind == "theorem" and name in envs) or (kind == "proof" and base == "proof") or (kind == "equation" and base in EQUATION_ENVS):
                        del stack[index:]
                        if kind == "theorem":
                            nodes[frame["id"]]["tex"] = body[frame["start"]:match.start()].strip()[:MAX_TEX]
                            spans[frame["id"]][1] = after  # après « \end{lemma} »
                        elif kind == "proof":
                            proof = proofs[frame["id"]]
                            proof["span"][1] = after
                            proof["tex"] = body[frame["start"]:match.start()].strip()
                            if proof["of"] and proof["of"] == candidate:
                                candidate = None
                            last_proof_target = proof["of"]
                            since_proof = True
                        break
            continue
        if command == "label":
            optional = _group(body, pos, "[")  # cleveref : \label[proposition]{…}
            group = _group(body, optional[1] if optional else pos, "{")
            if group is None:
                continue
            key = group[0].strip()
            inner = stack[-1] if stack else None
            owner = next((f["id"] for f in reversed(stack) if f["type"] == "theorem"), None)
            if inner and inner["type"] == "equation" or any(f["type"] == "equation" for f in stack):
                proof = next((f["id"] for f in reversed(stack) if f["type"] == "proof"), None)
                labels.setdefault(key, ("equation", owner, proof))
                if owner is None and proof is None:
                    text_equations.setdefault(key, match.start())  # équation du texte courant
            elif owner:  # (étiquette d'un \item de l'énoncé : son numéro n'est pas celui de l'énoncé)
                frame = next(f for f in reversed(stack) if f["type"] == "theorem")
                depth = sum(1 if m.group(1) == "begin" else -1 for m in ITEM_LISTS.finditer(body, frame["start"], match.start()))
                labels.setdefault(key, ("theorem", owner, depth > 0))
            continue
        if command in REF_COMMANDS or command == "hyperref":
            group = _group(body, pos, "[" if command == "hyperref" else "{")
            if group:
                refs.append(([k.strip() for k in group[0].split(",") if k.strip()], context(), match.start()))
            continue
        if command in CITE_COMMANDS:
            values, _ = _args(body, pos, "[[{")
            keys = values[-1] if values and values[-1] is not None else None
            if keys:
                cites.append(([f"bib.{k.strip()}" for k in keys.split(",") if k.strip()], context()))
            continue

    # Preuves dont le titre nomme l'énoncé (« Proof of Theorem \ref{…} ») et sections de preuve
    for proof in proofs.values():
        for key in proof["keys"]:
            found = labels.get(key)
            if found and found[0] == "theorem":
                proof["of"] = found[1]
                break
        if proof["of"] and proof["of"] in nodes:
            nodes[proof["of"]]["proofs"].append(proof["id"])
            nodes[proof["of"]]["proofTex"] = (nodes[proof["of"]]["proofTex"] + "\n\n" + proof.get("tex", "")).strip()[:MAX_TEX]
    section_targets = {}
    for section_id, keys in proof_sections.items():
        targets = [labels[k][1] for k in keys if labels.get(k, (None,))[0] == "theorem"]
        for target in targets:
            own = any(proofs.get(p, {}).get("section") == section_id for p in nodes[target]["proofs"])
            if len(targets) > 1 and own:
                continue  # section commune : celui-ci y a sa propre preuve
            nodes[target]["proofs"].append(section_id)
            nodes[target]["proofSection"] = True
            section_targets.setdefault(section_id, []).append(target)

    def owners(ctx):
        kind, ident = ctx
        if kind == "node":
            return [ident]
        if kind == "proof":
            target = proofs.get(ident, {}).get("of")
            return [target] if target else []
        return [t for section in ident for t in section_targets.get(section, [])]

    def add(lst, value):
        if value and value not in lst:
            lst.append(value)

    def use(owner, key):
        found = labels.get(key)
        if not found:
            return
        if found[0] == "theorem" and found[1] != owner:
            add(nodes[owner]["uses"], found[1])
        elif found[0] == "equation":
            _, statement, proof = found
            add(nodes[owner]["usesEquations"], key)
            holder = statement or (proofs.get(proof, {}).get("of") if proof else None)
            if holder and holder != owner:
                add(nodes[owner]["uses"], holder)

    for keys, ctx, _ in refs:
        for owner in owners(ctx):
            for key in keys:
                use(owner, key)
    implicit_dependencies(body, nodes, order, proofs, spans, refs, labels, text_equations, use, add)
    for keys, ctx in cites:
        for owner in owners(ctx):
            for key in keys:
                add(nodes[owner]["cites"], key)
    for node in nodes.values():  # énoncé posé dans une preuve (ou une section de preuve) : utilisé par l'énoncé démontré
        target = proofs.get(node["insideProof"] or "", {}).get("of")
        if target and target != node["id"]:
            add(nodes[target]["uses"], node["id"])
        for section in node["chain"]:
            for target in section_targets.get(section, []):
                if target != node["id"] and node["id"] not in nodes[target]["uses"] and target not in node["uses"]:
                    add(nodes[target]["uses"], node["id"])
    for node_id in order:
        for used in nodes[node_id]["uses"]:
            add(nodes[used]["usedBy"], node_id)

    equations = {key: {"label": "", "owner": found[1]} for key, found in labels.items() if found[0] == "equation"}
    preamble = text[:begin] if begin >= 0 else ""
    if positions is not None:
        offset = begin + len("\\begin{document}") if begin >= 0 else 0

        def place(pos, edge):
            """Position du corps réécrit → position dans le texte reçu (réécritures défaites dans l'ordre inverse)."""
            for shift in reversed(rewritten):
                pos = getattr(shift, edge)(pos)
            pos += offset
            for shift in reversed(hidden):
                pos = getattr(shift, edge)(pos)
            return pos

        positions["nodes"] = {n: (place(spans[n][0], "start"), place(spans[n][1], "end")) for n in order if n in spans}
        positions["proofs"] = {p["id"]: (place(p["span"][0], "start"), place(p["span"][1], "end")) for p in proofs.values()}
        positions["sections"] = {s: place(at, "start") for s, at in section_starts.items()}
    return assign_chapters({
        "labels": {key: (found[1] if found[0] == "theorem" else None) for key, found in labels.items()},
        "itemLabels": [key for key, found in labels.items() if found[0] == "theorem" and found[2]],
        "macros": macros(preamble),
        "version": STRUCTURE_VERSION, "source": "tex", "toc": toc, "book": book,
        "nodes": [{k: v for k, v in nodes[n].items() if k not in ("insideProof", "chain")} for n in order],
        "proofBlocks": [{"id": p["id"], "of": p["of"], "anchors": []} for p in proofs.values()],
        "equations": equations, "bibliography": bibliography(body + "\n" + bbl),
        "hiddenText": invisible_count[0],
    }, lambda proof: proofs.get(proof, {}).get("section") or proof)


def implicit_dependencies(body, nodes, order, proofs, spans, refs, labels, text_equations, use, add):
    """Dépendances que les \\ref ne disent pas, comme l'analyse de la version HTML (structure.py) :
    - renvoi implicite dans un énoncé ou sa preuve : « the above lemma », « by applying the above to … » ;
    - énoncé sans preuve justifié par la phrase juste avant (« The following corollary is a consequence of (2.5) »)
      ou juste après (« This follows from … ») : ses renvois sont ceux de l'énoncé ;
    - équation obtenue dans le texte courant à partir d'énoncés (« From (3.30) and the above lemma, we get (3.33) ») :
      un énoncé qui la cite dépend (faiblement, sans créer de cycle) de ces énoncés ;
    - corollaire placé juste après un énoncé (rien entre les deux) : il en découle."""
    starts = sorted((spans[n][0], n) for n in order if n in spans)
    blocks = sorted([tuple(spans[n]) for n in order if n in spans] + [tuple(p["span"]) for p in proofs.values() if p.get("span")])

    def before(pos, kind=None, exclude=None):
        """Dernier énoncé commencé avant `pos` (de ce type)."""
        for start, node_id in reversed(starts):
            if start < pos and node_id != exclude and (kind is None or nodes[node_id]["kind"] == kind):
                return node_id
        return None

    def implicit(a, b):
        found = []
        for match in IMPLICIT_REF.finditer(body, a, b):
            words = [g for g in match.groups() if g]
            node_id = before(match.start(), classify(words[0], []) if words else None)
            if node_id:
                found.append((match.start(), node_id))
        return found

    def last_end(pos):
        return max((end for start, end in blocks if end <= pos), default=0)

    def refs_in(a, b):
        return [(at, key) for keys, _, at in refs if a <= at < b for key in keys]

    for node_id in order:  # renvois implicites dans l'énoncé et ses preuves
        segments = [spans.get(node_id)] + [p.get("span") for p in proofs.values() if p.get("of") == node_id]
        for segment in filter(None, segments):
            for _, used in implicit(*segment):
                if used != node_id:
                    add(nodes[node_id]["uses"], used)

    sentence_end = re.compile(r"(?<=[.!?])\s+(?=[A-Z\\])")
    for node_id in order:  # justification en une phrase
        node = nodes[node_id]
        if node["proofs"] or node["cited"] or node["kind"] not in NEEDS_PROOF or node_id not in spans:
            continue
        start, end = spans[node_id]
        window_start = max(last_end(start), start - 1500)
        window = body[window_start:start]
        cut = [m.end() for m in sentence_end.finditer(window)]
        sentence_start = window_start + (cut[-1] if cut else 0)
        last = plain(body[sentence_start:start])
        span = None
        if last and JUSTIFIES_NEXT.search(last) and JUSTIFICATION.search(last) and not ANNOUNCES_PROOF_BELOW.search(last):
            span = (sentence_start, start)
        else:
            following_start = min((s for s, _ in starts if s > end), default=len(body))
            after = body[end:min(following_start, end + 1500)]
            first = sentence_end.split(after.strip(), 1)[0]
            if first and JUSTIFIES_PREVIOUS.search(plain(first)):
                span = (end, end + after.find(first[:20]) + len(first))
        if span:
            node["justifiedBy"] = node_id  # lu dans le PDF : pas de paragraphe à désigner, l'énoncé lui-même
            for _, key in refs_in(*span):
                use(node_id, key)
            for _, used in implicit(*span):
                if used != node_id:
                    add(node["uses"], used)

    derived = {}  # équation du texte courant → énoncés dont elle est tirée
    for key, at in text_equations.items():
        a = max(last_end(at), at - 800)
        sources = [(pos, labels[k][1]) for pos, k in refs_in(a, at)
                   if labels.get(k, (None,))[0] == "theorem" and not PROVING.search(plain(body[max(a, pos - 60):pos]))]
        sources += implicit(a, at)
        derived[key] = [n for _, n in sources if spans.get(n, [len(body)])[0] < at]

    for node_id in order:  # corollaire juste après un énoncé (ou sa preuve)
        node = nodes[node_id]
        if node["kind"] != "corollary" or node["insideProof"] or node_id not in spans:
            continue
        previous = before(spans[node_id][0], exclude=node_id)
        if previous is None:
            continue
        gap = body[last_end(spans[node_id][0]):spans[node_id][0]]
        if last_end(spans[node_id][0]) >= spans[previous][1] and not re.sub(r"\\label\s*\{[^}]*\}|\s", "", gap):
            add(node["uses"], previous)

    def reaches(start, goal):
        seen, stack = set(), [start]
        while stack:
            current = stack.pop()
            if current == goal:
                return True
            if current not in seen:
                seen.add(current)
                stack += nodes[current]["uses"]
        return False

    for node_id in order:  # liens faibles par une équation du texte courant, sans créer de cycle
        node = nodes[node_id]
        for key in list(node["usesEquations"]):
            for used in derived.get(key, []):
                if used != node_id and used not in node["uses"] and not reaches(used, node_id):
                    node["uses"].append(used)
                    add(node["weakUses"], used)


def macros(preamble):
    """Macros simples de l'auteur (\\newcommand, \\def, \\DeclareMathOperator), pour MathJax."""
    found = []
    for match in re.finditer(r"\\(?:re|provide)?newcommand\*?\s*\{?\\([A-Za-z]+)\}?\s*(\[\d\])?\s*(\[[^\]]*\])?\s*(?=\{)", preamble):
        group = _group(preamble, match.end(), "{")
        if group and len(group[0]) < 300 and "@" not in group[0] and "\\if" not in group[0]:
            found.append(f"\\newcommand{{\\{match.group(1)}}}{match.group(2) or ''}{match.group(3) or ''}{{{group[0]}}}")
    for match in re.finditer(r"\\def\s*\\([A-Za-z]+)\s*(?=\{)", preamble):
        group = _group(preamble, match.end(), "{")
        if group and len(group[0]) < 300 and "@" not in group[0] and "#" not in group[0]:
            found.append(f"\\newcommand{{\\{match.group(1)}}}{{{group[0]}}}")
    for match in re.finditer(r"\\DeclareMathOperator(\*?)\s*\{\\([A-Za-z]+)\}\s*(?=\{)", preamble):
        group = _group(preamble, match.end(), "{")
        if group:
            found.append(f"\\newcommand{{\\{match.group(2)}}}{{\\operatorname{match.group(1)}{{{group[0]}}}}}")
    return found[:400]


def bibliography(text):
    """Références d'un environnement thebibliography (ou d'un fichier .bbl de BibTeX), sinon d'un .bbl de biblatex."""
    start = text.find("\\begin{thebibliography}")
    if start < 0:
        if "\\entry{" in text:
            return _biblatex(text)
        return _amsrefs(text) if "\\bib{" in text else []
    stop = text.find("\\end{thebibliography}", start)
    block = text[start:stop if stop > 0 else len(text)]
    entries = []
    for index, match in enumerate(re.finditer(r"\\bibitem\s*(\[[^\]]*\])?\s*\{([^}]*)\}", block)):
        following = block.find("\\bibitem", match.end())
        raw = block[match.end():following if following > 0 else len(block)]
        raw = re.sub(r"\\newblock\b", " ", raw)
        url = " ".join(re.findall(r"\\(?:url|href)\s*\{([^}]*)\}", raw))
        text_entry = plain(re.sub(r"\\href\s*\{[^}]*\}\s*\{([^}]*)\}", r"\1", raw))
        doi = re.search(r"\b10\.\d{4,9}/[^\s\"<>{}]+", f"{raw} {url}")
        label = plain(match.group(1)[1:-1]) if match.group(1) else str(index + 1)
        entries.append({"id": f"bib.{match.group(2).strip()}", "label": label, "text": text_entry, "title": "",
                        "arxiv": extract_ids(f"{raw} {url}"), "doi": doi.group(0).rstrip(".,;") if doi else ""})
    return entries


def _amsrefs(text):
    """Entrées au format amsrefs (\\bib{clé}{type}{author={…}, title={…}, …}), du .bbl ou de la source."""
    entries = []
    for index, match in enumerate(re.finditer(r"\\bib\*?\s*\{([^}]*)\}\s*\{(\w+)\}\s*(?=\{)", text)):
        group = _group(text, match.end(), "{")
        if group is None:
            continue
        body = group[0]
        fields = {}
        for field in re.finditer(r"(\w+)\s*=\s*(?=\{)", body):
            value = _group(body, field.end(), "{")
            if value:
                fields.setdefault(field.group(1).lower(), []).append(plain(value[0]))
        authors = ", ".join(fields.get("author", []))
        venue = (fields.get("journal") or fields.get("conference") or fields.get("publisher") or [""])[0]
        year = re.sub(r"[-–].*$", "", (fields.get("date") or fields.get("year") or [""])[0])
        text_entry = ", ".join(part for part in (authors, (fields.get("title") or [""])[0], venue,
                                                (fields.get("volume") or [""])[0], year, (fields.get("pages") or [""])[0]) if part)
        doi = (fields.get("doi") or [""])[0]
        eprint = " ".join(fields.get("eprint", []) + fields.get("arxiv", []))
        entries.append({"id": f"bib.{match.group(1).strip()}", "label": str(index + 1), "text": text_entry,
                        "title": (fields.get("title") or [""])[0], "arxiv": extract_ids(eprint) if eprint else [],
                        "doi": doi.rstrip(".,;")})
    return entries


def _biblatex(text):
    """Entrées d'un .bbl de biblatex (\\entry{clé}{type}… \\endentry) : auteurs, titre, revue, année, DOI, arXiv."""
    entries = []
    for index, match in enumerate(re.finditer(r"\\entry\{([^}]*)\}\{(\w+)\}", text)):
        stop = text.find("\\endentry", match.end())
        block = text[match.end():stop if stop > 0 else len(text)]

        def field(name):
            found = re.search(r"\\field\{" + name + r"\}", block)
            group = found and _group(block, found.end(), "{")
            return plain(group[0]) if group else ""

        families = re.findall(r"family=\{([^{}]*)\}", block)
        givens = re.findall(r"given=\{([^{}]*)\}", block)
        names = [plain(f"{g} {f}" if len(givens) == len(families) else f) for f, g in zip(families, givens or families)]
        doi = re.search(r"\\verb\{doi\}\s*\\verb\s*(\S+)", block)
        venue = field("journaltitle") or field("booktitle") or field("publisher")
        text_entry = ", ".join(part for part in (", ".join(names), field("title"), venue, field("year")) if part)
        eprint = field("eprint")
        entries.append({"id": f"bib.{match.group(1).strip()}", "label": field("labelalpha") or str(index + 1), "text": text_entry,
                        "title": field("title"), "arxiv": extract_ids(eprint) if eprint else [],
                        "doi": doi.group(1).rstrip(".,;") if doi else ""})
    return entries


def from_source(data):
    """Structure à partir de l'archive de la source arXiv ; None si ce n'est pas du TeX."""
    files = _read_archive(data, extensions=(".tex", ".ltx", ".bbl", ".json"))
    if not files:
        return None
    main = choose_main(files, files.get("00README.json"))
    if not main:
        return None
    # .bbl du fichier principal de préférence (une source peut en contenir plusieurs)
    bbl = files.get(posixpath.splitext(main)[0] + ".bbl") or next((t for n, t in files.items() if n.endswith(".bbl")), "")
    return from_files(files, main, bbl)


def from_files(files, main, bbl=""):
    """Structure et texte développé du fichier principal `main` de `files` ({chemin: texte}) ; `bbl` : bibliographie
    produite par BibTeX, ajoutée si le texte n'a pas son propre thebibliography."""
    text = _expand(files, main)
    if "\\begin{thebibliography}" not in text and bbl:
        text = text.replace("\\end{document}", bbl + "\n\\end{document}") if "\\end{document}" in text else text + bbl
    return extract(text), text


# ---------- Pages du PDF ----------

def locate_pages(structure, pdf_bytes):
    """Page (à partir de 1) de chaque énoncé et section : ancres de hyperref, sinon texte du PDF (dans l'ordre du
    document : à partir de la page de l'élément précédent ; jamais une page de sommaire pour une section). Avec une
    ancre, aussi la hauteur dans la page (« top », coordonnées du PDF, origine en bas). Preuves : leur propre page."""
    import pypdf
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        destinations = {}
        for name, destination in (reader.named_destinations or {}).items():
            try:
                top = getattr(destination, "top", None)
                destinations[str(name)] = (reader.get_destination_page_number(destination) + 1,
                                           round(float(top), 1) if top is not None else None)
            except Exception:
                continue
        texts = None

        def page_texts():
            nonlocal texts
            if texts is None:  # ligatures (« ﬁ » de « Deﬁnition ») et espaces normalisés
                texts = [re.sub(r"\s+", " ", unicodedata.normalize("NFKC", page.extract_text() or "")) for page in reader.pages]
            return texts

        for items, is_toc in ((structure["nodes"], False), (structure["toc"], True)):
            previous = 1
            contents = _contents_pages(page_texts(), structure["toc"]) if is_toc and any(not t.get("anchors") for t in items) else set()
            for item in items:
                page, top = next((destinations[a] for a in item.get("anchors", []) if a in destinations), (None, None))
                if page is None:
                    page = _search(page_texts(), item, previous, contents)
                if page is None and item.get("fallback"):  # numéro du PDF refait introuvable : celui du HTML
                    page = _search(page_texts(), {**item, "label": item["fallback"]}, previous, contents)
                item["page"] = page
                if top is not None:
                    item["top"] = top
                previous = page or previous
        # équations et entrées de la bibliographie : seulement par leurs ancres (renvois cliquables du panneau)
        for item in [*structure.get("equations", {}).values(), *structure.get("bibliography", [])]:
            found = next((destinations[a] for a in item.get("anchors", []) if a in destinations), None)
            if found:
                item["page"], item["top"] = found
        for proof in structure.get("proofBlocks", []):
            target = next((n for n in structure["nodes"] if n["id"] == proof["of"]), None)
            proof["page"] = _proof_page(page_texts(), target) if target and target.get("page") else None
            if proof["page"] and proof["page"] == target.get("page") and target.get("top") is not None:
                proof["top"] = target["top"]  # même page : à la hauteur de l'énoncé (la preuve le suit)
    except Exception:
        pass
    return structure


def _contents_pages(texts, toc):
    """Pages de sommaire : celles où figurent au moins trois titres de sections (et le tiers d'entre eux)."""
    titles = [re.sub(r"^[\dA-Z.]+\s+", "", t.get("title", ""))[:40].lower() for t in toc]
    titles = [t for t in titles if len(t) >= 4]
    found = set()
    for number, text in enumerate(texts, 1):
        lowered = text.lower()
        # une introduction qui annonce le plan cite aussi les sections : seulement une page titrée comme un sommaire
        if not re.search(r"\b(contents|table des mati[eè]res|sommaire|inhaltsverzeichnis)\b", lowered):
            continue
        hits = sum(1 for t in titles if t in lowered)
        if hits >= max(3, len(titles) // 3):
            found.add(number)
    return found


def _proof_page(texts, node):
    """Page de la preuve d'un énoncé : « Proof of <énoncé> » plus loin, sinon le premier « Proof » à partir de
    l'énoncé (sur sa page, après lui)."""
    label = node["label"]
    name, _, value = label.rpartition(" ")
    titled = re.compile(rf"Proof of {re.escape(name)}\s*{re.escape(value)}\b") if value else None
    start = node["page"]
    if titled:
        for number in range(start, len(texts) + 1):
            if titled.search(texts[number - 1]):
                return number
    for number in range(start, len(texts) + 1):
        text = texts[number - 1]
        if number == start:
            at = re.search(rf"{re.escape(name)}\s*{re.escape(value)}", text) if value else None
            text = text[at.end():] if at else text
        if re.search(r"\bProof\b|\bDémonstration\b|\bPreuve\b", text):
            return number
    return start


# Ancres de hyperref possibles pour un énoncé de la version HTML : « lemma.2.3 », « lem.2.3 »…, puis le
# compteur commun le plus courant (« theorem.2.3 », « thm.2.3 ») quand les énoncés partagent un compteur
ANCHOR_WORDS = {
    "theorem": ["theorem", "thm", "theo", "teo"], "lemma": ["lemma", "lem", "lema"], "proposition": ["proposition", "prop"],
    "corollary": ["corollary", "cor", "coro"], "definition": ["definition", "defn", "defi", "dfn", "def"],
    "remark": ["remark", "rem", "rmk"], "example": ["example", "ex", "exa"], "claim": ["claim"],
    "conjecture": ["conjecture", "conj"], "notation": ["notation"], "problem": ["problem", "question"],
}
SECTION_COMMANDS = {0: "chapter", 1: "section", 2: "subsection", 3: "subsubsection"}


def pages_for(structure, pdf_bytes):
    """Pages du PDF des énoncés et sections d'une structure lue dans la version HTML :
    {"pages": {id: page}, "tops": {id: hauteur dans la page}} (hauteur connue seulement par les ancres)."""
    items = {"nodes": [], "toc": [], "proofBlocks": []}
    renumbered = _book_sections(structure)
    by_section = _numbered_by_section(structure, renumbered)
    for node in structure.get("nodes", []):
        label = node["label"]
        number = (re.search(r"(\S+)$", label) or [None, ""])[1].rstrip(".")
        words = ANCHOR_WORDS.get(node["kind"], []) + ["theorem", "thm"]
        anchors = [f"{word}.{number}" for word in words] if re.fullmatch(r"[\dA-Za-z.]+", number or "") else []
        section = node.get("section") or ""
        while section and section not in renumbered:
            section = section.rpartition(".")[0]
        own = STATEMENT_NUMBER.match(label)
        fallback = None
        if by_section and section and own and own.group(1) == renumbered[section][0]:
            pdf = f"{renumbered[section][1]}.{own.group(2)}"
            anchors = [f"{word}.{pdf}" for word in [*words, "subsection"]] + anchors
            label, fallback = f"{label[:own.start(1)]}{pdf}{label[own.end(2):]}", label  # sans ancre : l'un puis l'autre
        items["nodes"].append({"id": node["id"], "label": label, "fallback": fallback, "anchors": anchors})
    for section in structure.get("toc", []):
        title = section.get("title", "")
        number = re.match(r"^(?:Chapter\s+|Appendix\s+)?([\dA-Z]+(?:\.[\dA-Z]+)*)\.?\s", title)  # « Chapter 2 Spaces… », « Appendix 9.B … »
        command = SECTION_COMMANDS.get(section.get("level"), "section")
        if section.get("kind") in ("chapter", "appendix") and number:
            command = section["kind"]
        anchors = [f"{command}.{number.group(1)}", f"appendix.{number.group(1)}"] if number else []
        if section["id"] in renumbered and number:
            pdf = renumbered[section["id"]][1]
            anchors = [f"{'appendix' if section.get('kind') == 'chapter' else 'section'}.{pdf}"] + anchors
            title = re.sub(r"^(?:Chapter|Appendix)\s+", "", title)
            title = f"{pdf}{title[len(number.group(1)) if title.startswith(number.group(1)) else 0:]}"
        items["toc"].append({"id": section["id"], "title": title, "level": section.get("level"), "anchors": anchors})
    locate_pages(items, pdf_bytes)
    located = [item for item in items["nodes"] + items["toc"] if item.get("page")]
    return {"pages": {item["id"]: item["page"] for item in located},
            "tops": {item["id"]: item["top"] for item in located if item.get("top") is not None}}


STATEMENT_NUMBER = re.compile(r"^(?:[^\d\s]\S*\s+)?(\d+(?:\.[A-Z])?)\.(\d+)\b")  # « Proposition 7.10 » ou « 7.9 », titre éventuel après


def _numbered_by_section(structure, renumbered):
    """Énoncés numérotés d'après la section du HTML (« 7.10 » en section 7) et non d'après leur chapitre (« 2.10 ») :
    décidé sur les sections dont le numéro du HTML diffère de celui de leur chapitre (seules à trancher)."""
    votes = {True: 0, False: 0}
    for node in structure.get("nodes", []):
        section = node.get("section") or ""
        while section and section not in renumbered:
            section = section.rpartition(".")[0]
        own = STATEMENT_NUMBER.match(node.get("label", ""))
        if section and own:
            html_number, pdf_number = renumbered[section]
            if html_number != pdf_number.partition(".")[0]:
                votes[own.group(1) == html_number] += 1
    return votes[True] > votes[False]


def _book_sections(structure):
    """Livre dont LaTeXML numérote les sections à la suite (« 7 », 7e section du livre, et ses énoncés « 7.10 ») quand
    le PDF les numérote dans leur chapitre (« 2.3 », « 2.3.10 », ancres section.2.3, subsection.2.3.10…) :
    {id de section ou de chapitre: (numéro du HTML, numéro du PDF)}. Chapitres d'annexes : LaTeXML les numérote à la
    suite (« Chapter 9 », sections « Appendix 9.B »), le PDF par lettre (annexe A, section A.2)."""
    renumbered, chapter, count, letter, letters = {}, None, 0, None, 0
    for entry in structure.get("toc", []) if structure.get("book") else []:
        title = entry.get("title", "")
        number = re.match(r"^(?:Chapter\s+)?(\d+)\.?\s", title)
        appendix = re.match(r"^Appendix\s+(\d+)\.([A-Z])\.?\s", title)
        if entry.get("kind") == "chapter" or entry.get("level") == 0:
            chapter = number.group(1) if number and entry.get("kind") == "chapter" else None
            chapter_id, count, letter = entry["id"], 0, None
        elif appendix and chapter == appendix.group(1):
            if letter is None:
                letter, letters = chr(ord("A") + letters), letters + 1
                renumbered[chapter_id] = (chapter, letter)
            count += 1
            renumbered[entry["id"]] = (f"{chapter}.{appendix.group(2)}", f"{letter}.{count}")
        elif entry.get("kind") == "section" and number and chapter:
            count += 1
            renumbered[entry["id"]] = (number.group(1), f"{chapter}.{count}")
    # sections déjà numérotées dans leur chapitre (« 1 », « 2 »… recommençant à chaque chapitre) : rien à refaire
    return renumbered if any(html.rpartition(".")[2] != pdf.rpartition(".")[2] for html, pdf in renumbered.values()) else {}


def _search(texts, item, start=1, skip=frozenset()):
    """Page d'un énoncé ou d'une section d'après le texte du PDF, à partir de la page `start` (puis avant, si rien
    plus loin), en sautant les pages `skip` (sommaire)."""
    label = item.get("label") or item.get("title") or ""
    if not label:
        return None
    label = unicodedata.normalize("NFKC", label)
    if "level" in item:  # section : son titre
        words = re.escape(re.sub(r"^[\dA-Z.]+\s+", "", label)[:40]).replace(r"\ ", r"\s*")
        pattern = re.compile(words, re.I)
    else:  # « Theorem 2.3. » en tête d'énoncé, sinon toute mention
        name, _, value = label.rpartition(" ")
        pattern = re.compile(rf"{re.escape(name)}\s*{re.escape(value)}\s*[.(:]") if value else re.compile(re.escape(label))
    order = list(range(max(1, start), len(texts) + 1)) + list(range(1, max(1, start)))
    for number in order:
        if number not in skip and pattern.search(texts[number - 1]):
            return number
    return None
