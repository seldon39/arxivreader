"""Alertes de citation : articles qui citent les vôtres (Semantic Scholar simulé, base temporaire)."""

import pathlib
import tempfile
import unittest

from app import alerts, citations, db, feed, profile


def paper(paper_id, title, arxiv=None):
    return {"paperId": paper_id, "title": title, "authors": [{"name": "A. Author"}], "abstract": "…",
            "externalIds": {"ArXiv": arxiv} if arxiv else {}, "publicationDate": "2026-10-01", "url": f"https://s2/{paper_id}"}


class Citations(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, citations.fetch, alerts.display, citations.time.sleep)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        profile.add_item("mine", "My first paper", ref="2601.00001")
        profile.add_item("mine", "My second paper", ref="2601.00002")
        self.citing = {"ArXiv:2601.00001": [paper("a", "Citing both", "2610.00001")],
                       "ArXiv:2601.00002": [paper("a", "Citing both", "2610.00001"), paper("b", "Off arXiv")]}
        citations.fetch = lambda key, api_key=None: self.citing[key]
        citations.time.sleep = lambda seconds: None
        self.notified = []
        alerts.display = lambda *message: self.notified.append(message)

    def tearDown(self):
        db.DB_PATH, db.LIBRARY, citations.fetch, alerts.display, citations.time.sleep = self.saved

    def test_first_check_then_new_citation(self):
        self.assertEqual(citations.check(force=True), [])  # premier passage : rien d'annoncé
        self.assertEqual(self.notified, [])
        items = {i["key"]: i for i in feed.list_items("arxiv")}
        self.assertEqual(items["2610.00001"]["citing"], ["My first paper", "My second paper"])
        self.assertIn("s2:b", items)  # sans version arXiv : gardé, signalé « Hors arXiv »
        self.assertEqual(alerts.unseen(), [])  # l'existant ne compte pas comme nouveau
        self.citing["ArXiv:2601.00001"].append(paper("c", "A brand new citation", "2610.00009"))
        self.assertEqual(citations.check(), [])  # moins d'un jour après : pas de nouvelle requête
        new = citations.check(force=True)
        self.assertEqual([n["key"] for n in new], ["2610.00009"])
        self.assertEqual(len(self.notified), 1)
        self.assertIn("2610.00009", alerts.unseen())  # pastille du feed
        db.save_settings({"notifyCitations": False})
        self.citing["ArXiv:2601.00002"].append(paper("d", "Another one"))
        citations.check(force=True)
        self.assertEqual(len(self.notified), 1)  # notification désactivée


if __name__ == "__main__":
    unittest.main()
