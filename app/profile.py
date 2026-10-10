"""Profil de recherche : vos articles, articles et livres de référence, auteurs suivis, mots-clés.

Le profil peut être pré-rempli à partir de la bibliothèque et des bibliographies de vos articles
(auteurs souvent cités, livres cités, mots-clés). Il sert à trier les nouveautés arXiv par pertinence
et à suivre les nouveaux articles des auteurs choisis.
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter

import httpx
from bs4 import BeautifulSoup

from . import db
from .arxiv import ATOM, ArxivError, fetch_metadata, parse_arxiv_id
from .config import USER_AGENT
from .library import paper_dir
from .relevance import STOPWORDS, _ascii, author_key

KINDS = ("mine", "paper", "book", "author", "keyword")
S2 = "https://api.semanticscholar.org/graph/v1"


def _get_s2(path, params, api_key=None):
    headers = {"User-Agent": USER_AGENT, **({"x-api-key": api_key} if api_key else {})}
    with httpx.Client(headers=headers, timeout=30) as client:
        for attempt in range(4):
            response = client.get(f"{S2}{path}", params=params)
            if response.status_code != 429:
                break
            time.sleep(2 * 2 ** attempt)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


# ---------- Éléments du profil ----------

def list_items():
    with db.connect() as connection:
        rows = connection.execute("SELECT * FROM profile_items ORDER BY kind, added_at").fetchall()
    return [dict(r, authors=json.loads(r["authors"] or "[]")) for r in rows]


def add_item(kind, label, ref=None, authors=None, year=None, text=None, source="manual"):
    if kind not in KINDS or not label.strip():
        raise ValueError("bad item")
    with db.connect() as connection:
        duplicate = connection.execute(
            "SELECT id FROM profile_items WHERE kind = ? AND (lower(label) = lower(?) OR (ref IS NOT NULL AND ref = ?))",
            (kind, label.strip(), ref),
        ).fetchone()
        if duplicate:
            return duplicate["id"]
        cursor = connection.execute(
            "INSERT INTO profile_items (kind, label, ref, authors, year, text, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, label.strip(), ref, json.dumps(authors or [], ensure_ascii=False), year, text, source),
        )
        return cursor.lastrowid


def remove_item(item_id):
    with db.connect() as connection:
        connection.execute("DELETE FROM profile_items WHERE id = ?", (item_id,))


# ---------- Recherche d'un article, d'un livre ou d'un auteur ----------

def resolve(kind, query, api_key=None):
    """Candidats pour une saisie : identifiant arXiv, DOI ou titre (articles, livres) ; nom (auteurs)."""
    query = query.strip()
    if kind == "author":
        data = _get_s2("/author/search", {"query": query, "fields": "name,affiliations,paperCount", "limit": 6}, api_key)
        # mots affichés (« articles », « livre ») : traduits par l'interface
        return [{"label": a["name"], "ref": f"s2author:{a['authorId']}",
                 "detail": ", ".join(a.get("affiliations") or []), "paperCount": a.get("paperCount", 0)}
                for a in (data or {}).get("data", [])]
    try:
        arxiv_id, _ = parse_arxiv_id(query)
        meta = fetch_metadata(arxiv_id)
        return [{"label": meta.title, "ref": arxiv_id, "authors": meta.authors, "year": meta.published[:4],
                 "text": meta.abstract, "detail": f"arXiv:{arxiv_id}"}]
    except ArxivError:
        pass
    fields = "title,year,authors,abstract,externalIds,publicationTypes"
    doi = re.search(r"10\.\d{4,9}/\S+", query)
    if doi:
        paper = _get_s2(f"/paper/DOI:{doi.group(0)}", {"fields": fields}, api_key)
        papers = [paper] if paper else []
    else:
        papers = (_get_s2("/paper/search", {"query": query, "fields": fields, "limit": 6}, api_key) or {}).get("data", [])
    candidates = []
    for paper in papers:
        ids = paper.get("externalIds") or {}
        ref = ids.get("ArXiv") or f"s2:{paper['paperId']}"
        candidates.append({
            "label": paper.get("title") or "", "ref": ref, "year": str(paper.get("year") or ""),
            "authors": [a.get("name", "") for a in paper.get("authors") or []], "text": paper.get("abstract") or "",
            "detail": " · ".join(filter(None, [", ".join(a.get("name", "") for a in (paper.get("authors") or [])[:4]),
                                               str(paper.get("year") or "")])),
            "book": "Book" in (paper.get("publicationTypes") or []),
        })
    return candidates


# ---------- Pré-remplissage ----------

def _split_authors(text):
    parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", text or "")
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 2]


def prefill(owner, api_key=None):
    """Propose un profil à partir de la bibliothèque et des bibliographies des articles de `owner`."""
    owner_key = author_key(owner) if owner else None
    with db.connect() as connection:
        papers = [dict(r) for r in connection.execute("SELECT id, arxiv_id, title, authors, abstract FROM papers WHERE in_library = 1")]
    mine, cited_authors, author_labels, books, keywords = [], Counter(), {}, {}, []
    for paper in papers:
        authors = json.loads(paper["authors"] or "[]")
        is_mine = owner_key and any(author_key(a) == owner_key for a in authors)
        add_item("mine" if is_mine else "paper", paper["title"], ref=paper["arxiv_id"], authors=authors,
                 text=paper["abstract"], source="suggested")
        if not is_mine:
            continue
        mine.append(paper)
        content = paper_dir(paper["id"]) / "content.html"
        if not content.exists():
            continue
        soup = BeautifulSoup(content.read_text(encoding="utf-8"), "html.parser")
        for item in soup.select("li.ltx_bibitem"):
            author_text = item.select_one(".ltx_bib_author")
            names = _split_authors(author_text.get_text(" ", strip=True)) if author_text else []
            for name in names:
                key = author_key(name)
                if key and key != owner_key:
                    cited_authors[key] += 1
                    if len(name) > len(author_labels.get(key, "")):  # forme la plus complète (« Xiaonan Ma » > « X. Ma »)
                        author_labels[key] = name
            title = item.select_one(".ltx_bib_title")
            if "ltx_bib_book" in (item.get("class") or []) and title:
                year = item.select_one(".ltx_bib_year")
                books[title.get_text(" ", strip=True)] = (names, (year.get_text(strip=True).strip("()") if year else ""))
        keyword_block = soup.select_one(".ltx_keywords")
        if keyword_block:
            text = keyword_block.get_text(" ", strip=True).split(":", 1)[-1]
            keywords += [k.strip(" .") for k in text.split(",") if k.strip(" .")]

    # Auteurs cités au moins deux fois (au plus 12), sous la forme la plus complète rencontrée
    categories = db.get_settings().get("feedCategories") or ["math.DG", "math.CV", "math.SP", "math-ph"]
    for key, count in cited_authors.most_common():
        if count < 2:
            break
        label = author_labels[key]
        if _initials_only(label):  # « X. Ma » : prénom complet retrouvé sur arXiv si possible
            colleagues = {k for k, c in cited_authors.items() if c >= 2 and k != key}
            label = complete_author_name(label, categories, colleagues) or label
            time.sleep(3.1)
        add_item("author", label, source="suggested")
        if sum(1 for i in list_items() if i["kind"] == "author") >= 12:
            break

    # Livres cités : retrouvés sur Semantic Scholar quand c'est possible (pour leur vecteur de sens)
    for title, (names, year) in list(books.items())[:10]:
        ref, text = None, ""
        try:
            match = _get_s2("/paper/search/match", {"query": title, "fields": "title,abstract,externalIds"}, api_key)
            found = (match or {}).get("data") or []
            if found:
                ref, text = f"s2:{found[0]['paperId']}", found[0].get("abstract") or ""
        except httpx.HTTPError:
            pass
        add_item("book", title, ref=ref, authors=names, year=year, text=text, source="suggested")
        time.sleep(1.1)

    # Mots-clés : ceux déclarés par l'article, sinon les expressions de deux mots les plus présentes
    if not keywords:
        counts = Counter()
        for paper in mine:
            words = re.findall(r"[a-z][a-z\-]+", _ascii(f"{paper['title']} {paper['abstract'] or ''}").lower())
            # paires de mots réellement consécutifs, sans mot vide (« indicator function », pas « symbol indicator »)
            counts.update(f"{a} {b}" for a, b in zip(words, words[1:])
                          if a not in STOPWORDS and b not in STOPWORDS and len(a) > 3 and len(b) > 3)
        keywords = [k for k, c in counts.most_common(6) if c >= 2][:5]
    for keyword in keywords[:8]:
        add_item("keyword", keyword, source="suggested")
    return list_items()


# ---------- Prénom complet d'un auteur cité par ses initiales ----------

def complete_author_name(name, categories, colleagues=()):
    """« X. Ma » → « Xiaonan Ma » : prénom complet le plus fréquent parmi les articles arXiv de ce nom
    de famille dans vos catégories (au moins deux articles), sinon None. Les articles cosignés avec
    d'autres auteurs que vous citez (`colleagues`, clés nom/initiale) comptent davantage : c'est ce qui
    distingue Xiaonan Ma (coauteur de G. Marinescu) de Xiang Ma."""
    surname = _surname(name)
    wanted = author_key(name)
    if not surname or not wanted:
        return None
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=40) as client:
        for attempt in range(4):
            response = client.get("https://export.arxiv.org/api/query", params={
                "search_query": f"au:{surname} AND ({' OR '.join(f'cat:{c}' for c in categories)})", "max_results": 100})
            if response.status_code not in (429, 503):
                break
            time.sleep(3 * (attempt + 1))
    if response.status_code != 200:
        return None
    counts = Counter()
    for entry in ET.fromstring(response.text).findall(f"{ATOM}entry"):
        if not any(c.get("term") in categories for c in entry.findall(f"{ATOM}category")):
            continue
        names = [" ".join((a.findtext(f"{ATOM}name") or "").split()) for a in entry.findall(f"{ATOM}author")]
        shared = any(author_key(n) in colleagues for n in names)
        for full in names:
            if author_key(full) == wanted and not re.match(r"^\w\.", full) and len(full.split()) > 1:
                counts[full] += 4 if shared else 1
    best = counts.most_common(1)
    return best[0][0] if best and best[0][1] >= 2 else None


def _initials_only(name):
    return bool(re.match(r"^(\w\.\s*)+\S", name.strip()))


# ---------- Auteurs suivis : leurs articles récents sur arXiv ----------

def _surname(name):
    parts = [p for p in re.split(r"[\s.]+", _ascii(name)) if p]
    return parts[-1] if parts else None


def followed_author_papers(categories, per_author=30, days=60):
    """Articles récents des auteurs suivis (toutes catégories).

    L'API arXiv cherche « au:Marinescu_G » à l'identique (« G. Marinescu » seulement) : on demande
    donc le nom de famille seul, un auteur à la fois (3 s entre deux requêtes, comme le demande arXiv),
    puis on filtre l'initiale du prénom et, pour un auteur connu seulement par son initiale, les
    catégories (les homonymes sont fréquents hors des mathématiques).
    """
    authors = [i["label"] for i in list_items() if i["kind"] == "author"]
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    papers = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=40) as client:
        for index, author in enumerate(authors):
            surname = _surname(author)
            if not surname:
                continue
            if index:
                time.sleep(3.1)
            for attempt in range(4):
                response = client.get("https://export.arxiv.org/api/query", params={
                    "search_query": f"au:{surname}", "sortBy": "submittedDate", "sortOrder": "descending",
                    "max_results": per_author})
                if response.status_code not in (429, 503):
                    break
                time.sleep(3 * (attempt + 1))
            if response.status_code != 200:
                continue
            wanted = author_key(author)
            full_name = _ascii(author).lower() if len(author.split()) > 1 and not re.match(r"^\w\.", author) else None
            for entry in ET.fromstring(response.text).findall(f"{ATOM}entry"):
                published = (entry.findtext(f"{ATOM}published") or "")[:10]
                if published < since:
                    continue
                names = [" ".join((a.findtext(f"{ATOM}name") or "").split()) for a in entry.findall(f"{ATOM}author")]
                if full_name:
                    matched = any(_ascii(n).lower() == full_name for n in names)
                else:
                    matched = any(author_key(n) == wanted for n in names)
                categories_here = [c.get("term") for c in entry.findall(f"{ATOM}category")]
                if not matched or (not full_name and not any(c in categories for c in categories_here)):
                    continue
                match = re.search(r"abs/(.+?)(v(\d+))?$", entry.findtext(f"{ATOM}id") or "")
                if not match:
                    continue
                papers[match.group(1)] = {
                    "key": match.group(1), "arxiv_id": match.group(1), "version": int(match.group(3) or 1),
                    "title": " ".join((entry.findtext(f"{ATOM}title") or "").split()),
                    "authors": names, "abstract": " ".join((entry.findtext(f"{ATOM}summary") or "").split()),
                    "categories": categories_here, "primary_category": categories_here[0] if categories_here else "",
                    "announced": published, "link": f"https://arxiv.org/abs/{match.group(1)}",
                }
    return list(papers.values())
