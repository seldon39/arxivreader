"""Renvois d'un article à l'autre : une référence de la bibliographie qui est aussi un article lu ici.

« [12, Theorem 4.3] » : si la référence [12] est un article de la bibliothèque (même identifiant arXiv,
même DOI, ou titre retrouvé dans la référence), l'énoncé « Theorem 4.3 » de cet article peut s'afficher
au survol et s'ouvrir d'un clic.
"""

import re
import unicodedata

from . import db, library

MIN_TITLE_WORDS = 4  # titre trop court (« Bergman kernels ») : pas de rapprochement par le titre


def _words(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\\[a-zA-Z]+|\$|[{}]", " ", text)  # TeX des titres ($L^2$, \mathbb…)
    return re.findall(r"[a-z0-9]+", text.lower())


def _arxiv(identifier):
    return re.sub(r"v\d+$", "", (identifier or "").strip().lower())


def library_index(exclude=None):
    """Articles lus ici (bibliothèque ou simplement ouverts) dont la structure est connue."""
    index = []
    for paper in db.list_all_papers():
        if paper["id"] == exclude:
            continue
        structure = library.load_structure(paper["id"])
        if not structure:
            continue
        index.append({
            "id": paper["id"], "title": paper["title"], "arxiv": _arxiv(paper["arxiv_id"]),
            "doi": (paper.get("doi") or "").lower(), "words": " ".join(_words(paper["title"])),
            "nodes": [{"id": n["id"], "label": n["label"], "kind": n["kind"]} for n in structure.get("nodes", [])],
        })
    return index


def match(entry, index):
    arxiv_ids = {_arxiv(a) for a in entry.get("arxiv") or []}
    doi = (entry.get("doi") or "").lower()
    text = " " + " ".join(_words(f"{entry.get('title') or ''} {entry.get('text') or ''}")) + " "
    for paper in index:
        if paper["arxiv"] and paper["arxiv"] in arxiv_ids:
            return paper
        if doi and paper["doi"] and doi == paper["doi"]:
            return paper
    for paper in index:
        if len(paper["words"].split()) >= MIN_TITLE_WORDS and f" {paper['words']} " in text:
            return paper
    return None


def links(paper_id):
    """{id de la référence: {paper, title, nodes}} pour les références qui sont des articles lus ici."""
    structure = library.load_structure(paper_id) or {}
    index = library_index(exclude=paper_id)
    found = {}
    for entry in structure.get("bibliography", []):
        paper = match(entry, index)
        if paper:
            found[entry["id"]] = {"paper": paper["id"], "title": paper["title"], "nodes": paper["nodes"]}
    return found


_statements = {}  # id d'article → (date du fichier, {id d'énoncé: HTML}) : lu une fois par article


def statement(paper_id, node_id):
    """Énoncé d'un article (HTML nettoyé à l'import), pour un aperçu depuis un autre article."""
    from bs4 import BeautifulSoup
    path = library.paper_dir(paper_id) / "content.html"
    structure = library.load_structure(paper_id) or {}
    node = next((n for n in structure.get("nodes", []) if n["id"] == node_id), None)
    if node is None:
        return None
    html = ""
    if path.exists():
        stamp = path.stat().st_mtime
        if _statements.get(paper_id, (None,))[0] != stamp:
            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            _statements[paper_id] = (stamp, {e["id"]: str(e) for e in soup.select(".ltx_theorem[id]")})
        html = _statements[paper_id][1].get(node_id, "")
    return {"label": node["label"], "kind": node["kind"], "html": html, "tex": node.get("tex")}
