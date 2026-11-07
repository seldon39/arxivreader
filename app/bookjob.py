"""Livre : relecture ou résumé de tous les chapitres en parallèle (deux à la fois), puis synthèse ou résumé d'ensemble.

Tâche de fond du serveur, une par livre et par sorte (résumé, relecture) : elle continue si l'article ou la page se
ferme ; la page suit son état (`state`) et peut l'arrêter. Chaque chapitre fini est enregistré aussitôt comme un
échange ordinaire (son résumé ou sa relecture, qu'on ouvre dans le menu des chapitres) ; un arrêt ou une erreur garde
les chapitres finis, et un nouveau lancement les reprend sans les refaire (relecture : seulement celles qui ont leur
fiche). La synthèse est un échange de plus, de cible ai.BOOK, écrite d'après les documents des chapitres."""

import asyncio
import logging
import time

from . import ai

log = logging.getLogger("arxivreader.bookjob")

PARALLEL = 2           # chapitres faits en même temps (relecture croisée : deux assistants par chapitre)
KEEP_FINISHED = 3600   # secondes pendant lesquelles l'état d'une tâche finie reste lisible (erreur montrée à la page)

jobs = {}  # (paper_id, task) -> Job ; lu et modifié seulement dans la boucle du serveur


def reusable(paper_id, task, chapter_ids):
    """Chapitre -> son dernier résumé (ou sa relecture avec fiche) terminé, qu'une nouvelle tâche reprend tel quel."""
    found = {}
    for exchange in ai.list_for(paper_id):  # dans l'ordre : le dernier l'emporte
        if (exchange["task"] == task and exchange["parent_id"] is None and exchange["status"] == "done"
                and exchange["target"] in chapter_ids and exchange["answer"] and (task != "review" or exchange.get("card"))):
            found[exchange["target"]] = exchange
    return found


class Job:
    def __init__(self, paper, structure, task, model, second, language, fresh):
        self.paper, self.structure, self.task = paper, structure, task
        self.model, self.second, self.language = model, second, language
        chapters = structure.get("chapters", [])
        done = {} if fresh else reusable(paper["id"], task, {c["id"] for c in chapters})
        self.chapters = [{"id": c["id"], "state": "reused" if c["id"] in done else "wait",
                          "exchange": done[c["id"]]["id"] if c["id"] in done else None, "seconds": None, "started": None}
                         for c in chapters]
        self.answers = {cid: e["answer"] for cid, e in done.items()}
        self.phase = "chapters"  # puis synthesis, et done, stopped ou error
        self.synthesis = {"exchange": None, "answer": ""}
        self.error = self.code = None
        self.started, self.ended = time.time(), None
        self.open = set()  # échanges créés et pas encore finis (supprimés ou clos à l'arrêt)
        self.handle = None

    @property
    def running(self):
        return self.phase in ("chapters", "synthesis")

    def state(self):
        now = time.time()
        return {"task": self.task, "phase": self.phase, "model": self.model, "second": self.second, "parallel": PARALLEL,
                "elapsed": round((self.ended or now) - self.started),
                "chapters": [{"id": c["id"], "state": c["state"], "exchange": c["exchange"], "seconds": c["seconds"],
                              "elapsed": round(now - c["started"]) if c["state"] == "running" else None}
                             for c in self.chapters],
                "synthesis": dict(self.synthesis), "error": self.error, "code": self.code}

    async def run(self):
        try:
            self.profile = await asyncio.to_thread(ai.reader_profile)
            gate = asyncio.Semaphore(PARALLEL)
            tasks = [asyncio.ensure_future(self.chapter(c, gate)) for c in self.chapters if c["state"] == "wait"]
            try:
                await asyncio.gather(*tasks)
            except BaseException:  # erreur d'un chapitre, ou arrêt : les autres s'arrêtent aussi
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            self.phase = "synthesis"
            await self.synthesise()
            self.phase = "done"
        except asyncio.CancelledError:
            self.phase = "stopped"
        except ai.AIError as error:
            self.phase, self.error, self.code = "error", str(error), error.code
        except Exception as error:  # noqa: BLE001 — la tâche de fond ne doit jamais rester « en cours »
            log.exception("book job")
            self.phase, self.error, self.code = "error", str(error) or type(error).__name__, "other"
        finally:
            self.ended = time.time()
            self.close_open()
            self.structure, self.answers = None, {}  # gardée une heure pour son état : sans le livre ni les réponses

    async def chapter(self, entry, gate):
        async with gate:
            entry["state"], entry["started"] = "running", time.time()
            exchange = ai.create(self.paper["id"], self.task, entry["id"], None, None, None, self.model)  # aussitôt suivi (arrêt)
            entry["exchange"] = exchange["id"]
            self.open.add(exchange["id"])
            try:
                answer, steps = [], []
                if self.second:  # relecture croisée du chapitre : quatre demandes (ai.cross_review)
                    async for event in ai.cross_review(self.paper, self.structure, self.model, self.second, self.language,
                                                       chapter=entry["id"]):
                        if "delta" in event:
                            answer.append(event["delta"])
                        if "step" in event:
                            steps.append(event["step"])
                else:
                    system, prompt = await asyncio.to_thread(
                        ai.build_prompt, self.paper, self.structure, self.task, language=self.language,
                        limit=ai.paper_limit(self.model), profile=self.profile, chapter=entry["id"])
                    async for text in ai.stream_answer(system, prompt, self.model):
                        answer.append(text)
            except BaseException:
                entry["state"] = "wait"
                raise
            text = "".join(answer)
            ai.finish(exchange["id"], text, "done", steps or None, card=self.task == "review")
            self.open.discard(exchange["id"])
            self.answers[entry["id"]] = text
            entry["state"], entry["seconds"] = "done", round(time.time() - entry["started"])

    async def synthesise(self):
        docs = [(c["id"], self.answers[c["id"]]) for c in self.chapters if self.answers.get(c["id"])]
        system, prompt = await asyncio.to_thread(ai.book_prompt, self.paper, self.structure, self.task, docs,
                                                 self.language, ai.paper_limit(self.model), self.profile)
        exchange = ai.create(self.paper["id"], self.task, ai.BOOK, None, None, None, self.model)
        self.synthesis["exchange"] = exchange["id"]
        self.open.add(exchange["id"])
        async for text in ai.stream_answer(system, prompt, self.model):
            self.synthesis["answer"] += text
        ai.finish(exchange["id"], self.synthesis["answer"], "done")
        self.open.discard(exchange["id"])

    def close_open(self):
        """Arrêt ou erreur : un chapitre inachevé est supprimé (rien n'en est gardé), la synthèse commencée est close
        comme une réponse interrompue."""
        for exchange_id in list(self.open):
            if exchange_id == self.synthesis["exchange"]:
                ai.finish(exchange_id, self.synthesis["answer"], "error" if self.phase == "error" else "stopped")
            else:
                ai.delete(exchange_id)
            self.open.discard(exchange_id)


