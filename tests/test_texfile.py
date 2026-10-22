"""Fichiers TeX de l'ordinateur : lecture de la source, identifiants stables, bibliographie, compilation (quand TeX
est installé), livres (numérotation par chapitre), place d'un passage retrouvé après recompilation."""

import pathlib
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import annotations, db, library, main, texfile, texstructure

ARTICLE = r"""\documentclass{amsart}
\usepackage{amsthm}
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{proposition}[theorem]{Proposition}
\title{Averages on the sphere\thanks{Draft}}
\author{First Author}
\author{Second Author}
\begin{document}
\begin{abstract}We study averages.\end{abstract}
\maketitle
\input{sections/intro}
\section{Results}
\begin{lemma}\label{lem:a}Lemma A.\end{lemma}
\begin{proof}See \cite{knuth}.\end{proof}
\begin{lemma}\label{lem:unused}Unused.\end{lemma}
\begin{proof}Trivial.\end{proof}
\begin{theorem}\label{thm:main}By Lemma~\ref{lem:a} and \ref{lem:trace}.\end{theorem}
\begin{proposition}\label{prop:b}No proof.\end{proposition}
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""
BIB = "@book{knuth, author={Donald Knuth}, title={The TeXbook}, year={1984}, publisher={AW}}\n"
BOOK = r"""\documentclass{book}
\usepackage{amsthm}
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
\begin{document}
\chapter{First}
\section{Start}
\begin{lemma}\label{l1}A.\end{lemma}\begin{proof}x\end{proof}
\chapter{Second}
\section{Next}
\begin{theorem}B by Lemma \ref{l1}.\end{theorem}\begin{proof}By \ref{l1}.\end{proof}
\appendix
\chapter{Extra}
\section{More}
\begin{lemma}C.\end{lemma}
\end{document}
"""
HAS_TEX = bool(texfile.find_tool("latexmk") or texfile.find_tool("pdflatex"))


class TexFile(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY, library.PAPERS)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.tmp / "test.db", self.tmp, self.tmp / "papers"
        db.init()
        self.build = mock.patch.object(texfile, "build_dir", lambda pid: (self.tmp / "build" / pid).mkdir(parents=True, exist_ok=True)
                                       or self.tmp / "build" / pid)
        self.build.start()
        self.project = self.tmp / "my draft"  # espace dans le chemin, comme souvent
        (self.project / "sections").mkdir(parents=True)
        (self.project / "main.tex").write_text(ARTICLE, encoding="utf-8")
        (self.project / "sections" / "intro.tex").write_text("\\section{Introduction}\nSee \\cite{knuth}.\n", encoding="utf-8")
        (self.tmp / "bib").mkdir()
        (self.tmp / "bib" / "refs.bib").write_text(BIB, encoding="utf-8")
        self.client = TestClient(main.app, base_url=f"http://{main.HOST}:{main.PORT}")

    def tearDown(self):
        self.build.stop()
        db.DB_PATH, db.LIBRARY, library.PAPERS = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_source_meta_and_bibliographies(self):
        files = texfile.read_project(self.project / "main.tex")
        self.assertEqual(sorted(files), ["main.tex", "sections/intro.tex"])
        text = texstructure._expand(files, "main.tex")
        self.assertEqual(texfile._meta(text, "main"), ("Averages on the sphere", ["First Author", "Second Author"], "We study averages."))
        self.assertEqual(texfile._bibliographies(text), ["refs.bib"])
        self.assertEqual(texfile._bibliographies(r"\addbibresource{a.bib}\bibliography{b,c}"), ["a.bib", "b.bib", "c.bib"])

    def test_stable_ids_follow_labels(self):
        structure = texfile.stable_ids(texstructure.extract(ARTICLE))
        ids = [n["id"] for n in structure["nodes"]]
        self.assertEqual(ids, ["tex-lbl-lem:a", "tex-lbl-lem:unused", "tex-lbl-thm:main", "tex-lbl-prop:b"])
        lemma = structure["nodes"][0]
        self.assertEqual(lemma["usedBy"], ["tex-lbl-thm:main"])
        self.assertEqual(lemma["proofs"], ["tex-lbl-lem:a-proof-1"])
        self.assertEqual(structure["labels"]["lem:a"], "tex-lbl-lem:a")
        # un énoncé ajouté avant : mêmes identifiants pour les énoncés qui ont un \label
        shifted = ARTICLE.replace(r"\section{Results}", "\\section{Results}\n\\begin{lemma}New.\\end{lemma}")
        self.assertEqual([n["id"] for n in texfile.stable_ids(texstructure.extract(shifted))["nodes"]][1:], ids)

    def test_book_numbered_by_chapter(self):
        structure = texstructure.extract(BOOK)
        self.assertTrue(structure["book"])
        self.assertEqual([(s["title"], s["level"]) for s in structure["toc"]],
                         [("1. First", 0), ("1.1. Start", 1), ("2. Second", 0), ("2.1. Next", 1), ("A. Extra", 0), ("A.1. More", 1)])
        self.assertEqual([n["label"] for n in structure["nodes"]], ["Lemma 1.1.1", "Theorem 2.1.1", "Lemma A.1.1"])
        self.assertFalse(texstructure.extract(ARTICLE)["book"])
        self.assertEqual(texstructure.extract(ARTICLE)["toc"][0]["title"], "1. Results")  # article : inchangé

    def test_rejects_non_main_files(self):
        with self.assertRaises(texfile.TexError):
            texfile.main_file(self.project / "sections" / "intro.tex")
        with self.assertRaises(texfile.TexError):
            texfile.main_file(self.tmp / "bib" / "refs.bib")
        response = self.client.post("/api/papers/tex", json={"path": str(self.project / "sections" / "intro.tex")})
        self.assertEqual(response.status_code, 400)

    def test_without_tex_reads_pdf_next_to_file(self):
        from tests.test_localpdf import make_pdf
        (self.project / "main.pdf").write_bytes(make_pdf(["Averages on the sphere", "Lemma 1.1. Lemma A."]))
        with mock.patch.object(texfile, "find_tool", lambda name: None):
            result = texfile.import_tex(self.project / "main.tex")
        self.assertEqual(result["report"]["pdfFrom"], "existing")
        paper = result["paper"]
        self.assertEqual((paper["source"], paper["title"], paper["arxiv_id"]), ("local-tex", "Averages on the sphere", ""))
        self.assertEqual(result["report"]["missingBibs"], ["refs.bib"])
        (self.project / "main.pdf").unlink()
        with mock.patch.object(texfile, "find_tool", lambda name: None):
            again = texfile.recompile(paper["id"])
        self.assertFalse(again["report"]["pdf"])  # ni TeX ni PDF : l'article reste (PDF précédent)
        self.assertTrue((library.paper_dir(paper["id"]) / "paper.pdf").exists())
        # corrigés à la main : titre et auteurs gardés aux compilations suivantes
        self.client.put(f"/api/papers/{paper['id']}/meta", json={"title": "Better title", "authors": ["A. B."]})
        (self.project / "main.pdf").write_bytes(make_pdf(["x"]))
        with mock.patch.object(texfile, "find_tool", lambda name: None):
            texfile.recompile(paper["id"])
        self.assertEqual(db.get_paper(paper["id"])["title"], "Better title")
        # rien à mettre à jour depuis arXiv
        self.assertEqual(self.client.post(f"/api/papers/{paper['id']}/refresh", json={}).status_code, 400)

    @unittest.skipUnless(HAS_TEX, "TeX n'est pas installé")
    def test_compile_missing_bib_then_chosen(self):
        response = self.client.post("/api/papers/tex", json={"path": str(self.project / "main.tex")})
        job = response.json()["job"]
        state = self.wait(job)
        report = state["result"]["report"]
        self.assertTrue(report["pdf"])
        self.assertEqual(report["missingBibs"], ["refs.bib"])
        self.assertEqual(report["undefinedRefs"], ["lem:trace"])
        self.assertEqual((report["statements"], report["withoutProof"]), (4, 2))
        self.client.post(f"/api/papers/{job}/bib", json={"name": "refs.bib", "path": str(self.tmp / "bib" / "refs.bib")})
        report = self.wait(job)["result"]["report"]
        self.assertEqual((report["missingBibs"], report["undefinedCites"]), ([], []))
        self.assertEqual(report["bibs"][0]["entries"], 1)
        self.assertEqual(self.client.get(f"/api/papers/{job}/texfile").json()["report"]["bibs"][0]["name"], "refs.bib")
        structure = self.client.get(f"/api/papers/{job}").json()["structure"]
        self.assertEqual(structure["nodes"][0]["id"], "tex-lbl-lem:a")
        self.assertEqual(structure["nodes"][0]["page"], 1)
        self.assertNotIn("main.aux", [p.name for p in self.project.iterdir()])  # rien d'écrit chez l'utilisateur

    def wait(self, job):
        for _ in range(600):
            state = self.client.get(f"/api/tex/job/{job}").json()
            if not state["running"]:
                return state
            time.sleep(0.2)
        self.fail("compilation trop longue")

    def test_relocated_highlight(self):
        db.save_paper({"id": "tex-0", "arxiv_id": "", "version": None, "title": "T", "authors": [], "abstract": "", "categories": [],
                       "primary_category": "", "published": None, "updated": None, "doi": None, "journal_ref": None,
                       "comment": "", "source": "local-tex", "in_library": 1})
        item = annotations.create("tex-0", "highlight", {"pdf": {"page": 1, "start": 0, "endPage": 1, "end": 5}}, quote="Lemma")
        span = {"page": 2, "start": 10, "endPage": 2, "end": 15}
        self.assertEqual(self.client.put(f"/api/annotations/{item['id']}", json={"pdf": span}).json()["target"]["pdf"], span)
        self.assertEqual(self.client.put(f"/api/annotations/{item['id']}", json={"pdf": {"page": "x"}}).status_code, 400)


    def test_frontmatter_chapters_unnumbered(self):
        book = BOOK.replace(r"\begin{document}", "\\begin{document}\n\\frontmatter\n\\chapter{Preface}\n\\mainmatter")
        structure = texstructure.extract(book)
        self.assertEqual([s["title"] for s in structure["toc"]][:3], ["Preface", "1. First", "1.1. Start"])
        self.assertEqual(structure["nodes"][0]["label"], "Lemma 1.1.1")
        self.assertIn("appendix.A", next(s for s in structure["toc"] if s["title"] == "A. Extra")["anchors"])

    def test_busy_and_bad_pdf(self):
        self.assertTrue(texfile.start("tex-busy", time.sleep, 0.5))
        self.assertFalse(texfile.start("tex-busy", time.sleep, 0))  # déjà en cours : refusée
        with self.assertRaises(texfile.TexError):
            texfile.import_tex(self.project / "main.tex", pdf=self.tmp / "bib" / "refs.bib")

    @unittest.skipUnless(HAS_TEX, "TeX n'est pas installé")
    def test_include_in_subfolder_and_renamed_bib(self):
        (self.project / "chapters").mkdir()
        (self.project / "chapters" / "one.tex").write_text("\\section{One}\nSee \\cite{knuth}.\n", encoding="utf-8")
        (self.project / "main.tex").write_text(ARTICLE.replace("\\input{sections/intro}", "\\include{chapters/one}"), encoding="utf-8")
        renamed = self.tmp / "bib" / "references.bib"
        renamed.write_text(BIB, encoding="utf-8")
        result = texfile.import_tex(self.project / "main.tex")
        self.assertTrue(result["report"]["pdf"])
        self.assertEqual(result["report"]["missingBibs"], ["refs.bib"])
        report = texfile.choose_bib(result["paper"]["id"], "refs.bib", renamed)["report"]
        self.assertEqual((report["missingBibs"], report["undefinedCites"]), ([], []))


    def test_second_audit_structure(self):
        # \input sans accolades, preuve dans une preuve, compteur numéroté dans un théorème
        (self.project / "sections" / "a.tex").write_text("\\begin{theorem}\\label{t:in}In a file.\\end{theorem}\n", encoding="utf-8")
        text = ARTICLE.replace("\\input{sections/intro}", "\\input{sections/intro}\n\\section{S}\n\\input sections/a")
        (self.project / "main.tex").write_text(text, encoding="utf-8")
        files = texfile.read_project(self.project / "main.tex")
        self.assertIn("sections/a.tex", files)
        source = r"""\documentclass{amsart}\newtheorem{theorem}{Theorem}[section]\newtheorem{claim}{Claim}[theorem]
