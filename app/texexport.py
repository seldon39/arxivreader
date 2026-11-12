"""Copie annotée de la source TeX d'un article de l'ordinateur (source « local-tex ») : toutes les notes de l'article
insérées dans une COPIE du dossier du projet, à côté de l'endroit qu'elles concernent ; l'original n'est jamais
modifié. Téléchargée en .zip. Aucun appel à l'IA.

Deux formes, au choix :
- « comments » : blocs de commentaires « % [arXivReader] … % [/arXivReader] » — le PDF compilé ne change pas ;
- « todo » : les mêmes blocs, plus une courte note en marge (\\todo du paquet todonotes, ajouté au préambule de la
  copie) — visible dans le PDF. Mise en page « wide » (par défaut) : texte décalé vers la gauche et page élargie au
  besoin pour une colonne de notes d'au moins 4,5 cm, notes en 7 pt, largeur du texte inchangée (lignes coupées comme
  dans l'original ; WIDE_LAYOUT, WIDE_TODO) ; « keep » : notes dans la marge telle qu'elle est. Notes longues
  (option « endnotes », par défaut) : la marge n'en montre que le début et un renvoi « → note N, p. X » ; leur texte
  entier est dans une section « Notes de relecture (arXivReader) » ajoutée à la fin du document, par \\AtEndDocument (_end_section, ENDNOTE_MACROS).

Place des notes :
- énoncés et preuves (notes, états de lecture) : avant leur \\begin{…}, retrouvés par leur identifiant, celui que
  l'import calcule (même analyse de la source, positions gardées : texsource.expand_with_origins,
  texstructure.extract(positions=…)) ;
- passages surlignés : leur texte cherché dans la source (lettres seulement, tolérant au texte du PDF : ligatures,
  coupures, formules), la page départageant les passages répétés (SyncTeX) ;
- zones et notes épinglées du PDF : la ligne de la source à cet endroit de la page (SyncTeX, gardé par la compilation
  dans paper.synctex.gz), sinon après le dernier énoncé situé au-dessus sur la page ;
- fiche de lecture, marque-pages et notes non localisées : en tête du fichier principal.
Seules des lignes sont ajoutées aux fichiers .tex de la copie (et, en mode « todo », le paquet todonotes dans le
préambule) ; jamais dans un environnement verbatim, et en mode « todo » jamais dans une formule, un flottant, un
tableau, une figure TikZ ni un groupe {…} ouvert (note de bas de page, légende, titre de section).
"""

import bisect
import json
import os
import posixpath
import re
import stat
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path

from . import annotations, export, synctex, texfile, texstructure
from .library import paper_dir
from .texsource import _expand, _strip_comments, expand_with_origins

MODES = ("comments", "todo")
LANGUAGES = ("fr", "en")
MAX_COPY_FILES = 3000
MAX_COPY_BYTES = 300_000_000
MAX_QUOTE = 200          # caractères du passage cité dans le bloc de commentaires
MAX_SHORT = 300          # caractères de la note en marge (\\todo)
MAX_MATH = 200           # formule $…$ gardée telle quelle dans la note en marge
MARK, END_MARK = "[arXivReader]", "[/arXivReader]"
MACRO = "\\arxivreadertodo"  # nos notes en marge : \\todo de todonotes, seulement hors des boîtes (\\ifinner)
# … et hors d'une formule (\\ifmmode : formule ouverte par une macro de l'auteur que l'analyse n'a pas reconnue)
GUARD = "\\ifmmode\\else\\ifinner\\else"

SKIPPED_DIRS = {"__MACOSX", "node_modules"}
# toujours produits par la compilation
BUILD_FILES = (".fls", ".fdb_latexmk", ".synctex", ".synctex.gz", ".synctex(busy)", ".xdv")
# produits par la compilation d'un fichier .tex du projet du même nom (main.aux…) ; gardés sinon (run.out lu par
# \\verbatiminput, sup.aux de \\externaldocument…)
ARTEFACTS = (".aux", ".log", ".out", ".toc", ".lof", ".lot", ".blg", ".bcf", ".run.xml", ".nav", ".snm", ".vrb",
             ".dvi", ".idx", ".ind", ".ilg", ".glo", ".gls", ".glg", ".bak", ".tmp")
MAX_VISITED = 50_000     # entrées parcourues dans le dossier du projet, ignorées comprises
LARGE_FILES, LARGE_BYTES = 300, 50_000_000  # au-delà : avertissement (dossier qui contient sans doute autre chose)
VERBATIM = {"verbatim", "verbatim*", "Verbatim", "Verbatim*", "BVerbatim", "LVerbatim", "spverbatim", "lstlisting",
            "minted", "comment", "alltt", "filecontents", "filecontents*", "luacode", "luacode*", "tcblisting",
            "sageblock", "sagesilent", "pycode"}
MATH = {"equation", "align", "gather", "multline", "eqnarray", "flalign", "alignat", "xalignat", "xxalignat",
        "displaymath", "math", "split", "dmath", "dgroup", "IEEEeqnarray"}
BOXES = {"figure", "table", "wrapfigure", "wraptable", "sidewaysfigure", "sidewaystable", "subfigure", "subtable",
         "tabular", "tabularx", "tabulary", "longtable", "tabu", "array", "tikzpicture", "pgfpicture", "picture",
         "tikzcd", "pspicture", "minipage", "multicols", "tcolorbox", "mdframed", "framed", "shaded", "boxedminipage",
         "adjustbox", "xy"}
# listes : rien entre leur \begin et leur premier \item (ou \bibitem)
LISTS = {"itemize", "enumerate", "description", "thebibliography", "list", "trivlist", "compactitem", "compactenum",
         "compactdesc", "inparaenum", "inparaitem", "asparaenum", "asparaitem", "asparadesc", "steps", "theindex"}
COLORS = {"yellow": "yellow!40", "green": "green!30", "blue": "blue!20", "pink": "pink!30"}
NOTE_COLOR = "orange!30"

WORDS = {
    "fr": {"proof": "Preuve de {label}", "highlight": "Passage surligné, page {page}", "htmlHighlight": "Passage surligné",
           "zone": "Zone de la page {page}", "pin": "Note épinglée, page {page}", "htmlNote": "Note",
           "sheet": "Fiche de lecture", "bookmarks": "Marque-pages", "page": "page {page}",
           "approx": "emplacement approximatif", "ai": "{who} (proposé, non vérifié)", "local": "IA locale",
           "quote": "« {quote} »",
           "unplacedOne": "Note non localisée : {title}",
           "reason": {"lost": "perdue à la recompilation", "notFound": "énoncé introuvable dans la source",
                      "quote": "passage introuvable dans la source", "position": "position inconnue dans la source",
                      "outside": "fichier hors du dossier du projet"},
           "headTitle": "Copie annotée par arXivReader le {date} — {mode}",
           "modes": {"comments": "notes en commentaires % (le PDF ne change pas)",
                     "todo": "notes en commentaires % et en marge (\\todo, paquet todonotes)"},
           "headCounts": "{total} notes : {exact} placées exactement, {approx} approximativement, {unplaced} non localisées, {header} ici en tête.",
           "headRemove": ["Chaque note est un bloc « % [arXivReader] … » terminé par « % [/arXivReader] » : cherchez",
                          "« [arXivReader] » pour les retrouver ou les retirer (les lignes \\arxivreadertodo et le bloc",
                          "ajouté avant \\begin{document} portent aussi cette marque). Le projet d'origine n'a pas été modifié."],
           "preamble": "marges pour les notes de la copie annotée (à retirer avec elles)",
           "layout": ["Mise en page des notes en marge : le texte est décalé vers la gauche (marge de gauche ramenée à",
                      "1,5 cm, jamais agrandie) et la page n'est élargie à droite que si la colonne des notes ferait",
                      "moins de 4,5 cm (colonne limitée à 6 cm : lignes de notes faciles à lire) ; sur deux colonnes,",
                      "le texte n'est pas décalé et la page est élargie des deux côtés au besoin. Notes en police de",
                      "7 pt (\\arxivreaderNoteSize). La largeur du texte ne change pas : les lignes et les pages sont",
                      "coupées comme dans l'original (rares exceptions : comme tout \\marginpar, une note peut",
                      "déplacer une coupure de page, par exemple juste avant une formule centrée qui en suit une",
                      "autre). Après \\twocolumn, \\newgeometry ou \\restoregeometry, la mise en page est refaite.",
                      "Ce que le document met lui-même dans la marge de gauche (étiquettes, numéros de ligne) y a moins",
                      "de place. Une page élargie n'a plus le format A4 ou Letter : imprimée « ajustée à la page »,",
                      "tout y est réduit d'autant (texte et notes) ; à l'écran, zoomer sur la largeur de la page.",
                      "Valeurs obtenues (largeur des notes, décalage, élargissement de la page) : ligne",
                      "« arXivReader [mise en page] » du journal de compilation (.log)."],
           "layoutLog": "arXivReader [mise en page] notes en marge larges de {width} (police 7 pt), texte décalé de {shift} "
                        "vers la gauche, page élargie de {more}, largeur du texte inchangée",
           "readmeName": "LISEZMOI-arXivReader.txt", "folder": "{name} (annoté)", "zip": "{name} annoté.zip",
           "readme": ["Copie annotée par arXivReader le {date}", "",
                      "Ce dossier est une copie du projet {name} : les notes de l'article y sont insérées dans les",
                      "fichiers .tex, à côté des énoncés et des passages qu'elles concernent. Le projet d'origine n'a",
                      "pas été modifié.", "", "Forme choisie : {mode}.", "",
                      "{counts}", "",
                      "Retirer les notes : chaque note est un bloc de commentaires qui commence par « % [arXivReader] »",
                      "et finit par « % [/arXivReader] » ; en mode notes en marge, les lignes \\arxivreadertodo et le",
                      "bloc ajouté juste avant \\begin{{document}} portent aussi la marque « [arXivReader] ». Cherchez",
                      "« [arXivReader] » dans les fichiers .tex."],
           "refs": "Renvois", "endTitle": "Notes de relecture (arXivReader)", "endPage": "en marge, page",
           "endnotes": ["Notes longues ({n}) : la marge n'en montre que le début, suivi d'un renvoi « → note N, p. X » ;",
                        "leur texte entier est dans « Notes de relecture (arXivReader) », sur les dernières pages (ajoutées",
                        "juste avant \\end{document}, après la bibliographie : les pages d'avant ne changent pas). Les",
                        "numéros de page des renvois demandent deux compilations (latexmk les fait) : après une seule, ils",
                        "s'affichent « ?? ». Pour tout garder en marge : décocher « Renvoyer les notes longues en fin de",
                        "document » dans arXivReader."],
           "endComment": "notes longues en entier (renvois « → note N » des notes en marge)",
           "readmeWarnings": "Avertissements :", "readmeUnplaced": "Notes non localisées (en tête de {main}) :",
           "counts": "{total} notes : {exact} placées exactement, {approx} approximativement, {unplaced} non localisées, {header} en tête du fichier principal (fiche de lecture, marque-pages).",
           "warn": {"noSynctex": "Pas de données SyncTeX pour ce PDF : recompilez l'article dans arXivReader pour placer plus précisément les zones et notes épinglées du PDF.",
                    "staleSynctex": "La source a changé depuis la dernière compilation : recompilez l'article dans arXivReader pour placer plus précisément les notes du PDF.",
                    "outside": "Fichiers lus hors du dossier du projet, non copiés : {files}.",
                    "notCopied": "Fichiers de la source absents de la copie (cachés ou ignorés) : {files}.",
                    "latin1": "Fichiers en Latin-1 : les caractères des notes hors Latin-1 y sont remplacés par « ? » ({files}).",
                    "disabled": "todonotes est chargé avec l'option disable : les notes en marge n'apparaîtront pas dans le PDF.",
                    "ownTodo": "Le document définit sa propre commande \\todo : elle est gardée, les notes en marge utilisent \\arxivreadertodo.",
                    "noPositions": "La source n'a pas pu être analysée avec ses positions : les notes sont en tête du fichier principal.",
                    "noDocument": "\\begin{document} introuvable : pas de notes en marge, seulement des commentaires.",
                    "sharedLine": "\\documentclass et \\begin{document} sont sur la même ligne : pas de notes en marge, seulement des commentaires.",
                    "large": "Le dossier du projet « {folder} » est copié en entier ({files} fichiers, {size} Mo) : vérifiez qu'il ne contient que cet article avant de partager la copie."}},
    "en": {"proof": "Proof of {label}", "highlight": "Highlighted passage, page {page}", "htmlHighlight": "Highlighted passage",
           "zone": "Region of page {page}", "pin": "Pinned note, page {page}", "htmlNote": "Note",
           "sheet": "Reading notes", "bookmarks": "Bookmarks", "page": "page {page}",
           "approx": "approximate location", "ai": "{who} (suggested, unverified)", "local": "local AI",
           "quote": "“{quote}”",
           "unplacedOne": "Note not located: {title}",
           "reason": {"lost": "lost after recompiling", "notFound": "statement not found in the source",
                      "quote": "passage not found in the source", "position": "position unknown in the source",
                      "outside": "file outside the project folder"},
           "headTitle": "Annotated copy made by arXivReader on {date} — {mode}",
           "modes": {"comments": "notes as % comments (the PDF does not change)",
                     "todo": "notes as % comments and in the margin (\\todo, todonotes package)"},
           "headCounts": "{total} notes: {exact} placed exactly, {approx} approximately, {unplaced} not located, {header} here at the top.",
           "headRemove": ["Each note is a block \"% [arXivReader] ...\" ending with \"% [/arXivReader]\": search for",
                          "\"[arXivReader]\" to find or remove them (the \\arxivreadertodo lines and the block added",
                          "before \\begin{document} carry this mark too). The original project was not modified."],
           "preamble": "margins for the notes of the annotated copy (remove with them)",
           "layout": ["Layout of the margin notes: the text is shifted to the left (left margin brought down to 1.5 cm,",
                      "never enlarged) and the page is widened on the right only if the column of the notes would be",
                      "narrower than 4.5 cm (column limited to 6 cm: note lines easy to read); on two columns, the text",
                      "is not shifted and the page is widened on both sides if needed. Notes in a 7 pt font",
                      "(\\arxivreaderNoteSize). The width of the text does not change: the lines and the pages break as",
                      "in the original (rare exceptions: like any \\marginpar, a note can move a page break, for",
                      "instance just before a displayed formula that follows another one). After \\twocolumn,",
                      "\\newgeometry or \\restoregeometry, the layout is computed again. What the document itself puts",
                      "in the left margin (labels, line numbers) has less room there. A widened page is no longer A4 or",
                      "Letter: printed \"fit to page\", everything on it is reduced accordingly (text and notes); on",
                      "screen, zoom to the width of the page. Values obtained (width of the notes, shift, widening of",
                      "the page): line \"arXivReader [layout]\" of the compilation log (.log)."],
           "layoutLog": "arXivReader [layout] margin notes {width} wide (7 pt font), text shifted {shift} to the left, "
                        "page widened by {more}, text width unchanged",
           "readmeName": "README-arXivReader.txt", "folder": "{name} (annotated)", "zip": "{name} annotated.zip",
           "readme": ["Annotated copy made by arXivReader on {date}", "",
                      "This folder is a copy of the project {name}: the notes of the paper are inserted in its .tex",
                      "files, next to the statements and passages they are about. The original project was not",
                      "modified.", "", "Chosen form: {mode}.", "",
                      "{counts}", "",
                      "Removing the notes: each note is a comment block starting with \"% [arXivReader]\" and ending",
                      "with \"% [/arXivReader]\"; in margin-note mode, the \\arxivreadertodo lines and the block added",
                      "just before \\begin{{document}} carry the mark \"[arXivReader]\" too. Search for \"[arXivReader]\"",
                      "in the .tex files."],
           "refs": "References", "endTitle": "Review notes (arXivReader)", "endPage": "in the margin, page",
           "endnotes": ["Long notes ({n}): the margin shows only their beginning, followed by a reference \"→ note N, p. X\";",
                        "their full text is in \"Review notes (arXivReader)\", on the last pages (added just before",
                        "\\end{document}, after the bibliography: the pages before do not change). The page numbers of the",
                        "references need two compilations (latexmk runs them): after a single one, they show \"??\". To keep",
                        "everything in the margin: untick \"Continue long notes at the end of the document\" in arXivReader."],
           "endComment": "long notes in full (references \"→ note N\" of the margin notes)",
           "readmeWarnings": "Warnings:", "readmeUnplaced": "Notes not located (at the top of {main}):",
           "counts": "{total} notes: {exact} placed exactly, {approx} approximately, {unplaced} not located, {header} at the top of the main file (reading notes, bookmarks).",
           "warn": {"noSynctex": "No SyncTeX data for this PDF: recompile the paper in arXivReader to place the PDF regions and pinned notes more precisely.",
                    "staleSynctex": "The source changed since the last compilation: recompile the paper in arXivReader to place the PDF notes more precisely.",
                    "outside": "Files read outside the project folder, not copied: {files}.",
                    "notCopied": "Source files missing from the copy (hidden or skipped): {files}.",
                    "latin1": "Latin-1 files: characters of the notes outside Latin-1 are replaced by \"?\" there ({files}).",
                    "disabled": "todonotes is loaded with the disable option: the margin notes will not appear in the PDF.",
                    "ownTodo": "The document defines its own \\todo command: it is kept, the margin notes use \\arxivreadertodo.",
                    "noPositions": "The source could not be analysed with its positions: the notes are at the top of the main file.",
                    "noDocument": "\\begin{document} not found: no margin notes, comments only.",
                    "sharedLine": "\\documentclass and \\begin{document} are on the same line: no margin notes, comments only.",
                    "large": "The project folder \"{folder}\" is copied in full ({files} files, {size} MB): check that it holds only this paper before sharing the copy."}},
}
ERRORS = {"fr": {"main": "Fichier principal introuvable : {path}",
                 "tooBig": "Le dossier du projet est trop volumineux : placez le fichier .tex dans un dossier à part",
                 "unreadable": "Le dossier du projet n'a pas pu être lu : {error}",
                 "unreadableFile": "Ce fichier du projet ne peut pas être lu : {path}",
                 "changed": "Le projet a été modifié pendant la copie : réessayez",
                 "params": "Forme de copie, mise en page, renvoi des notes longues ou langue inconnus"},
          "en": {"main": "Main file not found: {path}",
                 "tooBig": "The project folder is too large: put the .tex file in a folder of its own",
                 "unreadable": "The project folder could not be read: {error}",
                 "unreadableFile": "This file of the project cannot be read: {path}",
                 "changed": "The project was modified during the copy: try again",
                 "params": "Unknown copy form, layout, long-note option or language"}}


class ExportError(ValueError):
    """Copie impossible ; le message est déjà dans la langue demandée."""


def _words(language):
    return WORDS.get(language, WORDS["en"])


# ---------- Lignes des fichiers ----------

NEWLINE = re.compile(r"\r\n|\r|\n")  # fins de ligne de TeX (celles que compte SyncTeX)


class Lines:
    """Débuts de ligne d'un texte (lignes comptées à partir de 0)."""

    def __init__(self, text):
        self.text = text
        self.starts = [0] + [m.end() for m in NEWLINE.finditer(text)]

    def __len__(self):
        return len(self.starts)

    def of(self, offset):
        return bisect.bisect_right(self.starts, max(0, offset)) - 1

    def content(self, index):
        if index >= len(self.starts):
            return ""
        end = self.starts[index + 1] if index + 1 < len(self.starts) else len(self.text)
        return NEWLINE.sub("", self.text[self.starts[index]:end])


# ---------- Où une ligne peut être ajoutée (source d'origine, commentaires compris) ----------

SCAN = re.compile(r"%|\\verb(?![A-Za-z])\*?|\\lstinline(?![A-Za-z])|\\begin\s*\{([^{}]*)\}|\\end\s*\{([^{}]*)\}|\\\[|\\\]|\\\(|\\\)|\$\$|\$"
                  r"|\\([A-Za-z@]+)|\\.|[{}\[\]]", re.S)
