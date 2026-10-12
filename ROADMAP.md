# Roadmap

arXivReader works well for one mathematician on one Mac. These are the steps that would let it serve many
readers, roughly in order of impact. Each one is a GitHub issue with the context, the code involved and what
"done" means: comment there before starting, so that work is not duplicated.

## Easy to install and keep up to date

- [#1](https://github.com/seldon39/arxivreader/issues/1) Sign and notarize the Mac app (Developer ID): no more
  Gatekeeper warning on other Macs.
- [#2](https://github.com/seldon39/arxivreader/issues/2) Signed in-app updates.

## More platforms, more devices

- [#3](https://github.com/seldon39/arxivreader/issues/3) Windows version (preview).
- [#4](https://github.com/seldon39/arxivreader/issues/4) Read on iPad.
- [#5](https://github.com/seldon39/arxivreader/issues/5) Sync the library, notes and highlights across devices.

## Reading and library

- [#6](https://github.com/seldon39/arxivreader/issues/6) Full-text search across the library.
- [#7](https://github.com/seldon39/arxivreader/issues/7) Export references as BibTeX and copy a citation
  (good first issue).
- [#13](https://github.com/seldon39/arxivreader/issues/13) Drawings LaTeXML cannot convert (PSTricks, picture).
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
