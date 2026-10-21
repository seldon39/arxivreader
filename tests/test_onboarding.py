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
        self.assertEqual(onboarding.identifier("https://orcid.org/0000-0002-1825-0097"), "0000-0002-1825-0097")
        self.assertIsNone(onboarding.identifier("Xiaonan Ma"))

    def test_find_by_name_marks_homonyms(self):
        found = [item("0806.2370", ["Xiaonan Ma", "George Marinescu"], ["math.DG", "math.CV"], "math.DG"),
                 item("2101.00001", ["Xiaoming Ma"], ["cs.LG"], "cs.LG")] + [
                 item(f"1{i}03.00001", ["X. Ma"], ["math.DG"], "math.DG") for i in range(9)] + [
                 item("2301.00001", ["Xiaonan Ma"], ["cond-mat.mtrl-sci"], "cond-mat.mtrl-sci")]  # homonyme exact, autre domaine
        with mock.patch.object(onboarding.search, "_fetch", return_value=(found, 2)) as fetch:
            result = onboarding.find("Xiaonan Ma")
        self.assertEqual(fetch.call_args.args[0], 'au:"Xiaonan Ma"')
        self.assertEqual([p["likely"] for p in result["papers"]], [True, False] + [True] * 9 + [False])
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


    def test_identifier_feed(self):
        feed = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
          <title>Alice Martin's articles on arXiv</title>
          <entry><id>http://arxiv.org/abs/2603.21385v1</id><published>2026-03-22T16:09:38-04:00</published><title>A paper</title>
            <summary>Text</summary><author><name>Bob Advisor</name></author><author><name>Alice Martin</name></author>
            <category term="math.DG"/></entry></feed>"""
        answer = mock.Mock(status_code=200, text=feed, raise_for_status=lambda: None)
        with mock.patch.object(onboarding.httpx.Client, "get", return_value=answer):
            result = onboarding.find("", "https://arxiv.org/a/martin_a_1")
        self.assertEqual((result["owner"], len(result["papers"]), result["papers"][0]["likely"]), ("Alice Martin", 1, True))
        with mock.patch.object(onboarding.httpx.Client, "get", return_value=mock.Mock(status_code=404)), self.assertRaises(onboarding.OnboardingError):
            onboarding.find("", "martin_a_1")
        with mock.patch.object(onboarding.httpx.Client, "get", return_value=mock.Mock(status_code=200, text="<html>maintenance", raise_for_status=lambda: None)):
            with self.assertRaises(onboarding.OnboardingError) as caught:
                onboarding.find("", "martin_a_1")
        self.assertEqual(caught.exception.code, "bad-response")

    def test_names(self):
        self.assertTrue(onboarding.same_person("George Marinescu", "George M. Marinescu"))
        self.assertTrue(onboarding.same_person("G. Marinescu", "George Marinescu"))
        self.assertFalse(onboarding.same_person("Albert Martin", "Alice Martin"))
        self.assertFalse(onboarding.same_person("Xin Ma", "Xiaonan Ma"))
        self.assertTrue(onboarding.same_person("J.-M. Bismut", "Jean-Michel Bismut"))
        self.assertEqual(onboarding.identifier("0000-0002-1825-009x"), "0000-0002-1825-009X")
        # identifiant sans nom dans le flux : à égalité (un directeur sur tous les articles), le nom donné, sinon rien
        papers = [{"authors": ["Bob Advisor", "Alice Martin"]}] * 3
        self.assertEqual(onboarding._owner_name(papers, "Alice Martin"), "Alice Martin")
        self.assertEqual(onboarding._owner_name(papers, ""), "")

    def test_few_papers_or_balanced_fields_untick_nothing(self):
        few = [item(f"2{i}01.00001", ["Alice Martin"], ["math.DG" if i < 3 else "cs.LG"], "math.DG") for i in range(5)]
        with mock.patch.object(onboarding.search, "_fetch", return_value=(few, 5)):
            self.assertTrue(all(p["likely"] for p in onboarding.find("Alice Martin")["papers"]))
        majority = [item(f"1{i:02d}1.00001", ["Alice Martin"], ["cs.LG"], "cs.LG") for i in range(20)] + [
            item(f"2{i}01.00001", ["Alice Martin"], ["math.DG"], "math.DG") for i in range(3)]
        with mock.patch.object(onboarding.search, "_fetch", return_value=(majority, 23)):
            result = onboarding.find("Alice Martin")
        self.assertEqual(result["total"], 23)
        self.assertTrue(all(p["likely"] for p in result["papers"]))  # 87 % : pas assez net pour deviner

    def test_odd_answers_and_large_collaborations(self):
        papers = [{"arxivId": "2101.00001", "title": "Big", "authors": [f"Person {i}" for i in range(800)], "categories": ["hep-ex"], "primary": "hep-ex"},
                  {"arxivId": "2101.00002", "title": "Small", "authors": ["Alice Martin", "Carol Coauthor"], "categories": ["math-ph"], "primary": "math-ph"}]
        for body in ({"error": "x"}, [{"references": [None, {"authors": None}, {"authors": [None, {"name": 3}]}]}], None):
            answer = mock.Mock(status_code=200, json=lambda body=body: body)
            with mock.patch.object(onboarding.httpx.Client, "post", return_value=answer):
                suggestions = onboarding.suggest(papers, "Alice Martin")
            self.assertEqual([a["name"] for a in suggestions["authors"]], ["Carol Coauthor"])

    def test_apply_skips_blank_titles(self):
        onboarding.apply("Alice Martin", [{"arxivId": "2101.00001", "title": "  "}, {"arxivId": "2101.00002", "title": "Kept", "abstract": "x" * 30000}],
                         ["math.DG"], ["Carol Coauthor"])
        mine = [i for i in profile.list_items() if i["kind"] == "mine"]
        self.assertEqual([(i["label"], len(i["text"])) for i in mine], [("Kept", 20000)])
        self.assertTrue(db.get_settings()["onboarded"])


if __name__ == "__main__":
    unittest.main()
