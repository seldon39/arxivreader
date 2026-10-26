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
  1.9.32; figures, PDF images and lost bibliographies checked on 15 recent arXiv papers in 1.10.4. Next: faster
  or partial conversion of very long documents; line spacing inside multi-line TikZ nodes.
- AI tab as a conversation (Questions, Summary and Review threads, field at the bottom, ↩ Reply, ↑ ↓): done in
  1.11.0. Next: pass the whole chain of follow-ups to the assistant, not only the previous exchange.
- AI answers adapted to the reader (profile and a free "Your profile" field sent with every request): done in
  1.11.4. Next: a "Link with my work" action that sends the reader's own papers in full when they fit.
- [#12](https://github.com/seldon39/arxivreader/issues/12) Cancelling a related-papers build should interrupt
  the request in flight.

## Code health (for more contributors)

- [#8](https://github.com/seldon39/arxivreader/issues/8) Translate code comments to English (good first issue,
  one module at a time).
- [#9](https://github.com/seldon39/arxivreader/issues/9) Automated interface tests (WebKit).
- [#10](https://github.com/seldon39/arxivreader/issues/10) Split the largest front-end modules.
- [#11](https://github.com/seldon39/arxivreader/issues/11) Warning icon never shown in notices (good first
  issue).

## What will not change

Whatever the scale, the principles in [CONTRIBUTING.md](CONTRIBUTING.md#principles) stay: local first, the AI
assistant without tools or network, no telemetry, the user's keys and data never leave their machine unasked.