# définition d'une macro : son nom n'est pas un emploi
DEFINING = re.compile(r"\\(?:(?:re|provide)?newcommand|DeclareRobustCommand|[egx]?def|let)\*?\s*\{?\s*$")
# dans le corps d'une définition (\newcommand{\be}{\begin{equation}}) : un \begin ou \end qui n'ouvre ni ne ferme rien ici
IN_DEFINITION = re.compile(r"\\(?:(?:re|provide)?newcommand|DeclareRobustCommand|[egx]?def)\*?\s*\{?\s*\\[A-Za-z@]+\s*\}?\s*"
                           r"(?:\[[^\]]*\]\s*)*(?:#\d\s*)*\{[^{}]*$")
LIST_ITEMS = {"item", "bibitem"}
# macros de l'auteur qui ouvrent ou ferment un environnement (\newcommand{\be}{\begin{equation}}, \def\ee{\end{equation}})
ALIAS = re.compile(r"\\(?:(?:(?:re|provide)?newcommand|DeclareRobustCommand)\*?\s*\{?\s*\\([A-Za-z@]+)\s*\}?\s*(?:\[[^\]]*\]\s*){0,2}"
                   r"|[egx]?def\s*\\([A-Za-z@]+)\s*(?:#\d\s*)*)"
                   r"\{\s*(?:\\begin\s*\{([^{}]+)\}|[^{}]*?\\end\s*\{([^{}]+)\}\s*\})")


def aliases(text):
    """{nom: (« begin » | « end », environnement)} des macros qui ouvrent ou ferment une formule, un flottant, une
    boîte ou une liste (`text` sans commentaires)."""
    found = {}
    for match in ALIAS.finditer(text):
        name = match.group(1) or match.group(2)
        env = (match.group(3) or match.group(4) or "").strip()
        if env.rstrip("*") in MATH or env.rstrip("*") in BOXES or env.rstrip("*") in LISTS:
            found[name] = ("begin" if match.group(3) else "end", env)
    return found


def scan(text, body_from_start, macros=None):
    """Pour chaque ligne i (et la fin du fichier) : (commentaire possible avant la ligne i, note en marge possible
    avant la ligne i). Commentaire : hors verbatim. Note en marge : de plus dans le corps du document, hors formule,
    flottant, tableau, figure, boîte, hors d'une liste avant son premier \\item, et hors d'un groupe {…} ou d'un
    argument […] d'environnement ouvert. `body_from_start` : le fichier commence dans le corps du document (fichier
    inclus après \\begin{document}) ; `macros` : macros de l'auteur qui ouvrent ou ferment un environnement (aliases)."""
    macros = macros or {}
    lines = Lines(text)
    states = []
    verbatim, envs, braces, brackets = None, [], 0, 0
    lists = []  # listes ouvertes : premier \item vu ?
    display = inline = dollars = False
    body = body_from_start

    def safe():
        return (verbatim is None and body and not envs and not display and not inline and not dollars
                and braces == 0 and brackets == 0 and all(lists))

    pending = False  # « \begin{…} » vu : un « [ » qui suit ouvre son argument optionnel
    for index in range(len(lines)):
        states.append((verbatim is None, safe()))
        line = lines.content(index)
        pos = 0
        if verbatim is not None:
            found = re.search(r"\\end\s*\{" + re.escape(verbatim) + r"\}", line)
            if not found:
                continue
            verbatim, pos = None, found.end()
        while True:
            match = SCAN.search(line, pos)
            if not match:
                if line[pos:].strip():
                    pending = False
                break
            token = match.group(0)
            was_pending, pending = pending and not line[pos:match.start()].strip(), False
            pos = match.end()
            if token == "%":
                break
            if token.startswith("\\verb") or token == "\\lstinline":
                if pos >= len(line):
                    break
                closing = "}" if line[pos] == "{" else line[pos]
                end = line.find(closing, pos + 1)
                pos = end + 1 if end >= 0 else len(line)
                continue
            begin, finish = match.group(1), match.group(2)
            word = match.group(3)
            if word is not None:
                if word in LIST_ITEMS:
                    if lists:
                        lists[-1] = True
                    continue
                if word not in macros or DEFINING.search(line[:match.start()]):
                    continue
                kind, env = macros[word]
                begin, finish = (env, None) if kind == "begin" else (None, env)
            elif (begin is not None or finish is not None) and IN_DEFINITION.search(line[:match.start()]):
                continue
            if begin is not None:  # \begin{…}
                name = begin.strip()
                if name in VERBATIM:
                    found = re.search(r"\\end\s*\{" + re.escape(name) + r"\}", line[pos:])
                    if not found:
                        verbatim = name
                        break
                    pos += found.end()
                    continue
                if name == "document":
                    body = True
                elif name.rstrip("*") in MATH or name.rstrip("*") in BOXES:
                    envs.append(name)
                elif name.rstrip("*") in LISTS:
                    lists.append(False)
                pending = True
                continue
            if finish is not None:  # \end{…}
                name = finish.strip()
                if name == "document":
                    body = False
                elif name in envs:
                    del envs[len(envs) - 1 - envs[::-1].index(name):]
                elif name.rstrip("*") in LISTS and lists:
                    lists.pop()
                continue
            math_env = any(e.rstrip("*") in MATH for e in envs)
            if token == "\\[":
                display = True
            elif token == "\\]":
                display = False
            elif token == "\\(":
                inline = True
            elif token == "\\)":
                inline = False
            elif token == "$$":
                if not math_env and not display and not inline:
                    dollars = not dollars
            elif token == "$":
                if not math_env and not display and not dollars:
                    inline = not inline
            elif token == "{":
                braces += 1
            elif token == "}":
                braces = max(0, braces - 1)
            elif token == "[":
                if was_pending or brackets:
                    brackets += 1
            elif token == "]":
                brackets = max(0, brackets - 1)
    states.append((verbatim is None, safe()))
    return states


def _climb(states, line, which):
    """Ligne sûre la plus proche à partir de `line` en remontant (0 : commentaire, 1 : note en marge), ou None."""
    while line >= 0:
        if line < len(states) and states[line][which]:
            return line
        line -= 1
    return None


# ---------- Texte des notes ----------

def _assistant(note):
    """(nom de l'assistant, texte sans son en-tête) si la note commence par l'en-tête d'un assistant
    (« **Claude** — *non vérifié* »), sinon (None, texte)."""
    from .ai import ASSISTANT_NOTE
    text = note or ""
    if not ASSISTANT_NOTE.match(text):
        return None, text
    name = re.match(r"\s*\*\*([^*\n]+)\*\*", text)
    rest = text.lstrip().partition("\n")[2]
    return (name.group(1).strip() if name else "Claude"), rest.strip("\n")


def _who(item, words):
    """Assistant qui a proposé l'annotation (colonne author ou en-tête de la note) et texte de la note, ou (None, texte)."""
    named, text = _assistant(item.get("note"))
    author = item.get("author")
    if author:
        return (words["local"] if author == "local" else export.AUTHORS.get(author, author.capitalize())), text
    if named:
        for key, value in export.AUTHORS.items():
            if value.lower() in named.lower():
                return value, text
        if named in ("IA locale", "local AI"):
            return words["local"], text
        return named.split(" · ")[0], text
    return None, text


# coupure de fin de ligne du texte du PDF (pdf.js) entre deux minuscules : « com-\npact » → « compact »
LINE_HYPHEN = re.compile(r"(?<=[^\W\d_])-[ \t]*(?:\r\n|\r|\n)\s*(?=[^\W\d_])")


def one_line(text, limit=None):
    text = LINE_HYPHEN.sub(lambda m: "" if text[m.start() - 1].islower() and text[m.end()].islower() else m.group(0), text or "")
    text = " ".join(text.split())
    if limit and len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def comment_block(entry, words, known=None):
    """Lignes du bloc de commentaires d'une note (jamais interprétées par TeX : chaque ligne commence par %) ; avec les
    renvois du document (`known`) : une ligne qui donne leurs commandes (« (4.75) = \\eqref{…} »)."""
    title = entry["title"]
    if entry.get("state"):
        title += f" — {entry['state']}"
    if entry.get("who"):
        title += " — " + words["ai"].format(who=entry["who"])
    lines = [f"% {MARK} {one_line(title)}"]  # sur une ligne : ni \r ni autre fin de ligne qui ferait sortir du commentaire
    if entry.get("quote"):
        lines.append("% " + words["quote"].format(quote=one_line(entry["quote"], MAX_QUOTE)))
    for line in (entry.get("note") or "").strip("\n").splitlines() if (entry.get("note") or "").strip() else []:
        lines.append(f"% {line}" if line.strip() else "%")
    refs = ref_summary(f"{entry.get('quote') or ''}\n{entry.get('note') or ''}", known) if known is not None else ""
    if refs:
        lines.append(f"% {words['refs']} : {one_line(refs)}")
    lines.append(f"% {END_MARK}")
    return lines


# Commandes sûres dans une formule recopiée dans une note en marge (sinon : utilisées par la source elle-même)
MATH_WORDS = set("""alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi
rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi
Omega frac sqrt sum prod int oint lim limsup liminf infty leq geq le ge neq ne in notin subset subseteq supset supseteq
cup cap bigcup bigcap setminus to mapsto rightarrow leftarrow Rightarrow Leftarrow leftrightarrow Leftrightarrow
longrightarrow times cdot cdots ldots dots vdots ddots partial nabla pm mp circ otimes oplus forall exists neg
emptyset varnothing mid parallel perp langle rangle lfloor rfloor lceil rceil left right big Big bigg Bigg hat bar
tilde widetilde widehat overline underline vec dot ddot sin cos tan exp log ln det dim ker max min sup inf deg gcd
arg mathrm mathbf mathit mathsf mathtt mathcal ell hbar Re Im wedge vee approx sim simeq cong equiv propto ll gg
prime star ast quad qquad colon""".split())
FORBIDDEN_WORDS = {"par", "begin", "end", "section", "subsection", "subsubsection", "chapter", "part", "paragraph",
                   "subparagraph", "item", "label", "input", "include", "newcommand", "renewcommand", "def", "edef",
                   "gdef", "xdef", "let", "footnote", "caption", "todo", "marginpar", "verb", "url", "href", "newpage",
                   "clearpage", "documentclass", "usepackage", "includegraphics", "index", "bibitem", "bibliography",
                   "maketitle", "tableofcontents", "vspace", "hspace", "newline", "linebreak", "pagebreak", "write",
                   "immediate", "special", "catcode", "csname", "endcsname", "expandafter", "noexpand", "endinput",
                   "the", "relax", "arxivreadertodo", "item", "cite", "ref", "eqref", "cref", "Cref"}
TEXT_CHARS = {"’": "'", "‘": "'", "‚": ",", "“": "\"", "”": "\"", "„": "\"", "«": "\"", "»": "\"", "–": "--",
              "—": "---", "…": "...", "\u00a0": " ", "\u202f": " ", "\u2009": " ", "•": "-", "−": "-"}
ESCAPES = {"\\": "\\textbackslash{}", "{": "\\{", "}": "\\}", "$": "\\$", "&": "\\&", "#": "\\#",
           "^": "\\textasciicircum{}", "_": "\\_", "%": "\\%", "~": "\\textasciitilde{}"}
LATIN = set("àâäáãåçéèêëíìîïñóòôöõúùûüýÿÀÂÄÁÃÅÇÉÈÊËÍÌÎÏÑÓÒÔÖÕÚÙÛÜÝæÆøØœŒß")


def _text_chars(text):
    """Texte sûr pour LaTeX dans une note en marge : caractères spéciaux échappés ; hors ASCII, seulement les lettres
    accentuées du Latin-1 (déclarées par LaTeX quel que soit le codage des polices), le reste ramené à l'ASCII."""
    out = []
    for char in text:
        if char in ESCAPES:
            out.append(ESCAPES[char])
        elif char in TEXT_MATH:
            out.append(f"\\ensuremath{{{TEXT_MATH[char]}}}")
        elif char in TEXT_CHARS:
            out.append(TEXT_CHARS[char])
        elif 32 <= ord(char) < 127 or char in LATIN:
            out.append(char)
        elif char in "\t\n\r":
            out.append(" ")
        else:
            plain = "".join(c for c in unicodedata.normalize("NFKD", char) if 32 <= ord(c) < 127)
            out.append(plain or "?")
    return "".join(out)


BREAK = "\\allowbreak{}"   # coupure naturelle : après / _ { } ) ] , ; dans un long mot (« ([ma_generalized_2008]) »)
SPLIT = "\\arxivreaderBreak{}"  # coupure de secours dans un long mot, sans trait d'union (définie avec les marges)
# pénalité 1000 (plus forte qu'une coupure de syllabe ou une ligne lâche, assez faible pour que la somme des
# pénalités d'une longue note ne dépasse pas la limite de TeX) ; ressort infini en fin de ligne (sinon la ligne coupée, sans
# espace, serait infiniment mauvaise et TeX préférerait déborder), annulé quand la ligne n'est pas coupée ; morceau
# suivant précédé d'une pénalité : pas coupé en syllabes au hasard
SPLIT_MACRO = "\\newcommand{\\arxivreaderBreak}{\\nobreak\\hskip0pt plus1fil\\penalty1000\\hskip0pt plus-1fil\\nobreak}"
LONG_TOKEN = 12          # au-delà : mot à couper (la note en marge est étroite)
WORD_RUN = 10            # mot ordinaire : coupure de secours toutes les 10 lettres (TeX coupe d'abord ses syllabes)
OTHER_RUN = 4            # suite de symboles, de capitales, de chiffres… : toutes les 4 caractères
BREAK_AFTER = "/_{})],;"
EDGES = ".,;:!?()[]{}\"'«»“”‘’"
CLOSING = ".,;:!?)]}\"'»”’"


def _natural(piece):
    """Mot ordinaire (lettres, ponctuation autour), pas tout en capitales : TeX sait le couper en syllabes."""
    core = piece.strip(EDGES)
    return bool(core) and core.isalpha() and not core.isupper()


def _breakable(text):
    """_text_chars, et des coupures de ligne permises dans les mots de plus de 12 caractères (la note en marge est
    étroite) : après / _ { } ) ] , ; suivis d'autre chose qu'une ponctuation (et TeX coupe après un trait d'union) ;
    entre ces coupures, coupures de secours (pénalité forte, sans trait d'union) toutes les 10 lettres d'un mot
    ordinaire de plus de 12 lettres, tous les 4 caractères d'une autre suite de plus de 6 (chiffres, capitales,
    symboles), jamais devant une ponctuation fermante."""
    out = []
    for token in re.split(r"(\s+)", text):
        if len(token) <= LONG_TOKEN or token.isspace():
            out.append(_text_chars(token))
            continue
        pieces = re.findall(r"[^/_{})\],;\-–—]+|.", token, re.S)
        for number, piece in enumerate(pieces):
            if len(piece) == 1 and piece in BREAK_AFTER + "-–—":
                out.append(_text_chars(piece))
                following = pieces[number + 1][0] if number + 1 < len(pieces) else "."
                if piece in BREAK_AFTER and following not in BREAK_AFTER + ".:!?":
                    out.append(BREAK)
                continue
            natural = _natural(piece)
            every = WORD_RUN if natural else OTHER_RUN
            split = len(piece.strip(EDGES) if natural else piece) > (LONG_TOKEN if natural else OTHER_RUN + 2)
            run = 0
            for char in piece:
                if split and run >= every and (char.isalpha() if natural else char not in CLOSING):
                    out.append(SPLIT)
                    run = 0
                out.append(_text_chars(char))
                run += 1
    return "".join(out)


# Environnements permis dans une formule d'une note de fin de document (ceux d'amsmath s'il est chargé, voir
# _known_words ; array : noyau de LaTeX), seuls endroits où & et \\ sont permis
MATH_ENVS = {"matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "cases", "aligned",
             "alignedat", "gathered", "split", "array", "subarray"}
KERNEL_ENVS = {"array"}
MAX_LONG_MATH = 2000     # formule gardée telle quelle dans une note de fin de document
# Caractères Unicode des formules → commandes (Claude écrit parfois « λ ≤ ‖x‖ » dans une formule)
MATH_UNICODE = {"≤": "\\le ", "≥": "\\ge ", "≠": "\\ne ", "∈": "\\in ", "∉": "\\notin ", "⊂": "\\subset ",
                "⊆": "\\subseteq ", "⊃": "\\supset ", "⊇": "\\supseteq ", "∩": "\\cap ", "∪": "\\cup ", "→": "\\to ",
                "←": "\\leftarrow ", "↦": "\\mapsto ", "⇒": "\\Rightarrow ", "⇐": "\\Leftarrow ",
                "⇔": "\\Leftrightarrow ", "↔": "\\leftrightarrow ", "∞": "\\infty ", "∑": "\\sum ", "∏": "\\prod ",
                "∫": "\\int ", "∂": "\\partial ", "∇": "\\nabla ", "×": "\\times ", "·": "\\cdot ", "⋅": "\\cdot ",
                "±": "\\pm ", "∓": "\\mp ", "∘": "\\circ ", "⊗": "\\otimes ", "⊕": "\\oplus ", "≈": "\\approx ",
                "∼": "\\sim ", "≃": "\\simeq ", "≅": "\\cong ", "≡": "\\equiv ", "∝": "\\propto ", "≪": "\\ll ",
                "≫": "\\gg ", "∀": "\\forall ", "∃": "\\exists ", "∅": "\\emptyset ", "⟨": "\\langle ",
                "⟩": "\\rangle ", "‖": "\\| ", "−": "-", "…": "\\dots ", "⋯": "\\cdots ", "′": "'", "″": "''",
                "ℓ": "\\ell ", "ℏ": "\\hbar ", "⊥": "\\perp ", "∥": "\\parallel ", "¬": "\\neg ", "∧": "\\wedge ",
                "∨": "\\vee ", "⌊": "\\lfloor ", "⌋": "\\rfloor ", "⌈": "\\lceil ", "⌉": "\\rceil ", "√": "\\sqrt ",
                "∖": "\\setminus ", "⋆": "\\star ", "†": "\\dagger "}
MATH_UNICODE.update({chr(0x3b1 + i): f"\\{name} " for i, name in enumerate(
    "alpha beta gamma delta varepsilon zeta eta theta iota kappa lambda mu nu xi o pi rho varsigma sigma tau upsilon "
    "varphi chi psi omega".split()) if name != "o"})
MATH_UNICODE.update({"ϕ": "\\phi ", "ϵ": "\\epsilon ", "ϑ": "\\vartheta ", "ϱ": "\\varrho ", "ϖ": "\\varpi "})
MATH_UNICODE.update({char: f"\\{name} " for char, name in zip("ΓΔΘΛΞΠΣΥΦΨΩ", "Gamma Delta Theta Lambda Xi Pi Sigma "
                                                                         "Upsilon Phi Psi Omega".split())})
DOUBLE_STRUCK = {"ℝ": "R", "ℂ": "C", "ℕ": "N", "ℤ": "Z", "ℚ": "Q", "ℍ": "H", "ℙ": "P"}
SUPERSCRIPTS = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ", "0123456789+-=()ni"))
SUBSCRIPTS = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₙᵢⱼₖ", "0123456789+-=()nijk"))
# en dehors des formules : lettres grecques et symboles des commandes du noyau de LaTeX (\ensuremath)
TEXT_MATH = {char: command.strip() for char, command in MATH_UNICODE.items()
             if command.startswith("\\") and char not in "…−′″√"}


def _math_unicode(formula, known):
    """Formule avec ses caractères Unicode mathématiques remplacés par des commandes (ℝ : \\mathbb{R} si la source
    emploie \\mathbb, ² : ^{2}…) ; les autres restent (et _safe_math refuse alors la formule)."""
    formula = re.sub("[" + "".join(SUPERSCRIPTS) + "]+", lambda m: "^{" + "".join(SUPERSCRIPTS[c] for c in m.group(0)) + "}", formula)
    formula = re.sub("[" + "".join(SUBSCRIPTS) + "]+", lambda m: "_{" + "".join(SUBSCRIPTS[c] for c in m.group(0)) + "}", formula)
    out = []
    for char in formula:
        if char in MATH_UNICODE:  # (espace après la commande seulement devant une lettre)
            out.append(MATH_UNICODE[char].replace(" ", "\0"))
        elif char in DOUBLE_STRUCK and "mathbb" in known:
            out.append(f"\\mathbb{{{DOUBLE_STRUCK[char]}}}")
        elif char in "\u00a0\u202f\u2009":
            out.append(" ")
        else:
            out.append(char)
    return re.sub("\0(?![A-Za-z])", "", "".join(out)).replace("\0", " ").strip()


