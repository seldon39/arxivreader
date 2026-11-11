"""BibTeX : accents, particules, formules, anciens identifiants, revue connue ou non, clés, routes."""

import pathlib
import tempfile
import unittest

from fastapi.testclient import TestClient

from app import bibtex, db, journals, main

BASE = f"http://{main.HOST}:{main.PORT}"


def paper(**fields):
    return {"id": "2601.00001", "arxiv_id": "2601.00001", "version": 1, "title": "A title", "authors": ["Ada Lovelace"],
            "published": "2026-01-05T10:00:00Z", "primary_category": "math.DG", "doi": None, "journal_ref": None, **fields}


class Names(unittest.TestCase):
    def test_split_only_when_safe(self):
        self.assertEqual(bibtex.author_tex("Jean-Pierre Serre"), "Serre, Jean-Pierre")
        self.assertEqual(bibtex.author_tex("Louis Boutet de Monvel"), "de Monvel, Louis Boutet")
        self.assertEqual(bibtex.author_tex("B. L. van der Waerden"), "van der Waerden, B. L.")
        self.assertEqual(bibtex.author_tex("Émile Picard"), "Picard, {\\'E}mile")
        self.assertEqual(bibtex.author_tex("Wojciech Żelazko"), "{\\.Z}elazko, Wojciech")
        self.assertEqual(bibtex.author_tex("Martin Luther King Jr."), "King, Jr., Martin Luther")
        self.assertEqual(bibtex.author_tex("Camillo De Lellis"), "De Lellis, Camillo")  # BibTeX lirait « Lellis » sinon
        self.assertEqual(bibtex.author_tex("Jan van Den Bergh"), "van Den Bergh, Jan")
        self.assertEqual(bibtex.author_tex("G. 't Hooft"), "'t Hooft, G.")

    def test_kept_as_written(self):
        self.assertEqual(bibtex.author_tex("Plato"), "{Plato}")                       # un seul mot
        self.assertEqual(bibtex.author_tex("ATLAS Collaboration"), "{ATLAS Collaboration}")
        self.assertEqual(bibtex.author_tex("Tom Jr."), "{Tom Jr.}")                    # coupure pas sûre : nom entier entre accolades
        self.assertEqual(bibtex.author_tex("Michael Atiyah FRS"), "{Michael Atiyah FRS}")
        self.assertEqual(bibtex.author_tex("Le Thanh"), "{Le Thanh}")                  # « Le » : prénom ou nom ?
        self.assertEqual(bibtex.author_tex("Smith, John"), "Smith, John")              # déjà « Nom, Prénom »


class Titles(unittest.TestCase):
    def test_math_untouched_and_capitals_protected(self):
        text = bibtex.title_tex("Kähler–Einstein metrics on $L^2_{loc}$ spaces: a BV approach")
        self.assertIn("$L^2_{loc}$", text)  # ni « _ » protégé ni accolade touchée dans la formule
        self.assertIn("{K{\\\"a}hler--Einstein}", text)
        self.assertIn("{BV}", text)

    def test_first_word_not_protected_but_inner_capitals_are(self):
        self.assertTrue(bibtex.title_tex("Bergman kernels and Toeplitz operators").startswith("Bergman kernels and {Toeplitz}"))
        self.assertEqual(bibtex.title_tex("Łojasiewicz inequality"), "{\\L}ojasiewicz inequality")
        self.assertEqual(bibtex.title_tex("On the Łojasiewicz inequality"), "On the {{\\L}ojasiewicz} inequality")

    def test_special_characters(self):
        self.assertEqual(bibtex.title_tex("50% of R&D"), "50\\% of {R\\&D}")
        self.assertEqual(bibtex.title_tex("a_b"), "a\\_b")
        self.assertEqual(bibtex.title_tex("Broken {brace"), "Broken brace")


