"""Avertissement « IA » du graphe (static/js/graph.js) : profils GPT et Claude, seuils de l'étude, texte du message."""

import json
import shutil
import subprocess
import unittest

from tests import jsfunction
from tests.jsfunction import ROOT


def node(id, kind="theorem", uses=(), used_by=(), proved=False):
    return {"id": id, "label": f"{kind.capitalize()} {id}", "kind": kind, "uses": list(uses), "usedBy": list(used_by),
            "proofs": ["p"] if proved else [], "detectedProof": False}


def complete(top, bottom, kind_top="lemma", kind_bottom="theorem", proved=False):
    """Chaque énoncé du bas utilise tous ceux du haut : graphe complet biparti, croisements inévitables."""
    tops = [node(f"a{i}", kind_top, used_by=[f"b{j}" for j in range(bottom)], proved=proved) for i in range(top)]
    bottoms = [node(f"b{j}", kind_bottom, uses=[f"a{i}" for i in range(top)], proved=proved) for j in range(bottom)]
    return tops + bottoms


@unittest.skipUnless(shutil.which("node"), "Node absent")
class Profiles(unittest.TestCase):
    def profile(self, nodes, words):
        code = "\n".join([
            jsfunction.lines_of("graph.js", "const NODE_W", "const MARGIN"),
            jsfunction.lines_of("graph.js", "const SIDE_KINDS", "const SIDE_KINDS"),
            jsfunction.lines_of("graph.js", "const layouts", "const layouts"),
            jsfunction.source_of("graph.js", "layout"), jsfunction.source_of("graph.js", "cachedLayout"),
            jsfunction.source_of("graph.js", "aiProfile"),
            f"console.log(JSON.stringify(aiProfile({json.dumps(nodes)}, {json.dumps(words)})));"])
        return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)

    def test_gpt_many_lemmas_no_corollary_everything_proved(self):
        nodes = [node(f"l{i}", "lemma", proved=True) for i in range(6)] + [node(f"t{i}", proved=True) for i in range(4)]
        found = self.profile(nodes, 5000)
        self.assertEqual(found["model"], "gpt")
        self.assertEqual((found["lemmas"], found["corollaries"], found["proved"]), (0.6, 0, 1))

    def test_gpt_not_with_corollaries_or_unproved_statements(self):
        lemmas = [node(f"l{i}", "lemma", proved=True) for i in range(6)]
        self.assertIsNone(self.profile(lemmas + [node(f"c{i}", "corollary", proved=True) for i in range(4)], 5000))
        self.assertIsNone(self.profile(lemmas + [node(f"t{i}") for i in range(4)], 5000))

    def test_remarks_and_examples_do_not_count(self):
        nodes = [node(f"l{i}", "lemma", proved=True) for i in range(6)] + [node(f"t{i}", proved=True) for i in range(4)]
        self.assertEqual(self.profile(nodes + [node(f"r{i}", "remark") for i in range(10)], 5000)["model"], "gpt")

    def test_claude_crossings_and_density(self):
        # K(4,4) : 36 croisements pour 16 liens (2,25 par lien) ; 8 énoncés pour 1000 mots
        found = self.profile(complete(4, 4), 1000)
        self.assertEqual(found["model"], "claude")
        self.assertEqual(found["crossings"], 2.25)
        self.assertEqual(found["density"], 8)

    def test_claude_needs_both_the_crossings_and_the_density(self):
        self.assertIsNone(self.profile(complete(4, 4), 2000))  # 4 énoncés pour 1000 mots
        self.assertIsNone(self.profile(complete(4, 4), None))  # pas de texte : densité inconnue
        chain = [node(f"n{i}", uses=[f"n{i - 1}"] if i else [], used_by=[f"n{i + 1}"] if i < 9 else []) for i in range(10)]
        self.assertIsNone(self.profile(chain, 1000))  # 10 énoncés pour 1000 mots, aucun croisement

    def test_not_below_eight_statements(self):
        self.assertIsNone(self.profile(complete(3, 4), 500))
        self.assertIsNone(self.profile([node(f"l{i}", "lemma", proved=True) for i in range(7)], 5000))