def _safe_math(formula, known, envs=False):
    """La formule (sans ses $) peut-elle être recopiée telle quelle dans une note ? Vérifications prudentes :
    accolades équilibrées, rien qui change la structure, commandes connues de LaTeX ou employées par la source ;
    `envs` (notes de fin de document) : environnements de MATH_ENVS disponibles (_known_words), bien imbriqués,
    seuls endroits où & et \\\\ sont permis."""
    limit = MAX_LONG_MATH if envs else MAX_MATH
    if not formula.strip() or len(formula) > limit or any(ord(c) < 32 and c != " " or ord(c) > 126 for c in formula):
        return False
    if re.search(r"[#%]|\^\^|\\$|[\^_]\s*(?:$|\})", formula):
        return False
    depth = 0
    for match in re.finditer(r"\\[{}]|[{}]", formula):
        if match.group(0) in ("{", "}"):
            depth += 1 if match.group(0) == "{" else -1
            if depth < 0:
                return False
    if depth:
        return False
    if len(re.findall(r"\\left\b", formula)) != len(re.findall(r"\\right\b", formula)):
        return False
    stack = []
    for match in re.finditer(r"\\(begin|end)\s*\{([^{}]*)\}|\\\\|&|\\([A-Za-z]+|.)", formula):
        if match.group(1):
            name = match.group(2)
            if not envs or (name not in KERNEL_ENVS and f"env:{name}" not in known):
                return False
            if match.group(1) == "begin":
                stack.append(name)
            elif not stack or stack.pop() != name:
                return False
        elif match.group(0) in ("\\\\", "&"):
            if not stack:
                return False
        else:
            word = match.group(3)
            if not word.isalpha():
                if word not in ",;:!{}| ":
                    return False
            elif word in FORBIDDEN_WORDS or (word not in MATH_WORDS and word not in known):
                return False
    return not stack


MARKDOWN = [(re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"), (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
            (re.compile(r"\*\*(.+?)\*\*", re.S), r"\1"), (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.S), r"\1"),
            (re.compile(r"`+([^`]*)`+"), r"\1"), (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),
            (re.compile(r"^\s{0,3}>\s?", re.M), ""), (re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.M), " / "),
            (re.compile(r"^\s*(?:`{3,}|~{3,}).*$", re.M), "")]
MATH_PIECE = re.compile(r"(\\\$)|(\$\$[\s\S]+?\$\$)|\$([^$]+?)\$")


def short_tex(text, known, limit=MAX_SHORT, word=False):
    """Texte d'une note en marge : Markdown retiré, paragraphes joints, LaTeX échappé, formules $…$ gardées quand
    elles sont sûres ; au plus `limit` caractères (« … » si coupé ; `word` : coupé entre deux mots)."""
    pieces, pos = [], 0  # (texte, formule gardée ?)
    for match in MATH_PIECE.finditer(text or ""):
        pieces.append((text[pos:match.start()], False))
        formula = _math_unicode(match.group(3), known) if match.group(3) is not None else None
        if formula is not None and _safe_math(formula, known) and not re.search(r"\n\s*\n", formula):
            pieces.append((formula, True))
        elif formula is not None and len(formula) > 30:  # longue formule refusée : pas de code illisible en marge
            pieces.append(("[…]", None))
        else:
            pieces.append((match.group(0).replace("\\$", "$"), None))  # (formule refusée : ni Markdown ni renvoi)
        pos = match.end()
    pieces.append(((text or "")[pos:], False))
    merged = []
    for part, is_math in pieces:  # Markdown retiré des morceaux de texte seulement ; renvois : \eqref, \ref, \cite
        if is_math is False:  # (code entre accents graves : sans renvoi)
            for number, segment in enumerate(re.split(r"(`+[^`]*`+)", part)):
                for pattern, replacement in MARKDOWN:
                    segment = pattern.sub(replacement, segment)
                merged += [(segment, False)] if number % 2 else _ref_pieces(segment, known)
            continue
        merged.append((part, bool(is_math)))
    out, used, cut = [], 0, False
    for part, is_math in merged:
        if isinstance(is_math, str):  # renvoi : jamais coupé
            shown = " ".join(part.split())
            if used + len(shown) > limit:
                cut = True
                break
            out.append(is_math)
            used += len(shown)
            continue
        if is_math:
            part = " ".join(part.split())
            if used + len(part) + 2 > limit:
                cut = True
                break
            out.append(f"${part}$")
            used += len(part) + 2
            continue
        part = re.sub(r"\s+", " ", part)
        if out and out[-1].endswith(" ") and part.startswith(" "):
            part = part[1:]
        if used + len(part) > limit:
            room = max(0, limit - used)
            kept = part[:room]
            if word and part[room:room + 1].strip() and kept.rfind(" ") >= 0:  # coupé au milieu d'un mot : avant lui
                kept = kept[:kept.rfind(" ")]
            part, cut = kept.rstrip(), True
        out.append(_breakable(part))
        used += len(part)
        if cut:
            break
    result = "".join(out).strip()
    return result + ("\\dots{}" if cut else "")


# ---------- Notes longues : début en marge, texte entier en fin de document ----------

# Note « longue » (mode « todo », option endnotes) : texte de la note en marge (titre compris, Markdown retiré) de
# plus de LONG_CHARS caractères, ou note de plusieurs paragraphes, avec une liste, une formule centrée ou du code.
# Seuil : environ 7 lignes de la colonne des notes (au-delà, la note recouvre la suivante ou descend sous le bas de la
# page). Police de 7 pt (Computer Modern, Latin Modern) : environ 7,5 caractères par cm, espaces comprises.
# - « wide » : colonne de 4,5 à 6 cm, 5 cm en moyenne (moins le cadre de todonotes) → 7 × 5 × 7,5 ≈ 260 ;
# - « keep » : colonne du document, souvent 2 à 4 cm (marge libre moins 0,3 cm), \scriptsize (7 pt dans un document
#   en 10 pt) ; 3 cm → 7 × 3 × 7,5 ≈ 160.
# Le choix (note longue ou non) fixe la numérotation et la section de fin : fait ici, avec ces largeurs supposées.
# En marge : son début, coupé entre deux mots vers 60 % du seuil (4 lignes environ, de quoi reconnaître la note), au
# moins 40 caractères de la note après son titre, puis le renvoi. « keep » : la largeur réelle de la colonne n'est
# connue qu'à la compilation (marge libre du document) ; colonne de moins de 2,5 cm (marges de 2 à 3 cm, fréquentes
# avec geometry) : début plus court, choisi à la compilation (\arxivreaderFit) : seuil d'une colonne de 2 cm
# (7 × 2 × 7,5 ≈ 105, coupé vers 63) et au moins 20 caractères après le titre (titre jamais coupé : il porte la
# mention de l'assistant et l'emplacement approximatif).
LONG_CHARS = {"wide": 260, "keep": 160}
LONG_CUT = 0.6
LONG_MIN_NOTE = 40
NARROW = {"width": "2.5cm", "chars": 105, "min": 20}
# formule centrée non vide ($$…$$, \[…\]) : « \[ \] » cité dans une phrase n'en est pas une
DISPLAY = re.compile(r"\$\$(.*?)\$\$|\\\[(.*?)\\\]", re.S)
LIST_LINE = re.compile(r"(?m)^\s*(?:[-*+]|\d{1,3}[.)])\s+\S")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
ITEM = re.compile(r"^\s*(?:([-*+])|(\d{1,3})[.)])\s+(.*)$")


def plain_length(text):
    """Longueur du texte d'une note en marge (Markdown retiré, espaces réduites)."""
    for pattern, replacement in MARKDOWN:
        text = pattern.sub(replacement, text)
    return len(" ".join(text.split()))


def is_long(title, note, layout):
    """La note doit-elle être renvoyée en fin de document (voir LONG_CHARS) ?"""
    note = (note or "").strip()
    if not note:
        return False
    display = any((m.group(1) or m.group(2) or "").strip() for m in DISPLAY.finditer(note))
    if re.search(r"\n\s*\n", note) or LIST_LINE.search(note) or display or re.search(r"(?m)^\s*(?:`{3,}|~{3,})", note):
        return True
    return plain_length(f"{title}: {note}") > LONG_CHARS.get(layout, LONG_CHARS["keep"])


