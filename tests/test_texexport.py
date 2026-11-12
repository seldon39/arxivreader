"""Copie annotée de la source TeX (texexport.py) : texte développé avec l'origine de chaque caractère (identique à
_expand), place des énoncés et preuves dans les fichiers d'origine, placement de chaque sorte de note, sûreté des
lignes ajoutées, notes en marge (échappement, formules), copie du projet (fichiers ignorés, limites, Latin-1,
original jamais modifié), archive et routes ; avec TeX, les deux formes de la copie compilées sans erreur."""

import io
import json
import os
import pathlib
import random
import re
import shutil
import tempfile
import time
import unittest
import zipfile
from unittest import mock
from urllib.parse import quote

from fastapi.testclient import TestClient

from app import annotations, db, library, main, synctex, texexport, texfile, texsource, texstructure
from tests.test_localpdf import make_pdf
from tests.test_texfile import ARTICLE, HAS_TEX

MAIN = r"""% !TEX program = pdflatex
\documentclass{article}
\usepackage{amsthm,amsmath,amssymb}
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
\newcommand{\todo}[1]{\textbf{TODO: #1}}
\begin{document}
\section{Intro}
Text with a footnote\footnote{A footnote that
spans two lines with a highlighted phrase.} and more words.
\todo{user todo}
\input{sec/one}
\begin{figure}[h]
\centering
\fbox{Box}
\caption{A caption that
mentions the special caption phrase.}
\end{figure}
\begin{verbatim}
verbatim words inside here % not a comment
\end{verbatim}
\section{A section title that
runs over two lines}
Closing paragraph of the paper.
\end{document}
"""
ONE = r"""\section{Results}
\begin{theorem}[Main]\label{thm:main}
Let $x \in \mathbb{R}$. Then
\begin{enumerate}
\item the first claim holds;
\item the second claim holds.
\end{enumerate}
\end{theorem}
\begin{proof}
\[
  x^2 \geq 0
\]
which proves the theorem.
\end{proof}
\begin{lemma}
An unlabelled lemma with
\begin{align}
  a &= b \text{ some aligned words}
\end{align}
and text after the alignment.
\end{lemma}
\begin{proof}Short.\end{proof}
"""


def strip_notes(text):
    """Texte sans ce que la copie annotée a ajouté : blocs « % [arXivReader] … % [/arXivReader] » et lignes marquées."""
    out, inside = [], False
    for line in text.splitlines(keepends=True):
        if not inside and line.startswith("% [arXivReader]"):
            inside = True
        if inside:
            if line.startswith("% [/arXivReader]"):
                inside = False
            continue
        if line.rstrip("\r\n").endswith("% [arXivReader]"):
            continue
        out.append(line)
    return "".join(out)


def block_before(text, title):
    """Première ligne d'origine après le bloc dont le titre contient `title` (et les lignes ajoutées qui suivent)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("% [arXivReader]") and title in line)
    index = start
    while index < len(lines) and (lines[index].startswith("%") or lines[index].endswith("% [arXivReader]")):
        index += 1
    return lines[index]


def todo_before(text, words):
    """Première ligne d'origine après la note en marge qui contient `words`."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(texexport.MACRO) and words in line)
    index = start + 1
    while index < len(lines) and (lines[index].startswith("%") or lines[index].endswith("% [arXivReader]")):
        index += 1
    return lines[index]


class Setup(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.tmp / "test.db", self.tmp, self.tmp / "papers"
        db.init()
        self.build = mock.patch.object(texfile, "build_dir", lambda pid: (self.tmp / "build" / pid).mkdir(parents=True, exist_ok=True)
                                       or self.tmp / "build" / pid)
        self.build.start()
        # archives temporaires dans un dossier à part : aucune ne doit y rester (voir tearDown)
        self.temp = self.tmp / "temp"
        self.temp.mkdir()
        self.tempdir = mock.patch.object(tempfile, "tempdir", str(self.temp))
        self.tempdir.start()
        self.project = self.tmp / "my draft"
        (self.project / "sec").mkdir(parents=True)
        (self.project / "main.tex").write_text(MAIN, encoding="utf-8")
        (self.project / "sec" / "one.tex").write_text(ONE, encoding="utf-8")
        self.client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")

    def tearDown(self):
        self.tempdir.stop()
        self.build.stop()
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.saved
        left = sorted(p.name for p in self.temp.glob(f"{texexport.TEMP_PREFIX}*"))
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.assertEqual(left, [], "archive temporaire laissée")

    def import_without_tex(self):
        """Article importé sans TeX (PDF à côté du fichier) : structure.json de l'application, pas de SyncTeX."""
        (self.project / "main.pdf").write_bytes(make_pdf(["Intro", "Theorem 2.1 (Main). Let x"]))
        with mock.patch.object(texfile, "find_tool", lambda name: None):
            self.pid = texfile.import_tex(self.project / "main.tex")["paper"]["id"]
        return self.pid

    def structure(self):
        return json.loads((library.paper_dir(self.pid) / "structure.json").read_text(encoding="utf-8"))

    def save_structure(self, structure):
        (library.paper_dir(self.pid) / "structure.json").write_text(json.dumps(structure), encoding="utf-8")

    def lemma_id(self):
        return next(n["id"] for n in self.structure()["nodes"] if n["kind"] == "lemma")

    def files_of(self, mode="comments", language="fr"):
        path, name = texexport.build_zip(self.pid, mode, language)
        try:
            with zipfile.ZipFile(path) as archive:
                return name, {n: archive.read(n) for n in archive.namelist()}
        finally:
            os.unlink(path)

    def annotated(self, mode="comments", name="sec/one.tex"):
        _, files = self.files_of(mode)
        return files[f"my draft (annoté)/{name}"].decode("utf-8")

    def snapshot(self):
        return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(self.project.rglob("*")) if p.is_file()}


