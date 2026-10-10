"""Où sont les preuves : section à part, résultat cité, justification en une phrase (HTML LaTeXML simplifié)."""

import unittest

from bs4 import BeautifulSoup

from app.structure import extract_structure


def theorem(number, kind, label, title_extra="", body="Statement."):
    return (f'<div id="S1.Thm{number}" class="ltx_theorem ltx_theorem_{kind}"><h6 class="ltx_title ltx_title_theorem">'
            f'<span class="ltx_tag ltx_tag_theorem">{label}</span>{title_extra}.</h6>'
            f'<div class="ltx_para"><p class="ltx_p">{body}</p></div></div>')


PAGE = f"""<article>
<section id="S1" class="ltx_section"><h2 class="ltx_title">1. Results</h2>
{theorem(1, "theorem", "Theorem 1.1", body='Main, with items <span id="S1.I1">(1)</span>.')}
{theorem(2, "corollary", "Corollary 1.2")}
<div id="S1.p1" class="ltx_para"><p class="ltx_p">The following corollary is a consequence of <a class="ltx_ref" href="#S1.Thm1">Theorem 1.1</a>.</p></div>
{theorem(3, "corollary", "Corollary 1.3")}
<div id="S1.p2" class="ltx_para"><p class="ltx_p">Inspired by this, we prove the following lemma.</p></div>
{theorem(4, "lemma", "Lemma 1.4")}
{theorem(5, "theorem", "Theorem 1.5", ' (<a class="ltx_ref" href="#bib.bib1">[1, Theorem 2]</a>)')}
{theorem(6, "lemma", "Lemma 1.6")}
<div id="S1.p3" class="ltx_para"><p class="ltx_p">This follows from <a class="ltx_ref" href="#S1.Thm5">Theorem 1.5</a>.</p></div>
</section>
<section id="S2" class="ltx_section"><h2 class="ltx_title">2. Proof of <a class="ltx_ref" href="#S1.Thm1">Theorem 1.1</a></h2>
<div id="S2.p1" class="ltx_para"><p class="ltx_p">We use <a class="ltx_ref" href="#S1.Thm5">Theorem 1.5</a>.</p></div>
<section id="S2.SS1" class="ltx_subsection"><h3 class="ltx_title">Proof of <a class="ltx_ref" href="#S1.I1">(1)</a></h3>
<div id="S2.SS1.p1" class="ltx_para"><p class="ltx_p">Easy.</p></div></section>
<section id="S2.SS2" class="ltx_subsection"><h3 class="ltx_title">Proof of <a class="ltx_ref" href="#S1.Thm4">Lemma 1.4</a></h3>
<div id="S2.SS2.p1" class="ltx_para"><p class="ltx_p">Also easy.</p></div></section>
</section>
<section id="bib" class="ltx_bibliography"><h2 class="ltx_title">References</h2>
<ul><li id="bib.bib1" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[1]</span><span class="ltx_bibblock">A. B.</span></li></ul></section>
</article>"""


class Proofs(unittest.TestCase):
    def setUp(self):
        self.fragment = BeautifulSoup(PAGE, "html.parser")
        self.structure = extract_structure(self.fragment)
        self.nodes = {n["label"]: n for n in self.structure["nodes"]}

    def test_proof_sections(self):
        main = self.nodes["Theorem 1.1"]
        self.assertEqual((main["proofs"], main["proofSection"]), (["S2"], True))  # pas « Proof of (1) »
        self.assertEqual(self.nodes["Lemma 1.4"]["proofs"], ["S2.SS2"])
        self.assertIn("S1.Thm5", main["uses"])  # renvois de la section de preuve

    def test_cited_and_justified(self):
        self.assertEqual(self.nodes["Theorem 1.5"]["cited"], ["bib.bib1"])
        self.assertEqual(self.nodes["Corollary 1.2"]["justifiedBy"], None)
        self.assertEqual(self.nodes["Corollary 1.3"]["justifiedBy"], "S1.p1")
        self.assertIn("S1.Thm1", self.nodes["Corollary 1.3"]["uses"])
        self.assertEqual(self.nodes["Lemma 1.6"]["justifiedBy"], "S1.p3")
        self.assertEqual(self.nodes["Lemma 1.4"]["justifiedBy"], None)  # « we prove the following » : pas une justification

    def test_reanalysis_is_stable(self):
        again = extract_structure(BeautifulSoup(str(self.fragment), "html.parser"))
        self.assertEqual(again["nodes"], self.structure["nodes"])


if __name__ == "__main__":
    unittest.main()


class ManualProofs(unittest.TestCase):
    def analyse(self, overrides):
        fragment = BeautifulSoup(PAGE, "html.parser")
        return {n["label"]: n for n in extract_structure(fragment, overrides)["nodes"]}, fragment

    def test_overrides(self):
        nodes, fragment = self.analyse({
            "S1.Thm2": {"id": "S1.Thm2", "mode": "passage", "start": "S1.p1", "end": "S1.Thm4"},
            "S1.Thm1": {"id": "S1.Thm1", "mode": "section", "ref": "S2.SS1"},
            "S1.Thm6": {"id": "S1.Thm6", "mode": "immediate"},
            "S1.Thm9": {"id": "S1.Thm9", "mode": "admitted"},  # énoncé inconnu : ignoré
        })
        corollary = nodes["Corollary 1.2"]
        self.assertEqual((corollary["manual"], corollary["proofPassage"]), ("passage", {"start": "S1.p1", "end": "S1.Thm4"}))
        self.assertIn("S1.Thm1", corollary["uses"])  # renvois du passage
        main = nodes["Theorem 1.1"]
        self.assertEqual((main["proofs"], main["manual"]), (["S2.SS1"], "section"))
        self.assertIsNone(fragment.find(id="S2").get("data-proof-of"))  # plus rattachée
        self.assertEqual((nodes["Lemma 1.6"]["manual"], nodes["Lemma 1.6"]["justifiedBy"]), ("immediate", None))

    def test_passage_elements(self):
        from app.structure import passage_elements
        fragment = BeautifulSoup(PAGE, "html.parser")
        ids = [e["id"] for e in passage_elements(fragment, "S1.p1", "S1.Thm4")]
        self.assertEqual(ids, ["S1.p1", "S1.Thm3", "S1.p2", "S1.Thm4"])  # paragraphes internes non répétés
        self.assertEqual([e["id"] for e in passage_elements(fragment, "S1.Thm4", "S1.p1")], ["S1.Thm4"])