INLINE = re.compile(r"(\\\$)|(`+)(.+?)\2|\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|\$([^$]+?)\$"
                    r"|!\[([^\]]*)\]\([^)]*\)|\[([^\]]+)\]\([^)]*\)|\*\*(.+?)\*\*"
                    r"|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.S)


def _formula(raw, known):
    """Formule (sans ses délimiteurs) gardée telle quelle si elle est sûre (_safe_math), sinon None."""
    if re.search(r"\n\s*\n", raw):
        return None
    formula = _math_unicode(" ".join(raw.split()), known)
    return formula if _safe_math(formula, known, envs=True) else None


def inline_tex(text, known):
    """Texte d'un paragraphe en LaTeX : **gras** → \\textbf, *italique* → \\emph, `code` → \\texttt, liens → leur
    texte, formules $…$, \\(…\\) et centrées $$…$$, \\[…\\] gardées si elles sont sûres (sinon montrées en texte),
    le reste échappé."""
    out, pos = [], 0
    for match in INLINE.finditer(text):
        out.append(_plain(text[pos:match.start()], known))
        pos = match.end()
        groups = match.groups()
        if groups[0]:
            out.append("\\$")
        elif groups[2] is not None:
            out.append("\\texttt{" + _breakable(groups[2]) + "}")
        elif groups[3] is not None or groups[4] is not None:
            formula = _formula(groups[3] if groups[3] is not None else groups[4], known)
            out.append(f"\\[{formula}\\]" if formula else _breakable(match.group(0)))
        elif groups[5] is not None or groups[6] is not None:
            formula = _formula(groups[5] if groups[5] is not None else groups[6], known)
            out.append(f"${formula}$" if formula else _breakable(match.group(0)))
        elif groups[7] is not None or groups[8] is not None:
            out.append(inline_tex(groups[7] if groups[7] is not None else groups[8], known))
        elif groups[9] is not None:
            out.append("\\textbf{" + inline_tex(groups[9], known) + "}")
        else:
            out.append("\\emph{" + inline_tex(groups[10], known) + "}")
    out.append(_plain(text[pos:], known))
    return re.sub(r" {2,}", " ", "".join(out)).strip()


# listes des notes de fin : items serrés (sinon \\parsep, \\itemsep du document : enumitem, parskip… les espacent
# parfois d'une ligne entière)
LIST_SPACING = "\\setlength{\\itemsep}{0.25\\baselineskip}\\setlength{\\parskip}{0pt}"


def long_tex(text, known):
    """Texte entier d'une note en LaTeX, en lignes (chacune complète : paragraphe terminé par \\par, \\item, début
    ou fin de liste) : paragraphes, listes « - » / « 1. » (itemize, enumerate), titres en gras, code en
    \\texttt, formules gardées quand elles sont sûres (inline_tex)."""
    out, para, items, kind = [], [], [], None
    blank = False

    def flush_para():
        if para:
            converted = inline_tex(" ".join(para), known)
            if converted:
                out.append(converted + "\\par")
            para.clear()

    def flush_list():
        nonlocal kind
        if kind:
            out.append(f"\\begin{{{kind}}}{LIST_SPACING}")
            out.extend("\\item " + inline_tex(item, known) for item in items)
            out.append(f"\\end{{{kind}}}")
            items.clear()
            kind = None

    lines = (text or "").strip("\n").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        fence = FENCE.match(line)
        if fence:  # bloc de code : lignes en \texttt, jusqu'à la clôture
            flush_para()
            flush_list()
            code = []
            while index < len(lines) and not lines[index].strip().startswith(fence.group(1)):
                code.append(lines[index])
                index += 1
            index += 1
            if code:
                out.append("{\\ttfamily\\noindent " + "\\newline ".join(_breakable(c) or "\\mbox{}" for c in code)
                           + "\\par}")
            blank = False
            continue
        if not line.strip():
            flush_para()
            blank = True
            continue
        line = re.sub(r"^\s{0,3}>\s?", "", line)  # citation : son texte
        item = ITEM.match(line)
        heading = HEADING.match(line)
        if RULE.match(line):
            flush_para()
            flush_list()
            out.append("\\medskip")
        elif heading:
            flush_para()
            flush_list()
            out.append("\\textbf{" + inline_tex(heading.group(1), known) + "}\\par")
        elif item:
            flush_para()
            wanted = "itemize" if item.group(1) else "enumerate"
            if kind != wanted:
                flush_list()
                kind = wanted
            items.append(item.group(3))
        elif kind and not blank:  # suite de l'élément de liste
            items[-1] += " " + line.strip()
        else:
            flush_list()
            para.append(line.strip())
        blank = False
    flush_para()
    flush_list()
    return out


# ---------- Recherche d'un passage dans la source ----------

GREEK = {name: chr(code) for name, code in (
    ("alpha", 0x3b1), ("beta", 0x3b2), ("gamma", 0x3b3), ("delta", 0x3b4), ("epsilon", 0x3b5), ("varepsilon", 0x3b5),
    ("zeta", 0x3b6), ("eta", 0x3b7), ("theta", 0x3b8), ("vartheta", 0x3b8), ("iota", 0x3b9), ("kappa", 0x3ba),
    ("lambda", 0x3bb), ("mu", 0x3bc), ("nu", 0x3bd), ("xi", 0x3be), ("pi", 0x3c0), ("varpi", 0x3c0), ("rho", 0x3c1),
    ("varrho", 0x3c1), ("sigma", 0x3c3), ("varsigma", 0x3c2), ("tau", 0x3c4), ("upsilon", 0x3c5), ("phi", 0x3c6),
    ("varphi", 0x3c6), ("chi", 0x3c7), ("psi", 0x3c8), ("omega", 0x3c9), ("Gamma", 0x393), ("Delta", 0x394),
    ("Theta", 0x398), ("Lambda", 0x39b), ("Xi", 0x39e), ("Pi", 0x3a0), ("Sigma", 0x3a3), ("Upsilon", 0x3a5),
    ("Phi", 0x3a6), ("Psi", 0x3a8), ("Omega", 0x3a9))}
# commandes qui impriment une lettre (hors grec) : pdf.js donne la lettre (ou son équivalent, ramené par _letters_of)
SYMBOL_LETTERS = {"ell": "ℓ", "hbar": "ℏ", "imath": "ı", "jmath": "ȷ", "aleph": "ℵ", "beth": "ℶ", "gimel": "ℷ",
                  "varkappa": "ϰ", "eth": "ð", "i": "ı", "j": "ȷ", "ss": "ß", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ",
                  "o": "ø", "O": "Ø", "l": "ł", "L": "Ł", "aa": "å", "AA": "Å"}
SYMBOL_LETTERS.update({"var" + name: letter for name, letter in GREEK.items() if name[0].isupper()})  # \varGamma…
# opérateurs imprimés en lettres droites (\det → « det » dans le PDF)
OPERATORS = set("""exp log ln lg det sin cos tan cot sec csc sinh cosh tanh coth arcsin arccos arctan max min dim ker
sup inf lim deg arg gcd Pr hom liminf limsup injlim projlim varliminf varlimsup varinjlim varprojlim""".split())
OPERATOR_LETTERS = {"varliminf": "lim", "varlimsup": "lim", "varinjlim": "lim", "varprojlim": "lim"}
# caractères que pdf.js donne pour les polices de TeX et qui ne se ramènent pas d'eux-mêmes à la lettre : Δ (INCREMENT),
# lettres sans point. NABLA (∇) n'est pas une lettre : ignoré des deux côtés, comme \nabla dans la source.
LOOKALIKES = {"\u2206": "δ", "ı": "i", "ȷ": "j"}
# TEX_TOKEN : commandes dont aucun argument n'est imprimé (couleurs, compteurs, longueurs : \\definecolor{c}{rgb}{…},
# \\rule{w}{h}…), puis celles dont l'argument n'est pas imprimé tel quel (renvois, citations, noms d'environnements…)
_ARG = r"\*?\s*(?:\[[^\]]*\]\s*)*\{[^{}]*\}"
_DIMEN = r"-?\s*(?:\d+\.?\d*|\.\d+)\s*(?:true\s*)?(?:pt|mm|cm|em|ex|in|bp|sp|pc|dd|cc|mu|px|fil+)"
TEX_TOKEN = re.compile(r"\\definecolor" + _ARG + r"\s*\{[^{}]*\}\s*\{[^{}]*\}"
                       r"|\\(?:setcounter|addtocounter|setlength|addtolength|settowidth|settoheight|settodepth|"
                       r"numberwithin|fcolorbox|rule)" + _ARG + r"\s*\{[^{}]*\}"
                       r"|\\(?:label|ref|eqref|cref|Cref|autoref|pageref|nameref|cite[A-Za-z]*|index|begin|end|hspace|"
                       r"vspace|includegraphics|bibliography|bibliographystyle|input|include|usepackage|href|"
                       r"documentclass|color|textcolor|colorbox|pagecolor|stepcounter|refstepcounter|pagestyle|"
                       r"thispagestyle|newcounter)" + _ARG +
                       r"|\\(?:[hvm]skip|m?kern)\s*" + _DIMEN + r"(?:\s*(?:plus|minus)\s*" + _DIMEN + r")*"
                       r"|\\([A-Za-z]+)\*?|\\.", re.S)
MACRO_DEPTH = 6          # macros de l'auteur développées les unes dans les autres
MACRO_LETTERS = 200      # lettres gardées d'une macro développée (une macro qui en imprime des milliers : tronquée)
MAX_MACROS = 2000
_LETTERS = {}


def _letters_of(char):
    found = _LETTERS.get(char)
    if found is None:
        found = "".join(c for c in unicodedata.normalize("NFKD", LOOKALIKES.get(char, char)).lower() if c.isalpha())
        found = "".join(unicodedata.normalize("NFKD", c) for c in found)  # « ϕ » → « φ »…
        found = "".join(LOOKALIKES.get(c, c) for c in found)
        _LETTERS[char] = found
    return found


def plain_letters(text):
    """Lettres d'un texte du PDF (pdf.js) : sans accents, ligatures défaites, casse ignorée, ni chiffres ni symboles."""
    return "".join(_letters_of(char) for char in text or "")


# textes produits par LaTeX devant une légende ou un énoncé (« Table 1: », « Figure 2. », « Theorem 1.2. »…), absents
# de la source : retirés du passage cherché
_NAMES = (r"(?i:Table|Tableau|Tab\.|Figure|Fig\.|Algorithm|Algorithme|Listing|Theorem|Théorème|Lemma|Lemme|Proposition|"
          r"Corollary|Corollaire|Definition|Définition|Remark|Remarque|Example|Exemple|Conjecture|Claim|Assertion|"
          r"Notation|Exercise|Exercice|Problem|Problème|Question|Hypothesis|Hypothèse|Assumption|Step|Étape|Case|Cas|"
          r"Equation|Équation|Section|Chapter|Chapitre|Appendix|Annexe|Proof|Preuve|Démonstration)")
_NUMBER = r"(?:[A-Z]|\d+)(?:[.\-–]\d+)*[a-z]?"
GENERATED = re.compile(rf"^\s*{_NAMES}(?:\s*~?\s*{_NUMBER})?\s*[.:]\s*|^\s*{_NAMES}\s*~?\s*{_NUMBER}\s+")


def quote_variants(quote):
    """Lettres du passage du PDF à chercher : le passage entier, puis sans le texte produit par LaTeX devant lui."""
    variants = [plain_letters(quote)]
    stripped = GENERATED.sub("", quote or "", count=1)
    if stripped != (quote or ""):
        letters = plain_letters(stripped)
        if len(letters) >= MIN_WINDOW and letters != variants[0]:
            variants.append(letters)
    return variants


def _body(text, pos):
    """Corps {…} d'une définition à partir de `pos` (espaces sautés), ou None."""
    found = texstructure._group(text, pos, "{")
    return found[0] if found else None


DEFINITIONS = re.compile(
    r"\\(?:(?P<new>(?:re)?newcommand|providecommand|DeclareRobustCommand)\*?\s*\{?\s*\\(?P<n1>[A-Za-z@]+)\s*\}?\s*(?P<args>\[\s*\d\s*\])?"
    r"|(?P<def>[egx]?def)\s*\\(?P<n2>[A-Za-z@]+)\s*(?P<params>#)?"
    r"|(?P<let>let)\s*\\(?P<n3>[A-Za-z@]+)\s*=?\s*(?P<target>\\[A-Za-z@]+)"
    r"|(?P<op>DeclareMathOperator)\*?\s*\{?\s*\\(?P<n4>[A-Za-z@]+)\s*\}?"
    r"|(?P<doc>(?:New|Renew|Provide|Declare)DocumentCommand)\s*\{?\s*\\(?P<n5>[A-Za-z@]+)\s*\}?\s*\{\s*\})")


def user_macros(text):
    """{nom: corps} des macros sans argument de l'auteur (\\newcommand{\\Lc}{\\mathcal L}, \\def\\vare{\\varepsilon},
    \\let\\a\\b, \\DeclareMathOperator{\\Spec}{Spec}…) dans `text` (sans commentaires) ; la dernière définition l'emporte."""
    found = {}
    for match in DEFINITIONS.finditer(text):
        if len(found) >= MAX_MACROS:
            break
        name = match.group("n1") or match.group("n2") or match.group("n3") or match.group("n4") or match.group("n5")
        if re.match(r"\\Provide|\\providecommand", match.group(0)) and (name in found or _builtin(name)):
            continue  # \\providecommand : sans effet sur une commande déjà définie
        if match.group("let"):
            # \\let copie le sens de la cible au moment de la définition (\\let\\temp\\phi\\let\\phi\\varphi…)
            target = match.group("target")[1:]
            found[name] = found[target] if target in found else _builtin(target)
            continue
        if match.group("params") or (match.group("args") and match.group("args").strip("[] \t\n") != "0"):
            continue  # macro à arguments : ses arguments restent dans le texte, la commande seule est ignorée
        if match.group("new") and re.match(r"\s*\[", text[match.end():match.end() + 50]):
            continue  # argument optionnel par défaut : macro à argument
        body = _body(text, match.end())
        if body is None or len(body) > 300:
            continue
        found[name] = body
    return found


def _builtin(name):
    """Lettres imprimées par la commande \\`name` de LaTeX (lettre grecque, symbole, opérateur), sinon ""."""
    if name in GREEK:
        return _letters_of(GREEK[name])
    if name in SYMBOL_LETTERS:
        return _letters_of(SYMBOL_LETTERS[name])
    if name in OPERATORS:
        return OPERATOR_LETTERS.get(name, name.lower())
    return ""


class Letters:
    """Lettres de la source TeX : commandes retirées, lettres grecques, opérateurs (\\det) et macros sans argument de
    l'auteur (développées, au plus MACRO_LETTERS lettres chacune) gardés."""

    def __init__(self, macros=None):
        self.macros = macros or {}
        self.cache = {}

    def of_macro(self, name, depth):
        if name in self.cache:
            return self.cache[name]
        if depth >= MACRO_DEPTH:
            return ""  # trop profond à partir d'ici : pas retenu, la même macro peut être lue de moins loin
        self.cache[name] = _builtin(name)  # définition récursive : le sens de LaTeX (ou rien)
        self.cache[name] = self.letters(self.macros[name], depth=depth + 1, limit=MACRO_LETTERS)[0][:MACRO_LETTERS]
        return self.cache[name]

    def word(self, name, depth=0):
        """Lettres imprimées par la commande \\`name` (sans ses arguments)."""
        if name in self.macros:
            return self.of_macro(name, depth)
        return _builtin(name)

    def letters(self, text, depth=0, limit=None):
        """Lettres de `text` et, pour chacune, sa position dans `text` (celle de la commande pour les
        lettres d'une commande ou d'une macro) ; `limit` : arrêt au-delà de ce nombre de lettres."""
        out, where, pos = [], [], 0

        def plain(a, b):
            for index in range(a, b):
                for letter in _letters_of(text[index]):
                    out.append(letter)
                    where.append(index)

        for match in TEX_TOKEN.finditer(text):
            plain(pos, match.start())
            if match.group(1):
                for letter in self.word(match.group(1), depth):
                    out.append(letter)
                    where.append(match.start())
            pos = match.end()
            if limit is not None and len(out) >= limit:
                return "".join(out), where
        plain(pos, len(text))
        return "".join(out), where


def tex_letters(text, macros=None):
    """Lettres de la source TeX `text` (commandes retirées ; lettres grecques, opérateurs et macros sans
    argument de l'auteur gardés) et, pour chacune, sa position dans `text`."""
    return Letters(macros).letters(text)


MIN_WINDOW = 12          # lettres d'un début ou d'une fin de passage cherché seul
STRONG_WINDOW = 25       # en dessous : fenêtre courte, confirmée par la page quand SyncTeX est là


class Finder:
    """Recherche de passages dans le texte développé (lettres seulement). Le préambule (avant `body`) n'est cherché
    que sur demande (titre, auteurs, \\thanks)."""

    def __init__(self, text, body=0, macros=None):
        self.letters, self.where = Letters(macros).letters(text)
        self.body = bisect.bisect_left(self.where, body)

    def range(self, a, b):
        """Lettres venues des positions [a, b) du texte."""
        return bisect.bisect_left(self.where, a), bisect.bisect_left(self.where, b)

    def find(self, needle, a=0, b=None, limit=200):
        """Positions (dans les lettres) des occurrences de `needle` parmi les lettres [a, b)."""
        b = len(self.letters) if b is None else b
        found, pos = [], a
        while len(found) < limit:
            pos = self.letters.find(needle, pos, b)
            if pos < 0:
                break
            found.append(pos)
            pos += 1
        return found

    def candidates(self, quote, a=None, b=None, windows=True):
        """Occurrences du passage (ses lettres) parmi les lettres [a, b) (par défaut : le corps du document) : entier,
        sinon début ou fin (60, 40, 25, 18 puis 12 lettres ; `windows` faux : entier seulement). Liste de (positions dans
        le texte, sûr), de la plus sûre à la moins sûre ; `sûr` est faux pour les fenêtres de moins de 25 lettres."""
        a = self.body if a is None else a
        if len(quote) < 4:
            return []
        attempts = [([self.where[p] for p in self.find(quote, a, b)], True)]
        for size in (60, 40, 25, 18, MIN_WINDOW):
            if size >= len(quote) or not windows:
                continue
            strong = size >= STRONG_WINDOW
            attempts.append(([self.where[p] for p in self.find(quote[:size], a, b)], strong))
            back = len(quote) - size  # fin du passage : son début estimé
            attempts.append(([self.where[max(0, p - back) if p - back >= a else p] for p in self.find(quote[-size:], a, b)],
                             strong))
        return [(found, strong) for found, strong in attempts if found]


# ---------- Analyse du projet ----------

def _project(pid, language):
    settings = texfile._settings(pid)
    errors = ERRORS.get(language, ERRORS["en"])
    main = Path(settings.get("main") or "")
    if not settings.get("main") or not main.is_file():
        raise ExportError(errors["main"].format(path=settings.get("main") or "?"))
    return main


def _locate(files, main_name):
    """Texte développé, origines de ses caractères et structure de la source (identifiants de l'import), avec la place
    de chaque énoncé, preuve et section dans le texte développé. None si les positions ne peuvent être suivies."""
    text, origins = expand_with_origins(files, main_name)
    if text != _expand(files, main_name):  # jamais attendu : alors aucune position plutôt qu'une fausse
        return None
    places = {}
    structure = texstructure.extract(text, positions=places)
    nodes = [n["id"] for n in structure.get("nodes", [])]
    proofs = [p["id"] for p in structure.get("proofBlocks", [])]
    texfile.stable_ids(structure)
    renamed = dict(zip(nodes, [n["id"] for n in structure.get("nodes", [])]))
    renamed.update(zip(proofs, [p["id"] for p in structure.get("proofBlocks", [])]))
    spans = {renamed.get(key, key): value for key, value in {**places["nodes"], **places["proofs"]}.items()}
    return {"text": text, "origins": origins, "structure": structure, "spans": spans, "sections": places["sections"]}


def _known_words(text):
    """Commandes employées par la source, et environnements de formules disponibles (« env:pmatrix ») : ceux qu'elle
    emploie, et ceux d'amsmath s'il est chargé (amsart, amsbook… le chargent)."""
    known = set(re.findall(r"\\([A-Za-z]+)", text))
    known |= {"env:" + name for name in re.findall(r"\\begin\s*\{([A-Za-z]+)\}", text) if name in MATH_ENVS}
    if re.search(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{[^}]*\b(?:amsmath|mathtools)\b"
                 r"|\\documentclass\s*(?:\[[^\]]*\])?\s*\{ams(?:art|book|proc)\}", text):
        known |= {"env:" + name for name in MATH_ENVS}
    return known


# ---------- Renvois : \eqref, \ref, \cite à la place des numéros écrits dans les notes ----------

class Known(set):
    """Commandes et environnements disponibles (_known_words), et renvois du document (references)."""
    refs = None


NUMBER = r"\d+(?:\.\d+)*[a-z]?"
REF_TEXT = re.compile(r"\\(cite[tp]?|ref|eqref|cref|Cref|autoref|pageref)\s*(\[[^\[\]{}]*\])?\s*\{([^{}]*)\}"
                      r"|(?<![\w\\^_\]])\[([^\[\]]{1,80})\](?!\()"
                      rf"|(?<![\w^_])\(({NUMBER})\)(?!\w)"
                      rf"|\b([^\W\d_]{{2,}})(\.?)(?:\s+|~)({NUMBER})(?!\.?\d)")
# noms d'énoncés écrits en français dans une note, pour un document en anglais (le mot écrit est gardé)
KIND_NAMES = {"théorème": "theorem", "lemme": "lemma", "corollaire": "corollary", "définition": "definition",
              "remarque": "remark", "remarques": "remarks", "exemple": "example", "hypothèse": "assumption",
              "proposition": "proposition", "conjecture": "conjecture", "thm": "theorem", "lem": "lemma",
              "prop": "proposition", "cor": "corollary", "def": "definition", "rem": "remark"}
# « Theorem 4.3 of [KMM19] », « in [3] Theorem 1.1 », « Ma–Marinescu's Theorem 1.1 » : numéro d'un autre article
OTHER_AFTER = re.compile(r"\s*,?\s*(?:of|in|from|de|du|des|dans|chez|d['’])\s*(?:\[|\\cite|[A-Z])")
OTHER_BEFORE = re.compile(r"(?:\]|\\cite\w*\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}|[A-Z][\w–-]*['’]s?)\s*,?\s*(?:the\s+|le\s+|la\s+|l['’])?$")
# « on [1, 2] », « dans [0, 1] » : intervalle, pas deux références
INTERVAL_BEFORE = re.compile(r"(?:\b(?:interval|intervalle|on|in|over|sur|dans|de|from)|∈)\s*$", re.I)
BASE_REFS = {"cite", "ref", "pageref"}  # (les autres : commandes que la source emploie, \eqref : amsmath)


def references(structure, text, eqref):
    """Renvois du document, d'après la structure de la dernière compilation, gardés seulement si l'étiquette est
    encore dans la source (\\label) et désigne un seul objet : équations (« (1.17) » → étiquette), énoncés
    (« lemma 4.2 » → étiquette), bibliographie (clé et numéro affiché, « 2 » → clé)."""
    labels = set(re.findall(r"\\label\s*\{([^{}]+)\}", text))

    def unique(pairs):
        found = {}
        for name, key in pairs:
            found.setdefault(name, set()).add(key)
        return {name: keys.pop() for name, keys in found.items() if len(keys) == 1}
    def anchored(shown, anchors):
        """Nom affiché, son numéro confirmé par les ancres de hyperref (« thm.1.3 ») quand il y en a : la structure peut
        se tromper (numéro d'un \\item étiqueté dans l'énoncé) ; numéro de l'ancre s'il n'y en a qu'un, sinon ""."""
        shown = " ".join((shown or "").split())
        numbers = {m.group(1) for a in anchors or [] for m in [re.fullmatch(r"(?!Item\.)[A-Za-z*]+\.(\d+(?:\.\d+)*)", a)] if m}
        number = re.search(rf"{NUMBER}", shown)
        if not numbers or (number and number.group(0) in numbers):
            return shown
        return shown[:number.start()] + numbers.pop() + shown[number.end():] if number and len(numbers) == 1 else ""

    def agrees(shown, anchors):
        return anchored(shown, anchors) == " ".join((shown or "").split())
    equations = unique(((value or {}).get("label") or "", key) for key, value in (structure.get("equations") or {}).items()
                       if key in labels and re.fullmatch(rf"\({NUMBER}\)", (value or {}).get("label") or "")
                       and agrees((value or {}).get("label") or "", (value or {}).get("anchors")))
    names = structure.get("labels") or {}
    nodes = unique((anchored(node.get("label"), node.get("anchors")).lower(), node["id"][len("tex-lbl-"):])
                   for node in structure.get("nodes") or [] if node.get("id", "").startswith("tex-lbl-")
                   and node["id"][len("tex-lbl-"):] in labels and names.get(node["id"][len("tex-lbl-"):]) == node["id"]
                   and re.fullmatch(rf"[^\W\d_]{{2,}}\.? {NUMBER}", anchored(node.get("label"), node.get("anchors"))))
    keys = {item["id"][4:] for item in structure.get("bibliography") or []
            if item.get("id", "").startswith("bib.") and re.fullmatch(r"[^\s{},%#\\~]+", item["id"][4:])}
    bib = unique((name.lower(), item["id"][4:]) for item in structure.get("bibliography") or [] if item["id"][4:] in keys
                 for name in {item["id"][4:], (item.get("label") or "").strip()} if name)
    return {"eq": equations, "node": nodes, "bib": bib, "keys": keys, "labels": labels, "eqref": eqref}


def _ref_tex(match, known):
    """LaTeX d'un renvoi trouvé par REF_TEXT, ou None (inconnu du document : le texte reste tel quel)."""
    refs = known.refs
    if match.group(1):  # commande écrite dans la note : gardée si ses clés existent
        command, option = match.group(1), match.group(2) or ""
        keys = [key.strip() for key in match.group(3).split(",")]
        if not all(keys) or (command not in BASE_REFS and command not in known
                             and not (command == "eqref" and refs["eqref"])):
            return None
        if command.startswith("cite"):
            if not all(key in refs["keys"] for key in keys):
                return None
        elif not all(key in refs["labels"] for key in keys) or (len(keys) > 1 and command not in ("cref", "Cref")):
            return None
        option = f"[{_text_chars(option[1:-1])}]" if option else ""
        return f"\\{command}{option}{{{','.join(keys)}}}"
    if match.group(4) is not None:  # [clé], [2], [clé, Theorem 3], [2, 5]
        parts = [part.strip() for part in match.group(4).split(",")]
        keys = []
        while parts and parts[0].lower() in refs["bib"]:
            keys.append(refs["bib"][parts.pop(0).lower()])
        if not keys:
            return None
        option = " ".join(", ".join(parts).split())
        return "\\cite" + (f"[{_text_chars(option)}]" if option else "") + "{" + ",".join(keys) + "}"
    if match.group(5):
        key = refs["eq"].get(f"({match.group(5)})")
        return None if not key else (f"\\eqref{{{key}}}" if refs["eqref"] else f"(\\ref{{{key}}})")
    word = match.group(6).lower()
    key = refs["node"].get(f"{word} {match.group(8)}") or refs["node"].get(f"{KIND_NAMES.get(word, word)} {match.group(8)}")
    return None if not key else f"{_text_chars(match.group(6) + match.group(7))}~\\ref{{{key}}}"


def _ref_pieces(text, known):
    """Texte découpé en (morceau, None) et (renvoi tel qu'écrit, son LaTeX)."""
    if not getattr(known, "refs", None):
        return [(text, None)]
    pieces, pos = [], 0
    for match in REF_TEXT.finditer(text):
        before, after = text[max(0, match.start() - 40):match.start()], text[match.end():match.end() + 40]
        if (match.group(5) or match.group(6)) and (OTHER_BEFORE.search(before) or OTHER_AFTER.match(after)):
            continue  # numéro d'un autre article
        if match.group(4) is not None and re.fullmatch(r"\s*\d+\s*,\s*\d+\s*", match.group(4)) \
                and INTERVAL_BEFORE.search(before):
            continue
        tex = _ref_tex(match, known)
        if tex:
            pieces += [(text[pos:match.start()], None), (match.group(0), tex)]
            pos = match.end()
    pieces.append((text[pos:], None))
    return [piece for piece in pieces if piece[0]]


def _plain(text, known):
    """_breakable, renvois du document en \\eqref, \\ref, \\cite."""
    return "".join(_breakable(part) if tex is None else tex for part, tex in _ref_pieces(text, known))


def ref_summary(text, known):
    """« (4.75) = \\eqref{…} ; [2] = \\cite{…} » : renvois d'une note (hors formules), pour son bloc de commentaires."""
    found = []
    for part, tex in _ref_pieces(re.sub(r"\$\$[\s\S]*?\$\$|\$[^$]*\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)", " ", text or ""), known):
        if tex is None:
            continue
        item = f"{' '.join(part.split())} = {tex.replace('~', ' ')}"
        if part.strip() != tex and item not in found:
            found.append(item)
    return " ; ".join(found)


def _todo_setup(text):
    """Préambule (texte développé sans commentaires) : (todonotes déjà chargé, avec l'option disable, \\todo défini
    par le document)."""
    loaded = disabled = False
    for match in re.finditer(r"\\(?:usepackage|RequirePackage)\s*(\[[^\]]*\])?\s*\{([^}]*)\}", text):
        if "todonotes" in [p.strip() for p in match.group(2).split(",")]:
            loaded = True
            disabled = disabled or bool(match.group(1) and re.search(r"\bdisable\b", match.group(1)))
    own = bool(re.search(r"\\(?:(?:re)?newcommand\*?|providecommand\*?|DeclareRobustCommand\*?|NewDocumentCommand|"
                         r"RenewDocumentCommand|ProvideDocumentCommand|DeclareDocumentCommand)\s*\{?\s*\\todo(?![A-Za-z])"
                         r"|\\(?:[egx]?def|let)\s*\\todo(?![A-Za-z])", text))
    return loaded, disabled, own


# ---------- Placement des notes ----------

def _counts_for(entries):
    counts = {"total": len(entries), "exact": 0, "approx": 0, "unplaced": 0, "header": 0, "byKind": {}}
    for entry in entries:
        key = {"exact": "exact", "approx": "approx", "none": "unplaced", "header": "header"}[entry["precision"]]
        counts[key] += 1
        counts["byKind"][entry["kind"]] = counts["byKind"].get(entry["kind"], 0) + 1
    return counts


def _page_boxes(pid):
    """{page: (mediabox, cropbox, rotation)} du PDF lu (paper.pdf), ou {} s'il est illisible."""
    import pypdf
    try:
        reader = pypdf.PdfReader(str(paper_dir(pid) / "paper.pdf"))
        boxes = {}
        for number, page in enumerate(reader.pages, 1):
            media = [float(v) for v in page.mediabox]
            crop = [float(v) for v in page.cropbox]
            boxes[number] = (media, crop, int(page.get("/Rotate") or 0) % 360)
        return boxes
    except Exception:
        return {}


class Plan:
    """Tout ce que la copie annotée contiendra : notes placées (ou non), fichiers à copier, avertissements."""

    def __init__(self, pid, mode, language, layout="wide", endnotes=True):
        self.pid, self.mode, self.language = pid, mode, language
        self.layout = layout if mode == "todo" else None  # mise en page des notes en marge (sans effet en commentaires)
        self.endnotes = bool(endnotes) and mode == "todo"  # notes longues renvoyées en fin de document
        self.long_entries = []  # notes longues numérotées (voir number_long)
        self.wide = False  # mise en page « wide » ajoutée au préambule de la copie (voir _insertions)
        self.theorems = None  # environnements d'énoncés déclarés par le projet (voir _ends_paragraph)
        self.words = _words(language)
        self.warnings = []
        self.main = _project(pid, language)
        self.root = self.main.parent
        self.files = texfile.read_project(self.main)
        self.lines = {name: Lines(text) for name, text in self.files.items()}
        self.copy, self.size = _walk(self.root, self.main, language, referenced(self.files))
        if len(self.copy) > LARGE_FILES or self.size > LARGE_BYTES:
            self.warnings.append(self.words["warn"]["large"].format(
                folder=self.root.name, files=len(self.copy), size=f"{self.size / 1_000_000:.0f}"))
        copied = {rel for rel, _, _ in self.copy}
        lowered = {}
        for rel in copied:
            lowered.setdefault(rel.lower(), rel)
        # fichier lu par la source → fichier de la copie (même chemin, sinon à la casse près : disque insensible)
        self.copy_name = {name: (name if name in copied else lowered.get(name.lower())) for name in self.files}
        outside = sorted(n for n in self.files if n.startswith("../") or n == ".." or posixpath.isabs(n))
        missing = sorted(n for n in self.files if n not in outside and not self.copy_name[n])
        if outside:
            self.warnings.append(self.words["warn"]["outside"].format(files=", ".join(outside[:10])))
        if missing:
            self.warnings.append(self.words["warn"]["notCopied"].format(files=", ".join(missing[:10])))
        self.located = _locate(self.files, self.main.name) if self.main.name in self.files else None
        if self.located is None:
            self.warnings.append(self.words["warn"]["noPositions"])
        self.saved = export._structure(pid)
        self.known = Known(_known_words(self.located["text"]) if self.located else set())
        if self.located:  # \eqref : amsmath chargé (ses environnements sont alors disponibles)
            self.known.refs = references(self.saved or {}, self.located["text"], "env:pmatrix" in self.known)
        self.notes = [n for n in annotations.list_for(pid) if n["kind"] != "proof" and export.has_notes([n])]
        self.lost = {n["uid"] for n in texfile.notes_to_check(pid)}
        self.lost_context = texfile.lost_notes(paper_dir(pid)) if self.lost else {}  # énoncé d'avant la recompilation
        self._synctex()
        self._preambles()
        self.entries = [self._entry(note) for note in self.notes]
        self._order_entries()

    # --- SyncTeX ---
    def _synctex(self):
        """SyncTeX de la dernière compilation, s'il est encore celui de la source (aucun fichier modifié depuis)."""
        self.sync = None
        path = paper_dir(self.pid) / texfile.SYNCTEX
        pdf_notes = [n for n in self.notes if ((n.get("target") or {}).get("pdf"))]
        if not path.is_file():
            if pdf_notes:
                self.warnings.append(self.words["warn"]["noSynctex"])
            return
        made = path.stat().st_mtime
        stale = False
        for name in self.files:
            try:
                if (self.root / name).stat().st_mtime > made:
                    stale = True
                    break
            except OSError:
                continue
        if stale:
            if pdf_notes:
                self.warnings.append(self.words["warn"]["staleSynctex"])
            return
        pages = {n["target"]["pdf"].get("page") for n in pdf_notes if isinstance(n["target"]["pdf"].get("page"), int)}
        self.sync = synctex.load(path, self.root, pages)

    # --- préambule et corps de chaque fichier ---
    def _preambles(self):
        """Ligne de \\begin{document} (fichier, ligne) et, pour chaque fichier, s'il commence dans le corps du document."""
        self.begin_document, self.begin_shared = None, False
        for name, text in self.files.items():
            stripped = _strip_comments(text)
            if "\\begin{document}" in stripped:
                lines = Lines(text)
                for index in range(len(lines)):
                    content = lines.content(index)
                    match = re.search(r"\\begin\s*\{document\}", content)
                    if match and not re.search(r"(?<!\\)%", content[:match.start()]):
                        self.begin_document = (name, index)
                        self.begin_shared = bool(re.search(r"\\documentclass", content[:match.start()]))
                        break
                if self.begin_document:
                    break
        self.body_start = {}
        located = self.located
        begin = located["text"].find("\\begin{document}") if located else -1
        for name in self.files:
            first = located["origins"].first(name) if located else None
            self.body_start[name] = name != self.main.name and (first is None or begin < 0 or first > begin) \
                and (not self.begin_document or self.begin_document[0] != name)
        self.states = {}

    def states_of(self, name):
        if name not in self.states:
            if not hasattr(self, "macros"):
                self.macros = aliases("\n".join(_strip_comments(text) for text in self.files.values()))
            self.states[name] = scan(self.files[name], self.body_start.get(name, False), self.macros)
        return self.states[name]

    # --- positions ---
    def where(self, offset):
        """Position du texte développé → (fichier, ligne) dans la source d'origine."""
        found = self.located["origins"].at(offset) if self.located else None
        if found is None:
            return None
        name, at = found
        return name, self.lines[name].of(at)

    def span_lines(self, element):
        """(fichier, ligne du \\begin, ligne du \\end, position du début dans le fichier) d'un énoncé ou d'une preuve."""
        span = (self.located or {}).get("spans", {}).get(element)
        if not span:
            return None
        start = self.located["origins"].at(span[0])
        end = self.located["origins"].at(max(span[0], span[1] - 1))
        if start is None:
            return None
        name = start[0]
        first = self.lines[name].of(start[1])
        last = self.lines[name].of(end[1]) if end and end[0] == name else first
        return name, first, last, start[1]

    def user_macros(self):
        if not hasattr(self, "_user_macros"):
            self._user_macros = user_macros(self.located["text"]) if self.located else {}
        return self._user_macros

    def finder(self):
        if not hasattr(self, "_finder"):
            text = self.located["text"]
            begin = text.find("\\begin{document}")
            self._finder = Finder(text, begin if begin >= 0 else 0, self.user_macros())
        return self._finder

    def located_items(self):
        """Énoncés et sections de la structure enregistrée qui ont une page, avec leur position dans le texte développé :
        [(position, page, hauteur, id, fin)], dans l'ordre du texte."""
        if not hasattr(self, "_items"):
            items = []
            spans = (self.located or {}).get("spans", {})
            sections = (self.located or {}).get("sections", {})
            for node in self.saved.get("nodes", []):
                if node.get("page") and node["id"] in spans:
                    items.append((spans[node["id"]][0], node["page"], node.get("top"), node["id"], spans[node["id"]][1]))
            for section in self.saved.get("toc", []):
                if section.get("page") and section["id"] in sections:
                    items.append((sections[section["id"]], section["page"], section.get("top"), section["id"], None))
            self._items = sorted(items)
        return self._items

    # --- une note ---
    def _entry(self, note):
        words = self.words
        target = note.get("target") or {}
        pdf = target.get("pdf") or {}
        who, text = _who(note, words)
        entry = {"id": note["id"], "uid": note.get("uid"), "note": text if (text or "").strip() else "",
                 "quote": None, "state": None, "who": who, "page": pdf.get("page"), "color": note.get("color"),
                 "file": None, "line": None, "todoLine": None, "precision": "none", "reason": None, "rank": 2,
                 "statement": False}
        kind = note["kind"]
        if kind == "general":
            entry.update(kind="general", title=words["sheet"], precision="header", rank=0)
            return entry
        if kind == "bookmark":
            entry.update(kind="bookmark", title=note.get("quote") or "", precision="header")
            return entry
        if kind == "block":
            return self._block(entry, note)
        if kind == "highlight" and "rect" in pdf:
            entry.update(kind="zone", title=words["zone"].format(page=pdf.get("page")), rank=1, quote=None)
            return self._point(entry, note)
        if kind == "highlight" and pdf:
            entry.update(kind="highlight", title=words["highlight"].format(page=pdf.get("page")), rank=1,
                         quote=note.get("quote"))
            return self._pdf_passage(entry, note)
        if kind == "highlight":
            entry.update(kind="highlight", title=words["htmlHighlight"], rank=1, quote=note.get("quote"))
            return self._html_passage(entry, note, (target.get("start") or {}).get("id"))
        if kind == "note" and pdf:
            entry.update(kind="note", title=words["pin"].format(page=pdf.get("page")), color=None)
            return self._point(entry, note)
        entry.update(kind="note", title=words["htmlNote"], color=None)
        return self._html_passage(entry, note, target.get("id"))

    def _label(self, element):
        """Nom d'un énoncé, d'une preuve ou d'une section (structure enregistrée, sinon recalculée)."""
        for structure in (self.saved, (self.located or {}).get("structure") or {}):
            nodes = {n["id"]: n for n in structure.get("nodes", [])}
            if element in nodes:
                node = nodes[element]
                return node["label"] + (f" ({node['name']})" if node.get("name") else "")
            for node in nodes.values():
                if element in (node.get("proofs") or []):
                    return self.words["proof"].format(label=node["label"])
            for proof in structure.get("proofBlocks", []):
                if proof.get("id") == element and proof.get("of") in nodes:
                    return self.words["proof"].format(label=nodes[proof["of"]]["label"])
            section = next((s for s in structure.get("toc", []) if s["id"] == element), None)
            if section:
                return section.get("title") or element
        return element

    def _place(self, entry, name, line, precision, statement=None):
        """Note placée avant la ligne `line` (à partir de 0) de `name` ; `statement` : ligne de sa note en marge, dans
        un énoncé. Une note placée approximativement le dit dans son titre (commentaire et note en marge)."""
        entry.update(file=name, line=line, precision=precision)
        suffix = " — " + self.words["approx"]
        if precision == "approx" and not entry["title"].endswith(suffix):
            entry["title"] += suffix
        if statement is not None:
            entry["statement"] = True
            entry["todoLine"] = statement
        return entry

    def _block(self, entry, note):
        element = (note.get("target") or {}).get("id") or ""
        states = export.STATES.get(self.language, export.STATES["en"])
        structures = (self.saved, (self.located or {}).get("structure") or {})
        proofs = {p.get("id") for structure in structures for p in structure.get("proofBlocks", [])}
        # sections qui servent de preuve à un énoncé (« Proof of Theorem 1.2 ») : des preuves aussi
        proofs |= {p for structure in structures for n in structure.get("nodes", []) for p in (n.get("proofs") or [])}
        entry.update(kind="proof" if element in proofs else "statement", title=self._label(element),
                     state=states.get(note.get("state")), rank=0, review=note.get("state") == "review")
        context = self.lost_context.get(note.get("uid"))
        if entry["title"] == element and isinstance(context, dict):  # énoncé disparu : celui de la compilation d'avant
            label = context.get("label") or element
            proof = context.get("on") == "proof"
            entry.update(kind="proof" if proof else "statement", page=entry["page"] or context.get("page"),
                         title=self.words["proof"].format(label=label) if proof else label,
                         quote=one_line(context.get("statement") or "", 120) or None)
        if note.get("uid") in self.lost:
            entry["reason"] = "lost"
            return entry
        found = self.span_lines(element)
        if found is None:
            section = (self.located or {}).get("sections", {}).get(element)
            where = self.where(section) if section is not None else None
            if where:
                return self._place(entry, where[0], where[1], "exact")
            entry["reason"] = "notFound"
            return entry
        name, first, last, offset = found
        return self._place(entry, name, first, "exact", statement=self._after_begin(name, first, last, offset))

    def _after_begin(self, name, first, last, offset):
        """Ligne où mettre la note en marge d'un énoncé : après la ligne du \\begin{…}[…] (et d'un \\label seul sur la
        ligne suivante) ; sur la ligne du \\begin quand l'énoncé tient sur cette ligne."""
        text, lines = self.files[name], self.lines[name]
        begin = re.compile(r"\\begin\s*\{[^{}]*\}").match(text, offset)
        pos = begin.end() if begin else offset + 1
        if begin:
            look = re.compile(r"(?:\s|(?<!\\)%[^\r\n]*)*").match(text, pos).end()
            if look < len(text) and text[look] == "[":
                depth = 0
                for index in range(look, min(len(text), look + 5000)):
                    if text[index] == "[" and text[index - 1] != "\\":
                        depth += 1
                    elif text[index] == "]" and text[index - 1] != "\\":
                        depth -= 1
                        if depth == 0:
                            pos = index + 1
                            break
        line = lines.of(pos - 1)
        if re.fullmatch(r"\s*\\label\s*\{[^{}]*\}\s*(?:%.*)?", lines.content(line + 1)):
            line += 1
        if last <= line:
            return first
        return line + 1

    def _html_passage(self, entry, note, element):
        """Passage surligné ou note épinglée du HTML : dans son énoncé (passage cherché dedans), sinon le passage
        cherché dans tout le texte."""
        quote = note.get("quote") if note["kind"] == "highlight" else None
        span = (self.located or {}).get("spans", {}).get(element or "")
        if span:
            if quote:
                finder = self.finder()
                found = finder.candidates(tex_letters(quote, macros=self.user_macros())[0], *finder.range(*span))
                if found and len(found[0][0]) == 1:
                    where = self.where(found[0][0][0])
                    if where:
                        return self._place(entry, where[0], where[1], "exact" if found[0][1] else "approx")
            lines = self.span_lines(element)
            if lines:
                return self._place(entry, lines[0], lines[1], "exact" if not quote else "approx")
        if quote and self.located:
            for found, strong in self.finder().candidates(tex_letters(quote, macros=self.user_macros())[0]):
                if len(found) == 1 and strong:
                    where = self.where(found[0])
                    if where:
                        return self._place(entry, where[0], where[1], "exact")
                break
            entry["reason"] = "quote"
            return entry
        entry["reason"] = "position"
        return entry

    def _pdf_passage(self, entry, note):
        """Passage surligné du PDF : cherché dans la source ; plusieurs endroits possibles : la page départage. Trouvé
        seulement dans le préambule (titre, auteurs, \\thanks) : après \\maketitle."""
        if note.get("uid") in self.lost:
            entry["reason"] = "lost"
            return entry
        page = entry["page"]
        if self.located:
            finder = self.finder()
            variants = quote_variants(note.get("quote") or "")
            # passage entier (avec puis sans « Table 1: »…) dans le corps, puis entier dans le préambule (titre…), puis
            # ses débuts et fins dans le corps, enfin dans le préambule
            for windows in (False, True):
                for quote in variants:
                    for found, strong in finder.candidates(quote, windows=windows):
                        chosen, precision = self._choose(found, page, strong)
                        if chosen is not None:
                            where = self.where(chosen)
                            if where:
                                return self._place(entry, where[0], where[1], precision)
                if finder.body and any(finder.candidates(quote, 0, finder.body, windows=windows) for quote in variants):
                    title = self._title_line()
                    if title:
                        return self._place(entry, title[0], title[1], "approx")
        return self._page_start(entry, page)

    def _title_line(self):
        """(fichier, ligne) juste après la ligne de \\maketitle (sinon de \\begin{document}) : place des notes sur le
        titre, les auteurs, le résumé définis dans le préambule."""
        text = self.located["text"]
        begin = text.find("\\begin{document}")
        if begin < 0:
            return None
        title = re.compile(r"\\maketitle(?![A-Za-z])").search(text, begin)
        where = self.where(title.start() if title else begin)
        return (where[0], where[1] + 1) if where else None

    def _choose(self, offsets, page, strong=True):
        """Une occurrence parmi plusieurs : (position, précision) ou (None, None). Fenêtre courte (`strong` faux) :
        confirmée par la page quand SyncTeX est là, toujours approximative (le reste du passage n'a pas été trouvé).
        Occurrence unique sur une autre page que celle de la note d'après SyncTeX : approximative."""
        synced = self.sync is not None and isinstance(page, int)
        if len(offsets) == 1 and strong:
            where = self.where(offsets[0]) if synced else None
            pages = self.sync.pages_of(where[0], where[1] + 1) if where else set()
            return offsets[0], "approx" if pages and page not in pages else "exact"
        if len(offsets) == 1 and not synced:
            return offsets[0], "approx"
        if synced:
            kept = []
            for offset in offsets:
                where = self.where(offset)
                if where and page in self.sync.pages_of(where[0], where[1] + 1):
                    kept.append(offset)
            if len(kept) == 1:
                return kept[0], "exact" if strong else "approx"
            if kept:  # plusieurs encore sur la bonne page : la première, emplacement approximatif
                return kept[0], "approx"
            return None, None
        if isinstance(page, int):  # sans SyncTeX : pages des énoncés et sections qui l'entourent
            items = self.located_items()
            kept = []
            for offset in offsets:
                before = [item[1] for item in items if item[0] <= offset]
                after = [item[1] for item in items if item[0] > offset]
                low, high = (before[-1] if before else 1), (after[0] if after else 10 ** 6)
                if low <= page <= high:
                    kept.append((offset, low))
            same = [offset for offset, low in kept if low == page]  # l'énoncé qui le précède est sur cette page
            if len(same) == 1:
                return same[0], "approx"
            if len(kept) == 1:
                return kept[0][0], "approx"
        return None, None

    def _page_start(self, entry, page):
        """Passage introuvable : au premier énoncé (ou section) de sa page, sinon au haut de la page (SyncTeX). Section :
        après son titre (le titre commence la page : la note avant lui serait au bas de la page d'avant) ; énoncé :
        avant son \\begin, la note en marge dedans."""
        if isinstance(page, int):
            for item in self.located_items():
                if item[1] == page:
                    if item[4] is None:  # section
                        after = self._after_heading(item[0])
                        if after:
                            return self._place(entry, after[0], after[1], "approx")
                    found = self.span_lines(item[3]) if item[4] is not None else None
                    if found:
                        name, first, last, offset = found
                        return self._place(entry, name, first, "approx",
                                           statement=self._after_begin(name, first, last, offset))
                    where = self.where(item[0])
                    if where:
                        return self._place(entry, where[0], where[1], "approx")
            if self.sync:
                first = self.sync.first_line(page)
                if first and first[0] in self.files:
                    return self._place(entry, first[0], first[1] - 1, "approx")
        entry["reason"] = "quote"
        return entry

    def _after_heading(self, offset):
        """(fichier, ligne) après le titre de section qui commence à `offset` du texte développé (et après un \\label
        seul sur la ligne suivante), ou None."""
        text = self.located["text"]
        match = re.compile(r"\\[A-Za-z]+\*?").match(text, offset)
        if not match:
            return None
        pos = match.end()
        short = texstructure._group(text, pos, "[")
        if short:
            pos = short[1]
        title = texstructure._group(text, pos, "{")
        if not title:
            return None
        start, end = self.where(offset), self.where(title[1] - 1)
        if not start or not end or start[0] != end[0]:
            return None
        name, line = end
        if re.fullmatch(r"\s*\\label\s*\{[^{}]*\}\s*(?:%.*)?", self.lines[name].content(line + 1)):
            line += 1
        return name, line + 1

    def _point(self, entry, note):
        """Zone ou note épinglée du PDF : la ligne de la source à cet endroit (SyncTeX), sinon après le dernier énoncé
        placé au-dessus du point sur la page."""
        pdf = note["target"]["pdf"]
        page = pdf.get("page")
        boxes = getattr(self, "_boxes", None)
        if boxes is None:
            boxes = self._boxes = _page_boxes(self.pid) if any((n.get("target") or {}).get("pdf") for n in self.notes) else {}
        box = boxes.get(page)
        user_x = user_y = None  # point en coordonnées du PDF (origine en bas à gauche)
        if box:
            media, crop, rotation = box
            if "rect" in pdf:
                rect = pdf["rect"]
                width, height = crop[2] - crop[0], crop[3] - crop[1]
                # milieu de la zone en largeur ; en hauteur, sa première ligne (au plus 14 pt sous son haut)
                right, bottom = (rect[2], rect[3]) if len(rect) >= 4 else (rect[0], rect[1])
                top = rect[1] * height + min(max(0.0, (bottom - rect[1]) * height) / 2, 14.0)
                user_x, user_y = crop[0] + (rect[0] + right) / 2 * width, crop[3] - top
            else:
                user_x, user_y = pdf.get("left") or crop[0], pdf.get("top")
            if self.sync and rotation == 0 and user_y is not None:
                found = self.sync.line_at(page, user_x - media[0], media[3] - user_y)
                if found and found[0] in self.files:
                    return self._place(entry, found[0], found[1] - 1, "approx")
        elif "rect" not in pdf:
            user_y = pdf.get("top")
        if user_y is not None and isinstance(page, int):
            above = [item for item in self.located_items() if item[1] == page and item[2] is not None
                     and item[2] >= user_y and item[4] is not None]
            if above:
                end = self.located["origins"].at(max(above[-1][0], above[-1][4] - 1))
                if end:
                    return self._place(entry, end[0], self.lines[end[0]].of(end[1]) + 1, "approx")
        entry["reason"] = "position"
        return entry

    # --- ordre et sûreté des lignes ---
    def _order_entries(self):
        """Notes placées : fichier copié, ligne sûre (jamais dans un verbatim) ; sinon non localisées. Ligne de la note en
        marge choisie en mode « todo »."""
        for entry in self.entries:
            if entry["precision"] in ("exact", "approx"):
                name = entry["file"]
                if not self.copy_name.get(name):
                    self._unplace(entry, "outside")
                    continue
                states = self.states_of(name)
                line = _climb(states, min(entry["line"], len(states) - 1), 0)
                if line is None:
                    self._unplace(entry, "position")
                    continue
                entry["line"] = line
            if self.mode != "todo" or entry["precision"] not in ("exact", "approx"):
                entry["todoLine"] = None
                continue
            if not ((entry.get("note") or "").strip() or entry.get("review")):
                entry["todoLine"] = None
                continue
            states = self.states_of(entry["file"])
            wanted = entry["todoLine"] if entry.get("statement") and entry.get("todoLine") is not None else entry["line"]
            line = _climb(states, min(wanted, len(states) - 1), 1)
            if line is None:
                entry["todoLine"] = None
                continue
            if not entry.get("statement") and line != entry["line"]:
                entry["line"] = line  # note en marge remontée avant la formule, la boîte… : le commentaire avec elle
            entry["todoLine"] = line
            entry["short"] = self._short(entry, self.known)
            entry["long"] = self.endnotes and is_long(self._margin_title(entry), entry.get("note"), self.layout)

    def _unplace(self, entry, reason):
        """Note placée qui ne peut finalement pas l'être (fichier non copié, aucune ligne sûre) : non localisée, sans
        « emplacement approximatif » dans son titre."""
        suffix = " — " + self.words["approx"]
        if entry["title"].endswith(suffix):
            entry["title"] = entry["title"][:-len(suffix)]
        entry.update(precision="none", reason=reason, todoLine=None, statement=False)

    def _margin_title(self, entry):
        """Titre de la note (état, assistant qui l'a proposée ; « emplacement approximatif » déjà dans le titre)."""
        title = entry["title"] + (f" — {entry['state']}" if entry.get("state") else "")
        if entry.get("who"):
            title += " — " + self.words["ai"].format(who=entry["who"])
        return title

    def _short(self, entry, known, number=None):
        """Texte de la note en marge : titre (état, assistant qui l'a proposée), puis la note ; note longue numérotée
        `number` : son début (coupé entre deux mots), puis le renvoi « → note N, p. X » (ENDNOTE_MACROS)."""
        title = self._margin_title(entry)
        note = (entry.get("note") or "").strip()
        if number is None:
            return short_tex(f"{title}: {note}" if note else title, known)

        def start(chars, least):
            text = short_tex(f"{title}: {note}", known, limit=max(int(chars * LONG_CUT), len(title) + 2 + least), word=True)
            return text[:-len("\\dots{}")] + "~\\dots{}" if text.endswith("\\dots{}") else text
        text = start(LONG_CHARS.get(self.layout, LONG_CHARS["keep"]), LONG_MIN_NOTE)
        if self.layout == "keep":  # colonne étroite (largeur connue à la compilation) : début plus court
            narrow = start(NARROW["chars"], NARROW["min"])
            if narrow != text:
                text = f"\\arxivreaderFit{{{narrow}}}{{{text}}}"
        return f"\\arxivreaderNoteMark{{{number}}}{text} \\arxivreaderEndRef{{{number}}}"

    def number_long(self):
        """Notes longues qui auront une note en marge : numérotées dans l'ordre du document (place de leur ligne dans
        le texte développé, \\input suivis), leur texte en marge raccourci."""
        origins = (self.located or {}).get("origins")

        def order(entry):
            name, line = entry["file"], entry["todoLine"]
            pos = self.lines[name].starts[min(line, len(self.lines[name]) - 1)]
            found = None
            if origins:  # premier morceau venu de ce fichier qui finit après la ligne (commentaires retirés)
                found = next((start + max(0, pos - offset) for start, other, offset, length
                              in zip(origins.starts, origins.names, origins.offsets, origins.lengths)
                              if other == name and offset + length > pos), None)
            return (found if found is not None else 0, line, entry["rank"], entry["id"])
        found = sorted((e for e in self.entries if e.get("long") and e.get("todoLine") is not None
                        and e["precision"] in ("exact", "approx")), key=order)
        for number, entry in enumerate(found, 1):
            entry["endnote"] = number
            entry["short"] = self._short(entry, self.known, number)
        self.long_entries = found

    def counts(self):
        return _counts_for(self.entries)


# ---------- Copie du projet ----------

# arguments de commandes qui nomment un fichier lu autrement que par \\input (\\verbatiminput{run.out}…)
ARGUMENT = re.compile(r"\\([A-Za-z]+)\*?\s*(?:\[[^\]]*\]\s*)*\{([^{}]*)\}")
NOT_FILES = {"input", "include", "includeonly", "label", "ref", "eqref", "cref", "Cref", "autoref", "pageref", "begin",
             "end", "usepackage", "RequirePackage", "documentclass", "section", "subsection", "emph", "textbf", "textit"}


def referenced(files):
    """Noms (chemins, avec ou sans extension) cités en argument d'une commande dans la source sans commentaires."""
    names = set()
    for text in files.values():
        for match in ARGUMENT.finditer(_strip_comments(text)):
            if match.group(1) in NOT_FILES or match.group(1).startswith("cite"):
                continue
            for name in match.group(2).split(","):
                name = name.strip()
                if name and len(name) < 200:
                    names.add(posixpath.normpath(name))
    return names


def _generated(rel, lowered, stems, names):
    """Fichier produit par la compilation : extension d'artefact, même nom qu'un .tex du projet, cité nulle part."""
    ext = next((e for e in ARTEFACTS if lowered.endswith(e)), None)
    if ext is None:
        return False
    stem = rel[:-len(ext)]
    base = posixpath.basename(stem)
    if not (stem.lower() in stems or base.lower() in stems or stem.lower().endswith((".tex", ".ltx", ".bib"))):
        return False
    return not ({rel, stem, posixpath.basename(rel), base} & names)


def _walk(root, main, language, names=frozenset()):
    """Fichiers du dossier du projet à copier : [(chemin relatif, chemin, taille)] ; dossiers liés jamais suivis,
    fichiers liés seulement si leur cible est dans le projet ; fichiers produits par la compilation ignorés (`names` :
    fichiers cités par la source, gardés). ExportError au-delà des limites ou pour un fichier illisible."""
    errors = ERRORS.get(language, ERRORS["en"])
    candidates, visited = [], 0
    real_root = os.path.realpath(root)
    try:
        for folder, dirnames, filenames in os.walk(root, followlinks=False):
            visited += len(dirnames) + len(filenames)
            if visited > MAX_VISITED:
                raise ExportError(errors["tooBig"])
            relative = os.path.relpath(folder, root)
            relative = "" if relative == "." else relative.replace(os.sep, "/")
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIPPED_DIRS
                                 and not os.path.islink(os.path.join(folder, d)))
            for name in sorted(filenames):
                rel = posixpath.join(relative, name) if relative else name
                lowered = name.lower()
                if (name.startswith(".") or name.endswith("~") or lowered.endswith(BUILD_FILES)
                        or rel == f"{main.stem}.pdf"):
                    continue
                candidates.append((rel, Path(folder) / name))
        stems = {rel.lower()[:-4] for rel, _ in candidates if rel.lower().endswith((".tex", ".ltx"))}
        stems |= {posixpath.basename(stem) for stem in stems}
        found, total = [], 0
        for rel, path in candidates:
            if _generated(rel, rel.lower(), stems, names):
                continue
            if path.is_symlink():
                target = os.path.realpath(path)
                if not (target == real_root or target.startswith(real_root + os.sep)) or not os.path.isfile(target):
                    continue
            if not path.is_file():
                continue
            if not os.access(path, os.R_OK):
                raise ExportError(errors["unreadableFile"].format(path=rel))
            size = path.stat().st_size
            total += size
            found.append((rel, path, size))
            if len(found) > MAX_COPY_FILES or total > MAX_COPY_BYTES:
                raise ExportError(errors["tooBig"])
    except OSError as error:
        raise ExportError(errors["unreadable"].format(error=error))
    return found, total


