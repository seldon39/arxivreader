"""Cache du feed : élagage au lancement, et vecteurs de sens gardés en binaire.

Rien ne supprimait les articles du feed ni leurs vecteurs : environ 35 Mo de plus par mois, pour classer des articles
qu'on n'ouvrira plus. Sont gardés : ceux de la bibliothèque ou lus, notés 👍 ou 👎, de la dernière recommandation, ceux
qui citent vos articles, et les vecteurs du profil et de ses références. Un jour d'annonce élagué est simplement
redemandé à arXiv si l'on y revient.
"""

import json
from array import array
from datetime import date, timedelta

from . import db

KEEP_DAYS = 60
VACUUM_FROM = 1000  # lignes supprimées à partir desquelles le fichier est compacté (VACUUM)


# ---------- Vecteurs : float32 binaire (environ 3 Ko au lieu de 11 Ko de JSON) ----------

def pack(vector):
    return array("f", vector).tobytes() if vector else None


def unpack(value):
    """Vecteur d'une ligne de la base : binaire, ou ancien texte JSON (relu tel quel, jamais réécrit en bloc)."""
    if not value:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        vector = array("f")
        vector.frombytes(bytes(value))
        return vector.tolist()
    return json.loads(value)


# ---------- Élagage ----------

def prune(days=KEEP_DAYS):
    """Articles du feed et vecteurs plus vieux que `days` jours, hors ceux qu'on garde ; renvoie (articles, vecteurs)
    supprimés."""
    from . import feed, relevance
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with feed._day_lock:  # pas pendant le chargement d'un jour
        with db.connect() as connection:
            papers = connection.execute("SELECT arxiv_id FROM papers WHERE arxiv_id IS NOT NULL").fetchall()
            connection.execute(
                """DELETE FROM feed_items WHERE fetched_at < ? AND s2_rank IS NULL AND citing IS NULL
                   AND COALESCE(announced, '') < ?
                   AND key NOT IN (SELECT key FROM feed_status)
                   AND (arxiv_id IS NULL OR arxiv_id NOT IN (SELECT arxiv_id FROM papers WHERE arxiv_id IS NOT NULL))""",
                (cutoff, cutoff))  # aussi `announced` : `fetched_at` ne change pas quand un article est ré-annoncé (nouvelle version)
            removed_items = connection.total_changes
            left = {r["announced"] for r in connection.execute("SELECT DISTINCT announced FROM feed_items WHERE in_arxiv = 1")}
            remaining = connection.execute("SELECT key, arxiv_id FROM feed_items").fetchall()
        _forget_days(cutoff, left)
        keep = {relevance.s2_key(r["arxiv_id"]) for r in papers}
        keep |= {relevance.s2_key(r["arxiv_id"], r["key"]) for r in remaining}
        positives, negatives, _, _ = relevance.profile_documents()
        keep |= {d["s2"] for d in positives + negatives if d.get("s2")}
        with db.connect() as connection:
            old = [r["key"] for r in connection.execute("SELECT key FROM embeddings WHERE fetched_at < ?", (cutoff + " 00:00:00",))]
            doomed = [k for k in old if k not in keep]
            for start in range(0, len(doomed), 500):
                batch = doomed[start:start + 500]
                connection.execute(f"DELETE FROM embeddings WHERE key IN ({','.join('?' * len(batch))})", batch)
    if removed_items + len(doomed) >= VACUUM_FROM:
        connection = db.sqlite3.connect(db.DB_PATH)
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()
    return removed_items, len(doomed)


def _forget_days(cutoff, left):
    """Jours élagués oubliés des réglages (feed.days) : sinon la page les croirait chargés et les montrerait vides."""
    settings = db.get_settings()
    days = settings.get("feed.days") or {}
    gone = [d for d in days if d < cutoff and d not in left]
    if not gone:
        return
    changes = {"feed.days": {d: n for d, n in days.items() if d not in gone}}
    partial = settings.get("feed.partialDays") or []
    changes["feed.partialDays"] = [d for d in partial if d not in gone]
    db.save_settings(changes)
