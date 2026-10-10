"""Structure mathématique d'un article LaTeXML : sommaire, énoncés, preuves et dépendances."""

import re

from bs4 import NavigableString, Tag

from .arxiv import extract_ids

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


def extract_structure(fragment):
    """Analyse la soupe nettoyée (modifiée sur place) et renvoie la structure de l'article."""
    counter = iter(range(1, 10**6))

    def ensure_id(element, prefix):
        if not element.get("id"):
            element["id"] = f"arx-{prefix}-{next(counter)}"
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
        nodes[node_id] = {
            "id": node_id,
            "kind": classify(label, element.get("class") or []),
            "label": label,
            "name": name,
            "section": section.get("id") if section else None,
            "proofs": [],
            "detectedProof": False,
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
            target = fragment.find(id=link["href"][1:])
            if target is None:
                continue
            theorem = target if has_class(target, "ltx_theorem") else enclosing(target, lambda e: has_class(e, "ltx_theorem"))
            if theorem is not None:
                return theorem
        return None

    def attach(proof, theorem, detected=False):
        proof_id = ensure_id(proof, "proof")
        node = nodes[theorem["id"]]
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
            target = fragment.find(id=link["href"][1:])
            if target is not None:
                theorem = target if has_class(target, "ltx_theorem") else enclosing(target, lambda e: has_class(e, "ltx_theorem"))
                found = theorem or found
        return found

    def proof_target(element, title, last_theorem):
        target = theorem_of_ref(title) if title is not None else None
        if target is not None:
            return target
        previous = element.find_previous_sibling(True)
        if previous is not None and has_class(previous, "ltx_theorem"):
            return previous
        target = announced_theorem(element)
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
            attach(element, target)

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

    # Renvois → dépendances
    def resolve(target_id):
        target = fragment.find(id=target_id)
        if target is None:
            return None
        if target_id.startswith("bib."):
            return ("bib", target_id)
        theorem = target if has_class(target, "ltx_theorem") else enclosing(target, lambda e: has_class(e, "ltx_theorem"))
        equation_tag = target.find(class_="ltx_tag_equation") if _is_equation(target) else None
        if equation_tag is not None:
            if theorem is None:
                proof = enclosing(target, lambda e: e.get("data-proof-of"))
                theorem = fragment.find(id=proof["data-proof-of"]) if proof else None
            return ("equation", target_id, squash(plain(equation_tag)), theorem.get("id") if theorem else None)
        if theorem is not None:
            return ("theorem", theorem["id"])
        return None

    equations = {}
    for node_id in order:
        node = nodes[node_id]
        scope = [fragment.find(id=node_id)] + [fragment.find(id=p) for p in node["proofs"]]
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
                    if owner != node_id:
                        _add(node["usesEquations"], eq_id)
                        if owner:
                            _add(node["uses"], owner)
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
            element["class"] = (element.get("class") or []) + ["arx-hidden-text"]
            hidden += 1

    return {
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