class Expansion(unittest.TestCase):
    """Texte développé avec origines : identique à _expand, chaque caractère venu de sa place."""

    def check(self, files, name="main.tex"):
        text, origins = texsource.expand_with_origins(files, name)
        self.assertEqual(text, texsource._expand(files, name))
        breaks = set("\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
        for index, char in enumerate(text):
            file, offset = origins.at(index)
            source = files[file][offset]
            self.assertTrue(source == char or (char == "\n" and source in breaks), (index, char, source))
        self.assertIsNone(origins.at(len(text)))
        return text, origins

    def test_projects(self):
        self.check({"main.tex": MAIN, "sec/one.tex": ONE})
        self.check({"main.tex": "a % comment\r\nb \\% kept % gone\r\n\\input{sub/x}\r\nend", "sub/x.tex": "x1\n\\input{y}\nx2",
                    "sub/y.tex": "in y\x0cff\x85nel\u2028ls % c\n"})
        self.check({"main.tex": "\\input a.tex and \\include{b}\\subfile{c}\n", "a.tex": "A\\\\%c\nA2", "b.tex": "B\n",
                    "c.tex": "\\input{main}\\input{b}C"})  # inclusion de soi et d'un fichier déjà lu : rien
        deep = {f"f{i}.tex": f"level {i} \\input{{f{i + 1}}} after {i}\n" for i in range(10)}
        deep["main.tex"] = "\\input{f0}\n"
        self.check(deep)  # au-delà de la profondeur 6 : rien
        text, origins = self.check({"main.tex": "abc\n\\input{missing}\ndef"})
        self.assertEqual(origins.at(text.index("def")), ("main.tex", 20))
        self.assertEqual(origins.first("main.tex"), 0)

    def test_random_sources(self):
        rng = random.Random(7)
        pieces = ["a", "b", " ", "%", "\\", "{", "}", "\n", "\r\n", "\r", "\x0c", "\x85", "\u2028", "\\%", "\\\\",
                  "\\input{b}", "\\input{c}", "\\input c ", "\\include{sub/d}", "é"]
        for _ in range(300):
            files = {name: "".join(rng.choice(pieces) for _ in range(rng.randint(0, 40)))
                     for name in ("main.tex", "b.tex", "c.tex", "sub/d.tex")}
            self.check(files)


class Positions(unittest.TestCase):
    """Place des énoncés, preuves et sections dans le texte reçu par extract, réécritures défaites."""

    TEXT = ARTICLE.replace(r"\begin{proof}Trivial.\end{proof}",
                           "\\begin{comment}\nhidden\n\\end{comment}\n\\iffalse not \\fi\n\\proof Trivial.\\endproof\n"
                           "\\nothing{x}\n\\textbf{Proof.} Another one. \\qed\n\\begin{pf}Third.\\end{pf}") \
        .replace(r"\usepackage{amsthm}", "\\usepackage{amsthm}\n\\newcommand{\\nothing}[1]{}")

    def test_spans_and_same_structure(self):
        places = {}
        structure = texstructure.extract(self.TEXT, positions=places)
        self.assertEqual(structure, texstructure.extract(self.TEXT))
        for start, end in places["nodes"].values():
            self.assertRegex(self.TEXT[start:end], r"^\\begin\{(lemma|theorem|proposition)\}.*\\end\{\1\}$")
        proofs = [self.TEXT[a:b] for a, b in places["proofs"].values()]
        self.assertEqual(proofs, ["\\begin{proof}See \\cite{knuth}.\\end{proof}", "\\proof Trivial.\\endproof",
                                  "\\textbf{Proof.} Another one. \\qed", "\\begin{pf}Third.\\end{pf}"])
        self.assertEqual([self.TEXT[a:a + 17] for a in places["sections"].values()], ["\\section{Results}"])

    def test_shifts(self):
        shifts = []
        out = texstructure._edit("0123456789", [(2, 4, "XYZ"), (6, 6, "+"), (8, 10, "")], shifts)
        self.assertEqual(out, "01XYZ45+67")
        shift = shifts[0]
        self.assertEqual([shift.start(i) for i in range(len(out) + 1)], [0, 1, 2, 2, 2, 4, 5, 6, 6, 7, 10])
        self.assertEqual([shift.end(i) for i in range(1, len(out) + 1)], [1, 2, 4, 4, 4, 5, 6, 6, 7, 8])


class Placement(Setup):
    def test_ids_and_lines_of_statements_and_proofs(self):
        pid = self.import_without_tex()
        plan = texexport.Plan(pid, "comments", "fr")
        ids = [n["id"] for n in self.structure()["nodes"]]
        self.assertEqual(ids[0], "tex-lbl-thm:main")
        self.assertTrue(ids[1].startswith("tex-txt-lemma-"))
        self.assertEqual(plan.span_lines("tex-lbl-thm:main")[:3], ("sec/one.tex", 1, 7))
        self.assertEqual(plan.span_lines(ids[1])[:3], ("sec/one.tex", 14, 20))
        self.assertEqual(plan.span_lines("tex-lbl-thm:main-proof-1")[:3], ("sec/one.tex", 8, 13))
        self.assertEqual(plan.span_lines(f"{ids[1]}-proof-1")[:3], ("sec/one.tex", 21, 21))
        self.assertIsNone(plan.span_lines("tex-lbl-absent"))

    def test_every_kind_placed(self):
        pid = self.import_without_tex()
        lemma = self.lemma_id()
        structure = self.structure()
        theorem = next(n for n in structure["nodes"] if n["id"] == "tex-lbl-thm:main")
        theorem.update(page=1, top=500.0)  # « à la main » : énoncé situé en haut de la page 1
        self.save_structure(structure)
        create = annotations.create
        notes = {
            "statement": create(pid, "block", {"id": "tex-lbl-thm:main"}, note="Statement note", state="review"),
            "lemma": create(pid, "block", {"id": lemma}, state="understood"),
            "proof": create(pid, "block", {"id": "tex-lbl-thm:main-proof-1"}, note="**Claude** — *non vérifié*\n\nCheck the square."),
            "footnote": create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                               quote="spans two lines with a high- lighted phrase.", color="yellow", note="In the footnote"),
            "caption": create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                              quote="mentions the special caption phrase", color="blue", author="claude", note="Caption"),
            "item": create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                           quote="the second claim holds", color="green"),
            "align": create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                            quote="some aligned words", color="pink", note="Align"),
            "verbatim": create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                               quote="verbatim words inside here", note="Verbatim"),
            "title": create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                            quote="A section title that runs over two lines", note="Title"),
            "missing": create(pid, "highlight", {"pdf": {"page": 3, "start": 0, "endPage": 3, "end": 9}},
                              quote="nowhere in this paper at all", note="Missing"),
            "html": create(pid, "highlight", {"start": {"id": "tex-lbl-thm:main", "offset": 0}, "end": {"id": "tex-lbl-thm:main", "offset": 9}},
                           quote="Let $x \\in \\mathbb{R}$. Then", note="Html"),
            "htmlNote": create(pid, "note", {"id": lemma, "version": None}, note="On the lemma"),
            "paragraph": create(pid, "note", {"id": "S1.p1", "version": None}, note="Paragraph"),
            "zone": create(pid, "highlight", {"pdf": {"page": 1, "rect": [0.1, 0.5, 0.4, 0.6]}}, color="pink", note="Zone"),
            "pin": create(pid, "note", {"pdf": {"page": 1, "top": 300, "left": 80}, "version": None}, note="Pin below"),
            "pinAbove": create(pid, "note", {"pdf": {"page": 1, "top": 700, "left": 80}, "version": None}, note="Pin above"),
            "unknown": create(pid, "block", {"id": "tex-lbl-gone"}, note="Gone"),
            "sheet": create(pid, "general", note="Reading sheet"),
            "mark": create(pid, "bookmark", {"pdf": {"page": 1, "top": 400}}, quote="Start here"),
        }
        annotations.set_proof(pid, lemma, "admitted")  # emplacement de preuve : pas une note
        create(pid, "note", {"pdf": {"page": 1, "top": 1}, "version": None}, note="")  # brouillon vide : ignoré
        entries = {e["id"]: e for e in texexport.plan_json(pid)}
        found = {key: entries.get(note["id"]) for key, note in notes.items()}
        self.assertEqual(len(entries), len(notes))
        expected = {"statement": ("exact", 1), "lemma": ("exact", 14), "proof": ("exact", 8), "item": ("exact", 5),
                    "align": ("exact", 17), "verbatim": ("exact", 18), "html": ("exact", 2), "htmlNote": ("exact", 14),
                    "zone": ("approx", 8), "pin": ("approx", 8), "footnote": ("exact", 9), "caption": ("exact", 16),
                    "title": ("exact", 21), "missing": ("none", None), "paragraph": ("none", None),
                    "pinAbove": ("none", None), "unknown": ("none", None), "sheet": ("header", None), "mark": ("header", None)}
        for key, (precision, line) in expected.items():
            self.assertEqual(found[key]["precision"], precision, key)
            if line is not None:
                self.assertEqual(found[key]["line"], line, key)
        self.assertEqual(found["verbatim"]["file"], "main.tex")
        self.assertEqual(found["unknown"]["reason"], "notFound")
        self.assertEqual(found["proof"]["kind"], "proof")
        self.assertEqual(found["proof"]["title"], "Preuve de Theorem 2.1")
        self.assertEqual(found["proof"]["who"], "Claude")
        self.assertEqual(found["proof"]["note"], "Check the square.")
        self.assertEqual(found["caption"]["who"], "Claude")
        self.assertIsNone(found["footnote"]["who"])
        # comments : chaque bloc avant sa ligne, rien d'autre de changé
        text = self.annotated()
        self.assertEqual(strip_notes(text), ONE)
        self.assertEqual(block_before(text, "Theorem 2.1 (Main) — à revoir"), r"\begin{theorem}[Main]\label{thm:main}")
        self.assertEqual(block_before(text, "Preuve de Theorem 2.1 — Claude (proposé, non vérifié)"), r"\begin{proof}")
        self.assertNotIn("**Claude**", text)
        self.assertEqual(block_before(text, "Passage surligné, page 1"), r"\item the second claim holds.")
        self.assertEqual(block_before(text, "Zone de la page 1 — emplacement approximatif"), r"\begin{proof}")
        main_text = self.annotated(name="main.tex")
        self.assertEqual(strip_notes(main_text), MAIN)
        self.assertTrue(main_text.startswith("% !TEX program = pdflatex\n% [arXivReader] Copie annotée"))
        self.assertEqual(block_before(main_text.split("\n\\begin{document}\n")[1], "Passage surligné, page 1"),
                         "spans two lines with a highlighted phrase.} and more words.")
        self.assertIn("% - Start here — page 1", main_text)
        self.assertIn("% Reading sheet", main_text)
        self.assertIn("Note non localisée : Passage surligné, page 3 (passage introuvable dans la source)", main_text)
        self.assertIn("Note non localisée : tex-lbl-gone (énoncé introuvable dans la source)", main_text)
        self.assertNotIn("\\usepackage{todonotes}", main_text)
        lines = main_text.splitlines()
        verbatim = lines.index(r"\begin{verbatim}")
        self.assertTrue(lines[verbatim - 1].startswith("% [/arXivReader]"))  # bloc avant le verbatim, pas dedans
        self.assertEqual(lines[verbatim + 1], "verbatim words inside here % not a comment")

    def test_todo_lines_are_safe(self):
        pid = self.import_without_tex()
        create = annotations.create
        create(pid, "block", {"id": "tex-lbl-thm:main"}, note="Statement note $x \\in \\mathbb{R}$")
        create(pid, "block", {"id": "tex-lbl-thm:main-proof-1"}, state="review")
        create(pid, "block", {"id": self.lemma_id()}, state="verified")  # ni note ni « à revoir » : commentaire seul
        for quote_, note in (("spans two lines with a highlighted phrase", "Footnote note"),
                             ("mentions the special caption phrase", "Caption note"),
                             ("some aligned words", "Align note"), ("the second claim holds", "Item note"),
                             ("A section title that runs over two lines", "Title note")):
            create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}}, quote=quote_, note=note)
        create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}}, quote="Closing paragraph of the paper")
        one, main_text = self.annotated("todo"), self.annotated("todo", "main.tex")
        self.assertEqual(strip_notes(one), ONE)
        self.assertEqual(strip_notes(main_text), MAIN)
        self.assertEqual(todo_before(one, "Statement note"), "Let $x \\in \\mathbb{R}$. Then")
        self.assertIn("{Theorem 2.1 (Main): Statement note $x \\in \\mathbb{R}$}", one)
        self.assertEqual(todo_before(one, "Preuve de Theorem 2.1 --- à revoir"), "\\[")  # preuve qui commence par une formule
        self.assertEqual(todo_before(one, "Align note"), r"\begin{align}")
        self.assertEqual(todo_before(one, "Item note"), r"\item the second claim holds.")
        self.assertEqual(todo_before(main_text, "Footnote note"), r"Text with a footnote\footnote{A footnote that")
        self.assertEqual(todo_before(main_text, "Caption note"), r"\begin{figure}[h]")
        self.assertEqual(todo_before(main_text, "Title note"), r"\section{A section title that")
        self.assertEqual(one.count(texexport.MACRO), 4)  # énoncé, preuve à revoir, alignement, item
        self.assertNotIn("Closing paragraph", "".join(l for l in main_text.splitlines() if l.startswith(texexport.MACRO)))
        # préambule : todonotes chargé, \todo du document gardé (sauvegardé puis remis), juste avant \begin{document}
        before = main_text.split("\n\\begin{document}\n")[0]
        self.assertIn("\\let\\todo\\relax\\usepackage{todonotes}% [arXivReader]", before)
        self.assertIn("\\newcommand{\\arxivreadertodo}[2][]{\\ifmmode\\else\\ifinner\\else\\arxivreaderTodonotes[#1]{#2}\\fi\\fi}", before)
        self.assertTrue(before.rstrip("\n").endswith("% [/arXivReader]"))
        summary = texexport.summary(pid, "todo", "fr")
        self.assertEqual(summary["todoMacro"], "\\arxivreadertodo")
        self.assertTrue(any("\\todo" in w for w in summary["warnings"]))

    def test_multiline_and_hostile_notes_stay_in_comments(self):
        pid = self.import_without_tex()
        hostile = "line 1 % not a comment end\r\nline 2 \\end{document} }}}\rline 3\u2028line 4\x0cline 5\x85\n\n\\input{evil}"
        annotations.create(pid, "block", {"id": "tex-lbl-thm:main"}, note=hostile, state="review")
        annotations.create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                           quote="the first claim\nholds % }", note="\\begin{verbatim}\nx # $ & ^ _ ~ {")
        # nom d'assistant forgé (note partagée) : \r, \x0b, \u2028 ne font pas sortir du commentaire
        annotations.create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                           quote="the second claim holds",
                           note="**X\r\\input{evil}\x0b\\def\u2028\\x · m** — *2026-01-01, 12:00*\n\nForged name")
        for mode in ("comments", "todo"):
            text = self.annotated(mode)
            self.assertEqual(strip_notes(text), ONE)
            added = [l for l in text.splitlines() if l not in ONE.splitlines()]
            for line in added:
                self.assertTrue(line.startswith("%") or (line.startswith(texexport.MACRO) and line.endswith("% [arXivReader]")), line)
            self.assertIn("% line 2 \\end{document} }}}", text)
            self.assertIn("% \\input{evil}", text)
        todo = [l for l in self.annotated("todo").splitlines() if l.startswith(texexport.MACRO)]
        self.assertIn("\\begin\\{verbatim\\}", todo[1].replace("\\textbackslash{}", "\\").replace(texexport.BREAK, "").replace(texexport.SPLIT, ""))
        self.assertIn("\\# \\$ \\& \\textasciicircum{} \\_ \\textasciitilde{} \\{", todo[1])

    def test_lost_note_and_html_paragraph_unplaced(self):
        pid = self.import_without_tex()
        note = annotations.create(pid, "block", {"id": "tex-lbl-thm:main"}, note="Lost one")
        (library.paper_dir(pid) / texfile.PREVIOUS_NOTES).write_text(json.dumps({note["uid"]: {}}), encoding="utf-8")
        gone = annotations.create(pid, "block", {"id": "tex-txt-lemma-0123456789"}, note="Gone one")
        (library.paper_dir(pid) / texfile.PREVIOUS_NOTES).write_text(json.dumps({
            note["uid"]: {}, gone["uid"]: {"on": "statement", "label": "Lemma 3.4", "statement": "An old\nlemma text " * 20,
                                           "page": 3}}), encoding="utf-8")
        entries = {e["note"]: e for e in texexport.plan_json(pid)}
        entry = entries["Lost one"]
        self.assertEqual((entry["precision"], entry["reason"]), ("none", "lost"))
        entry = entries["Gone one"]  # énoncé disparu : nommé d'après la compilation d'avant, pas par son identifiant
        self.assertEqual((entry["precision"], entry["reason"], entry["title"], entry["page"]), ("none", "lost", "Lemma 3.4", 3))
        self.assertTrue(entry["quote"].startswith("An old lemma text An old") and len(entry["quote"]) <= 120)
        _, files = self.files_of()
        self.assertIn("% [arXivReader] Note non localisée : Lemma 3.4 (perdue à la recompilation)",
                      files["my draft (annoté)/main.tex"].decode("utf-8"))

    def test_duplicate_passage_page_decides(self):
        """Passage écrit deux fois : sans SyncTeX, les pages des énoncés voisins départagent (approximatif) ; avec
        SyncTeX, la page de chaque ligne (exact)."""
        (self.project / "sec" / "one.tex").write_text(ONE.replace("and text after the alignment.", "and the first claim holds; again."),
                                                      encoding="utf-8")
        pid = self.import_without_tex()
        structure = self.structure()
        for node, page in zip(structure["nodes"], (1, 2)):
            node["page"] = page
        self.save_structure(structure)
        annotations.create(pid, "highlight", {"pdf": {"page": 2, "start": 0, "endPage": 2, "end": 9}}, quote="the first claim holds")
        entry = texexport.plan_json(pid)[0]
        self.assertEqual((entry["precision"], entry["line"]), ("approx", 19))
        sync = texexport.synctex.SyncTeX()
        sync.lines = {("sec/one.tex", 5): {1}, ("sec/one.tex", 20): {2}}
        (library.paper_dir(pid) / texfile.SYNCTEX).write_bytes(b"")
        with mock.patch.object(texexport.synctex, "load", lambda *a, **k: sync):
            entry = texexport.plan_json(pid)[0]
        self.assertEqual((entry["precision"], entry["line"]), ("exact", 19))
        # source modifiée depuis : SyncTeX ignoré, l'avertissement le dit
        future = time.time() + 100
        os.utime(self.project / "sec" / "one.tex", (future, future))
        summary = texexport.summary(pid, "comments", "fr")
        self.assertFalse(summary["synctex"])
        self.assertTrue(any("recompilez" in w for w in summary["warnings"]))


