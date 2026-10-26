# Contributing to arXivReader

Thank you for your interest. arXivReader is a reader for arXiv papers built for mathematics (see
[README.md](README.md)). Bug reports, papers that display badly, ideas and code are all welcome.

## Reporting a problem

[Open an issue](https://github.com/seldon39/arxivreader/issues) with:

- the **arXiv identifier and version** of the paper (e.g. `arXiv:2609.40232v1`) — most display problems are
  specific to one paper's LaTeXML conversion;
- the **app version** (Settings → Updates) and your system (macOS or Windows, with its version);
- a **screenshot** or a short screen recording, and what you expected instead.

To see whether a problem comes from arXivReader or from arXiv's own HTML, compare with
`https://arxiv.org/html/<id>` (or `https://ar5iv.labs.arxiv.org/html/<id>` for older papers) and with the PDF.

For a TeX file of your own (statements numbered wrongly, compilation report), a minimal `.tex` that shows the
problem helps most, with the TeX distribution and its year (`latexmk -v`, `pdflatex --version`).

## Where to start

- [ROADMAP.md](ROADMAP.md) lists the larger steps, each with its issue.
- Issues labelled [good first issue](https://github.com/seldon39/arxivreader/labels/good%20first%20issue) are
  small and self-contained.
- Comment on an issue before starting on it.

## Principles

These are not negotiable; a change that weakens one of them will not be merged.

- **Local first.** The server listens on 127.0.0.1 only; the `local_only` middleware (`app/main.py`) refuses
  foreign hosts, cross-site requests and non-JSON writes. Data lives in the user's library folder.
- **The AI assistant has no tools and no network**, and sees only the paper. Text hidden in a paper (white,
  tiny, invisible) is flagged to the reader and never sent to the assistant (`app/structure.py`, `app/ai.py`):
  it can carry prompt injections. The reader's profile (`ai.reader_profile`) goes into the system prompt with titles
  and names escaped; it adapts answers, never the rules (a review stays as critical for everyone).
- **No telemetry, no accounts.** The only network calls are the services listed in `MAINTENANCE.md` §6 (arXiv,
  Semantic Scholar, Zotero, GitHub releases, the AI provider the user chose).
- **The user's keys** (Semantic Scholar, Gemini, Zotero) are typed by the user in the settings; they never
  appear in code, logs or error messages.
- **Zotero: no item is ever deleted or trashed.** Only the "arXivReader" collection and its sub-collections are
  modified (a sub-collection is removed when its folder is deleted in arXivReader; the papers stay in Zotero).
- **No long requests from the page**: anything slow (imports, related-papers graphs, compiling a TeX file) runs in
  the background and is polled, because WebKit may drop a request left unanswered for about a minute.
- **The user's files are read, never written.** A TeX file chosen by the user is compiled in a work folder of the
  app (latexmk `-outdir`), never in its own folder, without shell escape; imported PDFs and notes exports are
  copies. External programs (TeX, and LaTeXML for the experimental HTML) run only on files the user chose.

## Development setup

Requirements: Python 3.12 or later (3.14 is the version the apps ship; CI also tests 3.12 and 3.13 on Linux) on
macOS — or on Windows/Linux for the server and the tests —, and Xcode command-line tools to build the Mac app. The Windows app (`windows/`: a C# window around
WebView2, and the server built with PyInstaller) is built and tested by GitHub Actions on every push to `main`
(`.github/workflows/windows.yml`, which also runs `windows/smoke.ps1` on the built app); the test suite also runs on
Linux (`.github/workflows/linux.yml`, with the `./arxivreader` script itself) and passes on the three systems,
Mac-only tests being skipped on Windows.

```bash
git clone https://github.com/seldon39/arxivreader.git && cd arxivreader
./arxivreader            # creates .venv from requirements.lock, starts the server, opens the browser
```

The server runs at http://127.0.0.1:8710 with the library in `library/`. For experiments, use another port
and a **copy** of a library, never someone's real one:

```bash
ARXIVREADER_PORT=8711 ARXIVREADER_LIBRARY=/tmp/test-library ARXIVREADER_NO_ALERTS=1 \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8711
```

(`ARXIVREADER_PORT` must match `--port`: the server refuses any other `Host`.) When copying a real library,
remove the keys first: `DELETE FROM settings WHERE key LIKE '%ApiKey%'` in the copy's `arxivreader.db` — the
Zotero sync starts 30 s after launch.

The Mac app is built with `macos/build.sh` (into `~/Library/Caches/arXivReader-build`, outside iCloud-synced
folders, which break code signing). It can run on a library copy without being installed:
`ARXIVREADER_LIBRARY=… ARXIVREADER_START=#/paper/<id> ~/Library/Caches/arXivReader-build/arXivReader.app/Contents/MacOS/arXivReader`;
`ARXIVREADER_WINDOWSHOT=file.png` saves a screenshot of the window and `ARXIVREADER_EVAL` runs JavaScript once
the page is loaded.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

The tests are offline: every external service is simulated. To make sure none goes out, run them with
`HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9` (known exception: the Gemini tests use a local
fake server that the proxy intercepts). `./arxivreader check` calls the real services, one minimal request each,
to detect changes in their answers.

There are no automated interface tests yet ([#9](https://github.com/seldon39/arxivreader/issues/9)): check
interface changes by hand in WebKit (Safari or the Mac app), in light and dark mode, below 900 px and on wide
screens, and on several papers — a fix for one paper's LaTeXML output often breaks another's.

## Code

- **Layout**: see [README.md → Code layout](README.md#code-layout). Python server in `app/` (FastAPI, SQLite),
  interface in `static/` (plain JavaScript modules, no build step, MathJax and pdf.js vendored), Mac shell in
  `macos/main.swift`.
- **Maintenance guide**: [MAINTENANCE.md](MAINTENANCE.md) (in French for now): rules, work loop, release, what to
  do when an external service changes, WebKit checks, known pitfalls. It is also what AI coding assistants read
  (`CLAUDE.md`, `AGENTS.md`).
- **Comments** are mostly in French; new comments may be in English, and translating existing ones is welcome
  ([#8](https://github.com/seldon39/arxivreader/issues/8)). Comments explain *why* (a LaTeXML quirk, a WebKit
  pitfall), not what the line does.
- **Interface strings** go in `static/js/i18n.js`, in both French and English, with the same placeholders.
- **Dependencies**: `requirements.txt` lists what is wanted, `requirements.lock` the exact tested versions. Add
  or update one deliberately, run the tests, then `./arxivreader lock`.
- **Settings**: a new value of a setting must also be accepted by the server (`SETTING_CHOICES` in
  `app/main.py`), otherwise it is silently not saved.
- **Analysis changes**: when `app/structure.py` or `app/texstructure.py` changes what it extracts or marks, bump
  `STRUCTURE_VERSION` so imported papers are re-analysed.
- **Followed authors**: recognise a followed author's paper only through `relevance.follows` (names, namesakes set
  aside, categories kept for that author).

## Pull requests

- One coherent change per pull request; commit messages in English, imperative, saying what changed and why.
- Tests pass; new behaviour comes with a test where the server is involved.
- `README.md` and `README.fr.md` are updated together when a visible behaviour changes.
- Mention the papers you checked the change on.

Releases (version number in `app/version.py`, `macos/release.sh`) are made by the maintainer.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
