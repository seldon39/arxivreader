"""Pertinence d'un article pour le profil de recherche.

Deux mesures de proximité avec les textes du profil (articles, livres, articles de la bibliothèque,
articles marqués 👍) :
- locale : vocabulaire commun (TF-IDF sur titres et résumés, mots et paires de mots) ;
- sémantique : vecteurs SPECTER de Semantic Scholar (API publique, sans compte), mis en cache.
S'y ajoutent les auteurs suivis et les mots-clés ; les articles proches de ceux marqués 👎 reculent.
"""

import json
import math
import re
import unicodedata
from collections import Counter

import httpx

from . import db, s2
from .arxiv import with_retry
from .config import PAPERS

S2_BATCH = f"{s2.GRAPH}/paper/batch"
STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been before being below between both but by
can could did do does doing down during each few for from further had has have having here how i if in into is it its
itself just more most no nor not now of off on once only or other our out over own same she should so some such than that
the their them then there these they this those through to too under until up very was we were what when where which while
who whom why will with would you your paper prove show study give obtain result results new also case cases general
using use used via well one two first second let thus hence give given main known recent approach consider provide
""".split())


# ---------- Texte ----------

def _ascii(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _stem(word):
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokens(text):
    text = _ascii(text or "").lower().replace("\\", " ")
    words = [_stem(w) for w in re.findall(r"[a-z][a-z\-]{2,}", text)]
    words = [w.strip("-") for w in words if w.strip("-") and w not in STOPWORDS]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


def tfidf_vectors(texts):
    counts = [Counter(tokens(t)) for t in texts]
    documents = Counter(term for c in counts for term in c)
    n = len(texts)
    vectors = []
    for c in counts:
        vector = {term: (1 + math.log(k)) * (math.log((n + 1) / (documents[term] + 1)) + 1) for term, k in c.items()}
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
        vectors.append({term: v / norm for term, v in vector.items()})
    return vectors


def cosine_sparse(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def _unit(vector):
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else None


def author_key(name):
    """(nom, initiale du prénom) en ASCII minuscule : « G. Marinescu » ≈ « George Marinescu »."""
    parts = [p for p in re.split(r"[\s.]+", _ascii(name).lower()) if p]
    if not parts:
        return None
    return (parts[-1], parts[0][0] if len(parts) > 1 else "")


def _plain(name):
    return " ".join(re.sub(r"[^a-z\s]", " ", _ascii(name).lower()).split())


def same_person(name, other):
    """Même auteur : même nom complet, ou mêmes nom et initiale quand l'un des deux n'a que l'initiale du prénom
    (« A. Martin » et « Alice Martin », mais pas « Albert Martin » ; « X. Ma » et « Xiaonan Ma », pas « Xin Ma »)."""
    a, b = _plain(name).split(), _plain(other).split()
    if a == b:
        return True
    if not a or not b or author_key(name) != author_key(other):
        return False
    if len(a) > 1 and len(b) > 1 and a[0] == b[0]:
        return True  # même prénom et même nom, une initiale au milieu en plus ou en moins (« George M. Marinescu »)
    first = lambda words: words[0] if len(words) > 1 else ""  # noqa: E731
    return len(first(a)) <= 1 or len(first(b)) <= 1


CATEGORY_ALIASES = {"math-ph": "math.MP", "math.MP": "math-ph"}  # deux noms d'une même catégorie


def follows(author, names, categories=(), key=None):
    """Article de cet auteur suivi (élément du profil : label, only, excluded) ? Même personne d'après le nom (pas un
    homonyme de même initiale), article pas écarté à la main (« pas cette personne »), et dans les catégories
    gardées pour lui s'il y en a (homonyme d'un autre sous-domaine)."""
    if not any(same_person(name, author["label"]) for name in names or []):
        return False
    if key and key in (author.get("excluded") or []):
        return False
    only = set(author.get("only") or [])
    only |= {CATEGORY_ALIASES[c] for c in only if c in CATEGORY_ALIASES}
    return not only or bool(only & set(categories or []))


# ---------- Vecteurs SPECTER (Semantic Scholar) ----------

# Identifiant arXiv nouveau (2606.24503) ou ancien (math/0601001, math.DG/0601001)
ARXIV_REF = re.compile(r"\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Z]{2})?/\d{7}")


def s2_key(arxiv_id=None, ref=None):
    if ref and ref.startswith("s2:"):  # identifiant Semantic Scholar, même si l'article a été relié à arXiv
        return ref[3:]
    if arxiv_id:
        return f"ArXiv:{arxiv_id}"
    if ref:
        if ref.startswith("s2:"):
            return ref[3:]
        if ref.startswith("10."):
            return f"DOI:{ref}"
        if ARXIV_REF.fullmatch(ref):
            return f"ArXiv:{ref}"
    return None


def cached_embeddings(keys):
    keys = [k for k in keys if k]
    if not keys:
        return {}
    with db.connect() as connection:
        rows = connection.execute(
            f"SELECT key, vector FROM embeddings WHERE key IN ({','.join('?' * len(keys))})", keys
        ).fetchall()
    return {r["key"]: json.loads(r["vector"]) if r["vector"] else None for r in rows}


def fetch_embeddings(keys, api_key=None, timeout=60):
    """Demande à Semantic Scholar les vecteurs manquants (par lots de 500) ; inconnus mémorisés comme NULL
    et redemandés après deux jours (les nouveaux articles y arrivent avec un peu de retard)."""
    keys = list(dict.fromkeys(k for k in keys if k))
    with db.connect() as connection:
        known = {
            r["key"] for r in connection.execute(
                "SELECT key FROM embeddings WHERE vector IS NOT NULL OR fetched_at > datetime('now', '-2 days')"
            )
        }
    missing = [k for k in keys if k not in known]
    headers = s2.headers(api_key)
    with httpx.Client(headers=headers, timeout=timeout) as client:
        for start in range(0, len(missing), 500):
            batch = missing[start:start + 500]
            response = with_retry(s2.paced(lambda: client.post(S2_BATCH, params={"fields": "embedding.specter_v2"}, json={"ids": batch}), api_key),
                                  retry_on=(429,), delays=(2, 4, 8))
            if response.status_code != 200:
                return False
            with db.connect() as connection:
                for key, paper in zip(batch, response.json()):
                    vector = ((paper or {}).get("embedding") or {}).get("vector")
                    connection.execute(
                        "INSERT INTO embeddings (key, vector, fetched_at) VALUES (?, ?, datetime('now')) "
                        "ON CONFLICT(key) DO UPDATE SET vector = excluded.vector, fetched_at = excluded.fetched_at",
                        (key, json.dumps(vector) if vector else None),
                    )
    return True


def library_relevance(api_key=None):
    """Pertinence pour le profil des articles de la bibliothèque (tri d'un dossier) : calculée sur toute la
    bibliothèque à la fois, car la proximité de sens est relative au lot comparé."""
    items = [{"key": p["id"], "arxiv_id": p["arxiv_id"], "title": p["title"] or "", "abstract": p["abstract"] or "",
              "authors": p["authors"]} for p in db.list_papers()]
    try:
        fetch_embeddings([s2_key(i["arxiv_id"]) for i in items], api_key, timeout=15)  # le tri attend : délai court
    except Exception:
        pass  # sans réseau : vecteurs déjà connus et vocabulaire seulement
    score_items(items, skip_self=True)
    return {i["key"]: i["score"] for i in items}


# ---------- Profil ----------

def profile_documents():
    """Textes positifs (profil, bibliothèque, 👍) et négatifs (👎) servant de référence."""
    with db.connect() as connection:
        profile = [dict(r) for r in connection.execute("SELECT * FROM profile_items")]
        rated = [dict(r) for r in connection.execute(
            "SELECT s.status, f.key, f.arxiv_id, f.title, f.abstract FROM feed_status s JOIN feed_items f ON f.key = s.key")]
    positives, seen = [], set()
    for item in profile:
        if item["kind"] not in ("mine", "paper", "book"):
            continue
        arxiv = item["ref"] if item["ref"] and ARXIV_REF.fullmatch(item["ref"]) else None
        positives.append({"label": item["label"], "text": f"{item['label']}. {item['text'] or ''}",
                          "s2": s2_key(arxiv, item["ref"]), "weight": 1.0})
        if arxiv:
            seen.add(arxiv)
    # La bibliothèque seule ne compte pas : lire un article n'est pas s'y intéresser durablement.
    # Seul le profil (pré-rempli, puis corrigé) et les 👍 servent de référence.
    # Références de vos propres articles (bibliographie lue à l'import) : elles décrivent votre domaine
    for item in profile:
        if item["kind"] != "mine" or not item["ref"]:
            continue
        path = PAPERS / item["ref"].replace("/", "_") / "structure.json"
        if not path.exists():
            continue
        for entry in json.loads(path.read_text(encoding="utf-8")).get("bibliography", []):
            key = s2_key(entry["arxiv"][0]) if entry.get("arxiv") else (f"DOI:{entry['doi']}" if entry.get("doi") else None)
            if key:
                positives.append({"label": entry.get("title") or entry["text"][:120], "text": entry["text"], "s2": key,
                                  "weight": 0.8, "reference": True})
    negatives = []
    for item in rated:
        document = {"label": item["title"], "text": f"{item['title']}. {item['abstract'] or ''}",
                    "s2": s2_key(item["arxiv_id"], item["key"]), "weight": 1.0}
        if item["status"] == "liked" and item["arxiv_id"] not in seen:
            positives.append(dict(document, via="liked"))
        elif item["status"] == "disliked":
            negatives.append(document)
    authors = [followed_author(i) for i in profile if i["kind"] == "author" and author_key(i["label"])]
    keywords = [i["label"] for i in profile if i["kind"] == "keyword"]
    return positives, negatives, authors, keywords


def followed_author(row):
    """Auteur suivi (ligne du profil) : nom, catégories gardées, articles écartés (« pas cette personne »)."""
    return {"id": row.get("id"), "label": row["label"], "only": json.loads(row.get("only_categories") or "[]"),
            "excluded": json.loads(row.get("not_papers") or "[]")}


# ---------- Score ----------

def _scale(value, low, high):
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _robust_z(values):
    """Écarts à la médiane, en unités d'écart absolu médian : la proximité SPECTER entre articles de
    mathématiques est toujours élevée (0,8 à 0,9) ; seul l'écart au lot du jour est parlant."""
    ordered = sorted(values)
    median = ordered[len(ordered) // 2]
    spread = sorted(abs(v - median) for v in values)[len(values) // 2] * 1.4826 or 0.01
    return [(v - median) / spread for v in values]


def score_items(items, skip_self=False):
    """Ajoute à chaque article « relevance » (0 à 1) et « reasons » (pourquoi il est mis en avant).

    Pour chaque texte de référence, on combine la proximité de vocabulaire (absolue) et la proximité
    de sens (relative au lot d'articles affiché) ; on garde la meilleure référence. « score » : la même
    valeur sans plafond, pour départager des articles tous très pertinents (tri d'un dossier).
    `skip_self` : un article n'est pas comparé à lui-même s'il figure dans le profil (bibliothèque).
    """
    if not items:
        return items
    positives, negatives, authors, keywords = profile_documents()
    references = positives + negatives
    vectors = tfidf_vectors([f"{i['title']}. {i.get('abstract') or ''}" for i in items] + [d["text"] for d in references])
    item_vectors, ref_vectors = vectors[:len(items)], vectors[len(items):]
    keys = [s2_key(i.get("arxiv_id"), i.get("key")) for i in items]
    # vecteurs de sens normalisés une fois : la proximité devient un simple produit scalaire
    embeddings = {k: _unit(v) for k, v in cached_embeddings(keys + [d["s2"] for d in references]).items() if v}
    known = [embeddings.get(k) for k in keys]

    # proximité de chaque article à chaque référence : [référence][article]
    def closeness(offset, doc):
        local = [_scale(cosine_sparse(v, ref_vectors[offset]), 0.04, 0.25) for v in item_vectors]
        other = embeddings.get(doc["s2"])
        sims = [sum(x * y for x, y in zip(e, other)) if e and other else None for e in known]
        present = [s for s in sims if s is not None]
        if len(present) < 8:
            return local
        z = iter(_robust_z(present))
        semantic = [_scale(next(z), 1.0, 3.5) if s is not None else None for s in sims]
        # un livre connu seulement par son titre a un vecteur peu informatif
        weight = 0.7 if len(doc["text"]) > len(doc["label"]) + 40 else 0.4
        return [max(l, weight * s + (1 - weight) * l) if s is not None else l for l, s in zip(local, semantic)]

    positive_rows = [closeness(j, doc) for j, doc in enumerate(positives)]
    negative_rows = [closeness(len(positives) + j, doc) for j, doc in enumerate(negatives)]

    for index, item in enumerate(items):
        reasons = []
        positive, best = 0.0, None
        for row, doc in zip(positive_rows, positives):
            if skip_self and doc["s2"] and doc["s2"] == keys[index]:
                continue
            value = row[index] * doc.get("weight", 1.0)
            if value > positive:
                positive, best = value, doc
        negative = max((row[index] for row in negative_rows), default=0.0)
        score = positive - 0.6 * max(0.0, negative - positive)
        if positive >= 0.2 and best:
            reasons.append({"kind": "similar", "label": best["label"], "value": round(positive, 2),
                            "via": "reference" if best.get("reference") else best.get("via", "profile")})

        # auteur suivi : même personne (pas seulement mêmes nom et initiale : Xin Ma n'est pas Xiaonan Ma), pas un
        # homonyme écarté à la main
        followed = [a for a in authors if follows(a, item.get("authors"), item.get("categories"), item.get("arxiv_id") or item.get("key"))]
        if followed:
            score += 0.6
            reasons.insert(0, {"kind": "author", "label": ", ".join(a["label"] for a in followed),
                               "authors": [{"id": a["id"], "label": a["label"]} for a in followed]})
        title = _ascii(item["title"]).lower()
        abstract = _ascii(item.get("abstract") or "").lower()
        bonus = 0.0
        for keyword in keywords:
            needle = _ascii(keyword).lower()
            if needle and needle in title:
                bonus += 0.35
                reasons.append({"kind": "keyword", "label": keyword})
            elif needle and needle in abstract:
                bonus += 0.15
                reasons.append({"kind": "keyword", "label": keyword})
        score += min(bonus, 0.6)
        if item.get("status") == "liked":
            score = max(score, 0.9)
        item["relevance"] = round(max(0.0, min(1.0, score)), 3)
        item["score"] = round(score, 4)
        item["reasons"] = reasons
    return items
