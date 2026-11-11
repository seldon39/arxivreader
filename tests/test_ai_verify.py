"""Vérification d'une relecture faite (simple ou croisée), à la demande : le même assistant (modèle plus léger) tente
de réfuter chaque point grave, puis la relecture est réécrite (Claude simulé)."""

import json
import unittest

from fastapi.testclient import TestClient

from app import ai, db, main
from tests import test_ai
from tests.test_ai import STRUCTURE

REVIEW = "1. **Lemma 1.1** (certain)\n3. **gap**"


def fake(calls):
    async def answer(system, prompt, model, image=None, pages=(), flags=None, effort=None):
        calls.append((model, effort, prompt))
        if '<review id="verification"' in prompt:
            yield "### Points to check\n1. [[confirmed]] Lemma 1.1\n2. [[uncertain]] Lemma 2\n### Withdrawn\n[[withdrawn]] 3. gap closed"
        elif "try to refute each of its" in prompt:
            yield "1. **confirmed** lines 3-4\n3. **withdrawn** the gap is closed in Lemma 2"
        else:
            yield f"review by {model}"
    return answer


def review(paper_id, target=None, model="opus", status="done", answer=REVIEW, steps=None, parent=None):
    """Relecture enregistrée, comme si elle venait d'être faite."""
    exchange = ai.create(paper_id, "review", target, None, None, parent, model)
    return ai.finish(exchange["id"], answer, status, steps)


class Verify(unittest.TestCase):
    setUp_paper, tearDown_paper = test_ai.AI.setUp, test_ai.AI.tearDown

    def setUp(self):
        self.setUp_paper()
        self.saved_models = ai.claude_models
        ai.claude_models = lambda: [{"value": "opus", "label": "Opus", "levels": ["low", "high"]},
                                    {"value": "sonnet", "label": "Sonnet", "levels": ["low", "high"]}]
        self.client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        db.save_settings({"aiProvider": "claude", "aiModel": "opus"})

    def tearDown(self):
        ai.claude_models = self.saved_models
        self.tearDown_paper()

    def events(self, body):
        return [json.loads(line) for line in self.client.post("/api/papers/p1/ai", json=body).text.splitlines()]

    def test_check_makes_two_calls_and_keeps_the_review(self):
        calls = []
        ai.stream_answer = fake(calls)
        db.save_settings({"aiVerifyEffort": "low"})
        first = review("p1")
        events = self.events({"task": "review", "check": first["id"]})
        self.assertEqual([e["phase"] for e in events if "phase" in e], ["verify", "conclude"])
        self.assertEqual([(e["step"]["kind"], e["step"]["model"]) for e in events if "step" in e],
                         [("review", "opus"), ("verify", "sonnet")])
        # vérification par le modèle léger (à son niveau), puis relecture réécrite par l'assistant choisi
        self.assertEqual([(model, effort) for model, effort, _ in calls], [("sonnet", "low"), ("opus", None)])
        check, final = calls[0][2], calls[1][2]
        self.assertIn(f'<review id="A" author="another assistant">\n{REVIEW}', check)
        self.assertIn("<paper", check)  # l'article est joint : la réfutation se fait d'après le texte
        self.assertIn('<review id="verification" author="another assistant">\n1. **confirmed** lines 3-4', final)
        self.assertIn('<review id="A" author="you">', final)
        done = events[-1]["exchange"]
        self.assertEqual((done["status"], done["model"], done["task"], done["target"], done["parent_id"]),
                         ("done", "opus", "review", None, None))
        self.assertNotEqual(done["id"], first["id"])
        self.assertEqual(ai.get(first["id"])["answer"], REVIEW)  # la relecture vérifiée reste, intacte
        self.assertEqual([s["kind"] for s in ai.get(done["id"])["steps"]], ["review", "verify"])
        text = ai.export_text(ai.get(done["id"]), self.paper, STRUCTURE, "en")
        self.assertIn("**Confirmed** Lemma 1.1", text)
        self.assertIn("**Uncertain** Lemma 2", text)
        self.assertIn("**Withdrawn** 3. gap closed", text)

    def test_check_a_cross_review(self):
        """Une relecture croisée se vérifie aussi : sa synthèse est la relecture de départ."""
        calls = []
        ai.stream_answer = fake(calls)
        steps = [{"kind": "review", "model": "opus", "answer": "a"}, {"kind": "blind", "model": "codex", "answer": "b"},
                 {"kind": "compare", "model": "codex", "answer": "c"}]
        first = review("p1", answer="1. [[both]] **Lemma 1.1**", steps=steps)
        events = self.events({"task": "review", "check": first["id"]})
        done = events[-1]["exchange"]
        self.assertEqual(done["status"], "done")
        self.assertIn("1. [[both]] **Lemma 1.1**", calls[0][2])
        self.assertEqual(len(calls), 2)

    def test_checker_choice(self):
        self.assertEqual(ai.verifier("opus", {}), ("sonnet", None))
        self.assertEqual(ai.verifier("opus", {"aiVerifyModel": "opus", "aiVerifyEffort": "high"}), ("opus", "high"))
        self.assertEqual(ai.verifier("opus", {"aiVerifyModel": "gone", "aiVerifyEffort": "max"}), ("sonnet", None))
        self.assertEqual(ai.verifier("codex", {"aiVerifyModel": "sonnet"}), ("codex", None))
        ai.claude_models = lambda: [{"value": "opus", "label": "Opus", "levels": []}]
        self.assertEqual(ai.verifier("opus", {}), ("opus", None))

    def test_request_checks(self):
        ai.stream_answer = fake([])
        post = lambda body: self.client.post("/api/papers/p1/ai", json=body).status_code  # noqa: E731
        good = review("p1")["id"]
        running = ai.create("p1", "review", None, None, None, None, "opus")["id"]
        summary = ai.finish(ai.create("p1", "summary", None, None, None, None, "opus")["id"], "s", "done")["id"]
        reply = review("p1", parent=good)["id"]
        empty = review("p1", answer="  ")["id"]
        db.save_paper({"id": "p2", "arxiv_id": "2601.00009", "version": 1, "title": "Other", "authors": [], "abstract": "",
                       "categories": ["math.DG"], "source": "arxiv-html", "in_library": 1})
        other = review("p2")["id"]
        self.assertEqual(post({"task": "summary", "check": good}), 400)
        self.assertEqual(post({"task": "review", "check": good, "second": "codex"}), 400)
        self.assertEqual(post({"task": "review", "check": good, "chapter": "S1"}), 400)
        for bad in (running, summary, reply, empty, other, 99999):
            self.assertEqual(post({"task": "review", "check": bad}), 400, bad)
        self.assertEqual(self.client.put("/api/settings", json={"aiReviewMode": "verify"}).status_code, 400)  # plus une sorte de relecture
        self.assertEqual(self.client.put("/api/settings", json={"aiVerifyModel": "sonnet", "aiVerifyEffort": "high"}).status_code, 200)
        self.assertEqual(self.client.put("/api/settings", json={"aiVerifyEffort": "extreme"}).status_code, 400)
        self.assertEqual(self.client.put("/api/settings", json={"aiVerifyModel": "bad name"}).status_code, 400)
        self.assertEqual(post({"task": "review", "verify": True}), 200)  # ancien champ ignoré : une relecture simple

    def test_checker_error_names_the_checker(self):
        async def answer(system, prompt, model, image=None, pages=(), flags=None, effort=None):
            if model == "sonnet":
                raise ai.AIError("Not logged in", "login")
            yield "review"
        ai.stream_answer = answer
        events = self.events({"task": "review", "check": review("p1")["id"]})
        error = next(e for e in events if "error" in e)
        self.assertEqual((error["error"], error["code"]), ("Claude: Not logged in", "other"))
        self.assertEqual(events[-1]["exchange"]["status"], "error")

    def test_cut_conclusion_is_flagged(self):
        async def answer(system, prompt, model, image=None, pages=(), flags=None, effort=None):
            if '<review id="verification"' in prompt and flags is not None:
                flags["cut"] = True
            yield "text"
        ai.stream_answer = answer
        done = self.events({"task": "review", "check": review("p1")["id"]})[-1]["exchange"]
        self.assertTrue(done["cut"])


