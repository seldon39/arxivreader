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
CREATE TABLE IF NOT EXISTS zotero_index (item_key TEXT PRIMARY KEY, arxiv_id TEXT NOT NULL);
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
        backoff = str(response.headers.get("Backoff") or "")
        if backoff.isdigit():
            time.sleep(min(60, int(backoff)))  # Zotero demande de ralentir
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
        return self.listing(path, **params)[0]

    def listing(self, path, **params):
        """Toutes les pages d'une liste, et la version de la bibliothèque (Last-Modified-Version) au début."""
        found, start, version = [], 0, None
        while True:
            response = self.call("GET", f"/users/{self.user}{path}", limit=PAGE, start=start, **params)
            if version is None:
                version = response.headers.get("Last-Modified-Version")
            page = response.json()
            found += page
            start += PAGE
            total = response.headers.get("Total-Results")
            # sans le total (inattendu), on continue tant que les pages sont pleines : jamais une liste tronquée
            if not page or (start >= int(total) if total is not None else len(page) < PAGE):
                return found, version

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
    places = {p["id"]: {LIBRARY} for p in db.list_papers() if p["arxiv_id"]}  # PDF importés : pas dans Zotero
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
        connection.executescript("DELETE FROM zotero_items; DELETE FROM zotero_folders; DELETE FROM zotero_places; DELETE FROM zotero_index;")
    db.save_settings({"zoteroIndexVersion": 0})


def _index(client):
    """Identifiants arXiv des éléments de toute la bibliothèque Zotero ({identifiant: [clés]}), tenus à jour par
    différences (`since`) : un parcours complet la première fois, ensuite seulement ce qui a changé."""
    since = db.get_settings().get("zoteroIndexVersion") or 0
    changed, version = client.listing("/items/top", since=since, includeTrashed=1)
    deleted = client.call("GET", f"/users/{client.user}/deleted", since=since).json().get("items") or [] if since else []
    with db.connect() as connection:
        for item in changed:
            arxiv_id = None if item["data"].get("deleted") else arxiv_of(item["data"])
            if arxiv_id:
                connection.execute("INSERT INTO zotero_index (item_key, arxiv_id) VALUES (?, ?) ON CONFLICT(item_key) DO UPDATE SET arxiv_id = excluded.arxiv_id",
                                   (item["key"], arxiv_id))
            else:
                connection.execute("DELETE FROM zotero_index WHERE item_key = ?", (item["key"],))
        connection.executemany("DELETE FROM zotero_index WHERE item_key = ?", [(key,) for key in deleted])
        rows = connection.execute("SELECT item_key, arxiv_id FROM zotero_index").fetchall()
    if version and str(version).isdigit():
        db.save_settings({"zoteroIndexVersion": int(version)})
    found = {}
    for row in rows:
        found.setdefault(row["arxiv_id"], []).append(row["item_key"])
    return found


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


def _clean(name):
    try:
        return folders.clean_name(name)
    except folders.FolderError:
        return None  # nom impossible ici (trop long…) : sous-collection laissée sans dossier


