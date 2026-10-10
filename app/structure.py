"""Structure mathématique d'un article LaTeXML : sommaire, énoncés, preuves et dépendances."""

import bisect
import re

from bs4 import NavigableString, Tag

from .arxiv import extract_ids

STRUCTURE_VERSION = 5  # à augmenter quand l'analyse change : les articles déjà importés sont ré-analysés

SECTION_LEVELS = {
    "ltx_part": 0, "ltx_chapter": 0, "ltx_section": 1, "ltx_appendix": 1,
    "ltx_bibliography": 1, "ltx_subsection": 2, "ltx_subsubsection": 3,
}

# Nature d'un énoncé d'après le premier mot de son étiquette (« Theorem 2.1 », « Lemme 3 »…).
KIND_PREFIXES = [
    ("theorem", ("theorem", "théorème", "theoreme", "thm", "main theorem")),
    ("lemma", ("lemma", "lemme", "sublemma")),
    ("proposition", ("proposition", "prop")),
    ("corollary", ("corollary", "corollaire", "cor")),
    ("definition", ("definition", "définition", "defn", "def")),
    ("conjecture", ("conjecture",)),
    ("claim", ("claim", "assertion", "fact", "fait", "step", "étape")),
    ("remark", ("remark", "remarque", "note", "observation")),
    ("example", ("example", "exemple", "counterexample", "contre-exemple")),
    ("notation", ("notation", "convention", "setting", "assumption", "hypothesis", "hypothèse")),
    ("problem", ("problem", "problème", "question", "exercise", "exercice")),
]

PROOF_START = re.compile(r"^\s*(proof|preuve|démonstration|demonstration)\b", re.I)
ANNOUNCES_PROOF = re.compile(r"\b(prove|proof|proving|démontr|preuve|montrons)", re.I)
PROOF_HEADING = re.compile(r"\b(proofs?|preuves?|d[ée]monstrations?)\b", re.I)
# Phrase juste avant l'énoncé qui le justifie (« The following corollary is a consequence of (2.6) »)
JUSTIFIES_NEXT = re.compile(r"\b(following|below|suivante?s?|ci-dessous)\b", re.I)
JUSTIFICATION = re.compile(
    r"\b(consequence|follows?|by|from|deduce|obtain|get|same way|simplif\w*|combin\w*|immediate\w*|implies"
    r"|conséquence|découle|d'après|grâce|obtient|déduit|même façon|même manière)\b", re.I)
ANNOUNCES_PROOF_BELOW = re.compile(r"\b(we|nous)\s+(now\s+|will\s+|allons\s+)?(prove|show|establish|montrer|démontrer|montrons|démontrons)", re.I)
NEEDS_PROOF = {"theorem", "proposition", "lemma", "corollary", "claim"}
# « In the proof of Theorem 1.2, … » : l'énoncé est celui qu'on démontre, pas un énoncé utilisé
PROVING = re.compile(r"(proof\s+of|prove|proving|show|preuve\s+d[eu]|démontrer|montrer)\s*(the\s+|le\s+|la\s+|du\s+)?(\w+\s*)?$", re.I)
# Renvoi implicite à l'énoncé qui précède : « the above lemma », « the previous theorem », « le lemme précédent »
IMPLICIT_REF = re.compile(
    r"\b(?:the|this)\s+(?:above|previous|preceding|last|former)\s+(lemma|proposition|theorem|corollary|claim)\b"
    r"|\bthe\s+(lemma|proposition|theorem|corollary)\s+above\b"
    r"|\b(?:le|la|du|de\s+la)\s+(lemme|proposition|théorème|corollaire)\s+(?:précédente?|ci-dessus)\b", re.I)
# Phrase juste après l'énoncé (« This follows from Lemma 2.1. »)
JUSTIFIES_PREVIOUS = re.compile(
    r"^\s*(this|it|the (above )?(lemma|corollary|proposition|result) )\s*(follows|is (a |an )?(direct |immediate |straightforward )?consequence)"
    r"|^\s*(ceci|cela|ce résultat)\s+(découle|est une (conséquence|application))", re.I)