def _decode(raw):
    """(texte, codage) : UTF-8 strict, sinon Latin-1."""
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _newline(text):
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text and "\n" not in text:
        return "\r"
    return "\n"


def insert_lines(text, insertions, newline):
    """`text` avec, avant chaque ligne i de `insertions` ({i: [lignes]}), ces lignes (i = nombre de lignes : à la fin)."""
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    text = text[len(bom):]
    lines = Lines(text)
    out, pos = [bom], 0
    for index in sorted(insertions):
        at = lines.starts[index] if index < len(lines) else len(text)
        out.append(text[pos:at])
        if at == len(text) and text and not text.endswith(("\n", "\r")):
            out.append(newline)
        out.append("".join(line + newline for line in insertions[index]))
        pos = at
    out.append(text[pos:])
    return "".join(out)


def _magic_end(lines):
    """Première ligne après ce qui doit rester en tête du fichier : commentaires magiques (« % !TEX program = … »,
    « % !BIB … »), format « %&… » de la première ligne, et « \\pdfoutput=1 » des cinq premières lignes (arXiv)."""
    index = 0
    while index < len(lines) and (re.match(r"\s*%\s*!\s*(?:TEX|BIB)", lines.content(index), re.I)
                                  or (index == 0 and lines.content(0).startswith("%&"))):
        index += 1
    for line in range(index, min(5, len(lines))):
        if re.match(r"\s*\\pdfoutput\s*=?\s*1\b", lines.content(line)):
            index = line + 1
    return index