class TodoText(unittest.TestCase):
    def test_escaping_markdown_and_math(self):
        known = {"R", "ker"}
        self.assertEqual(texexport.short_tex("**Bold** and *it* `code` [link](http://x) 50% #1 a_b ~ ^ & {x} \\cmd", known),
                         "Bold and it code link 50\\% \\#1 a\\_b \\textasciitilde{} \\textasciicircum{} \\& \\{x\\} \\textbackslash{}cmd")
        self.assertEqual(texexport.short_tex("# Title\n\nPara one.\n\nPara two.", known), "Title Para one. Para two.")
        self.assertEqual(texexport.short_tex("Kept $x \\in \\R^n$ and $\\ker f$", known), "Kept $x \\in \\R^n$ and $\\ker f$")
        for bad in ("$\\unknownmacro x$", "$a # b$", "$\\begin{matrix}a\\end{matrix}$", "${a$", "$a^$", "$\\left( a$",
                    "$a \\\\ b$", "$\\section{x}$", "$α$", "$a % b$", "$$display$$"):
            out = texexport.short_tex(bad, known)
            self.assertNotRegex(out.replace("\\$", ""), r"\$", bad)  # formule échappée, jamais recopiée
        self.assertEqual(texexport.short_tex("Café « déjà » – ∑ → x", known), "Café \" déjà \" -- ? -> x")
        long = texexport.short_tex("word " * 200, known)
        self.assertTrue(long.endswith("\\dots{}") and len(long) < 320)
        cut = texexport.short_tex("x" * 290 + " $\\alpha + \\beta + \\gamma$", known)
        self.assertTrue(cut.endswith("\\dots{}") and "$" not in cut)  # formule jamais coupée

    def test_comment_block(self):
        block = texexport.comment_block({"title": "T\nwith newline", "state": "à revoir", "who": "Claude",
                                         "quote": "q " * 300, "note": "a\r\nb\n\nc"}, texexport.WORDS["fr"])
        self.assertEqual(block[0], "% [arXivReader] T with newline — à revoir — Claude (proposé, non vérifié)")
        self.assertTrue(block[1].startswith("% « q q") and block[1].endswith("… »") and len(block[1]) < 215)
        self.assertEqual(block[2:], ["% a", "% b", "%", "% c", "% [/arXivReader]"])


class Safety(unittest.TestCase):
    def test_scan(self):
        text = "\n".join([
            r"\documentclass{article}",            # 0 préambule
            r"\begin{document}",                    # 1
            r"Text \footnote{open",                 # 2
            r"still open} closed.",                 # 3 dans la note de bas de page
            r"\begin{verbatim}",                    # 4
            r"\end{document} % inside",             # 5 verbatim
            r"\end{verbatim} after",                # 6 verbatim
            r"$$ x",                                # 7
            r"y $$ \[",                             # 8 formule
            r"z \]",                                # 9 formule
            r"\begin{theorem}[A long",              # 10
            r"name] text",                          # 11 argument optionnel ouvert
            r"\begin{tabular}{c} a \\",             # 12
            r"\end{tabular} \verb|{| 50\% done",    # 13 tableau
            r"$a",                                  # 14
            r"b$ \begin{align*}",                   # 15 formule
            r"\end{align*}",                        # 16 formule
            r"% \begin{align}",                     # 17
            r"\end{document}"])                     # 18
        states = texexport.scan(text, False)
        comments = [i for i, s in enumerate(states) if not s[0]]
        todos = [i for i, s in enumerate(states) if s[1]]
        self.assertEqual(comments, [5, 6])
        self.assertEqual(todos, [2, 4, 7, 10, 12, 14, 17, 18])
        self.assertEqual(texexport._climb(states, 6, 0), 4)
        self.assertEqual(texexport._climb(states, 16, 1), 14)
        self.assertIsNone(texexport._climb(states, 1, 1))  # jamais dans le préambule
        self.assertEqual([i for i, s in enumerate(texexport.scan("a\n{b\nc}\nd", True)) if s[1]], [0, 1, 3, 4])
        # listes : rien avant le premier \item ; macros de l'auteur qui ouvrent une formule (pas leur définition)
        macros = texexport.aliases("\\newcommand{\\be}{\\begin{equation}}\\def\\ee{\\end{equation}}"
                                   "\\newcommand*\\bi[1][]{\\begin{itemize}}\\newcommand{\\bt}{\\begin{theorem}}")
        self.assertEqual(macros, {"be": ("begin", "equation"), "ee": ("end", "equation"), "bi": ("begin", "itemize")})
        text = "\n".join([r"\newcommand{\be}{\begin{equation}}",  # 0 (dans le corps : définition, pas un emploi)
                          r"\begin{enumerate}[(a)]",             # 1
                          r"\item one",                           # 2 avant le premier \item
                          r"\item two \begin{itemize}",           # 3
                          r"\item inner \end{itemize}",           # 4 avant le premier \item de la liste interne
                          r"\end{enumerate}",                     # 5
                          r"\be",                                 # 6
                          r"x \ee",                               # 7 formule
                          r"\begin{thebibliography}{9}",          # 8
                          r"\bibitem{a} A.",                      # 9 avant le premier \bibitem
                          r"\end{thebibliography}"])              # 10
        states = texexport.scan(text, True, macros)
        self.assertEqual([i for i, s in enumerate(states) if not s[1]], [2, 4, 7, 9])


