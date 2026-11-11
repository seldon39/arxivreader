# Contributing to arXivReader

Thank you for your interest. arXivReader is a reader for arXiv papers built for mathematics (see
[README.md](README.md)). Bug reports, papers that display badly, ideas and code are all welcome.

## Reporting a problem

[Open an issue](https://github.com/seldon39/arxivreader/issues) with:

- the **arXiv identifier and version** of the paper (e.g. `arXiv:2609.40232v1`) — most display problems are
  specific to one paper's LaTeXML conversion;
- the **app version** (Settings → About) and your system (macOS, Windows or Linux, with its version);
- a **screenshot** or a short screen recording, and what you expected instead.

To see whether a problem comes from arXivReader or from arXiv's own HTML, compare with
`https://arxiv.org/html/<id>` (or `https://ar5iv.labs.arxiv.org/html/<id>` for older papers) and with the PDF.

For a TeX file of your own (statements numbered wrongly, compilation report), a minimal `.tex` that shows the
problem helps most, with the TeX distribution and its year (`latexmk -v`, `pdflatex --version`).

## Where to start

- [ROADMAP.md](ROADMAP.md) lists the larger steps, most of the open ones with their issue.
- Issues labelled [good first issue](https://github.com/seldon39/arxivreader/labels/good%20first%20issue) are
  small and self-contained.
- Comment on an issue before starting on it.

## Principles

These are not negotiable; a change that weakens one of them will not be merged.

- **Local first.** The server listens on 127.0.0.1 only; the `local_only` middleware (`app/main.py`) refuses
  foreign hosts, cross-site requests, non-JSON writes and, when the launcher set one (`ARXIVREADER_TOKEN`), any
  `/api/` request without the session token (cookie set by `/?t=`, or the `X-ArXivReader-Token` header). Data
  lives in the user's library folder.
- **The AI assistant has no tools and no network**, and sees only the paper. Text hidden in a paper (white,
  tiny, invisible) is flagged to the reader and never sent to the assistant (`app/structure.py`, `app/ai.py`):
  it can carry prompt injections. Every request goes through `ai.build_prompt`, including the four of a
  cross-review (second assistant too), a book chapter's summary or review (other chapters: statements only) and a
  paper section's summary or review (the whole paper is sent, with the statements linked to the section; only if it
  is too long for the assistant, the section and the start of the paper); the synthesis of a whole book, or of a
  paper done section by section (`ai.book_prompt`, run by `app/bookjob.py`), receives the reviews or summaries of the
  chapters or sections, never the text. The reader's profile (`ai.reader_profile`) goes into the system prompt with titles
  and names escaped; it adapts answers, never the rules (a review stays as critical for everyone). A question may
  also receive another arXiv version of the same paper, the source of a TeX file's previous compilation or the paper's
  annotations, always in their own tagged blocks whose text has its tags neutralised (`ai._neutral`; a new block tag
  goes into `ai.PREVIOUS_TAGS`). The assistant only proposes annotations (to add, adapt or delete): the page applies
  them after the reader's choice, with Undo (`applyProposals` in `static/js/ai.js`).
- **No telemetry, no accounts.** The only network calls are the services listed in `MAINTENANCE.md` §6 (arXiv,
  Semantic Scholar, zbMATH's citation matcher, Zotero, GitHub releases, the AI provider the user chose). Links to
  outside sites (DOI, MathSciNet, zbMATH) open in the browser, only when clicked; the server asks zbMATH about a
  reference only when the reader points at its zbMATH link.
- **The user's keys** (Semantic Scholar, Gemini, Zotero) are typed by the user in the settings; they never
  appear in code, logs or error messages, and on macOS and Windows are stored in the system keychain
  (`app/keystore.py`, with `ARXIVREADER_KEYCHAIN=1`; on Linux, or if the keychain refuses, they stay in the library's
  database), never passed as a program argument.
- **Zotero: no item is ever deleted or trashed.** Only the "arXivReader" collection and its sub-collections are
  modified (a sub-collection is removed when its folder is deleted in arXivReader; the papers stay in Zotero).
- **No long requests from the page**: anything slow (imports, related-papers graphs, compiling a TeX file) runs in
  the background and is polled, because WebKit may drop a request left unanswered for about a minute.
- **The user's files are read, never written.** A TeX file chosen by the user is compiled in a work folder of the
  app (latexmk `-outdir`), never in its own folder; the app does not enable shell escape (TeX's default setting
  applies, usually restricted) and, like latexmk in a terminal, follows the folder's `latexmkrc` if it has one, which
  can run code (the READMEs tell users to import only documents they trust); imported PDFs and notes exports are copies. External programs run only on files the
  user chose (TeX) or, for LaTeXML and the experimental HTML, on those files and on the arXiv source of a paper
  without HTML, when the reader asks for that version. LaTeXML extensions shipped in that downloaded source (`.ltxml`,
  `.latexml`, Perl that LaTeXML runs without restriction) are never extracted (`latexml._extract`).

## Development setup

Requirements: Python 3.12 or later (3.14 is the version the apps ship; CI also tests 3.12, 3.13 and 3.15 on Linux) on
macOS — or on Windows/Linux for the server and the tests —, and Xcode command-line tools and miniforge (`conda`, Python taken from its cache) to build the Mac app. The Windows app (`windows/`: a C# window around
WebView2, and the server built with PyInstaller) is built and tested by GitHub Actions on every push to `main`
(`.github/workflows/windows.yml`, which also runs `windows/smoke.ps1` on the built app); the test suite also runs on
Linux (`.github/workflows/linux.yml`, with the `./arxivreader` script itself) and passes on the three systems,
Mac-only tests being skipped on Windows.

```bash
git clone https://github.com/seldon39/arxivreader.git && cd arxivreader
./arxivreader            # creates .venv from requirements.lock (macOS: in .venv.nosync, out of iCloud), starts the server, opens the browser
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
`ARXIVREADER_NO_ALERTS=1 ARXIVREADER_LIBRARY=… ARXIVREADER_START=#/paper/<id> ~/Library/Caches/arXivReader-build/arXivReader.app/Contents/MacOS/arXivReader`;
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

Since 1.12, `tests/test_versions.py` covers paper versions (update, interrupted update, archived versions, the
Versions menu routes, notes import and export, the feed's new versions), `tests/test_fulltext.py` the library's
full-text search, `tests/test_texfile.py` the notes that lose their place after a TeX file is edited,
`tests/test_localpdf.py` the bibliography look-ups (arXiv, zbMATH) and `tests/test_ai.py` the detection of
questions about other versions, notes to carry over or delete.

Since 1.13, `tests/test_safety.py` covers the daily copy and the integrity check, `tests/test_bibtex.py` BibTeX,
`tests/test_ai_honesty.py`, `tests/test_ai_rigor.py` and `tests/test_ai_verify.py` what the assistant says it read and the
review checked on demand, `tests/test_notes_reliability.py` notes between computers, `tests/test_position.py` and
`tests/test_trail.py` the reading position and back / forward (the page's pure functions, run with Node), and
`tests/test_hardening.py` the other fixes.

There are no automated interface tests yet ([#9](https://github.com/seldon39/arxivreader/issues/9)): check
interface changes by hand in WebKit (Safari or the Mac app), in light and dark mode, below 900 px and on wide
screens, and on several papers — a fix for one paper's LaTeXML output often breaks another's.

## Code

- **Layout**: see [README.md → Code layout](README.md#code-layout). Python server in `app/` (FastAPI, SQLite),
  interface in `static/` (plain JavaScript modules, no build step, MathJax and pdf.js vendored), Mac shell in
  `macos/main.swift`.
- **Maintenance guide**: [MAINTENANCE.md](MAINTENANCE.md): rules, work loop, release, what to
  do when an external service changes, WebKit checks, known pitfalls. It is also what AI coding assistants read
  (`CLAUDE.md`, `AGENTS.md`).
- **Comments** are mostly in French; new comments may be in English, and translating existing ones is welcome
  ([#8](https://github.com/seldon39/arxivreader/issues/8)). Comments explain *why* (a LaTeXML quirk, a WebKit
  pitfall), not what the line does.
- **Interface strings** go in `static/js/i18n.js`, in both French and English, with the same placeholders.
- **Shared helpers**: use them rather than writing the logic again — `escapeHtml`, `escapeRegExp`, `toastError`
  (`static/js/util.js`); `s2.headers(api_key)` and `s2.GRAPH` for Semantic Scholar; in `app/main.py`,
  `_failed(error)` (502 when an outside service fails) and `_s2_key()`.
- **Dependencies**: `requirements.txt` lists what is wanted, `requirements.lock` the exact tested versions. Add
  or update one deliberately, run the tests, then `./arxivreader lock`.
- **Settings**: a new value of a setting must also be accepted by the server (`SETTING_CHOICES` in
  `app/main.py`, or `BOOLEAN_SETTINGS` for an on/off setting), otherwise it is silently not saved.
- **Paper versions**: an older arXiv version of a paper is a full row of `papers`, with id `<paper>v<k>`
  (`library.archive_key`), `in_library = 0` and `archived_of` set to the main paper, which always holds the latest
  installed version. A query that lists papers (feed, search, profile, related papers…) filters
  `archived_of IS NULL` or uses `db.list_all_papers`; `db.archives_of(id)` lists the versions kept. A change of
  version goes through `library._replace_version` (one SQLite transaction; the folder renames, outside it, are
  undone at startup by `library.recover_interrupted`).
- **Writes from the reader**: a new route that writes for an open paper page (annotations, reading state, AI)
  takes `dependencies=READER_WRITE` in `app/main.py`, and its call in `static/js/api.js` sends the reading version
  (`versioned(id)`): a page left on a version replaced in another tab then gets 409 `stale_version`, which
  `toastError` shows with "Reload".
- **Library search** (`app/fulltext.py`): no index, the files are scanned at each search (extracted text cached by
  path and modification time). `fold()` maps one character to one character, so positions found in the folded text
  are valid in the original; snippets are shown as text, never as HTML.
- **Additions inside the paper's text** (like the `.bib-links` line of a bibliography entry) go at the end of their
  element and are listed in the `UI` selector of `static/js/annotations.js`, so that highlights, counted from the
  start of the text, do not move.
- **Routes with parameters** (`#/search?terms=…`): the parameters are fully percent-encoded, because the Mac and
  Windows shells only open routes that match the pattern in `macos/main.swift` and `windows/shell/MainForm.cs`.
- **Mac menu commands** go through `static/js/commands.js`: the menu item (`macos/main.swift`) carries a fixed command
  name, the shell sends it to the page (`app:command`), and `commands.js` or `Reader.menuCommand` maps it to an action.
  A command name never comes from the page, and the shell only accepts letters, digits and dots in it; the page
  only reports its state (`menuState`) so that items are greyed out and relabelled.
- **Data safety**: a change that makes the database unreadable by the previous version raises `safety.DB_VERSION`; every
  SQLite connection waits for a busy database (`timeout=30`, as `db.connect`); the daily copy (`app/safety.py`) and the
  integrity check never delete or overwrite a library: a damaged file is set aside, never erased.
- **Analysis changes**: when `app/structure.py` or `app/texstructure.py` changes what it extracts or marks, bump
  `STRUCTURE_VERSION` so imported papers are re-analysed.
- **Reader layout changes**: anything that changes the height of sections away from the screen (text size, width,
  panes, revealed hidden text) goes through `reader.relayout(fn)` or `keepReadingPosition`: those sections are not
  drawn (`static/js/lazypaint.js`) and keep the height measured before they were hidden.
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
The copyright line (seldon39) is the same in `LICENSE`, Settings → About and the Mac and
Windows packages: see MAINTENANCE.md.