QED_END = re.compile(r"(□|∎|■|\$\\(Box|square|blacksquare|qed|qedsymbol|QED)\$)\s*$")

HIDDEN_STYLE = re.compile(
    r"color\s*:\s*(#fff(fff)?\b|white|rgba?\([^)]*,\s*0\))"
    r"|font-size\s*:\s*(0*\.\d+|[0-3](\.\d+)?)\s*(px|pt)|font-size\s*:\s*(\d|1\d)(\.\d+)?\s*%"
    r"|display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(\.0+)?\s*(;|$)",
    re.I,
)


def plain(element):
    """Texte d'un élément, les formules étant remplacées par leur source TeX entre $."""
    if isinstance(element, NavigableString):
        return str(element)
    if element.name == "math":
        return "$" + element.get("alttext", "") + "$"
    return "".join(plain(child) for child in element.children)


def squash(text):
    return " ".join(text.split())


def has_class(element, name):
    return isinstance(element, Tag) and name in (element.get("class") or [])


def classify(label, classes):
    word = label.lower()
    for kind, prefixes in KIND_PREFIXES:
        if any(word.startswith(prefix) for prefix in prefixes):
            return kind
    for cls in classes:
        if cls.startswith("ltx_theorem_"):
            return classify(cls[len("ltx_theorem_"):], [])
    return "other"


def enclosing(element, predicate):
    for parent in element.parents:
        if isinstance(parent, Tag) and predicate(parent):
            return parent
    return None


def element_siblings_after(element):
    for sibling in element.next_siblings:
        if isinstance(sibling, Tag):
            yield sibling


def is_boundary(element):
    classes = element.get("class") or []
    return (
        any(c in SECTION_LEVELS or c in ("ltx_theorem", "ltx_proof", "ltx_paragraph") for c in classes)
        or element.name in ("section", "h1", "h2", "h3", "h4", "h5", "h6")
    )


# Blocs qu'on peut désigner comme passage de preuve (aussi dans l'interface, static/js/reader.js)
PASSAGE_BLOCKS = {"ltx_para", "ltx_theorem", "ltx_proof", "ltx_equation", "ltx_equationgroup", "ltx_figure", "ltx_table"}


def is_passage_block(element):
    return isinstance(element, Tag) and bool(set(element.get("class") or []) & PASSAGE_BLOCKS) and bool(element.get("id"))


def passage_elements(fragment, start_id, end_id, by_id=None):
    """Blocs de l'article du bloc `start_id` au bloc `end_id` inclus, dans l'ordre (sans les blocs imbriqués).
    `by_id` : recherche par identifiant déjà indexée (sinon parcours de l'article)."""
    find = by_id or (lambda element_id: fragment.find(id=element_id))
    start = find(start_id)
    end = find(end_id) if end_id else None
    if start is None:
        return []
    if end is None or end is start:
        return [start]
    blocks, taken = [start], {id(start)}
    for element in start.find_all_next(True):
        inside = any(id(parent) in taken for parent in element.parents)
        if element is end:
            if not inside:
                blocks.append(element)
            return blocks
        if is_passage_block(element) and not inside:
            blocks.append(element)
            taken.add(id(element))
    return [start]  # fin avant le début (ou introuvable) : le premier bloc seul