def _sync(client, importer, careful):
    settings = db.get_settings()
    who = client.connect()
    same_user = settings.get("zoteroUser") == who["userID"]
    if settings.get("zoteroUser") is not None and not same_user:
        _forget_all()  # autre compte Zotero : la liaison repart de zéro
    report = {"user": who["username"], "toZotero": 0, "fromZotero": 0, "removedZotero": 0, "removedHere": 0, "folders": 0,
              "skipped": 0, "duplicates": 0, "unmapped": 0, "importFailed": 0, "held": 0}

    # ---- toutes les collections (quelques requêtes) : « arXivReader » et son arbre, sans celles à la corbeille ----
    every = {c["key"]: c for c in client.all("/collections") if not c["data"].get("deleted")}
    root = settings.get("zoteroRoot") if same_user else None
    if root and root not in every:
        _forget_all()  # « arXivReader » supprimée dans Zotero : la liaison repart de zéro (rien n'est retiré ici)
        root = None
    if not root:
        root = next((k for k, c in every.items() if c["data"]["name"] == ROOT_NAME and not c["data"].get("parentCollection")), None)
        if not root:
            written, failed = client.write("/collections", [{"name": ROOT_NAME, "parentCollection": False}])
            if 0 not in written:
                raise ZoteroError("server", f"Collection {ROOT_NAME} impossible à créer : {failed}")
            root = written[0]["key"]
            every[root] = written[0]
        db.save_settings({"zoteroRoot": root, "zoteroUser": who["userID"]})
    children = {}
    for key, collection in every.items():
        children.setdefault(collection["data"].get("parentCollection") or None, []).append(key)
    subs = {k: every[k] for k in children.get(root, [])}
    top_of = {}  # collection de l'arbre → sous-collection de premier niveau (un dossier) qui la contient
    for sub in subs:
        stack = [sub]
        while stack:
            current = stack.pop()
            top_of[current] = sub
            stack += children.get(current, [])
    tree = {root, *top_of}

    saved_items, mapped, before = _saved()
    by_saved = {key: pid for pid, key in saved_items.items()}

    # ---- dossiers ↔ sous-collections de premier niveau ----
    local_folders = {f["id"]: f for f in folders.list_folders()}
    links, agreed = {}, {}  # dossier → sous-collection ; dossier → nom convenu des deux côtés
    vanished = sum(1 for fid, (key, _) in mapped.items() if fid in local_folders and key not in subs)
    hold_folders = careful and vanished > 1 and vanished * 2 > len(mapped)  # presque toutes disparues : on attend
    held = set()  # sous-collections disparues, gardées pour l'instant (synchronisation automatique)
    for folder_id, (key, name) in mapped.items():
        here, there = local_folders.get(folder_id), subs.get(key)
        if here and there:
            links[folder_id], agreed[folder_id] = key, here["name"]
            remote_name = _clean(there["data"]["name"])
            if here["name"] != name and here["name"] != remote_name:  # renommé ici (gagne aussi s'il l'a été là-bas)
                written, _ = client.write("/collections", [{"key": key, "version": there["version"], "name": here["name"]}])
                if 0 in written:
                    report["folders"] += 1
                else:
                    agreed[folder_id] = name  # refusé (changé entre-temps) : nouvel essai au prochain passage
            elif remote_name and remote_name != name and remote_name != here["name"]:  # renommé dans Zotero seulement
                try:
                    folders.rename(folder_id, remote_name)
                    agreed[folder_id] = remote_name
                    report["folders"] += 1
                except folders.FolderError:
                    agreed[folder_id] = name  # nom déjà pris ici : on garde le nôtre pour l'instant
        elif here and not there:  # sous-collection supprimée dans Zotero : le dossier aussi (pas ses articles)
            if hold_folders:
                links[folder_id], agreed[folder_id] = key, here["name"]
                held.add(key)
                report["held"] += 1
                continue
            folders.delete(folder_id)
            report["folders"] += 1
        elif there and not here:
            # dossier supprimé ici : la sous-collection aussi (pas ses éléments) ; mais si Zotero y a rangé quelque
            # chose depuis la dernière fois (ou y a créé des sous-collections), le dossier revient
            added = bool(children.get(key)) or any(
                by_saved.get(item["key"]) is None or str(folder_id) not in before.get(by_saved[item["key"]], set())
                for item in client.all(f"/collections/{key}/items/top"))
            name_back = _clean(there["data"]["name"]) if added else None
            if name_back:
                try:
                    created = folders.create(name_back)
                    links[created["id"]], agreed[created["id"]] = key, created["name"]
                    report["folders"] += 1
                    continue
                except folders.FolderError:
                    pass
            client.call("DELETE", f"/users/{client.user}/collections/{key}", version=there["version"])
            report["folders"] += 1
    for folder_id, folder in local_folders.items():
        if folder_id in mapped:
            continue
        same = next((k for k, c in subs.items() if k not in links.values() and k not in by_collection_keys(mapped)
                     and (_clean(c["data"]["name"]) or "").casefold() == folder["name"].casefold()), None)
        if same is None:
            written, _ = client.write("/collections", [{"name": folder["name"], "parentCollection": root}])
            if 0 not in written:
                continue  # au prochain passage ; ses articles n'y sont pas comptés d'ici là
            same = written[0]["key"]
            report["folders"] += 1
        links[folder_id], agreed[folder_id] = same, folder["name"]
    for key, collection in subs.items():
        if key in links.values() or key in by_collection_keys(mapped):
            continue
        name = _clean(collection["data"]["name"])
        try:
            created = folders.create(name) if name else None
        except folders.FolderError:  # même nom qu'un dossier déjà lié (« Notes » et « notes »…)
            created = None
        if created is None:
            report["unmapped"] += 1  # ses éléments comptent comme « dans la bibliothèque », sans dossier
            continue
        links[created["id"]], agreed[created["id"]] = key, created["name"]
        report["folders"] += 1
    with db.connect() as connection:
        connection.execute("DELETE FROM zotero_folders")
        connection.executemany("INSERT INTO zotero_folders (folder_id, collection_key, name) VALUES (?, ?, ?)",
                               [(fid, key, agreed[fid]) for fid, key in links.items()])
    folder_of = {key: str(fid) for fid, key in links.items()}
    place_of = {c: folder_of[top] for c, top in top_of.items() if top in folder_of}  # collection de l'arbre → dossier
    held_places = {str(fid) for fid, key in links.items() if key in held}
    linked = set(folder_of.values())
    keep = lambda places: {p for p in places if p == LIBRARY or p in linked}  # dossiers liés seulement

    _check_stop()
    # ---- éléments de l'arbre « arXivReader » ----
    def places_of(item):
        data = item["data"]
        mine = [] if data.get("deleted") else [c for c in data.get("collections", []) if c in tree]  # corbeille : dehors
        return ({LIBRARY} | {place_of[c] for c in mine if c in place_of}) if mine else set()

    remote = {}
    for collection in tree:
        for item in client.all(f"/collections/{collection}/items/top", sort="dateAdded", direction="asc"):
            remote[item["key"]] = item
    remote_places = {key: places_of(item) for key, item in remote.items()}
    remote_arxiv = {key: arxiv_of(item["data"]) for key, item in remote.items()}
    report["skipped"] = sum(1 for a in remote_arxiv.values() if not a)

    local = {pid: keep(places) for pid, places in _local().items()}
    before = {pid: keep(places) | (places & held_places) for pid, places in before.items()}
    papers = {p["id"]: p for p in db.list_all_papers()}
    items = dict(saved_items)  # article → élément lié

    # éléments liés hors de l'arbre (retirés, à la corbeille, supprimés) : relus par lots, seulement ceux qui comptent
    gone = set()
    outside = [key for pid, key in items.items() if key not in remote and LIBRARY in local.get(pid, set()) | before.get(pid, set())]
    for start in range(0, len(outside), WRITE):
        chunk = outside[start:start + WRITE]
        found = {i["key"]: i for i in client.all("/items", itemKey=",".join(chunk), includeTrashed=1)}
        for key in chunk:
            if key in found:
                remote[key], remote_places[key] = found[key], places_of(found[key])
            else:
                gone.add(key)  # supprimé de Zotero (corbeille vidée)
    # un article dont l'élément n'est plus dans l'arbre, alors qu'un autre élément du même identifiant y est (doublons
    # fusionnés dans Zotero…) : relié à celui-ci ; voulu de nouveau ici alors que son élément est à la corbeille ou
    # supprimé : un nouvel élément sera créé
    in_tree = {}
    for key, arxiv_id in remote_arxiv.items():
        if arxiv_id:
            in_tree.setdefault(arxiv_id, []).append(key)
    by_item = {key: pid for pid, key in items.items()}
    drop = set()  # liaisons à oublier après la fusion (élément supprimé de Zotero)
    for pid, key in list(items.items()):
        if remote_places.get(key):
            continue
        arxiv_id = papers[pid]["arxiv_id"] if pid in papers else None
        other = next((k for k in in_tree.get(arxiv_id, []) if k not in by_item), None)
        trashed = key in gone or bool(remote.get(key, {}).get("data", {}).get("deleted"))
        if other:
            items[pid] = other
            by_item.pop(key, None)
            by_item[other] = pid
        elif trashed and LIBRARY in local.get(pid, set()) and local[pid] != before.get(pid, set()):
            items.pop(pid)  # rajouté ici depuis : nouvel élément
            by_item.pop(key, None)
            before.pop(pid, None)
        elif key in gone:
            drop.add(pid)
    # éléments de l'arbre pas encore liés : à l'article du même identifiant (importé d'arXiv s'il n'est pas ici)
    for key, arxiv_id in remote_arxiv.items():
        if key in by_item or not arxiv_id:
            continue
        pid = paper_key(arxiv_id)
        if pid in items:
            report["duplicates"] += 1  # autre élément du même article dans Zotero : laissé
            continue
        items[pid], by_item[key] = key, pid
    # articles d'ici pas encore liés : à un élément Zotero du même identifiant, même hors de l'arbre (pas de doublon ;
    # index des identifiants de toute la bibliothèque Zotero), sinon créés
    unlinked = [pid for pid in local if pid not in items]
    to_create = []
    if unlinked:
        index = _index(client)
        wanted = {pid: next((k for k in index.get(papers[pid]["arxiv_id"], []) if k not in by_item), None) for pid in unlinked}
        keys = [k for k in wanted.values() if k]
        found = {}
        for start in range(0, len(keys), WRITE):
            found.update({i["key"]: i for i in client.all("/items", itemKey=",".join(keys[start:start + WRITE]))})
        for pid in unlinked:
            match = found.get(wanted[pid])
            if match and not match["data"].get("deleted") and match["key"] not in by_item:
                items[pid], by_item[match["key"]] = match["key"], pid
                remote[match["key"]], remote_places[match["key"]] = match, places_of(match)
            else:
                to_create.append(pid)

    _check_stop()
    # ---- fusion des places, article par article ----
    plan = []
    for pid, key in items.items():
        here, then = local.get(pid, set()), before.get(pid, set())
        there = remote_places.get(key, set()) | (then & held_places)  # dossier en attente : rien de changé là-bas
        if LIBRARY in then and LIBRARY not in here:
            result = set()  # retiré de la bibliothèque ici : retiré partout (même rangé entre-temps dans Zotero)
        else:
            result = {place for place in here | there | then if _merge(place in here, place in there, place in then)}
            if result - {LIBRARY}:
                result.add(LIBRARY)  # dans un dossier : dans la bibliothèque
        plan.append((pid, key, here, there, then, result))
    # synchronisation automatique : beaucoup d'articles retirés d'un coup par Zotero, ou des sous-collections
    # disparues (réponse incomplète ?) : rien n'est changé, d'un côté ni de l'autre, avant « Synchroniser maintenant »
    leaving = [entry for entry in plan if LIBRARY in entry[2] and LIBRARY not in entry[5]]
    if careful and (held or _massive(len(leaving), len(local))):
        report["held"] += len(leaving)
        _save(items, {pid: before[pid] for pid in before if pid in items}, who, report)
        return report

    results, updates, changes = {}, [], []
    current = {f["id"] for f in folders.list_folders()}
    for pid, key, here, there, then, result in plan:
        results[pid] = result
        # ici
        if LIBRARY in result and LIBRARY not in here:
            _check_stop()
            try:
                if pid not in papers:
                    papers[pid] = importer(remote_arxiv.get(key) or arxiv_of(remote[key]["data"]))
                else:
                    library.set_in_library(pid, True)
                report["fromZotero"] += 1
            except Exception:
                report["importFailed"] += 1  # arXiv injoignable… : au prochain passage
                if then:
                    results[pid] = then
                else:
                    results.pop(pid)
                continue
        elif LIBRARY not in result and LIBRARY in here:
            library.set_in_library(pid, False)
            report["removedHere"] += 1
        for place in (result ^ here) - {LIBRARY}:
            if int(place) in current:  # dossier supprimé ici entre-temps : rien
                folders.set_member(int(place), pid, place in result)
        # dans Zotero : collections de l'arbre voulues (une sous-collection plus profonde est gardée telle quelle),
        # les autres collections de l'élément gardées
        if key not in remote or result == there:
            continue
        data = remote[key]["data"]
        mine = data.get("collections", [])
        others = [c for c in mine if c not in tree]
        kept = [c for c in mine if c in tree and (place_of.get(c) in result if c in place_of else LIBRARY in result)
                and not data.get("deleted")]
        covered = {place_of.get(c) for c in kept}
        added = [links[int(p)] for p in result - {LIBRARY} if p not in covered and int(p) in links and links[int(p)] not in held]
        if LIBRARY in result and not kept and not added:
            added = [root]
        wanted = sorted(set(others + kept + added))
        if set(wanted) != set(mine):
            updates.append({"key": key, "version": remote[key]["version"], "collections": wanted})
            changes.append((pid, LIBRARY in result and LIBRARY not in there, LIBRARY not in result and LIBRARY in there))
    if updates:
        written, failed = client.write("/items", updates)
        for index, (pid, joined, left) in enumerate(changes):
            if index in written:
                report["toZotero"] += joined
                report["removedZotero"] += left
            elif before.get(pid):
                results[pid] = before[pid]  # refusé (changé entre-temps) : le changement d'ici sera repris au prochain passage
            else:
                results.pop(pid, None)
    if to_create:
        objects = [new_item(papers[pid], [root] + [links[int(p)] for p in local[pid] - {LIBRARY} if int(p) in links and links[int(p)] not in held])
                   for pid in to_create]
        written, _ = client.write("/items", objects)
        for index, item in written.items():
            pid = to_create[index]
            items[pid], results[pid] = item["key"], local[pid]
            report["toZotero"] += 1
    for pid in drop:
        items.pop(pid, None)  # élément supprimé de Zotero : la place a été fusionnée, la liaison est oubliée
    _save(items, results, who, report)
    return report


