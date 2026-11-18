"""Corrections de l'audit des pages (navigateur) : fonctions pures de static/js essayées dans Node (si présent)."""

import json
import pathlib
import shutil
import subprocess
import unittest

from tests import jsfunction

ROOT = pathlib.Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "Node absent")
class Proposals(unittest.TestCase):
    ANSWER = 'Voici.\\n```annotations\\n[{\\"delete\\": 3}, {\\"quote\\": \\"x\\"}]\\n```'

    def parse(self, options=""):
        return jsfunction.run("ai.js", "parseProposals", f'parseProposals("{self.ANSWER}"{options}).items')

    def test_deletion_only_for_an_ask_exchange(self):
        self.assertEqual(self.parse(), [{"quote": "x"}])
        self.assertEqual(self.parse(", {deletions: false}"), [{"quote": "x"}])
        self.assertEqual(self.parse(", {deletions: true}"), [{"delete": 3}, {"quote": "x"}])


@unittest.skipUnless(shutil.which("node"), "Node absent")
class BookPolling(unittest.TestCase):
    """pollJobs et reloadExchanges de ai.js, avec de faux objets aussi stricts que les vrais pour ce qu'ils touchent."""

    SCRIPT = """
const ERROR_KEYS = {}, SECTIONS = "@sections";
const t = (key) => key, toast = (message) => toasts.push(message), toasts = [], timers = [];
const setTimeout = (fn) => { timers.push(fn); return timers.length; };
%(constants)s
class Assistant {
%(reload)s
%(poll)s
}
let reads = 0;
const api = {
  aiBook: async () => ({ summary: { phase: "done", elapsed: 5, chapters: [] } }),
  aiExchanges: async () => { if (++reads === 1) throw new Error("réseau"); return [{ id: 1 }]; },
};
const a = new Assistant();
Object.assign(a, { paper: { id: "p" }, exchanges: [], running: null, pending: {}, docPending: {}, positions: {}, isArticle: false,
  tb: (key) => key, onChange() {}, destroyed: false, jobsStamp: 0,
  jobs: { summary: { phase: "synthesis", elapsed: 1, chapters: [] } } });
a.pollJobs();
const out = {};
await timers.shift()();                       // relevé : la liste des échanges échoue
out.afterFailure = { toasts: toasts.length, jobs: a.jobs.summary.phase, retry: timers.length, exchanges: a.exchanges.length };
if (timers.length) await timers.shift()();    // relevé suivant : la liste est relue
out.afterRetry = { toasts: toasts.length, jobs: a.jobs.summary.phase, retry: timers.length, exchanges: a.exchanges.length };
console.log(JSON.stringify(out));
"""

    def test_failed_reload_is_retried_and_the_end_is_announced_once(self):
        code = self.SCRIPT % {
            "constants": jsfunction.lines_of("ai.js", "const BOOK_TASKS", "const duration"),
            "reload": jsfunction.method_source("ai.js", "reloadExchanges"),
            "poll": jsfunction.method_source("ai.js", "pollJobs"),
        }
        done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, check=True)
        result = json.loads(done.stdout)
        self.assertEqual(result["afterFailure"], {"toasts": 0, "jobs": "synthesis", "retry": 1, "exchanges": 0})
        self.assertEqual(result["afterRetry"], {"toasts": 1, "jobs": "done", "retry": 0, "exchanges": 1})


@unittest.skipUnless(shutil.which("node"), "Node absent")
class ReaderClosing(unittest.TestCase):
    """Une minuterie de défilement armée avant la fermeture du lecteur ne doit pas écraser la position enregistrée."""

    SCRIPT = """
const scrollY = 0, CSS = { escape: (s) => s }, encodePosition = (id, offset) => id + "@" + offset;
class Reader {
%(scroll)s
}
const saved = [];
const anchor = { id: "S1", closest: () => null, getBoundingClientRect: () => ({ top: 0 }) };
const reader = Object.assign(new Reader(), {
  destroyed: %(destroyed)s, pdfMode: false, article: { classList: { contains: () => false } }, proofBar: { hidden: false },
  currentAnchor: () => anchor, viewTop: () => 0, tocLinks: new Map(), anchors: [], followStatementsChapter() {},
  panel: { querySelectorAll: () => [], querySelector: () => null }, proofOwner: () => null, showProofBar() {},
  savePosition: (place) => saved.push(place),
});
reader.onScroll();
console.log(JSON.stringify(saved));
"""

    def saved(self, destroyed):
        code = self.SCRIPT % {"scroll": jsfunction.method_source("reader.js", "onScroll"), "destroyed": destroyed}
        done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    def test_late_scroll_timer_saves_nothing_after_destroy(self):
        self.assertEqual(self.saved("false"), ["S1@24"])  # témoin : un lecteur ouvert enregistre
        self.assertEqual(self.saved("true"), [])

    def test_destroy_cuts_the_saver(self):
        text = jsfunction.method_source("reader.js", "destroy")
        self.assertLess(text.index("this.positionSaver?.flush();"), text.index("this.savePosition = () => {};"))


@unittest.skipUnless(shutil.which("node"), "Node absent")
class Markdown(unittest.TestCase):
    PRELUDE = ('globalThis.window = globalThis; globalThis.document = {addEventListener() {}, querySelectorAll: () => [],'
               ' getElementById: () => null, createElement: () => ({})}; globalThis.localStorage = {getItem: () => null, setItem() {}};\n')

    def render(self, source):
        code = self.PRELUDE + f'const {{ markdownToHtml }} = await import("{(ROOT / "static/js/markdown.js").as_uri()}");\n' \
            'const refs = (s) => s.replace(/\\[(\\d+)\\]/g, \'<a class="ai-ref" href="#r$1">[$1]</a>\');\n' \
            f"console.log(JSON.stringify(markdownToHtml({json.dumps(source)}, {{ refs }})));"
        done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    def test_link_address_is_left_alone_by_emphasis_and_references(self):
        out = self.render("[t](http://x/`c`[12])")
        self.assertIn('<a href="http://x/%60c%60', out)
        self.assertNotIn("<code>", out)
        out = self.render("[t](http://x/_a_/b)")
        self.assertNotIn("<em>", out)
        self.assertIn('href="http://x/%5Fa%5F/b"', out)
        self.assertIn('<a href="http://x/a" target="_blank"', self.render("[t](http://x/a) et *fin*"))


if __name__ == "__main__":
    unittest.main()
