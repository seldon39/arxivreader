"""Assistant de premier lancement : identifiants, articles probables, suggestions, profil rempli (sans réseau)."""

import pathlib
import tempfile
import unittest
from unittest import mock

from app import db, onboarding, profile


def item(arxiv_id, authors, categories, primary):
    return {"arxiv_id": arxiv_id, "title": f"Paper {arxiv_id}", "authors": authors, "announced": "2020-01-01", "categories": categories,
            "primary_category": primary, "abstract": "About Toeplitz operators"}


class Onboarding(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def test_identifier(self):
        self.assertEqual(onboarding.identifier("https://arxiv.org/a/ma_x_1"), "ma_x_1")
        self.assertEqual(onboarding.identifier("ma_x_1.atom2"), "ma_x_1")
        self.assertEqual(onboarding.identifier("https://orcid.org/0000-0002-6659-4134"), "0000-0002-6659-4134")
        self.assertIsNone(onboarding.identifier("Xiaonan Ma"))

    def test_find_by_name_marks_homonyms(self):
        found = [item("0806.2370", ["Xiaonan Ma", "George Marinescu"], ["math.DG", "math.CV"], "math.DG"),
                 item("2101.00001", ["Xiaoming Ma"], ["cs.LG"], "cs.LG")] + [
                 item(f"1{i}03.00001", ["X. Ma"], ["math.DG"], "math.DG") for i in range(5)] + [
                 item("2301.00001", ["Xiaonan Ma"], ["cond-mat.mtrl-sci"], "cond-mat.mtrl-sci")]  # homonyme exact, autre domaine
        with mock.patch.object(onboarding.search, "_fetch", return_value=(found, 2)) as fetch:
            result = onboarding.find("Xiaonan Ma")
        self.assertEqual(fetch.call_args.args[0], 'au:"Xiaonan Ma"')
        self.assertEqual([p["likely"] for p in result["papers"]], [True, False] + [True] * 5 + [False])
        with self.assertRaises(onboarding.OnboardingError):
            onboarding.find("", "not an id")

    def test_suggest_and_apply(self):
        papers = [{"arxivId": "0806.2370", "title": "A", "authors": ["Xiaonan Ma", "George Marinescu"], "categories": ["math.DG", "math.CV"], "primary": "math.DG"},
                  {"arxivId": "1203.4201", "title": "B", "authors": ["X. Ma", "G. Marinescu"], "categories": ["math.DG"], "primary": "math.DG"}]
        answer = mock.Mock(status_code=200, json=lambda: [{"references": [{"authors": [{"name": "Martin Schlichenmaier"}]}] * 3}, None])
        with mock.patch.object(onboarding.httpx.Client, "post", return_value=answer):
            suggestions = onboarding.suggest(papers, "Xiaonan Ma")
        self.assertEqual(suggestions["categories"][0], {"category": "math.DG", "count": 4})
        self.assertEqual(suggestions["authors"][0], {"name": "George Marinescu", "why": "coauthor", "count": 2})  # forme la plus complète
        self.assertIn({"name": "Martin Schlichenmaier", "why": "cited", "count": 3}, suggestions["authors"])
        self.assertNotIn("Xiaonan Ma", [a["name"] for a in suggestions["authors"]])
        onboarding.apply("Xiaonan Ma", papers, ["math.DG", "math.CV"], ["George Marinescu"])
        kinds = sorted((i["kind"], i["label"]) for i in profile.list_items())
        self.assertEqual(kinds, [("author", "George Marinescu"), ("mine", "A"), ("mine", "B")])
        settings = db.get_settings()
        self.assertEqual((settings["feedCategories"], settings["onboarded"]), (["math.DG", "math.CV"], True))


if __name__ == "__main__":
    unittest.main()
