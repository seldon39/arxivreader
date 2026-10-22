"""Export des notes d'un article en Markdown (lisible partout : Obsidian, éditeur de texte, GitHub…).

Fiche de lecture, puis énoncés et preuves annotés (état de lecture, note), dans l'ordre du texte, puis passages
surlignés regroupés par section, avec leur note. Les formules restent en LaTeX ($…$), comme dans l'application.
Toute la bibliothèque : une archive .zip, un fichier par article annoté.
"""

import io
import json
import re
import time
import zipfile

from . import annotations, db, notesync
from .library import paper_dir

ID = re.compile(r"""\sid=["']([^"']+)["']""")

STATES = {"fr": {"understood": "compris", "review": "à revoir", "verified": "vérifié"},
          "en": {"understood": "understood", "review": "to review", "verified": "verified"}}
WORDS = {"fr": {"sheet": "Fiche de lecture", "statements": "Énoncés et preuves", "highlights": "Passages surlignés",
                "proof": "Preuve de {label}", "exported": "Notes exportées d'arXivReader le {date}", "other": "Autres passages", "pdfPage": "PDF, page {page}", "zone": "Zone de la page (image)", "bookmarks": "Marque-pages", "library": "Bibliothèque", "folders": "Dossiers", "localPdf": "PDF importé", "texFile": "Fichier TeX",
                "empty": "Aucune note sur cet article.", "ai": "proposée par {who}, acceptée", "local": "un modèle local"},
         "en": {"sheet": "Reading notes", "statements": "Statements and proofs", "highlights": "Highlighted passages",
                "proof": "Proof of {label}", "exported": "Notes exported from arXivReader on {date}", "other": "Other passages", "pdfPage": "PDF, page {page}", "zone": "Region of the page (image)", "bookmarks": "Bookmarks", "library": "Library", "folders": "Folders", "localPdf": "Imported PDF", "texFile": "TeX file",
                "empty": "No notes on this paper.", "ai": "suggested by {who}, accepted", "local": "a local model"}}
AUTHORS = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}


def _order(paper_id):
    """Rang de chaque identifiant d'élément dans le texte de l'article (ordre de lecture ; lecture rapide des
    attributs id, sans analyser tout le HTML)."""
    content = paper_dir(paper_id) / "content.html"
    if not content.exists():
        return {}
    order = {}
    for element_id in ID.findall(content.read_text(encoding="utf-8")):
        order.setdefault(element_id, len(order))
    return order


