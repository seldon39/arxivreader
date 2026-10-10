"""Dépendances entre énoncés : renvois implicites, lemme posé dans une preuve, formules obtenues dans le texte."""

import unittest

from bs4 import BeautifulSoup

from app.structure import extract_structure


def theorem(name, kind, label, body="Statement."):
    return (f'<div id="{name}" class="ltx_theorem ltx_theorem_{kind}"><h6 class="ltx_title ltx_title_theorem">'
            f'<span class="ltx_tag ltx_tag_theorem">{label}</span>.</h6><div class="ltx_para"><p class="ltx_p">{body}</p></div></div>')


def equation(name, tag):
    return (f'<table id="{name}" class="ltx_equation ltx_eqn_table"><tr><td>x</td>'
            f'<td><span class="ltx_tag ltx_tag_equation">{tag}</span></td></tr></table>')


PAGE = f"""<article>
<section id="S1" class="ltx_section"><h2 class="ltx_title">1. Main</h2>
{theorem("T1", "theorem", "Theorem 1.1", 'Then <a class="ltx_ref" href="#E9">(9)</a> holds.')}
</section>
<section id="S2" class="ltx_section"><h2 class="ltx_title">2. Tools</h2>
{theorem("L1", "lemma", "Lemma 2.1")}
<div id="p1" class="ltx_para"><p class="ltx_p">From the above lemma, we get</p></div>
<div id="p2" class="ltx_para">{equation("E5", "(2.5)")}</div>
<div id="p3" class="ltx_para"><p class="ltx_p">In the proof of <a class="ltx_ref" href="#T1">Theorem 1.1</a> we set</p>{equation("E6", "(2.6)")}</div>
{theorem("L2", "lemma", "Lemma 2.2", 'By <a class="ltx_ref" href="#E5">(2.5)</a> and <a class="ltx_ref" href="#E6">(2.6)</a>.')}
{theorem("C1", "corollary", "Corollary 2.3")}
<div id="p4" class="ltx_para"><p class="ltx_p">The previous corollary is used below.</p></div>
</section>
<section id="S3" class="ltx_section"><h2 class="ltx_title">3. Proof of <a class="ltx_ref" href="#T1">Theorem 1.1</a></h2>
<div id="p5" class="ltx_para"><p class="ltx_p">We have</p>{equation("E9", "(3.1)")}</div>
{theorem("L3", "lemma", "Lemma 3.1", 'Using <a class="ltx_ref" href="#E9">(3.1)</a>.')}
<div id="p6" class="ltx_para"><p class="ltx_p">By the above lemma and <a class="ltx_ref" href="#L2">Lemma 2.2</a>, done.</p></div>
</section></article>"""


class Dependencies(unittest.TestCase):
    def setUp(self):
        nodes = extract_structure(BeautifulSoup(PAGE, "html.parser"))["nodes"]
        self.nodes = {n["label"]: n for n in nodes}

    def test_lemma_inside_proof_does_not_depend_on_proved_theorem(self):
        self.assertNotIn("T1", self.nodes["Lemma 3.1"]["uses"])  # (3.1) appartient à la preuve de Theorem 1.1
        self.assertIn("L3", self.nodes["Theorem 1.1"]["uses"])  # lemme posé dans la preuve, et « the above lemma »
        self.assertIn("L2", self.nodes["Theorem 1.1"]["uses"])

    def test_formula_derived_in_text(self):
        lemma = self.nodes["Lemma 2.2"]
        self.assertIn("L1", lemma["uses"])          # (2.5) obtenue « from the above lemma »
        self.assertIn("L1", lemma["weakUses"])
        self.assertNotIn("T1", lemma["uses"])       # « In the proof of Theorem 1.1 » : pas une utilisation

    def test_no_cycle(self):
        for node in self.nodes.values():
            for used in node["uses"]:
                self.assertNotIn(node["id"], [u for u in self.nodes.values() if u["id"] == used][0]["uses"])


if __name__ == "__main__":
    unittest.main()