@unittest.skipUnless(shutil.which("node"), "Node absent")
class Message(unittest.TestCase):
    def text(self, profile, language):
        stub = ("const getLanguage = () => %s;\n"
                "const t = (key, params = {}) => key + JSON.stringify(params);\n") % json.dumps(language)
        code = stub + jsfunction.source_of("graph.js", "aiWarningText") + \
            f"\nconsole.log(JSON.stringify(aiWarningText({json.dumps(profile)})));"
        return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)

    def test_claude_values_in_the_reader_language(self):
        self.assertEqual(self.text({"model": "claude", "crossings": 1.0512, "density": 6.84}, "fr"),
                         'graph.ai.claude{"crossings":"graph.ai.crossings.one{\\"n\\":\\"1,05\\"}","density":"6,8"}')
        self.assertEqual(self.text({"model": "claude", "crossings": 1.0512, "density": 6.84}, "en"),
                         'graph.ai.claude{"crossings":"graph.ai.crossings.other{\\"n\\":\\"1.05\\"}","density":"6.8"}')

    def test_gpt_percentages(self):
        self.assertEqual(self.text({"model": "gpt", "lemmas": 0.55, "corollaries": 0, "proved": 0.9}, "fr"),
                         'graph.ai.gpt{"lemmas":"55","proved":"90","corollaries":"graph.ai.noCorollary{}"}')
        self.assertIn('graph.ai.corollaries{\\"n\\":\\"5\\"}',
                      self.text({"model": "gpt", "lemmas": 0.5, "corollaries": 0.05, "proved": 0.85}, "fr"))


class Wiring(unittest.TestCase):
    def test_badge_left_of_the_full_screen_icon_in_the_panel_and_the_overlay(self):
        reader = (ROOT / "static/js/reader.js").read_text(encoding="utf-8")
        self.assertIn('h("div", { class: "graph-actions" }, profile && aiWarningBadge(profile), expand)', reader)
        self.assertIn("this.aiWarning() && aiWarningBadge(this.aiWarning()), closeButton)", reader)
        self.assertIn("html: icons.maximize, onclick: () => this.openGraphOverlay()", reader)
        self.assertIn("aiProfile(this.nodes, this.paper.words)", reader)

    def test_bubble_opens_only_on_click(self):
        badge = jsfunction.source_of("reader.js", "aiWarningBadge")
        self.assertIn('hidden: true', badge)
        self.assertIn('button.addEventListener("click"', badge)
        self.assertEqual(badge.count("bubble.hidden = false"), 1)

    @unittest.skipUnless(shutil.which("node"), "Node absent")
    def test_escape_only_for_a_bubble_one_can_see(self):
        takes = lambda *args: jsfunction.run("reader.js", "bubbleTakesEscape", f"bubbleTakesEscape({', '.join(json.dumps(a) for a in args)})")
        self.assertTrue(takes(True, True, False, False))  # bulle du panneau, rien par-dessus
        self.assertTrue(takes(True, True, True, True))  # bulle du graphe en plein écran
        self.assertFalse(takes(True, True, True, False))  # bulle du panneau restée ouverte derrière le plein écran
        self.assertFalse(takes(True, False, False, False))  # panneau fermé (caché)
        self.assertFalse(takes(False, True, False, False))  # panneau redessiné
        badge = jsfunction.source_of("reader.js", "aiWarningBadge")
        self.assertIn("if (bubbleTakesEscape(box.isConnected,", badge)

    def test_note_links_to_prooftrace(self):
        reader = (ROOT / "static/js/reader.js").read_text(encoding="utf-8")
        strings = (ROOT / "static/js/i18n.js").read_text(encoding="utf-8")
        self.assertIn('const PROOFTRACE = "2611.08412";', reader)
        self.assertIn("href: `https://arxiv.org/abs/${PROOFTRACE}`", jsfunction.source_of("reader.js", "aiWarningNote"))
        self.assertIn("méthode proche de ProofTrace, {link})", strings)
        self.assertIn("method close to ProofTrace, {link})", strings)

    def test_strings_in_both_languages(self):
        strings = (ROOT / "static/js/i18n.js").read_text(encoding="utf-8")
        for key in ("badge", "label", "claude", "gpt", "crossings.one", "crossings.other", "noCorollary", "corollaries", "note"):
            self.assertEqual(strings.count(f'"graph.ai.{key}":'), 2, key)


if __name__ == "__main__":
    unittest.main()