def _purge():
    """Tâches finies depuis plus de KEEP_FINISHED : oubliées (de tous les livres, pas seulement celui qu'on lit)."""
    now = time.time()
    for key in [k for k, job in jobs.items() if not job.running and now - job.ended > KEEP_FINISHED]:
        del jobs[key]


def start(paper, structure, task, model, second=None, language="fr", fresh=False):
    """Lance la tâche (une seule à la fois par livre et par sorte) ; `second` : modèle du second relecteur."""
    _purge()
    key = (paper["id"], task)
    if key in jobs and jobs[key].running:
        raise ai.AIError("Already running")
    job = Job(paper, structure, task, model, second, language, fresh)
    jobs[key] = job
    job.loop = asyncio.get_running_loop()
    job.handle = asyncio.ensure_future(job.run())
    return job


def state(paper_id, task):
    _purge()
    job = jobs.get((paper_id, task))
    return job.state() if job else None


async def stop(paper_id, task):
    job = jobs.get((paper_id, task))
    if job and job.running:
        job.handle.cancel()
        await asyncio.gather(job.handle, return_exceptions=True)
    return job.state() if job else None


async def stop_all():
    """Arrêt du serveur : chaque tâche s'arrête et range ses échanges."""
    running = [job.handle for job in jobs.values() if job.running]
    for handle in running:
        handle.cancel()
    await asyncio.gather(*running, return_exceptions=True)


def forget(paper_id):
    """Article supprimé (peut-être depuis un autre fil) : sa tâche s'arrête, sans attendre, et son état est oublié ;
    le tout dans la boucle du serveur, seule à toucher `jobs`."""
    for key, job in list(jobs.items()):
        if key[0] == paper_id:
            job.loop.call_soon_threadsafe(_forget, key, job)


def _forget(key, job):
    if jobs.get(key) is job:
        del jobs[key]
    if job.running:
        job.handle.cancel()