def by_collection_keys(mapped):
    return {key for key, _ in mapped.values()}


def _save(items, results, who, report):
    with db.connect() as connection:
        connection.execute("DELETE FROM zotero_items")
        connection.executemany("INSERT INTO zotero_items (paper_id, item_key) VALUES (?, ?)", list(items.items()))
        connection.execute("DELETE FROM zotero_places")
        connection.executemany("INSERT INTO zotero_places (paper_id, place) VALUES (?, ?)",
                               [(pid, place) for pid, places in results.items() if pid in items for place in places])
    db.save_settings({"zoteroLastSync": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "zoteroLastError": "", "zoteroLastErrorCode": "",
                      "zoteroUserName": who["username"], "zoteroReport": report})


def status():
    settings = db.get_settings()
    return {"connected": bool(settings.get("zoteroApiKey")), "user": settings.get("zoteroUserName") or "",
            "lastSync": settings.get("zoteroLastSync") or "", "lastError": settings.get("zoteroLastError") or "",
            "lastErrorCode": settings.get("zoteroLastErrorCode") or "",
            "report": settings.get("zoteroReport") or None, "running": _lock.locked()}


def describe(error):
    """(code, message) d'une erreur de synchronisation ; le code est traduit par la page."""
    if isinstance(error, ZoteroError):
        return error.code, str(error)
    if isinstance(error, httpx.HTTPError):
        return "network", type(error).__name__
    log.exception("synchronisation Zotero : erreur inattendue")
    return "unexpected", f"{type(error).__name__}: {error}"


def record_error(code, message=""):
    db.save_settings({"zoteroLastError": message or code, "zoteroLastErrorCode": code})


def _check_stop():
    if stop.is_set():
        raise ZoteroError("stopped", "Serveur arrêté pendant la synchronisation")


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
            except Exception as error:
                code, message = describe(error)
                if code not in ("busy", "stopped"):
                    record_error(code, message)
        await asyncio.sleep(5)

