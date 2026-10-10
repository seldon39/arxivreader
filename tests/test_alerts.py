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

    def test_old_papers_of_a_newly_followed_author_are_not_notified(self):
        self.run_check([])
        old = paper("2601.00005")
        old["announced"] = "2000-01-01"
        new, notify = self.run_check([old, paper("2601.00006")])
        self.assertEqual(len(new), 2)
        self.assertEqual([p["key"] for p in notify.call_args[0][0]], ["2601.00006"])  # seul le récent est annoncé

    def test_unfollowed_author_leaves_the_followed_section(self):
        self.run_check([paper("2601.00007")])
        for item in profile.list_items():
            profile.remove_item(item["id"])
        self.run_check([])
        self.assertEqual(alerts.recent_followed(), [])

    def test_seen_keys_are_restricted_to_followed_papers(self):
        self.run_check([paper("2601.00008")])
        alerts.unseen()
        alerts.mark_seen(["2601.00008", "anything-else"])
        self.assertEqual(db.get_settings()["alerts.seen"], ["2601.00008"])

    def test_notification_text(self):
        with mock.patch.object(alerts.subprocess, "run") as run:
            alerts.notify([paper("1"), paper("2"), paper("3"), paper("4")])
        script = run.call_args[0][0][2]
        self.assertIn("4 nouveaux articles", script)
        self.assertIn("Xiaonan Ma", script)
        with mock.patch.object(alerts.subprocess, "run") as run:
            alerts.notify([{**paper("5"), "title": 'Quote " and \\ ' + "x" * 400}])
        script = run.call_args[0][0][2]
        self.assertIn('\\"', script)
        self.assertLess(len(script), 420)  # titre coupé

    def test_who(self):
        self.assertEqual(alerts._who(paper("x", "X. Ma")), "X. Ma")  # forme abrégée reconnue
        self.assertIn('\\"', alerts._quote('Title with "quotes"'))


if __name__ == "__main__":
    unittest.main()
