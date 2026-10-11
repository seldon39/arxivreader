"""Dossiers de la bibliothèque. Un article peut être rangé dans plusieurs dossiers ; supprimer un dossier
ne touche pas à ses articles."""

import unicodedata

from . import db

MAX_NAME = 80


class FolderError(ValueError):
    pass


def clean_name(name):
    name = " ".join(str(name or "").split())
    if not name or len(name) > MAX_NAME or not name.isprintable():
        raise FolderError("Nom de dossier invalide")
    return name


def list_folders():
    """[{id, name, papers: [id d'article…]}], par ordre alphabétique."""
    with db.connect() as connection:
        folders = connection.execute("SELECT id, name FROM folders").fetchall()
        members = connection.execute("SELECT folder_id, paper_id FROM folder_papers ORDER BY added_at").fetchall()
    papers = {}
    for row in members:
        papers.setdefault(row["folder_id"], []).append(row["paper_id"])
    # ordre alphabétique sans accents ni majuscules (« À relire » avant « Bergman »)
    key = lambda f: unicodedata.normalize("NFD", f["name"]).encode("ascii", "ignore").decode().casefold()
    return [{"id": f["id"], "name": f["name"], "papers": papers.get(f["id"], [])} for f in sorted(folders, key=key)]


def _taken(connection, name, exclude=None):
    row = connection.execute("SELECT id FROM folders WHERE name = ? COLLATE NOCASE AND id IS NOT ?", (name, exclude)).fetchone()
    return row is not None


def create(name, papers=()):
    """Nouveau dossier (éventuellement déjà rempli : annulation d'une suppression)."""
    name = clean_name(name)
    with db.connect() as connection:
        if _taken(connection, name):
            raise FolderError("Un dossier porte déjà ce nom")
        folder_id = connection.execute("INSERT INTO folders (name) VALUES (?)", (name,)).lastrowid
        connection.executemany("INSERT OR IGNORE INTO folder_papers (folder_id, paper_id) VALUES (?, ?)",
                               [(folder_id, paper_id) for paper_id in papers])
    return get(folder_id)


def get(folder_id):
    return next((f for f in list_folders() if f["id"] == folder_id), None)


def rename(folder_id, name):
    name = clean_name(name)
    with db.connect() as connection:
        if _taken(connection, name, exclude=folder_id):
            raise FolderError("Un dossier porte déjà ce nom")
        connection.execute("UPDATE folders SET name = ? WHERE id = ?", (name, folder_id))
    return get(folder_id)


def delete(folder_id):
    """Supprime le dossier (pas ses articles) ; renvoie ce qu'il contenait, pour pouvoir l'annuler."""
    folder = get(folder_id)
    with db.connect() as connection:
        connection.execute("DELETE FROM folder_papers WHERE folder_id = ?", (folder_id,))
        connection.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    return folder


def set_member(folder_id, paper_id, member):
    with db.connect() as connection:
        if member:
            connection.execute("INSERT OR IGNORE INTO folder_papers (folder_id, paper_id) VALUES (?, ?)", (folder_id, paper_id))
        else:
            connection.execute("DELETE FROM folder_papers WHERE folder_id = ? AND paper_id = ?", (folder_id, paper_id))
    return get(folder_id)


def forget_paper(paper_id):
    """Article effacé du cache : retiré de tous les dossiers."""
    with db.connect() as connection:
        connection.execute("DELETE FROM folder_papers WHERE paper_id = ?", (paper_id,))
