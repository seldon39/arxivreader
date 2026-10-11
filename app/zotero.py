"""Synchronisation de la bibliothèque et des dossiers avec Zotero (API web, clé personnelle).

Dans Zotero, tout se passe dans une collection « arXivReader » : un article de la bibliothèque y est rangé,
et chaque dossier en est une sous-collection. Le reste de la bibliothèque Zotero n'est jamais modifié, et
aucun élément Zotero n'est supprimé ni mis à la corbeille : un article retiré n'est que sorti des collections
arXivReader (ses autres collections restent).

Dans les deux sens, par comparaison de trois états : arXivReader, Zotero, et ce qu'ils étaient à la
synchronisation précédente (gardé ici). Ce qui n'a changé que d'un côté est reporté de l'autre ; pour la place
d'un article (bibliothèque, tel dossier), deux changements ne peuvent pas se contredire ; pour le nom d'un
dossier renommé des deux côtés, arXivReader l'emporte. Un élément Zotero rangé dans « arXivReader » sans
identifiant arXiv est laissé de côté (compté) ; avec un identifiant, il est importé d'arXiv.
"""

import asyncio
import logging
import re
import threading
import time

import httpx
from starlette.concurrency import run_in_threadpool

from . import db, folders, library
from .arxiv import ArxivError, parse_arxiv_id, paper_key, with_retry
from .config import USER_AGENT

API = "https://api.zotero.org"
ROOT_NAME = "arXivReader"
PAGE = 100
WRITE = 50              # éléments écrits par requête (limite de Zotero)
LIBRARY = "library"     # place « dans la bibliothèque » (la collection arXivReader ou l'une de ses sous-collections)
SCHEMA = """
CREATE TABLE IF NOT EXISTS zotero_items (paper_id TEXT PRIMARY KEY, item_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS zotero_folders (folder_id INTEGER PRIMARY KEY, collection_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS zotero_places (paper_id TEXT NOT NULL, place TEXT NOT NULL, PRIMARY KEY (paper_id, place));
"""
EVERY = 600            # synchronisation régulière (s), en plus de celle qui suit un changement ici
SETTLE = 20            # délai après un changement (plusieurs rangements d'affilée : une seule synchronisation)
log = logging.getLogger("arxivreader.zotero")
stop = threading.Event()
_lock = threading.Lock()
_changed = 0.0  # dernière modification de la bibliothèque ou des dossiers (synchronisation prochaine)


class ZoteroError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


def touch():
    """La bibliothèque ou un dossier vient de changer : la boucle synchronisera bientôt."""
    global _changed
    _changed = time.time()


def last_change():
    return _changed


def _ensure():
    with db.connect() as connection:
        connection.executescript(SCHEMA)


# ---------- API web de Zotero ----------

class Client:
    def __init__(self, key, http=None):
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
        self.headers = {"Zotero-API-Key": key, "Zotero-API-Version": "3"}
        self.user = None

    def close(self):
        self.http.close()

    def call(self, method, path, json_body=None, version=None, **params):
        headers = dict(self.headers)
        if version is not None:
            headers["If-Unmodified-Since-Version"] = str(version)
        response = with_retry(lambda: self.http.request(method, f"{API}{path}", params=params or None, json=json_body, headers=headers),
                              retry_on=(429, 503), delays=(2, 5, 10))
        if response.status_code in (401, 403):
            raise ZoteroError("key", "Clé Zotero refusée (ou sans accès en écriture à la bibliothèque)")
        if response.status_code == 404:
            raise ZoteroError("missing")
        if response.status_code == 412:
            raise ZoteroError("conflict")
        if response.status_code >= 400:
            raise ZoteroError("server", f"Zotero : erreur {response.status_code}")
        return response

    def connect(self):
        """Utilisateur de la clé ; elle doit permettre de lire et d'écrire dans la bibliothèque."""
        data = self.call("GET", "/keys/current").json()
        access = (data.get("access") or {}).get("user") or {}
        if not access.get("library") or not access.get("write"):
            raise ZoteroError("key", "La clé Zotero doit donner accès à la bibliothèque, en écriture")
        self.user = data["userID"]
        return {"userID": data["userID"], "username": data.get("username") or ""}

    def all(self, path, **params):
        found, start = [], 0
        while True:
            response = self.call("GET", f"/users/{self.user}{path}", limit=PAGE, start=start, **params)
            page = response.json()
            found += page
            start += PAGE
            total = response.headers.get("Total-Results")
            # sans le total (inattendu), on continue tant que les pages sont pleines : jamais une liste tronquée
            if not page or (start >= int(total) if total is not None else len(page) < PAGE):
                return found

    def collection(self, key):
        try:
            return self.call("GET", f"/users/{self.user}/collections/{key}").json()
        except ZoteroError as error:
            if error.code == "missing":
                return None
            raise

    def write(self, path, objects):
        """Création ou mise à jour (avec `key` et `version`) par lots ; renvoie {indice: objet écrit} et les échecs."""
        written, failed = {}, {}
        for start in range(0, len(objects), WRITE):
            answer = self.call("POST", f"/users/{self.user}{path}", json_body=objects[start:start + WRITE]).json()
            for index, item in (answer.get("successful") or {}).items():
                written[start + int(index)] = item
            for index, problem in (answer.get("failed") or {}).items():
                failed[start + int(index)] = problem
        return written, failed


