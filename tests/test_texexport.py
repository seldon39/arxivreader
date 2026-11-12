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
import subprocess
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


def is_todo(line):
    """Ligne d'une note en marge (précédée de \\arxivreaderPar quand la source termine le paragraphe juste après)."""
    return line.removeprefix(texexport.PAR_MACRO).startswith(texexport.MACRO)


def todo_before(text, words):
    """Première ligne d'origine après la note en marge qui contient `words`."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if is_todo(line) and words in line)
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
        (self.project / "main.tex").write_text(MAIN, encoding="utf-8", newline="\n")
        (self.project / "sec" / "one.tex").write_text(ONE, encoding="utf-8", newline="\n")
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
        (library.paper_dir(self.pid) / "structure.json").write_text(json.dumps(structure), encoding="utf-8", newline="\n")

    def lemma_id(self):
        return next(n["id"] for n in self.structure()["nodes"] if n["kind"] == "lemma")

    def files_of(self, mode="comments", language="fr", layout="wide", endnotes=True):
        path, name = texexport.build_zip(self.pid, mode, language, layout, endnotes)
        try:
            with zipfile.ZipFile(path) as archive:
                return name, {n: archive.read(n) for n in archive.namelist()}
        finally:
            os.unlink(path)

    def annotated(self, mode="comments", name="sec/one.tex", layout="wide", endnotes=True):
        _, files = self.files_of(mode, layout=layout, endnotes=endnotes)
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
        self.assertIn("{Theorem~\\ref{thm:main} (Main): Statement note $x \\in \\mathbb{R}$}", one)
        self.assertEqual(todo_before(one, "Preuve de Theorem~\\ref{thm:main} --- à revoir"), "\\[")  # preuve qui commence par une formule
        self.assertEqual(todo_before(one, "Align note"), r"\begin{align}")
        self.assertEqual(todo_before(one, "Item note"), r"\item the second claim holds.")
        self.assertEqual(todo_before(main_text, "Footnote note"), r"Text with a footnote\footnote{A footnote that")
        self.assertEqual(todo_before(main_text, "Caption note"), r"\begin{figure}[h]")
        self.assertEqual(todo_before(main_text, "Title note"), r"\section{A section title that")
        self.assertEqual(one.count(texexport.MACRO), 4)  # énoncé, preuve à revoir, alignement, item
        self.assertNotIn("Closing paragraph", "".join(l for l in main_text.splitlines() if is_todo(l)))
        # préambule : todonotes chargé, \todo du document gardé (sauvegardé puis remis), juste avant \begin{document}
        before = main_text.split("\n\\begin{document}\n")[0]
        self.assertIn("\\let\\todo\\relax\\usepackage{todonotes}% [arXivReader]", before)
        # « wide » : entre deux paragraphes ou au début d'un paragraphe vide, la boîte de la note seulement
        # (\\arxivreaderVtodo, \\arxivreaderHtodo), nos \\marginpar repérés pour la sortie ; « keep » : todonotes tel quel
        self.assertIn("\\newcommand{\\arxivreadertodo}[2][]{\\ifmmode\\else\\ifinner\\else\\global\\arxivreaderOurstrue"
                      "\\arxivreaderVcheck\\ifarxivreaderV\\arxivreaderVtodo{#1}{#2}\\else\\ifarxivreaderH"
                      "\\arxivreaderHtodo{#1}{#2}\\else\\arxivreaderTodonotes[#1]{#2}\\fi\\fi\\global\\arxivreaderOursfalse"
                      "\\fi\\fi}", before)
        self.assertIn("\\def\\arxivreaderPar{\\ifmmode\\else\\ifinner\\else\\ifhmode\\ifnum\\lastnodetype=-1 "
                      "{\\csname @@par\\endcsname}\\fi\\fi\\fi\\fi}", before)
        # « [arXivReader] » sur chaque ligne ajoutée ; @ rendu tel qu'il était
        self.assertIn("\\catcode`\\@=\\arxivreaderAt\\relax% [arXivReader]", before)
        self.assertIn("\\arxivreaderTodonotes[nolist,#1]{#2}", before)
        self.assertTrue(before.rstrip("\n").endswith("% [/arXivReader]"))
        kept = self.annotated("todo", "main.tex", layout="keep").split("\n\\begin{document}\n")[0]
        self.assertIn("\\newcommand{\\arxivreadertodo}[2][]{\\ifmmode\\else\\ifinner\\else\\arxivreaderTodonotes[#1]{#2}\\fi\\fi}", kept)
        self.assertNotIn("arxivreaderVtodo", kept)
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
                self.assertTrue(line.startswith("%") or (is_todo(line) and line.endswith("% [arXivReader]")), line)
            self.assertIn("% line 2 \\end{document} }}}", text)
            self.assertIn("% \\input{evil}", text)
        todo = [l for l in self.annotated("todo").splitlines() if is_todo(l)]
        self.assertIn("\\begin\\{verbatim\\}", todo[1].replace("\\textbackslash{}", "\\").replace(texexport.BREAK, "").replace(texexport.SPLIT, ""))
        self.assertIn("\\# \\$ \\& \\textasciicircum{} \\_ \\textasciitilde{} \\{", todo[1])

    def test_lost_note_and_html_paragraph_unplaced(self):
        pid = self.import_without_tex()
        note = annotations.create(pid, "block", {"id": "tex-lbl-thm:main"}, note="Lost one")
        (library.paper_dir(pid) / texfile.PREVIOUS_NOTES).write_text(json.dumps({note["uid"]: {}}), encoding="utf-8", newline="\n")
        gone = annotations.create(pid, "block", {"id": "tex-txt-lemma-0123456789"}, note="Gone one")
        (library.paper_dir(pid) / texfile.PREVIOUS_NOTES).write_text(json.dumps({
            note["uid"]: {}, gone["uid"]: {"on": "statement", "label": "Lemma 3.4", "statement": "An old\nlemma text " * 20,
                                           "page": 3}}), encoding="utf-8", newline="\n")
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
                                                      encoding="utf-8", newline="\n")
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


class EndNotes(Setup):
    """Notes longues (mode « todo », option endnotes) : début en marge et renvoi, texte entier dans une section
    ajoutée juste avant \\end{document} ; rien en mode commentaires ni sans l'option."""

    LONG = ("A long remark about the main theorem. " * 9).strip() + " Lastwords here."

    def test_long_notes_sent_to_the_end(self):
        pid = self.import_without_tex()
        annotations.create(pid, "block", {"id": "tex-lbl-thm:main"}, note=self.LONG)
        annotations.create(pid, "block", {"id": self.lemma_id()}, note="Short lemma note")
        annotations.create(pid, "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 9}},
                           quote="Closing paragraph of the paper", note="Two paragraphs.\n\nSecond one.", author="claude")
        self.assertEqual(texexport.summary(pid, "todo", "fr")["endnotes"], 2)
        self.assertEqual(texexport.summary(pid, "todo", "fr", "keep")["endnotes"], 2)
        self.assertEqual(texexport.summary(pid, "todo", "fr", "wide", False)["endnotes"], 0)
        self.assertEqual(texexport.summary(pid, "comments", "fr")["endnotes"], 0)
        entries = {e["note"][:10]: e for e in texexport.plan_json(pid, "todo")}
        # numéros dans l'ordre du document : sec/one.tex (lu par \input) avant la fin de main.tex
        self.assertEqual((entries["A long rem"]["endnote"], entries["Two paragr"]["endnote"]), (1, 2))
        self.assertNotIn("endnote", entries["Short lemm"])
        one, main_tex = self.annotated("todo"), self.annotated("todo", "main.tex")
        margin = next(line for line in one.splitlines() if is_todo(line) and "A long remark" in line)
        self.assertTrue(margin.startswith("\\arxivreadertodo[color=orange!30,size=\\arxivreaderNoteSize]{"
                                          "\\arxivreaderNoteMark{1}Theorem~\\ref{thm:main} (Main): A long remark"), margin)
        self.assertTrue(margin.endswith(" about~\\dots{} \\arxivreaderEndRef{1}}% [arXivReader]"), margin)
        self.assertNotIn("Lastwords", margin)
        self.assertIn("% " + self.LONG, one)  # texte entier aussi dans le bloc de commentaires
        self.assertIn("{Lemma 2.2: Short lemma note}% [arXivReader]", one)
        end = main_tex.split("% [arXivReader] Notes de relecture (arXivReader)")[1]
        self.assertTrue(end.endswith("% [/arXivReader]\n\\end{document}\n"), end[-200:])
        self.assertIn("\\clearpage% [arXivReader]", end)
        self.assertIn("{\\raggedright\\noindent\\arxivreaderEndMark{1}\\textbf{Note 1 --- Theorem 2.1 (Main)}\\arxivreaderEndBack{1}\\par}", end)
        self.assertIn("Lastwords here.\\par% [arXivReader]", end)
        self.assertIn("\\textbf{Note 2 --- Passage surligné, page 1 --- Claude (proposé, non vérifié)}", end)
        self.assertIn("Two paragraphs.\\par% [arXivReader]\nSecond one.\\par% [arXivReader]", end)
        self.assertIn("{\\itshape ``Closing paragraph of the paper''\\par}", end)
        self.assertNotRegex(end, r"\\(?:sub)*section|\\chapter|\\addcontentsline|\\refstepcounter")
        self.assertIn("\\DeclareRobustCommand{\\arxivreaderEndRef}", main_tex)
        self.assertIn("% Notes longues (2) : la marge", main_tex)
        _, files = self.files_of("todo", "en")
        readme = files["my draft (annotated)/README-arXivReader.txt"].decode("utf-8")
        self.assertIn("Long notes (2): the margin shows only their beginning", readme)
        self.assertIn("Review notes (arXivReader)", files["my draft (annotated)/main.tex"].decode("utf-8"))
        # sans l'option : tout en marge, comme avant ; en commentaires : rien ne change
        without = self.annotated("todo", "main.tex", endnotes=False)
        self.assertNotIn("Notes de relecture", without)
        self.assertNotIn("arxivreaderEnd", without + self.annotated("todo", endnotes=False))
        self.assertEqual(self.files_of("comments")[1], self.files_of("comments", endnotes=False)[1])
        self.assertNotIn("Notes de relecture", self.annotated("comments", "main.tex"))
        # routes : endnotes=1|0, autre valeur refusée
        for query, count in (("mode=todo", 2), ("mode=todo&endnotes=1", 2), ("mode=todo&endnotes=0", 0),
                             ("mode=comments&endnotes=1", 0)):
            self.assertEqual(self.client.get(f"/api/papers/{pid}/tex-annotated/summary?{query}").json()["endnotes"], count)
        response = self.client.get(f"/api/papers/{pid}/tex-annotated?mode=todo&lang=fr&endnotes=0")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertNotIn("Notes de relecture", archive.read("my draft (annoté)/main.tex").decode("utf-8"))
        self.assertEqual(self.client.get(f"/api/papers/{pid}/tex-annotated?mode=todo&endnotes=2").status_code, 400)

    def test_threshold_and_markdown(self):
        title = "Passage surligné, page 1"
        self.assertFalse(texexport.is_long(title, "x" * 200, "wide"))
        self.assertTrue(texexport.is_long(title, "x " * 130, "wide"))
        self.assertTrue(texexport.is_long(title, "x " * 70, "keep"))
        self.assertFalse(texexport.is_long(title, "x " * 60, "keep"))
        self.assertTrue(texexport.is_long(title, "- one item", "wide"))
        self.assertTrue(texexport.is_long(title, "Short.\n\nShort.", "wide"))
        self.assertTrue(texexport.is_long(title, "See $$x$$", "wide"))
        self.assertTrue(texexport.is_long(title, "See \\[ x \\]", "wide"))
        # « \[ \] » cité (formule vide) : pas une formule centrée, note courte
        self.assertFalse(texexport.is_long(title, "Formule affichée avec \\[ \\] : $p^{-n}$.", "wide"))
        self.assertFalse(texexport.is_long(title, "Empty $$ $$ here", "wide"))
        self.assertFalse(texexport.is_long(title, "A\nB (single line break) and **bold** $x$", "wide"))
        self.assertFalse(texexport.is_long(title, "   ", "wide"))
        known = {"R"}
        self.assertEqual(texexport.long_tex(
            "# Head\nText **bold** *it* `a_b` [link](http://x) $x \\in \\R$ $\\evil{y}$ 50%.\n\n"
            "- one\n  more\n- two $a$\n\n1. first\n2) second\n\n$$\\frac{1}{2}$$\n\n\\[ \\badone \\]\n\n"
            "```\ncode {x}\n```\n---\n> quoted", known),
            ["\\textbf{Head}\\par",
             "Text \\textbf{bold} \\emph{it} \\texttt{a\\_b} link $x \\in \\R$ \\$\\textbackslash{}evil\\{y\\}\\$ 50\\%.\\par",
             "\\begin{itemize}" + texexport.LIST_SPACING, "\\item one more", "\\item two $a$", "\\end{itemize}",
             "\\begin{enumerate}" + texexport.LIST_SPACING, "\\item first", "\\item second", "\\end{enumerate}",
             "\\[\\frac{1}{2}\\]\\par", "\\textbackslash{}[ \\textbackslash{}badone \\textbackslash{}]\\par",
             "{\\ttfamily\\noindent code \\{x\\}\\par}", "\\medskip", "quoted\\par"])
        # début coupé entre deux mots
        self.assertEqual(texexport.short_tex("alpha beta gamma", set(), limit=13, word=True), "alpha beta\\dots{}")
        self.assertEqual(texexport.short_tex("alpha beta gamma", set(), limit=13), "alpha beta ga\\dots{}")
        self.assertEqual(texexport.short_tex("$x$ vient de", set(), limit=6, word=True), "$x$\\dots{}")  # après une formule


