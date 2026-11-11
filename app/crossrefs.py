"""Renvois d'un article à l'autre : une référence de la bibliographie qui est aussi un article lu ici.

« [12, Theorem 4.3] » : si la référence [12] est un article de la bibliothèque (même identifiant arXiv,
même DOI, ou titre retrouvé dans la référence), l'énoncé « Theorem 4.3 » de cet article peut s'afficher
au survol et s'ouvrir d'un clic.
"""

import json
import re
import unicodedata

from . import arxiv, db, library

SEQUEL = {"ii", "iii", "iv", "v", "vi", "vii", "viii", "part"}  # mot qui suit un titre : c'est une suite
MIN_TITLE_WORDS = 4  # titre trop court (« Bergman kernels ») : pas de rapprochement par le titre


def _words(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\\[a-zA-Z]+|\$|[{}]", " ", text)  # TeX des titres ($L^2$, \mathbb…)
    return re.findall(r"[a-z0-9]+", text.lower())


def _arxiv(identifier):
    # sans version ni classe (math.DG/0211159 → math/0211159) : comme les identifiants de la bibliographie ; la classe
    # s'enlève avant de passer en minuscules (elle s'écrit en capitales)
    return arxiv.strip_subject_class(re.sub(r"v\d+$", "", (identifier or "").strip())).lower()


_nodes_cache = {}  # id d'article → ((date, taille) de structure.json, énoncés) : relu seulement si le fichier change


def nodes_of(paper_id):
    """Énoncés d'un autre article, lus tels qu'enregistrés : jamais d'analyse refaite d'ici (ce serait réécrire, à
    l'ouverture d'un article, la structure de tous les autres) ; une structure d'une analyse plus ancienne suffit
    pour ce renvoi, elle est remise à jour quand cet article est ouvert."""
    path = library.paper_dir(paper_id) / "structure.json"
    try:
        info = path.stat()
        stamp = (info.st_mtime_ns, info.st_size)
        if _nodes_cache.get(paper_id, (None,))[0] != stamp:
            structure = json.loads(path.read_text(encoding="utf-8"))
            nodes = [{"id": n["id"], "label": n["label"], "kind": n["kind"]} for n in structure.get("nodes", [])]
            _nodes_cache[paper_id] = (stamp, nodes)
        return _nodes_cache[paper_id][1]
    except (OSError, ValueError, KeyError, TypeError):
        _nodes_cache.pop(paper_id, None)
        return None


def library_index(exclude=None):
    """Articles lus ici (bibliothèque ou simplement ouverts) dont la structure est connue."""
    index = []
    for paper in db.list_all_papers():
        if paper["id"] == exclude:
            continue
        nodes = nodes_of(paper["id"])
        if nodes is None:
            continue
        index.append({
            "id": paper["id"], "title": paper["title"], "arxiv": _arxiv(paper["arxiv_id"]),
            "doi": (paper.get("doi") or "").lower(), "words": " ".join(_words(paper["title"])), "nodes": nodes,
        })
    return index


def match(entry, index):
    return match_by(entry, index)[0]


def match_by(entry, index):
    """(article, comment) : « id » ou « doi » (certain), « title » (titre retrouvé dans la référence : approximatif),
    (None, None) sans correspondance."""
    arxiv_ids = {_arxiv(a) for a in entry.get("arxiv") or []}
    doi = (entry.get("doi") or "").lower()
    text = " " + " ".join(_words(f"{entry.get('title') or ''} {entry.get('text') or ''}")) + " "
    for paper in index:
        if paper["arxiv"] and paper["arxiv"] in arxiv_ids:
            return paper, "id"
        if doi and paper["doi"] and doi == paper["doi"]:
            return paper, "doi"
    best = None
    for paper in index:
        words = paper["words"]
        if len(words.split()) < MIN_TITLE_WORDS:
            continue
        at = text.find(f" {words} ")
        if at < 0:
            continue
        after = text[at + len(words) + 2:].split(" ", 1)[0]
        if after in SEQUEL:  # « …manifolds, II » : la suite d'un article, pas lui
            continue
        if best is None or len(words) > len(best["words"]):  # le titre le plus long (« …, I » avant son abrégé)
            best = paper
    return best, ("title" if best else None)


def links(paper_id):
    """{id de la référence: {paper, title, nodes}} pour les références qui sont des articles lus ici."""
    structure = library.load_structure(paper_id) or {}
    index = library_index(exclude=paper_id)
    found = {}
    for entry in structure.get("bibliography", []):
        paper, by = match_by(entry, index)
        if paper:
            # `by` : le bouton BibTeX n'est montré que pour une correspondance sûre (jamais par ressemblance de titre)
            found[entry["id"]] = {"paper": paper["id"], "title": paper["title"], "nodes": paper["nodes"], "by": by}
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
