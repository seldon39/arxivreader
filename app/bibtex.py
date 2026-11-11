"""BibTeX d'un article de la bibliothèque, construit hors ligne, à partir de ce qui est enregistré seulement.

Un BibTeX faux est pire que pas de BibTeX : on suit les conventions d'arXiv (eprint, archivePrefix, primaryClass),
on n'écrit « @article » que si la revue est vraiment connue, on n'invente ni volume, ni pages, ni année de
parution, et on ne coupe un nom en « Nom, Prénom » que quand la coupure est sûre (sinon : nom tel qu'écrit).
Aucun réseau ici : la revue connue de Semantic Scholar est donnée par l'appelant (`s2`).
"""

import re
import unicodedata

from .arxiv import strip_subject_class

ARXIV_DOI = re.compile(r"^10\.(48550|1101|2139|5281|13140|21203|20944|31219|31234|31235)/", re.I)
# particules de noms que BibTeX lui-même reconnaît (en minuscules) : elles restent avec le nom de famille
PARTICLES = {"de", "van", "von", "der", "den", "la", "le", "di", "da", "du", "del", "della", "des", "dos", "das", "do",
             "ter", "ten", "al", "el", "zu", "af", "av", "'t"}
# particules qu'on reconnaît aussi écrites avec une capitale (« Camillo De Lellis ») ; « Le », « Do », « La »… peuvent être
# des prénoms : coupure pas sûre
CAPITAL_PARTICLES = {"de", "van", "von", "der", "den", "del", "della", "di", "du", "da", "ter", "ten", "dos", "das", "des"}
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "ph.d."}
# mentions après le nom (« Michael Atiyah FRS », « Smith et al. ») : BibTeX les prendrait pour le nom de famille ; pas de coupure
HONORIFICS = {"frs", "frse", "al.", "obe", "cbe", "md"}
GROUP_WORDS = re.compile(r"\b(?:collaboration|consortium|group|team|committee|project|institute|university|society)\b", re.I)
STOP_WORDS = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "with", "from", "by", "via", "at", "as", "is",
              "are", "some", "new", "note", "notes", "sur", "de", "la", "le", "les", "des", "du", "un", "une", "et", "pour"}
# lettres sans décomposition Unicode, et accents (caractère combinant → commande TeX) pour du BibTeX en ASCII, qui
# marche avec bibtex comme avec biblatex
SPECIAL = {"ł": r"\l", "Ł": r"\L", "ø": r"\o", "Ø": r"\O", "ß": r"\ss", "æ": r"\ae", "Æ": r"\AE", "œ": r"\oe",
           "Œ": r"\OE", "đ": r"\dj", "Đ": r"\DJ", "ı": r"\i", "å": r"\aa", "Å": r"\AA"}
ACCENTS = {"̀": "`", "́": "'", "̂": "^", "̈": '"', "̃": "~", "̄": "=", "̇": ".",
           "̧": "c", "̌": "v", "̆": "u", "̊": "r", "̨": "k", "̋": "H"}
PUNCTUATION = {"–": "--", "—": "---", "“": "``", "”": "''", "‘": "`", "’": "'", "\u00a0": " ", "…": "..."}
MATH = re.compile(r"\$\$.+?\$\$|\$.+?\$|\\\(.+?\\\)|\\\[.+?\\\]", re.S)
JOURNAL = re.compile(r"^(?P<journal>[^\d]*?[^\d\s,])[\s,]+(?P<volume>\d{1,4})\s*(?:\((?P<year>(?:19|20)\d\d)\))?\s*,?\s*"
                     r"(?:(?:no\.?|issue|n°)\s*\d+\s*,?\s*)?(?P<pp>pp?\.\s*)?(?P<pages>\d+(?:\s*[-–—]+\s*\d+)?)?"
                     r"\s*(?:\((?P<year2>(?:19|20)\d\d)\))?\.?$", re.I)
YEAR_LIKE = re.compile(r"^(?:19|20)\d\d$")
NOT_PUBLISHED = re.compile(r"submitted|preprint|to appear|accepted|in press|forthcoming|arxiv|soumis|à paraître", re.I)


def _plain(text):
    """Lettres sans accents, minuscules (clé, tri)."""
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode().lower()


def _tex_letters(text):
    """Texte hors formule en ASCII : accents en commandes TeX ({\\'e}), et & % # _ protégés. Ce qui n'a pas
    d'équivalent (cyrillique, chinois…) reste tel quel."""
    out, before = [], ""
    for char in unicodedata.normalize("NFC", text):
        if char in "&%#_" and before == "\\":
            out.append(char)  # déjà protégé dans le titre d'origine
        elif char in PUNCTUATION:
            out.append(PUNCTUATION[char])
        elif char in SPECIAL:
            out.append("{" + SPECIAL[char] + "}")
        elif char in "&%#_":
            out.append("\\" + char)
        elif ord(char) < 128 or unicodedata.category(char)[0] != "L":
            out.append(char)
        else:
            base = unicodedata.normalize("NFD", char)
            if len(base) == 2 and base[1] in ACCENTS and base[0].isascii():
                command, letter = ACCENTS[base[1]], base[0]
                if command.isalpha():
                    out.append("{\\" + command + " " + letter + "}")  # {\c c}, {\v s}
                else:
                    out.append("{\\" + command + ("\\" + letter if letter in "ij" else letter) + "}")  # {\'e}, {\'\i}
            else:
                out.append(char)
        before = char
    return "".join(out)