class Copy(Setup):
    def test_zip_layout_skipped_files_and_originals_unchanged(self):
        (self.project / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\nbinary\x00data")
        (self.project / "main.bbl").write_text("\\begin{thebibliography}{1}\\end{thebibliography}", encoding="utf-8")
        for name in ("main.aux", "main.log", "main.synctex.gz", "main.fdb_latexmk", "notes.tex~", ".DS_Store"):
            (self.project / name).write_text("x", encoding="utf-8")
        for folder in (".git", "__MACOSX", "node_modules"):
            (self.project / folder).mkdir()
            (self.project / folder / "inside.tex").write_text("x", encoding="utf-8")
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (self.project / "linked").symlink_to(outside, target_is_directory=True)
        (self.project / "secret-link.txt").symlink_to(outside / "secret.txt")
        (self.project / "fig-link.png").symlink_to(self.project / "fig.png")
        pid = self.import_without_tex()  # écrit main.pdf (ignoré : le PDF du fichier principal)
        annotations.create(pid, "block", {"id": "tex-lbl-thm:main"}, note="A note")
        before = self.snapshot()
        summary = texexport.summary(pid, "todo", "fr")
        name, files = self.files_of("todo")
        self.assertEqual(self.snapshot(), before)  # original : mêmes octets, même date
        self.assertEqual(name, "my draft annoté.zip")
        root = "my draft (annoté)/"
        self.assertEqual(sorted(files), sorted(root + n for n in ("LISEZMOI-arXivReader.txt", "fig-link.png", "fig.png",
                                                                   "main.bbl", "main.tex", "sec/one.tex")))
        self.assertEqual(files[root + "fig.png"], (self.project / "fig.png").read_bytes())
        self.assertEqual(files[root + "fig-link.png"], (self.project / "fig.png").read_bytes())
        self.assertEqual(summary["files"], 5)
        self.assertEqual(summary["size"], sum((self.project / n).stat().st_size for n in ("fig.png", "fig-link.png", "main.bbl", "main.tex", "sec/one.tex")))
        readme = files[root + "LISEZMOI-arXivReader.txt"].decode("utf-8")
        self.assertIn("copie du projet my draft", readme)
        self.assertIn("[arXivReader]", readme)
        _, english = self.files_of("comments", "en")
        self.assertIn("my draft (annotated)/README-arXivReader.txt", english)

    def test_outside_files_limits_and_latin1(self):
        shared = self.tmp / "shared"
        shared.mkdir()
        (shared / "macros.tex").write_text("\\newcommand{\\RR}{\\mathbb{R}}\n", encoding="utf-8")
        (self.project / "sec" / "latin.tex").write_bytes("Caf\xe9 au lait: some latin words here.\r\n\\par\r\n".encode("latin-1"))
        text = MAIN.replace("\\input{sec/one}", "\\input{../shared/macros}\n\\input{sec/one}\n\\input{sec/latin}")
        (self.project / "main.tex").write_text(text, encoding="utf-8")
        pid = self.import_without_tex()
        annotations.create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                           quote="some latin words here", note="Note: café ∑ end")
        summary = texexport.summary(pid, "comments", "fr")
        self.assertTrue(any("../shared/macros.tex" in w for w in summary["warnings"]))
        self.assertTrue(any("Latin-1" in w and "sec/latin.tex" in w for w in summary["warnings"]))
        self.assertEqual(summary["exact"], 1)
        _, files = self.files_of()
        raw = files["my draft (annoté)/sec/latin.tex"]
        self.assertEqual(raw.decode("latin-1"), "% [arXivReader] Passage surligné, page 1\r\n% « some latin words here »\r\n"
                                                "% Note: café ? end\r\n% [/arXivReader]\r\nCafé au lait: some latin words here.\r\n\\par\r\n")
        self.assertNotIn("my draft (annoté)/../shared/macros.tex", files)
        with mock.patch.object(texexport, "MAX_COPY_FILES", 2):
            with self.assertRaises(texexport.ExportError) as caught:
                texexport.summary(pid, "comments", "fr")
            self.assertIn("trop volumineux", str(caught.exception))
            response = self.client.get(f"/api/papers/{pid}/tex-annotated/summary?mode=comments&lang=en")
            self.assertEqual(response.status_code, 400)
            self.assertIn("too large", response.json()["detail"])
        with mock.patch.object(texexport, "MAX_COPY_BYTES", 100):
            self.assertEqual(self.client.get(f"/api/papers/{pid}/tex-annotated?mode=todo&lang=fr").status_code, 400)

    def test_inputs_with_artefact_names_permissions_and_unreadable(self):
        """Fichiers lus par la source malgré une extension d'artefact (run.out, sup.aux de \\externaldocument, data.tmp)
        copiés ; artefacts d'un .tex du projet ignorés ; droits gardés ; fichier illisible : erreur dès le bilan."""
        text = MAIN.replace("\\begin{document}", "\\usepackage{verbatim}\n\\begin{document}\n\\verbatiminput{run.out}"
                            "\n\\externaldocument{sup}", 1)
        (self.project / "main.tex").write_text(text, encoding="utf-8")
        for name in ("run.out", "sup.aux", "sup.tex", "data.tmp", "sec/one.aux", "sec/one.log", "main.toc", "main.tex.bak"):
            (self.project / name).write_text("x\n", encoding="utf-8")
        (self.project / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.project / "build.sh").chmod(0o755)
        (self.project / "sec" / "one.tex").chmod(0o640)
        pid = self.import_without_tex()
        annotations.create(pid, "block", {"id": "tex-lbl-thm:main"}, note="A note")
        path, _ = texexport.build_zip(pid, "comments", "en")
        try:
            with zipfile.ZipFile(path) as archive:
                infos = {i.filename.split("/", 1)[1]: i for i in archive.infolist()}
        finally:
            os.unlink(path)
        self.assertEqual(sorted(infos), ["README-arXivReader.txt", "build.sh", "data.tmp", "main.tex", "run.out",
                                         "sec/one.tex", "sup.aux", "sup.tex"])
        mode = {name: (info.external_attr >> 16) & 0o777 for name, info in infos.items()}
        self.assertEqual((mode["sec/one.tex"], mode["build.sh"], mode["README-arXivReader.txt"]), (0o640, 0o755, 0o644))
        (self.project / "data.tmp").chmod(0)
        try:
            if os.access(self.project / "data.tmp", os.R_OK):
                self.skipTest("lecture toujours permise (administrateur)")
            response = self.client.get(f"/api/papers/{pid}/tex-annotated/summary?mode=comments&lang=en")
            self.assertEqual(response.status_code, 400)
            self.assertIn("cannot be read: data.tmp", response.json()["detail"])
            self.assertEqual(self.client.get(f"/api/papers/{pid}/tex-annotated?lang=fr").status_code, 400)
        finally:
            (self.project / "data.tmp").chmod(0o644)

    def test_insert_lines(self):
        self.assertEqual(texexport.insert_lines("a\nb", {0: ["x"], 2: ["z"]}, "\n"), "x\na\nb\nz\n")
        self.assertEqual(texexport.insert_lines("\ufeffa\r\nb\r\n", {0: ["x"], 2: ["z"]}, "\r\n"), "\ufeffx\r\na\r\nb\r\nz\r\n")
        self.assertEqual(texexport.insert_lines("a\rb", {1: ["x"]}, "\r"), "a\rx\rb")


class Routes(Setup):
    def test_routes(self):
        db.save_paper({"id": "2401.00001", "arxiv_id": "2401.00001", "version": 1, "title": "T", "authors": [], "abstract": "",
                       "categories": [], "primary_category": "", "published": None, "updated": None, "doi": None,
                       "journal_ref": None, "comment": "", "source": "arxiv", "in_library": 1})
        self.assertEqual(self.client.get("/api/papers/2401.00001/tex-annotated/summary").status_code, 400)
        pid = self.import_without_tex()
        annotations.create(pid, "block", {"id": "tex-lbl-thm:main"}, note="A note", state="review")
        self.assertEqual(self.client.get(f"/api/papers/{pid}/tex-annotated/summary?mode=bad").status_code, 400)
        self.assertEqual(self.client.get(f"/api/papers/{pid}/tex-annotated?lang=de").status_code, 400)
        summary = self.client.get(f"/api/papers/{pid}/tex-annotated/summary?mode=todo&lang=en").json()
        self.assertEqual({k: summary[k] for k in ("total", "exact", "approx", "unplaced", "header", "synctex", "files", "todoMacro")},
                         {"total": 1, "exact": 1, "approx": 0, "unplaced": 0, "header": 0, "synctex": False, "files": 2,
                          "todoMacro": "\\arxivreadertodo"})
        self.assertEqual(summary["byKind"], {"statement": 1})
        self.assertEqual(self.client.get(f"/api/papers/{pid}/tex-annotated/summary").json()["todoMacro"], None)
        leftovers = set(pathlib.Path(tempfile.gettempdir()).glob("arxivreader-annotated-*"))
        response = self.client.get(f"/api/papers/{pid}/tex-annotated?mode=todo&lang=fr")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertEqual(response.headers["content-disposition"],
                         f"attachment; filename=\"my draft annote.zip\"; filename*=UTF-8''{quote('my draft annoté.zip')}")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn("\\arxivreadertodo[color=orange!30,size=\\scriptsize]{Theorem 2.1 (Main) --- à revoir: A note}% [arXivReader]",
                          archive.read("my draft (annoté)/sec/one.tex").decode("utf-8"))
        self.assertEqual(set(pathlib.Path(tempfile.gettempdir()).glob("arxivreader-annotated-*")), leftovers)  # supprimé
        (self.project / "main.tex").unlink()
        response = self.client.get(f"/api/papers/{pid}/tex-annotated/summary?lang=fr")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Fichier principal introuvable", response.json()["detail"])


