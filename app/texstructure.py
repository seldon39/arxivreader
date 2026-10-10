"""Structure d'un article à partir de sa source TeX, pour les articles sans version HTML.

Mêmes informations que structure.py (sommaire, énoncés, preuves, dépendances, bibliographie), lues
dans la source : environnements déclarés par \\newtheorem (numérotation simulée : compteurs partagés,
remis à zéro par section, annexes), preuves rattachées comme dans la version HTML, renvois \\ref,
\\eqref, \\cref… Les pages de chaque énoncé dans le PDF viennent des ancres de hyperref (« thm.2.3 »),
sinon d'une recherche du texte « Theorem 2.3 » dans le PDF.

La source n'est ni extraite sur le disque ni compilée.
"""

import io
import re

from .arxiv import extract_ids
from .structure import NEEDS_PROOF, PROOF_HEADING, STRUCTURE_VERSION, classify
from .texsource import DOCUMENTCLASS, _expand, _read_archive, _strip_comments

SECTIONS = {"part": 0, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3}
EQUATION_ENVS = {"equation", "align", "gather", "multline", "eqnarray", "flalign", "alignat", "split", "dmath",
                 "subequations"}
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
    for index in range(pos, min(len(text), pos + 20000)):
        char = text[index]
        if char == "\\":
            continue
        if index and text[index - 1] == "\\":
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[pos + 1:index], index + 1
    return None


def _args(text, pos, pattern):
    """Arguments selon un motif (« {[{[ » : obligatoire, optionnel…) ; None pour un optionnel absent."""
    values = []
    for kind in pattern:
        found = _group(text, pos, "{" if kind == "{" else "[")
        if found is None:
            if kind == "{":
                return values, pos
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
    text = text.replace("~", " ").replace("``", "“").replace("''", "”").replace("--", "–")
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


def normalize_proofs(body, envs):
    """Preuves écrites autrement (\\proof…\\endproof, environnement « pf », titre « \\textbf{Proof of …} »)
    réécrites en \\begin{proof}[…]…\\end{proof}, la forme que reconnaît l'analyse."""
    body = re.sub(r"\\proof\b(?![A-Za-z])", r"\\begin{proof}", body)
    body = re.sub(r"\\endproof\b(?![A-Za-z])", r"\\end{proof}", body)
    names = "|".join(re.escape(n) for n in PROOF_ENVS - {"proof"} if n not in envs)
    body = re.sub(rf"\\(begin|end)\s*\{{({names})\*?\}}", r"\\\1{proof}", body)
    starts = (rf"\\begin\s*\{{(?:{'|'.join(re.escape(e) for e in envs)})\*?\}}" if envs else None)
    out, pos = [], 0
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
        heading = re.sub(r"^(?:Proof|Preuve|Démonstration)\s*(?:of|de)?\s*", "", plain(title.replace("\\ref", "\\ref")), flags=re.I)
        out.append(body[pos:match.start()])
        out.append(f"\\begin{{proof}}[{title}]{body[rest:stop]}\\end{{proof}}")
        pos = stop
    out.append(body[pos:])
    return "".join(out)


# ---------- Analyse ----------

def _letter(n):
    return chr(ord("A") + n - 1) if 1 <= n <= 26 else str(n)


def strip_hidden(text):
    """Passages que LaTeX n'imprime pas : \\begin{comment}…\\end{comment}, \\iffalse…\\fi."""
    text = re.sub(r"\\begin\s*\{comment\}.*?\\end\s*\{comment\}", " ", text, flags=re.S)
    out, pos = [], 0
    for match in re.finditer(r"\\iffalse\b", text):
        if match.start() < pos:
            continue
        depth, cursor = 1, match.end()
        for token in re.finditer(r"\\(if[A-Za-z]*|fi)\b", text[cursor:]):
            depth += -1 if token.group(1) == "fi" else 1
            if depth == 0:
                out.append(text[pos:match.start()])
                pos = cursor + token.end()
                break
        else:
            break
    out.append(text[pos:])
    return "".join(out)