def _header(plan, counts, date):
    words, mode = plan.words, plan.mode
    lines = [f"% {MARK} {words['headTitle'].format(date=date, mode=words['modes'][mode])}",
             "% " + words["headCounts"].format(**{k: counts[k] for k in ("total", "exact", "approx", "unplaced", "header")})]
    lines += [f"% {line}" for line in words["headRemove"]]
    if plan.wide:
        lines += [f"% {line}" for line in words["layout"]]
    if plan.long_entries:
        lines += [f"% {line}".replace("({n})", f"({len(plan.long_entries)})") for line in words["endnotes"]]
    lines += [f"% {one_line(line)}" for line in plan.warnings]
    lines.append(f"% {END_MARK}")
    sheet = [e for e in plan.entries if e["kind"] == "general"]
    for entry in sheet:
        lines += comment_block(entry, words, plan.known)
    marks = sorted((e for e in plan.entries if e["kind"] == "bookmark"), key=lambda e: (e["page"] or 0, e["id"]))
    if marks:
        lines.append(f"% {MARK} {words['bookmarks']}")
        for entry in marks:
            where = words["page"].format(page=entry["page"]) if entry.get("page") else ""
            lines.append(f"% - {one_line(entry['title'])}" + (f" — {where}" if where else ""))
        lines.append(f"% {END_MARK}")
    unplaced = sorted((e for e in plan.entries if e["precision"] == "none"), key=lambda e: (e["page"] or 0, e["id"]))
    for entry in unplaced:
        title = entry["title"] + (f" ({words['reason'][entry['reason']]})" if entry.get("reason") in words["reason"] else "")
        block = comment_block({**entry, "title": words["unplacedOne"].format(title=title)}, words, plan.known)
        lines += block
    return lines


# largeur des notes en marge, fixée au début du document (après geometry) : la plus petite place libre des marges où
# LaTeX les met (pages impaires à droite, paires à gauche en recto verso ; l'inverse avec \\reversemarginpar ; les deux
# marges sur deux colonnes), moins 0,3 cm, jamais plus que la largeur choisie par le document s'il en a choisi une, jamais
# moins de 1 cm. Conditions de LaTeX recopiées par \\let avant usage (un \\csname if…\\endcsname ne s'emboîte pas).
MARGIN_WIDTH = [
    "\\newdimen\\arxivreaderWidth\\newdimen\\arxivreaderOther",
    "\\def\\arxivreaderKeep{\\ifdim\\arxivreaderOther<\\arxivreaderWidth\\arxivreaderWidth\\arxivreaderOther\\fi}",
    "\\def\\arxivreaderLeft#1{\\arxivreaderOther1in\\relax\\advance\\arxivreaderOther\\hoffset"
    "\\advance\\arxivreaderOther#1\\advance\\arxivreaderOther-\\marginparsep\\arxivreaderKeep}",
    "\\def\\arxivreaderRight#1{\\arxivreaderOther\\paperwidth\\advance\\arxivreaderOther-1in\\relax"
    "\\advance\\arxivreaderOther-\\hoffset\\advance\\arxivreaderOther-#1\\advance\\arxivreaderOther-\\textwidth"
    "\\advance\\arxivreaderOther-\\marginparsep\\arxivreaderKeep}",
    "\\AtBeginDocument{\\arxivreaderWidth\\maxdimen"
    "\\expandafter\\let\\expandafter\\arxivreaderIfTwoside\\csname if@twoside\\endcsname"
    "\\expandafter\\let\\expandafter\\arxivreaderIfTwocolumn\\csname if@twocolumn\\endcsname"
    "\\expandafter\\let\\expandafter\\arxivreaderIfReverse\\csname if@reversemargin\\endcsname",
    "\\arxivreaderIfTwocolumn\\arxivreaderLeft\\oddsidemargin\\arxivreaderRight\\oddsidemargin"
    "\\arxivreaderIfTwoside\\arxivreaderLeft\\evensidemargin\\arxivreaderRight\\evensidemargin\\fi",
    "\\else\\arxivreaderIfReverse\\arxivreaderLeft\\oddsidemargin\\else\\arxivreaderRight\\oddsidemargin\\fi"
    "\\arxivreaderIfTwoside\\arxivreaderIfReverse\\arxivreaderRight\\evensidemargin\\else"
    "\\arxivreaderLeft\\evensidemargin\\fi\\fi\\fi",
    "\\advance\\arxivreaderWidth-0.3cm\\relax{own}\\ifdim\\arxivreaderWidth<1cm\\arxivreaderWidth1cm\\relax\\fi"
    "\\global\\marginparwidth\\arxivreaderWidth}"]
OWN_WIDTH = "\\ifdim\\marginparwidth<\\arxivreaderWidth\\arxivreaderWidth\\marginparwidth\\fi"
SETS_WIDTH = re.compile(r"marginparwidth|\bmarginpar\s*=")  # \setlength{\marginparwidth}…, geometry marginparwidth=…

