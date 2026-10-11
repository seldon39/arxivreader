"""Synchronisation avec Zotero : un faux Zotero en mémoire (API web v3, ce que l'application en utilise)."""

import json
import pathlib
import re
import tempfile
import unittest
from unittest import mock

from app import db, folders, zotero


class Answer:
    def __init__(self, status, body=None, total=None):
        self.status_code = status
        self.body = body
        self.headers = {"Total-Results": str(total)} if total is not None else {}

    def json(self):
        return self.body


class FakeZotero:
    """Bibliothèque Zotero d'un utilisateur : collections et éléments, numéros de version."""

    def __init__(self):
        self.collections = {}  # clé → {key, version, data: {name, parentCollection}}
        self.items = {}        # clé → {key, version, data: {…, collections, deleted?}}
        self.version = 0
        self.serial = 0
        self.requests = []

    def _key(self):
        self.serial += 1
        return f"K{self.serial:07d}"

    def add_collection(self, name, parent=False):
        self.version += 1
        key = self._key()
        self.collections[key] = {"key": key, "version": self.version, "data": {"key": key, "name": name, "parentCollection": parent}}
        return key

    def add_item(self, data):
        self.version += 1
        key = self._key()
        self.items[key] = {"key": key, "version": self.version, "data": {"key": key, "collections": [], **data}}
        return key

    def request(self, method, url, params=None, json=None, headers=None):
        assert headers["Zotero-API-Key"] == "a" * 24
        path = url.removeprefix(zotero.API)
        self.requests.append((method, path))
        params = params or {}
        if path == "/keys/current":
            return Answer(200, {"userID": 7, "username": "chercheur", "access": {"user": {"library": True, "write": True}}})
        assert path.startswith("/users/7/")
        path = path.removeprefix("/users/7")
        page = lambda rows: Answer(200, rows[int(params.get("start", 0)):int(params.get("start", 0)) + int(params.get("limit", 100))], len(rows))
        if method == "GET":
            if path == "/collections/top":
                return page([c for c in self.collections.values() if not c["data"]["parentCollection"]])
            if m := re.fullmatch(r"/collections/(\w+)/collections", path):
                return page([c for c in self.collections.values() if c["data"]["parentCollection"] == m.group(1)])
            if m := re.fullmatch(r"/collections/(\w+)/items/top", path):
                return page([i for i in self.items.values() if m.group(1) in i["data"]["collections"] and not i["data"].get("deleted")])
            if m := re.fullmatch(r"/collections/(\w+)", path):
                return Answer(200, self.collections[m.group(1)]) if m.group(1) in self.collections else Answer(404)
            if path == "/items/top":
                rows = [i for i in self.items.values() if not i["data"].get("deleted")]
                if "q" in params:
                    rows = [i for i in rows if params["q"] in str(i["data"])]
                return page(rows)
            if m := re.fullmatch(r"/items/(\w+)", path):
                return Answer(200, self.items[m.group(1)]) if m.group(1) in self.items else Answer(404)
        if method == "POST" and path in ("/collections", "/items"):
            store = self.collections if path == "/collections" else self.items
            successful = {}
            for index, obj in enumerate(json):
                self.version += 1
                if "key" in obj:  # mise à jour partielle
                    current = store[obj["key"]]
                    if obj["version"] != current["version"]:
                        continue
                    current["data"].update({k: v for k, v in obj.items() if k not in ("key", "version")})
                    current["version"] = self.version
                    successful[str(index)] = current
                else:
                    key = self._key()
                    store[key] = {"key": key, "version": self.version, "data": {"key": key, **obj}}
                    successful[str(index)] = store[key]
            return Answer(200, {"successful": successful, "failed": {}})
        if method == "PATCH" and (m := re.fullmatch(r"/collections/(\w+)", path)):
            collection = self.collections[m.group(1)]
            if headers.get("If-Unmodified-Since-Version") != str(collection["version"]):
                return Answer(412)
            self.version += 1
            collection["data"].update(json)
            collection["version"] = self.version
            return Answer(204)
        if method == "DELETE" and (m := re.fullmatch(r"/collections/(\w+)", path)):
            self.collections.pop(m.group(1))
            for item in self.items.values():  # comme Zotero : les éléments restent, sortis de la collection
                item["data"]["collections"] = [c for c in item["data"]["collections"] if c != m.group(1)]
            return Answer(204)
        raise AssertionError(f"requête inattendue {method} {path}")

    def close(self):
        pass

    def named(self, name):
        return next(k for k, c in self.collections.items() if c["data"]["name"] == name)


