"""Export des notes d'un article en Markdown (lisible partout : Obsidian, éditeur de texte, GitHub…).

Fiche de lecture, puis énoncés et preuves annotés (état de lecture, note), dans l'ordre du texte, puis passages
surlignés regroupés par section, avec leur note. Les formules restent en LaTeX ($…$), comme dans l'application.
Toute la bibliothèque : une archive .zip, un fichier par article annoté.
"""

import io
import re
import time
import zipfile

from bs4 import BeautifulSoup

from . import annotations, db
from .library import load_structure, paper_dir

STATES = {"fr": {"understood": "compris", "review": "à revoir", "verified": "vérifié"},
          "en": {"understood": "understood", "review": "to review", "verified": "verified"}}
WORDS = {"fr": {"sheet": "Fiche de lecture", "statements": "Énoncés et preuves", "highlights": "Passages surlignés",
                "proof": "Preuve de {label}", "exported": "Notes exportées d'arXivReader le {date}", "other": "Autres passages",
                "empty": "Aucune note sur cet article."},
         "en": {"sheet": "Reading notes", "statements": "Statements and proofs", "highlights": "Highlighted passages",
                "proof": "Proof of {label}", "exported": "Notes exported from arXivReader on {date}", "other": "Other passages",
                "empty": "No notes on this paper."}}


def _order(paper_id):
    """Rang de chaque identifiant d'élément dans le texte de l'article (ordre de lecture)."""
    content = paper_dir(paper_id) / "content.html"
    if not content.exists():
        return {}
    soup = BeautifulSoup(content.read_text(encoding="utf-8"), "html.parser")
    return {tag["id"]: rank for rank, tag in enumerate(soup.find_all(id=True))}


def _quote(text):
    return "\n".join(f"> {line}" if line.strip() else ">" for line in (text or "").strip().splitlines())


def _section_of(element_id, toc):
    """Section (la plus précise) qui contient l'élément : « S3.SS3.p2 » est dans « S3.SS3 »."""
    best = None
    for entry in toc:
        if element_id == entry["id"] or element_id.startswith(entry["id"] + "."):
            if best is None or len(entry["id"]) > len(best["id"]):
                best = entry
    return best


def paper_markdown(paper_id, language="fr"):
    words, states = WORDS.get(language, WORDS["en"]), STATES.get(language, STATES["en"])
    paper = db.get_paper(paper_id)
    notes = annotations.list_for(paper_id)
    structure = load_structure(paper_id) or {}
    nodes = {n["id"]: n for n in structure.get("nodes", [])}
    proof_of = {proof: node for node in nodes.values() for proof in node.get("proofs") or []}
    toc = structure.get("toc") or []
    order = _order(paper_id)
    rank = lambda element_id: order.get(element_id, len(order))  # noqa: E731

    link = f"https://arxiv.org/abs/{paper['arxiv_id']}v{paper['version']}" if paper.get("version") else f"https://arxiv.org/abs/{paper['arxiv_id']}"
    lines = [f"# {paper['title']}", "", ", ".join(paper.get("authors") or []), "",
             f"arXiv:{paper['arxiv_id']} · {link}", "", f"*{words['exported'].format(date=time.strftime('%Y-%m-%d'))}*", ""]
    sheet = next((n for n in notes if n["kind"] == "general" and (n.get("note") or "").strip()), None)
    blocks = [n for n in notes if n["kind"] == "block" and n.get("target") and ((n.get("note") or "").strip() or n.get("state"))]
    highlights = [n for n in notes if n["kind"] == "highlight" and n.get("target")]
    if not (sheet or blocks or highlights):
        return "\n".join(lines + [words["empty"], ""])
    if sheet:
        lines += [f"## {words['sheet']}", "", sheet["note"].strip(), ""]
    if blocks:
        lines += [f"## {words['statements']}", ""]
        for note in sorted(blocks, key=lambda n: rank(n["target"].get("id", ""))):
            element = note["target"].get("id", "")
            node = nodes.get(element)
            label = (node["label"] + (f" ({node['name']})" if node.get("name") else "")) if node else (
                words["proof"].format(label=proof_of[element]["label"]) if element in proof_of else element)
            state = f" — *{states[note['state']]}*" if note.get("state") in states else ""
            lines += [f"### {label}{state}", ""]
            if (note.get("note") or "").strip():
                lines += [note["note"].strip(), ""]
    if highlights:
        lines += [f"## {words['highlights']}", ""]
        current = None  # section du passage précédent (titre répété seulement quand elle change)
        for note in sorted(highlights, key=lambda n: (rank((n["target"].get("start") or {}).get("id", "")), n["id"])):
            section = _section_of((note["target"].get("start") or {}).get("id", ""), toc)
            if current is None or (section or {}).get("id", "") != current:
                lines += [f"### {section['title'] if section else words['other']}", ""]
                current = (section or {}).get("id", "")
            lines += [_quote(note.get("quote")), ""]
            if (note.get("note") or "").strip():
                lines += [note["note"].strip(), ""]
    return "\n".join(lines)


def filename(paper):
    """Nom de fichier : identifiant et début du titre (« 0806.2370 Toeplitz operators on symplectic.md »)."""
    title = re.sub(r"\$[^$]*\$", "", paper.get("title") or "")
    title = re.sub(r"[^\w\s\-]", "", title, flags=re.UNICODE)
    title = " ".join(title.split()[:6])
    return f"{paper['arxiv_id'].replace('/', '_')} {title}".strip() + ".md"


def library_zip(language="fr"):
    """Archive des notes de tous les articles annotés (bibliothèque ou seulement lus)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for paper_id in sorted(annotations.annotated_papers()):
            paper = db.get_paper(paper_id)
            notes = [n for n in annotations.list_for(paper_id) if n["kind"] in ("highlight", "block", "general")]
            if paper and notes:  # (un emplacement de preuve indiqué à la main n'est pas une note)
                archive.writestr(filename(paper), paper_markdown(paper_id, language))
    return buffer.getvalue()