MATCH = "\n".join([
    r"\documentclass{article}",                                                       # 0
    r"\usepackage{amsmath,amsthm}",                                                   # 1
    r"\newtheorem{theorem}{Theorem}",                                                 # 2
    r"\newcommand{\Lc}{\mathcal L}",                                                  # 3
    r"\newcommand\Oc{\mathcal{O}}",                                                   # 4
    r"\def\vare{\varepsilon}",                                                        # 5
    r"\DeclareMathOperator{\inj}{inj}",                                               # 6
    r"\title{A title with uniquetitleword in it}",                                    # 7
    r"\author{Ada Author\thanks{Supported by the uniquegrant foundation of science.}}",  # 8
    r"\begin{document}",                                                              # 9
    r"\maketitle",                                                                    # 10
    r"\section{Operators}",                                                           # 11
    r"Let $\Lc_t = \Lc + \sum_{r \geq 1} t^r \Oc_r$ with $\vare > 0$ and $\inj^X > 0$.",  # 12
    r"We have $\exp(-\det A) \leq \sup_x f$ for all matrices here.",                 # 13
    r"The space $\mathrm{Spec}(\Delta_p) \subset \Omega$ is bounded.",               # 14
    r"\begin{table}[h]",                                                              # 15
    r"\centering",                                                                    # 16
    r"\begin{tabular}{l}",                                                            # 17
    r"x",                                                                             # 18
    r"\end{tabular}",                                                                 # 19
    r"\caption{Computing times of $b_k(x_0)$.}\label{tab:times}",                    # 20
    r"\end{table}",                                                                   # 21
    r"A short closing sentence goes right here today.",                               # 22
    r"\end{document}", ""])


class FakeSync:
    """SyncTeX simulé : pages des lignes, et les points demandés à line_at."""

    def __init__(self, lines=None, at=None):
        self.lines, self.at, self.asked = lines or {}, at, []

    def pages_of(self, file, line):
        return set(self.lines.get((file, line), ()))

    def first_line(self, page):
        return None

    def line_at(self, page, x, y):
        self.asked.append((page, x, y))
        return self.at