class TodoText(unittest.TestCase):
    def test_escaping_markdown_and_math(self):
        known = {"R", "ker"}
        self.assertEqual(texexport.short_tex("**Bold** and *it* `code` [link](http://x) 50% #1 a_b ~ ^ & {x} \\cmd", known),
                         "Bold and it code link 50\\% \\#1 a\\_b \\textasciitilde{} \\textasciicircum{} \\& \\{x\\} \\textbackslash{}cmd")
        self.assertEqual(texexport.short_tex("# Title\n\nPara one.\n\nPara two.", known), "Title Para one. Para two.")
        self.assertEqual(texexport.short_tex("Kept $x \\in \\R^n$ and $\\ker f$", known), "Kept $x \\in \\R^n$ and $\\ker f$")
        self.assertEqual(texexport.short_tex("$x ∈ ℝ², ‖f‖ ≤ ε$ and λ", known | {"mathbb"}),
                         "$x \\in \\mathbb{R}^{2}, \\| f\\| \\le \\varepsilon$ and \\ensuremath{\\lambda}")
        long_formula = "$" + "+".join(f"a_{{{i}}}" for i in range(20)) + "\\unknownmacro$"
        self.assertEqual(texexport.short_tex(long_formula, known), "[...]")  # longue formule refusée : abrégée
        ams = texexport._known_words("\\documentclass{amsart}")
        matrix = "$A=\\begin{pmatrix}1&2\\\\3&4\\end{pmatrix}$"
        self.assertEqual(texexport.inline_tex(matrix, ams), matrix)  # notes de fin : environnements d'amsmath
        self.assertTrue(texexport.inline_tex(matrix, set()).startswith("\\$A"))  # sans amsmath : montrée en texte
        for bad in ("$a & b$", "$\\begin{pmatrix}a\\end{bmatrix}$", "$\\begin{align}a\\end{align}$"):
            self.assertNotRegex(texexport.inline_tex(bad, ams).replace("\\$", ""), r"\$", bad)
        for bad in ("$\\unknownmacro x$", "$a # b$", "$\\begin{matrix}a\\end{matrix}$", "${a$", "$a^$", "$\\left( a$",
                    "$a \\\\ b$", "$\\section{x}$", "$⨁$", "$a % b$", "$$display$$"):
            out = texexport.short_tex(bad, known)
            self.assertNotRegex(out.replace("\\$", ""), r"\$", bad)  # formule échappée, jamais recopiée
        self.assertEqual(texexport.short_tex("Café « déjà » – ∑ → x", known), "Café \" déjà \" -- \\ensuremath{\\sum} \\ensuremath{\\to} x")
        long = texexport.short_tex("word " * 200, known)
        self.assertTrue(long.endswith("\\dots{}") and len(long) < 320)
        cut = texexport.short_tex("x" * 290 + " $\\alpha + \\beta + \\gamma$", known)
        self.assertTrue(cut.endswith("\\dots{}") and "$" not in cut)  # formule jamais coupée

    def references_known(self, eqref=True):
        structure = {"equations": {"eqA": {"label": "(1.2)"}, "eqB": {"label": "(1.3)"}, "gone": {"label": "(9.9)"}},
                     "labels": {"thmA": "tex-lbl-thmA", "One": "tex-lbl-thmA", "eqA": None},
                     "nodes": [{"id": "tex-lbl-thmA", "label": "Theorem 2.1"}, {"id": "tex-txt-x", "label": "Lemma 3"}],
                     "bibliography": [{"id": "bib.Smith20", "label": "1"}, {"id": "bib.Doe19", "label": "2"}]}
        known = texexport.Known({"mathbb"})
        known.refs = texexport.references(structure, "\\label{eqA} \\label{eqB} \\label{thmA} \\label{One}", eqref)
        return known

    def references_known_fr(self):
        known = self.references_known()
        known.refs["node"]["lemma 2.1"] = "thmA"  # (énoncé nommé « Lemma » par le document, « Lemme » dans la note)
        return known

    def test_references(self):
        """Numéros écrits dans une note → \\eqref, \\ref, \\cite (structure de la dernière compilation) ; le reste
        inchangé."""
        known = self.references_known()
        self.assertEqual(texexport.inline_tex("By (1.2) and Theorem 2.1, see [Smith20, Lemma 3] and [2].", known),
                         "By \\eqref{eqA} and Theorem~\\ref{thmA}, see \\cite[Lemma 3]{Smith20} and \\cite{Doe19}.")
        unknown = texexport.inline_tex("(9.9), Lemma 3, [Other21], (1.4), Section 2, [text](http://x)", known)
        self.assertNotIn("\\ref", unknown)  # étiquette absente de la source, énoncé sans \label, clé inconnue…
        self.assertNotIn("\\cite", unknown)
        self.assertEqual(texexport.inline_tex("$f(1.2)$ and **Theorem 2.1**", known),
                         "$f(1.2)$ and \\textbf{Theorem~\\ref{thmA}}")  # jamais dans une formule
        self.assertEqual(texexport.inline_tex("\\cite[p. 3]{Smith20} \\eqref{eqB} \\ref{nope}", known),
                         "\\cite[p. 3]{Smith20} \\eqref{eqB} \\textbackslash{}ref\\{nope\\}")
        self.assertEqual(texexport.short_tex("Theorem 2.1: see (1.3)", known), "Theorem~\\ref{thmA}: see \\eqref{eqB}")
        self.assertEqual(texexport.short_tex("Theorem 2.1: see (1.3)", known, limit=20), "Theorem~\\ref{thmA}: see\\dots{}")
        self.assertEqual(texexport.inline_tex("(1.2)", self.references_known(eqref=False)), "(\\ref{eqA})")  # sans amsmath
        self.assertEqual(texexport.inline_tex("(1.2) and [2]", {"mathbb"}), "(1.2) and [2]")  # renvois inconnus
        for other in ("Theorem 2.1 of [Smith20]", "In [1] Theorem 2.1", "Smith's Theorem 2.1", "the (1.2) of [Doe19]",
                      "On [1, 2] it holds", "a[2] and x_[1]", "`see (1.2)`"):
            self.assertNotIn("\\ref", texexport.short_tex(other, known), other)  # numéro d'un autre article, intervalle, code
            self.assertNotIn("\\cite{Smith20,", texexport.short_tex(other, known), other)
        self.assertEqual(texexport.short_tex("Lemme 2.1 et\nTheorem\n2.1, [1,\nThm 3]", self.references_known_fr()),
                         "Lemme~\\ref{thmA} et Theorem~\\ref{thmA}, \\cite[Thm 3]{Smith20}")
        mislabelled = {"equations": {}, "labels": {"thmA": "tex-lbl-thmA"}, "bibliography": [],
                       "nodes": [{"id": "tex-lbl-thmA", "label": "Theorem 2", "anchors": ["Item.4", "thm.1.3"]}]}
        refs = texexport.references(mislabelled, "\\label{thmA}", True)
        self.assertEqual(refs["node"], {"theorem 1.3": "thmA"})  # numéro de l'ancre de hyperref, pas celui d'un \item
        block = texexport.comment_block({"title": "T", "note": "See (1.2) and $x_{(1.3)}$."}, texexport.WORDS["fr"], known)
        self.assertIn("% Renvois : (1.2) = \\eqref{eqA}", block)
        self.assertNotIn("eqB", "\n".join(block))

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
        (self.project / "main.bbl").write_text("\\begin{thebibliography}{1}\\end{thebibliography}", encoding="utf-8", newline="\n")
        for name in ("main.aux", "main.log", "main.synctex.gz", "main.fdb_latexmk", "notes.tex~", ".DS_Store"):
            (self.project / name).write_text("x", encoding="utf-8", newline="\n")
        for folder in (".git", "__MACOSX", "node_modules"):
            (self.project / folder).mkdir()
            (self.project / folder / "inside.tex").write_text("x", encoding="utf-8", newline="\n")
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8", newline="\n")
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
        (shared / "macros.tex").write_text("\\newcommand{\\RR}{\\mathbb{R}}\n", encoding="utf-8", newline="\n")
        (self.project / "sec" / "latin.tex").write_bytes("Caf\xe9 au lait: some latin words here.\r\n\\par\r\n".encode("latin-1"))
        text = MAIN.replace("\\input{sec/one}", "\\input{../shared/macros}\n\\input{sec/one}\n\\input{sec/latin}")
        (self.project / "main.tex").write_text(text, encoding="utf-8", newline="\n")
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
        (self.project / "main.tex").write_text(text, encoding="utf-8", newline="\n")
        for name in ("run.out", "sup.aux", "sup.tex", "data.tmp", "sec/one.aux", "sec/one.log", "main.toc", "main.tex.bak"):
            (self.project / name).write_text("x\n", encoding="utf-8", newline="\n")
        (self.project / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8", newline="\n")
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
        if os.name != "nt":  # Windows : pas de droits Unix sur les fichiers (chmod ne règle que la lecture seule)
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

    def test_layouts(self):
        """Notes en marge : en-tête et LISEZMOI décrivent la mise en page « wide » (pas « keep ») ; commentaires : la
        mise en page ne change rien ; mise en page inconnue refusée."""
        self.import_without_tex()
        annotations.create(self.pid, "block", {"id": "tex-lbl-thm:main"}, note="A note")
        first = texexport.WORDS["fr"]["layout"][0]
        wide, kept = self.files_of("todo"), self.files_of("todo", layout="keep")
        main_wide, main_kept = (files["my draft (annoté)/main.tex"].decode("utf-8") for _, files in (wide, kept))
        self.assertIn(f"% {first}", main_wide)
        self.assertNotIn(first, main_kept)
        self.assertIn("\\AddToHook{begindocument/end}{\\arxivreaderLayout}", main_wide)
        self.assertNotIn("arxivreaderLayout", main_kept)
        self.assertIn(first, wide[1]["my draft (annoté)/LISEZMOI-arXivReader.txt"].decode("utf-8"))
        self.assertNotIn(first, kept[1]["my draft (annoté)/LISEZMOI-arXivReader.txt"].decode("utf-8"))
        self.assertTrue(all(line.rstrip().endswith("% [arXivReader]") or line.startswith("% ")
                            for line in main_wide.split("\n\\begin{document}\n")[0].splitlines()
                            if "arxivreader" in line.lower()))
        self.assertEqual(self.files_of("comments")[1], self.files_of("comments", layout="keep")[1])
        self.assertIsNone(texexport.summary(self.pid, "comments", "fr", "keep")["layout"])
        self.assertTrue(texexport.check("todo", "en", "keep"))
        self.assertFalse(texexport.check("todo", "en", "narrow"))

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
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:  # mise en page « wide » par défaut
            self.assertIn("\\arxivreadertodo[color=orange!30,size=\\arxivreaderNoteSize]{Theorem~\\ref{thm:main} (Main) --- à revoir: A note}% [arXivReader]",
                          archive.read("my draft (annoté)/sec/one.tex").decode("utf-8"))
            self.assertIn("7 pt (\\arxivreaderNoteSize)", archive.read("my draft (annoté)/LISEZMOI-arXivReader.txt").decode("utf-8"))
        response = self.client.get(f"/api/papers/{pid}/tex-annotated?mode=todo&lang=en&layout=keep")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn("\\arxivreadertodo[color=orange!30,size=\\scriptsize]{Theorem~\\ref{thm:main} (Main) --- to review: A note}% [arXivReader]",
                          archive.read("my draft (annotated)/sec/one.tex").decode("utf-8"))
            self.assertNotIn("Layout of the margin notes", archive.read("my draft (annotated)/README-arXivReader.txt").decode("utf-8"))
        self.assertEqual(self.client.get(f"/api/papers/{pid}/tex-annotated/summary?mode=todo&layout=keep").json()["layout"], "keep")
        self.assertEqual(self.client.get(f"/api/papers/{pid}/tex-annotated/summary?mode=todo").json()["layout"], "wide")
        self.assertIsNone(self.client.get(f"/api/papers/{pid}/tex-annotated/summary?mode=comments&layout=keep").json()["layout"])
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
        todo = [l for l in self.annotated("todo").splitlines() if is_todo(l)]
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
        (self.project / "main.tex").write_text(MATCH, encoding="utf-8", newline="\n")
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
                                               encoding="utf-8", newline="\n")
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
        preamble = self.annotated("todo", "main.tex", layout="keep").split("\n\\begin{document}\n")[0]
        self.assertIn("\\setlength{\\marginparwidth}{2cm}% [arXivReader]", preamble)  # avant todonotes, remplacé ensuite
        self.assertIn("\\AtBeginDocument{\\arxivreaderWidth\\maxdimen", preamble)
        self.assertIn("\\csname if@reversemargin\\endcsname", preamble)
        self.assertNotIn(texexport.OWN_WIDTH, preamble)
        self.assertNotIn("\\makeatletter", preamble)
        (self.project / "main.tex").write_text(MAIN.replace("\\begin{document}", "\\usepackage[marginparwidth=5cm]{geometry}\n\\begin{document}", 1),
                                               encoding="utf-8", newline="\n")
        preamble = self.annotated("todo", "main.tex", layout="keep").split("\n\\begin{document}\n")[0]
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
        fr, en = texexport.ERRORS["fr"]["params"], texexport.ERRORS["en"]["params"]
        self.assertEqual((fr, en), ("Forme de copie, mise en page, renvoi des notes longues ou langue inconnus",
                                    "Unknown copy form, layout, long-note option or language"))
        for query, message in (("mode=bad&lang=fr", fr), ("mode=bad&lang=en", en), ("mode=todo&lang=de", en),
                               ("mode=todo&lang=fr&layout=narrow", fr), ("mode=comments&lang=en&layout=", en),
                               ("mode=todo&lang=fr&endnotes=yes", fr), ("mode=comments&lang=en&endnotes=", en)):
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
        # chaîne trop longue lue d'en haut : coupée, mais une macro du bas reste lisible quand elle est employée seule
        chain = texexport.user_macros("".join(f"\\def\\m{a}{{\\m{b}}}" for a, b in zip("abcdefg", "bcdefgh"))
                                      + r"\def\mh{xyzw}")
        self.assertEqual(texexport.tex_letters(r"\ma, then \mg", macros=chain)[0], "thenxyzw")
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
            r"\end{document}", ""]), encoding="utf-8", newline="\n")
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
                                               "Some uniquely placed words in the text.\n\\end{document}\n", encoding="utf-8", newline="\n")
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

    def compile_copy(self, mode, patch=None, with_log=False, layout="wide", endnotes=True):
        """Copie annotée compilée : texte de chaque page (et le journal) ; `patch` change le texte du fichier principal
        de la copie avant la compilation."""
        _, files = self.files_of(mode, layout=layout, endnotes=endnotes)
        folder = self.tmp / f"copy-{mode}"
        shutil.rmtree(folder, ignore_errors=True)
        for name, data in files.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        main_file = folder / "my draft (annoté)" / "main.tex"
        if patch:
            main_file.write_text(patch(main_file.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
        out = self.tmp / f"out-{mode}"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        pdf, log, tool = texfile.compile_pdf(main_file, out, "pdflatex", False, [])
        self.assertIsNotNone(pdf, log[-2000:])
        self.pdf = pdf
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

    def margin_document(self, geometry, extra="", layout="keep"):
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
            r"\end{document}", ""]), encoding="utf-8", newline="\n")
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
        pages, log = self.compile_copy("todo", patch=self.layout, with_log=True, layout=layout)
        width, room = self.margins(log)
        return [texexport.plain_letters(p) for p in pages], width, room, log

    def test_narrow_margins_page_fallback_and_ai_origin(self):
        """Marges étroites (geometry margin=2.6cm) : notes dans la page, mots longs coupés ; repli sur une page qui
        commence par une section : la note sur cette page ; origine IA et « approximatif » visibles dans le PDF."""
        cm = 72.27 / 2.54
        (first, second, end), width, room, log = self.margin_document("a4paper,margin=2.6cm")
        # note de Claude longue : son début en marge, son texte entier sur une dernière page (notes longues)
        self.assertIn(texexport.plain_letters("Notes de relecture (arXivReader)"), end)
        self.assertIn(texexport.plain_letters("Wohlgeformtheitsbedingungen"), end)
        self.assertNotIn(texexport.plain_letters("Wohlgeformtheitsbedingungen"), first)
        self.assertIn(texexport.plain_letters("note 1, p. 3"), first)
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

    # --- Mise en page « wide » : notes lisibles, corps du texte inchangé ---

    def chunks(self, path):
        """Morceaux de texte de chaque page : (largeur de la page, [(x, y, taille, texte)]) en points PDF, y compté depuis
        le haut de la page (todonotes, par pgf, donne au PDF la taille \\paperwidth × \\paperheight du document, que
        l'original n'a pas toujours : article en lettre US compilé sur du A4)."""
        import pypdf
        pages = []
        for page in pypdf.PdfReader(str(path)).pages:
            found, top = [], float(page.mediabox.top)

            def visit(text, cm, tm, font, size, found=found, top=top):
                if text.strip():
                    x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                    y = top - (tm[4] * cm[1] + tm[5] * cm[3] + cm[5])
                    found.append((x, y, size * (tm[0] ** 2 + tm[1] ** 2) ** .5 * (cm[0] ** 2 + cm[1] ** 2) ** .5, text))
            page.extract_text(visitor_text=visit)
            pages.append((float(page.mediabox.width), found))
        return pages

    def values(self, text):
        """Fichier principal qui écrit dans le journal la mise en page avant celle de la copie (\\AtBeginDocument) et
        à la fin du document (après celle de la copie, refaite au besoin par \\twocolumn ou geometry)."""
        dims = ("\\the\\paperwidth;\\the\\hoffset;\\the\\oddsidemargin;\\the\\evensidemargin;\\the\\textwidth;"
                "\\the\\textheight;\\the\\marginparsep;\\the\\marginparwidth")
        return re.sub(r"(?m)^\\begin\{document\}", lambda m: f"\\AtBeginDocument{{\\typeout{{BEFORE={dims}}}}}\n"
                      f"\\AtEndDocument{{\\typeout{{AFTER={dims}}}}}\n\\begin{{document}}", text, count=1)

    FILLER = "Some filler text to make a paragraph long enough to break across lines. " * 4
    NOTES = ((1, "Uniquealpha opening words", "Alpha vertical note with enough words to wrap over a few lines"),
             (1, "Middle uniquegamma words", "Gamma paragraph note"),
             (2, "Uniquebeta words", "Beta second page note"))

    def wide_document(self, head, twoside=False, twocolumn=False, prefix="", body=None, notes=NOTES, crop=False):
        """Article (préambule `head`, `prefix` au début du texte), par défaut de deux pages et trois notes loin des bas
        de page : en tête de paragraphe (mode vertical) sur chaque page et au milieu d'un paragraphe. Copie « wide »
        compilée, puis vérifie : pas de débordement, largeur de la page du PDF = celle calculée, colonne des notes de
        4,5 à 6 cm à 0,4 cm du bord au plus, notes en 7 pt dans la page (à droite du texte, sauf sur deux colonnes),
        corps de chaque page au même endroit que dans l'original au décalage horizontal près. `crop` : marges
        comptées depuis le bord de la page coupée (\\hoffset), feuille du PDF plus grande. Rend (avant, après,
        journal) en points TeX."""
        filler = self.FILLER
        if body is None:
            body = [r"\section{First part}", r"Uniquealpha opening words of the first page.", filler, filler,
                    r"Middle uniquegamma words of the paragraph.", filler, "", filler, filler, "", r"\newpage",
                    r"\section{Second part}", r"Uniquebeta words of the second page.", filler, filler]
        (self.project / "main.tex").write_text("\n".join([
            *head.splitlines(), r"\begin{document}", *prefix.splitlines(), *body, r"\end{document}", ""]),
            encoding="utf-8", newline="\n")
        self.pid = pid = texfile.import_tex(self.project / "main.tex")["paper"]["id"]
        original = self.chunks(library.paper_dir(pid) / "paper.pdf")
        for page, quote_, note in notes:
            annotations.create(pid, "highlight", {"pdf": {"page": page, "start": 0, "endPage": page, "end": 9}},
                               quote=quote_, note=note)
        _, log = self.compile_copy("todo", patch=self.values, with_log=True)
        self.assertNotRegex(log, r"Overfull \\hbox")
        self.assertIn("arXivReader [mise en page] notes en marge larges de", log)
        before, after = ([float(v.rstrip("pt")) for v in re.search(rf"{key}=([^\n]*)", log).group(1).split(";")]
                         for key in ("BEFORE", "AFTER"))
        paper, hoffset, odd, even, text, height, sep, width = after
        cm, bp = 72.27 / 2.54, 72 / 72.27
        self.assertEqual((text, height), (before[4], before[5]))  # largeur et hauteur du texte inchangées
        self.assertGreaterEqual(width, 4.5 * cm - 0.1)
        self.assertLessEqual(width, 6 * cm + 0.1)
        wide = self.chunks(self.pdf)
        self.assertEqual(len(wide), len(original))
        origin = 72.27 + (0 if crop else hoffset)  # bord gauche de la page (coupée), en points TeX depuis la feuille

        def left(values, number):  # marge de gauche, depuis le bord de la page (coupée)
            return 72.27 + (0 if crop else values[1]) + (values[3] if twoside and number % 2 == 0 else values[2])
        for number, ((width0, body0), (width1, body1)) in enumerate(zip(original, wide), 1):
            if not crop:
                self.assertAlmostEqual(width1, paper * bp, delta=0.05)  # page du PDF élargie comme calculé
            start = left(after, number)
            if not twocolumn:
                self.assertLessEqual(start, min(left(before, number), 1.5 * cm) + 0.1)  # texte décalé vers la gauche
                self.assertAlmostEqual(width, min(paper - 0.4 * cm - start - text - sep, 6 * cm), delta=0.1)
            shift = (start - left(before, number)) * bp
            # notes : morceaux en 7 pt absents de la page d'origine (texte et hauteur ; amsart a des en-têtes en 7 pt) ;
            # le reste est le corps (découpé autrement là où une note est ancrée : comparé ligne par ligne)
            known = {(round(y), re.sub(r"\s+", "", words)) for x, y, size, words in body0}
            found = [c for c in body1 if 6.8 <= c[2] <= 7.2 and (round(c[1]), re.sub(r"\s+", "", c[3])) not in known]
            self.assertTrue(found or number > len({page for page, _, _ in notes}), number)
            for x, y, size, words in found:
                self.assertGreaterEqual(x, (origin - 72.27) * bp - 0.1)  # dans la page (coupée)
                self.assertLess(x, (origin - 72.27 + paper) * bp - 0.4 * cm * bp)
                if not twocolumn:
                    self.assertGreater(x, (origin - 72.27 + start + text) * bp)  # à droite du texte, page paire comprise
            rows0, rows1 = {}, {}
            for rows, chunks in ((rows0, body0), (rows1, [c for c in body1 if c not in found])):
                for x, y, size, words in chunks:
                    rows.setdefault(round(y), []).append((x, words, y))
            self.assertEqual(sorted(rows0), sorted(rows1), number)  # mêmes lignes, à la même hauteur
            for y in rows0:
                line0, line1 = sorted(rows0[y]), sorted(rows1[y])
                self.assertAlmostEqual(min(c[2] for c in line0), min(c[2] for c in line1), delta=0.2)  # (au 0,2 pt près)
                self.assertEqual(re.sub(r"\s+", "", "".join(c[1] for c in line0)),
                                 re.sub(r"\s+", "", "".join(c[1] for c in line1)), (number, y))
                if not crop:  # (crop : pypdf ne suit pas le placement de la page sur la feuille)
                    self.assertAlmostEqual(line1[0][0] - line0[0][0], shift, delta=0.3)
        margin = texexport.plain_letters(" ".join(self.pdf_text(self.pdf)))
        for _, _, note in notes:
            self.assertIn(texexport.plain_letters(note), margin, note)
        return before, after, log

    def test_wide_article(self):
        """Article (lettre US) : marge de droite assez large, page non élargie ; texte décalé vers la gauche, à 1,5 cm ;
        colonne des notes limitée à 6 cm (lignes de notes lisibles)."""
        before, after, _ = self.wide_document("\\documentclass{article}")
        self.assertEqual(after[0], before[0])
        cm = 72.27 / 2.54
        self.assertAlmostEqual(72.27 + after[1] + after[2], 1.5 * cm, delta=0.1)
        self.assertAlmostEqual(after[7], 6 * cm, delta=0.1)

    def test_wide_amsart(self):
        """amsart (recto verso par défaut, en-têtes en 7 pt comme les notes)."""
        self.wide_document("\\documentclass{amsart}", twoside=True)

    def test_wide_narrow_geometry(self):
        """geometry margin=2.6cm (A4) : page élargie juste assez pour une colonne de 4,5 cm."""
        cm = 72.27 / 2.54
        before, after, _ = self.wide_document("\\documentclass[a4paper]{article}\n\\usepackage[margin=2.6cm]{geometry}")
        self.assertAlmostEqual(after[7], 4.5 * cm, delta=0.1)
        self.assertGreater(after[0], before[0] + 1 * cm)

    def test_wide_room_just_above_the_minimum(self):
        """Marge de droite de 5 cm, \\marginparsep de 0,3 cm : 4,3 cm de place, page élargie de 0,2 cm (TeX ne doit
        pas comparer à 4,5 cm avant d'avoir retiré les 0,4 cm du bord)."""
        cm = 72.27 / 2.54
        before, after, _ = self.wide_document("\\documentclass[a4paper]{article}\n"
                                              "\\usepackage[left=1.5cm,right=5cm,marginparsep=0.3cm]{geometry}")
        self.assertAlmostEqual(after[7], 4.5 * cm, delta=0.1)
        self.assertAlmostEqual(after[0] - before[0], 0.2 * cm, delta=0.1)

    def test_wide_twoside(self):
        """Recto verso : même marge de gauche sur les pages paires, notes à droite (page 2 comprise)."""
        _, after, _ = self.wide_document("\\documentclass[twoside]{article}", twoside=True)
        self.assertEqual(after[2], after[3])

    def test_wide_twocolumn(self):
        """Deux colonnes : texte non décalé, page élargie des deux côtés pour les notes."""
        cm = 72.27 / 2.54
        before, after, _ = self.wide_document("\\documentclass[a4paper,twocolumn]{article}", twocolumn=True)
        self.assertGreater(after[0], before[0])
        self.assertGreaterEqual(after[7], 4.5 * cm - 0.1)

    def test_wide_twocolumn_after_begin_document(self):
        """\\twocolumn au début du texte (comme acmart sigconf dans \\maketitle) : mise en page refaite pour deux
        colonnes, notes de la colonne de gauche dans la page."""
        before, after, log = self.wide_document("\\documentclass[a4paper]{article}\n\\sloppy", twocolumn=True,
                                                prefix="\\twocolumn")
        self.assertGreaterEqual(after[2], before[2])  # texte remis à sa place (page élargie à gauche au besoin)
        self.assertEqual(log.count("arXivReader [mise en page]"), 2)

    def test_wide_twocolumn_classes(self):
        """revtex (deux colonnes de ltxgrid, \\if@twocolumn faux) et acmart sigconf (\\twocolumn dans \\maketitle)."""
        for head, prefix in (("\\documentclass[aps,prl,twocolumn]{revtex4-2}", ""),
                             ("\\documentclass[sigconf]{acmart}\n\\settopmatter{printacmref=false}\\setcopyright{none}",
                              "\\title{T}\\maketitle")):
            if not shutil.which("kpsewhich") or not subprocess.run(
                    ["kpsewhich", re.search(r"\{(\w[\w-]*)\}", head).group(1) + ".cls"], capture_output=True).stdout:
                continue
            with self.subTest(head):
                self.wide_document(head, twocolumn=True, prefix=prefix)

    def test_wide_hyperref(self):
        """hyperref fixe lui-même la taille de la page du PDF : la page élargie s'y retrouve."""
        before, after, _ = self.wide_document("\\documentclass[a4paper]{article}\n\\usepackage[margin=2.6cm]{geometry}\n"
                                              "\\usepackage{hyperref}")
        self.assertGreater(after[0], before[0])

    def test_wide_restoregeometry(self):
        """\\restoregeometry au début du texte (geometry y remet ses valeurs) : mise en page refaite, page et colonne
        des notes comme sans lui."""
        cm = 72.27 / 2.54
        before, after, _ = self.wide_document("\\documentclass[a4paper]{article}\n\\usepackage[margin=2.6cm]{geometry}",
                                              prefix="\\restoregeometry")
        self.assertAlmostEqual(after[7], 4.5 * cm, delta=0.1)
        self.assertGreater(after[0], before[0] + 1 * cm)

    def test_wide_crop(self):
        """crop (page A4 centrée sur une feuille A3 : \\hoffset) : marges comptées depuis le bord de la page coupée,
        texte et notes dans la page coupée."""
        cm = 72.27 / 2.54
        _, after, _ = self.wide_document("\\documentclass[a4paper]{article}\n\\usepackage[a3,center,cam]{crop}", crop=True)
        self.assertAlmostEqual(72.27 + after[2], 1.5 * cm, delta=0.1)
        self.assertGreater(after[1], 0)

    def test_wide_own_margin_width(self):
        """Largeur des notes trop petite choisie par le document (1 cm) : remplacée par la colonne calculée."""
        before, after, _ = self.wide_document("\\documentclass{article}\n\\setlength{\\marginparwidth}{1cm}")
        self.assertAlmostEqual(before[7], 72.27 / 2.54, delta=0.1)
        self.assertGreater(after[7], 4.5 * 72.27 / 2.54)

    def test_wide_footnote_page_break(self):
        """Note au début d'un énoncé en bas d'une page qui a une note de bas de page : LaTeX remet les notes de bas de
        page à la fin de la page après chaque \\marginpar ; pour les nôtres, avant (et sans coupure de plus) — pages
        coupées comme dans l'original (ici, sans cela, la fin de la page 1 change)."""
        filler = self.FILLER[:72]
        body = [r"\section{First part}", "Opening words with a footnote\\footnote{" + filler * 3 + "} and more. " + filler * 6,
                "", filler * 25, "", (filler * 30)[:780], "", r"\vspace{8.5pt}", "", r"\begin{theorem}",
                "Uniquetheorem words of the statement. " + filler * 6, r"\end{theorem}", filler * 30, "", filler * 30]
        self.wide_document("\\documentclass{article}\n\\newtheorem{theorem}{Theorem}", body=body,
                           notes=((1, "Uniquetheorem words of the statement", "Theorem note"),))

    def test_wide_notes_after_a_display(self):
        """Notes juste après une formule centrée (paragraphe encore vide) : avant un \\item (paragraphe terminé
        d'abord, \\arxivreaderPar) et avant une ligne qui ne commence que par une couleur (boîte seule, sans repère de
        trait qui donnerait de la hauteur à la ligne) : rien ne bouge dans le texte."""
        filler = self.FILLER
        body = [r"\section{First part}", filler, r"\begin{itemize}", r"\item Opening item " + filler,
                r"\[ x = y \]", r"\item Uniqueitem words of the second item.", r"\end{itemize}", filler,
                r"\[ a = \frac{1}{\displaystyle\sum_{k=1}^{n} b_k} \]",  # (formule profonde : interligne \lineskip)
                r"{\color{blue}\begin{theorem}Uniquecolor words of the statement.\end{theorem}}",
                filler, filler]
        notes = ((1, "Uniqueitem words of the second", "Item note after a display"),
                 (1, "Uniquecolor words of the statement", "Colour note after a display"))
        self.wide_document("\\documentclass{amsart}\n\\usepackage{xcolor}\n\\newtheorem{theorem}{Theorem}",
                           twoside=True, body=body, notes=notes)
        todo = {note: line for line in self.annotated("todo", "main.tex").splitlines() if texexport.MACRO in line
                for note in ("Item note", "Colour note") if note in line}
        self.assertTrue(todo["Item note"].startswith("\\arxivreaderPar\\arxivreadertodo["))
        self.assertTrue(todo["Colour note"].startswith("\\arxivreadertodo["))

    def test_todo_defined_in_a_local_package(self):
        """\\todo défini dans un .sty du projet (que l'analyse ne lit pas) : la copie compile, le \\todo du document
        garde son sens, les notes en marge passent par todonotes."""
        (self.project / "mymacros.sty").write_text("\\newcommand{\\todo}[1]{\\textbf{MINE: #1}}\n", encoding="utf-8", newline="\n")
        (self.project / "main.tex").write_text("\n".join([
            r"\documentclass{article}",
            r"\usepackage{mymacros}",
            r"\begin{document}",
            r"Opening words of the paper \todo{kept meaning}.",
            r"\end{document}", ""]), encoding="utf-8", newline="\n")
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

    # --- Notes longues : début en marge, texte entier en fin de document ---

    LONG_NOTES = (
        (1, "Uniquealpha opening words", None,
         "Upsilon long note. " + "This remark goes on about the estimate and the constants involved in it. " * 5
         + "Finalwordupsilon."),
        (1, "Middle uniquegamma words", None,
         "Phi structured note.\n\nSecond paragraph with **bold words** and $x^2 \\geq 0$ and unsafe $\\foo{x}$.\n\n"
         "- first item of the list\n- second item of the list\n\n$$\\sum_{k=1}^{n} k = \\frac{n(n+1)}{2}$$\n\n"
         "Last paragraph finalwordphi."),
        (1, "Uniquedelta closing words", None, "Psi short note"),
        (2, "Uniquebeta words", "claude",
         "Chi note proposed by the assistant. " + "It checks every step of the argument once more. " * 6
         + "Finalwordchi."))

    def endnote_document(self, head, layout="wide", bibliography=False, chapter=False):
        """Document de deux pages (`head` : préambule), trois notes longues (longueur ; paragraphes, liste et formules ;
        note de Claude) et une courte, compilé avec et sans renvoi des notes longues (latexmk). Vérifie : compilation
        sans erreur ni débordement, renvois résolus (aucun « ?? »), pages d'avant les notes de fin identiques (corps du
        texte : tout sauf les notes en 7 pt), notes longues courtes en marge et terminées par leur renvoi, texte entier
        sur les dernières pages. Rend (pages avec, pages sans, source du fichier principal)."""
        filler = self.FILLER
        body = [r"\chapter{First part}" if chapter else r"\section{First part}",
                r"Uniquealpha opening words of the first page.", filler, filler,
                r"Middle uniquegamma words of the paragraph.", filler, "", filler,
                r"Uniquedelta closing words of the page.", "", r"\newpage",
                r"\section{Second part}", r"Uniquebeta words of the second page.", filler, filler]
        if bibliography:
            body += [r"\begin{thebibliography}{9}", r"\bibitem{a} A. Author, \emph{Uniquebib title}, 2020.",
                     r"\end{thebibliography}"]
        (self.project / "main.tex").write_text("\n".join([*head.splitlines(), r"\begin{document}", *body,
                                                          r"\end{document}", ""]), encoding="utf-8", newline="\n")
        self.pid = pid = texfile.import_tex(self.project / "main.tex")["paper"]["id"]
        for page, quote_, author, note in self.LONG_NOTES:
            annotations.create(pid, "highlight", {"pdf": {"page": page, "start": 0, "endPage": page, "end": 9}},
                               quote=quote_, note=note, author=author)
        summary = texexport.summary(pid, "todo", "fr", layout)
        self.assertEqual(summary["endnotes"], 3)
        self.assertEqual(texexport.summary(pid, "todo", "fr", layout, False)["endnotes"], 0)
        source = self.annotated("todo", "main.tex", layout=layout)
        pages = []
        for endnotes in (False, True):  # (copie avec renvois compilée en dernier : self.pdf)
            texts, log = self.compile_copy("todo", with_log=True, layout=layout, endnotes=endnotes)
            self.assertNotRegex(log, r"Overfull \\[hv]box")
            self.assertNotRegex(log, r"(?i)undefined references|Reference `arxivreader")
            self.assertFalse(any("??" in text for text in texts))
            pages.append((texts, self.chunks(self.pdf)))
        (texts0, chunks0), (texts, chunks) = pages
        count = len(texts0)
        self.assertGreater(len(texts), count)
        notes = lambda page: [c for c in page[1] if 6.8 <= c[2] <= 7.2]

        def body_of(page):  # corps du texte, dans l'ordre de la page (notes en 7 pt, leurs indices et exposants retirés ;
            # pypdf regroupe parfois deux lignes en un morceau : texte comparé, pas les lignes)
            kept = sorted((round(y), x, re.sub(r"\s+", "", words)) for x, y, size, words in page[1] if size > 7.3)
            return "".join(words for _, _, words in kept)
        for number in range(count):  # pages d'avant : corps du texte inchangé
            self.assertEqual(body_of(chunks[number]), body_of(chunks0[number]), number + 1)
        before, end = " ".join(texts[:count]), " ".join(texts[count:])
        margin = " ".join(c[3] for page in chunks[:count] for c in notes(page))
        letters = texexport.plain_letters
        self.assertIn(letters("Notes de relecture (arXivReader)"), letters(end))
        for word in ("Finalwordupsilon", "finalwordphi", "Finalwordchi"):
            self.assertNotIn(letters(word), letters(before), word)
            self.assertIn(letters(word), letters(end), word)
        self.assertIn(letters("Psi short note"), letters(margin))
        self.assertNotIn(letters("Psi short note"), letters(end))
        # renvois : « → note N, p. X » en marge, « Note N — titre (page Y) » à la fin, dans l'ordre du document
        refs = re.findall(r"note\s*(\d)\s*,\s*p\.\s*(\d+)", margin)
        self.assertEqual(sorted(refs), [("1", str(count + 1)), ("2", str(count + 1)), ("3", str(count + 1))])
        flat = " ".join(end.split())
        self.assertRegex(flat, r"Note 1 — Passage surlign[^()]*, page 1[^()]*\s?\(en marge, page 1\)")
        self.assertRegex(flat, r"Note 3 — Passage surlign[^()]*, page 2[^()]* — Claude \([^()]*\)\s?\(en marge, page 2\)")
        self.assertLess(flat.index("Upsilon long note"), flat.index("Phi structured note"))
        self.assertLess(flat.index("Phi structured note"), flat.index("Chi note proposed"))
        self.assertIn(letters("Claude (proposé, non vérifié)"), letters(margin))
        # texte entier : paragraphes, liste, gras, formule sûre gardée, formule inconnue montrée en texte
        self.assertIn("\\begin{itemize}" + texexport.LIST_SPACING + "% [arXivReader]", source)
        self.assertIn("\\item first item of the list% [arXivReader]", source)
        self.assertIn("\\[\\sum_{k=1}^{n} k = \\frac{n(n+1)}{2}\\]", source)
        self.assertIn("\\textbf{bold words} and $x^2 \\geq 0$ and unsafe \\$\\textbackslash{}foo\\{x\\}\\$", source)
        self.assertIn("\\foo{x}", flat.replace(" ", ""))
        self.assertIn(letters("first item of the list"), letters(end))
        # en marge : début court (≈ 60 % du seuil) terminé par le renvoi
        for line in source.splitlines():
            if is_todo(line) and "arxivreaderEndRef" in line:
                text = re.sub(r"\\arxivreaderNoteMark\{\d\}|\\arxivreaderBreak\{\}|\\allowbreak\{\}", "", line.split("]{", 1)[1])
                self.assertRegex(text, r"~\\dots\{\}\}? \\arxivreaderEndRef\{\d\}\}% \[arXivReader\]$")
                fit = re.match(r"\\arxivreaderFit\{(.*?~\\dots\{\})\}\{(.*)\}( \\arxivreaderEndRef.*)$", text)
                if fit:  # « keep » : début plus court choisi à la compilation dans une colonne étroite
                    self.assertEqual(layout, "keep")
                    narrow, text = fit.group(1) + fit.group(3), fit.group(2) + fit.group(3)
                    self.assertLess(len(narrow), texexport.NARROW["chars"] * 0.6 + 70, narrow)
                    self.assertLess(len(narrow), len(text))
                self.assertLess(len(text), texexport.LONG_CHARS[layout] * 0.6 + 70, text)
        # passage cité (s'écrit sans « ? ») repris au-dessus du texte entier
        self.assertIn(letters("Uniquealpha opening words"), letters(end))
        return texts, texts0, source

    def test_endnotes_article(self):
        """Article, mise en page « wide » : section des notes longues juste avant \\end{document}."""
        _, _, source = self.endnote_document("\\documentclass{article}")
        self.assertRegex(source, r"\n\\AtEndDocument\{% \[arXivReader\]\n\\clearpage% \[arXivReader\]\n(?:.*\n)*"
                                 r"\\endgroup% \[arXivReader\]\n\}% \[arXivReader\]\n% \[/arXivReader\]\n\\end\{document\}\n$")
        self.assertIn("% Notes longues (3)", source)

    def test_end_section_quote(self):
        """Section de fin : passage cité gardé s'il s'écrit sans « ? », omis sinon (formules extraites du PDF)."""
        from types import SimpleNamespace
        entries = [{"endnote": 1, "title": "Passage surligné, page 3", "quote": "p^{-n} κ_p(Z) = ∑ F_r(√p Z)",
                    "note": "Note one."},
                   {"endnote": 2, "title": "Passage surligné, page 4", "quote": "Café « déjà » – clean quote?",
                    "note": "Note two."}]
        plan = SimpleNamespace(words=texexport.WORDS["fr"], known=set(), long_entries=entries,
                               _margin_title=lambda entry: entry["title"])
        text = "\n".join(texexport._end_section(plan))
        self.assertNotIn("Z) =", text)
        self.assertIn("clean quote?", text)
        self.assertFalse(texexport._text_only("κ ⨁"))
        self.assertTrue(texexport._text_only("Café « déjà » – x? → y"))

    def test_endnotes_keep_narrow(self):
        """« keep » dans une colonne de moins de 2,5 cm (geometry, marges de 2,4 cm) : début plus court en marge,
        choisi à la compilation (\\arxivreaderFit)."""
        texts, texts0, source = self.endnote_document("\\documentclass{article}\n\\usepackage[margin=2.4cm]{geometry}",
                                                      layout="keep")
        self.assertIn("\\arxivreaderFit{", source)
        margin = texexport.plain_letters(" ".join(c[3] for page in self.chunks(self.pdf)[:len(texts0)]
                                                  for c in page[1] if 6.8 <= c[2] <= 7.2))
        self.assertIn(texexport.plain_letters("Upsilon long note"), margin)
        self.assertNotIn(texexport.plain_letters("the estimate and the"), margin)  # coupé avant (normal : gardé)

    def test_endnotes_amsart_keep(self):
        """amsart (recto verso, en-têtes en 7 pt), mise en page « keep » (notes en \\scriptsize) ; adresse de l'auteur
        (imprimée par \\end{document}) toujours sur la dernière page du texte, avant les notes longues."""
        texts, texts0, _ = self.endnote_document("\\documentclass{amsart}\n\\address{Uniqueaddress Street}", layout="keep")
        self.assertIn("Uniqueaddress", texts0[-1])
        self.assertIn("Uniqueaddress", texts[len(texts0) - 1])
        margin = texexport.plain_letters(" ".join(c[3] for page in self.chunks(self.pdf)[:len(texts0)]
                                                  for c in page[1] if 6.8 <= c[2] <= 7.2))
        self.assertIn(texexport.plain_letters("the estimate and the"), margin)  # colonne large : début normal

    def test_endnotes_book_twoside_hyperref(self):
        """book recto verso avec hyperref : renvois en liens (\\hyperlink, \\pageref*), compteurs et table des matières
        du document intacts."""
        texts, _, source = self.endnote_document("\\documentclass[twoside]{book}\n\\usepackage{hyperref}", chapter=True)
        import pypdf
        links = [annot.get_object().get("/A", {}).get("/D") for page in pypdf.PdfReader(str(self.pdf)).pages
                 for annot in page.get("/Annots") or []]
        self.assertIn("arxivreader-end-1", [str(link) for link in links])
        self.assertIn("arxivreader-note-3", [str(link) for link in links])
        self.assertNotIn("\\section", source.split("% [arXivReader] Notes de relecture")[1])

    def test_endnotes_after_bibliography(self):
        """\\end{document} juste après la bibliographie : notes longues après elle ; \\end{document} sur la ligne d'un
        autre texte : section ajoutée depuis le préambule (\\AtEndDocument), même résultat."""
        texts, texts0, _ = self.endnote_document("\\documentclass{article}", bibliography=True)
        self.assertIn("Uniquebib", texts0[-1])
        self.assertNotIn("Uniquebib", " ".join(texts[len(texts0):]))
        main_tex = (self.project / "main.tex").read_text(encoding="utf-8")
        (self.project / "main.tex").write_text(main_tex.replace("\\end{thebibliography}\n\\end{document}",
                                                                "\\end{thebibliography}\\end{document}"), encoding="utf-8", newline="\n")
        self.pid = texfile.import_tex(self.project / "main.tex")["paper"]["id"]
        source = self.annotated("todo", "main.tex")
        self.assertIn("\\AtEndDocument{% [arXivReader]", source.split("\n\\begin{document}\n")[0])
        texts1 = self.compile_copy("todo")
        self.assertEqual(len(texts1), len(texts))
        self.assertIn(texexport.plain_letters("Finalwordchi"), texexport.plain_letters(texts1[-1]))

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
            r"\end{document}", ""]), encoding="utf-8", newline="\n")
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