# ---------- éléments Zotero ↔ articles ----------

ARXIV_IN_TEXT = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv\s*[:.]\s*|10\.48550/arXiv\.)\s*([\w.\-]+/\d{7}|\d{4}\.\d{4,5})", re.I)


def arxiv_of(data):
    """Identifiant arXiv d'un élément Zotero (identifiant d'archive, adresse, DOI d'arXiv, champ « extra »…)."""
    for field in ("archiveID", "url", "DOI", "extra", "publicationTitle", "number", "reportNumber"):
        value = str(data.get(field) or "")
        found = ARXIV_IN_TEXT.search(value)
        if found:
            try:
                return parse_arxiv_id(found.group(1))[0]
            except ArxivError:
                continue
    return None


def _creators(authors):
    creators = []
    for name in authors or []:
        parts = name.split()
        if len(parts) > 1:
            creators.append({"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]})
        elif parts:
            creators.append({"creatorType": "author", "name": parts[0]})
    return creators


def new_item(paper, collections):
    """Élément Zotero (prépublication) d'un article de la bibliothèque."""
    arxiv_id = paper["arxiv_id"]
    extra = [f"arXiv: {arxiv_id}"] + ([f"Published: {paper['journal_ref']}"] if paper.get("journal_ref") else [])
    return {"itemType": "preprint", "title": paper.get("title") or arxiv_id, "creators": _creators(paper.get("authors")),
            "abstractNote": paper.get("abstract") or "", "repository": "arXiv", "archiveID": f"arXiv:{arxiv_id}",
            "url": f"https://arxiv.org/abs/{arxiv_id}", "date": (paper.get("published") or "")[:10],
            "DOI": f"10.48550/arXiv.{arxiv_id}", "extra": "\n".join(extra), "collections": sorted(collections), "tags": [], "relations": {}}


# ---------- états ----------

def _local():
    """Articles de la bibliothèque et leurs places : {id d'article: {"library", id de dossier…}}."""
    places = {p["id"]: {LIBRARY} for p in db.list_papers()}
    with db.connect() as connection:
        for row in connection.execute("SELECT folder_id, paper_id FROM folder_papers"):
            if row["paper_id"] in places:
                places[row["paper_id"]].add(str(row["folder_id"]))
    return places


def _saved():
    with db.connect() as connection:
        items = {r["paper_id"]: r["item_key"] for r in connection.execute("SELECT paper_id, item_key FROM zotero_items")}
        mapped = {r["folder_id"]: (r["collection_key"], r["name"]) for r in connection.execute("SELECT * FROM zotero_folders")}
        places = {}
        for r in connection.execute("SELECT paper_id, place FROM zotero_places"):
            places.setdefault(r["paper_id"], set()).add(r["place"])
    return items, mapped, places


def _forget_all():
    with db.connect() as connection:
        connection.executescript("DELETE FROM zotero_items; DELETE FROM zotero_folders; DELETE FROM zotero_places;")


def _merge(local, remote, before):
    """Place d'un article après fusion : celle d'arXivReader si elle a changé depuis la dernière fois, sinon celle
    de Zotero (pour une place, deux changements vont forcément dans le même sens)."""
    return local if local != before else remote


# ---------- synchronisation ----------

def sync(key, http=None, importer=None, careful=False):
    """Synchronise ; renvoie le résumé (ajouts, retraits de chaque côté, éléments sans identifiant arXiv).
    `careful` (synchronisation automatique) : des retraits massifs venus de Zotero (réponse incomplète ?) ne sont
    pas appliqués ici ; « Synchroniser maintenant » les applique."""
    if not _lock.acquire(blocking=False):
        raise ZoteroError("busy", "Synchronisation déjà en cours")
    try:
        _ensure()
        client = Client(key, http)
        try:
            return _sync(client, importer or (lambda arxiv_id: library.import_paper(arxiv_id)), careful)
        finally:
            if http is None:
                client.close()
    finally:
        _lock.release()


def _massive(count, total):
    return count > max(5, total // 2)


def _sync(client, importer, careful):
    settings = db.get_settings()
    who = client.connect()
    if settings.get("zoteroUser") not in (None, who["userID"]):
        _forget_all()  # autre compte Zotero : la liaison repart de zéro
    report = {"user": who["username"], "toZotero": 0, "fromZotero": 0, "removedZotero": 0, "removedHere": 0, "folders": 0, "skipped": 0}

    # collection « arXivReader » (retrouvée par son nom, sinon créée) ; disparue de Zotero : la liaison repart de zéro
    root = settings.get("zoteroRoot") if settings.get("zoteroUser") == who["userID"] else None
    if root and client.collection(root) is None:
        _forget_all()
        root = None
    if not root:
        top = client.all("/collections/top")
        root = next((c["key"] for c in top if c["data"]["name"] == ROOT_NAME and not c["data"].get("parentCollection")), None)
        if not root:
            written, failed = client.write("/collections", [{"name": ROOT_NAME, "parentCollection": False}])
            if 0 not in written:
                raise ZoteroError("server", f"Collection {ROOT_NAME} impossible à créer : {failed}")
            root = written[0]["key"]
        db.save_settings({"zoteroRoot": root, "zoteroUser": who["userID"]})

    saved_items, mapped, before = _saved()

    # ---- dossiers ↔ sous-collections ----
    subs = {c["key"]: c for c in client.all(f"/collections/{root}/collections")}
    local_folders = {f["id"]: f for f in folders.list_folders()}
    by_collection = {key: folder_id for folder_id, (key, _) in mapped.items()}
    links = {}  # id de dossier → clé de sous-collection, après cette étape
    vanished = sum(1 for fid, (key, _) in mapped.items() if fid in local_folders and key not in subs)
    hold_folders = careful and vanished > 1 and vanished * 2 > len(mapped)  # presque toutes disparues : on attend
    held = set()  # sous-collections disparues, gardées pour l'instant (synchronisation automatique)
    for folder_id, (key, name) in mapped.items():
        here, there = local_folders.get(folder_id), subs.get(key)
        if here and there:
            links[folder_id] = key
            remote_name = there["data"]["name"]
            if here["name"] != name and here["name"] != remote_name:  # renommé ici (gagne aussi s'il l'a été là-bas)
                client.call("PATCH", f"/users/{client.user}/collections/{key}", {"name": here["name"]}, version=there["version"])
                report["folders"] += 1
            elif remote_name != name and remote_name != here["name"]:  # renommé dans Zotero seulement
                try:
                    folders.rename(folder_id, remote_name)
                    report["folders"] += 1
                except folders.FolderError:
                    pass  # nom déjà pris ici : on garde le nôtre (reporté là-bas au prochain renommage)
        elif here and not there:  # sous-collection supprimée dans Zotero : le dossier aussi (pas ses articles)
            if hold_folders:
                links[folder_id] = key  # gardé, comme lié (rien ne change ici tant que ce n'est pas confirmé)
                held.add(key)
                report["held"] = report.get("held", 0) + 1
                continue
            folders.delete(folder_id)
            report["folders"] += 1
        elif there and not here:  # dossier supprimé ici : la sous-collection aussi (pas ses éléments)
            client.call("DELETE", f"/users/{client.user}/collections/{key}", version=there["version"])
            report["folders"] += 1
    for folder_id, folder in local_folders.items():
        if folder_id in mapped:
            continue
        same = next((k for k, c in subs.items() if k not in by_collection and c["data"]["name"].casefold() == folder["name"].casefold()
                     and k not in links.values()), None)
        if same is None:
            written, _ = client.write("/collections", [{"name": folder["name"], "parentCollection": root}])
            if 0 not in written:
                continue
            same = written[0]["key"]
            report["folders"] += 1
        links[folder_id] = same
    for key, collection in subs.items():
        if key in by_collection or key in links.values():
            continue
        name = collection["data"]["name"]
        try:
            created = folders.create(name)
        except folders.FolderError:  # même nom qu'un dossier déjà lié, ou nom invalide ici
            continue
        links[created["id"]] = key
        report["folders"] += 1
    names = {f["id"]: f["name"] for f in folders.list_folders()}
    with db.connect() as connection:
        connection.execute("DELETE FROM zotero_folders")
        connection.executemany("INSERT INTO zotero_folders (folder_id, collection_key, name) VALUES (?, ?, ?)",
                               [(fid, key, names[fid]) for fid, key in links.items() if fid in names])
    place_of = {key: str(fid) for fid, key in links.items()}  # sous-collection → place
    ours = {root, *links.values()}

    # ---- éléments des collections arXivReader ----
    remote = {}
    held_places = {str(fid) for fid, key in links.items() if key in held}
    for collection in [root, *(k for k in links.values() if k not in held)]:
        for item in client.all(f"/collections/{collection}/items/top"):
            remote[item["key"]] = item
    remote_places, remote_arxiv = {}, {}
    for key, item in remote.items():
        mine = [c for c in item["data"].get("collections", []) if c in ours]
        if not mine:
            continue
        remote_places[key] = {LIBRARY} | {place_of[c] for c in mine if c in place_of}
        remote_arxiv[key] = arxiv_of(item["data"])

    local = _local()
    papers = {p["id"]: p for p in db.list_all_papers()}
    items = dict(saved_items)  # id d'article → clé d'élément
    by_item = {key: pid for pid, key in items.items()}
    # éléments de Zotero pas encore liés : à l'article du même identifiant arXiv, sinon importé d'arXiv
    for key, arxiv_id in remote_arxiv.items():
        if key in by_item:
            continue
        if not arxiv_id:
            report["skipped"] += 1
            continue
        pid = paper_key(arxiv_id)
        if pid in items:  # article déjà lié à un autre élément (doublon dans Zotero) : laissé
            continue
        items[pid], by_item[key] = key, pid
    # articles d'ici pas encore liés : à un élément Zotero du même identifiant (même hors arXivReader : pas de
    # doublon dans Zotero), sinon créé. Peu d'articles : une recherche chacun ; beaucoup : toute la bibliothèque Zotero
    to_create = []
    unlinked = [pid for pid in local if pid not in items]
    elsewhere = {}  # identifiant arXiv → élément Zotero hors des collections arXivReader
    if len(unlinked) > 20:
        for item in client.all("/items/top"):
            arxiv_id = arxiv_of(item["data"])
            if arxiv_id and item["key"] not in by_item:
                elsewhere.setdefault(arxiv_id, item)
    for pid in unlinked:
        arxiv_id = papers[pid]["arxiv_id"]
        found = next((k for k, a in remote_arxiv.items() if a == arxiv_id and k not in by_item), None)
        if found is None:
            if len(unlinked) > 20:
                match = elsewhere.get(arxiv_id)
            else:
                matches = client.call("GET", f"/users/{client.user}/items/top", q=arxiv_id, qmode="everything", limit=25).json()
                match = next((m for m in matches if arxiv_of(m["data"]) == arxiv_id and m["key"] not in by_item), None)
            if match and match["key"] not in by_item:
                found = match["key"]
                remote[found], remote_places[found] = match, set()
        if found:
            items[pid], by_item[found] = found, pid
        else:
            to_create.append(pid)

    # ---- fusion des places, article par article ----
    plan = []
    for pid, key in list(items.items()):
        if key not in remote and key not in remote_places:
            # élément lié mais hors des collections arXivReader : on le relit (retiré de Zotero, ou corbeille)
            try:
                remote[key] = client.call("GET", f"/users/{client.user}/items/{key}").json()
            except ZoteroError as error:
                if error.code != "missing":
                    raise
                items.pop(pid)  # élément supprimé de Zotero : la liaison est oubliée (l'article reste ici)
                continue
            data = remote[key]["data"]
            mine = [] if data.get("deleted") else [c for c in data.get("collections", []) if c in ours]  # corbeille : retiré
            remote_places[key] = {LIBRARY} | {place_of[c] for c in mine if c in place_of} if mine else set()
        here, then = local.get(pid, set()), before.get(pid, set())
        there = remote_places.get(key, set()) | (then & held_places)  # dossier gardé en attente : rien de changé là-bas
        result = {place for place in here | there | then if _merge(place in here, place in there, place in then)}
        if result - {LIBRARY}:
            result.add(LIBRARY)  # dans un dossier : dans la bibliothèque
        plan.append((pid, key, here, there, then, result))
    # synchronisation automatique : beaucoup d'articles retirés d'un coup par Zotero, ou des sous-collections
    # disparues (réponse incomplète ?) : rien n'est changé, d'un côté ni de l'autre, avant « Synchroniser maintenant »
    leaving = [entry for entry in plan if LIBRARY in entry[2] and LIBRARY not in entry[5]]
    if careful and (held or _massive(len(leaving), len(local))):
        report["held"] = report.get("held", 0) + len(leaving)
        skip = {entry[0] for entry in plan}
        to_create = []
    else:
        skip = set()

    updates, results = [], {}
    for pid, key, here, there, then, result in plan:
        if pid in skip:
            if then:
                results[pid] = then  # rien ne change ; la prochaine synchronisation manuelle tranchera
            continue
        results[pid] = result
        # ici
        if LIBRARY in result and LIBRARY not in here:
            if pid not in papers:
                try:
                    papers[pid] = importer(remote_arxiv.get(key) or arxiv_of(remote[key]["data"]))
                except Exception:
                    results.pop(pid)  # arXiv injoignable : au prochain passage
                    continue
            else:
                library.set_in_library(pid, True)
            report["fromZotero"] += 1
        elif LIBRARY not in result and LIBRARY in here:
            library.set_in_library(pid, False)
            report["removedHere"] += 1
        for folder_id in {p for p in result | here if p != LIBRARY}:
            if folder_id in place_of.values() and (folder_id in result) != (folder_id in here):
                folders.set_member(int(folder_id), pid, folder_id in result)
        # dans Zotero : collections arXivReader voulues (sauf celles en attente), les autres gardées
        if result != there and key in remote:
            data = remote[key]["data"]
            others = [c for c in data.get("collections", []) if c not in ours]
            wanted = ([root] if LIBRARY in result else []) + [links[int(p)] for p in result
                                                              if p != LIBRARY and int(p) in links and links[int(p)] not in held]
            if set(others + wanted) != set(data.get("collections", [])):
                updates.append({"key": key, "version": remote[key]["version"], "collections": sorted(set(others + wanted))})
            if LIBRARY in result and LIBRARY not in there:
                report["toZotero"] += 1
            elif LIBRARY not in result and LIBRARY in there:
                report["removedZotero"] += 1
    if updates:
        client.write("/items", updates)  # un conflit (élément changé entre-temps) se règle au prochain passage
    if to_create:
        objects = [new_item(papers[pid], [root] + [links[int(p)] for p in local[pid] if p != LIBRARY and int(p) in links and links[int(p)] not in held])
                   for pid in to_create]
        written, _ = client.write("/items", objects)
        for index, item in written.items():
            pid = to_create[index]
            items[pid] = item["key"]
            results[pid] = local[pid]
            report["toZotero"] += 1

    with db.connect() as connection:
        connection.execute("DELETE FROM zotero_items")
        connection.executemany("INSERT INTO zotero_items (paper_id, item_key) VALUES (?, ?)", list(items.items()))
        connection.execute("DELETE FROM zotero_places")
        connection.executemany("INSERT INTO zotero_places (paper_id, place) VALUES (?, ?)",
                               [(pid, place) for pid, places in results.items() for place in places])
    db.save_settings({"zoteroLastSync": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "zoteroLastError": "",
                      "zoteroUserName": who["username"], "zoteroReport": report})
    return report


def status():
    settings = db.get_settings()
    return {"connected": bool(settings.get("zoteroApiKey")), "user": settings.get("zoteroUserName") or "",
            "lastSync": settings.get("zoteroLastSync") or "", "lastError": settings.get("zoteroLastError") or "",
            "report": settings.get("zoteroReport") or None, "running": _lock.locked()}


def record_error(error):
    db.save_settings({"zoteroLastError": str(error)})


async def loop():
    """Synchronisation automatique (clé donnée, réglage « automatique » non décoché) : peu après un changement
    de la bibliothèque ou des dossiers, et toutes les dix minutes (changements faits dans Zotero)."""
    await asyncio.sleep(30)
    done = 0.0
    while not stop.is_set():
        settings = db.get_settings()
        key = settings.get("zoteroApiKey")
        now = time.time()
        if key and settings.get("zoteroAutoSync") is not False and (now - done > EVERY or (_changed > done and now - _changed > SETTLE)):
            done = now
            try:
                await run_in_threadpool(lambda: sync(key, careful=True))
            except ZoteroError as error:
                if error.code != "busy":
                    record_error(error)
            except Exception as error:  # réseau…
                log.warning("synchronisation Zotero impossible : %s", error)
                record_error(f"Zotero injoignable ({type(error).__name__})")
        await asyncio.sleep(5)