class Fixes(Setup):
    """Corrections après les essais sur de vrais articles : origine IA dans la note en marge, « emplacement
    approximatif » partout, recherche des passages (symboles, opérateurs, macros, fenêtres courtes, textes produits par
    LaTeX, préambule), repli sur la page après un titre de section, centre des zones, largeur des marges, détails."""

    def pdf_note(self, pid, quote, note=None, page=1, **extra):
        return annotations.create(pid, "highlight", {"pdf": {"page": page, "start": 0, "endPage": page, "end": 9}},
                                  quote=quote, note=note, **extra)

    def with_sync(self, pid, sync):
        (library.paper_dir(pid) / texfile.SYNCTEX).write_bytes(b"")
        return mock.patch.object(texexport.synctex, "load", lambda *a, **k: sync)

    def test_ai_origin_in_margin_note(self):
        pid = self.import_without_tex()
        self.pdf_note(pid, "the second claim holds", "Typo here", author="claude")
        annotations.create(pid, "block", {"id": "tex-lbl-thm:main"}, note="**Codex** — *non vérifié*\n\nCheck it")
        self.pdf_note(pid, "some aligned words", "Mine")
        for language, words in (("fr", ("Claude (proposé, non vérifié)", "Codex (proposé, non vérifié)")),
                                ("en", ("Claude (suggested, unverified)", "Codex (suggested, unverified)"))):
            entries = {e["note"]: e for e in texexport.plan_json(pid, "todo", language)}
            self.assertIn(words[0], entries["Typo here"]["short"])
            self.assertIn(words[1], entries["Check it"]["short"])
            self.assertNotIn("non vérifié", entries["Mine"]["short"])
            self.assertNotIn("unverified", entries["Mine"]["short"])
        todo = [l for l in self.annotated("todo").splitlines() if l.startswith(texexport.MACRO)]
        self.assertTrue(any("Claude (proposé, non vérifié): Typo here" in l for l in todo), todo)

    def test_every_approximate_note_says_so(self):
        pid = self.import_without_tex()
        structure = self.structure()
        lemma = self.lemma_id()
        for node in structure["nodes"]:
            node["page"] = 2 if node["id"] == lemma else 1
        self.save_structure(structure)
        create = annotations.create
        notes = {
            # passage introuvable : premier énoncé de sa page (_page_start)
            "page": self.pdf_note(pid, "nowhere in this paper at all", "Page start", page=2),
            # passage écrit deux fois, la page des énoncés voisins départage sans SyncTeX (_choose)
            "choose": self.pdf_note(pid, "claim holds", "Twice", page=1),
            # passage du HTML introuvable dans son énoncé : à l'énoncé (_html_passage)
            "html": create(pid, "highlight", {"start": {"id": "tex-lbl-thm:main", "offset": 0},
                                              "end": {"id": "tex-lbl-thm:main", "offset": 9}},
                           quote="words that are not there at all", note="Html approx"),
            "exact": self.pdf_note(pid, "some aligned words", "Exact one"),
        }
        entries = {e["id"]: e for e in texexport.plan_json(pid, "todo")}
        suffix = " — emplacement approximatif"
        for key in ("page", "choose", "html"):
            entry = entries[notes[key]["id"]]
            self.assertEqual(entry["precision"], "approx", key)
            self.assertTrue(entry["title"].endswith(suffix), (key, entry["title"]))
            self.assertEqual(entry["title"].count("approximatif"), 1)
            self.assertIn("--- emplacement approximatif", entry["short"])
        self.assertNotIn("approximatif", entries[notes["exact"]["id"]]["title"])
        page = entries[notes["page"]["id"]]  # énoncé : commentaire avant son \begin, note en marge dedans
        self.assertEqual((page["file"], page["line"], page["todoLine"]), ("sec/one.tex", 14, 15))
        text = self.annotated("todo")
        self.assertEqual(block_before(text, "Passage surligné, page 2 — emplacement approximatif"), r"\begin{lemma}")
        self.assertEqual(todo_before(text, "Page start"), r"An unlabelled lemma with")

    def test_letters_of_symbols_operators_and_macros(self):
        letters = lambda tex, macros=None: texexport.tex_letters(tex, macros=macros)[0]  # noqa: E731
        plain = texexport.plain_letters
        self.assertEqual(plain("Spec(\u2206p)"), letters(r"\mathrm{Spec}(\Delta_p)"))  # INCREMENT de pdf.js
        self.assertEqual(plain("\u2126 \u03a9"), letters(r"\Omega \Omega"))         # OHM
        self.assertEqual(plain("\u2207det"), letters(r"\nabla^{\det}"))             # ∇ ignoré des deux côtés
        self.assertEqual(plain("\u2207det"), "det")
        self.assertEqual(plain("ℓ ℏ ϵ ϕ µ ı"), letters(r"\ell \hbar \epsilon \phi \mu \imath"))
        for name in ("exp", "log", "ln", "det", "sin", "cos", "tan", "max", "min", "dim", "ker", "sup", "inf", "lim",
                     "deg", "arg", "gcd", "hom", "sinh", "cosh", "tanh", "coth", "liminf", "limsup", "arcsin"):
            self.assertEqual(letters(f"\\{name}"), name)
        self.assertEqual(letters(r"\Pr"), "pr")
        self.assertEqual(letters(r"\operatorname*{Ric} \mathrm{Spec} x"), "ricspecx")
        macros = texexport.user_macros(r"""\newcommand{\Lc}{\mathcal L}\newcommand\Oc{\mathcal{O}}
            \def\vare{\varepsilon}\renewcommand*{\CP}{\mathbb{CP}}\DeclareMathOperator*{\inj}{inj}
            \let\eps=\vare \newcommand{\norm}[1]{\|#1\|}\def\pair#1#2{(#1,#2)}\newcommand{\opt}[1][x]{#1}
            \newcommand{\a}{\b x}\newcommand{\b}{\a y}\NewDocumentCommand{\K}{}{\mathbb{K}}\newcommand{\z}[0]{z}""")
        self.assertEqual({k: macros[k] for k in ("Lc", "Oc", "vare", "CP", "inj", "eps", "K", "z")},
                         {"Lc": r"\mathcal L", "Oc": r"\mathcal{O}", "vare": r"\varepsilon", "CP": r"\mathbb{CP}",
                          "inj": "inj", "eps": r"\varepsilon", "K": r"\mathbb{K}", "z": "z"})  # \let : sens de \vare à ce moment
        self.assertFalse({"norm", "pair", "opt"} & set(macros))  # macros à arguments : pas développées
        self.assertEqual(letters(r"\Lc_t + \Oc \vare \eps \CP^1 \inj \K \norm{y} \z", macros), "ltoεεcpinjkyz")
        self.assertEqual(letters(r"\a", macros), "yx")  # définitions récursives : bornées, sans boucle
        text, where = texexport.tex_letters(r"a \Lc b", macros=macros)
        self.assertEqual((text, where), ("alb", [0, 2, 6]))  # lettres d'une macro : à la place de la commande

    def test_quote_variants(self):
        variants = texexport.quote_variants
        self.assertEqual(variants("Table 1: Computing times of bk(x0)."), ["tablecomputingtimesofbkx", "computingtimesofbkx"])
        self.assertEqual(variants("Tableau 2 : Temps de calcul des noyaux")[1], "tempsdecalculdesnoyaux")
        self.assertEqual(variants("Figure 3a. Some long caption text here")[1], "somelongcaptiontexthere")
        self.assertEqual(variants("Theorem 1.2. Let x be a real number now")[1], "letxbearealnumbernow")
        self.assertEqual(variants("Théorème 2.1 (Principal). Soit x un réel")[1], "principalsoitxunreel")
        self.assertEqual(variants("Algorithm 3 Compute the kernel coefficients")[1], "computethekernelcoefficients")
        self.assertEqual(len(variants("Remark that the quantity is computed")), 1)  # pas un numéro : rien retiré
        self.assertEqual(len(variants("Table 1: short")), 1)  # reste trop court

    def test_quotes_found_with_macros_operators_prefixes_and_short_windows(self):
        (self.project / "main.tex").write_text(MATCH, encoding="utf-8")
        pid = self.import_without_tex()
        notes = {
            "macros": self.pdf_note(pid, "Let Lt = L + ∑ r≥1 trOr with ε > 0 and injX > 0."),
            "operators": self.pdf_note(pid, "We have exp(−det A) ≤ supx f for all matrices here."),
            "symbols": self.pdf_note(pid, "The space Spec(\u2206p) ⊂ \u2126 is bounded."),
            "table": self.pdf_note(pid, "Table 1: Computing times of bk(x0)."),
            "tableau": self.pdf_note(pid, "Tableau 1 : Computing times of bk(x0)."),
            "window": self.pdf_note(pid, "short closing sentence gzq"),     # 23 lettres, fin différente
            "thanks": self.pdf_note(pid, "Supported by the uniquegrant foundation of science.", "Thanks"),
            "title": self.pdf_note(pid, "A title with uniquetitleword in it"),
        }
        entries = {e["id"]: e for e in texexport.plan_json(pid)}
        found = {key: (entries[n["id"]]["precision"], entries[n["id"]]["line"]) for key, n in notes.items()}
        self.assertEqual(found, {"macros": ("exact", 12), "operators": ("exact", 13), "symbols": ("exact", 14),
                                 "table": ("exact", 20), "tableau": ("exact", 20),
                                 "window": ("approx", 22),          # fenêtre de 18 lettres, sans SyncTeX : approximatif
                                 "thanks": ("approx", 11), "title": ("approx", 11)})  # préambule : après \maketitle
        self.assertTrue(entries[notes["thanks"]["id"]]["title"].endswith("emplacement approximatif"))
        # fenêtre courte : confirmée par la page de SyncTeX (approximative), refusée sur une autre page
        with self.with_sync(pid, FakeSync({("main.tex", 23): {1}})):
            entries = {e["id"]: e for e in texexport.plan_json(pid)}
        self.assertEqual(entries[notes["window"]["id"]]["precision"], "approx")  # le reste du passage manque
        self.assertEqual(entries[notes["window"]["id"]]["line"], 22)
        with self.with_sync(pid, FakeSync({("main.tex", 23): {2}})):
            entries = {e["id"]: e for e in texexport.plan_json(pid)}
        self.assertEqual(entries[notes["window"]["id"]]["precision"], "none")
        text = self.annotated("todo", "main.tex")  # note sur le \thanks : dans le corps, compile
        self.assertEqual(todo_before(text, "Thanks"), r"\section{Operators}")

    def test_page_fallback_after_section_heading(self):
        (self.project / "main.tex").write_text(MAIN.replace("runs over two lines}\n", "runs over two lines}\n\\label{sec:last}\n"),
                                               encoding="utf-8")
        pid = self.import_without_tex()
        structure = self.structure()
        last = next(s for s in structure["toc"] if s["title"].startswith("3"))
        results = next(s for s in structure["toc"] if "Results" in s["title"])
        last["page"], results["page"] = 3, 2
        self.save_structure(structure)
        on3 = self.pdf_note(pid, "nowhere in this paper at all", "Fallback three", page=3)
        on2 = self.pdf_note(pid, "nowhere in this paper either", "Fallback two", page=2)
        entries = {e["id"]: e for e in texexport.plan_json(pid, "todo")}
        entry = entries[on3["id"]]  # après le titre sur deux lignes et son \label : sur la page 3, pas au bas de la 2
        self.assertEqual((entry["precision"], entry["file"], entry["line"], entry["todoLine"]), ("approx", "main.tex", 24, 24))
        entry = entries[on2["id"]]
        self.assertEqual((entry["precision"], entry["file"], entry["line"]), ("approx", "sec/one.tex", 1))
        text = self.annotated("todo", "main.tex")
        self.assertEqual(todo_before(text, "Fallback three"), "Closing paragraph of the paper.")
        self.assertEqual(block_before(text, "Passage surligné, page 3"), "Closing paragraph of the paper.")

    def test_zone_uses_centre_and_first_line(self):
        pid = self.import_without_tex()
        sync = FakeSync(at=("sec/one.tex", 17))
        tall = annotations.create(pid, "highlight", {"pdf": {"page": 1, "rect": [0.1, 0.5, 0.5, 0.6]}}, note="Tall")
        flat = annotations.create(pid, "highlight", {"pdf": {"page": 1, "rect": [0.2, 0.25, 0.4, 0.26]}}, note="Flat")
        with self.with_sync(pid, sync):
            entries = {e["id"]: e for e in texexport.plan_json(pid)}
        self.assertEqual((entries[tall["id"]]["precision"], entries[tall["id"]]["line"]), ("approx", 16))
        asked = sorted(sync.asked, key=lambda a: a[2])
        width, height = 612, 792  # make_pdf
        self.assertAlmostEqual(asked[0][1], 0.3 * width)
        self.assertAlmostEqual(asked[0][2], 0.25 * height + 0.01 * height / 2)  # moitié d'une zone basse
        self.assertAlmostEqual(asked[1][1], 0.3 * width)
        self.assertAlmostEqual(asked[1][2], 0.5 * height + 14)  # zone haute : sa première ligne (14 pt sous le haut)
        self.assertEqual(entries[flat["id"]]["title"], "Zone de la page 1 — emplacement approximatif")

    def test_margin_width_and_breakable_notes(self):
        pid = self.import_without_tex()
        self.pdf_note(pid, "the second claim holds", "Note")
        preamble = self.annotated("todo", "main.tex").split("\n\\begin{document}\n")[0]
        self.assertIn("\\setlength{\\marginparwidth}{2cm}% [arXivReader]", preamble)  # avant todonotes, remplacé ensuite
        self.assertIn("\\AtBeginDocument{\\arxivreaderWidth\\maxdimen", preamble)
        self.assertIn("\\csname if@reversemargin\\endcsname", preamble)
        self.assertNotIn(texexport.OWN_WIDTH, preamble)
        self.assertNotIn("\\makeatletter", preamble)
        (self.project / "main.tex").write_text(MAIN.replace("\\begin{document}", "\\usepackage[marginparwidth=5cm]{geometry}\n\\begin{document}", 1),
                                               encoding="utf-8")
        preamble = self.annotated("todo", "main.tex").split("\n\\begin{document}\n")[0]
        self.assertNotIn("\\setlength{\\marginparwidth}{2cm}", preamble)  # largeur du document gardée (au plus)
        self.assertIn(texexport.OWN_WIDTH, preamble)
        added = preamble.split("% [arXivReader] marges")[1].splitlines()[1:]
        self.assertTrue(all(l.endswith("% [arXivReader]") or l == "% [/arXivReader]" for l in added), added)
        short = texexport.short_tex
        brk, cut = texexport.BREAK, texexport.SPLIT
        self.assertEqual(short("Prop ([ma_generalized_2008])", set()), f"Prop ([ma\\_{brk}generalized\\_{brk}2008])")
        self.assertEqual(short("création/annihilation", set()), f"création/{brk}annihilation")
        # mot ordinaire : coupures de secours toutes les 10 lettres (TeX coupe d'abord ses syllabes)
        self.assertEqual(short("supercalifragilisticexpialidocious", set()),
                         f"supercalif{cut}ragilistic{cut}expialidoc{cut}ious")
        self.assertEqual(short("intégrabilité, approximatif: skew-adjointness", set()),
                         f"intégrabil{cut}ité, approximatif: skew-adjointness")  # 12 lettres ou moins : rien
        # capitales, symboles, chiffres : tous les 4 caractères, jamais devant une ponctuation fermante
        self.assertEqual(short("MMMMMMMMMMMMMM", set()), f"MMMM{cut}MMMM{cut}MMMM{cut}MM")
        self.assertEqual(short("x============.", set()), f"x==={cut}===={cut}===={cut}=.")
        self.assertEqual(short("a/b x_y {z} short.words, here", set()), "a/b x\\_y \\{z\\} short.words, here")
        self.assertEqual(short("\\end{document}", set()), f"\\textbackslash{{}}end\\{{{brk}document\\}}")

    def test_details_hyphens_quotes_kinds_and_errors(self):
        self.assertEqual(texexport.one_line("over a com-\npact Käh-\r\nler manifold, well-known, Berezin-\nToeplitz"),
                         "over a compact Kähler manifold, well-known, Berezin- Toeplitz")
        pid = self.import_without_tex()
        self.pdf_note(pid, "the second claim\nholds", "Quoted")
        structure = self.structure()
        intro = structure["toc"][0]["id"]
        next(n for n in structure["nodes"] if n["id"] == "tex-lbl-thm:main")["proofs"] = [intro]
        self.save_structure(structure)
        annotations.create(pid, "block", {"id": intro}, note="Section used as a proof")
        summary = texexport.summary(pid, "comments", "fr")
        self.assertEqual(summary["byKind"], {"highlight": 1, "proof": 1})
        entry = next(e for e in texexport.plan_json(pid) if e["note"] == "Section used as a proof")
        self.assertEqual((entry["kind"], entry["title"], entry["file"], entry["line"]), ("proof", "Preuve de Theorem 2.1", "main.tex", 7))
        _, files = self.files_of("comments", "en")
        self.assertIn("% “the second claim holds”", files["my draft (annotated)/sec/one.tex"].decode("utf-8"))
        self.assertIn("% « the second claim holds »", self.annotated())
        for query, message in (("mode=bad&lang=fr", "Forme de copie ou langue inconnue"),
                               ("mode=bad&lang=en", "Unknown copy form or language"),
                               ("mode=todo&lang=de", "Unknown copy form or language")):
            for route in ("tex-annotated/summary", "tex-annotated"):
                response = self.client.get(f"/api/papers/{pid}/{route}?{query}")
                self.assertEqual((response.status_code, response.json()["detail"]), (400, message))

    def test_temporary_archives_always_deleted(self):
        import asyncio
        (self.project / "fig.png").write_bytes(b"\x89PNG data")  # copié tel quel (ZipFile.write)
        pid = self.import_without_tex()
        annotations.create(pid, "block", {"id": "tex-lbl-thm:main"}, note="A note")
        # écriture de l'archive en échec (disque plein…) : erreur, rien ne reste
        for method in ("writestr", "write"):
            with mock.patch.object(zipfile.ZipFile, method, side_effect=OSError(28, "No space left on device")):
                with self.assertRaises(texexport.ExportError):
                    texexport.build_zip(pid, "comments", "fr")
        with mock.patch.object(texexport, "_readme", side_effect=RuntimeError("bug")):
            with self.assertRaises(RuntimeError):
                texexport.build_zip(pid, "comments", "fr")
        self.assertEqual(list(self.temp.glob(f"{texexport.TEMP_PREFIX}*")), [])
        # réponse de la route : fichier supprimé après l'envoi, même interrompu (téléchargement annulé)
        for fails in (False, True):
            path, _ = texexport.build_zip(pid, "comments", "fr")
            response = main._TemporaryFileResponse(path, media_type="application/zip")

            async def receive():
                return {"type": "http.disconnect"}

            async def send(message):
                if fails and message["type"] == "http.response.body":
                    raise OSError("connection reset")

            scope = {"type": "http", "method": "GET", "headers": [], "path": "/"}
            try:
                asyncio.run(response(scope, receive, send))
            except Exception:  # (Starlette la change en ClientDisconnect selon les versions)
                self.assertTrue(fails)
            self.assertFalse(os.path.exists(path), fails)
        # nom de l'archive mal formé après sa construction : supprimée aussi
        with mock.patch.object(main.unicodedata, "normalize", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                main.tex_annotated(pid, "comments", "fr")
        # archives oubliées (application arrêtée pendant un envoi) : supprimées au-delà de 6 h
        old, recent, other = (self.temp / f"{texexport.TEMP_PREFIX}old.zip", self.temp / f"{texexport.TEMP_PREFIX}new.zip",
                              self.temp / "other-old.zip")
        for path in (old, recent, other):
            path.write_bytes(b"x")
        past = time.time() - 7 * 3600
        os.utime(old, (past, past))
        os.utime(other, (past, past))
        path, _ = texexport.build_zip(pid, "comments", "fr")
        os.unlink(path)
        self.assertEqual(sorted(p.name for p in self.temp.iterdir()), sorted([recent.name, other.name]))
        recent.unlink()

    def test_macros_provide_let_size_and_silent_commands(self):
        """\\providecommand lu (sans écraser une définition) ; \\let pris au moment de la définition ; macros développées
        bornées en taille ; couleurs, compteurs, longueurs et espaces des macros : aucune lettre."""
        macros = texexport.user_macros(r"\providecommand{\Zz}{zz}\providecommand*{\Yy}{yy}\newcommand{\Xx}{a}"
                                       r"\providecommand{\Xx}{b}\providecommand{\phi}{q}\ProvideDocumentCommand{\Ww}{}{ww}")
        self.assertEqual(macros, {"Zz": "zz", "Yy": "yy", "Xx": "a", "Ww": "ww"})
        swap = texexport.user_macros(r"\let\temp\phi\let\phi\varphi\let\varphi\temp")
        self.assertEqual(texexport.tex_letters(r"The map $\phi$ sends $\varphi$", macros=swap)[0], "themapφsendsφ")
        loop = texexport.user_macros(r"\renewcommand{\phi}{\phi}\def\a{\a\a}")
        self.assertEqual(texexport.tex_letters(r"\phi \a", macros=loop)[0], "φ")  # récursion : le sens de LaTeX
        fan = texexport.user_macros("\\def\\x{" + "\\y" * 140 + "}\\def\\y{" + "\\z" * 140 + "}\\def\\z{" + "a" * 290 + "}")
        start = time.monotonic()
        letters = texexport.tex_letters("\\x " * 1000, macros=fan)[0]
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(len(letters), 1000 * texexport.MACRO_LETTERS)
        quote = texexport.plain_letters("We prove that the operator is bounded")
        for preamble, body in ((r"\newcommand{\red}{\color{red}}", r"We prove \red that the operator is bounded"),
                               (r"\newcommand{\ra}{\textcolor{red}}", r"We prove \ra{that} the operator is bounded"),
                               (r"\newcommand{\sa}{\setcounter{equation}{0}}", r"We prove \sa that the operator is bounded"),
                               (r"\newcommand{\sep}{\hskip 1em plus 2fill}", r"We prove\sep that the operator is bounded"),
                               ("", r"We \kern-3.5pt prove \setlength{\parindent}{0pt}that the \definecolor{c}{rgb}{1,0,0}"
                                    r"operator \rule[1pt]{2cm}{3pt}is \colorbox{yellow}{bounded}")):
            finder = texexport.Finder(preamble + "\n\\begin{document}\n" + body, len(preamble) + 1,
                                      texexport.user_macros(preamble))
            self.assertTrue(finder.candidates(quote, windows=False), (preamble, body))

    def test_title_before_short_windows_and_page_check(self):
        """Passage entier du titre (préambule) avant les fenêtres courtes du corps ; passage unique sur une autre page
        que celle de la note (SyncTeX) : approximatif."""
        (self.project / "main.tex").write_text("\n".join([
            r"\documentclass{article}",                                   # 0
            r"\title{Toeplitz operators on the unit sphere}",              # 1
            r"\begin{document}",                                          # 2
            r"\maketitle",                                                # 3
            r"\begin{abstract}",                                          # 4
            r"We study Toeplitz operators acting on Hardy spaces.",        # 5
            r"\end{abstract}",                                            # 6
            r"Another uniquely placed sentence for the page check here.",  # 7
            r"\end{document}", ""]), encoding="utf-8")
        pid = self.import_without_tex()
        title = self.pdf_note(pid, "Toeplitz operators on the unit sphere", "Title note")
        here = self.pdf_note(pid, "Another uniquely placed sentence for the page check", "Right page", page=2)
        there = self.pdf_note(pid, "Another uniquely placed sentence for the page check", "Wrong page", page=5)
        with self.with_sync(pid, FakeSync({("main.tex", 6): {1}, ("main.tex", 8): {2}})):
            entries = {e["id"]: e for e in texexport.plan_json(pid)}
        found = {key: (entries[n["id"]]["precision"], entries[n["id"]]["line"])
                 for key, n in (("title", title), ("here", here), ("there", there))}
        self.assertEqual(found, {"title": ("approx", 4), "here": ("exact", 7), "there": ("approx", 7)})
        self.assertTrue(entries[there["id"]]["title"].endswith("— emplacement approximatif"))

    def test_unplaced_note_drops_approximate_suffix(self):
        plan = mock.Mock(words=texexport._words("fr"))
        entry = {"title": "Zone de la page 1 — emplacement approximatif", "precision": "approx", "todoLine": 3,
                 "statement": True}
        texexport.Plan._unplace(plan, entry, "outside")
        self.assertEqual((entry["title"], entry["precision"], entry["reason"], entry["todoLine"]),
                         ("Zone de la page 1", "none", "outside", None))

    def test_documentclass_on_the_line_of_begin_document(self):
        (self.project / "main.tex").write_text("\\documentclass{article}\\begin{document}\n"
                                               "Some uniquely placed words in the text.\n\\end{document}\n", encoding="utf-8")
        pid = self.import_without_tex()
        self.pdf_note(pid, "Some uniquely placed words in the text", "Shared line note")
        summary = texexport.summary(pid, "todo", "fr")
        self.assertIn(texexport._words("fr")["warn"]["sharedLine"], summary["warnings"])
        text = self.annotated("todo", "main.tex")
        self.assertNotIn("todonotes}%", text)
        self.assertNotIn(texexport.MACRO + "[", text)
        self.assertIn("% Shared line note", text)
        self.assertEqual(strip_notes(text), (self.project / "main.tex").read_text(encoding="utf-8"))


@unittest.skipUnless(HAS_TEX and texfile.find_tool("latexmk"), "TeX (latexmk) n'est pas installé")
class Compiled(Setup):
    """Copie annotée compilée dans les deux formes : aucune erreur de LaTeX ; commentaires : même texte que le PDF
    d'origine ; notes en marge : le texte des notes est dans le PDF."""

    def pdf_text(self, path):
        import pypdf
        return [page.extract_text() for page in pypdf.PdfReader(str(path)).pages]

    def compile_copy(self, mode, patch=None, with_log=False):
        """Copie annotée compilée : texte de chaque page (et le journal) ; `patch` change le texte du fichier principal
        de la copie avant la compilation."""
        _, files = self.files_of(mode)
        folder = self.tmp / f"copy-{mode}"
        for name, data in files.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        main_file = folder / "my draft (annoté)" / "main.tex"
        if patch:
            main_file.write_text(patch(main_file.read_text(encoding="utf-8")), encoding="utf-8")
        out = self.tmp / f"out-{mode}"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        pdf, log, tool = texfile.compile_pdf(main_file, out, "pdflatex", False, [])
        self.assertIsNotNone(pdf, log[-2000:])
        self.assertEqual(texfile._log_report(log)[0], [], log[-3000:])
        self.assertNotRegex(log, r"(?m)^! ")
        return (self.pdf_text(pdf), log) if with_log else self.pdf_text(pdf)

    def test_both_forms_compile(self):
        result = texfile.import_tex(self.project / "main.tex")
        self.pid = pid = result["paper"]["id"]
        self.assertTrue((library.paper_dir(pid) / texfile.SYNCTEX).is_file())
        original = self.pdf_text(library.paper_dir(pid) / "paper.pdf")
        lemma = self.lemma_id()
        create = annotations.create
        create(pid, "block", {"id": "tex-lbl-thm:main"}, note="Gamma statement note with $x^2 \\geq 0$", state="review")
        create(pid, "block", {"id": lemma}, note="Delta unlabelled lemma note")
        create(pid, "block", {"id": "tex-lbl-thm:main-proof-1"}, note="**Claude** — *non vérifié*\n\nEpsilon proof note")
        create(pid, "general", note="Reading sheet % $ {")
        for quote_, note in (("spans two lines with a highlighted phrase", "Zeta footnote note"),
                             ("mentions the special caption phrase", "Eta caption note 50% & #"),
                             ("some aligned words", "Theta align note"), ("the second claim holds", "Iota item note"),
                             ("verbatim words inside here", "Kappa verbatim note"),
                             ("A section title that runs over two lines", "Lambda title note")):
            create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}}, quote=quote_, note=note, color="green")
        sync = synctex.load(library.paper_dir(pid) / texfile.SYNCTEX, self.project, detail_pages=(1,))
        align = next(box for box in sync.boxes[1] if any(sync.inputs.get(l[0]) == "sec/one.tex" and l[1] == 17 for l in box[7]))
        y = (align[1] - align[3] / 2) / synctex.SP_PER_BP  # depuis le haut
        create(pid, "highlight", {"pdf": {"page": 1, "rect": [0.3, y / 792, 0.6, y / 792 + 0.02]}}, note="Mu zone note")
        create(pid, "note", {"pdf": {"page": 1, "top": 792 - y, "left": 200}, "version": None}, note="Nu pinned note")
        entries = texexport.plan_json(pid, "todo")
        zone = next(e for e in entries if e["kind"] == "zone")
        self.assertEqual((zone["precision"], zone["file"], zone["line"], zone["todoLine"]), ("approx", "sec/one.tex", 16, 16))
        self.assertTrue(all(e["precision"] in ("exact", "approx", "header") for e in entries), entries)
        before = self.snapshot()
        commented = self.compile_copy("comments")
        self.assertEqual(commented, original)
        margin = texexport.plain_letters(" ".join(self.compile_copy("todo")))
        self.assertEqual(self.snapshot(), before)
        for word in ("Gamma statement", "Delta unlabelled", "Epsilon proof", "Zeta footnote", "Eta caption", "Theta align",
                     "Iota item", "Kappa verbatim", "Lambda title", "Mu zone", "Nu pinned", "TODO: user todo"):
            self.assertIn(texexport.plain_letters(word), margin, word)

    def layout(self, text):
        """Fichier principal qui écrit dans le journal la largeur des notes en marge et la place libre à droite."""
        return re.sub(r"(?m)^\\begin\{document\}", lambda m: "\\AtBeginDocument{\\typeout{LAYOUT=\\the\\marginparwidth;"
                      "\\the\\paperwidth;\\the\\hoffset;\\the\\oddsidemargin;\\the\\textwidth;\\the\\marginparsep}}\n"
                      "\\begin{document}", text, count=1)

    def margins(self, log):
        values = [float(v.rstrip("pt")) for v in re.search(r"LAYOUT=([^\n]*)", log).group(1).split(";")]
        width, paper, hoffset, odd, text, sep = values
        return width, paper - 72.27 - hoffset - odd - text - sep  # largeur des notes, place libre (pt)

    def margin_document(self, geometry, extra=""):
        """Article de deux pages (la seconde commence par une section) ; notes : passage introuvable de la page 2, note de
        Claude avec des mots longs. Rend (textes des pages en lettres, largeur des notes, place libre) en mode « todo »."""
        (self.project / "main.tex").write_text("\n".join([
            r"\documentclass{article}",
            f"\\usepackage[{geometry}]{{geometry}}{extra}",
            r"\begin{document}",
            r"\section{First}",
            r"Opening words of the paper on the first page.",
            r"\newpage",
            r"\section{Second part}",
            r"\label{sec:second}",
            r"Words of the second page only.",
            r"\end{document}", ""]), encoding="utf-8")
        self.pid = pid = texfile.import_tex(self.project / "main.tex")["paper"]["id"]
        annotations.create(pid, "highlight", {"pdf": {"page": 2, "start": 0, "endPage": 2, "end": 9}},
                           quote="nowhere in the paper", note="Omicron fallback note")
        annotations.create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                           quote="Opening words of the paper", author="claude",
                           note="Pi note ([ma_generalized_2008]) création/annihilation \\end{document} "
                                "supercalifragilisticexpialidocious MMMMMMMMMMMMMMMMMMMMMMMM xxxxx========================= "
                                "Wohlgeformtheitsbedingungen https://arxiv.org/abs/2401.12345v2")
        entry = next(e for e in texexport.plan_json(pid, "todo") if e["note"] == "Omicron fallback note")
        self.assertEqual((entry["precision"], entry["line"]), ("approx", 8))  # après \section{Second part} et son \label
        pages, log = self.compile_copy("todo", patch=self.layout, with_log=True)
        width, room = self.margins(log)
        return [texexport.plain_letters(p) for p in pages], width, room, log

    def test_narrow_margins_page_fallback_and_ai_origin(self):
        """Marges étroites (geometry margin=2.6cm) : notes dans la page, mots longs coupés ; repli sur une page qui
        commence par une section : la note sur cette page ; origine IA et « approximatif » visibles dans le PDF."""
        cm = 72.27 / 2.54
        (first, second), width, room, log = self.margin_document("a4paper,margin=2.6cm")
        self.assertIn(texexport.plain_letters("Omicron fallback note"), second)
        self.assertNotIn(texexport.plain_letters("Omicron"), first)
        self.assertIn(texexport.plain_letters("emplacement approximatif"), second)
        self.assertIn(texexport.plain_letters("Claude (proposé, non vérifié): Pi note"), first)
        self.assertAlmostEqual(width, room - 0.3 * cm, delta=0.1)  # dans la page, à 0,3 cm du bord
        self.assertGreaterEqual(width, cm - 0.1)
        self.assertNotRegex(log, r"Overfull \\hbox")  # mots longs coupés

    def test_reversed_margin(self):
        """\\reversemarginpar : notes dans la marge de gauche (5 cm), largeur calculée sur elle ; rien ne dépasse."""
        cm = 72.27 / 2.54
        _, width, _, log = self.margin_document("a4paper,left=5cm,right=1.5cm", "\\reversemarginpar")
        values = [float(v.rstrip("pt")) for v in re.search(r"LAYOUT=([^\n]*)", log).group(1).split(";")]
        _, _, hoffset, odd, _, sep = values
        self.assertAlmostEqual(width, 72.27 + hoffset + odd - sep - 0.3 * cm, delta=0.1)
        self.assertGreater(width, 4 * cm)
        self.assertNotRegex(log, r"Overfull \\hbox")

    def test_todo_defined_in_a_local_package(self):
        """\\todo défini dans un .sty du projet (que l'analyse ne lit pas) : la copie compile, le \\todo du document
        garde son sens, les notes en marge passent par todonotes."""
        (self.project / "mymacros.sty").write_text("\\newcommand{\\todo}[1]{\\textbf{MINE: #1}}\n", encoding="utf-8")
        (self.project / "main.tex").write_text("\n".join([
            r"\documentclass{article}",
            r"\usepackage{mymacros}",
            r"\begin{document}",
            r"Opening words of the paper \todo{kept meaning}.",
            r"\end{document}", ""]), encoding="utf-8")
        self.pid = pid = texfile.import_tex(self.project / "main.tex")["paper"]["id"]
        annotations.create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                           quote="Opening words of the paper", note="Rho margin note")
        text = texexport.plain_letters(" ".join(self.compile_copy("todo")))
        self.assertIn(texexport.plain_letters("MINE: kept meaning"), text)
        self.assertIn(texexport.plain_letters("Rho margin note"), text)

    def test_document_margin_width_kept(self):
        """Largeur des notes choisie par le document (geometry marginparwidth=5cm, place libre plus grande) : gardée."""
        cm = 72.27 / 2.54
        _, width, room, _ = self.margin_document("a4paper,left=2cm,right=7cm,marginparwidth=5cm")
        self.assertGreater(room - 0.3 * cm, 5 * cm)
        self.assertAlmostEqual(width, 5 * cm, delta=0.1)

    def test_lists_math_macros_and_top_lines(self):
        """Note sur le premier \\item d'une liste ou le premier \\bibitem, formule entre \\be et \\ee (macros de
        l'auteur), \\pdfoutput=1 en tête : la copie compile, l'en-tête vient après \\pdfoutput."""
        (self.project / "main.tex").write_text("\n".join([
            r"%&pdflatex",
            r"\pdfoutput=1",
            r"\documentclass{article}",
            r"\usepackage{amsmath}",
            r"\newcommand{\be}{\begin{equation}}",
            r"\newcommand{\ee}{\end{equation}}",
            r"\def\bal{\begin{eqnarray}}",
            r"\def\eal{\end{eqnarray}}",
            r"\let\beq\equation \let\eeq\endequation",
            r"\begin{document}",
            r"Opening words of the paper.",
            r"\begin{enumerate}",
            r"\item first uniqueitemone claim here.",
            r"\item second claim.",
            r"\end{enumerate}",
            r"\begin{description}",
            r"\item[A] uniquedescone words.",
            r"\end{description}",
            r"\be",
            r"a + bbbbbbbbbbbbbb",
            r"\ee",
            r"\bal",
            r"c &=& dddddddddddddd",
            r"\eal",
            r"\beq",
            r"e + ffffffffffffff",
            r"\eeq",
            r"\begin{thebibliography}{9}",
            r"\bibitem{a} Uniquebibone Author, Title.",
            r"\end{thebibliography}",
            r"\end{document}", ""]), encoding="utf-8")
        result = texfile.import_tex(self.project / "main.tex")
        self.pid = pid = result["paper"]["id"]
        for quote_, note in (("first uniqueitemone claim", "Alpha item note"), ("uniquedescone words", "Beta desc note"),
                             ("a + bbbbbbbbbbbbbb", "Gamma be note"), ("c = dddddddddddddd", "Delta bal note"),
                             ("Uniquebibone Author", "Epsilon bib note"),
                             ("e + ffffffffffffff", "Zeta let note")):
            annotations.create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}}, quote=quote_, note=note)
        entries = {e["note"]: e for e in texexport.plan_json(pid, "todo")}
        self.assertEqual({k: (e["precision"], e["todoLine"]) for k, e in entries.items()},
                         {"Alpha item note": ("exact", 11), "Beta desc note": ("exact", 15), "Gamma be note": ("exact", 18),
                          "Delta bal note": ("exact", 21), "Epsilon bib note": ("exact", 27),
                          "Zeta let note": ("exact", 25)})  # \\let non reconnu : note dans la formule, sans effet (\\ifmmode)
        self.compile_copy("comments")
        margin = texexport.plain_letters(" ".join(self.compile_copy("todo")))
        for word in ("Alpha item", "Beta desc", "Gamma be", "Delta bal", "Epsilon bib"):
            self.assertIn(texexport.plain_letters(word), margin, word)
        self.assertNotIn(texexport.plain_letters("Zeta let"), margin)
        top = self.annotated("todo", "main.tex").splitlines()
        self.assertEqual(top[:2], [r"%&pdflatex", r"\pdfoutput=1"])
        self.assertTrue(top[2].startswith("% [arXivReader]"))


if __name__ == "__main__":
    unittest.main()