\begin{document}\section{A}\begin{theorem}T\end{theorem}\begin{proof}\begin{claim}c\end{claim}\begin{proof}pc\end{proof}
\begin{claim}d\end{claim}\end{proof}\end{document}"""
        nodes = texstructure.extract(source)["nodes"]
        self.assertEqual([(n["label"], len(n["proofs"])) for n in nodes], [("Theorem 1.1", 1), ("Claim 1.1.1", 1), ("Claim 1.1.2", 0)])

    def test_unlabeled_statements_keep_their_notes(self):
        ids = [n["id"] for n in texfile.stable_ids(texstructure.extract(BOOK))["nodes"]]
        shifted = BOOK.replace(r"\section{Next}", "\\section{Next}\n\\begin{lemma}Inserted.\\end{lemma}")
        new = [n["id"] for n in texfile.stable_ids(texstructure.extract(shifted))["nodes"]]
        self.assertTrue(set(ids) <= set(new))  # l'énoncé sans \label (« Theorem B ») garde son identifiant
        self.assertTrue(any(i.startswith("tex-txt-theorem-") for i in ids))

    def test_aux_numbers_and_anchors(self):
        structure = texstructure.extract(ARTICLE.replace("No proof.", "No proof \\begin{equation}x\\label{eq:x}\\end{equation}"))
        out = self.tmp / "out"
        out.mkdir()
        (out / "main.aux").write_text("\\newlabel{lem:a}{{2.1}{1}{}{theorem.2.1}{}}\n\\newlabel{prop:b}{{2.7}{2}{}{theorem.2.7}{}}\n"
                                      "\\newlabel{eq:x}{{2.1}{2}{}{equation.2.1}{}}\n\\newlabel{prop:b@cref}{{[proposition][7][2]2.7}{[1][2][]2}}\n")
        texfile.apply_aux(structure, out)
        nodes = {n["label"]: n for n in structure["nodes"]}
        self.assertIn("Proposition 2.7", nodes)  # numéro imprimé par LaTeX
        self.assertEqual(nodes["Proposition 2.7"]["anchors"][0], "theorem.2.7")
        self.assertEqual(structure["equations"]["eq:x"], {"label": "(2.1)", "owner": structure["labels"]["prop:b"], "anchors": ["equation.2.1"]})

    def test_biblatex_and_page_search(self):
        bbl = r"""\refsection{0}\entry{knuth}{book}{}\name{author}{1}{}{{{hash=a}{family={Knuth}, familyi={K\bibinitperiod}, given={Donald}, giveni={D\bibinitperiod}}}}