def _balanced(text):
    depth = 0
    for char in text:
        depth += (char == "{") - (char == "}")
        if depth < 0:
            return False
    return depth == 0


def _protect_words(text, first):
    """Mots à majuscule gardés tels quels par BibTeX ({Bergman}) ; le premier mot de la phrase
    n'est protégé que s'il a une majuscule ailleurs qu'au début (BV, CR)."""
    words = re.split(r"(\s+)", text)
    start = first
    for i, word in enumerate(words):
        if not word or word.isspace():
            continue
        head, core, tail = re.match(r"^([\(\[“\"'‘]*)(.*?)([\)\].,;:!?”\"'’]*)$", word, re.S).groups()
        letters = [c for c in core if c.isalpha()]
        protect = "{" not in core and bool(letters) and (any(c.isupper() for c in letters[1:]) or (letters[0].isupper() and not start))
        words[i] = f"{head}{{{_tex_letters(core)}}}{tail}" if protect else _tex_letters(word)
        start = word.endswith(":")  # après « : » une nouvelle phrase commence
    return "".join(words)


def title_tex(title):
    """Titre pour BibTeX : formules $…$ intactes, capitales protégées, accents en TeX, espaces normalisés."""
    title = " ".join(str(title or "").split())
    out, last, first = [], 0, True
    for match in MATH.finditer(title):
        out.append(_protect_words(title[last:match.start()].replace("$", r"\$"), first))
        out.append(match.group(0))
        first = False
        last = match.end()
    out.append(_protect_words(title[last:].replace("$", r"\$"), first and last == 0))
    text = "".join(out)
    # accolades déséquilibrées dans le titre d'origine : retirées plutôt que de casser tout le fichier
    return text if _balanced(text) else text.replace("{", "").replace("}", "")


def _particle(word, capital=False):
    word = word.replace("’", "'")
    return word.lower() in PARTICLES and (word in PARTICLES or (capital and word.lower() in CAPITAL_PARTICLES))


def _safe_split(name):
    """(nom, prénoms, suffixe ou None) quand la coupure est sûre, sinon None. Sûr : au moins deux mots, un « Prénom Nom » sans
    virgule, sans suffixe au milieu, sans chiffre, mot de collectif ni mention (FRS, et al.), et sans particule écrite avec
    une capitale qui pourrait être un prénom (« Le », « Do »). « Camillo De Lellis » → « De Lellis, Camillo » : sans cela
    BibTeX lirait « Lellis » comme nom de famille."""
    parts = name.split()
    if len(parts) < 2 or "," in name or any(c.isdigit() or c in "{}$\\" for c in name) or GROUP_WORDS.search(name):
        return None
    if any(p.lower() in SUFFIXES for p in parts[:-1]) or (parts[-1].lower() in SUFFIXES and len(parts) < 3):
        return None
    suffix = parts.pop() if parts[-1].lower() in SUFFIXES else None
    if parts[-1].lower() in HONORIFICS:
        return None
    last = len(parts) - 1
    while last > 1 and _particle(parts[last - 1], capital=True):  # particules : avec le nom
        last -= 1
    if any(p.replace("’", "'").lower() in PARTICLES and not _particle(p, capital=True) for p in parts[:-1]):
        return None
    return " ".join(parts[last:]), " ".join(parts[:last]), suffix


def author_tex(name):
    name = " ".join(str(name or "").split())
    split = _safe_split(name)
    if split:
        suffix = f", {_tex_letters(split[2])}" if split[2] else ""  # « King, Jr., Martin Luther »
        return f"{_tex_letters(split[0])}{suffix}, {_tex_letters(split[1])}"
    if not name:
        return ""
    # coupure pas sûre : le nom entier entre accolades, que BibTeX ne coupe pas (jamais « Prénom Nom » écrit tel quel, dont il
    # prendrait le dernier mot pour le nom de famille) ; seul un nom déjà écrit « Nom, Prénom » reste comme il est
    text = _tex_letters(name)
    return text if "," in name and not GROUP_WORDS.search(name) else f"{{{text}}}"


def _last_name(name):
    name = " ".join(str(name or "").split())
    if "," in name:
        return name.split(",")[0]
    split = _safe_split(name)
    return split[0] if split else (name.split()[-1] if name.split() else "")


