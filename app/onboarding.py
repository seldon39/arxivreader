"""Assistant de premier lancement : retrouver ses articles sur arXiv (identifiant d'auteur arXiv, ORCID ou nom),
en déduire les catégories du feed et les auteurs à suivre (coauteurs, auteurs les plus cités), et remplir le
profil en une fois. Rien n'est enregistré avant « Terminer » (apply)."""

import re
import threading
import time
import unicodedata
from collections import Counter

import httpx

from . import db, library, profile, search, zotero
from .arxiv import with_retry
from .config import USER_AGENT
from .relevance import author_key

ORCID = re.compile(r"\b(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b")
ARXIV_AUTHOR = re.compile(r"(?:arxiv\.org/a/)?([a-z][a-z0-9_]*_\d+)(?:\.atom2?|\.html)?/?$", re.I)
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"


class OnboardingError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def identifier(text):
    """Identifiant d'auteur arXiv (« ma_x_1 », ou l'adresse arxiv.org/a/…) ou ORCID, sinon None."""
    text = (text or "").strip()
    if found := ORCID.search(text):
        return found.group(1)
    if found := ARXIV_AUTHOR.search(text):
        return found.group(1).lower()
    return None


def _plain(name):
    return " ".join(re.sub(r"[^a-z\s]", " ", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()).split())


def same_person(name, other):
    """Même auteur : même nom complet, ou mêmes nom et initiale quand l'un des deux n'a que l'initiale du prénom
    (« X. Ma » et « Xiaonan Ma », mais pas « Xiaoming Ma »)."""
    a, b = _plain(name), _plain(other)
    if a == b:
        return True
    if author_key(name) != author_key(other):
        return False
    first = lambda text: text.split()[0] if len(text.split()) > 1 else ""  # noqa: E731
    return len(first(a)) <= 1 or len(first(b)) <= 1


def find(name="", ident=""):
    """Articles d'arXiv de l'auteur : par son identifiant (tous les siens), sinon par son nom (homonymes possibles :
    chacun est marqué « probable » si un de ses auteurs porte ce nom)."""
    key = identifier(ident)
    if ident.strip() and not key:
        raise OnboardingError("bad-id")
    if key:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True) as client:
            response = with_retry(lambda: client.get(f"https://arxiv.org/a/{key}.atom2"))
        if response.status_code == 404:
            raise OnboardingError("unknown-id")
        response.raise_for_status()
        items, _ = search.parse(response.text)
        owner = None
    else:
        name = " ".join((name or "").split())
        if len(name) < 3:
            raise OnboardingError("no-name")
        items, _ = search._fetch(f'au:"{name}"', [], "newest", 0, 100)
        owner = name
    papers = []
    for item in items:
        likely = owner is None or any(same_person(owner, a) for a in item["authors"])
        papers.append({"arxivId": item["arxiv_id"], "title": item["title"], "authors": item["authors"], "year": item["announced"][:4],
                       "categories": item["categories"], "primary": item["primary_category"], "abstract": item["abstract"], "likely": likely})
    if not key:
        _outside_fields(papers)
    return {"papers": papers, "owner": _owner_name(papers, name) if key else name}


def _archive(category):
    return category.split(".")[0]  # math.DG → math, cond-mat.mtrl-sci → cond-mat


def _outside_fields(papers):
    """Homonyme exact (même nom complet) : son article est souvent dans un autre domaine. Domaines habituels : ceux
    d'au moins un cinquième des articles du nom ; un article qui n'en touche aucun est décoché d'office."""
    named = [p for p in papers if p["likely"]]
    counts = Counter(a for p in named for a in {_archive(c) for c in p["categories"]})
    usual = {a for a, n in counts.items() if n * 5 >= len(named)}
    for paper in named:
        if usual and not usual & {_archive(c) for c in paper["categories"]}:
            paper["likely"] = False


def _owner_name(papers, fallback):
    """Nom de l'auteur d'un identifiant : celui qui figure sur tous ses articles (sous sa forme la plus complète)."""
    counts, forms = Counter(), {}
    for paper in papers:
        for name in paper["authors"]:
            key = author_key(name)
            counts[key] += 1
            if len(name) > len(forms.get(key, "")):
                forms[key] = name
    return forms[counts.most_common(1)[0][0]] if counts else fallback


def suggest(papers, owner, api_key=None):
    """Catégories (principales comptées double) et auteurs à suivre : coauteurs, puis auteurs les plus cités par ces
    articles (Semantic Scholar, si elle répond)."""
    me = author_key(owner) if owner else None
    categories = Counter()
    for paper in papers:
        for category in paper.get("categories") or []:
            categories[category] += 2 if category == paper.get("primary") else 1
    coauthors, forms = Counter(), {}
    def count(counter, name, weight=1):
        key = author_key(name)
        if not key or key == me:
            return
        counter[key] += weight
        if len(name) > len(forms.get(key, "")):
            forms[key] = name
    for paper in papers:
        for name in paper.get("authors") or []:
            count(coauthors, name)
    cited = Counter()
    ids = [f"arXiv:{p['arxivId']}" for p in papers][:100]
    if ids:
        try:
            headers = {"User-Agent": USER_AGENT, **({"x-api-key": api_key} if api_key else {})}
            with httpx.Client(headers=headers, timeout=60) as client:
                response = with_retry(lambda: client.post(S2_BATCH, params={"fields": "references.authors"}, json={"ids": ids}),
                                      retry_on=(429, 503), delays=(3, 6))
            if response.status_code == 200:
                for paper in response.json() or []:
                    for reference in (paper or {}).get("references") or []:
                        for author in reference.get("authors") or []:
                            if author.get("name"):
                                count(cited, author["name"])
        except (httpx.HTTPError, ValueError):
            pass  # sans Semantic Scholar : les coauteurs suffisent
    authors = []
    for key, n in coauthors.most_common(12):
        authors.append({"name": forms[key], "why": "coauthor", "count": n})
    for key, n in cited.most_common(30):
        if len(authors) >= 20:
            break
        if n >= 3 and all(author_key(a["name"]) != key for a in authors):
            authors.append({"name": forms[key], "why": "cited", "count": n})
    ranked = [{"category": c, "count": n} for c, n in categories.most_common(8)]
    return {"categories": ranked, "authors": authors}


def apply(owner, papers, categories, authors, import_mine=False):
    """Profil rempli (« vos articles », auteurs suivis), catégories du feed ; les articles importés dans la
    bibliothèque en arrière-plan si demandé."""
    for paper in papers:
        profile.add_item("mine", paper["title"], ref=paper["arxivId"], authors=paper.get("authors") or [],
                         year=paper.get("year") or None, text=paper.get("abstract") or "")
    for name in authors:
        profile.add_item("author", name)
    values = {"onboarded": True}
    if owner:
        values["profileOwner"] = owner
    if categories:
        values["feedCategories"] = categories
    db.save_settings(values)
    if import_mine and papers:
        threading.Thread(target=_import_all, args=([p["arxivId"] for p in papers],), daemon=True).start()
    return profile.list_items()


def _import_all(arxiv_ids):
    for arxiv_id in arxiv_ids:
        try:
            library.import_paper(arxiv_id)
            zotero.touch()
        except Exception:
            time.sleep(1)  # article introuvable, réseau : on passe au suivant