\field{title}{The {\TeX}book}\field{year}{1984}\field{labelalpha}{Knu84}\verb{doi}
\verb 10.1000/xyz
\endverb
\endentry"""
        entries = texstructure.bibliography(bbl)
        self.assertEqual((entries[0]["id"], entries[0]["label"], entries[0]["doi"]), ("bib.knuth", "Knu84", "10.1000/xyz"))
        self.assertIn("Donald Knuth", entries[0]["text"])
        texts = ["Contents 1. Intro 2. Results 3. Proofs", "we prove Theorem 2.1. later", "1. Intro text", "Deﬁnition 2.1. A thing"]
        import unicodedata  # texte des pages normalisé par locate_pages : ligatures défaites
        self.assertEqual(texstructure._search([unicodedata.normalize("NFKC", t) for t in texts], {"label": "Definition 2.1"}), 4)
        self.assertEqual(texstructure._search(texts, {"label": "Theorem 2.1"}, start=3), 2)  # plus loin rien : avant
        self.assertEqual(texstructure._contents_pages(texts, [{"title": "1. Intro"}, {"title": "2. Results"}, {"title": "3. Proofs"}]), {1})

    def test_ai_tex_context_and_export_order(self):
        from app import ai, export
        structure = texfile.stable_ids(texstructure.extract(ARTICLE))
        context = ai._tex_context(structure)
        self.assertIn("lem:a = Lemma 1.1", context)
        db.save_paper({"id": "tex-x", "arxiv_id": "", "version": None, "title": "T", "authors": [], "abstract": "", "categories": [],
                       "primary_category": "", "published": None, "updated": None, "doi": None, "journal_ref": None, "comment": "",
                       "source": "local-tex", "in_library": 1})
        folder = library.paper_dir("tex-x")
        folder.mkdir(parents=True)
        import json
        (folder / "structure.json").write_text(json.dumps(structure))
        annotations.create("tex-x", "block", {"id": "tex-lbl-thm:main"}, note="on the theorem")
        annotations.create("tex-x", "block", {"id": "tex-lbl-lem:a"}, note="on the lemma")
        markdown = export.paper_markdown("tex-x", "en") if hasattr(export, "paper_markdown") else None
        if markdown:
            self.assertLess(markdown.index("on the lemma"), markdown.index("on the theorem"))  # ordre du document


    def test_implicit_dependencies(self):
        # les quatre tournures d'un vrai article : phrase qui précède un corollaire sans preuve, « by applying the
        # above », « the above lemma » dans le texte courant qui obtient une équation, corollaire juste après un lemme
        source = r"""\documentclass{amsart}\newtheorem{lemma}{Lemma}[section]\newtheorem{corollary}[lemma]{Corollary}