def proof(proof_id, body):
    return f'<div id="{proof_id}" class="ltx_proof"><h6 class="ltx_title ltx_title_proof">Proof.</h6><div class="ltx_para"><p class="ltx_p">{body}</p></div></div>'


def statement(number, kind, label):
    return theorem(number, kind, label).replace('id="S1.Thm', 'id="S3.Thm')


SEPARATED = f"""<article><section id="S3" class="ltx_section"><h2 class="ltx_title">3. Proofs at a distance</h2>
{statement(1, "lemma", "Lemma 3.1")}
{proof("S3.P1", "Direct.")}
{statement(2, "theorem", "Theorem 3.2")}
{statement(3, "remark", "Remark 3.3")}
{proof("S3.P2", 'By <a class="ltx_ref" href="#S3.Thm1">Lemma 3.1</a>.')}
{statement(4, "proposition", "Proposition 3.4")}
<div id="S3.p1" class="ltx_para"><p class="ltx_p">We comment on the hypotheses.</p></div>
{proof("S3.P3", "First argument.")}
{proof("S3.P4", 'Second argument, by <a class="ltx_ref" href="#S3.Thm1">Lemma 3.1</a>.')}
{statement(5, "lemma", "Lemma 3.5")}
{proof("S3.P5", "Easy.")}
{statement(6, "remark", "Remark 3.6")}
{proof("S3.P6", "Of the remark.")}
{statement(7, "theorem", "Theorem 3.7")}
{proof("S3.P7", "Outline of the proof.")}
</section>
<section id="S4" class="ltx_section"><h2 class="ltx_title">4. Proof of <a class="ltx_ref" href="#S3.Thm7">Theorem 3.7</a></h2>
<div id="S4.p1" class="ltx_para"><p class="ltx_p">By <a class="ltx_ref" href="#S3.Thm5">Lemma 3.5</a>.</p></div></section>
<section id="S5" class="ltx_section"><h2 class="ltx_title">5. Statements</h2>
{statement(8, "theorem", "Theorem 3.8")}
{statement(9, "theorem", "Theorem 3.9")}
</section>
<section id="S6" class="ltx_section"><h2 class="ltx_title">6. Proofs of <a class="ltx_ref" href="#S3.Thm8">Theorem 3.8</a> and <a class="ltx_ref" href="#S3.Thm9">Theorem 3.9</a></h2>
<div id="S6.p1" class="ltx_para"><p class="ltx_p">Common setting.</p></div>
<div id="S6.P1" class="ltx_proof"><h6 class="ltx_title ltx_title_proof">Proof of <a class="ltx_ref" href="#S3.Thm8">Theorem 3.8</a>.</h6><div class="ltx_para"><p class="ltx_p">Done.</p></div></div>
</section>
<section id="S7" class="ltx_section"><h2 class="ltx_title">7. Proof of <a class="ltx_ref" href="#S3.Thm9">Theorem 3.9</a>, continued</h2>
<div id="S7.p1" class="ltx_para"><p class="ltx_p">Setting.</p></div>
</section></article>"""


class SeparatedProofs(unittest.TestCase):
    def setUp(self):
        structure = extract_structure(BeautifulSoup(SEPARATED, "html.parser"))
        self.nodes = {n["label"]: n for n in structure["nodes"]}

    def test_remark_between_statement_and_proof(self):
        self.assertEqual(self.nodes["Theorem 3.2"]["proofs"], ["S3.P2"])  # pas la remarque
        self.assertIn("S3.Thm1", self.nodes["Theorem 3.2"]["uses"])
        self.assertEqual(self.nodes["Remark 3.3"]["proofs"], [])

    def test_paragraph_between_and_second_proof(self):
        self.assertEqual(self.nodes["Proposition 3.4"]["proofs"], ["S3.P3", "S3.P4"])
        self.assertIn("S3.Thm1", self.nodes["Proposition 3.4"]["uses"])

    def test_remark_with_its_own_proof(self):
        # le lemme d'avant a déjà sa preuve : celle qui suit la remarque est bien la sienne
        self.assertEqual(self.nodes["Lemma 3.5"]["proofs"], ["S3.P5"])
        self.assertEqual(self.nodes["Remark 3.6"]["proofs"], ["S3.P6"])

    def test_shared_proof_section(self):
        # « Proofs of Theorems 3.8 and 3.9 » contient une preuve propre à 3.8 : la section n'est attribuée qu'à 3.9
        self.assertEqual(self.nodes["Theorem 3.8"]["proofs"], ["S6.P1"])
        self.assertEqual(self.nodes["Theorem 3.9"]["proofs"], ["S6"])

    def test_outline_then_proof_section(self):
        self.assertEqual(self.nodes["Theorem 3.7"]["proofs"], ["S3.P7", "S4"])  # esquisse, puis la section de preuve
        self.assertIn("S3.Thm5", self.nodes["Theorem 3.7"]["uses"])