class VerifyChapter(unittest.TestCase):
    setUp_book, tearDown_book = test_ai.Chapters.setUp, test_ai.Chapters.tearDown

    def setUp(self):
        self.setUp_book()
        self.saved_models = ai.claude_models
        ai.claude_models = lambda: [{"value": "opus", "label": "Opus", "levels": []}, {"value": "sonnet", "label": "Sonnet", "levels": []}]
        self.client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")
        db.save_settings({"aiProvider": "claude", "aiModel": "opus"})

    def tearDown(self):
        ai.claude_models = self.saved_models
        self.tearDown_book()

    def test_chapter_review_checked_in_its_place(self):
        """Relecture d'un chapitre : vérifiée sur ce chapitre, rangée avec lui, avec sa fiche (reprise par le livre)."""
        calls = []
        ai.stream_answer = fake(calls)
        first = review("b1", target="Ch2")
        events = [json.loads(line) for line in self.client.post("/api/papers/b1/ai", json={"task": "review", "check": first["id"]}).text.splitlines()]
        done = events[-1]["exchange"]
        self.assertEqual((done["status"], done["target"]), ("done", "Ch2"))
        self.assertTrue(ai.get(done["id"])["card"])
        self.assertIn("Text only in chapter two.", calls[0][2])
        self.assertIn("keep the record of the review", calls[1][2])

    def test_book_synthesis_not_checked(self):
        ai.stream_answer = fake([])
        synthesis = review("b1", target=ai.BOOK)
        self.assertEqual(self.client.post("/api/papers/b1/ai", json={"task": "review", "check": synthesis["id"]}).status_code, 400)

    def test_book_job_has_no_verify_mode(self):
        from app import bookjob
        self.assertNotIn("verify", bookjob.Job.__init__.__code__.co_varnames)


if __name__ == "__main__":
    unittest.main()