class Entries(unittest.TestCase):
    def test_arxiv_conventions_new_and_old_ids(self):
        new = bibtex.entry(paper())
        self.assertTrue(new.startswith("@misc{lovelace2026title,"))
        for line in ("eprint = {2601.00001}", "archivePrefix = {arXiv}", "primaryClass = {math.DG}", "year = {2026}",
                     "url = {https://arxiv.org/abs/2601.00001}", "author = {Lovelace, Ada}"):
            self.assertIn(line, new)
        old = bibtex.entry(paper(arxiv_id="math/0404494v2", id="math_0404494v2"))
        self.assertIn("eprint = {math/0404494}", old)  # identifiant de base, sans version
        self.assertNotIn("v2", old)

    def test_article_only_with_a_known_journal(self):
        full = bibtex.entry(paper(journal_ref="J. Differential Geom. 78 (2008), 1-45", doi="10.4310/jdg/1"))
        self.assertTrue(full.startswith("@article{"))
        for line in ("journal = {J. Differential Geom.}", "volume = {78}", "year = {2008}", "pages = {1--45}", "doi = {10.4310/jdg/1}"):
            self.assertIn(line, full)
        self.assertNotIn("url =", full)
        alt = bibtex.entry(paper(journal_ref="Comm. Math. Phys. 301, 1-30 (2011)"))
        for line in ("journal = {Comm. Math. Phys.}", "volume = {301}", "year = {2011}", "pages = {1--30}"):
            self.assertIn(line, alt)
        # référence illisible (année prise pour des pages, thèse, ordre inhabituel) : @misc, chaîne dite telle quelle dans
        # « note », ni pages ni revue inventées
        for ref in ("Annals of Math 180, 2014", "Ann. Inst. Fourier 2012, 62, 1111-1150", "Invent. Math. 100(2), 1990, 20-30",
                    "PhD thesis, University of X, 2010", "Duke Math. J. 2012"):
            odd = bibtex.entry(paper(journal_ref=ref))
            self.assertTrue(odd.startswith("@misc{"), ref)
            self.assertIn(f"note = {{{ref}}}", odd)
            self.assertNotIn("journal =", odd)
            self.assertNotIn("pages =", odd)

    def test_legacy_id_is_normalised(self):
        text = bibtex.entry(paper(arxiv_id="math.DG/0211159v2"))
        self.assertIn("eprint = {math/0211159}", text)
        self.assertIn("url = {https://arxiv.org/abs/math/0211159}", text)

    def test_not_published_stays_misc(self):
        for ref in ("to appear in Ann. of Math.", "Submitted to Invent. Math.", "Annals of Mathematics"):
            text = bibtex.entry(paper(journal_ref=ref))
            self.assertTrue(text.startswith("@misc{"), ref)
            self.assertIn(f"note = {{{ref}}}", text)
        only_doi = bibtex.entry(paper(doi="10.1007/s00222-020-00001-1"))
        self.assertTrue(only_doi.startswith("@misc{"))  # DOI sans revue : pas de @article sans « journal »
        self.assertIn("doi = {10.1007/s00222-020-00001-1}", only_doi)
        self.assertNotIn("10.48550", bibtex.entry(paper(doi="10.48550/arXiv.2601.00001")))

    def test_semantic_scholar_journal_is_used(self):
        text = bibtex.entry(paper(), s2=("Adv. Math. 400, 108-150", "10.1016/j.aim.2022.1"))
        self.assertTrue(text.startswith("@article{"))
        self.assertIn("journal = {Adv. Math.}", text)
        self.assertIn("pages = {108--150}", text)
        self.assertIn("doi = {10.1016/j.aim.2022.1}", text)

    def test_local_pdf_without_arxiv(self):
        text = bibtex.entry(paper(id="pdf-ab", arxiv_id="", published=None, doi="10.1/x"))
        self.assertTrue(text.startswith("@misc{lovelacetitle,"))
        for absent in ("eprint", "archivePrefix", "year", "url"):
            self.assertNotIn(absent, text)

    def test_keys_stable_and_unique(self):
        self.assertEqual(bibtex.cite_key(paper(authors=["Émile Picard"], title="The $L^2$ Théorème of things")), "picard2026theoreme")
        self.assertEqual(bibtex.cite_key(paper(authors=[], title="$x$ on the")), "anon2026")
        a, b, c = paper(id="1"), paper(id="2"), paper(id="3", authors=["Ada Lovelace"])
        self.assertEqual(bibtex.unique_keys([a, b, c]), ["lovelace2026title", "lovelace2026titlea", "lovelace2026titleb"])
        text = bibtex.export([a, b, a])  # même article deux fois : une entrée
        self.assertEqual(text.count("@misc{"), 2)
        self.assertEqual(bibtex.export([]), "")