def save(arxiv_id, title, in_library=1):
    db.save_paper({"id": arxiv_id.replace("/", "_"), "arxiv_id": arxiv_id, "title": title, "authors": ["Xiaonan Ma", "George Marinescu"],
                   "abstract": "", "categories": [], "published": "2008-06-14", "in_library": in_library})
    return arxiv_id.replace("/", "_")


class Sync(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        self.zotero = FakeZotero()
        self.imported = []

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def importer(self, arxiv_id):
        self.imported.append(arxiv_id)
        save(arxiv_id, f"Imported {arxiv_id}")
        return db.get_paper(arxiv_id.replace("/", "_"))

    def run_sync(self):
        return zotero.sync("a" * 24, http=self.zotero, importer=self.importer)

    def places(self, item_key):
        names = {k: c["data"]["name"] for k, c in self.zotero.collections.items()}
        return sorted(names[c] for c in self.zotero.items[item_key]["data"]["collections"])

    def folder_of(self, name):
        return next(f for f in folders.list_folders() if f["name"] == name)

    def test_two_way(self):
        # arXivReader : A (dossier « Toeplitz ») et B ; Zotero : B déjà présent, dans « Thèse », hors arXivReader
        a = save("0806.2370", "Toeplitz operators")
        b = save("math/0404494", "Bergman kernel")
        save("1111.1111", "Seulement lu", in_library=0)
        toeplitz = folders.create("Toeplitz", [a])
        these = self.zotero.add_collection("Thèse")
        b_item = self.zotero.add_item({"itemType": "journalArticle", "title": "On the asymptotic expansion", "url": "https://arxiv.org/abs/math/0404494v3",
                                       "collections": [these]})

        report = self.run_sync()
        self.assertEqual((report["toZotero"], report["fromZotero"]), (2, 0))
        a_item = next(k for k, i in self.zotero.items.items() if i["data"].get("archiveID") == "arXiv:0806.2370")
        self.assertEqual(self.places(a_item), ["Toeplitz", "arXivReader"])
        self.assertEqual(self.places(b_item), ["Thèse", "arXivReader"])  # lié, pas recréé ; « Thèse » gardée
        self.assertEqual(len(self.zotero.items), 2)
        self.assertEqual(self.zotero.items[a_item]["data"]["creators"][0], {"creatorType": "author", "firstName": "Xiaonan", "lastName": "Ma"})

        # rien n'a changé : rien n'est écrit
        writes = len([r for r in self.zotero.requests if r[0] != "GET"])
        self.run_sync()
        self.assertEqual(len([r for r in self.zotero.requests if r[0] != "GET"]), writes)

        # dans Zotero : C rangé dans « Toeplitz », nouvelle sous-collection « Bergman » avec A, B sorti d'arXivReader,
        # un élément sans identifiant arXiv rangé dans arXivReader
        root, sub = self.zotero.named("arXivReader"), self.zotero.named("Toeplitz")
        self.zotero.add_item({"itemType": "preprint", "title": "Review", "archiveID": "arXiv:1003.2523", "collections": [sub]})
        bergman = self.zotero.add_collection("Bergman", root)
        self.zotero.items[a_item]["data"]["collections"].append(bergman)
        self.zotero.items[b_item]["data"]["collections"] = [these]
        self.zotero.add_item({"itemType": "book", "title": "A book", "collections": [root]})

        report = self.run_sync()
        self.assertEqual(self.imported, ["1003.2523"])
        self.assertEqual(report["skipped"], 1)
        self.assertIn("1003.2523", self.folder_of("Toeplitz")["papers"])
        self.assertEqual(self.folder_of("Bergman")["papers"], [a])
        self.assertFalse(db.get_paper(b)["in_library"])  # retiré de la bibliothèque, pas effacé
        self.assertIsNotNone(db.get_paper(b))

        # ici : dossier renommé, A retiré de la bibliothèque, dossier « Bergman » supprimé
        folders.rename(toeplitz["id"], "Berezin–Toeplitz")
        db.update_paper(a, in_library=0)
        folders.delete(self.folder_of("Bergman")["id"])
        self.run_sync()
        self.assertEqual(self.zotero.collections[sub]["data"]["name"], "Berezin–Toeplitz")
        self.assertNotIn(bergman, self.zotero.collections)
        self.assertIn(a_item, self.zotero.items)  # jamais supprimé de Zotero
        self.assertEqual(self.places(a_item), [])
        self.assertEqual(self.places(b_item), ["Thèse"])

    def test_root_deleted_in_zotero(self):
        # collection « arXivReader » supprimée dans Zotero : recréée, la bibliothèque ici reste entière
        a = save("0806.2370", "Toeplitz operators")
        self.run_sync()
        root = self.zotero.named("arXivReader")
        self.zotero.collections.pop(root)
        for item in self.zotero.items.values():
            item["data"]["collections"] = []
        self.run_sync()
        self.assertTrue(db.get_paper(a)["in_library"])
        new_root = self.zotero.named("arXivReader")
        self.assertNotEqual(new_root, root)
        self.assertTrue(any(new_root in i["data"]["collections"] for i in self.zotero.items.values()))
        self.assertEqual(len(self.zotero.items), 1)  # l'élément déjà créé est repris, pas dupliqué

    def test_trashed_in_zotero(self):
        a = save("0806.2370", "Toeplitz operators")
        self.run_sync()
        item = next(iter(self.zotero.items.values()))
        item["data"]["deleted"] = 1
        self.run_sync()
        self.assertFalse(db.get_paper(a)["in_library"])

    def test_massive_removal_waits_for_manual_sync(self):
        # automatique : Zotero ne montre plus aucun article (réponse incomplète ?) : rien n'est retiré ici
        ids = [save(f"2101.0000{i}", f"Paper {i}") for i in range(8)]
        folders.create("A", ids[:2]), folders.create("B", ids[2:4]), folders.create("C", ids[4:6])
        self.run_sync()
        for item in self.zotero.items.values():
            item["data"]["collections"] = []
        for name in ("A", "B", "C"):
            self.zotero.collections.pop(self.zotero.named(name))
        report = zotero.sync("a" * 24, http=self.zotero, importer=self.importer, careful=True)
        self.assertEqual(report["removedHere"], 0)
        self.assertTrue(all(db.get_paper(i)["in_library"] for i in ids))
        self.assertEqual(sorted(f["name"] for f in folders.list_folders()), ["A", "B", "C"])
        self.assertEqual(self.folder_of("A")["papers"], ids[:2])
        # « Synchroniser maintenant » : appliqué
        report = self.run_sync()
        self.assertEqual(report["removedHere"], 8)
        self.assertEqual(folders.list_folders(), [])

    def test_read_only_key(self):
        with mock.patch.object(self.zotero, "request", side_effect=lambda *a, **k: Answer(200, {"userID": 7, "access": {"user": {"library": True}}})):
            with self.assertRaises(zotero.ZoteroError) as caught:
                self.run_sync()
        self.assertEqual(caught.exception.code, "key")

    def test_arxiv_of(self):
        self.assertEqual(zotero.arxiv_of({"url": "https://arxiv.org/pdf/0806.2370v2"}), "0806.2370")
        self.assertEqual(zotero.arxiv_of({"DOI": "10.48550/arXiv.1003.2523"}), "1003.2523")
        self.assertEqual(zotero.arxiv_of({"extra": "tex.ids: x\narXiv: math/0404494"}), "math/0404494")
        self.assertIsNone(zotero.arxiv_of({"title": "A book", "url": "https://example.org/arxiv"}))


if __name__ == "__main__":
    unittest.main()