# Mise en page « wide » (mode « todo », par défaut) : plus de place pour les notes sans toucher aux coupures de lignes
# — \textwidth et \textheight ne changent pas, seul le bloc de texte se déplace et la page s'élargit.
# Calculée tout à la fin de \begin{document} (crochet begindocument/end, après geometry, hyperref et la classe) :
# - une colonne : texte décalé vers la gauche (marge de gauche WIDE_LEFT, jamais agrandie ; même place sur les pages
#   paires en recto verso), notes toujours dans la marge de droite (\normalmarginpar, sans alternance) ;
# - deux colonnes (option twocolumn, ou revtex : \twocolumn@sw) : texte non décalé (LaTeX met les notes de la colonne
#   de gauche dans la marge de gauche) ;
# - colonne des notes = marge − \marginparsep − WIDE_EDGE ; sous WIDE_MIN, page élargie (à droite ; à gauche aussi sur
#   deux colonnes) juste assez : \paperwidth, taille de la page du moteur (\pdfpagewidth de pdfTeX et XeTeX,
#   \pagewidth de LuaTeX, \special pdf:pagesize en DVI) et \stockwidth de memoir et de crop ; au-delà de WIDE_MAX,
#   colonne limitée à WIDE_MAX (lignes de notes trop longues à lire), le reste de la marge laissé vide ;
# - largeur de la page : la plus petite de \paperwidth et de celle du moteur (sans geometry ni hyperref, celle du
#   moteur peut être plus étroite que celle que LaTeX croit) ; avec crop, la page coupée (\paperwidth) et les marges
#   mesurées depuis son bord (crop met dans \hoffset la place de la page sur la feuille) ;
# - refaite après un changement de mise en page dans le texte : \twocolumn (acmart sigconf l'appelle dans \maketitle ;
#   texte remis à sa place d'origine, notes des deux côtés), \newgeometry, \restoregeometry, \loadgeometry (geometry y
#   remet ses propres valeurs) ; la page n'y redevient jamais plus étroite (\arxivreaderFloor) ;
# - valeurs obtenues écrites dans le journal (ligne « arXivReader », une par calcul).
# Dimensions suivies de \relax : sans lui, TeX développe ce qui suit l'unité (un \ifdim…) en cherchant une espace
# optionnelle, avant la fin de l'affectation en cours.
# La police des notes (NOTE_SIZE, 7 pt) : la plus petite taille dessinée telle quelle en Computer Modern, EC et Latin
# Modern (6,5 pt y est remplacé par 6 pt) ; on élargit la page plutôt que de la réduire encore.
LAYOUTS = ("wide", "keep")
WIDE_LEFT, WIDE_MIN, WIDE_MAX, WIDE_EDGE = "1.5cm", "4.5cm", "6cm", "0.4cm"
NOTE_SIZE = "\\fontsize{7}{8.5}\\selectfont"
SIZE_MACRO = "\\arxivreaderNoteSize"
WIDE_LAYOUT = [
    f"\\newcommand{{{SIZE_MACRO}}}{{{NOTE_SIZE}}}",
    "\\newdimen\\arxivreaderPageW\\newdimen\\arxivreaderLmin\\newdimen\\arxivreaderLmax\\newdimen\\arxivreaderMoreL"
    "\\newdimen\\arxivreaderMoreR\\newdimen\\arxivreaderShift\\newdimen\\arxivreaderWidth\\newdimen\\arxivreaderTmp",
    "\\newdimen\\arxivreaderBase\\newdimen\\arxivreaderRoom\\newdimen\\arxivreaderFloor\\newdimen\\arxivreaderWide"
    "\\newdimen\\arxivreaderOdd\\newdimen\\arxivreaderEven\\newdimen\\arxivreaderPaper\\newdimen\\arxivreaderStock"
    "\\newdimen\\arxivreaderEngine\\newif\\ifarxivreaderTwo\\newif\\ifarxivreaderDone\\newif\\ifarxivreaderDviHook",
    # largeur de la page du moteur → \arxivreaderTmp (inchangé en DVI) ; nouvelle largeur (\arxivreaderTmp) au moteur
    "\\def\\arxivreaderGetEngine{\\ifdefined\\directlua\\arxivreaderTmp\\pagewidth\\else\\ifdefined\\XeTeXversion"
    "\\arxivreaderTmp\\pdfpagewidth\\else\\ifdefined\\pdfoutput\\ifnum\\pdfoutput>0 \\arxivreaderTmp\\pdfpagewidth"
    "\\fi\\fi\\fi\\fi}",
    "\\def\\arxivreaderSetEngine{\\ifdefined\\directlua\\global\\pagewidth\\arxivreaderTmp\\else\\ifdefined\\XeTeXversion"
    "\\global\\pdfpagewidth\\arxivreaderTmp\\else\\ifdefined\\pdfoutput\\ifnum\\pdfoutput>0 \\global\\pdfpagewidth"
    "\\arxivreaderTmp\\else\\arxivreaderDvi\\fi\\else\\arxivreaderDvi\\fi\\fi\\fi}",
    "\\def\\arxivreaderDvi{\\ifarxivreaderDviHook\\else\\global\\arxivreaderDviHooktrue\\ifdefined\\AddToHook"
    "\\AddToHook{shipout/before}{\\special{pdf:pagesize width \\the\\arxivreaderPageW\\space height \\the\\paperheight}}"
    "\\fi\\fi}",
    # côté des notes : toujours à droite (une colonne) ; côtés d'origine gardés pour \twocolumn
    # (\\iftrue, \\iffalse cachés dans des macros : dans une branche sautée, ils seraient comptés)
    "\\def\\arxivreaderRightNotes{\\expandafter\\global\\expandafter\\let\\csname if@mparswitch\\endcsname\\iffalse"
    "\\expandafter\\global\\expandafter\\let\\csname if@reversemargin\\endcsname\\iffalse}",
    "\\def\\arxivreaderSaveSides{\\expandafter\\global\\expandafter\\let\\expandafter\\arxivreaderSwitch"
    "\\csname if@mparswitch\\endcsname\\expandafter\\global\\expandafter\\let\\expandafter\\arxivreaderReverse"
    "\\csname if@reversemargin\\endcsname}",
    "\\def\\arxivreaderSides{\\expandafter\\global\\expandafter\\let\\csname if@mparswitch\\endcsname\\arxivreaderSwitch"
    "\\expandafter\\global\\expandafter\\let\\csname if@reversemargin\\endcsname\\arxivreaderReverse}",
    "\\def\\arxivreaderMm#1{\\the\\numexpr\\dimexpr#1\\relax/186467\\relax\\space mm}",
    # deux colonnes : option twocolumn, ou revtex (colonnes de ltxgrid, \\if@twocolumn faux)
    "\\def\\arxivreaderColumns{\\expandafter\\let\\expandafter\\arxivreaderIf\\csname if@twocolumn\\endcsname"
    "\\arxivreaderIf\\global\\arxivreaderTwotrue\\fi"
    "\\ifcsname twocolumn@sw\\endcsname\\csname twocolumn@sw\\endcsname{\\global\\arxivreaderTwotrue}{}\\fi}",
    # calcul, à partir des marges, de \\paperwidth et des côtés des notes en vigueur
    "\\def\\arxivreaderCompute{"
    "\\expandafter\\let\\expandafter\\arxivreaderIfTwoside\\csname if@twoside\\endcsname"
    "\\arxivreaderBase1in\\relax\\ifcsname ver@crop.sty\\endcsname\\else\\advance\\arxivreaderBase\\hoffset\\fi",
    # largeur de la page ; plus petite et plus grande marge de gauche (pages impaires, paires en recto verso)
    "\\arxivreaderPageW\\paperwidth\\ifcsname ver@crop.sty\\endcsname\\else\\arxivreaderTmp0pt\\relax\\arxivreaderGetEngine"
    "\\ifdim\\arxivreaderTmp>0pt\\relax\\ifdim\\arxivreaderTmp<\\arxivreaderPageW\\arxivreaderPageW\\arxivreaderTmp"
    "\\fi\\fi\\fi",
    "\\arxivreaderLmin\\arxivreaderBase\\advance\\arxivreaderLmin\\oddsidemargin\\arxivreaderLmax\\arxivreaderLmin"
    "\\arxivreaderIfTwoside\\arxivreaderTmp\\arxivreaderBase\\advance\\arxivreaderTmp\\evensidemargin"
    "\\ifdim\\arxivreaderTmp<\\arxivreaderLmin\\arxivreaderLmin\\arxivreaderTmp\\fi"
    "\\ifdim\\arxivreaderTmp>\\arxivreaderLmax\\arxivreaderLmax\\arxivreaderTmp\\fi\\fi",
    "\\arxivreaderMoreL0pt\\relax\\arxivreaderMoreR0pt\\relax\\arxivreaderShift0pt\\relax",
    # deux colonnes : place à gauche (\\arxivreaderWidth) et à droite (\\arxivreaderRoom), chacune élargie au besoin
    "\\ifarxivreaderTwo"
    "\\arxivreaderWidth\\arxivreaderLmin\\advance\\arxivreaderWidth-\\marginparsep\\advance\\arxivreaderWidth-{edge}\\relax"
    "\\ifdim\\arxivreaderWidth<{min}\\relax\\arxivreaderMoreL{min}\\relax\\advance\\arxivreaderMoreL-\\arxivreaderWidth\\fi",
    "\\arxivreaderRoom\\arxivreaderPageW\\advance\\arxivreaderRoom-\\arxivreaderLmax\\advance\\arxivreaderRoom-\\textwidth"
    "\\advance\\arxivreaderRoom-\\marginparsep\\advance\\arxivreaderRoom-{edge}\\relax"
    "\\ifdim\\arxivreaderRoom<{min}\\relax\\arxivreaderMoreR{min}\\relax\\advance\\arxivreaderMoreR-\\arxivreaderRoom\\fi",
    "\\global\\advance\\oddsidemargin\\arxivreaderMoreL\\global\\advance\\evensidemargin\\arxivreaderMoreL",
    # une colonne : marge de gauche ramenée à {left} au plus, la même sur toutes les pages ; notes à droite
    "\\else\\arxivreaderTmp\\arxivreaderLmin\\ifdim\\arxivreaderTmp>{left}\\relax\\arxivreaderTmp{left}\\relax\\fi"
    "\\arxivreaderShift\\arxivreaderBase\\advance\\arxivreaderShift\\oddsidemargin\\advance\\arxivreaderShift-\\arxivreaderTmp",
    "\\global\\oddsidemargin\\arxivreaderTmp\\global\\advance\\oddsidemargin-\\arxivreaderBase"
    "\\global\\evensidemargin\\oddsidemargin",
    "\\arxivreaderRoom\\arxivreaderPageW\\advance\\arxivreaderRoom-\\arxivreaderTmp\\advance\\arxivreaderRoom-\\textwidth"
    "\\advance\\arxivreaderRoom-\\marginparsep\\advance\\arxivreaderRoom-{edge}\\relax"
    "\\ifdim\\arxivreaderRoom<{min}\\relax\\arxivreaderMoreR{min}\\relax\\advance\\arxivreaderMoreR-\\arxivreaderRoom\\fi",
    "\\arxivreaderWidth\\maxdimen\\arxivreaderRightNotes\\fi",
    # page jamais plus étroite qu'au calcul précédent ; colonne = la plus étroite des marges, au plus {max}
    "\\arxivreaderTmp\\arxivreaderPageW\\advance\\arxivreaderTmp\\arxivreaderMoreL\\advance\\arxivreaderTmp\\arxivreaderMoreR"
    "\\ifdim\\arxivreaderTmp<\\arxivreaderFloor\\advance\\arxivreaderMoreR\\arxivreaderFloor"
    "\\advance\\arxivreaderMoreR-\\arxivreaderTmp\\fi",
    "\\advance\\arxivreaderRoom\\arxivreaderMoreR\\ifarxivreaderTwo\\advance\\arxivreaderWidth\\arxivreaderMoreL\\fi"
    "\\ifdim\\arxivreaderRoom<\\arxivreaderWidth\\arxivreaderWidth\\arxivreaderRoom\\fi"
    "\\ifdim\\arxivreaderWidth>{max}\\relax\\arxivreaderWidth{max}\\relax\\fi",
    # page élargie : LaTeX, memoir et crop, puis le moteur (jamais plus étroit qu'avant)
    "\\arxivreaderTmp\\arxivreaderMoreL\\advance\\arxivreaderTmp\\arxivreaderMoreR"
    "\\global\\arxivreaderWide\\arxivreaderPageW\\global\\advance\\arxivreaderWide\\arxivreaderTmp"
    "\\ifdim\\arxivreaderTmp>0pt\\relax"
    "\\advance\\arxivreaderPageW\\arxivreaderTmp\\global\\paperwidth\\arxivreaderPageW"
    "\\ifdefined\\stockwidth\\global\\advance\\stockwidth\\arxivreaderTmp"
    "\\ifdim\\stockwidth>\\arxivreaderPageW\\arxivreaderPageW\\stockwidth\\fi\\fi",
    "\\arxivreaderTmp0pt\\relax\\arxivreaderGetEngine\\ifdim\\arxivreaderTmp<\\arxivreaderPageW"
    "\\arxivreaderTmp\\arxivreaderPageW\\fi\\arxivreaderPageW\\arxivreaderTmp\\arxivreaderSetEngine\\fi",
    "\\global\\arxivreaderPageW\\arxivreaderPageW\\global\\marginparwidth\\arxivreaderWidth",
    "\\advance\\arxivreaderMoreR\\arxivreaderMoreL\\typeout{{log}}}",
    # premier calcul : valeurs d'origine gardées (pour \\twocolumn et les changements de geometry)
    "\\def\\arxivreaderLayout{\\global\\arxivreaderOdd\\oddsidemargin\\global\\arxivreaderEven\\evensidemargin"
    "\\global\\arxivreaderPaper\\paperwidth\\global\\arxivreaderStock0pt\\relax"
    "\\ifdefined\\stockwidth\\global\\arxivreaderStock\\stockwidth\\fi"
    "\\arxivreaderTmp0pt\\relax\\arxivreaderGetEngine\\global\\arxivreaderEngine\\arxivreaderTmp",
    "\\arxivreaderSaveSides\\global\\arxivreaderFloor0pt\\relax\\global\\arxivreaderTwofalse\\arxivreaderColumns"
    "\\arxivreaderCompute\\global\\arxivreaderDonetrue\\arxivreaderSetup}",
    # nouveau calcul : page et feuille d'origine, moteur compris ; page jamais plus étroite qu'avant
    "\\def\\arxivreaderAgain{\\global\\arxivreaderFloor\\arxivreaderWide"
    "\\ifdefined\\stockwidth\\global\\stockwidth\\arxivreaderStock\\fi"
    "\\ifdim\\arxivreaderEngine>0pt\\relax\\arxivreaderTmp\\arxivreaderEngine\\arxivreaderSetEngine\\fi"
    "\\arxivreaderCompute}",
    # \\twocolumn après \\begin{document} (une seule fois) : texte remis à sa place, notes des deux côtés, après la page
    # en cours (\\clearpage, comme \\twocolumn lui-même)
    "\\def\\arxivreaderToTwo{\\ifarxivreaderTwo\\else\\clearpage\\global\\arxivreaderTwotrue"
    "\\global\\oddsidemargin\\arxivreaderOdd\\global\\evensidemargin\\arxivreaderEven\\global\\paperwidth\\arxivreaderPaper"
    "\\arxivreaderSides\\arxivreaderAgain\\fi}",
    "\\let\\arxivreaderTwocolumn\\twocolumn",
    "\\def\\twocolumn{\\ifarxivreaderDone\\expandafter\\arxivreaderToTwo\\fi\\arxivreaderTwocolumn}",
    # geometry : \\newgeometry, \\restoregeometry, \\loadgeometry remettent ses valeurs (marges, \\paperwidth,
    # \\marginparwidth) ; nouvelles marges d'origine, puis nouveau calcul
    "\\def\\arxivreaderGeometry{\\ifarxivreaderDone\\global\\arxivreaderOdd\\oddsidemargin"
    "\\global\\arxivreaderEven\\evensidemargin\\global\\arxivreaderPaper\\paperwidth\\arxivreaderColumns"
    "\\arxivreaderAgain\\fi}",
    "\\ifdefined\\newgeometry\\let\\arxivreaderNewgeometry\\newgeometry"
    "\\long\\def\\newgeometry#1{\\arxivreaderNewgeometry{#1}\\arxivreaderGeometry}\\fi",
    "\\ifdefined\\restoregeometry\\let\\arxivreaderRestoregeometry\\restoregeometry"
    "\\def\\restoregeometry{\\arxivreaderRestoregeometry\\arxivreaderGeometry}\\fi",
    "\\ifdefined\\loadgeometry\\let\\arxivreaderLoadgeometry\\loadgeometry"
    "\\def\\loadgeometry#1{\\arxivreaderLoadgeometry{#1}\\arxivreaderGeometry}\\fi",
    "\\ifdefined\\AddToHook\\AddToHook{begindocument/end}{\\arxivreaderLayout}\\else"
    "\\AtBeginDocument{\\arxivreaderLayout}\\fi"]


# Note en mode vertical (mise en page « wide ») — entre deux paragraphes, après \\maketitle, avant un environnement :
# todonotes y ouvre un paragraphe vide pour son trait (\\vskip-\\baselineskip\\noindent…), ce qui décale tout le
# texte qui suit, et le \\marginpar cache le dernier espace vertical (\\lastskip) aux \\addvspace suivants. Ici : la
# boîte de la note seulement (sans trait ni entrée dans \\listoftodos), le dernier espace retiré puis remis après elle
# (précédé de \\nobreak : pas de coupure de page de plus). Pas dans une étiquette en attente (\\item, début d'énoncé,
# titre en ligne) : todonotes y passe en mode horizontal, comme le texte.
# Note au début d'un paragraphe encore vide (après une formule centrée, après \\noindent) : la boîte seulement aussi —
# le repère du trait (tikzpicture, baseline=-0.75ex) donnerait 0,75 ex de hauteur à une ligne sans texte. Si la ligne
# suivante de la source termine de toute façon le paragraphe (ligne vide, \\section, \\item, \\begin{theorem}…, voir
# _ends_paragraph), \\arxivreaderPar le termine d'abord (sans ligne, comme TeX pour un paragraphe vide) : la note y
# est entre deux paragraphes ; sinon elle ferait une ligne vide.
# Sortie de LaTeX pour nos notes (\\@specialoutput, une fois par \\marginpar) : il remet la page en cours puis les notes
# de bas de page à sa fin (\\@reinserts) — la place qu'elles prennent ne compte alors qu'à partir de là, et la page
# peut se couper autrement (premières lignes d'un énoncé en bas d'une page qui a une note de bas de page). Ici, pour nos
# notes seulement (repérées dans l'ordre des \\marginpar, \\arxivreaderMparFlags) : notes de bas de page remises avant
# la page. (La coupure possible qu'il ajoute après la note, \\interlinepenalty, est gardée : la retirer déplace
# d'autres coupures, notes entre deux paragraphes.)
WIDE_TODO = [
    "\\newcount\\arxivreaderLast\\newskip\\arxivreaderSkip\\newif\\ifarxivreaderV\\newif\\ifarxivreaderH",
    "\\def\\arxivreaderMarginOnly{\\marginpar[\\csname @todonotes@drawMarginNote\\endcsname]"
    "{\\csname @todonotes@drawMarginNote\\endcsname}}",
    "\\def\\arxivreaderVcheck{\\arxivreaderVfalse\\arxivreaderHfalse\\ifvmode\\arxivreaderVtrue\\else"
    "\\ifnum\\lastnodetype=-1 \\arxivreaderHtrue\\fi\\fi"
    "\\expandafter\\let\\expandafter\\arxivreaderIf\\csname if@inlabel\\endcsname"
    "\\arxivreaderIf\\arxivreaderVfalse\\arxivreaderHfalse\\fi"
    "\\expandafter\\let\\expandafter\\arxivreaderIf\\csname if@noskipsec\\endcsname"
    "\\arxivreaderIf\\arxivreaderVfalse\\arxivreaderHfalse\\fi}",
    "\\def\\arxivreaderVtodo#1#2{\\arxivreaderLast\\lastnodetype\\arxivreaderSkip\\lastskip"
    "\\ifnum\\arxivreaderLast=11 \\vskip-\\arxivreaderSkip\\fi\\begingroup"
    "\\expandafter\\let\\csname @todonotes@drawMarginNoteWithLine\\endcsname\\arxivreaderMarginOnly"
    "{call}[nolist,#1]{#2}\\endgroup\\ifnum\\arxivreaderLast=11 \\penalty10000 \\vskip\\arxivreaderSkip\\fi}",
    "\\def\\arxivreaderHtodo#1#2{\\begingroup"
    "\\expandafter\\let\\csname @todonotes@drawMarginNoteWithLine\\endcsname\\arxivreaderMarginOnly"
    "{call}[nolist,#1]{#2}\\endgroup}",
    "\\newcommand{{macro}}[2][]{{guard}\\global\\arxivreaderOurstrue\\arxivreaderVcheck\\ifarxivreaderV"
    "\\arxivreaderVtodo{#1}{#2}\\else\\ifarxivreaderH\\arxivreaderHtodo{#1}{#2}\\else{call}[#1]{#2}\\fi\\fi"
    "\\global\\arxivreaderOursfalse\\fi\\fi}",
    "\\def{par}{{guard}\\ifhmode\\ifnum\\lastnodetype=-1 {\\csname @@par\\endcsname}\\fi\\fi\\fi\\fi}",
    # (lignes suivantes avec @ lettre, rendu tel qu'il était)
    "\\edef\\arxivreaderAt{\\the\\catcode`\\@}\\catcode`\\@=11 ",
    "\\newif\\ifarxivreaderOurs\\def\\arxivreaderMparFlags{}",
    "\\def\\arxivreaderMparPush{\\ifnum\\@floatpenalty<\\z@\\xdef\\arxivreaderMparFlags{\\arxivreaderMparFlags"
    "\\ifarxivreaderOurs1\\else0\\fi}\\fi}",
    "\\def\\arxivreaderHead\\@elt#1#2\\relax{\\let\\arxivreaderBox#1}",
    "\\def\\arxivreaderPop#1#2\\relax{\\gdef\\arxivreaderMparFlags{#2}\\if1#1\\arxivreaderFront\\fi}",
    "\\def\\arxivreaderFront{\\global\\setbox\\@holdpg\\vbox{\\@reinserts\\unvbox\\@holdpg}}",
    # \\outputpenalty −10002 (note dans un paragraphe) ou −10003 (entre deux) ; en tête de \\@currlist, la boîte d'un
    # \\marginpar a le compte −1 (celle d'un flottant, un compte positif)
    "\\def\\arxivreaderSpecial{\\ifnum\\outputpenalty<-\\@Mi\\ifnum\\outputpenalty>-\\@Miv"
    "\\ifx\\@currlist\\@empty\\else\\expandafter\\arxivreaderHead\\@currlist\\relax\\ifnum\\count\\arxivreaderBox<\\z@"
    "\\ifx\\arxivreaderMparFlags\\@empty\\else\\expandafter\\arxivreaderPop\\arxivreaderMparFlags\\relax"
    "\\fi\\fi\\fi\\fi\\fi}",
    "\\def\\arxivreaderSetup{\\let\\arxivreaderXympar\\@xympar\\def\\@xympar{\\arxivreaderMparPush\\arxivreaderXympar}"
    "\\let\\arxivreaderSpecialSaved\\@specialoutput\\def\\@specialoutput{\\arxivreaderSpecial\\arxivreaderSpecialSaved}}",
    "\\catcode`\\@=\\arxivreaderAt\\relax"]
PAR_MACRO = "\\arxivreaderPar"