def extract(text, bbl=""):
    """Structure (même forme que structure.extract_structure) à partir du texte TeX développé."""
    text = strip_hidden(text)
    envs, within, formats = declarations(text)
    begin = text.find("\\begin{document}")
    body = text[begin + len("\\begin{document}"):] if begin >= 0 else text
    end = body.find("\\end{document}")
    body = body[:end] if end >= 0 else body
    body = normalize_proofs(body, envs)

    toc, nodes, order, proofs = [], {}, [], {}
    counters, sections = {}, [0, 0, 0, 0]
    appendix = False
    labels = {}              # clé \label → ("theorem", id) ou ("equation", id d'énoncé ou None)
    refs = []                # (clés, contexte) contexte = ("node", id) | ("proof", id) | ("text", id de section)
    cites = []
    stack = []               # cadres ouverts : {"type": theorem|proof|equation, "id", "start"}
    candidate = None         # énoncé à démontrer qui attend sa preuve
    last_proof_target = None
    since_proof = True       # rien depuis la dernière preuve (deuxième preuve à la suite)
    proof_sections = {}      # id de section → clés citées dans son titre (« Proof of Theorem \ref{…} »)
    section_of_level = {}

    def number(env):
        counter = envs[env]["counter"]
        counters[counter] = counters.get(counter, 0) + 1
        level = within.get(counter) or within.get(env)
        value = _format(counters[counter], formats.get(counter) or formats.get(env))
        section = _letter(sections[1]) if appendix else str(sections[1])
        if level == "section":
            value = f"{section}.{value}"
        elif level == "subsection":
            value = f"{section}.{sections[2]}.{value}"
        elif level == "chapter":
            value = f"{sections[0]}.{value}"
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
            (short, title), _ = _args(body, pos, "[{")
            if title is None:
                continue
            level = SECTIONS[command]
            if not star:
                if level == 0:
                    sections[0] += 1
                elif level in (1, 2, 3):
                    sections[level] += 1
                    for deeper in range(level + 1, 4):
                        sections[deeper] = 0
                for counter, inside in within.items():
                    if (inside == "section" and level <= 1) or (inside == "subsection" and level <= 2) or (inside == "chapter" and level == 0):
                        counters[envs.get(counter, {}).get("counter", counter)] = 0
            section_id = f"tex-sec-{len(toc) + 1}"
            numbered = "" if star else ".".join(
                [_letter(sections[1]) if appendix else str(sections[1])] + [str(sections[i]) for i in range(2, level + 1)]) + ". "
            toc.append({"id": section_id, "level": level, "title": numbered + plain(title),
                        "anchors": [f"{command}.{numbered.rstrip('. ')}"] if numbered else []})
            section_of_level[level] = section_id
            for deeper in range(level + 1, 4):
                section_of_level.pop(deeper, None)
            if PROOF_HEADING.search(plain(title)):
                keys = [k.strip() for m in re.finditer(r"\\(?:ref|cref|Cref|autoref)\*?\{([^}]*)\}", title) for k in m.group(1).split(",")]
                if keys:
                    proof_sections[section_id] = keys
            candidate = None
            continue
        if command == "appendix":
            appendix = True
            sections[1] = 0
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
                    if not keys:
                        if candidate:
                            target = candidate
                        elif since_proof and last_proof_target:
                            target = last_proof_target  # deuxième preuve à la suite
                        elif order and not nodes[order[-1]]["proofs"] and not nodes[order[-1]]["insideProof"]:
                            target = order[-1]
                    proofs[proof_id] = {"id": proof_id, "of": target, "keys": keys, "start": start,
                                        "section": current_section(), "title": plain(optional[0]) if optional else ""}
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
                        elif kind == "proof":
                            proof = proofs[frame["id"]]
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
            elif owner:
                labels.setdefault(key, ("theorem", owner))
            continue
        if command in REF_COMMANDS or command == "hyperref":
            group = _group(body, pos, "[" if command == "hyperref" else "{")
            if group:
                refs.append(([k.strip() for k in group[0].split(",") if k.strip()], context()))
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

    for keys, ctx in refs:
        for owner in owners(ctx):
            for key in keys:
                found = labels.get(key)
                if not found:
                    continue
                if found[0] == "theorem" and found[1] != owner:
                    add(nodes[owner]["uses"], found[1])
                elif found[0] == "equation":
                    _, statement, proof = found
                    add(nodes[owner]["usesEquations"], key)
                    holder = statement or (proofs.get(proof, {}).get("of") if proof else None)
                    if holder and holder != owner:
                        add(nodes[owner]["uses"], holder)
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
    return {
        "labels": {key: (found[1] if found[0] == "theorem" else None) for key, found in labels.items()},
        "macros": macros(preamble),
        "version": STRUCTURE_VERSION, "source": "tex", "toc": toc,
        "nodes": [{k: v for k, v in nodes[n].items() if k not in ("insideProof", "chain")} for n in order],
        "proofBlocks": [{"id": p["id"], "of": p["of"], "anchors": []} for p in proofs.values()],
        "equations": equations, "bibliography": bibliography(body + "\n" + bbl), "hiddenText": 0,
    }


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
    """Références d'un environnement thebibliography (ou d'un fichier .bbl de BibTeX)."""
    start = text.find("\\begin{thebibliography}")
    if start < 0:
        return []
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