\newtheorem{theorem}[lemma]{Theorem}
\begin{document}\section{A}
\begin{lemma}We have \begin{equation}\label{eq:a} a = 1. \end{equation}\end{lemma}
\begin{proof}Clear.\end{proof}
The following corollary is a consequence of~\eqref{eq:a}.
\begin{corollary}\label{cor:a}So $a^2 = 1$.\end{corollary}
\begin{lemma}A general kernel identity.\end{lemma}
\begin{proof}Compute.\end{proof}
By applying the above to $P$ we get
\begin{equation}\label{eq:b} P = Q. \end{equation}
\begin{lemma}A Taylor identity.\end{lemma}

\begin{corollary}[{\cite[Corollary 2.7]{F}}]A consequence.\end{corollary}
From \eqref{eq:b} and the above lemma, we get
\begin{equation}\label{eq:c} R = S. \end{equation}
\begin{theorem}Main.\end{theorem}
\begin{proof}By \eqref{eq:b} and \eqref{eq:c}.\end{proof}
\end{document}"""
        structure = texstructure.extract(source)
        nodes = {n["label"]: n for n in structure["nodes"]}
        ids = {n["id"]: n["label"] for n in structure["nodes"]}
        used = lambda label: sorted(ids[u] for u in nodes[label]["usedBy"])  # noqa: E731
        self.assertEqual(used("Lemma 1.1"), ["Corollary 1.2"])
        self.assertTrue(nodes["Corollary 1.2"]["justifiedBy"])     # plus « preuve non trouvée »
        self.assertEqual(used("Lemma 1.3"), ["Theorem 1.6"])         # « by applying the above »
        self.assertEqual(used("Lemma 1.4"), ["Corollary 1.5", "Theorem 1.6"])  # corollaire qui suit ; « the above lemma »
        self.assertIn(ids[nodes["Lemma 1.4"]["id"]], [ids[u] for u in nodes["Theorem 1.6"]["weakUses"]])


    def test_bilingual_title_void_macro_and_greek_labels(self):
        from app.texsource import analyze_text
        source = r"""\documentclass{article}\newcommand{\comment}[1]{}