# Notes longues (Plan.number_long) : repère de la note en marge et de son texte en fin de document — numéro de page
# écrit dans le .aux (\arxivreaderPageIs, relu au début de la compilation suivante : comme \label, sans \label ni
# \pageref, que d'autres paquets redéfinissent : refcheck, cleveref, showkeys…) et, avec hyperref, cible de lien ;
# renvoi « → note N, p. X » (lien avec hyperref) et « (page Y) » vers la note en marge (omis si elle n'a pas été
# composée : note sautée dans une boîte ou une formule, \ifinner, \ifmmode). Page encore inconnue (première
# compilation) : « ?? » et l'avertissement des renvois indéfinis de LaTeX. hyperref cherché à la compilation (chargé
# avant ou après nous). Commandes robustes : jamais développées dans un argument mobile.
ENDNOTE_MACROS = [
    "\\edef\\arxivreaderAt{\\the\\catcode`\\@}\\catcode`\\@=11 ",  # (lignes suivantes avec @ lettre)
    "\\newcommand{\\arxivreaderPageIs}[2]{\\expandafter\\gdef\\csname arxivreaderP@#1\\endcsname{#2}}",
    "\\newcommand{\\arxivreaderPageOf}[1]{\\ifcsname arxivreaderP@#1\\endcsname\\csname arxivreaderP@#1\\endcsname"
    "\\else\\textbf{??}\\G@refundefinedtrue\\fi}",
    "\\DeclareRobustCommand{\\arxivreaderMark}[1]{\\protected@write\\@auxout{}{\\string\\arxivreaderPageIs{#1}{\\thepage}}"
    "\\ifcsname ver@hyperref.sty\\endcsname\\hypertarget{arxivreader-#1}{}\\fi}",
    "\\catcode`\\@=\\arxivreaderAt\\relax",
    "\\DeclareRobustCommand{\\arxivreaderNoteMark}[1]{\\arxivreaderMark{note-#1}}",
    "\\DeclareRobustCommand{\\arxivreaderEndMark}[1]{\\arxivreaderMark{end-#1}}",
    "\\DeclareRobustCommand{\\arxivreaderFit}[2]{\\ifdim\\marginparwidth<{narrow}#1\\else#2\\fi}",
    "\\DeclareRobustCommand{\\arxivreaderEndRef}[1]{\\ifcsname ver@hyperref.sty\\endcsname"
    "\\hyperlink{arxivreader-end-#1}{$\\rightarrow$ note #1, p.~\\arxivreaderPageOf{end-#1}}"
    "\\else$\\rightarrow$ note #1, p.~\\arxivreaderPageOf{end-#1}\\fi}",
    "\\DeclareRobustCommand{\\arxivreaderEndBack}[1]{\\ifcsname arxivreaderP@note-#1\\endcsname\\space("
    "\\ifcsname ver@hyperref.sty\\endcsname\\hyperlink{arxivreader-note-#1}{{page}~\\arxivreaderPageOf{note-#1}}"
    "\\else {page}~\\arxivreaderPageOf{note-#1}\\fi)\\fi}"]

# environnements qui commencent par \\par (listes de LaTeX) : une note juste avant, au début d'un paragraphe vide, y
# est entre deux paragraphes (avec ceux de l'auteur déclarés par \\newtheorem, \\declaretheorem, \\spnewtheorem)
PAR_ENVS = {"itemize", "enumerate", "description", "list", "trivlist", "center", "flushleft", "flushright", "quote",
            "quotation", "verse", "proof", "abstract", "thebibliography", "theorem", "lemma", "proposition",
            "corollary", "definition", "remark", "example", "conjecture", "claim", "notation", "exercise", "problem",
            "question", "hypothesis", "assumption", "fact", "observation"}
PAR_ENDS = {"itemize", "enumerate", "description", "center", "flushleft", "flushright", "quote", "quotation", "verse"}
THEOREMS = re.compile(r"\\(?:newtheorem|spnewtheorem)\*?\s*\{\s*([^{}\s]+)\s*\}|\\declaretheorem\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
PAR_START = re.compile(r"\s*(?:\\(par|item|bibitem|part|chapter|section|subsection|subsubsection|paragraph|subparagraph)"
                       r"(?![A-Za-z@])|\\(begin|end)\s*\{\s*([^{}]*?)\s*\})")


def _preamble(plan):
    """Lignes ajoutées avant \\begin{document} en mode « todo » ; None si rien à ajouter."""
    text = plan.located["text"] if plan.located else _strip_comments(plan.files.get(plan.main.name, ""))
    begin = text.find("\\begin{document}")
    loaded, disabled, own = _todo_setup(text[:begin] if begin >= 0 else text)
    own = own or _todo_setup(text)[2]
    width_set = bool(SETS_WIDTH.search(text[:begin] if begin >= 0 else text))
    if disabled:
        plan.warnings.append(plan.words["warn"]["disabled"])
    lines = [f"% {MARK} {plan.words['preamble']}"]
    if not loaded and not width_set:  # (todonotes le demande à son chargement ; remplacé au début du document)
        lines.append(f"\\setlength{{\\marginparwidth}}{{2cm}}% {MARK}")
    if plan.layout == "wide":  # largeur choisie par le document ignorée : la colonne des notes est la nôtre
        log = plan.words["layoutLog"].format(width="\\arxivreaderMm{\\arxivreaderWidth}",
                                             shift="\\arxivreaderMm{\\arxivreaderShift}", more="\\arxivreaderMm{\\arxivreaderMoreR}")
        lines += [line.replace("{edge}", WIDE_EDGE).replace("{min}", WIDE_MIN).replace("{max}", WIDE_MAX)
                  .replace("{left}", WIDE_LEFT).replace("{log}", log) + f"% {MARK}" for line in WIDE_LAYOUT]
    else:
        lines += [line.replace("{own}", OWN_WIDTH if width_set else "") + f"% {MARK}" for line in MARGIN_WIDTH]
    lines.append(f"{SPLIT_MACRO}% {MARK}")
    if own and not loaded:
        plan.warnings.append(plan.words["warn"]["ownTodo"])
    call = "\\todo" if loaded else "\\arxivreaderTodonotes"
    if not loaded:  # \\todo du document (même défini dans un .sty du projet) sauvegardé, puis remis après todonotes
        lines += [f"\\let\\arxivreaderSavedTodo\\todo\\expandafter\\let\\expandafter\\arxivreaderSavedTodoArgs\\csname\\string\\todo\\endcsname% {MARK}",
                  f"\\let\\todo\\relax\\usepackage{{todonotes}}% {MARK}",
                  f"\\expandafter\\let\\expandafter\\arxivreaderTodonotes\\csname\\string\\todo\\endcsname% {MARK}",
                  f"\\let\\todo\\arxivreaderSavedTodo\\expandafter\\let\\csname\\string\\todo\\endcsname\\arxivreaderSavedTodoArgs% {MARK}"]
    if plan.layout == "wide":  # notes entre deux paragraphes sans décaler le texte (WIDE_TODO)
        lines += [line.replace("{call}", call).replace("{macro}", MACRO).replace("{guard}", GUARD)
                  .replace("{par}", PAR_MACRO) + f"% {MARK}" for line in WIDE_TODO]
    else:
        lines.append(f"\\newcommand{{{MACRO}}}[2][]{{{GUARD}{call}[#1]{{#2}}\\fi\\fi}}% {MARK}")
    if plan.long_entries:
        lines += [line.replace("{page}", plan.words["endPage"]).replace("{narrow}", NARROW["width"]) + f"% {MARK}"
                  for line in ENDNOTE_MACROS]
        if plan.end_document is None:  # \\end{document} partagé avec du texte : section ajoutée d'ici
            lines += _end_hook(plan)
    lines.append(f"% {END_MARK}")
    return lines


def _end_document(plan):
    """(fichier de la copie, ligne) de \\end{document}, seul sur sa ligne (espaces et commentaire mis à part), dans le
    corps du document hors de tout environnement ou groupe ; None sinon (la section des notes longues est alors
    ajoutée depuis le préambule)."""
    names = sorted(plan.files, key=lambda n: (plan.begin_document is None or n != plan.begin_document[0], n != plan.main.name, n))
    for name in names:
        lines = plan.lines[name]
        if "\\end" not in plan.files[name]:
            continue
        for index in range(len(lines)):
            content = lines.content(index)
            match = re.search(r"\\end\s*\{document\}", content)
            if not match or re.search(r"(?<!\\)%", content[:match.start()]):
                continue
            states = plan.states_of(name)
            if not states[index][0]:  # dans un verbatim
                continue
            if content[:match.start()].strip() or not states[index][1] or not plan.copy_name.get(name):
                return None
            return plan.copy_name[name], index
    return None


def _end_hook(plan):
    """Section des notes longues confiée à \\AtEndDocument : composée après tout ce que \\end{document} compose
    encore (adresses des auteurs d'amsart, flottants d'endfloat…), dont les pages ne changent donc pas."""
    return [f"\\AtEndDocument{{% {MARK}"] + _end_section(plan) + [f"}}% {MARK}"]


def _end_section(plan):
    """Lignes LaTeX de la section des notes longues (chacune marquée « % [arXivReader] ») : nouvelle page, titre sans
    numéro (ni compteur de section ni table des matières touchés), puis chaque note : « Note N — titre (page Y) », le
    passage cité, son texte entier en taille normale (long_tex)."""
    words, known = plan.words, getattr(plan, "known", set())
    lines = ["\\clearpage",
             "\\begingroup\\normalsize\\parindent0pt\\parskip0.5\\baselineskip\\relax\\partopsep0pt\\relax"
             "\\emergencystretch3em\\relax",
             "\\noindent{\\large\\bfseries " + _text_chars(words["endTitle"]) + "}\\par\\medskip"]
    for entry in plan.long_entries:
        number = entry["endnote"]
        title = _breakable(one_line(plan._margin_title(entry)))
        lines.append(f"\\par\\medskip{{\\raggedright\\noindent\\arxivreaderEndMark{{{number}}}"
                     f"\\textbf{{Note {number} --- {title}}}\\arxivreaderEndBack{{{number}}}\\par}}\\nobreak")
        if entry.get("quote") and _text_only(entry["quote"]):  # passage cité, en italique entre guillemets anglais
            # (présents dans tous les codages) ; omis s'il a des caractères sans équivalent (« ? » : formules du PDF)
            lines.append("{\\itshape ``" + inline_tex(one_line(entry["quote"], MAX_QUOTE), known) + "''\\par}\\nobreak")
        lines += long_tex(entry.get("note") or "", known)
    lines.append("\\endgroup")
    return [f"{line}% {MARK}" for line in lines]


def _text_only(text):
    """Le texte s'écrit-il sans « ? » de remplacement (_text_chars) ?"""
    return "?" not in _text_chars((text or "").replace("?", ""))


def _ends_paragraph(plan, name, line):
    """La source termine-t-elle le paragraphe juste avant la ligne `line` de `name` (avant tout texte) : ligne vide,
    \\par, \\item, titre de section, début d'une liste ou d'un énoncé (PAR_ENVS et les \\newtheorem du projet), fin
    d'une liste (PAR_ENDS) ? Les lignes de commentaires seuls ne comptent pas (rien pour TeX)."""
    lines = plan.lines.get(name)
    if lines is None:
        return False
    if plan.theorems is None:
        plan.theorems = {a or b.strip() for text in plan.files.values()
                         for a, b in THEOREMS.findall(_strip_comments(text))}
        plan.theorems |= {part.strip() for name_ in list(plan.theorems) if "," in name_ for part in name_.split(",")}
    for index in range(line, len(lines)):
        raw = lines.content(index)
        if not raw.strip():
            return index + 1 < len(lines)  # ligne vide (pas la fin du fichier, où rien ne suit)
        text = _strip_comments(raw)
        if not text.strip():
            continue
        match = PAR_START.match(text)
        if not match:
            return False
        if match.group(1):
            return True
        names = PAR_ENVS | plan.theorems if match.group(2) == "begin" else PAR_ENDS
        return match.group(3) in names or match.group(3).rstrip("*") in names
    return False


def _insertions(plan):
    """{fichier de la copie: {ligne: [lignes ajoutées]}}."""
    out = {}
    counts = plan.counts()
    date = time.strftime("%Y-%m-%d")
    main = plan.copy_name.get(plan.main.name) or plan.main.name

    def add(name, line, new):
        out.setdefault(name, {}).setdefault(line, []).extend(new)

    preamble = None
    plan.end_document = None
    if plan.mode == "todo" and any(e.get("todoLine") is not None and e["precision"] in ("exact", "approx")
                                   for e in plan.entries):
        if plan.begin_document and plan.copy_name.get(plan.begin_document[0]) and not plan.begin_shared:
            if plan.endnotes:
                plan.number_long()
                plan.end_document = _end_document(plan) if plan.long_entries else None
            preamble = _preamble(plan)  # (ses avertissements dans l'en-tête)
            plan.wide = plan.layout == "wide"
        else:  # (\\documentclass sur la ligne de \\begin{document} : rien ne peut être ajouté entre les deux)
            plan.warnings.append(plan.words["warn"]["sharedLine" if plan.begin_shared else "noDocument"])
            for entry in plan.entries:
                entry["todoLine"] = None
    add(main, _magic_end(plan.lines.get(plan.main.name) or Lines("")), _header(plan, counts, date))
    if preamble:
        add(plan.copy_name[plan.begin_document[0]], plan.begin_document[1], preamble)
    placed = sorted((e for e in plan.entries if e["precision"] in ("exact", "approx")),
                    key=lambda e: (e["rank"], e["id"]))
    for name in sorted({e["file"] for e in placed}):
        lines = {}
        for entry in (e for e in placed if e["file"] == name):
            lines.setdefault(entry["line"], []).append((entry, "comment"))
            if entry.get("todoLine") is not None:
                lines.setdefault(entry["todoLine"], []).append((entry, "todo"))
        for line, items in lines.items():
            new = []
            # « wide » : paragraphe terminé d'abord s'il est encore vide et que la source le termine juste après
            par = PAR_MACRO if plan.wide and _ends_paragraph(plan, name, line) else ""
            for entry, part in sorted(items, key=lambda item: (item[0]["rank"], item[0]["id"], item[1] == "todo")):
                if part == "comment":
                    new += comment_block(entry, plan.words, plan.known)
                else:
                    color = COLORS.get(entry.get("color") or "", NOTE_COLOR) if entry["kind"] in ("highlight", "zone") else NOTE_COLOR
                    size = SIZE_MACRO if plan.wide else "\\scriptsize"
                    new.append(f"{par}{MACRO}[color={color},size={size}]{{{entry['short']}}}% {MARK}")
            add(plan.copy_name[name], line, new)
    if plan.long_entries and plan.end_document:  # après tout ce qui précède \\end{document}
        add(plan.end_document[0], plan.end_document[1],
            [f"% {MARK} {words_end(plan)}"] + _end_hook(plan) + [f"% {END_MARK}"])
    return out


def words_end(plan):
    return f"{plan.words['endTitle']} — {plan.words['endComment']}"


def _texts(plan, insertions):
    """{fichier de la copie: octets annotés}, et la liste des fichiers en Latin-1."""
    by_rel = {rel: path for rel, path, _ in plan.copy}
    errors = ERRORS.get(plan.language, ERRORS["en"])
    written, latin = {}, []
    for rel, lines in insertions.items():
        path = by_rel.get(rel)
        if path is None:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            raise ExportError(errors["unreadableFile"].format(path=rel))
        text, encoding = _decode(raw)
        source = next((n for n, c in plan.copy_name.items() if c == rel), None)
        if source and len(Lines(text)) != len(plan.lines[source]):  # modifié depuis l'analyse : rien de mal placé
            raise ExportError(errors["changed"])
        if encoding == "latin-1":
            latin.append(rel)
        annotated = insert_lines(text, lines, _newline(text))
        written[rel] = annotated.encode(encoding, errors="replace")
    return written, latin


def _prepare(pid, mode, language, layout, endnotes):
    """Plan de la copie, fichiers annotés (octets) et nombres de notes, avertissements complétés : commun au résumé et
    à l'archive, qui montrent ainsi les mêmes avertissements."""
    plan = Plan(pid, mode, language, layout, endnotes)
    written, latin = _texts(plan, _insertions(plan))
    if latin:
        plan.warnings.append(plan.words["warn"]["latin1"].format(files=", ".join(latin)))
    return plan, written, plan.counts()


def summary(pid, mode, language, layout="wide", endnotes=True):
    """Ce que contiendra la copie (sans rien écrire) : nombres de notes placées, SyncTeX, taille, avertissements,
    notes longues renvoyées en fin de document."""
    plan, _, counts = _prepare(pid, mode, language, layout, endnotes)
    return {**{k: counts[k] for k in ("total", "exact", "approx", "unplaced", "header", "byKind")},
            "synctex": plan.sync is not None, "files": len(plan.copy), "size": plan.size, "folder": str(plan.root),
            "warnings": plan.warnings,
            "todoMacro": MACRO if mode == "todo" else None, "mode": mode, "layout": plan.layout,
            "endnotes": len(plan.long_entries)}


def _readme(plan, counts, date):
    words = plan.words
    mode = words["modes"][plan.mode]
    lines = [line.format(date=date, name=plan.root.name, mode=mode,
                         counts=words["counts"].format(**{k: counts[k] for k in ("total", "exact", "approx", "unplaced", "header")}))
             for line in words["readme"]]
    if plan.wide:
        lines += [""] + words["layout"]
    if plan.long_entries:
        lines += [""] + [line.replace("({n})", f"({len(plan.long_entries)})") for line in words["endnotes"]]
    if plan.warnings:
        lines += ["", words["readmeWarnings"]] + [f"- {one_line(w)}" for w in plan.warnings]
    unplaced = sorted((e for e in plan.entries if e["precision"] == "none"), key=lambda e: (e["page"] or 0, e["id"]))
    if unplaced:
        lines += ["", words["readmeUnplaced"].format(main=plan.main.name)]
        for entry in unplaced:
            reason = words["reason"].get(entry.get("reason") or "", "")
            lines.append(f"- {one_line(entry['title'])}" + (f" ({reason})" if reason else "")
                         + (" — " + words["quote"].format(quote=one_line(entry["quote"], 120)) if entry.get("quote") else ""))
    return "\n".join(lines) + "\n"


TEMP_PREFIX = "arxivreader-annotated-"
STALE_AFTER = 6 * 3600   # secondes : archive temporaire oubliée (application arrêtée pendant l'envoi…)


def _purge_stale(now=None):
    """Supprime les archives temporaires de copies annotées restées dans le dossier temporaire depuis plus de 6 h."""
    now = time.time() if now is None else now
    try:
        names = [n for n in os.listdir(tempfile.gettempdir()) if n.startswith(TEMP_PREFIX) and n.endswith(".zip")]
    except OSError:
        return
    for name in names:
        path = os.path.join(tempfile.gettempdir(), name)
        try:
            if os.path.isfile(path) and not os.path.islink(path) and now - os.stat(path).st_mtime > STALE_AFTER:
                os.unlink(path)
        except OSError:
            continue


def build_zip(pid, mode, language, layout="wide", endnotes=True):
    """Archive .zip de la copie annotée, écrite dans un fichier temporaire : (chemin, nom proposé). L'appelant le
    supprime après l'envoi (et chaque appel supprime celles de plus de 6 h, oubliées)."""
    _purge_stale()
    plan, written, counts = _prepare(pid, mode, language, layout, endnotes)
    date = time.strftime("%Y-%m-%d")
    name = unicodedata.normalize("NFC", plan.root.name)
    folder = plan.words["folder"].format(name=name)
    readme = plan.words["readmeName"]
    errors = ERRORS.get(language, ERRORS["en"])
    handle, temporary = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".zip")
    try:
        os.close(handle)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
            for rel, path, _ in plan.copy:
                if rel == readme:
                    continue
                inside = f"{folder}/{rel}"
                try:
                    if rel in written:
                        info = path.stat()
                        entry = zipfile.ZipInfo(inside, time.localtime(max(info.st_mtime, 315532800))[:6])
                        entry.external_attr = (stat.S_IFREG | (info.st_mode & 0o777)) << 16  # droits de l'original
                        archive.writestr(entry, written[rel], zipfile.ZIP_DEFLATED)
                    else:
                        archive.write(path, inside)
                except OSError as error:  # fichier devenu illisible, disque plein…
                    raise ExportError(errors["unreadable"].format(error=f"{rel} ({error.strerror or error})"))
            entry = zipfile.ZipInfo(f"{folder}/{readme}", time.localtime()[:6])
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, _readme(plan, counts, date).encode("utf-8"), zipfile.ZIP_DEFLATED)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return temporary, plan.words["zip"].format(name=name)


ENDNOTES = ("1", "0")  # notes longues renvoyées en fin de document (mode « todo ») : oui (par défaut) ou non


def check(mode, language, layout="wide", endnotes="1"):
    """Paramètres de la requête : True s'ils sont valides (la mise en page et le renvoi des notes longues aussi en
    mode commentaires, où ils sont sans effet)."""
    return mode in MODES and language in LANGUAGES and layout in LAYOUTS and endnotes in ENDNOTES


def params_error(language):
    """Message de l'erreur 400 des paramètres : dans la langue demandée si elle est connue, sinon en anglais."""
    return ERRORS.get(language if language in LANGUAGES else "en", ERRORS["en"])["params"]


def plan_json(pid, mode="comments", language="fr", layout="wide", endnotes=True):
    """Notes et leur place (tests, diagnostic)."""
    plan = Plan(pid, mode, language, layout, endnotes)
    _insertions(plan)
    return json.loads(json.dumps(plan.entries, default=str))
