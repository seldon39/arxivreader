"""Alertes : premier passage silencieux, notification des seuls nouveaux articles, articles vus."""

import pathlib
import tempfile
import unittest
from unittest import mock

from app import alerts, db, profile


def paper(key, author="Xiaonan Ma"):
    return {"key": key, "arxiv_id": key, "version": 1, "title": f"Paper {key}", "authors": [author, "Other Person"],
            "abstract": "", "categories": ["math.DG"], "primary_category": "math.DG",
            "announced": "2099-01-01", "link": f"https://arxiv.org/abs/{key}"}


class Alerts(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        profile.add_item("author", "Xiaonan Ma")

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def run_check(self, papers):
        with mock.patch.object(profile, "followed_author_papers", return_value=papers), \
                mock.patch.object(alerts, "notify") as notify:
            new = alerts.check(["math.DG"])
        return new, notify

    def test_first_check_is_silent_then_only_new_papers(self):
        new, notify = self.run_check([paper("2601.00001")])
        self.assertEqual((new, notify.called), ([], False))
        new, notify = self.run_check([paper("2601.00001"), paper("2601.00002")])
        self.assertEqual([p["key"] for p in new], ["2601.00002"])
        notify.assert_called_once()
        new, notify = self.run_check([paper("2601.00001"), paper("2601.00002")])
        self.assertEqual((new, notify.called), ([], False))

    def test_setting_disables_notifications(self):
        self.run_check([])
        db.save_settings({"notifyFollowed": False})
        new, notify = self.run_check([paper("2601.00003")])
        self.assertEqual((len(new), notify.called), (1, False))

    def test_unseen_and_seen(self):
        self.run_check([paper("2601.00001")])
        self.assertEqual(alerts.unseen(), [])  # première fois : l'existant compte comme vu
        self.run_check([paper("2601.00001"), paper("2601.00004")])
        self.assertEqual(alerts.unseen(), ["2601.00004"])
        alerts.mark_seen()
        self.assertEqual(alerts.unseen(), [])

    def test_who(self):
        self.assertEqual(alerts._who(paper("x", "X. Ma")), "X. Ma")  # forme abrégée reconnue
        self.assertIn('\\"', alerts._quote('Title with "quotes"'))


if __name__ == "__main__":
    unittest.main()