def cite_key(paper):
    """Clé stable : nom du premier auteur (sans accents) + année d'arXiv + premier mot significatif du titre."""
    authors = paper.get("authors") or []
    last = re.sub(r"[^a-z0-9]", "", _plain(_last_name(authors[0]))) if authors else ""
    year = _arxiv_year(paper)
    title = re.sub(r"\$.*?\$", " ", str(paper.get("title") or ""))
    word = next((w for w in re.findall(r"[a-z]+", _plain(title)) if w not in STOP_WORDS and len(w) > 2), "")
    return (last or "anon") + year + word


def _arxiv_year(paper):
    match = re.match(r"^((?:19|20)\d\d)", str(paper.get("published") or ""))
    return match.group(1) if match else ""


def _journal(text):
    """(revue, volume, année, pages, brut) d'une référence de revue ; None si ce n'est pas une parution (« to
    appear », « submitted »…) ou si elle ne se lit pas sûrement (thèse, « 2012, 62, 1111-1150 »…) : l'appelant en fait alors
    un @misc avec la chaîne dans « note ». Des pages ne sont reconnues que si c'est un intervalle, précédé de « p. », ou un
    numéro qui n'est pas une année (« 180, 2014 » : 2014 est l'année, pas une page)."""
    text = " ".join(str(text or "").split())
    if not text or NOT_PUBLISHED.search(text) or not re.search(r"\d", text):
        return None
    found = JOURNAL.match(text)
    if not found or not re.search(r"[A-Za-z]{2}", found.group("journal")):
        return None
    pages = found.group("pages")
    if pages and not (found.group("pp") or re.search(r"\D", pages) or not YEAR_LIKE.match(pages)):
        return None
    year = found.group("year") or found.group("year2")
    if not pages and not year:
        return None
    pages = re.sub(r"\s*[-–—]+\s*", "--", pages) if pages else None
    return found.group("journal").strip(), found.group("volume"), year, pages, text


def entry(paper, s2=None, key=None):
    """Entrée BibTeX d'un article (ligne de la table `papers`). `s2` : (revue, DOI) connus de Semantic Scholar."""
    s2_journal, s2_doi = s2 or (None, None)
    # toujours l'identifiant de base, sans version ni classe (math.DG/0211159 → math/0211159, comme le fait arXiv)
    arxiv_id = strip_subject_class(re.sub(r"v\d+$", "", str(paper.get("arxiv_id") or "").strip()))
    published = _journal(paper.get("journal_ref")) or _journal(s2_journal)
    doi = next((d.strip() for d in (paper.get("doi"), s2_doi) if d and d.strip() and not ARXIV_DOI.match(d.strip())), None)
    kind = "article" if published else "misc"
    fields = []
    authors = [author_tex(a) for a in paper.get("authors") or [] if str(a).strip()]
    if authors:
        fields.append(("author", " and ".join(authors)))
    if paper.get("title"):
        fields.append(("title", title_tex(paper["title"])))
    if published:
        journal, volume, year, pages, _ = published
        fields.append(("journal", _tex_letters(journal)))
        if volume:
            fields.append(("volume", volume))
        if year:  # année de parution : seulement celle écrite dans la référence de la revue
            fields.append(("year", year))
        if pages:
            fields.append(("pages", pages))
    elif _arxiv_year(paper):
        fields.append(("year", _arxiv_year(paper)))
    if doi:
        fields.append(("doi", doi))
    if arxiv_id:
        fields += [("eprint", arxiv_id), ("archivePrefix", "arXiv")]
        if paper.get("primary_category"):
            fields.append(("primaryClass", paper["primary_category"]))
        if not published:
            fields.append(("url", f"https://arxiv.org/abs/{arxiv_id}"))
    note = (paper.get("journal_ref") or "").strip()
    if not published and note:
        fields.append(("note", _tex_letters(" ".join(note.split()))))  # « to appear in… », ou référence illisible : dite telle quelle, sans en faire un @article
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
    return f"@{kind}{{{key or cite_key(paper)},\n{body}\n}}"


def unique_keys(papers):
    """Clés de chaque article d'un même export : une collision reçoit a, b, c… (BibTeX ignore la casse)."""
    taken, keys = set(), []
    for paper in papers:
        base = cite_key(paper)
        key, letter = base, 0
        while key.lower() in taken:
            letter += 1
            key = base + _suffix(letter)
        taken.add(key.lower())
        keys.append(key)
    return keys


def _suffix(n):
    out = ""
    while n:
        n, rest = divmod(n - 1, 26)
        out = chr(97 + rest) + out
    return out


def export(papers, s2_of=lambda paper: None):
    """Fichier .bib de plusieurs articles (dans l'ordre, sans doublon d'identifiant)."""
    seen, unique = set(), []
    for paper in papers:
        if paper["id"] not in seen:
            seen.add(paper["id"])
            unique.append(paper)
    return "\n\n".join(entry(p, s2_of(p), k) for p, k in zip(unique, unique_keys(unique))) + ("\n" if unique else "")
