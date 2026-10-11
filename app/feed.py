"""Feed : nouveautés arXiv (flux RSS des catégories suivies), recommandations Semantic Scholar,
et repérage d'articles arXiv dans un texte collé (e-mail Scholar Inbox, export BibTeX…)."""

import json
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from . import db, profile, relevance
from .arxiv import ATOM, ARXIV_NS, extract_ids, with_retry
from .config import FEED_CATEGORIES, USER_AGENT

RSS_URL = "https://rss.arxiv.org/rss/{categories}"
S2_RECOMMEND = "https://api.semanticscholar.org/recommendations/v1/papers"
S2_FIELDS = "title,externalIds,authors,abstract,publicationDate,year,url"
DC = "{http://purl.org/dc/elements/1.1/}"
RSS_ARXIV = "{http://arxiv.org/schemas/atom}"


class FeedError(Exception):
    pass


# Accents et tirets TeX hors formules (« K\\"ahler », « Bohr--Sommerfeld ») → caractères ordinaires
ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "c": "\u0327", "v": "\u030c", "=": "\u0304"}
ACCENT = re.compile(r"""\\(["'`^~=]|[cv](?=[\s{]))\s*(?:\{\s*\\?([A-Za-z])\s*\}|\\?([A-Za-z]))""")


# Formules gardées telles quelles : $…$, $$…$$, \(…\), \[…\] et environnements (equation, align…)
MATH = re.compile(r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]"
                  r"|\\begin\{(equation|align|gather|multline|eqnarray|displaymath)\*?\}.*?\\end\{\1\*?\}", re.S)


def _plain_tex(part):
    part = ACCENT.sub(lambda m: unicodedata.normalize("NFC", (m.group(2) or m.group(3)) + ACCENTS[m.group(1)]), part)
    part = part.replace("``", "“").replace("''", "”").replace("---", "—").replace("--", "–").replace("\\ ", " ").replace("\\&", "&")
    return re.sub(r"\{\\(?:em|it|bf)\s+([^{}]*)\}", r"\1", part).replace("{", "").replace("}", "")


def tex_text(text):
    text, out, last = text or "", [], 0
    for match in MATH.finditer(text):  # hors des formules seulement
        out += [_plain_tex(text[last:match.start()]), match.group(0)]
        last = match.end()
    out.append(_plain_tex(text[last:]))
    return " ".join("".join(out).split())


# Résumés abîmés par l'ancienne version de tex_text : accolades retirées hors des formules $…$, donc
# aussi dans les équations $$…$$, \[…\], \(…\) et \begin{equation}… (« \beginequation* », « \fracn(n-2)4 »,
# indices doubles). Relus une fois sur arXiv, par requêtes groupées.
BROKEN = re.compile(r"\\(?:begin|end|frac|mathrm|mathbb|mathcal|mathfrak|operatorname|text)(?=[A-Za-z0-9])")
REPAIR_FLAG = "feed.abstractsRepaired"


def _maybe_broken(abstract):
    return bool(BROKEN.search(re.sub(r"\$[^$]*\$", "", abstract))) or any(m in abstract for m in ("$$", "\\[", "\\(", "\\begin"))


def repair_abstracts(batch=50):
    """Relit sur arXiv les résumés possiblement abîmés (une seule fois) ; nombre de résumés relus."""
    if db.get_settings().get(REPAIR_FLAG):
        return 0
    with db.connect() as connection:
        rows = connection.execute("SELECT key, arxiv_id, abstract FROM feed_items WHERE arxiv_id IS NOT NULL").fetchall()
    broken = {row["arxiv_id"]: row["key"] for row in rows if row["abstract"] and _maybe_broken(row["abstract"])}
    ids = list(broken)
    repaired = 0
    with _client() as client:
        for start in range(0, len(ids), batch):
            if start:
                time.sleep(3)  # arXiv : une requête toutes les 3 s au plus
            chunk = ids[start:start + batch]
            response = with_retry(lambda: client.get("https://export.arxiv.org/api/query",
                                                     params={"id_list": ",".join(chunk), "max_results": len(chunk)}))
            response.raise_for_status()
            with db.connect() as connection:
                for entry in ET.fromstring(response.text).findall(f"{ATOM}entry"):
                    found = re.search(r"abs/(.+?)(?:v\d+)?$", entry.findtext(f"{ATOM}id") or "")
                    summary = " ".join((entry.findtext(f"{ATOM}summary") or "").split())
                    if found and found.group(1) in broken and summary:
                        connection.execute("UPDATE feed_items SET abstract = ? WHERE key = ?", (tex_text(summary), broken[found.group(1)]))
                        repaired += 1
    db.save_settings({REPAIR_FLAG: _now()})
    return repaired


