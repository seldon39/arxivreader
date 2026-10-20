"""PDF importés depuis l'ordinateur : import, métadonnées, texte pour l'assistant, version arXiv, garde-fous."""

import pathlib
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import ai, db, export, library, localpdf, main, zotero


def make_pdf(lines, title=None):
    """PDF minimal (une page, Helvetica) contenant ces lignes de texte."""
    text = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               f"<< /Length {len(text)} >>\nstream\n{text}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    if title:
        objects.append(f"<< /Title ({title}) /Author (Ada Lovelace; Alan Turing) >>")
    out, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    start = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    info = f" /Info {len(objects)} 0 R" if title else ""
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R{info} >>\nstartxref\n{start}\n%%EOF\n"
    return out.encode("latin-1")


class LocalPdf(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.tmp / "test.db", self.tmp, self.tmp / "papers"
        db.init()
        self.client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        self.pdf = make_pdf(["Spectral asymptotics of Toeplitz operators", "Abstract",
                             "We study the eigenvalue distribution of Toeplitz operators on compact Kahler manifolds and prove",
                             "a semiclassical trace formula with remainder estimates for smooth symbols.", "1 Introduction"],
                            title="Spectral asymptotics of Toeplitz operators")

    def tearDown(self):
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.saved

    def upload(self, data, name="paper.pdf"):
        with mock.patch.object(localpdf, "arxiv_match", return_value=None):
            return self.client.post("/api/papers/pdf", content=data, headers={"Content-Type": "application/pdf", "X-Filename": name})

    def test_import_reads_metadata_and_text(self):
        answer = self.upload(self.pdf, "toeplitz%20final.pdf")
        self.assertEqual(answer.status_code, 200)
        paper = answer.json()["paper"]
        self.assertTrue(paper["id"].startswith("pdf-"))
        self.assertEqual((paper["source"], paper["arxiv_id"], paper["in_library"]), ("local-pdf", "", 1))
        self.assertEqual(paper["title"], "Spectral asymptotics of Toeplitz operators")
        self.assertEqual(paper["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertIn("eigenvalue distribution", paper["abstract"])
        self.assertEqual(paper["comment"], "toeplitz final.pdf")
        self.assertIn("[page 1]", localpdf.text(paper["id"]))
        self.assertIn("trace formula", ai._load(paper["id"])[2])  # l'assistant lit le texte du PDF
        self.assertEqual(self.client.get(f"/api/papers/{paper['id']}/pdf").status_code, 200)
        structure = self.client.get(f"/api/papers/{paper['id']}").json()["structure"]
        self.assertEqual((structure["source"], structure["nodes"], structure["bibliography"], structure["toc"]), ("local-pdf", [], [], []))
        # même fichier : le même article, pas un doublon
        self.assertEqual(self.upload(self.pdf).json()["paper"]["id"], paper["id"])
        self.assertEqual(len(db.list_papers()), 1)

    def test_guards(self):
        self.assertEqual(self.upload(b"not a pdf at all").status_code, 400)
        # le PDF n'est accepté que sur son adresse, et en application/pdf
        self.assertEqual(self.client.post("/api/papers", content=self.pdf, headers={"Content-Type": "application/pdf"}).status_code, 415)
        paper = self.upload(self.pdf).json()["paper"]
        self.assertEqual(self.client.post(f"/api/papers/{paper['id']}/refresh", json={}).status_code, 400)  # rien à lire sur arXiv
        self.assertNotIn(paper["id"], zotero._local())  # pas écrit dans Zotero sans identifiant arXiv
        self.assertNotIn("arxiv.org", export.paper_markdown(paper["id"], "fr"))  # pas de lien arXiv vide
        renamed = self.client.put(f"/api/papers/{paper['id']}/meta", json={"title": "  New  title ", "authors": ["A. B", ""]}).json()
        self.assertEqual((renamed["title"], renamed["authors"]), ("New title", ["A. B"]))

    def test_arxiv_version(self):
        paper = self.upload(make_pdf(["Some preprint", "arXiv:2305.12345v2 [math.DG] 3 Jun 2023"])).json()["paper"]
        self.assertEqual(localpdf.arxiv_match(paper, get_s2=lambda *a: self.fail("no network needed"))["arxivId"], "2305.12345")
        other = self.upload(self.pdf).json()["paper"]
        calls = []

        def fake_s2(path, params, key):
            calls.append(path)
            return {"title": "Spectral Asymptotics of Toeplitz Operators", "externalIds": {"ArXiv": "1801.00001"}}
        self.assertEqual(localpdf.arxiv_match(other, get_s2=fake_s2)["arxivId"], "1801.00001")
        self.assertIsNone(localpdf.arxiv_match(other, get_s2=lambda *a: {"title": "Something else entirely", "externalIds": {"ArXiv": "x"}}))