class Routes(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, journals.BACKGROUND)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, journals.BACKGROUND = tmp / "test.db", tmp, False
        db.init()
        for pid, aid, title in (("2601.00001", "2601.00001", "First"), ("math_0404494", "math/0404494", "Second")):
            db.save_paper({**paper(id=pid, arxiv_id=aid, title=title), "abstract": "", "categories": ["math.DG"]})
        self.client = TestClient(main.app, base_url=BASE)

    def tearDown(self):
        db.DB_PATH, db.LIBRARY, journals.BACKGROUND = self.saved

    def test_paper_and_unknown(self):
        self.assertIn("@misc{lovelace2026first,", self.client.get("/api/papers/2601.00001/bibtex").json()["bibtex"])
        self.assertEqual(self.client.get("/api/papers/9999.99999/bibtex").status_code, 404)
        self.assertEqual(self.client.get("/api/papers/bad id/bibtex").status_code, 400)

    def test_journal_known_from_semantic_scholar_table(self):
        journals.store("ArXiv:2601.00001", {"journal": {"name": "Adv. Math.", "volume": "9", "pages": "1-2"}, "externalIds": {}})
        text = self.client.get("/api/papers/2601.00001/bibtex").json()["bibtex"]
        self.assertTrue(text.startswith("@article{"))
        self.assertIn("volume = {9}", text)

    def test_selection_json_and_file(self):
        data = self.client.get("/api/bibtex", params={"ids": "2601.00001,math/0404494,0000.00000"}).json()
        self.assertEqual((data["count"], data["missing"]), (2, 1))
        self.assertIn("eprint = {math/0404494}", data["bibtex"])
        file = self.client.get("/api/bibtex.bib", params={"ids": "2601.00001"})
        self.assertIn("attachment", file.headers["content-disposition"])
        self.assertIn("@misc{", file.text)
        self.assertEqual(self.client.get("/api/bibtex", params={"ids": "0000.00000"}).status_code, 404)
        self.assertEqual(self.client.get("/api/bibtex", params={"ids": "../x"}).status_code, 404)

    def test_whole_library(self):
        data = self.client.get("/api/library/bibtex").json()
        self.assertEqual(data["count"], 2)
        self.assertIn("eprint = {math/0404494}", data["bibtex"])
        file = self.client.get("/api/library.bib")
        self.assertIn("attachment", file.headers["content-disposition"])
        self.assertEqual(file.text.count("@misc{"), 2)
        with db.connect() as conn:  # articles seulement ouverts : pas dans l'export
            conn.execute("UPDATE papers SET in_library = 0")
        self.assertEqual(self.client.get("/api/library/bibtex").status_code, 404)

    def test_folder(self):
        folder = self.client.post("/api/folders", json={"name": "Noyaux / Bergman", "papers": ["2601.00001", "math_0404494"]}).json()
        data = self.client.get(f"/api/folders/{folder['id']}/bibtex").json()
        self.assertEqual(data["count"], 2)
        file = self.client.get(f"/api/folders/{folder['id']}.bib")
        self.assertEqual(file.status_code, 200)
        self.assertIn("Noyaux", file.headers["content-disposition"])
        self.assertNotIn("/", file.headers["content-disposition"].split("filename*=")[1].replace("%2F", ""))
        empty = self.client.post("/api/folders", json={"name": "Vide"}).json()
        self.assertEqual(self.client.get(f"/api/folders/{empty['id']}.bib").status_code, 404)


if __name__ == "__main__":
    unittest.main()
