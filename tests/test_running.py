"""Travaux en cours : la coque Mac prévient avant de quitter (GET /api/ai/running)."""
import pathlib
import tempfile
import types
import unittest
import unittest.mock

from fastapi.testclient import TestClient

from app import ai, bookjob, db, library, main, related


class Running(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.tmp / "test.db", self.tmp, self.tmp / "papers"
        db.init()
        db.save_paper({"id": "p1", "arxiv_id": "2601.00001", "version": 1, "title": "T", "authors": ["A. B."],
                       "abstract": "", "categories": ["math.DG"], "source": "arxiv-html", "in_library": 1})
        self.client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")

    def tearDown(self):
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.saved

    def test_nothing_running(self):
        self.assertEqual(self.client.get("/api/ai/running").json(),
                         {"answers": 0, "jobs": 0, "related": 0, "count": 0})

    def test_running_answer_counts_until_finished(self):
        exchange = ai.create("p1", "ask", None, None, "Why?", None, "opus")
        self.assertEqual(self.client.get("/api/ai/running").json()["count"], 1)
        ai.finish(exchange["id"], "Because.", "done")
        self.assertEqual(self.client.get("/api/ai/running").json()["count"], 0)

    def test_book_job_counts_once_not_per_chapter(self):
        c1 = ai.create("p1", "review", "c1", None, None, None, "opus")
        c2 = ai.create("p1", "review", "c2", None, None, None, "opus")
        ai.create("p1", "ask", None, None, "Why?", None, "opus")  # question à part, hors du livre
        job = types.SimpleNamespace(running=True, open={c1["id"], c2["id"]})
        with unittest.mock.patch.dict(bookjob.jobs, {("p1", "review"): job}, clear=True):
            data = self.client.get("/api/ai/running").json()
        self.assertEqual((data["jobs"], data["answers"], data["count"]), (1, 1, 2))
        finished = types.SimpleNamespace(running=False, open=set())
        with unittest.mock.patch.dict(bookjob.jobs, {("p1", "summary"): finished}, clear=True):
            self.assertEqual(self.client.get("/api/ai/running").json()["jobs"], 0)

    def test_related_build_counts(self):
        with unittest.mock.patch.dict(related._jobs, {"a": types.SimpleNamespace(state="building"),
                                                      "b": types.SimpleNamespace(state="done")}, clear=True):
            data = self.client.get("/api/ai/running").json()
        self.assertEqual((data["related"], data["count"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