def from_source(data):
    """Structure à partir de l'archive de la source arXiv ; None si ce n'est pas du TeX."""
    files = _read_archive(data, extensions=(".tex", ".ltx", ".bbl"))
    if not files:
        return None
    candidates = [name for name, text in files.items() if name.endswith((".tex", ".ltx")) and DOCUMENTCLASS.search(_strip_comments(text))]
    if not candidates:
        return None
    main = next((n for n in candidates if "\\begin{document}" in files[n]), candidates[0])
    text = _expand(files, main)
    bbl = next((t for n, t in files.items() if n.endswith(".bbl")), "")
    if "\\begin{thebibliography}" not in text and bbl:
        text = text.replace("\\end{document}", bbl + "\n\\end{document}") if "\\end{document}" in text else text + bbl
    return extract(text), text


# ---------- Pages du PDF ----------

def locate_pages(structure, pdf_bytes):
    """Page (à partir de 1) de chaque énoncé et section : ancres de hyperref, sinon texte du PDF."""
    import pypdf
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        destinations = {}
        for name, destination in (reader.named_destinations or {}).items():
            try:
                destinations[str(name)] = reader.get_destination_page_number(destination) + 1
            except Exception:
                continue
        texts = None
        for item in structure["nodes"] + structure["toc"]:
            page = next((destinations[a] for a in item.get("anchors", []) if a in destinations), None)
            if page is None:
                if texts is None:
                    texts = [re.sub(r"\s+", " ", page.extract_text() or "") for page in reader.pages]
                page = _search(texts, item)
            item["page"] = page
        for proof in structure.get("proofBlocks", []):
            target = next((n for n in structure["nodes"] if n["id"] == proof["of"]), None)
            proof["page"] = target.get("page") if target else None
    except Exception:
        pass
    return structure


def _search(texts, item):
    label = item.get("label") or item.get("title") or ""
    if not label:
        return None
    if "level" in item:  # section : son titre
        words = re.escape(re.sub(r"^[\dA-Z.]+\s+", "", label)[:40]).replace(r"\ ", r"\s*")
        pattern = re.compile(words, re.I)
    else:  # « Theorem 2.3. » en tête d'énoncé, sinon toute mention
        name, _, value = label.rpartition(" ")
        pattern = re.compile(rf"{re.escape(name)}\s*{re.escape(value)}\s*[.(:]") if value else re.compile(re.escape(label))
    for number, text in enumerate(texts):
        if pattern.search(text):
            return number + 1
    return None