def _client(headers=None):
    return httpx.Client(headers={"User-Agent": USER_AGENT, **(headers or {})}, follow_redirects=True, timeout=40)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------- Nouveautés arXiv ----------

def parse_rss(xml_text):
    items = []
    channel = ET.fromstring(xml_text).find("channel")
    for item in channel.findall("item") if channel is not None else []:
        description = item.findtext("description") or ""
        match = re.match(r"\s*arXiv:(\S+?)(v(\d+))?\s+Announce Type:\s*(\S+)", description)
        if not match:
            continue
        abstract = description.split("Abstract:", 1)[-1].strip()
        try:
            announced = parsedate_to_datetime(item.findtext("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            announced = None
        categories = [c.text for c in item.findall("category") if c.text]
        authors = [tex_text(a) for a in (item.findtext(f"{DC}creator") or "").split(",") if a.strip()]
        items.append({
            "key": match.group(1),
            "arxiv_id": match.group(1),
            "version": int(match.group(3) or 1),
            "title": tex_text(item.findtext("title")),
            "authors": authors,
            "abstract": tex_text(abstract),
            "categories": categories,
            "primary_category": categories[0] if categories else "",
            "announce_type": item.findtext(f"{RSS_ARXIV}announce_type") or match.group(4),
            "announced": announced,
            "link": item.findtext("link") or f"https://arxiv.org/abs/{match.group(1)}",
        })
    return items


FOLLOWED_DAYS = 45  # articles récents des auteurs suivis : section du feed, alertes


def _store(connection, item, followed=False):
    connection.execute(
        f"""INSERT INTO feed_items (key, arxiv_id, version, title, authors, abstract, categories,
               primary_category, announce_type, announced, in_arxiv, followed, link, fetched_at)
           VALUES (:key, :arxiv_id, :version, :title, :authors, :abstract, :categories,
               :primary_category, :announce_type, :announced, :in_arxiv, :followed, :link, :fetched_at)
           ON CONFLICT(key) DO UPDATE SET {"followed = 1" if followed else
               "version = excluded.version, title = excluded.title, authors = excluded.authors, "
               "abstract = excluded.abstract, categories = excluded.categories, "
               "primary_category = excluded.primary_category, announce_type = excluded.announce_type, "
               "announced = excluded.announced, in_arxiv = 1, link = excluded.link"}""",
        {**item, "authors": json.dumps(item["authors"], ensure_ascii=False), "categories": json.dumps(item["categories"]),
         "announce_type": item.get("announce_type"), "in_arxiv": 0 if followed else 1, "followed": 1 if followed else 0,
         "fetched_at": _now()},
    )


def refresh_arxiv(categories=None, api_key=None):
    categories = categories or FEED_CATEGORIES
    with _client() as client:
        response = client.get(RSS_URL.format(categories="+".join(categories)))
    response.raise_for_status()
    items = parse_rss(response.text)
    with db.connect() as connection:
        for item in items:
            _store(connection, item)
    # Articles récents des auteurs suivis, hors des catégories comprises
    followed = []
    try:
        followed = profile.followed_author_papers(categories)
        with db.connect() as connection:
            for item in followed:
                item["title"], item["abstract"] = tex_text(item["title"]), tex_text(item["abstract"])
                _store(connection, item, followed=True)
    except Exception:
        pass
    # Vecteurs de sens (Semantic Scholar) pour les nouveaux articles et le profil
    positives, negatives, _, _ = relevance.profile_documents()
    keys = [relevance.s2_key(i["arxiv_id"]) for i in items + followed] + [d["s2"] for d in positives + negatives]
    try:
        relevance.fetch_embeddings(keys, api_key)
    except Exception:
        pass
    try:
        repair_abstracts()
    except Exception:
        pass
    db.save_settings({"feed.arxivRefreshed": _now()})
    return len(items)


# ---------- Jours précédents : interface OAI-PMH d'arXiv ----------

OAI_URL = "https://oaipmh.arxiv.org/oai"
OAI = "{http://www.openarchives.org/OAI/2.0/}"
RAW = "{http://arxiv.org/OAI/arXivRaw/}"


def _oai_set(category):
    """Ensemble OAI d'une catégorie (math-ph est aussi math.MP, donc dans « math »)."""
    archive = category.split(".")[0]
    if archive in ("math", "math-ph"):
        return "math"
    if archive in ("cs", "stat", "eess", "econ", "q-bio", "q-fin"):
        return archive
    return "physics"


def _split_raw_authors(text):
    parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", " ".join((text or "").split()))
    return [tex_text(p) for p in parts if p.strip()]


def parse_oai_day(xml_text, day, categories):
    """Notices arXivRaw d'une date d'annonce → articles de vos catégories, classés comme dans le RSS :
    une seule version → nouveau (ou croisé si la catégorie principale n'est pas suivie) ;
    version récente → nouvelle version ; sinon simple mise à jour des métadonnées, ignorée."""
    wanted = set(categories)
    root = ET.fromstring(xml_text)
    items = []
    for raw in root.iter(f"{RAW}arXivRaw"):
        cats = (raw.findtext(f"{RAW}categories") or "").split()
        if not wanted & set(cats):
            continue
        versions = raw.findall(f"{RAW}version")
        try:
            last = parsedate_to_datetime(versions[-1].findtext(f"{RAW}date")).date().isoformat()
        except (TypeError, ValueError, IndexError):
            continue
        primary = cats[0] in wanted
        if len(versions) == 1:
            kind = "new" if primary else "cross"
        elif (datetime.fromisoformat(day) - datetime.fromisoformat(last)).days <= 5:
            kind = "replace" if primary else "replace-cross"
        else:
            continue
        arxiv_id = raw.findtext(f"{RAW}id")
        items.append({
            "key": arxiv_id, "arxiv_id": arxiv_id, "version": len(versions),
            "title": tex_text(raw.findtext(f"{RAW}title")), "authors": _split_raw_authors(raw.findtext(f"{RAW}authors")),
            "abstract": tex_text(raw.findtext(f"{RAW}abstract")), "categories": cats, "primary_category": cats[0],
            "announce_type": kind, "announced": day, "link": f"https://arxiv.org/abs/{arxiv_id}",
        })
    token = root.find(f".//{OAI}resumptionToken")
    return items, (token.text.strip() if token is not None and token.text and token.text.strip() else None)


def fetch_day(day, categories=None, api_key=None):
    """Annonces d'un jour passé (date YYYY-MM-DD) ; 0 si arXiv n'a rien annoncé ce jour-là."""
    categories = categories or FEED_CATEGORIES
    items = []
    requested = False
    with _client() as client:
        for oai_set in sorted({_oai_set(c) for c in categories}):
            params = {"verb": "ListRecords", "metadataPrefix": "arXivRaw", "set": oai_set, "from": day, "until": day}
            while params:
                if requested:
                    time.sleep(3.1)  # au plus une requête toutes les 3 s, comme le demande arXiv
                requested = True
                response = with_retry(lambda: client.get(OAI_URL, params=params), retry_on=(503,), delays=(5, 10, 20))
                response.raise_for_status()
                if "noRecordsMatch" in response.text[:3000]:
                    break
                page, token = parse_oai_day(response.text, day, categories)
                items += page
                params = {"verb": "ListRecords", "resumptionToken": token} if token else None
    with db.connect() as connection:
        for item in items:
            _store(connection, item)
    try:
        relevance.fetch_embeddings([relevance.s2_key(i["arxiv_id"]) for i in items], api_key)
    except Exception:
        pass
    # « 0 annonce » n'est retenu que pour un jour assez ancien : un jour récent peut ne pas être encore
    # publié par l'OAI-PMH d'arXiv, il sera redemandé à la prochaine visite
    recent = date.fromisoformat(day) >= datetime.now(timezone.utc).date() - timedelta(days=3)
    if items or not recent:
        fetched = db.get_settings().get("feed.days") or {}
        fetched[day] = len(items)
        db.save_settings({"feed.days": fetched})
    return len(items)


def known_days():
    """Jours d'annonce déjà en base (date → nombre d'articles), et jours vérifiés sans annonce."""
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT announced, COUNT(*) AS n FROM feed_items WHERE in_arxiv = 1 GROUP BY announced").fetchall()
    days = {r["announced"]: r["n"] for r in rows if r["announced"]}
    for day, count in (db.get_settings().get("feed.days") or {}).items():
        days.setdefault(day, count)
    return days


# ---------- Recommandations Semantic Scholar ----------

def _positive_negative():
    with db.connect() as connection:
        library = [r["arxiv_id"] for r in connection.execute("SELECT arxiv_id FROM papers WHERE in_library = 1 ORDER BY added_at DESC")]
        rows = connection.execute(
            "SELECT s.key, s.status, f.arxiv_id FROM feed_status s LEFT JOIN feed_items f ON f.key = s.key"
        ).fetchall()
    # une recommandation reliée à la main à arXiv garde son identifiant Semantic Scholar (s2:…)
    pick = lambda r: r["key"] if r["key"].startswith("s2:") else (r["arxiv_id"] or r["key"])  # noqa: E731
    liked = [pick(r) for r in rows if r["status"] == "liked"]
    disliked = [pick(r) for r in rows if r["status"] == "disliked"]

    def s2_id(key):
        return key[3:] if key.startswith("s2:") else f"ArXiv:{key}"

    positives = list(dict.fromkeys(s2_id(k) for k in liked + library))[:100]
    negatives = list(dict.fromkeys(s2_id(k) for k in disliked))[:100]
    return positives, negatives


def refresh_recommendations(api_key=None, limit=60):
    positives, negatives = _positive_negative()
    if not positives:
        raise FeedError("no-seed")
    headers = {"x-api-key": api_key} if api_key else {}
    body = {"positivePaperIds": positives, "negativePaperIds": negatives}
    with _client(headers) as client:
        response = with_retry(lambda: client.post(S2_RECOMMEND, params={"fields": S2_FIELDS, "limit": limit}, json=body),
                              retry_on=(429,), delays=(2, 4, 8))
    if response.status_code == 429:
        raise FeedError("rate-limited")
    if response.status_code == 403 and api_key:
        raise FeedError("bad-key")
    response.raise_for_status()
    papers = response.json().get("recommendedPapers", [])
    with db.connect() as connection:
        connection.execute("UPDATE feed_items SET s2_rank = NULL")
        for rank, paper in enumerate(papers):
            arxiv_id = (paper.get("externalIds") or {}).get("ArXiv")
            key = arxiv_id or f"s2:{paper['paperId']}"
            connection.execute(
                """INSERT INTO feed_items (key, arxiv_id, title, authors, abstract, categories, announced,
                       s2_rank, link, fetched_at)
                   VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET s2_rank = excluded.s2_rank,
                       title = CASE WHEN feed_items.in_arxiv = 1 THEN feed_items.title ELSE excluded.title END,
                       authors = CASE WHEN feed_items.in_arxiv = 1 THEN feed_items.authors ELSE excluded.authors END,
                       abstract = CASE WHEN feed_items.in_arxiv = 1 THEN feed_items.abstract ELSE excluded.abstract END""",
                (key, arxiv_id, tex_text(paper.get("title")),
                 json.dumps([a.get("name", "") for a in paper.get("authors") or []], ensure_ascii=False),
                 tex_text(paper.get("abstract")), paper.get("publicationDate") or str(paper.get("year") or ""),
                 rank, f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else paper.get("url"), _now()),
            )
    try:  # vecteurs de sens des recommandations, pour les comparer au profil
        relevance.fetch_embeddings([relevance.s2_key((p.get("externalIds") or {}).get("ArXiv"), f"s2:{p['paperId']}")
                                    for p in papers], api_key)
    except Exception:
        pass
    db.save_settings({"feed.s2Refreshed": _now(), "feed.s2Seeds": [len(positives), len(negatives)]})
    return len(papers)


# ---------- Lecture du feed ----------

def list_items(view, announce_types=None, day=None):
    with db.connect() as connection:
        papers = connection.execute("SELECT id, arxiv_id, in_library FROM papers").fetchall()
        library = {r["arxiv_id"]: r["id"] for r in papers if r["in_library"]}
        cached = {r["arxiv_id"]: r["id"] for r in papers}
        status = {r["key"]: r["status"] for r in connection.execute("SELECT key, status FROM feed_status")}
        if view == "recommended":
            rows = connection.execute("SELECT * FROM feed_items WHERE s2_rank IS NOT NULL ORDER BY s2_rank").fetchall()
        else:
            # un seul jour d'annonce (le plus récent par défaut), plus les articles récents des auteurs suivis
            types = announce_types or ["new", "cross"]
            marks = ",".join("?" * len(types))
            day = day or connection.execute("SELECT MAX(announced) FROM feed_items WHERE in_arxiv = 1").fetchone()[0]
            rows = connection.execute(
                f"""SELECT * FROM feed_items WHERE (in_arxiv = 1 AND announce_type IN ({marks}) AND announced = ?)
                       OR (followed = 1 AND announced >= date('now', '-{FOLLOWED_DAYS} days'))
                       OR (citing IS NOT NULL AND cited_found >= date('now', '-{FOLLOWED_DAYS} days'))
                    ORDER BY CASE announce_type WHEN 'new' THEN 0 WHEN 'cross' THEN 1 ELSE 2 END, arxiv_id DESC""",
                [*types, day],
            ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["authors"] = json.loads(item["authors"] or "[]")
        item["categories"] = json.loads(item["categories"] or "[]")
        item["citing"] = json.loads(item.get("citing") or "[]")
        item["status"] = status.get(item["key"])
        item["libraryId"] = library.get(item["arxiv_id"])
        item["readId"] = cached.get(item["arxiv_id"])  # déjà téléchargé (lu), dans la bibliothèque ou non
        items.append(item)
    return relevance.score_items(items)


def link_arxiv(key, arxiv_id):
    """Recommandation sans version arXiv connue de Semantic Scholar : reliée à son identifiant arXiv."""
    if not key.startswith("s2:"):
        raise ValueError("only Semantic Scholar recommendations can be linked")
    with db.connect() as connection:
        connection.execute("UPDATE feed_items SET arxiv_id = ?, link = ? WHERE key = ?",
                           (arxiv_id, f"https://arxiv.org/abs/{arxiv_id}", key))


def get_item(key):
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM feed_items WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["authors"] = json.loads(item["authors"] or "[]")
    return item


def set_status(key, status):
    with db.connect() as connection:
        if status in ("liked", "disliked"):
            connection.execute(
                "INSERT INTO feed_status (key, status, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                (key, status),
            )
        else:
            connection.execute("DELETE FROM feed_status WHERE key = ?", (key,))


# ---------- Repérage d'articles dans un texte collé ----------

def lookup_ids(text, limit=200):
    """Identifiants arXiv trouvés dans un texte, avec titre et auteurs (API arXiv, par lots de 50)."""
    ids = extract_ids(text)[:limit]
    found = {}
    with _client() as client:
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            response = with_retry(lambda: client.get("https://export.arxiv.org/api/query",
                                                     params={"id_list": ",".join(batch), "max_results": len(batch)}))
            response.raise_for_status()
            for entry in ET.fromstring(response.text).findall(f"{ATOM}entry"):
                entry_id = entry.findtext(f"{ATOM}id") or ""
                match = re.search(r"abs/(.+?)(v\d+)?$", entry_id)
                title = tex_text(entry.findtext(f"{ATOM}title"))
                if not match or not title:
                    continue
                primary = entry.find(f"{ARXIV_NS}primary_category")
                found[match.group(1)] = {
                    "arxiv_id": match.group(1),
                    "title": title,
                    "authors": [" ".join((a.findtext(f"{ATOM}name") or "").split()) for a in entry.findall(f"{ATOM}author")],
                    "primary_category": primary.get("term") if primary is not None else "",
                    "published": (entry.findtext(f"{ATOM}published") or "")[:10],
                }
            if start + 50 < len(ids):
                time.sleep(3)
    with db.connect() as connection:
        library = {r["arxiv_id"]: r["id"] for r in connection.execute("SELECT id, arxiv_id FROM papers WHERE in_library = 1")}
    papers = [dict(found[i], libraryId=library.get(i)) for i in ids if i in found]
    missing = [i for i in ids if i not in found]
    return {"papers": papers, "missing": missing}