\title{Fubini\textendash Study forms \bigbreak \Large \textit{Formes de Fubini\textendash Study}}
\author{A. One, B. Two}
\comment{\author{A. One}\begin{theorem}Hidden.\end{theorem}}
\newtheorem{theorem}{Theorem}
\begin{document}\begin{enumerate}[label = (\greek*)]\item x\end{enumerate}\end{document}"""
        title, authors, _ = texfile._meta(texstructure.strip_hidden(source), "x")
        self.assertEqual((title, authors), ("Fubini–Study forms", ["A. One", "B. Two"]))
        self.assertEqual(texstructure.extract(source)["nodes"], [])  # énoncé dans \comment{…} : pas imprimé
        self.assertEqual(analyze_text(source)["enumerate"], [["(", "α", ")"]])


    def test_amsrefs_bibliography(self):
        bbl = r"""\begin{bibdiv}\begin{biblist}
\bib{BMMP14}{article}{
      author={Barron, T.},
      author={Ma, X.},
       title={Semi-classical properties of {B}erezin-{T}oeplitz operators},
     journal={J. Math. Phys.},
      volume={55},
        date={2014},
         doi={10.1063/1.4870869},
}
\end{biblist}\end{bibdiv}"""
        entry = texstructure.bibliography(bbl)[0]
        self.assertEqual((entry["id"], entry["label"], entry["doi"]), ("bib.BMMP14", "1", "10.1063/1.4870869"))
        self.assertEqual(entry["text"], "Barron, T., Ma, X., Semi-classical properties of Berezin-Toeplitz operators, J. Math. Phys., 55, 2014")


if __name__ == "__main__":
    unittest.main()