def extract_structure(fragment, overrides=None):
    """Analyse la soupe nettoyée (modifiée sur place) et renvoie la structure de l'article.
    `overrides` : emplacements de preuve indiqués à la main ({id d'énoncé: {mode, ref | start, end}})."""
    for element in fragment.find_all(attrs={"data-proof-of": True}):
        del element["data-proof-of"]  # nouvelle analyse : rattachements refaits ci-dessous
    counter = iter(range(1, 10**6))
    # Index des identifiants : une recherche par fragment.find(id=…) parcourt tout l'article
    index = {}
    for element in fragment.find_all(id=True):
        index.setdefault(element["id"], element)

    def by_id(element_id):
        return index.get(element_id) if element_id else None

    def ensure_id(element, prefix):
        if not element.get("id"):
            # identifiant neuf, jamais déjà présent (une analyse précédente a pu en poser)
            new = f"arx-{prefix}-{next(counter)}"
            while new in index:
                new = f"arx-{prefix}-{next(counter)}"
            element["id"] = new
            index[new] = element
        return element["id"]

    # Sommaire
    toc = []
    for element in fragment.find_all(True):
        classes = element.get("class") or []
        level = next((SECTION_LEVELS[c] for c in classes if c in SECTION_LEVELS), None)
        if level is None:
            continue
        heading = element.find(re.compile(r"^h[1-6]$"))
        if heading is None:
            continue
        toc.append({"id": ensure_id(element, "sec"), "level": level, "title": squash(plain(heading))})

    # Énoncés
    nodes = {}
    order = []
    for element in fragment.select(".ltx_theorem"):
        node_id = ensure_id(element, "thm")
        title = element.find(class_=re.compile(r"^ltx_title"))
        tag = element.find(class_="ltx_tag_theorem")
        label = squash(plain(tag)).rstrip(".") if tag else ""
        title_text = squash(plain(title)) if title else ""
        if not label:
            label = title_text.rstrip(".")[:60] or "Statement"
        name = squash(title_text.replace(label, "", 1)).strip(" .:")
        name = re.sub(r"^\((.*)\)$", r"\1", name)
        section = enclosing(element, lambda e: any(c in SECTION_LEVELS for c in e.get("class") or []))
        cites_in_title = [a["href"][1:] for a in title.select('a[href^="#bib"]')] if title else []
        nodes[node_id] = {
            "id": node_id,
            "kind": classify(label, element.get("class") or []),
            "label": label,
            "name": name,
            "section": section.get("id") if section else None,
            "proofs": [],
            "detectedProof": False,
            "proofSection": False,     # preuve dans une section à part (« 4.2. Proof of Theorem 1.3 »)
            "cited": cites_in_title,   # énoncé repris d'une référence (« Theorem 3.2 ([12, Theorem 4.3]) »)
            "justifiedBy": None,       # paragraphe voisin qui le justifie, sans preuve en forme
            "manual": None,            # emplacement indiqué à la main : section, passage, immediate, admitted
            "proofPassage": None,      # {start, end} : passage de l'article indiqué comme preuve
            "uses": [],
            "usesEquations": [],
            "cites": [],
            "usedBy": [],
        }
        order.append(node_id)
        element["data-kind"] = nodes[node_id]["kind"]

    def theorem_of_ref(container):
        """Énoncé visé par un renvoi présent dans un titre de preuve (« Proof of Theorem 1.1 »)."""
        for link in container.select('a.ltx_ref[href^="#"]'):
            target = by_id(link["href"][1:])
            if target is None:
                continue
            theorem = target if has_class(target, "ltx_theorem") else enclosing(target, lambda e: has_class(e, "ltx_theorem"))
            if theorem is not None:
                return theorem
        return None

    def attach(proof, theorem, detected=False):
        proof_id = ensure_id(proof, "proof")
        node = nodes[theorem["id"]]
        if proof_id in node["proofs"]:
            return
        node["proofs"].append(proof_id)
        node["detectedProof"] = node["detectedProof"] or detected
        proof["data-proof-of"] = theorem["id"]

    def announced_theorem(element):
        """Énoncé annoncé juste avant une preuve (« We now prove Theorem 3.5. »)."""
        previous = element.find_previous_sibling(True)
        if previous is None or not has_class(previous, "ltx_para"):
            return None
        if not ANNOUNCES_PROOF.search(squash(plain(previous))):
            return None
        found = None
        for link in previous.select('a.ltx_ref[href^="#"]'):
            target = by_id(link["href"][1:])
            if target is not None:
                theorem = target if has_class(target, "ltx_theorem") else enclosing(target, lambda e: has_class(e, "ltx_theorem"))
                found = theorem or found
        return found

    def statement_before(element, limit=4):
        """Énoncé à démontrer (théorème, lemme…) encore sans preuve, juste avant `element` : seules une
        remarque, un exemple, une définition ou quelques paragraphes peuvent les séparer."""
        for count, sibling in enumerate(element.find_previous_siblings(True)):
            if count >= limit:
                return None
            if has_class(sibling, "ltx_theorem"):
                node = nodes.get(sibling.get("id"))
                if node is not None and node["kind"] in NEEDS_PROOF:
                    return sibling if not node["proofs"] else None
            elif has_class(sibling, "ltx_proof") or sibling.get("data-proof-of") or sibling.name == "section":
                return None
        return None

    def proof_target(element, title, last_theorem):
        target = theorem_of_ref(title) if title is not None else None
        if target is not None:
            return target
        previous = element.find_previous_sibling(True)
        if previous is not None and has_class(previous, "ltx_theorem"):
            # « Théorème, Remarque, Preuve » : la preuve est celle du théorème (s'il n'en a pas déjà une)
            if nodes[previous["id"]]["kind"] not in NEEDS_PROOF:
                return statement_before(previous) or previous
            return previous
        # deuxième preuve à la suite (« Second proof », « Algebraic proof ») : même énoncé que la première
        if previous is not None and has_class(previous, "ltx_proof") and by_id(previous.get("data-proof-of")) is not None:
            return by_id(previous["data-proof-of"])
        target = announced_theorem(element)
        if target is not None:
            return target
        target = statement_before(element)
        if target is not None:
            return target
        if last_theorem is not None and not nodes[last_theorem["id"]]["proofs"]:
            return last_theorem
        return None

    # Preuves balisées par LaTeXML (environnement proof)
    last_theorem = None
    for element in fragment.select(".ltx_theorem, .ltx_proof"):
        if has_class(element, "ltx_theorem"):
            last_theorem = element
            continue
        if enclosing(element, lambda e: has_class(e, "ltx_proof") or has_class(e, "ltx_theorem")):
            continue
        target = proof_target(element, element.find(class_=re.compile(r"^ltx_title")), last_theorem)
        if target is not None:
            attach(element, target, detected=has_class(element, "arx-detected-proof"))

    # Preuves écrites à la main (« {\it Proof.} … $\Box$ ») : on les regroupe dans un bloc
    for para in fragment.select(".ltx_para"):
        if enclosing(para, lambda e: has_class(e, "ltx_proof") or has_class(e, "ltx_theorem")):
            continue
        text = squash(plain(para))
        if not PROOF_START.match(text):
            continue
        previous = para.find_previous_sibling(True)
        last = previous if previous is not None and has_class(previous, "ltx_theorem") else None
        target = proof_target(para, _first_sentence(para), last)
        if target is None:
            continue
        region = [para]
        if not QED_END.search(text):
            for sibling in element_siblings_after(para):
                if is_boundary(sibling):
                    break
                region.append(sibling)
                if QED_END.search(squash(plain(sibling))):
                    break
        wrapper = fragment.new_tag("div", attrs={"class": "ltx_proof arx-detected-proof"})
        para.insert_before(wrapper)
        for element in region:
            wrapper.append(element.extract())
        attach(wrapper, target, detected=True)

    def theorems_of(container, direct):
        """Énoncés cités dans un titre : directement (« Theorem 1.3 ») ou par un point ou une équation (« (1) »)."""
        found = []
        for link in container.select('a.ltx_ref[href^="#"]'):
            target = by_id(link["href"][1:])
            if target is None:
                continue
            if has_class(target, "ltx_theorem"):
                theorem = target if direct else None
            else:
                theorem = None if direct else enclosing(target, lambda e: has_class(e, "ltx_theorem"))
            if theorem is not None and theorem.get("id") in nodes and theorem not in found:
                found.append(theorem)
        return found

    # Preuves en section à part : titre « Proof of Theorem 1.1 », « 4.3. …, proofs of Corollary 1.4 and 1.5 ».
    # Les sections les plus profondes d'abord : la sous-section « Proof of Corollary 1.4 » l'emporte.
    # Un titre qui renvoie à l'énoncé lui-même l'emporte sur « Proof of (1) » (un point de l'énoncé).
    sections = [e for e in fragment.find_all("section") if any(c in SECTION_LEVELS for c in e.get("class") or [])]
    for direct in (True, False):
        for section in sorted(sections, key=lambda e: -len(list(e.parents))):
            heading = section.find(re.compile(r"^h[1-6]$"))
            if heading is None or enclosing(heading, lambda e: e.name == "section") is not section:
                continue
            if not PROOF_HEADING.search(squash(plain(heading))):
                continue
            for theorem in theorems_of(heading, direct):
                node = nodes[theorem["id"]]
                # une sous-section plus précise l'a déjà, ou, dans une section commune à plusieurs énoncés (« 8. Proofs
                # of Theorems 1.7 and 1.1 »), une preuve à lui (« Proof of Theorem 1.7 ») ; une esquisse trouvée
                # ailleurs, ou le bloc final d'une section qui ne prouve que lui, n'empêchent rien
                if node["proofSection"] or section.find(id=theorem["id"]) is not None:
                    continue
                shared = len(theorems_of(heading, True)) > 1
                if shared and any(section.find(id=proof_id) is not None for proof_id in node["proofs"]):
                    continue
                attach(section, theorem)
                node["proofSection"] = True

    # Justification en une phrase juste avant (ou après) l'énoncé, sans environnement de preuve
    for node_id in order:
        node = nodes[node_id]
        if node["proofs"] or node["cited"] or node["kind"] not in NEEDS_PROOF:
            continue
        element = by_id(node_id)
        previous = element.find_previous_sibling(True)
        if previous is not None and has_class(previous, "ltx_para"):
            last = re.split(r"(?<=[.!?])\s+(?=[A-Z])", squash(plain(previous)))[-1]
            if JUSTIFIES_NEXT.search(last) and JUSTIFICATION.search(last) and not ANNOUNCES_PROOF_BELOW.search(last):
                node["justifiedBy"] = ensure_id(previous, "para")
                continue
        following = element.find_next_sibling(True)
        if following is not None and has_class(following, "ltx_para") and JUSTIFIES_PREVIOUS.search(squash(plain(following))):
            node["justifiedBy"] = ensure_id(following, "para")

    # Emplacements indiqués à la main : ils remplacent ce qui a été trouvé automatiquement
    for node_id, choice in (overrides or {}).items():
        node = nodes.get(node_id)
        mode = choice.get("mode")
        if node is None:
            continue
        if mode == "section" and by_id(choice.get("ref")) is None:
            continue
        if mode == "passage" and by_id(choice.get("start")) is None:
            continue
        for proof_id in node["proofs"]:  # l'ancienne preuve trouvée n'est plus rattachée à l'énoncé
            proof = by_id(proof_id)
            if proof is not None and proof.get("data-proof-of") == node_id:
                del proof["data-proof-of"]
        node.update(proofs=[], detectedProof=False, proofSection=False, justifiedBy=None, manual=mode)
        if mode == "section":
            section = by_id(choice["ref"])
            attach(section, by_id(node_id))
            node["proofSection"] = True
        elif mode == "passage":
            end = choice.get("end") if by_id(choice.get("end") or "") is not None else choice["start"]
            node["proofPassage"] = {"start": choice["start"], "end": end}

    # Renvois → dépendances
    def resolve(target_id):
        target = by_id(target_id)
        if target is None:
            return None
        if target_id.startswith("bib."):
            return ("bib", target_id)
        theorem = target if has_class(target, "ltx_theorem") else enclosing(target, lambda e: has_class(e, "ltx_theorem"))
        equation_tag = target.find(class_="ltx_tag_equation") if _is_equation(target) else None
        if equation_tag is not None:
            if theorem is None:
                proof = enclosing(target, lambda e: e.get("data-proof-of"))
                theorem = by_id(proof["data-proof-of"]) if proof else None
            return ("equation", target_id, squash(plain(equation_tag)), theorem.get("id") if theorem else None)
        if theorem is not None:
            return ("theorem", theorem["id"])
        return None

    # Position de chaque élément dans l'article, pour les renvois implicites
    position = {id(element): i for i, element in enumerate(fragment.find_all(True))}
    statements = sorted((position[id(by_id(n))], n) for n in order)
    starts = [p for p, _ in statements]

    def paragraphs(container):
        return [container] if has_class(container, "ltx_para") else container.select(".ltx_para")

    def implicit_refs(container):
        """« the above lemma » : le dernier énoncé de cette nature avant le paragraphe."""
        found = []
        for para in paragraphs(container):
            for match in IMPLICIT_REF.finditer(squash(plain(para))):
                kind = classify(next(g for g in match.groups() if g), [])
                i = bisect.bisect_left(starts, position.get(id(para), 0)) - 1
                while i >= 0 and nodes[statements[i][1]]["kind"] != kind:
                    i -= 1
                if i >= 0:
                    _add(found, statements[i][1])
        return found

    def in_block(element):
        # le texte d'une section de preuve (« 7.1. Proof of Theorem 7.1 ») reste du texte courant : ses lemmes
        # s'appuient sur les formules qui y sont obtenues
        return enclosing(element, lambda e: has_class(e, "ltx_theorem") or has_class(e, "ltx_proof")
                         or (e.get("data-proof-of") and e.name != "section") or has_class(e, "ltx_bibliography")) is not None

    # Équation obtenue dans le texte courant (hors énoncés et preuves) à partir d'énoncés cités juste avant,
    # dans le même paragraphe : « From (3.30) and the above lemma, we get (3.33) ». Un énoncé qui cite
    # cette équation dépend (faiblement) de ces énoncés.
    statement_position = dict((n, p) for p, n in statements)
    derived = {}
    for para in fragment.select(".ltx_para"):
        if in_block(para):
            continue
        sources = []  # (position dans l'article, énoncé)
        for link in para.select('a.ltx_ref[href^="#"]'):
            hit = resolve(link["href"][1:])
            before = link.find_previous(string=True) or ""
            if hit and hit[0] == "theorem" and not PROVING.search(before[-40:]):
                sources.append((position[id(link)], hit[1]))
        sources += [(position[id(para)], n) for n in implicit_refs(para)]
        if not sources:
            continue
        # « … we get » suivi d'une formule centrée : LaTeXML la place dans le paragraphe suivant
        blocks = [para]
        following = para.find_next_sibling(True)
        if following is not None and has_class(following, "ltx_para") and _is_equation(following.find(True) or following):
            blocks.append(following)
        for equation in (e for block in blocks for e in block.find_all(_is_equation)):
            if not equation.get("id") or equation.find(class_="ltx_tag_equation") is None:
                continue
            here = position[id(equation)]
            derived[equation["id"]] = [n for at, n in sources if at < here and statement_position[n] < here]

    def proof_container(target):
        return enclosing(target, lambda e: e.get("data-proof-of"))

    equations = {}
    weak = {node_id: [] for node_id in order}
    for node_id in order:
        node = nodes[node_id]
        statement = by_id(node_id)
        scope = [statement] + [by_id(p) for p in node["proofs"]]
        if node["justifiedBy"]:
            scope.append(by_id(node["justifiedBy"]))
        if node["proofPassage"]:
            scope += passage_elements(fragment, node["proofPassage"]["start"], node["proofPassage"]["end"], by_id)
        for container in scope:
            for link in container.select('a.ltx_ref[href^="#"]'):
                hit = resolve(link["href"][1:])
                if hit is None:
                    continue
                if hit[0] == "bib":
                    _add(node["cites"], hit[1])
                elif hit[0] == "theorem" and hit[1] != node_id:
                    _add(node["uses"], hit[1])
                elif hit[0] == "equation":
                    _, eq_id, eq_label, owner = hit
                    equations[eq_id] = {"label": eq_label, "owner": owner}
                    if owner == node_id:
                        continue
                    _add(node["usesEquations"], eq_id)
                    holder = proof_container(by_id(eq_id))
                    if (owner and holder is not None and holder.get("data-proof-of") == owner
                            and any(parent is holder for parent in statement.parents)):
                        continue  # énoncé posé dans la preuve de `owner` : il n'en dépend pas
                    if owner:
                        _add(node["uses"], owner)
                    for used in derived.get(eq_id, []):
                        if used != node_id:
                            _add(weak[node_id], used)
            for used in implicit_refs(container):
                if used != node_id:
                    _add(node["uses"], used)
            # énoncés posés à l'intérieur de la preuve (lemme intermédiaire, théorème cité en cours de route)
            if container is not statement:
                for inner in container.select(".ltx_theorem[id]"):
                    if inner["id"] in nodes and inner["id"] != node_id:
                        _add(node["uses"], inner["id"])

    # Liens faibles (par une équation du texte courant) : seulement s'ils ne créent pas de cycle
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

    for node_id in order:
        node = nodes[node_id]
        node["weakUses"] = []
        for used in weak[node_id]:
            if used not in node["uses"] and not reaches(used, node_id):
                node["uses"].append(used)
                node["weakUses"].append(used)
    for node_id in order:
        for used in nodes[node_id]["uses"]:
            _add(nodes[used]["usedBy"], node_id)

    # Bibliographie
    bibliography = []
    for item in fragment.select(".ltx_bibitem"):
        tag = item.find(class_="ltx_tag_bibitem")
        text = squash(" ".join(plain(block) for block in item.select(".ltx_bibblock")))
        # DOI et identifiants arXiv : dans le texte, ou dans les liens (« Document », « Link »)
        links = " ".join(a.get("href", "") for a in item.select("a[href]"))
        doi = re.search(r"\b10\.\d{4,9}/[^\s\"<>]+", f"{text} {links}")
        arxiv_links = [m.group(1) for m in re.finditer(r"arxiv\.org/(?:abs|pdf)/([^\s\"?#]+?)(?:v\d+)?(?:\.pdf)?(?=[\s\"?#]|$)", links)]
        title = item.select_one(".ltx_bib_title")
        bibliography.append({
            "id": ensure_id(item, "bib"),
            "label": squash(plain(tag)).strip("[]") if tag else "",
            "text": text,
            "title": squash(plain(title)) if title else "",
            "arxiv": list(dict.fromkeys(extract_ids(text) + arxiv_links)),
            "doi": doi.group(0).rstrip(".,;") if doi else "",
        })

    # Texte invisible (couleur blanche, police minuscule…) : signalé, et révélable dans le lecteur
    hidden = 0
    for element in fragment.find_all(style=True):
        if HIDDEN_STYLE.search(element["style"]) and squash(element.get_text()):
            if "arx-hidden-text" not in (element.get("class") or []):
                element["class"] = (element.get("class") or []) + ["arx-hidden-text"]
            hidden += 1

    return {
        "version": STRUCTURE_VERSION,
        "toc": toc,
        "nodes": [nodes[n] for n in order],
        "equations": equations,
        "bibliography": bibliography,
        "hiddenText": hidden,
    }


EQUATION_CLASSES = {"ltx_equation", "ltx_equationgroup", "ltx_eqn_row", "ltx_eqn_table"}


def _is_equation(element):
    classes = set(element.get("class") or [])
    return (
        bool(classes & EQUATION_CLASSES)
        or element.name in ("tbody", "tr")
        or re.search(r"\.E[A-Za-z]*\d+$", element.get("id", "")) is not None
    )


def _first_sentence(para):
    """Copie du début d'un paragraphe (jusqu'au premier point), pour y chercher un renvoi."""
    clone = Tag(name="div")
    for child in list(para.descendants):
        if isinstance(child, NavigableString) and "." in child and child.parent is not None:
            break
        if isinstance(child, Tag) and child.name == "a" and has_class(child, "ltx_ref"):
            clone.append(Tag(name="a", attrs={"class": "ltx_ref", "href": child.get("href", "")}))
    return clone


def _add(items, value):
    if value not in items:
        items.append(value)
