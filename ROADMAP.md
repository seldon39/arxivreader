# Roadmap

arXivReader works well for one mathematician on macOS and Windows, and from source on Linux. These are the steps that would let it serve many
readers, roughly in order of impact. Each one is a GitHub issue with the context, the code involved and what
"done" means: comment there before starting, so that work is not duplicated.

## Easy to install and keep up to date

- [#1](https://github.com/seldon39/arxivreader/issues/1) Sign and notarize the Mac app (Developer ID): no more
  Gatekeeper warning on other Macs.
- [#2](https://github.com/seldon39/arxivreader/issues/2) Signed in-app updates.

## More platforms, more devices

- ~~[#3](https://github.com/seldon39/arxivreader/issues/3) Windows version~~: done in 1.9.15–1.9.22 (downloaded
  from each [release](https://github.com/seldon39/arxivreader/releases/latest)); a signed Windows build would be the
  next step. Linux runs from source; a packaged Linux app (AppImage) could follow.
- [#4](https://github.com/seldon39/arxivreader/issues/4) Read on iPad.
- [#5](https://github.com/seldon39/arxivreader/issues/5) Sync the library, notes and highlights across devices.
  First step done in 1.9.31: signed export and import of the library and notes (a synced folder can carry it);
  automatic sync remains.

## Reading and library

- [#6](https://github.com/seldon39/arxivreader/issues/6) Full-text search across the library.
- [#7](https://github.com/seldon39/arxivreader/issues/7) Export references as BibTeX and copy a citation
  (good first issue).
- [#13](https://github.com/seldon39/arxivreader/issues/13) Drawings LaTeXML cannot convert (PSTricks, picture).
- Your own TeX files (papers and books being written): compiled PDF with statements, checks and recompiling, and an
  experimental HTML version made on demand by LaTeXML (if installed), also for arXiv papers without HTML: done in
  1.9.32; figures, PDF images and lost bibliographies checked on 15 recent arXiv papers in 1.10.4. Books of
  400–500 pages no longer stopped after 10 minutes (time limit following the size of the source; biblatex
  bibliographies in a single pass): 1.11.25. Next: faster or partial conversion of very long documents; line spacing inside multi-line TikZ nodes.
- AI tab as a conversation (Questions, Summary and Review threads, field at the bottom, ↩ Reply, ↑ ↓): done in
  1.11.0. Memory of each thread (earlier exchanges of the thread sent with each question): done in 1.11.26.
- Cross-review by two assistants (independent reviews, comparison, tagged synthesis): done in 1.11.11. Next:
  a third assistant to write the synthesis, when there is one.
- Books: summary and review chapter by chapter, with the statements of other chapters the chapter uses and those
  that depend on it (statements only): done in 1.11.18. Statements tab following the chapter being read: 1.11.23.
  All chapters in parallel (two at a time, a background task of the server), then a synthesis that compares the
  chapter records, or an overall summary (replaces "Whole book"): done in 1.11.26. Reviews rewritten as a referee's
  report (verdict, Critical / Serious / Minor, certain or suspected): 1.11.26; each point as a short title and prose,
  repair included, without the repeated "Main results" and "What survives" sections: 1.11.27.
- Long papers: Scope menu in Summary and Review (full paper, all sections in parallel then synthesis, one section with
  the whole paper sent and the statements linked to it, following where each proof is), and graph by section: done in 1.11.31.
- Faster HTML conversion of long documents (idea, to be decided): LaTeXML run in parallel on blocks of about 20
  pages or more, cut at the nearest section or chapter (counters taken from the PDF compilation, cross-references,
  macros and one bibliography kept across blocks), then put back together as one page; for books and long papers,
  about 4–5 times faster in a first test. Falls back to the whole-document conversion if a block fails.
- AI answers adapted to the reader (profile and a free "Your profile" field sent with every request): done in
  1.11.4. Next: a "Link with my work" action that sends the reader's own papers in full when they fit.
- Lighter reading of long papers full of formulas (sections far from the window not drawn, a third of the memory):
  done in 1.11.23.
- [#12](https://github.com/seldon39/arxivreader/issues/12) Cancelling a related-papers build should interrupt
  the request in flight.

## Code health (for more contributors)

- [#8](https://github.com/seldon39/arxivreader/issues/8) Translate code comments to English (good first issue,
  one module at a time).
- [#9](https://github.com/seldon39/arxivreader/issues/9) Automated interface tests (WebKit).
- [#10](https://github.com/seldon39/arxivreader/issues/10) Split the largest front-end modules.
- [#11](https://github.com/seldon39/arxivreader/issues/11) Warning icon never shown in notices (good first
  issue).
- Major updates to plan: MathJax 4 (vendored 3.2.2: new fonts and configuration, formulas to check on many papers) and
  Python 3.15 for the bundled interpreter (all compiled dependencies already ship 3.15 wheels; Linux CI tests it).

## What will not change

Whatever the scale, the principles in [CONTRIBUTING.md](CONTRIBUTING.md#principles) stay: local first, the AI
assistant without tools or network, no telemetry, the user's keys and data never leave their machine unasked.
