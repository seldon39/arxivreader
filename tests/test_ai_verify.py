"""Relecture vérifiée : le même assistant (modèle plus léger) tente de réfuter chaque point grave (Claude simulé)."""

import json
import unittest

from fastapi.testclient import TestClient

from app import ai, bookjob, db, main
from tests import test_ai
from tests.test_ai import STRUCTURE


class Verify(unittest.TestCase):
    setUp_paper, tearDown_paper = test_ai.AI.setUp, test_ai.AI.tearDown

    def setUp(self):
        self.setUp_paper()
        self.saved_models = ai.claude_models
        ai.claude_models = lambda: [{"value": "opus", "label": "Opus", "levels": ["low", "high"]},
                                    {"value": "sonnet", "label": "Sonnet", "levels": ["low", "high"]}]
        self.client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")

    def tearDown(self):
        ai.claude_models = self.saved_models
        self.tearDown_paper()

    def fake(self, calls):
        async def fake(system, prompt, model, image=None, pages=(), flags=None, effort=None):
            calls.append((model, effort, prompt))
            if '<review id="verification"' in prompt:
                yield "### Points to check\n1. [[confirmed]] Lemma 1.1\n2. [[uncertain]] Lemma 2\n### Withdrawn\n[[withdrawn]] 3. gap closed"
            elif '<review id="A"' in prompt:
                yield "1. **Lemma 1.1** (certain)\n3. **gap**"
            else:
                yield f"review by {model}"
        return fake

    def test_verified_review_makes_three_calls(self):
        calls = []
        ai.stream_answer = self.fake(calls)
        db.save_settings({"aiProvider": "claude", "aiModel": "opus", "aiVerifyEffort": "low"})
        lines = self.client.post("/api/papers/p1/ai", json={"task": "review", "verify": True}).text.splitlines()
        events = [json.loads(line) for line in lines]
        self.assertEqual([e["phase"] for e in events if "phase" in e], ["review", "verify", "synthesis"])
        self.assertEqual([(e["step"]["kind"], e["step"]["model"]) for e in events if "step" in e],
                         [("review", "opus"), ("verify", "sonnet")])
        # relecture seule, puis vérification (modèle léger, son niveau), puis synthèse du premier relecteur
        self.assertEqual([(model, effort) for model, effort, _ in calls], [("opus", None), ("sonnet", "low"), ("opus", None)])
        check, final = calls[1][2], calls[2][2]
        self.assertIn('<review id="A" author="another assistant">\nreview by opus', check)
        self.assertIn("try to refute each of its", check)
        self.assertIn('<review id="verification" author="another assistant">', final)
        self.assertIn('<review id="A" author="you">', final)
        done = events[-1]["exchange"]
        self.assertEqual((done["status"], done["model"]), ("done", "opus"))
        self.assertIn("[[withdrawn]] 3. gap closed", done["answer"])
        text = ai.export_text(ai.get(done["id"]), self.paper, STRUCTURE, "en")
        self.assertIn("**Confirmed** Lemma 1.1", text)
        self.assertIn("**Uncertain** Lemma 2", text)
        self.assertIn("**Withdrawn** 3. gap closed", text)

    def test_checker_choice(self):
        self.assertEqual(ai.verifier("opus", {}), ("sonnet", None))
        self.assertEqual(ai.verifier("opus", {"aiVerifyModel": "opus", "aiVerifyEffort": "high"}), ("opus", "high"))
        self.assertEqual(ai.verifier("opus", {"aiVerifyModel": "gone", "aiVerifyEffort": "max"}), ("sonnet", None))
        self.assertEqual(ai.verifier("codex", {"aiVerifyModel": "sonnet"}), ("codex", None))
        ai.claude_models = lambda: [{"value": "opus", "label": "Opus", "levels": []}]
        self.assertEqual(ai.verifier("opus", {}), ("opus", None))

    def test_request_checks(self):
        ai.stream_answer = self.fake([])
        post = lambda body: self.client.post("/api/papers/p1/ai", json=body).status_code  # noqa: E731
        self.assertEqual(post({"task": "summary", "verify": True}), 400)
        self.assertEqual(post({"task": "review", "verify": True, "second": "gemini"}), 400)
        self.assertEqual(self.client.put("/api/settings", json={"aiReviewMode": "verify", "aiVerifyModel": "sonnet",
                                                                 "aiVerifyEffort": "high"}).status_code, 200)
        self.assertEqual(self.client.put("/api/settings", json={"aiVerifyEffort": "extreme"}).status_code, 400)
        self.assertEqual(self.client.put("/api/settings", json={"aiVerifyModel": "bad name"}).status_code, 400)

    def test_checker_error_names_the_checker(self):
        async def fake(system, prompt, model, image=None, pages=(), flags=None, effort=None):
            if model == "sonnet":
                raise ai.AIError("Not logged in", "login")
            yield "review"
        ai.stream_answer = fake
        db.save_settings({"aiProvider": "claude", "aiModel": "opus"})
        events = [json.loads(line) for line in self.client.post("/api/papers/p1/ai", json={"task": "review", "verify": True}).text.splitlines()]
        error = next(e for e in events if "error" in e)
        self.assertEqual((error["error"], error["code"]), ("Claude: Not logged in", "other"))


class VerifyBook(unittest.TestCase):
    setUp_book, tearDown_book = test_ai.Chapters.setUp, test_ai.Chapters.tearDown

    def setUp(self):
        self.setUp_book()
        self.saved_models = ai.claude_models
        ai.claude_models = lambda: [{"value": "opus", "label": "Opus", "levels": []}, {"value": "sonnet", "label": "Sonnet", "levels": []}]
        self.addCleanup(bookjob.jobs.clear)

    def tearDown(self):
        ai.claude_models = self.saved_models
        self.tearDown_book()

    def test_book_job_verified(self):
        """Chaque chapitre : trois demandes (relecture, vérification, conclusion), étapes gardées."""
        import asyncio
        models = []

        async def fake(system, prompt, model, image=None, pages=(), flags=None, effort=None):
            if "<chapter_review" in prompt:
                yield "book"
                return
            models.append(model)
            yield "1. [[confirmed]] point\n### Chapter record\n- notation"
        ai.stream_answer = fake

        async def run():
            job = bookjob.start(self.paper, self.structure, "review", "opus", "sonnet", "en", fresh=True, verify=True)
            self.assertTrue(job.state()["verify"])
            await job.handle
            return job
        job = asyncio.run(run())
        self.assertEqual(job.phase, "done")
        chapters = [e for e in ai.list_for("b1") if e["target"] != ai.BOOK]
        self.assertEqual(len(chapters), 3)
        self.assertTrue(all([s["kind"] for s in e["steps"]] == ["review", "verify"] for e in chapters))
        self.assertEqual(sorted(set(models)), ["opus", "sonnet"])
        self.assertEqual(len(models), 9)

    def test_book_endpoint_checks(self):
        client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        post = lambda body: client.post("/api/papers/b1/ai/book", json=body).status_code  # noqa: E731
        self.assertEqual(post({"task": "summary", "verify": True}), 400)
        self.assertEqual(post({"task": "review", "verify": True, "second": "gemini"}), 400)


if __name__ == "__main__":
    unittest.main()