def _structure(paper_id):
    """Structure enregistrée telle quelle (l'export ne relance pas l'analyse d'un article)."""
    try:
        return json.loads((paper_dir(paper_id) / "structure.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def has_notes(notes):
    return any((n["kind"] == "general" and (n.get("note") or "").strip())
               or (n["kind"] == "block" and n.get("target") and ((n.get("note") or "").strip() or n.get("state")))
               or (n["kind"] in ("highlight", "bookmark") and n.get("target"))
        or (n["kind"] == "note" and (n.get("note") or "").strip()) for n in notes)


def _who(note, words):
    who = words["local"] if note.get("author") == "local" else AUTHORS.get(note.get("author") or "")
    return f" *({words['ai'].format(who=who)})*" if who else ""


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
    structure = _structure(paper_id)
    nodes = {n["id"]: n for n in structure.get("nodes", [])}
    proof_of = {proof: node for node in nodes.values() for proof in node.get("proofs") or []}
    toc = structure.get("toc") or []
    # lu dans le PDF (source TeX) : pas de HTML, l'ordre des énoncés (et de leurs preuves) de la structure
    order = _order(paper_id) or {element_id: rank for rank, element_id in enumerate(
        [i for n in structure.get("nodes", []) for i in [n["id"], *(n.get("proofs") or [])]] + [s["id"] for s in toc])}
    rank = lambda element_id: order.get(element_id, len(order))  # noqa: E731

    link = f"https://arxiv.org/abs/{paper['arxiv_id']}v{paper['version']}" if paper.get("version") else f"https://arxiv.org/abs/{paper['arxiv_id']}"
    lines = [f"# {paper['title']}", "", ", ".join(paper.get("authors") or []), "",
             *([f"arXiv:{paper['arxiv_id']} · {link}", ""] if paper.get("arxiv_id") else []),
             f"*{words['exported'].format(date=time.strftime('%Y-%m-%d'))}*", ""]
    sheet = next((n for n in notes if n["kind"] == "general" and (n.get("note") or "").strip()), None)
    blocks = [n for n in notes if n["kind"] == "block" and n.get("target") and ((n.get("note") or "").strip() or n.get("state"))]
    # passages surlignés, et notes libres (sans passage : à leur paragraphe, ou à leur page du PDF)
    highlights = [n for n in notes if n.get("target") and (n["kind"] == "highlight" or n["kind"] == "note" and (n.get("note") or "").strip())]
    bookmarks = [n for n in notes if n["kind"] == "bookmark" and n.get("target")]
    if not (sheet or blocks or highlights or bookmarks):
        # rien de lisible (emplacements de preuve seulement) : les données restent, pour l'import
        return "\n".join(lines + [words["empty"], ""] + (["", notesync.data_block(paper, notes), ""] if notes else []))
    if sheet:
        lines += [f"## {words['sheet']}", "", sheet["note"].strip(), ""]
    if bookmarks:  # HTML dans l'ordre de l'article, puis PDF par page
        lines += [f"## {words['bookmarks']}", ""]
        spot = lambda n: (1, n["target"]["pdf"]["page"], -(n["target"]["pdf"].get("top") or 0)) if "pdf" in n["target"] \
            else (0, rank(n["target"].get("id", "")), 0)  # noqa: E731
        for note in sorted(bookmarks, key=spot):
            if "pdf" in note["target"]:
                where = words["pdfPage"].format(page=note["target"]["pdf"]["page"])
            else:
                section = _section_of(note["target"].get("id", ""), toc)
                where = section["title"] if section else ""
            lines.append(f"- **{note.get('quote') or ''}**" + (f" — {where}" if where else ""))
        lines.append("")
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
                lines += [note["note"].strip() + _who(note, words), ""]
    if highlights:
        lines += [f"## {words['highlights']}", ""]
        current = None  # section du passage précédent (titre répété seulement quand elle change)
        start_of = lambda n: n["target"].get("start") or ({"id": n["target"]["id"]} if n["target"].get("id") else {})  # noqa: E731
        pdf_of = lambda n: n["target"].get("pdf")  # noqa: E731  (passage surligné dans le PDF : page et position)
        # PDF : page, puis passages dans l'ordre du texte, puis notes libres de haut en bas (après les passages de la page)
        sort_key = lambda n: ((1, pdf_of(n)["page"], pdf_of(n)["start"] if "start" in pdf_of(n) else 10 ** 6 - (pdf_of(n).get("top") or 0), n["id"]) if pdf_of(n)  # noqa: E731
                              else (0, rank(start_of(n).get("id", "")), start_of(n).get("offset") or 0, n["id"]))
        for note in sorted(highlights, key=sort_key):
            if pdf_of(note):
                heading = words["pdfPage"].format(page=pdf_of(note)["page"])
                key = f"pdf-{pdf_of(note)['page']}"
            else:
                section = _section_of(start_of(note).get("id", ""), toc)
                heading, key = (section["title"] if section else words["other"]), (section or {}).get("id", "")
            if current is None or key != current:
                lines += [f"### {heading}", ""]
                current = key
            # zone du PDF (formule, figure) : pas de texte sûr, seulement sa mention
            if note["kind"] != "note":  # note libre : pas de passage cité, seulement la note
                lines += [f"*{words['zone']}*" if "rect" in (pdf_of(note) or {}) else _quote(note.get("quote")), ""]
            if (note.get("note") or "").strip():
                lines += [note["note"].strip() + _who(note, words), ""]
    # données de toutes les annotations, signées : l'import les recrée à l'identique (notesync.py)
    lines += ["", notesync.data_block(paper, notes), ""]
    return "\n".join(lines)


def filename(paper):
    """Nom de fichier : identifiant et début du titre (« 0806.2370 Toeplitz operators on symplectic.md »)."""
    title = re.sub(r"\$[^$]*\$", "", paper.get("title") or "")
    title = re.sub(r"[^\w\s\-]", "", title, flags=re.UNICODE)
    title = " ".join(title.split()[:6])
    return f"{(paper['arxiv_id'] or paper['id']).replace('/', '_')} {title}".strip() + ".md"  # PDF importé : son id


def _with_notes():
    """Articles qui ont de vraies notes (un emplacement de preuve indiqué à la main n'en est pas une)."""
    found = []
    for paper_id in sorted(annotations.annotated_papers()):
        paper = db.get_paper(paper_id)
        if paper and has_notes(annotations.list_for(paper_id)):
            found.append(paper)
    return found


def annotated_count():
    return len(_with_notes())


LIBRARY_FILE = "arXivReader library.md"


def library_markdown(language="fr"):
    """Toute la bibliothèque, annotée ou non, et ses dossiers : liste lisible, puis bloc de données signé (l'import
    rajoute les articles absents et recrée les dossiers)."""
    from . import folders
    words = WORDS.get(language, WORDS["en"])
    papers = db.list_papers()
    folder_list = folders.list_folders()
    by_id = {p["id"]: p for p in papers}
    line = lambda p: (f"- **{p['title']}** — {', '.join(p.get('authors') or [])}"  # noqa: E731
                      + (f" · arXiv:{p['arxiv_id']}" if p.get("arxiv_id") else f" · {words['texFile' if p.get('source') == 'local-tex' else 'localPdf']}"))
    lines = [f"# arXivReader — {words['library']}", "", f"*{words['exported'].format(date=time.strftime('%Y-%m-%d'))}*", ""]
    if folder_list:
        lines += [f"## {words['folders']}", ""]
        for folder in folder_list:
            lines += [f"### {folder['name']}", ""] + [line(by_id[i]) for i in folder["papers"] if i in by_id] + [""]
    lines += [f"## {words['library']} ({len(papers)})", ""] + [line(p) for p in papers] + ["", notesync.library_block(papers, folder_list), ""]
    return "\n".join(lines)


def library_zip(language="fr"):
    """Archive : la bibliothèque entière et ses dossiers, puis les notes de chaque article annoté."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(LIBRARY_FILE, library_markdown(language))
        # articles qui ont de vraies notes, ou des emplacements de preuve indiqués à la main (à garder à l'import)
        for paper_id in sorted(annotations.annotated_papers()):
            paper, notes = db.get_paper(paper_id), annotations.list_for(paper_id)
            if paper and (has_notes(notes) or any(n["kind"] == "proof" for n in notes)):
                archive.writestr(filename(paper), paper_markdown(paper_id, language))
    return buffer.getvalue()
