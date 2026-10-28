"""Assistant de premier lancement : retrouver ses articles sur arXiv (identifiant d'auteur arXiv, ORCID ou nom),
en déduire les catégories du feed et les auteurs à suivre (coauteurs, auteurs les plus cités), et remplir le
profil en une fois. Rien n'est enregistré avant « Terminer » (apply)."""

import re
import threading
import time
import logging
import xml.etree.ElementTree as ET
from collections import Counter

import httpx

from . import db, library, profile, s2, search, zotero
from .arxiv import with_retry
from .config import USER_AGENT
from .relevance import author_key, same_person

ORCID = re.compile(r"\b(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b", re.I)
FEED_OWNER = re.compile(r"<title>\s*(.+?)['’]s articles on arXiv\s*</title>")
MAX_AUTHORS = 30  # au-delà (grandes collaborations), un article ne propose pas ses coauteurs
log = logging.getLogger("arxivreader.onboarding")
ARXIV_AUTHOR = re.compile(r"(?:arxiv\.org/a/)?([a-z][a-z0-9_]*_\d+)(?:\.atom2?|\.html)?/?$", re.I)
S2_BATCH = f"{s2.GRAPH}/paper/batch"


class OnboardingError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def identifier(text):
    """Identifiant d'auteur arXiv (« ma_x_1 », ou l'adresse arxiv.org/a/…) ou ORCID, sinon None."""
    text = (text or "").strip()
    if found := ORCID.search(text):
        return found.group(1).upper()
    if found := ARXIV_AUTHOR.search(text):
        return found.group(1).lower()
    return None




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
        try:
            items, _ = search.parse(response.text)
        except ET.ParseError:
            raise OnboardingError("bad-response")  # page d'arXiv au lieu du flux (maintenance…)
        total = len(items)
        named = FEED_OWNER.search(response.text)  # « Prénom Nom's articles on arXiv »
        owner = None
    else:
        name = " ".join((name or "").replace('"', " ").split())
        if len(name) < 3:
            raise OnboardingError("no-name")
        try:
            items, total = search._fetch(f'au:"{name}"', [], "newest", 0, 100)
        except ET.ParseError:
            raise OnboardingError("bad-response")
        except search.SearchError:
            raise OnboardingError("search")
        owner = name
    papers = []
    for item in items:
        likely = owner is None or any(same_person(owner, a) for a in item["authors"])
        papers.append({"arxivId": item["arxiv_id"], "title": item["title"], "authors": item["authors"], "year": item["announced"][:4],
                       "categories": item["categories"], "primary": item["primary_category"], "abstract": item["abstract"], "likely": likely})
    if not key:
        _outside_fields(papers)
        return {"papers": papers, "owner": name, "total": total}
    owner = " ".join(named.group(1).split()) if named else _owner_name(papers, name)
    return {"papers": papers, "owner": owner, "total": total}


def _archive(category):
    return category.split(".")[0]  # math.DG → math, cond-mat.mtrl-sci → cond-mat


def _outside_fields(papers):
    """Homonyme exact (même nom complet) : son article est souvent dans un autre domaine. Seulement quand ces
    articles sont rares (au plus un dixième des articles du nom, sur au moins six) et hors du domaine principal :
    décochés d'office. Sinon (peu d'articles, ou deux domaines comparables : impossible de savoir lequel est le
    vôtre), rien n'est deviné ; un identifiant arXiv ou ORCID évite la question."""
    named = [p for p in papers if p["likely"]]
    if len(named) < 6:
        return
    counts = Counter(a for p in named for a in {_archive(c) for c in p["categories"]})
    main = counts.most_common(1)[0][0] if counts else None
    outside = [p for p in named if main not in {_archive(c) for c in p["categories"]}]
    if outside and len(outside) * 10 <= len(named):
        for paper in outside:
            paper["likely"] = False


def _owner_name(papers, fallback):
    """Nom de l'auteur d'un identifiant, quand le flux ne le dit pas : celui qui figure sur tous ses articles (sous
    sa forme la plus complète) ; à égalité (un directeur sur tous les articles), le nom donné, sinon rien : la page
    le fait préciser."""
    counts, forms = Counter(), {}
    for paper in papers:
        for name in paper["authors"]:
            key = author_key(name)
            counts[key] += 1
            if len(name) > len(forms.get(key, "")):
                forms[key] = name
    if not counts:
        return fallback
    best = [key for key, n in counts.items() if n == max(counts.values())]
    if len(best) == 1:
        return forms[best[0]]
    return next((forms[key] for key in best if fallback and same_person(fallback, forms[key])), fallback or "")


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
        if len(paper.get("authors") or []) <= MAX_AUTHORS:  # grande collaboration : pas de coauteurs proposés
            for name in paper.get("authors") or []:
                count(coauthors, name)
    cited = Counter()
    ids = [f"arXiv:{p['arxivId']}" for p in papers][:100]
    if ids:
        try:
            headers = s2.headers(api_key)
            with httpx.Client(headers=headers, timeout=60) as client:
                response = with_retry(s2.paced(lambda: client.post(S2_BATCH, params={"fields": "references.authors"}, json={"ids": ids}), api_key),
                                      retry_on=(429, 503), delays=(3, 6))
            answer = response.json() if response.status_code == 200 else []
            for paper in answer if isinstance(answer, list) else []:
                references = paper.get("references") if isinstance(paper, dict) else None
                for reference in references if isinstance(references, list) else []:
                    authors = reference.get("authors") if isinstance(reference, dict) else None
                    for author in authors if isinstance(authors, list) else []:
                        if isinstance(author, dict) and isinstance(author.get("name"), str) and author["name"].strip():
                            count(cited, author["name"])
        except (httpx.HTTPError, ValueError):
            pass  # sans Semantic Scholar (ou réponse inattendue) : les coauteurs suffisent
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
    bibliothèque en arrière-plan si demandé. Entrées vérifiées avant toute écriture (titres vides écartés, longueurs
    bornées comme pour le Profil)."""
    papers = [{**p, "title": " ".join((p.get("title") or "").split())[:400], "abstract": (p.get("abstract") or "")[:20000]}
              for p in papers if (p.get("title") or "").strip()]
    authors = [a for a in authors if a.strip()]
    values = {"onboarded": True}
    if owner:
        values["profileOwner"] = owner
    if categories:
        values["feedCategories"] = categories
    db.save_settings(values)
    for paper in papers:
        profile.add_item("mine", paper["title"], ref=paper["arxivId"], authors=(paper.get("authors") or [])[:100],
                         year=paper.get("year") or None, text=paper["abstract"])
    for name in authors:
        profile.add_item("author", name)
    if import_mine and papers:
        threading.Thread(target=_import_all, args=([p["arxivId"] for p in papers],), daemon=True).start()
    return profile.list_items()


def _import_all(arxiv_ids):
    """Import en arrière-plan (au rythme d'arXiv) ; déjà dans la bibliothèque : laissé ; échec : noté au journal."""
    present = {p["arxiv_id"] for p in db.list_papers()}
    for arxiv_id in arxiv_ids:
        if arxiv_id in present:
            continue
        try:
            library.import_paper(arxiv_id)
            zotero.touch()
        except Exception as error:
            log.warning("import de %s impossible : %s", arxiv_id, error)
            time.sleep(1)
