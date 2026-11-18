# arXivReader maintenance — instructions for an AI

This file is addressed to an assistant (Claude Code, Codex…) in charge of maintaining or evolving arXivReader:
what to know before touching the code, how to verify, how to release, and what to do when an external service
changes. The READMEs (`README.md`, `README.fr.md`) describe the application for its users; this
file, its upkeep. Keep it up to date when a rule or a pitfall changes.

## 1. In brief

Reader for arXiv papers (macOS, Windows; Linux from the source code), designed for mathematics: Python server (FastAPI + SQLite) on
127.0.0.1, JavaScript page with no compilation (`static/`), Mac application in Swift (`macos/main.swift`, one
WebKit window per tab) that embeds its own Python. The user's data is in
`~/Library/Application Support/arXivReader` (database `arxivreader.db`, folder `papers/`). Public repository
(github.com/seldon39/arxivreader, MIT license, © 2026 seldon39: same line in `LICENSE`,
Settings → About (`settings.js`), `NSHumanReadableCopyright` of `macos/build.sh` and `AppCopyright` of
`windows/installer.iss`; `LICENSE` is copied into the Mac app by `build.sh` and into the Windows zip and installer
by `windows.yml`). Code and comments in French; interface in French and in
English.

## 2. Rules never to break

- **Local only.** The server listens on 127.0.0.1; the `local_only` middleware (`app/main.py`) refuses foreign
  hosts, requests from other sites and writes without JSON. Do not weaken it. The only external
  calls are those of section 6.
- **The AI on a paper has neither tools nor network** and sees only the paper: do not give it any.
- **Keys and passwords** (Semantic Scholar, Gemini, Zotero, Claude/Codex accounts): the user is the one who
  creates them and pastes them into the settings. Never enter any, and never write any in the code, the logs or error
  messages.
- **Never touch the user's application or library during test runs**: no stopping of
  their application or their server, no writing to their library. Test runs on port 8711 with a copy
  (section 4).
- **Zotero: no item is ever deleted or moved to the trash**; only the "arXivReader" collections
  (and their sub-collections) are modified, an item's other collections are kept.
- **Install or release only with the user's agreement**, and only when the application has
  really quit (`pgrep -f /Applications/arXivReader.app` returns nothing: it can stay open with no
  window). Releasing is public (GitHub): always ask. A beta is never installed in `/Applications` and never goes
  through `macos/release.sh` (section 5, "Betas").
- **No download** (new library, MathJax update…) without explicit agreement.
- Commits: messages in English, ending with the assistant's `Co-Authored-By:` line; one commit per
  coherent change; commit only when the user wants it.

## 3. What is where

| Area | Server (`app/`) | Page (`static/js/`) |
|---|---|---|
| Import, cleaned HTML, PDF | `library.py`, `arxiv.py`, `sanitize.py` | `reader.js`, `pdfview.js`, `pdfpreview.js` (hover previews from the PDF), `pdfsync.js`, `lazypaint.js` (sections far from the screen not drawn) |
| Structure (statements, proofs, dependencies) | `structure.py` (LaTeXML HTML), `texstructure.py`, `texsource.py` (TeX source) | `reader.js`, `graph.js`, `texview.js` |
| Paper versions (each arXiv version with its own notes, Version menu) | `library.py` (`archive_key`, `versions_of`, `open_version`, `ensure_version`, `_replace_version`, `recover_interrupted`), `db.py` (`archived_of`, `archives_of`), `notesync.py` (`_version_of_notes`), `main.py` (`_same_version`) | `reader.js` (`versionChip`), `library.js` (`updatePaper`, shared with the feed), `api.js` (`setReadingVersion`) |
| Notes, highlights, reading state, export | `annotations.py`, `export.py` (`paper_export`: `.md`, or `.zip` with the images of the regions), `notesync.py` (signed export, import from one computer to another; the report's `changed` lists the papers whose notes changed) | `annotations.js` (`exportable`, `exportFromMenu`: the File menu's export; Notes tab: sections "In the HTML" / "In the PDF", `notesGroup`, `cardTools`, `removeMany`, `restore`), `markdown.js`, `bookmarks.js` (bookmarks, right-click menu), `pdfnotes.js` (passages, regions and notes in the PDF; passages found again after recompilation; `readingHeights`, `placeOf`, `addZone`, `addPin`) |
| Search of the library (full text and notes) | `fulltext.py` (no index), route `/api/library/search` | `library.js` (`searchText`, `drawTextHits`), `reader.js` (`showAnnotation`) |
| BibTeX (paper, selection, folder, library; bibliography entries of papers in the library) | `bibtex.py` (offline, from what is stored; the journal known to Semantic Scholar is passed in by the caller), routes `/api/papers/{id}/bibtex`, `/api/bibtex`, `/api/folders/{id}/bibtex`, `/api/library/bibtex` (and the `.bib` forms that download) | `bibtex.js` (`copyBibtex`, `saveBibtex`), `library.js`, `reader.js` |
| References into the library (+ Library, add all arXiv references, folder choice) | `crossrefs.py` (`match_by`: `by` = `id`, `doi` or `title`; `nodes_of`: cached index of other papers' statements), `library.quick_add` | `biblibrary.js` (`addButton`, `askDestination`; also used by the reader's bookmark) |
| Reading position, back / forward, reading keys | `db.py` / `main.py` (`papers.position`, 200 characters) | `position.js` (`encodePosition`, `decodePosition`, `positionSaver`: saved on close with `keepalive`), `trail.js` (`Trail`, `samePlace`: one history of jumps for HTML and PDF), `reader.js` (`j` / `k` / `p` / `?`, "Proof of…" banner; after `destroy()`, `onScroll` returns at once and `savePosition` does nothing: the debounced scroll timer armed before leaving must not overwrite the saved position) |
| Data safety (schema version, integrity check, daily copy) | `safety.py` (`prepare`, `snapshot`, `status`; `DB_VERSION`), `db.py` (`init`, `connect`), `main.py` (`_startup_maintenance`, `/api/library/backups`) | `settings.js` (`backupsLine`) |
| Feed cache pruning | `feedprune.py` (`prune`, `pack` / `unpack` of the vectors), `feed.py`, `main.py` (`_startup_maintenance`) | `feed.js` |
| Mac menus (bridge with the page) | — | `commands.js` (`command`, `reportMenu`, `copyFromMenu`, `showKeys`), `reader.js` (`menuCommand`), `macos/main.swift` (`pageCommand`, `validateMenuItem`) |
| Bibliography and paper links (DOI, MathSciNet, zbMATH, "Look up on arXiv") | `localpdf.py` (`reference_arxiv`, `reference_zbmath`, `zbmath_search`), routes `/api/references/arxiv`, `/api/references/zbmath` | `reader.js` (`findLinks`, `addFindLinks`, `bibActions`, `paperFindLinks`, `zbmathLink`, `lookUpArxiv`), `search.js` (`presetTerms`) |
| PDF and TeX files from the computer | `localpdf.py` (imported PDFs: text, title, arXiv version), `texfile.py` (TeX files: compilation, bibliography, checks, identifiers taken from the `\label` commands, background jobs) | `library.js` (import), `texfile.js` (import window, reader checks), `reader.js` (Recompile, books: statements and graph by chapter) |
| Annotated `.tex` copy (imported TeX files, Notes tab: notes inserted in a copy of the project, `.zip`) | `texexport.py` (`Plan`, `scan`, `build_zip`, `summary`, `references`, `short_tex`/`long_tex`, `_end_document`), `synctex.py` (`load`, source line ↔ PDF page and position), `texsource.py` (`expand_with_origins`: positions of the expanded text mapped back to files and lines), `texfile.py` (`_keep_synctex`), routes `/api/papers/{id}/tex-annotated` and `/tex-annotated/summary` | `texfile.js` (dialog « Copie .tex annotée… »: form of the notes, wide margin, end notes) |
| Experimental HTML version (LaTeXML) | `latexml.py` (on-demand conversion, the paper's `html/` folder, identifiers aligned with the structure of the source) | `latexml.js` ("HTML β" button, warning, waiting), `reader.js` |
| Library, folders, selection | `library.py`, `folders.py` | `library.js`, `selection.js` |
| Feed, profile, relevance, alerts, citations | `feed.py`, `profile.py`, `relevance.py` (`same_person`, `follows`: namesakes of followed authors), `alerts.py`, `citations.py`, `journals.py`, `library.new_versions` (new versions of library papers) | `feed.js` (👎 → "not this person"; `loadNewVersions`, `newVersionCard`), `profile.js` (categories kept for an author) |
| arXiv search | `search.py` | `search.js` |
| Related papers (graph) | `related.py` | `related.js` |
| Links from one paper to another (a bibliography reference that is a paper in the library) | `crossrefs.py` (`links`, `statement`, `match`) | `reader.js` (`openCross`) |
| Search in the page (⌘F, ⌘G) | — | `find.js` (Edit menu of the Mac app: `app:find` event) |
| AI | `ai.py` (Claude, Agent SDK; scans: pages attached as images; books that are too long: beginning and a window around the question), `providers.py` (Codex, Gemini, local), `bookjob.py` (book: all chapters in parallel, then synthesis) | `ai.js` (tab as a conversation: sub-tabs Questions / Summary / Review according to the root of each exchange, `viewOf`; a question on a summary or a review has that document as its parent; thread memory: `ai.thread_history` (same thread as `viewOf`: the whole Questions thread, or the document and the questions asked about it; finished or stopped, with an answer; the answered exchange even if in error, marked "[answer interrupted by an error]") and `_history_block` (`<conversation_history>`, at most `limit / HISTORY_SHARE` characters: the document and the answered exchange always, then the most recent ones; tags neutralised; not for summary, review, transcription); reading positions `positions`, ↑ ↓ buttons in the margin of the thread; cross-references in answers `buildRefs` (also used for the reader's notes: `Annotator.refs`, `followRefs` in `annotations.js`): statements, equations,
sections according to the number in the title (`toc`), references in brackets or author-year (`bibliography`), except the statement of a
cited paper, "Theorem 3.1 of [12]", "[12, Theorem 3.1]"; hover preview through `reader.previewFor`), `aiproviders.js`, `reader.js` (`contextPages`: pages of a scan rendered by pdf.js) |
| Reader's notes in questions (general note always, all notes when the question names them; `{"general": …}` proposals appended to the general note) | `ai.py` (`_reader_notes`, `asks_about_notes`, `NOTES_PROMPT`, `NOTES_TASKS`, `GENERAL_NOTE`; `general` item in the annotations format of `SYSTEM`) | `ai.js` (`parseProposals`, `proposalBox`, `applyProposals`: kind `general`), `annotations.js` (`setGeneral` for Undo) |
| Reviewing aids (review checked on demand, check by computation, what the assistant read) | `ai.py` (`verify_review`, `verifier`, `REVIEW_GUIDANCE`, `REVIEW_POINTS`; `coverage` and `cut` columns of `ai_exchanges`), `main.py` (`AIRequest.check`) | `ai.js` (button "Check the serious points", cost line, "Copy the code", banners "Read: n %" and "cut off") |
| AI on annotations (notes carried between views, versions or compilations; deletions; questions on another version) | `ai.py` (`asks_adaptation`, `_adapting`, `_annotations_block`, `asks_deletion`, `_deleting`, `version_asked`, `_versions_compared`), `texfile.py` (`track_lost_notes`, `notes_to_check`) | `ai.js` (`parseProposals`, `applyProposals`, `surfaceOf`, `prefill`), `annotations.js` (`addPin`, `setFreeNote`, `restore`), `pdfnotes.js`, `reader.js` (`pdfQuote`, `ensurePdf`) |
| Zotero | `zotero.py` | `zotero.js`, `settings.js` |
| First launch | `onboarding.py` | `onboarding.js` |
| Updates, service checks | `updates.py`, `check.py` | `updates.js` (in Settings → About), `check.js` (page `#/check`) |
| Configuration, keys, versions | `config.py` (paths, `TOKEN`, `TOKEN_FILE`, fixed settings), `keystore.py` (API keys in the system keychain, `ARXIVREADER_KEYCHAIN=1`), `s2.py` (`headers`, `paced`, `GRAPH`: Semantic Scholar requests), `version.py` (`VERSION`, section 5) | `settings.js` (About, keys) |
| Page skeleton, global styles | — | `app.js` (start, routes, global events, right-click menu suppression, middle click), `app.css` (all styles of the app; `latexml.css`: the paper's HTML) |
| TikZ drawings and labels | — | `figures.js` (`shapesOf`, correction of the drawings made by LaTeXML) |
| Formulas, DOM construction | `feed.tex_text` (TeX of titles and abstracts) | `math.js` (MathJax; `undefinedFonts`, `linksInText`), `amsfix.js`, `util.js` (`h`, `withMath`, `tidyTitle`, `texToText`: TeX of titles shown as plain text) |
| Settings | `main.py` (`allowed` list and `_valid_setting` of `/api/settings`) | `settings.js` ("About" section: version, updates, copyright, license) |
| Interface texts | — | `i18n.js` (FR and EN blocks: same keys) |
| Mac application | — | `macos/main.swift` (menus, session restored at launch, tabs, dragging onto a tab, downloads, test runs, native folder and file pickers: `chooseFolder`, `chooseFiles`; `isBeta`), `macos/build.sh`, `macos/release.sh`, `macos/build-beta.sh` (betas, section 5) |
| Windows application | `windows/launcher.py` (windowless server, Edge fallback) | `windows/shell/` (C# window: `MainForm.cs`, `TabStrip.cs`, `Program.cs`), `windows/smoke.ps1`, `.github/workflows/windows.yml` |

Database: `app/db.py` (`SCHEMA` as `CREATE TABLE IF NOT EXISTS`, columns added by `ALTER TABLE` after
`PRAGMA table_info` in `init()`, one column at a time: `_add_columns`, so a start interrupted between two columns of a pair finishes it next time); modules that have their own tables: `related.py`, `zotero.py`, `journals.py`
(`_ensure`). The `papers` table also holds the archived versions of a paper (`archived_of` not null, section 9):
any new query that lists papers filters `archived_of IS NULL`, as `db.list_all_papers`, `feed.list_items`,
`profile.author_categories`, `related.annotate` and `search.annotate_status` do.

Page: `h(tag, attrs, …)` adds the children as text; `html:` is reserved for internal icons (`icons`). All
text coming from arXiv, Semantic Scholar, Zotero or an AI goes through text or `withMath`, never through
`html:` or `innerHTML` (the CSP policy forbids the paper's scripts anyway).

## 4. Work loop

- **Tests** (no network: every external service is simulated in them; a test that calls the real network is a defect):
  `.venv/bin/python -m unittest discover -s tests`. To make sure that no test goes out, rerun them with
  `HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9` (known exception: the Gemini tests use a
  fake local server that this proxy intercepts).
- **Test server**, never the user's, on a copy **without their keys** (otherwise an action on the
  copy, or "Synchronise now", would write to their real Zotero or use up their quotas):
  ```bash
  LIB=$(mktemp -d)   # private folder (0700): the copy contains their data
  sqlite3 -readonly ~/Library/Application\ Support/arXivReader/arxivreader.db ".backup $LIB/arxivreader.db"
  sqlite3 "$LIB/arxivreader.db" "DELETE FROM settings WHERE key LIKE '%ApiKey%' OR key LIKE 'zotero%'"
  ARXIVREADER_LIBRARY=$LIB ARXIVREADER_PORT=8711 ARXIVREADER_NO_ALERTS=1 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8711
  ```
  `ARXIVREADER_NO_ALERTS=1` is **mandatory for every test run** (server as well as application): it cuts the
  background loops (followed authors and citations, `alerts.loop`; Zotero synchronisation, `zotero.loop`); what the
  page triggers itself (journals on Semantic Scholar, update check) still runs.
  Add `HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9` if the test run does not need the network. The
  copy does not have `papers/`: an opened paper is downloaded again from arXiv; to read a paper without network, also copy
  its folder (`papers/<id>`). Empty library (fresh installation, first-launch assistant): an empty
  `mktemp -d`. Erase the copy afterwards.
- **Verify in the browser** (the page reloads its files: force `location.reload()`), in light and in
  dark mode, at several widths (one column below 900 px). The user reads in WebKit (Safari): that is the engine
  that counts. **Every display change (formulas, CSS, glyphs) is also verified in a built copy of the application (WebKit:
  `static/` and `app/` replaced, `__pycache__` deleted, `ARXIVREADER_EVAL` + `ARXIVREADER_WINDOWSHOT`, see below), never only
  in a Chrome-engine browser**: the two engines place combining glyphs and fonts differently.
- **Test runs in the Mac application** (built by `macos/build.sh` in `~/Library/Caches/arXivReader-build`,
  without installing it): always `ARXIVREADER_NO_ALERTS=1` and `ARXIVREADER_LIBRARY` (copy without keys, as
  above), `ARXIVREADER_START=#/paper/…,#/feed` (pages opened, the
  next one in a tab), `ARXIVREADER_SNAPSHOT=fichier.png` (image of the page), `ARXIVREADER_WINDOWSHOT=fichier.png`
  (whole window and report in `fichier.png.txt`), `ARXIVREADER_EVAL` (JavaScript run on load),
  `ARXIVREADER_SNAPSHOT_DELAY`. Do not take over the user's screen.
  `ARXIVREADER_MENU=view.tab.3,go.feed` chooses menu items one by one (one per second, from 3 s on), each given by its
  command name or its exact title; the report of `ARXIVREADER_WINDOWSHOT` (`fichier.png.txt`) has an `items=` dump
  of every menu item (menu > title [shortcut], with `off` when greyed out and `hidden`), to check labels and states.
  A run with one of these variables set opens a single start page and leaves the user's saved session alone.
  Other variables: `ARXIVREADER_BUILD=<folder>` (`macos/build.sh`: where the application is built, default `~/Library/Caches/arXivReader-build`; `macos/build-beta.sh` sets its own); `ARXIVREADER_WATCH_PARENT=1` (set by `macos/main.swift`: the server stops by itself when the application disappears without stopping it, `main._watch_parent`; do not set it for a server started by hand); `ARXIVREADER_DEVTOOLS=1` (Windows: enables the WebView2 developer tools in `MainForm.cs`); `ARXIVREADER_TOAST` is internal (JSON passed by `alerts.py` to the PowerShell notification script), never set it by hand.
  `ARXIVREADER_NO_SNAPSHOT=1` skips the daily copy of the database (tests). `ARXIVREADER_FULLSCREEN=1` puts the window
  in full screen at 2 s (this does take over the screen for the run: say so first); the report then gives
  `fullScreen=… frame=… web=…` and a `views=` tree of the title bar, toolbar and tab bar (the cached capture does not
  draw the glass of the tab bar, so read its geometry there).
- **Before handing back**: tests green; every feature or fix carried over into all the documentation files: `MAINTENANCE.md`
  ("What is where" table, external services, known pitfalls), `CONTRIBUTING.md` (principles, code rules), `ROADMAP.md`
  (steps done, next steps), `README.md` and `README.fr.md` together (including "Code layout"); `i18n.js` keys present in both languages (plurals: `.one` and `.other`).

## 5. Releasing a version

1. Bump `VERSION` in `app/version.py` (new feature: minor; fix: patch), commit.
2. The user's agreement; app quit (`pgrep -f /Applications/arXivReader.app` empty).
3. Release notes in English in a file **outside the repository** (one line per visible change), for example
   `$(mktemp -d)/notes.md`: inside the repository, the file would make it "modified" and the script would refuse.
4. `macos/release.sh /chemin/absolu/notes.md`: it requires the `main` branch, a repository with no uncommitted
   changes, `gh` connected to GitHub, a `VERSION` without a beta suffix, a `HEAD` that contains `origin/main`
   (fetched first) and a `vX.Y.Z` tag that is free locally **and on GitHub**; then tests, build (refused
   if `.venv` differs from `requirements.lock`), tag, atomic push of the branch and the tag, release of the `.dmg` on GitHub.
5. Install: `rm -rf /Applications/arXivReader.app && ditto ~/Library/Caches/arXivReader-build/arXivReader.app /Applications/arXivReader.app`,
   then check `defaults read /Applications/arXivReader.app/Contents/Info.plist CFBundleShortVersionString` and
   `codesign --verify --deep --strict /Applications/arXivReader.app`.

The app has only an ad hoc signature (no notarization): on another Mac, open it with right-click >
Open.

### Windows version

Built by GitHub Actions (`.github/workflows/windows.yml`, machine `windows-latest`) **on every push to
`main`**: installation of `requirements.lock` and of `windows/build-requirements.txt` (PyInstaller, `tzdata` for
time zones, pinned versions), **all the tests** (the Mac-specific ones are skipped), server
`server\arXivReader-server.exe` (PyInstaller `--onedir`), window `arXivReader.exe` (`dotnet build`, WebView2 SDK at a
pinned version), zip, then `windows/smoke.ps1`: server on an empty library, type of the JavaScript modules, import
of 1603.04246, screenshots by Edge with no window, then the app window on two tabs (title checked),
server killed (the window must stay and offer to restart it), and no server left once the window is closed.
Screenshots in `shots/` (run artifact, kept 14 days, with the zip).

Release: one single GitHub release per number. `macos/release.sh` creates the `vX.Y.Z` tag and the release with the
`.dmg`; the tag triggers the Windows workflow, which checks that X.Y.Z is the one in `app/version.py`, builds and
test-runs the app (and the Inno Setup installer, `windows/installer.iss`: silent installation, shortcut,
uninstallation), then **attaches `arXivReader-setup.exe` and `arXivReader-windows.zip` to the `vX.Y.Z` release** (a separate step, the only one able to
write to the repository; when re-run, it replaces the zip). Both apps read "latest": the Mac takes the
`.dmg` from it, Windows takes the installer if it was installed that way (`unins000.exe` next to it), otherwise the `.zip` (not yet attached: nothing is announced, the next check will see it). The old
pre-releases `windows-v1.9.15`… `windows-v1.9.21` and `windows-latest` remain as history: Windows versions
1.9.21 and earlier do not see the unified releases, they must be updated once by hand.

- Window: `windows/shell/` (C#, .NET Framework 4.8 included in Windows, WebView2), the equivalent of
  `macos/main.swift`: one WebView2 page per tab (each with its own history), tab bar drawn in the macOS style
  (`TabStrip.cs`), external links in the default browser, `window.chrome.webview.postMessage` to
  open a tab (`nativeBridge` in `static/js/util.js`), size kept in `window.txt`. It launches
  `server\arXivReader-server.exe` (PyInstaller, `windows/launcher.py` with `ARXIVREADER_NO_BROWSER=1`) in a
  Windows "job" that stops it together with the window, and reads its port from `server.port`. Launched alone, `launcher.py` still opens
  an Edge window (fallback).
- Test run: `windows/smoke.ps1` also launches the window on two tabs and captures the screen (`shots/window.png`).
- Server-side differences, all under `sys.platform == "win32"`: notification through PowerShell (`alerts.py`;
  shown under PowerShell's name, a click does not open the app: an `arxivreader://` protocol and a `launch` attribute
  would be needed),
  sign-in of the assistants in `cmd.exe` (`providers.py`), the SDK's `claude.exe` (`ai.py`), the `.zip` of the latest release
  (`updates.py`). MIME types fixed in `main.py` (the Windows registry sometimes serves `.js` as `text/plain`).
- One instance per library: `launcher.single_instance` (named mutex); `ERROR_ALREADY_EXISTS` is read with `ctypes.get_last_error()` on a `WinDLL(use_last_error=True)`, never with a direct `GetLastError` call (the code may be overwritten in between).
- Interface: `MAC` / `WINDOWS` in `static/js/i18n.js`; outside the Mac, ⌘ ⌥ ⇧ become Ctrl Alt Shift on display.
- Not yet on Windows: "Copy a Library…" (menu of the Mac app), signing of the `.exe`, and the warning before quitting while work is running (the Mac app asks `GET /api/ai/running` in `askIfRunning`; the Windows window closes and stops the server without asking, so an AI answer or a book review in progress is lost).

### Betas

For future betas: work on a local `beta` branch, with `VERSION` = `X.Y.Z-beta.N` (never compared with the published
versions, so no update is offered). `macos/build-beta.sh` (refuses `main` and a `VERSION` that is not `X.Y.Z-beta.N`) builds a separate `~/Applications/arXivReader Beta.app`
(**never `/Applications`**), with its own settings, library and port. Never `macos/release.sh` for a beta; a GitHub
pre-release, if any, uses a tag that does not start with `v` (a `v*` tag starts the Windows workflow). Documentation is
updated when the beta is merged into `main`, and `VERSION` goes back to `X.Y.Z` before the release.

## 6. External services: what can change, where to look

Service unreachable (no network, address not found: waking from sleep, Wi-Fi reconnecting): `main._failed`
returns the code `offline` for an `httpx.ConnectError` or `httpx.TimeoutException` (no answer), translated by the page (`error.offline`, `feed.error.offline`);
the feed retries on its own (`retryWhenOnline`: "online" event, otherwise every 30 s as long as the page is open), except after a timeout of
its own refresh (504 `arxiv-timeout`: `patient_get` has already waited), which is not retried.

`./arxivreader check` makes real calls to those listed in `CHECKS` (`app/check.py`: to be extended if the app
starts depending on a new service) and checks the shape of the response (✓ good, ✗ changed or down, – not
checked). Run by hand, it reads the settings of the user's library (keys) and writes `check.json` there
(without touching the macOS/WebKit marker of the weekly job); `ARXIVREADER_LIBRARY=<copie>` to avoid this. The
weekly job (`./arxivreader check --weekly`, launchd
`org.arxivreader.check`, Monday 10:00) does this with the Python of the installed app and notifies in case of a
problem; log `~/Library/Logs/arXivReader-check.log`, result `check.json` in the library. No network (every network
check failing with `NETWORK_ERRORS`, none reached; `LOCAL` checks do not count): `offline`, retried `RETRIES` times
every `RETRY_WAIT` s by the weekly job, then status `offline` and no "service" notification. The report as text
(`check.text`, also printed) is served by `/api/check` and shown by the page `#/check` (`check.js`, "Copy the
report"), opened by a click on the notification (`route="#/check"`), Help > "Last Service Check" (`help.check`) or
the link in Settings > About (`updatesSection`); the system notification says what moved (`system_change`).
AI assistants: one check each (`CLAUDE`, `CODEX`, `GEMINI`, `LOCAL_MODEL`), chosen or not (the local model only
when chosen); a check may return `(detail, assistant)` or raise `Skip(message, assistant)` with
`{"id", "name", "version", "followed"}` (`cli_version`: `--version` of the CLI; Gemini: the list of models). Versions
are compared with `assistantsBaseline` (moved like the system marker; an assistant not seen keeps its entry);
`assistant_change` lists the changes in `assistantChanges`, notified ("Un assistant IA a changé", with
`ASSISTANT_POINTS`) unless the CLI is the one bundled with the SDK (`followed: false`, it changes with arXivReader) or
Gemini models were only added.

| Service | Used by | What usually breaks | Existing fallback |
|---|---|---|---|
| arXiv API `export.arxiv.org/api/query` | `arxiv.py`, `search.py`, `feed.py` (past days), `profile.py`, `onboarding.py`, `library.py` (latest version of a paper: `latest_version`; new versions of the library: `new_versions` → `arxiv.fetch_latest`, one request per 100 ids) | slowness, 429/503, Atom fields | one request every 3 s (`arxiv._paced`, `search._wait_turn`), `with_retry`; Version menu: answer after `LATEST_WAIT` (20 s) at most, then only the versions on the computer; feed: last answer kept in the setting `feed.newVersions` |
| LaTeXML HTML pages `arxiv.org/html/…` | `library.py`, `sanitize.py`, `structure.py` | `ltx_*` classes, markup of statements and proofs, image paths (two forms: `x1.png` or `<id>v<n>/fig.png`, `sanitize._relative`) | ar5iv, otherwise PDF + structure extracted from the TeX (`texstructure.py`); an archived version only from `arxiv.org/html/<id>v<k>` (`fetch_html(ar5iv=False)`: ar5iv serves its own version), otherwise TeX source and PDF |
| RSS `rss.arxiv.org` | `feed.py` (`parse_rss`) | format of the announcements (new/cross/replace types) | none on an HTTP error or a changed format (the feed is not refreshed and the page retries); an empty feed (weekend, holiday) reads the last announced day through `_latest_published` -> `fetch_day` (OAI-PMH for the last 7 days, otherwise the search API) |
| PDF and TeX source `arxiv.org/pdf/…`, `arxiv.org/e-print/…` | `arxiv.py` (called by `library.py`) | slowness, 429/503, format of the source archive | `_download` retries 3 times (honouring `Retry-After`) at import and in the background, but a single try for a page request (`ensure_pdf` → `fetch_pdf(patient=False)`: `/pdf`, `/pages` answer 502 at once instead of keeping the reader waiting); still no answer: `ArxivUnavailable`, never recorded as "no source" or "no PDF" (without HTML the import fails and is redone later; with HTML only the source's options are missing; a PDF missed at import leaves `pagesPending` in `structure.json`, statement pages found when it arrives; `ensure_pdf` repeats the failure at once for `PDF_RETRY_AFTER`, 60 s); without source: structure taken from the HTML; without HTML or source (source `none`, paper deposited as a PDF only): read in its PDF, `text.json` extracted like an imported PDF (at import, or by `ensure_pdf` for older papers; `{"pages": []}` for an unreadable PDF, so it is not re-read and the assistant gets page images); no PDF from arXiv: the reader (`pdfAvailable`) falls back to title and abstract |
| OAI-PMH `oaipmh.arxiv.org/oai` | `feed.py` (past days) | slow beyond one week | search API (submission window 14:00 New York) |
| Author feed `arxiv.org/a/<id>.atom2` | `onboarding.py` | format, ORCID not linked | search by name |
| Semantic Scholar (graph, batch, recommendations) | `related.py`, `relevance.py`, `journals.py`, `profile.py` (`_get_s2`), `citations.py`, `feed.py`, `onboarding.py`, `localpdf.py` (arXiv version of an imported PDF; "Look up on arXiv" of a bibliography entry, `reference_arxiv`: `/paper/DOI:…`, then `/paper/search/match` on the guessed title) | shared quota without a key (429), fields | wait and new attempt; features degraded without it; "Look up on arXiv" falls back to the guessed title and first author |
| zbMATH citation matcher `zbmath.org/citationmatching/match` (public JSON, no key) | `localpdf.py` (`reference_zbmath`, `_zbmath_match`; route `POST /api/references/zbmath`, asked when the pointer or the focus reaches a bibliography "zbMATH" link) | fields `results`, `zbl_id`, `probability`; accepted only for a Zbl number `dddd.ddddd` and `probability ≥ ZBMATH_SURE` (0.5) | the link stays a filled zbMATH search (`zbmath_search`), also offline; not in `CHECKS` |
| Zotero web API v3 | `zotero.py` | headers (`Total-Results`, `Last-Modified-Version`, `Backoff`), multiple writes (`successful`/`failed`), `since`/`/deleted` | safeguards: rejected write retried, mass removals held pending |
| Claude (Agent SDK and its CLI) | `ai.py` | SDK options, list of models (`get_server_info`), sign-in | sign-in message in the settings |
| The user's TeX (latexmk, pdflatex/xelatex, BibTeX/Biber, kpsewhich) | `texfile.py` (imported TeX files) | old latexmk (BibTeX cannot write into `-outdir`: `$bibtex_fudge=1`), MiKTeX without Perl (latexmk does not start), programs outside the PATH of an app launched from the Finder (`TEX_DIRS`) | manual compilation (LaTeX, BibTeX or Biber, LaTeX ×2); without TeX, PDF compiled next to the file |
| The user's LaTeXML (`latexmlc`) | `latexml.py` (HTML β) | options of `latexmlc`, slowness (pgfplots, large documents), installation on Windows (Perl) | pgfplots figures replaced by a note; time limit depending on the size of the source (`latexml.timeout`: 10 min to 1 h, about 300 s per MB of TeX measured on a recent Mac, ×4 margin), then failure remembered ("Retry"); the PDF remains readable |
| Codex CLI, Gemini API, local model | `providers.py` | CLI options (`-m`, `model_reasoning_effort`, `--image=` after the question `-`), `~/.codex/models_cache.json`, Gemini's `thinkingLevel` | — (the Gemini CLI no longer accepts free Google accounts: API with a key) |
| GitHub releases | `updates.py` | — | silent |

The DOI, MathSciNet and zbMATH links of the reader (paper header, bibliography) are ordinary addresses opened in the
browser on click: the application itself sends nothing to doi.org or MathSciNet. If MathSciNet (`mref?ref=…&dataType=mathscinet`)
or zbMATH (`?q=…`) change their search addresses, fix `findLinks` / `paperFindLinks` / `zbmathLink` (`reader.js`) and
`zbmath_search` (`localpdf.py`).

**Procedure when a service has changed**: reproduce with a minimal real request (like `check.py`); fix
the module concerned; update the tests' simulation so that it resembles the new response (the fake
services must be *as strict* as the real ones: an overly permissive fake has already hidden a defect); add a
test for the case; check that the fallback works if the service goes down.

**Caches to invalidate** when their computation changes: `STRUCTURE_VERSION` (`structure.py`, re-analysis of the imported
papers), `PAGES_VERSION` (`library.py`, pages of the statements in the PDF), `TEXT_VERSION` (`localpdf.py`, text.json of
PDF papers, redone at startup by `redetect_all`), `META_VERSION` (`localpdf.py`, title and authors guessed from an
imported PDF), `FORMAT` (`related.py`, graphs kept for fifteen days), Zotero index (`zoteroIndexVersion`, reset
by `_forget_all`), new versions of the library (setting `feed.newVersions`: shape of its items). The text extracted by the
full-text search (`fulltext._extracted`, `_read_labels`) is cached in memory only, keyed on the file's path and `mtime_ns`:
a restart is enough.

## 7. WebKit and macOS

WebKit changes with macOS (especially the major version, in September). `check.py` reports the change; re-check by
hand, in the installed app:
- (`WEBKIT_POINTS` in `app/check.py`, the text of the notification, summarises this list: add any new WebKit-specific point to it)
- notifications: `alerts.display` writes `<library>/notifications/<time>.json` (title, subtitle, body, route);
  the Mac shell (`postNotifications` in `main.swift`, every 5 s and at launch) posts them through
  `UNUserNotificationCenter` and deletes the files. A click opens `route` in a new tab: `#/arxiv/<id>` (`app.js`
  imports the paper as a click in the feed does, marks it seen, then replaces the route by `#/paper/<id>`) or
  `#/feed`, or `#/check` (weekly check: its report). The folder is created by the app, so the
  weekly check writes there too (shown at the next launch if the app is closed); without it (development server)
  `osascript` is used, which macOS attributes to Script Editor (a click opens it). macOS asks for permission at
  the first notification (bundle id `local.arxivreader`, ad-hoc signed); refused, the files are dropped. To recheck:
  a notification appears and a click opens the paper;
- drag a paper from one window to the other, onto a tab (opens after 0.6 s), onto "Library" and
  "Related papers"; drag types: `application/x-arxivreader-paper` and `text/uri-list`
  (`util.js`, `selection.js`, `TabWindow` in `main.swift`, which locates tabs through the `NSTabButton` views of
  the Mac's tab bar);
- SVG colors (the related-papers graph puts its colors in `style`, because WebKit rejects `var()` in the
  `fill` attribute), `color-mix`, `<dialog>` and `inert`, `ResizeObserver`;
- the PDF reader (pdf.js frozen in `static/vendor/pdfjs`) and the formulas (MathJax frozen in `static/vendor/mathjax`);
  the pdf.js viewer forbids styles written by script (CSP `style-src 'self'`): our additions go through
  `static/css/pdfjs-extra.css` (hidden buttons, highlights); the PDF highlights (`pdfnotes.js`) locate themselves
  in the text layer (`.textLayer`, `textlayerrendered` event): to be rechecked at every pdf.js update;
  link previews (`pdfpreview.js`): the bubble lives in the app page above the frame; targets from `getAnnotations`
  (`dest`, `overlaidText`), `getDestination` and `getPageIndex`, the extract drawn by `page.render` into a canvas of the
  frame then copied; the crop starts on the line holding the link's number, because hyperref puts the target above
  the line by a varying amount (about 8 pt for a statement, more than one line in a bibliography): to be rechecked at
  every pdf.js update;
  ⌘-click / middle-click on an internal reference opens the same paper at that place in a new tab
  (`Reader.openTabAt`, capture listener in `bindEvents`; PDF links: capture listener in `pdfpreview.js`, route
  `#/paper/<id>/pdf:<page>:<top>` read as `startPdf`, which forces the PDF view);
  the selection is drawn by `pdfnotes.js` (`enableSelectionRendering: false` in `pdfview.js`); the regions
  (⌥ + drag, button `#arxZoneButton` added to `#toolbarViewerMiddle`) are rendered as images by `page.render`;
  images kept in `papers/<id>/zones/` (notes) and `papers/<id>/ai-images/` (questions), sent only to the
  assistants of `ai.IMAGE_PROVIDERS` (Claude: the SDK's message with image + text blocks; Gemini: `inline_data`);
- exported notes: signed data block at the end of the file (`notesync.py`, Ed25519 from `cryptography`), the
  installation's key in `notes-signing.key` (library folder, never exported), accepted devices in
  `notes-trusted.json`; any change to the block's content goes through `FORMAT` (old files remain readable);
- the browser's right-click menu is suppressed (`app.js`) except on a selection, a field, a link or an image;
  right-click on the paper (`annotations.js`) and on the PDF (`pdfnotes.js`) offers the bookmarks (`bookmarks.js`);
- downloads (notes export: Downloads folder, `WKDownloadDelegate`; once saved, the shell sends the page an
  `arxivreader:downloaded` event, the page says so with "Show in Finder", and the Finder is not brought forward);
- Mac menus, a bridge in both directions. Page to shell: `static/js/commands.js` posts two messages through
  `window.webkit.messageHandlers.arxivreader`: `menuState` (page, reader or not, PDF or HTML, contents and panel shown,
  proofs folded, arXiv address, paper id…: stored in `Tab.menuState`, read by `validateMenuItem` to grey items out and
  change labels such as "Show HTML" / "Show PDF") and `copyText` (the clipboard of WebKit refuses a write that does not follow a click on
  the page, so the shell copies; in a browser `copyFromMenu` uses `navigator.clipboard`). Shell to page: `pageCommand`
  evaluates `document.dispatchEvent(new CustomEvent('app:command', { detail: name }))` in the key tab; the page's
  `app:command` listener (`commands.js`) maps the name to an action (`go.*` pages, `import.arxiv`, `import.file`,
  `edit.searchLibrary`, `export.bib`, `notes.import`, `help.keys`) or hands it to the open reader (`Reader.menuCommand`: `reader.*`, `view.*`,
  `go.back` / `go.forward` / `go.next` / `go.prev` / `go.proof`). A command name is fixed in Swift (the menu item's
  `representedObject`), never taken from the page, and `pageCommand` refuses any name that is not made of letters, digits
  and dots, since it is put in a script. The same holds for the other events (`app:find`, `app:zoom`, fixed
  words). The paper's PDF shown in the Finder and the arXiv page opened come from `menuState`, checked in Swift (an id
  of ASCII letters, digits and `._-`, an address starting with `https://arxiv.org/abs/`). To add a menu item: item and
  `representedObject` in `main.swift`, its name in `commands.js` (or `Reader.menuCommand`), its state in `validateMenuItem`
  if it can be greyed out, its label in both languages (`tr`), the shortcuts list `showKeys` and `i18n.js` if it has a key;
- scrolling through a long, formula-heavy paper (`lazypaint.js`). WebKit keeps the image of everything that has been
  scrolled and does not release it when it contains formulas: 2.3 GB for 2611.03736 read in full, peak of 4 GB. The
  sections more than one screen away from the window (`AHEAD`) therefore switch to `content-visibility: hidden`, at the height
  measured just before (`contain-intrinsic-height`): 0.8 GB. To recheck: memory of the
  `com.apple.WebKit.WebContent` process (`footprint <pid>`, "graphics" line), positions unchanged (table of contents, search,
  switching to the PDF, text size), nothing displayed late while scrolling. WebKit gives the true positions within a
  hidden section (elements and `Range`); `content-visibility: auto` is not suitable: no head start (a section is
  drawn once it is on screen) and estimated heights for sections never seen (table of contents shifted by 2 000 px).
Snapshots (`ARXIVREADER_SNAPSHOT`) before and after the update help with comparing.

## 8. Python dependencies

- `requirements.txt`: wanted packages; `requirements.lock`: exact, tested versions, which `.venv` installs and
  which the application embeds (`build.sh` copies `.venv`, and refuses to build if it differs from the lock).
- To update, deliberately and one package at a time: `.venv/bin/pip install -U <paquet>`, tests, test run, then
  `./arxivreader lock` and commit `requirements.lock`. Read the package's release notes beforehand.
- pypdf major version: compare before and after, on the library's PDFs (read-only), the text of
  each page (`localpdf.read_pdf`), the arXiv stamp, `latexml._pdf_size` and the placement of the pages
  (`texstructure.locate_pages`). pypdf 7.0 (1.11.27): only change, a space around subscripts and superscripts
  ("H 2 p" instead of "H 2p"); stamps, sizes and pages identical.
- Mac: the environment is in `.venv.nosync` (iCloud ignores names ending in `.nosync`), `.venv` is only a link;
  `./arxivreader` creates it this way (Linux: ordinary `.venv`). Reason: Documents is synchronised by iCloud, which
  recreated, in a rebuilt or updated `.venv`, the old versions of files as copies "name 2.py", "claude 3"
  (ghost files, `dataless`: moving them one by one downloads them), then embedded by the application (2 776
  in one .venv, 1 744 in app 1.11.26). `build.sh` refuses to build if it finds any (`find .venv/ -name
  '* [0-9]*'`). To rebuild the environment: `rm -rf .venv .venv.nosync && ./arxivreader`.
- Tools of the Windows version (`windows/build-requirements.txt`, `Microsoft.Web.WebView2` package of
  `windows/shell/arXivReader.csproj`) and GitHub Actions (`.github/workflows`): to be checked
  as well at every audit (`gh api repos/actions/<action>/releases/latest`), release notes of major versions read beforehand:
  download-artifact 8 fails if the file's digest does not match, upload-artifact 7 adds `archive` (true by
  default); download by name (`name:`) is unchanged.
- Changing the Python version: `.venv` rebuilt with the new version; `build.sh` creates the embedded Python
  from the conda-forge cache (`miniforge`, `conda create --offline`) at the same version: it must first be
  downloaded into this cache (`conda create` without `--offline`), with the user's agreement.

## 9. Known pitfalls

- Link to another paper from the paper being read ("Open the paper", "Open Theorem 4.3": preview of a cross-reference,
  bibliography pill): a plain `href="#/paper/…"` would replace the paper being read in its tab. These links
  carry `.cross-open` and go through `reader.openCross`, from `interceptLinks` and not through a listener set on the
  link, because `clean` copies the pill into the previews without its listeners (new tab in the foreground, ⌘-click in the
  background, like `readArxiv` for arXiv identifiers); middle click: common handler in app.js.

- Rendered notes and long formulas: MathJax 3 cannot break an inline formula, so a CSS grid column that holds one must be
  `minmax(0, 1fr)` and the note must scroll in its own box (`overflow: auto hidden`), otherwise the formula widens the
  column and everything in it. A `.note-popover` is placed before it reaches its final height (text area grown, preview
  rendered): the `ResizeObserver` of `openPopover` moves it back inside the window, since scrolling the page closes it.

- Hidden sections of the reader (`lazypaint.js`): a section far from the screen keeps the height measured before it was
  hidden. Any change that modifies the height of off-screen sections (text size, width, panes,
  hidden text revealed) goes through `reader.relayout(fn)` (or `keepReadingPosition`, which calls it): everything is redisplayed
  for the duration of the computation, then the heights are taken again. The sections (`.lazy-paint`) extend over the
  paper's margin (`--paper-pad`): a hidden section draws nothing outside its box
  (markers, proof arrows, notes, bookmarks are in this margin); the margins that stuck out of the section
  (title at the top, last paragraph or proof at the bottom) are carried by the section (`edgeChain`): a hidden section
  lets nothing out, and what follows would move up. No `flow-root`: it would enclose the floating □ of the
  proofs (one more line). A margin element must
  not exceed `--paper-pad` (26 to 32 px). `keepReadingPosition(..., { applied: true })` when the change is already
  made (settings, resized window): the previous place then comes from the retained positions (`this.anchors`).
- LaTeXML extensions (`.ltxml`, `.latexml`) are Perl that LaTeXML runs without restriction when it finds them next to the
  document: never extracted from a source downloaded from arXiv (`latexml._extract`, `BINDINGS`). Those of a TeX file the
  user imported are still honoured, like the folder's `latexmkrc` (README: import only documents you trust). The app's own
  extensions are written elsewhere (`_stubs`).
- HTML version by LaTeXML (`latexml.py`): stored in `papers/<id>/html/`, never in `content.html` at the root
  (its presence makes a paper an arXiv paper read as HTML: analysis, text for the assistant). The identifiers of the
  statements (matched in document order, type by type), of the sections, of the bibliography and of the numbered equations
  of the output are replaced by those of the source's structure (`align_ids`): notes and PDF pages
  (`structure.json`, source "tex") serve both views; proof location indicated by hand in HTML view:
  `latexml.reanalyze`. Main file of an arXiv source: `texsource.choose_main` (00README.json, otherwise the
  longest), the same for the analysis and for LaTeXML. Conversion and compilation of the same document: one at a time
  (409). Defects of LaTeXML 0.8.8 (Homebrew) worked around, which arXiv's development version fixes: drawings without
  `viewBox` (`add_viewboxes`), KOMA packages with no equivalent that empty the bibliography (`LAYOUT_ONLY`, empty packages
  written in the conversion folder), search paths with accents ("Thèse": .bib and images
  not found) replaced by symbolic links with a simple name (`_ascii`), the author's macros expanded in the
  copy of the `.bib` (LaTeXML reads it without the preamble: `_expand_macros`), labels of TikZ drawings with no height or
  depth and badly placed matrices (diagrams) (`FIGURE_LABELS`, loaded by `--preload`: same measurements as
  arXiv's output, matrices marked `ltx_tikzmatrix_base` that `figures.js` does not correct), hatchings whose pattern
  is lost (`fix_patterns`), text-only line of a node turned into an SVG `<text>` (wrapped, same module),
  cross-references of fixed-width drawings (`_figure_refs`: number from the `.aux`, link kept by `\arxivreaderref`).
  PDF figures (Image::Magick absent: `ltx_missing_image`) converted to PNG before LaTeXML (`_raster_figures`: sips,
  otherwise TeX's Ghostscript; width of the original figure; real PDFs only (`%PDF-`: no PostScript for
  Ghostscript), in the document's folder, 2400 px and 180 s at most in total, `page=` respected), images reduced to a few pixels by a length
  unknown to LaTeXML (`fix_tiny_images`). Empty bibliography (the `.bib` reread with the preamble: pdfx, CJK… stop
  LaTeXML): second conversion from a `.bbl` (`_lost_bibliography`, `_bbl`: compilation or BibTeX on the
  citations; biblatex `.bbl` changed to thebibliography, `_biblatex_bibliography`: done before the first conversion when the
  source loads biblatex, which LaTeXML does not know, so as not to convert a book twice).
  Remaining: lines of a multi-line node slightly tight (LaTeXML stacks them with no line spacing). Side-by-side
  subfigures (`ltx_flex_figure`): `latexml.css`. Size of figures (drawings, images) relative to the text
  (`figures.sizeToText`: LaTeXML width at 100 dpi converted to em based on the document's font size, 10pt by default).
  Hatchings of arXiv pages (development LaTeXML: the tile's `<symbol>` encloses the rest of the drawing, which is then
  invisible; surface filled in black, just before or just after a patterned group with no stroke, or after a
  redefinition of the pattern alone in a `<g>`): repaired at sanitising (`sanitize._repair_patterns`, before
  filtering). Checked on 6 arXiv papers found by going through 900 recent HTML pages. Bibliography: the `.bib` when all
  are found (an amsrefs `.bbl` escapes it), otherwise the `.bbl` (`nobibtex`). Implicit dependencies ("the above lemma", justification sentence,
  equations in the running text, corollary following a statement): `structure.py` and `texstructure.implicit_dependencies`.
  List labels unknown to LaTeXML ("(enumi)", e.g. `\greek*` from moreenum): redone by `relabelLists`
  (amsfix.js) based on the source (`texsource._label_format`), cross-references and tooltips included. Non-printed text:
  `strip_hidden` also removes the argument of an empty macro (`\newcommand{\comment}[1]{}`). LaTeXML is launched in the document's folder
  on a relative path (spaces in paths bother it), output in a temporary folder; bibliography through
  the compilation's `.bbl` (`nobibtex`). Made for one state of the source (`state.json`: `compiledAt` or arXiv
  version, `STRUCTURE_VERSION`): otherwise "stale", redone on click.

- Profile attached for the AI (`ai.reader_profile`, in the system prompt of every request, all tasks): feed
  categories, keywords, the user's papers (title, year, arXiv, beginning of the abstract: `PROFILE_ABSTRACT`), reference
  papers and books, followed authors (at most `PROFILE_ITEMS` per kind), free text `aiProfile` from the settings
  (`PROFILE_TEXT` characters, `<in_their_words>`). Everything is escaped (a title does not close the block). Exact preview:
  `GET /api/ai/profile` (Settings → AI assistant). Empty: no block, neutral prompt ("The reader is a researcher").
  Test run of the interface in a copy of the app: delete its `__pycache__` (embedded Python: bytecode not re-verified).
  Cross-references in answers: section keyword case-insensitive but number in uppercase ("la section a été…" (French: "the section was…") is not appendix
  A), a bare table-of-contents letter only for an appendix or "A.1"; whole bracketed label
  first ("[EGA-IV]"), then pieces separated by ",", ";" (outside an entity) or "–". Enumeration after a plural word
  ("Theorems 3.6, 1.2", "Lemmas 3.5–3.8", "§§2–3"): one link per known number (`listAfter`, `linkRefs`).
  The list closes after "and", keeps the depth of the first number (appendices: letters in order), and the
  "of [5]" test comes after it: "Theorems 1 and 2, 3 divides n" does not link the 3, "Theorems 1 and 2 of [5]" links nothing.
  Statement and equation numbers may start with an appendix letter ("Theorem B.2", "(B.12)", revtex "(A1)"): same
  pattern in `ai.js` (`NUMBER`), `texexport.py` (`NUMBER`), `graph.js` and `reader.js` (`citedStatement`), to keep in
  step — a label read only from its digits ("Theorem B.2" → "2") links "Theorem 2" to the appendix (bug before 1.14.7).
  The letter is uppercase only, checked in the list (`sameStatements`: the `i` flag would let "p.3" through); a body
  number and an appendix one can share a list ("Theorems 2 and B.2"). A statement may also be numbered by a letter
  alone ("Theorem A", `STATEMENT_NUMBER` in `ai.js`, `LETTER` in `texexport.py`; never for equations, where "(A)" is
  a condition): in a list it follows only a letter ("Theorems A and B", "Theorems A and 2.1", not "Theorems 1 and A
  proof"); a cross-paper citation needs the statement word ("[12, Appendix A]" stays plain); the .tex export links it
  only when an anchor carries the letter (`lettered`: a `\newtheorem*` has none, and its `\ref` would print the
  section number). LaTeXML numbers a whole `align` group once
  (`ltx_tag_equationgroup` on the group table): `structure.EQUATION_TAGS`, `reader.js` `EQUATION_TAGS`.

- Files dragged from the computer (import bar, library list: `library.importDropped`): paths requested from the
  window (`util.droppedFilePaths`) — Mac: `DropWebView` (main.swift) retains the PDF/TeX paths of the
  drag (draggingEntered, performDragOperation), returned once (`droppedFiles` → `arxivreader:dropped`); Windows:
  `postMessageWithAdditionalObjects` (files → `CoreWebView2File.Path`, MainForm.cs). Then same as the button
  (`importChosen`: PDF by path, TeX compiled in its folder, `.bib` and `\input` relative to it). Browser:
  PDF contents only.

- Dependencies (at every audit): `.venv/bin/pip list --outdated`; security advisories:
  `gh api -X GET /advisories -f ecosystem=pip -f affects=<paquet>@<version>` for each line of requirements.lock;
  pdf.js and MathJax (static/vendor, versions in their READMEs); the Claude Agent SDK and its CLI (options and login change:
  check whether the application must be adapted); embedded Python (conda-forge) and the Linux CI (3.12 →
  3.15). Update: tests, hidden-text check (the 16 papers listed by arXiv:2507.06185, described further down in this file: every
  instruction flagged, none passed on; no script in the repository), then `./arxivreader lock`. Embedded Python = the one in `.venv`
  (`build.sh` rebuilds a conda-forge copy of the same version from it): separate conda environment,
  `conda create -p ~/miniforge3/envs/arxivreader -c conda-forge python=3.14.X`, then `.venv` rebuilt on top of it
  (`rm -rf .venv .venv.nosync && PYTHON=~/miniforge3/envs/arxivreader/bin/python ./arxivreader`); never
  miniforge's base Python.

- Invisible text (`structure.mark_hidden`, at the start of the analysis: `plain` keeps it neither in labels nor in
  titles): text color white or nearly white (`structure.luminance` ≥ `WHITE`, 0,9; a saturated hue, channels at least
  `SATURATED` 0,5 apart, never counts as white: pure yellow is readable on the page), transparent or zero alpha (`_hiding`, `_color`:
  #rgb(a), #rrggbb(aa), rgb(a)(), a few names; not a background color: `--ltx-bg-color`, `background-color`,
  `--ltx-fill-color`), tiny font, hidden, opacity < 0,1; marked `.arx-hidden-text`, counted, never passed to
  the AI (`ai._render`, which does not read drawings either). Exceptions (white text only): dark background set on it
  or on a parent (`\colorbox`, `_dark_background`; dark: luminance < `DARK`, 0,6, i.e. a contrast with white of at
  least 1,6, so light turquoise labels stay); in a drawing, a dark shape covering its full width, drawn
  immediately before in the same coordinate frame (title of a tcolorbox box, `_on_dark_shape`, same `DARK`). Conservative:
  an unreadable shape on top (moved, relative commands, image), with no surface, partial, or a foreignObject with no
  width, and the text stays flagged; shapes read once per group.
- Invisible text in a TeX source (`texstructure.invisible`, used by `strip_hidden`, hence by `extract` and `ai.tex_body`;
  thresholds `structure.WHITE` / `DARK`, shared with the HTML and the PDF). Colors are evaluated where they are used
  (`_Colors`: a color redefined later does not change what precedes; `_xcolor`: names, mixes `white!95`, `black!3`,
  complement `-black`, weighted mixes `rgb:white,2;black,1`, models `[rgb]`, `[HTML]`, `[hsb]`, `[named]`…). Hidden: white or nearly (same rule as the HTML) by
  `\textcolor`, `\mathcolor` (argument in braces or `\bgroup…\egroup`), `\color` — scope (`_scope_end`) up to its
  group (braces, `\bgroup`, `\begingroup`), its environment, the end of the math it sits in (`$`, `$$`, `\)`, `\]`),
  the closing `]` of an option list (`fonttitle=\color{white}`, `_option_list`), the end of its cell in a table or an
  alignment (`&`, `\\`), the next `\color`/`\normalcolor` at its level (not inside `\verb` nor after `\string`); a `\color` that is the value of `backgroundcolor=`, `rulecolor=`, `fillcolor=` (`\lstset`) is a color, not text;
  at most `_SCAN_BUDGET` characters scanned per text (`_Work`), then open —
  or a tikz node whose text color is white (`[text=white]`, `[color=white]`, a color alone `[white]`; the last one wins; `[fill opacity=0]` only clears the fill, the text stays readable); `\fontsize` under 1 pt, taking effect at `\selectfont` or at any font switch (`\rmfamily`, `\normalfont`, `\bfseries`, `\usefont{…}{…}{…}{…}`…) (scope up to `\normalsize`, `\small`…), `\scalebox` under
  0,1 on either axis (nested ones multiply), `\resizebox` under 1 pt or with a width under half a point per letter of its longest line, `\texttransparent` and `\transparent` under 0,1, tikz node, picture
  or scope `[opacity=0]`, `\phantom`
  (removed, never counted); the author's macros and environments that do the same (`_hiding_macros`: `\newcommand`,
  `\def`, `\NewDocumentCommand`, environments; each argument `#k` whose every use is hidden, and each argument the body never uses, `\newcommand{\ign}[2]{#2}`: removed, never counted, as for `\newcommand{\x}[1]{}`; not for a redefined sectioning command, which stays a heading; arguments are read as declared: the optional `[…]` of `\newcommand{\x}[2][default]` or xparse `o`, the `*` of `s`), and what a definition hides without a
  parameter. Kept: a declaration that runs to `\end{document}` with no readable text before it (preamble, start
  of the document: a setting of the whole document); white on a dark background (`_Context.on_dark`): last
  `\pagecolor` (outside definitions, readable and dark), dark `\colorbox` (also opened by `\bgroup` in an
  environment's opening code), the arguments of an author macro (calls looked up by a running maximum of their ends, `_Context.reach`: linear in the number of calls) or the body of an author environment whose
  definition draws a dark box or fill (also in an environment's closing code: `\end{lrbox}\colorbox{black}{…}`; or opens an author's dark box: `\begin{mybox}` after `\newtcolorbox{mybox}{colback=black}`), `\todo[color=…]` and `\hl` after `\sethlcolor` when dark, the option list of a
  box (title, title font: on the title bar), colored box around it, however far or deep (tcolorbox, mdframed,
  shaded: its own fill option if it has one, otherwise its definition `\newtcolorbox{name}{…}`, `\newmdenv`, or a dark `\tcbset`/`\mdfsetup`;
  `shadecolor` dark), a tikz picture, pgfplots axis or tikzcd that draws an image, a plot, a background layer or a
  dark fill anywhere in it, dark fill declared before it in its environment, closed environments excluded: option
  keys `[fill=…]`, `[colback=…]`, `[background color=…]` (after a command, `\begin{…}`, `node` or `)`; not in prose,
  not `none`), table cells (`\columncolor`, `\rowcolors`, also just before the table; `\rowcolor` of its row,
  `\cellcolor` of its cell, a `\\` inside `{…}` not ending the row; also after the color in its own scope), tikz shapes `\fill`… and dark tikz styles, also through another style (in a
  tikz picture); a definition that sets its own dark background; beamer documents (`\documentclass` of the preamble).
  What TeX never executes is blanked first (`_unexecuted`): comments, `\verb`, `\lstinline`, verbatim, lstlisting
  and minted environments, the false branch of `\iffalse`, of `\ifnum` between two numbers and of a `\newif\ifname` flag that no `\nametrue` ever sets (up to its `\else`); `strip_hidden` removes those branches from the text (`_dead_branches`). Past ten times `_SCAN_BUDGET`
  characters of windows on a text (tens of thousands of white passages), white text is judged without background. Count in `hiddenText` of `structure.json`; papers analysed before are
  analysed again when loaded (`STRUCTURE_VERSION` 21), so their statements (`node["tex"]`, sent to the AI) lose it too.
- Invisible text in a PDF (`localpdf._page_text`, class `_Visible`): the content stream is walked with its graphics
  state (q/Q, `cm`, fill and stroke colors with their color spaces and Indexed palettes, `gs` opacity, clipping `W`,
  optional content `BDC /OC` hidden by default, `Tf`, `Tz`, `Tr`, text matrices), each text show judged with the
  state in force. Hidden: white or nearly (for fill-and-stroke text, `Tr` 2/6, both colors) unless the last shape
  painted under its start and its estimated middle is dark: a dark (or unreadable) fill, a thick dark stroke (a
  straight bar, or the sides of a stroked rectangle), thin dark strips painted one after another (sliced gradient), a
  shading (`sh`) or an image, at least `MIN_BACKGROUND_PT`, cut to the clip in force (a shape under `MIN_BACKGROUND_ALPHA`
  opacity, or in a hidden layer, paints nothing) (an opaque light fill, rectangle
  or axis-aligned outline, in normal blend mode without soft mask, painted later covers it); size under `MIN_VISIBLE_PT` on
  either axis (`Tf` size, times `1000 × FontMatrix` for a Type3 font: the bitmap fonts of dvips + Ghostscript have a unit matrix
  and size 1, the pixels being scaled down by `cm`), opacity under 0,05 (both for `Tr` 2/6), outside the clip, hidden layer (membership dictionaries by
  their `/P` policy; a `/VE` expression is not read: visible), invisible mode (`Tr` 3/7) except on a scanned page
  (images, whole or in strips, cover at least `SCAN_SHARE` of it: text layer of a recognized scan; a first pass finds
  it when the image comes after the text, also when the page draws forms). Form XObjects are judged at each draw with the state of that moment: a
  cleaned copy is drawn under another name, the shared original is never changed; forms nested deeper than
  `MAX_FORM_DEPTH` are emptied, a form that draws itself has that call emptied; each draw counts `FORM_COST` operations, and beyond `MAX_STEPS` operations on a
  page, forms are left as they are. Shapes are indexed in a grid (`GRID`); beyond `MAX_LOOKUPS` shapes examined on a
  page, only the last 64 shapes of a grid cell are examined. Hidden shows are removed from the stream in memory before pypdf extracts the text;
  counted (`hidden`, then `hiddenText`) when they hold letters: judged on the text decoded with the font (`_decoding`: the 256 codes
  of the font shown one by one in a blank page and read by pypdf, kept per font), since a subset font can put letters on
  punctuation codes; a CID font counts any non-zero byte. The rewritten stream is parsed again as bytes (`ContentStream(…, "bytes")`): pypdf would reuse an
  already parsed stream with strings decoded without the fonts' encodings and `ToUnicode`, and the whole page would
  come out wrong (ligatures, math symbols, CID fonts). A `text.json` older than `TEXT_VERSION` is redone once
  (`_refresh_text`), at startup (`redetect_all`) or at the AI's first reading.
  The reader shows the count without a reveal button when only the PDF is displayed (`reader.hiddenTextSource`).
- Known limits of both (heuristics, by design): other conditionals (`\ifdefined`, `\ifx`, `\if`, `\ifcase`, a flag set true somewhere), a font size combined with a scale (`\tiny\scalebox{0.15}`), TeX indirection beyond simple definitions (`\let`, styles of other
  packages, `\pdfcolorstack`, `\special`, fontspec `Color=`), text covered by a shape or placed off the page, colors
  behind Separation or pattern color spaces and shading colors (counted as dark), near-white just under 0,9. Leaning
  towards keeping readable text: in TeX, white text in a tikz picture that has a dark fill elsewhere, or in a box
  whose title or definition is dark, is kept; white text after a dark tikz overlay already closed (title page) is
  removed. In a PDF, a dark shape under the start and middle of a white line keeps the whole line; light shapes that
  are neither rectangles nor axis-aligned outlines do not cover. The
  prompt keeps its other defences (`ai._guard`, tagged blocks, instructions).
- Author text in the prompt: `ai._prompt_structure` (called by `build_prompt`) gives the AI a copy of the structure
  whose statement and equation labels are plain text (`_plain_label`: no `<`, `>`, quotes, 120 characters) and whose
  macros go through `_guard`.
- MathJax and the paper's TeX (`math.js`): the packages `require`, `html` and `bbox` are removed (no `\href`,
  `\style`, `\class`, `\bbox`), and `\mmlToken` is replaced before typesetting (`rawMml`, before `slashLetters`):
  a paper could otherwise draw an overlay or a link over the page. `\fcolorbox` and `\unicode` (autoloaded `color`,
  `unicode`) still copy their argument into a style, so a TeX postFilter (`cleanTexMath`, added in
  `mathJaxOptions().startup.ready`) cleans the tree built from TeX (titles, abstracts, notes, AI answers; not
  LaTeXML's MathML): a `style` is kept only as `border: <width> solid <name or #hex>` (frame of `\fcolorbox`), a
  `fontfamily` only without CSS punctuation, `href`/`class`/`id` always removed. A legitimate `\bbox` shows as raw
  text (package removed). `tests/test_mathjax.py` runs the shipped MathJax with these options in Node
  (`tests/mathjax_in_node.mjs`, minimal DOM): no `position: fixed` from either command, colours and frames kept.
- Slashed letter (`\not{\mathcal{D}}`, `\not D`, `\slashed{D}`: Dirac operator): LaTeXML leaves the letter followed by
  U+0338, a combining slash that **Chrome draws on the letter and WebKit after it** (on the subscript). `math.slashedLetters`
  replaces it with the font's same non-combining slash, ⧸ U+29F8 (slash of `\not` and of ≠: height 0,716 em,
  depth 0,215 em, box the width of an "="), with no width, at the start of the letter — as TeX places `\not`;
  in front of the whole subscript if the letter is its base (`attachScripts`), which stays placed as for `\mathcal{D}_m`.
  Letter kept calligraphic (`restoreCalligraphic`). TeX of titles, abstracts and notes: `\not` before a letter
  or a group → `\arxivslash` (`\rlap{\mathrm{⧸}}#1`); MathJax's `\not` puts the slash on the subscript, `\unicode`
  takes it from a fallback font; relations `\not=`, `\not\in`… and the author's `\cancel` left intact. Verified
  on 2610.36240 **in WebKit (copy of the app, ARXIVREADER_EVAL + WINDOWSHOT) and in Chrome**.

- Author colors (latexml.css, end): LaTeXML's `--ltx-fg-color` / `--ltx-bg-color`, and the `mathcolor` /
  `mathbackground` of formulas put into these variables by `amsfix.colorVariables` (otherwise MathJax or the browser
  freezes them: imposed black invisible in dark mode). Unchanged in light mode; in dark mode, oklch of the same hue,
  text lightened (lightness ≥ 0,72, black becomes white), background darkened (≤ 0,35; white or nearly white: transparent, it is the color of the
  page), `\rule` lines lightened like the text; never in drawings or their HTML labels (`.figure-card`, white
  background: black text there would become white on white). Verified on the 16 arXiv papers with hidden prompts listed by arXiv:2507.06185 (cited versions): each
  prompt flagged, none passed on, no other flag. The reader: "Show" goes to the first passage, "Next"
  to the others. SVG figures inserted via `<object data>`: changed to `<img>` during sanitizing (`sanitize._objects_to_images`).

- Followed authors and homonyms: a single rule, `relevance.follows` (same person according to the name, paper not dismissed,
  categories kept), used by the feed (`score_items`), the search for recent papers
  (`profile.followed_author_papers`, which no longer compares full names for equality: "George M. Marinescu" is "George Marinescu") and the alerts (`alerts._who`, `forget_unfollowed`). Any new way of
  recognizing a followed author goes through it. Categories normalized as arXiv writes them (`profile.clean_category`).

- Assistant and documents without text or too long: a scanned PDF (`ai.without_text`) sends the pages around the
  question as JPEG (7 before, 3 after, 12 MB at most: the furthest ones left out); a document beyond
  `PAPER_LIMIT` is sent reduced to its beginning and a window around the question (`ai._focus`, `ai._window`), except
  summary and review. A paper under the limit is sent whole, except a summary or review of a section when the paper and the
  attached context (`_chapter_context`) do not fit within the limit (`room = max(limit // 2, limit - len(context))`,
  `_reduced_to_section`). Paper beyond the limit, question asked on the summary or review of a section: that section takes
  priority over the window, unless the question targets a precise passage (`node`, `quote`, `zone`) found in the text; the
  displayed page, attached to every question in PDF mode, is not one. Audit of a proof: the statements and equations
  it refers to (`uses`, `usesEquations` of the node; equations only with the HTML version) are attached in
  `<used_statements>` (`ai._used_statements`: statements without their proofs, at most `USED_STATEMENTS`, within
  `min(USED_BUDGET, limit // 5)` characters), and the paper window is reduced by as much (`room`). Not the statements that
  depend on it: no published proof checker uses them (block verification passes the used results only).

- Smoothness of the AI tab (measured on 29/10/2026 in WebKit, 21 long answers, 64 000 px of thread): the thread is
  `contain: strict` (scrolling 23 → 17 ms per frame); the `--ai-scrollbar` variable is set only if it changes
  (set on every scroll, it restyled the whole thread); while the panel handle is dragged,
  the thread follows the handle on every frame, but only the visible answers are laid out again: `.reader.resizing
  .ai-answer { content-visibility: auto }`, the others keep their last height (`contain-intrinsic-size: auto`),
  the thread does not jump (54 → 16 ms per frame, gap at the panel edge 0 px). Rejected: thread frozen until release
  (1.11.12, large blank area when widening), laid out again every 100–200 ms (the blank area stayed visible for a moment). The
  setup happens on press; cursor and clicks blocked by an overlay (`.resize-shield`), not by inherited properties
  set on `.reader` (whole paper recalculated: ~400 ms at the start of the drag); moves listened to on
  the window (the overlay must never remain).
  First visit to the AI tab (1,6 s → 0,46 s): MathJax with `adaptiveCSS: false` (`math.js`), otherwise it adds
  rules to its stylesheet for every new character of an answer, and WebKit recalculates the whole paper (~1 s);
  and the AI tab is marked on the panel (`data-tab`), not on the root of the reader (otherwise ~180 ms of recalculation of
  the paper on every visit); its default width (`AI_PANEL_W`) is set only when no width has been chosen
  (`applyPanelWidth`). Rejected:
  `content-visibility: auto` on the answers (just as fast, but estimated heights: the kept position of each
  sub-tab would land elsewhere, and WebKit does not compensate for jumps when scrolling up). Release still costs
  ~250 ms: layout of the entire paper at the new width.

- Session token (`config.TOKEN`, `ARXIVREADER_TOKEN`): randomly generated by the Mac window (`main.swift`), the
  Windows window (`Program.cs`), the Windows launcher in browser mode (`launcher.py`) and `./arxivreader`; the page
  opens via `/?t=<jeton>` (HttpOnly cookie, SameSite=Strict, then redirect to `/`), the windows send the
  `X-ArXivReader-Token` header. The server writes it to `server.token` (readable by the user only, removed on
  shutdown) so that a launcher can reopen a window. Without the variable (tests, test runs), the API does not require it. Test run
  in a copy of the app: `curl` without the token must respond 403.
  Left by design (audit M1): in browser mode (`./arxivreader`, Windows fallback) the cookie is host-only, and
  cookies ignore the port, so the browser also sends it to any other server listening on 127.0.0.1 (a link to
  `http://127.0.0.1:<port>/` opened from a paper); that server would get the whole API. It takes a hostile program
  already running on the computer; the Mac and Windows windows send the header and are not concerned. A fix would
  carry the token in the URL fragment and send it as the header only.
- API keys in the keychain (`app/keystore.py`, only with `ARXIVREADER_KEYCHAIN=1`): macOS through the
  `security` tool (write command on standard input, never as an argument), Windows through CredWriteW; one entry
  per key and per library (path digest), so a test copy never touches the real keys. `db`
  reads and writes them in place of the database, migrates them into the keychain at startup, and falls back to the database if the keychain refuses.
  `app.check` (verification, by hand or weekly) enables it itself in `main()`.

- Assistant accounts (settings, `accountControls`): changing account = "switch" step of `open_setup`
  (logout then login in the Terminal or the command prompt). Claude: a browser already logged in to
  claude.ai would pick that account back up, so the login happens outside its session through `$BROWSER` (the CLI calls it without a shell with the
  link; verified on 2.1.319, to be re-verified if the SDK changes, without ever logging out: temporary HOME and `CLAUDE_CONFIG_DIR`,
  pseudo-terminal, kill before the end). Mac (`_private_browser`, `PRIVATE_WINDOW`): Chrome, Edge, Brave,
  Firefox in a private window; Safari, which has no command for this, or an unknown browser: link copied, instruction displayed.
  Windows: `BROWSER` does not exist, the CLI displays the link, instruction displayed. The plain login ("login") does not
  change. Logging out = `/api/ai/logout`
  (`ai.logout`: `claude auth logout`, `codex logout`). Another Claude account: its list of models is re-read
  (`_claude_status` compares the address). AI tabs that are open forget the state of the assistants (event
  `arxivreader:ai-accounts`). Never start a logout during a test run: the Claude login is that of
  Claude Code on the machine.

- Choice of the assistant in the AI tab (`Assistant.modelPicker`), which writes `aiProvider`, the model and the
  level. Default assistant (`aiDefault`, empty: the last one chosen): put back into `aiProvider` when the server starts
  (`select_default_assistant`) and as soon as it is chosen; an assistant that has just become ready in the settings replaces
  the one in use if the latter is not actually ready (`adoptIfActiveNotReady`; `inconclusive`: check without a clear answer — network, timeout, quota, unreadable response —, never a switch; only an explicit refusal or a local server that is off count as "not ready"). The settings serve only to configure each assistant (`configuring`, never saved; `/api/ai/status?provider=`); an
  account changed, or the section left (closing the window), triggers `arxivreader:ai-accounts` there, which makes the assistants be re-checked. The
  list comes from the state of the chosen assistant and from `/api/ai/partners` (the others, ready). Changing assistant
  (`switchAssistant`) reuses their already known states, set before saving the setting: saving
  redraws the panel immediately (`reader`, `onSettingsChange`), which would otherwise restart the checks.

- "Continue in Claude" (`Assistant.continueInClaude`): the `claude://claude.ai/new?q=` link holds about 14 000 characters. Beyond
  13 000, the full text goes through the clipboard and the conversation opens empty, to be pasted with ⌘V (Claude turns a long
  paste into an attachment, like "Copy the exchange"): never a truncated start that could be sent by mistake. Only if the copy
  failed is the start put in the link.

- Cross-review (`ai.cross_review`, called by the AI route when the request carries `second`): reviews
  by the two assistants in parallel, comparison by the second one, synthesis by the first one; all the requests go
  through `build_prompt` (same text, without the invisible text; paper limit specific to each assistant). The
  attached reviews go into `<review>` tags, declared "to be analysed" in `SYSTEM`. Steps saved
  in the `steps` column of the exchange (the synthesis), markers `[[both]]`, `[[a]]`, `[[b]]`, `[[dispute]]`,
  `[[withdrawn]]` shown as labels (`LiveAnswer.html`, placed after the Markdown) and written out in plain words in the
  notes, the copy and the export (`ai._marker_words`). An error from the second assistant carries its name and the code
  "other": a "login" code would make the first one appear logged out. Ready assistants: `/api/ai/partners`
  (`ai.partners`, checked in parallel; kept for two minutes by the page).

- Review prompts (`REVIEW_GUIDANCE`, `REVIEW_VERDICT`, `REVIEW_STYLE`, `REVIEW_POINTS`, `REVIEW_CHECKED`,
  common to the paper, the chapter, the cross-review synthesis and the book synthesis): a referee's report. Verdict
  first (at most four sentences, no list, with what holds nonetheless), then the points ranked Critical /
  Serious / Minor; each severe point is complete on its own: bold title with (certain) or (suspected), then one
  or two paragraphs of prose, repair included (no fields or sub-bullets, no "Main results"
  or "What survives" sections, which repeated the points); typos in a single point; "What I checked" in one or two
  sentences. An author reviews their own text with it: an error is always stated, a repair never removes it,
  a wrong proof is not a wrong result. `SYSTEM` asks for prose (a list only for parallel items, at most one level of
  nesting) and forbids checks claimed without content ("I checked
  this", "numerically"): the assistant has no computing tool. Summary: claimed novelty, limitations and open
  questions. Review of a chapter: ends with its record card (`CHAPTER_RECORD`); the exchange then carries `card = 1`
  (column `card`, set by `ai.finish`).
  Length (`REVIEW_LENGTH`, `BOOK_LENGTH`, the `{length}` slot of `REVIEW_GUIDANCE`): a ceiling, not a target
  (`REVIEW_WORDS` = 5,000 words; 7,000 for the parts of a book or sections synthesis written for the whole text; 2,500
  for the comparison and verification steps), above every review made so far (16/11/2026: median 2,200 words,
  longest 3,700). Only repetitions and the form of Minor points shrink (location, problem and correction kept); no
  point is dropped, typos included; a Critical or Serious point keeps all its parts; completeness wins over the
  ceiling. In a book synthesis, carried-over serious points keep their full paragraph and the chapters' Minor points
  are only counted (its own format). The ceilings are for concision; they do not prevent the error below (the
  thinking, not the text, fills the cap). The Claude CLI caps each answer, thinking included, at 32,000, 64,000 or
  128,000 output tokens depending on the model: `_stream_claude` sets `MAX_OUTPUT_TOKENS` (128,000, through
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, or the user's own value if higher), which lowers no model's default; the CLI brings
  it down to each model's maximum. Never a smaller value: it would lower the cap of the models whose default is higher. Reached anyway (`OUTPUT_LIMITS`:
  "output token maximum", or "context window limit"): text received is kept and marked cut; no text at all (the
  thinking took everything; the CLI then sends its error, "API Error: …", as the answer text) is code "reasoning",
  never an empty answer: `_claude_lowering` asks the same question once more, one reasoning level lower
  (`lower_effort`; "medium" when no level was chosen, since the assistant's default differs by model) and told to
  reason much more briefly (`BRIEFER`), and records it (`lowered`: column of `ai_exchanges`, key of a step; badge
  "ai.lowered"); a second failure is the error ("ai.error.reasoning"). 128,000 is the models' maximum: no setting
  raises it; only less thinking leaves room. The CLI itself recovers first, inside the same request, up to 3 times
  (`maxOutputTokensRecoveryCount` in its query loop, even with `max_turns=1`): cut text → "Output token limit hit.
  Resume directly…" (the continuation arrives in the same stream, joined); reasoning only → "…reason much more
  briefly", or resumes the unfinished thinking. So the error reaches the app only after those attempts (up to about
  4 × 128,000 tokens): no relay of our own (it would repeat the CLI's, resending the whole paper each time); the
  lower reasoning level is the one lever the CLI does not use. A cut intermediate step (review A or B, comparison, verification) is announced to the next one by a
  note of the application placed after its `<review>` block (inside, the model ignores instructions: `SYSTEM`) and
  shown on the step (`stepsFold`, "ai.stepCut"); the final review is marked cut only when its own text is.

- Rendering of answers (`LiveAnswer`, `markdown.js`): the answer is cut into blocks at blank lines (`splitBlocks`,
  rendered as the stream arrives); `markdownToHtml` handles nested lists (stack of lists, indent of at least two
  more spaces). A block that starts indented is the continuation of the list item of the previous block (paragraph
  of a review point): class `ai-continued`, same indent as the list.
  In `markdown.js`, `inline` percent-encodes `` ` ``, `*` and `_` in the address of a link before the code, bold and
  italic passes, which would otherwise write markup inside the `href`.

- Book, all chapters in parallel (`app/bookjob.py`, routes `/api/papers/{id}/ai/book`, `…/book/stop`): background
  job of the server (`asyncio`), one per book and per kind, `PARALLEL` chapters at a time (cross-review:
  `ai.cross_review` for each); it continues if the page closes, stops with the server (`stop_all` in
  `lifespan`) or when the paper is deleted (`forget`, from `library.remove_paper`: from another thread, it hands
  the forgetting to the server loop, `_forget`, the only one to touch `jobs`). Each
  finished chapter is an ordinary exchange (target: the chapter); on stopping or on an error, an unfinished chapter is
  deleted and the synthesis already begun is closed as "stopped" (`close_open`). A new launch reuses the last finished document
  of each chapter (`reusable`; review: with `card`), except with `fresh`. Synthesis: exchange with target
  `ai.BOOK` ("@book"), prompt `BOOK_PROMPTS` built by `ai.book_prompt`: table of contents, cross-references between chapters
  (`_book_links`), documents of the chapters in `<chapter_review>` / `<chapter_summary>` (tags neutralised, beginning
  and end kept if too long, `_cut_middle`; review: the last part, from the last "###" heading, kept
  whole if it fits in 7/10 of the share: that is the record card, located by its position because the model translates its title),
  never the text of the book. State (`state`) kept for one hour after the end (error shown to the page), purged at each
  launch or poll (`_purge`); finished job: book and answers released. Page side: first entry of the `chapterBar` menu (`bookPlace`), presentation
  `bookIntro`, progress `bookProgress` updated in place (`bookLive`; elapsed time
  `job.elapsed`, no estimate of the remaining time, often wrong; total duration in the final message), tracking `pollJobs` every 2 s as long
  as a job is running, 10 s after three failures, stop and panel rebuilt if the paper no longer exists (404); a change
  of state reloads the exchanges, `reloadExchanges`, which keeps the object of the request in progress and points the inputs
  (`pending`, `docPending`: reply to, redone question) to the new objects (it returns `false` when the list could not be
  read: `pollJobs` then keeps the previous state of the jobs and tries again at the next tick, so the end of a task is
  announced once, after a successful reload), then rebuilds the panel; "chapter in progress"
  notice only on an exchange of the job (`chapters[].exchange`, `synthesis.exchange`); `reusable` as on the server side; links to the chapters below the synthesis
  (`bookChips`). "Whole book" (a single send, truncated beyond `paper_limit`) has disappeared from books.

- Papers, section by section: `structure.assign_sections` (called by `assign_chapters` when it is not a book)
  sets `sections` (first level of the table of contents, except references and acknowledgements; at least two), `chapter` (section of
  the statement) and `provedIn` (list of the sections of its proof, often another one, passage indicated by hand included) on each statement. `ai.parts_of` gives the chapters,
  otherwise the sections: `bookjob`, the `/ai/book` route and the `chapter` parameter serve both. A section is sent with
  **the whole paper** (notation often fixed in the running text), prompts `SECTION_PROMPTS` and, for the synthesis,
  `SECTIONS_PROMPTS`, derived from those of the books (`_to_sections`, `_rewritten`: a sentence that has disappeared makes the
  import fail). `chapter_links` counts what a proof uses in the section where it is located (`chapterLinks` in `ai.js`,
  same rule); `<statements_proved_here>` lists the statements written elsewhere and proved there. Paper too long for
  the assistant (local model): `_reduced_to_section`, including for questions asked about the review of a section. Cross-review: `scope` keeps the comparison and the synthesis on the section. HTML version (LaTeXML) whose sections differ from the server's structure: no menu (`sections` emptied in the route). Page side (`ai.js`): the same `chapterBar` menu, entries whole
  paper (empty value), `SECTIONS` (parallel job) then sections; texts `ai.sections.*` (`tb`, `tnb`, otherwise those of the
  book). Graph: `reader.sectionGraph`, the whole paper by default (`graphSection`). "Statements" tab unchanged: following on
  scroll remains specific to books. The menu is called Portée / Scope for papers as well as for books.
- Books, summary and review by chapter: `structure.assign_chapters` (arXiv HTML and `texstructure`) marks
  `book`, lists `chapters` (top-level chapters and appendices, according to the `kind` of the table of contents entries) and sets
  `chapter` on each statement. The request carries `chapter` (summary or review only, otherwise 400); `build_prompt`
  sends only that chapter (`_chapter_text`: element of the HTML, otherwise n-th `\chapter` of the expanded source),
  the table of contents and `_chapter_context`: statements of other chapters that are used (`<used_statements>`) and that depend on it,
  in a chain through `usedBy` (`<dependent_statements>`, with `depends_on`), statements only, at most `CHAPTER_STATEMENTS`.
  Prompts `CHAPTER_PROMPTS`, and `CHAPTER_SYNTHESIS` for a cross-review. Page side (`ai.js`): menu
  `chapterBar`, threads per chapter (`chapterOf`, positions by `place`), `chapterLinksBox` lists; chapter read by
  default: `reader.readingChapter`. "Statements" tab of a book: menu `statements-chapter` in place of the title of the
  chapter (`bookChapters`, shared with the graph; `statementsChapter` follows `chapterOfReading` while scrolling and in
  the PDF, `followStatementsChapter`; a choice in the menu, `statementsChosen`, holds as long as the chapter being read remains
  `chosenWhileReading`; "Whole book" when chosen always holds); during a search, no more menu: the whole book, sorted by chapter.
  Pages in the PDF of a book read in HTML: LaTeXML numbers sections and statements consecutively ("7", "7.10", appendices
  "9.B.1"), the PDF by chapter ("2.3", "2.3.10", "A.2.1"); `texstructure._book_sections` recomputes the PDF numbers
  for the anchors (`subsection.2.3.10`…) and the searched text; the numbers of the statements only if the book
  numbers them after the section of the HTML (`_numbered_by_section`, otherwise a "Theorem 1.4" numbered by chapter would go to
  `subsection.1.1.4`), with the original label as a fallback for the search. Changing this computation: increase `PAGES_VERSION`.
  Text of a TeX chapter: counted and cut on `ai.tex_body` (source without hidden text, as for the whole
  document: `strip_hidden`). Attached statements: at most `max(20000, limit // 4)` characters in total (`_chapter_context`; the floor of 20,000 applies to the local model, whose limit is 70,000), data tags neutralised
  (`DATA_TAGS`); title of the chapter reduced to one line without tags in the prompt. Draft and "Edit": one per thread
  (`Assistant.draft`, key `place`); chapter choice kept when the structure is rebuilt (proof indicated).

- TikZ drawings, labels made of a paragraph (minipage, `\parbox`: tcolorbox frame): the text wraps
  (`white-space: normal`, box as `inline-block` whatever block-level wrapper LaTeXML puts around the label (no selector names these wrappers); paragraphs
  and lists as blocks, bullet on the line of its text: everything in there is `span`), positioned by the top of its box and kept inside the smallest shape of the drawing that contains it
  (`figures.shapesOf`, same margin on the right as on the left, font reduced by at most a quarter if it is too tall); drawings
  "remember picture, overlay" (1 × 1 with no stroke) hidden (`figure-overlay`), as on arXiv.

- Imported TeX files (`texfile.py`): compiled in `<temp>/arxivreader-tex-<uid>/<id>` (plain `arxivreader-tex` on Windows), never in the user's
  folder (latexmk `-outdir`); the root is created with mode 0700 and refused (`TexError`) if it is a link or belongs to someone else
  (shared `/tmp` on Linux: `build_dir`). A file named by the source (`\input`, `.bib`) is copied for LaTeXML only inside
  the project folder (`texfile.in_project`: no `..`, no absolute path; otherwise LaTeXML reads the original, never
  rewritten). latexmk remembers that a `.bib` was missing and no longer reruns BibTeX: after a
  missing `.bib`, its state (`.fdb_latexmk`) is erased. Statements that have a `\label` have an identifier derived from
  it, the others from their type and their text (`stable_ids`, also on re-analysis: `stableIds` in
  structure.json). The `.aux` files of the compilation give the printed numbers and the exact anchors of the `\label`s
  (`apply_aux`: statements, equations; `cite.<clé>` for the bibliography, also read from a `.bbl` of biblatex or
  of amsrefs); a `\label` inside a list item of a statement (`itemLabels` in structure.json, `texstructure`) is skipped
  by `apply_aux`: the statement keeps its own number, not the one of the inner `\item\label` (bug of 1.13.3, structure
  version 17 forces the re-analysis);
  compilation with `-synctex=1`: `paper.synctex.gz` is kept next to `paper.pdf` (`_keep_synctex`), removed when the PDF
  read is not the one compiled here (chosen PDF, PDF found next to the file); the one of a previous compilation is never reused;
  log without wrapped lines (`max_print_line`); the highlighted passages of the PDF that have
  moved are found again by their text on the page side (`PdfNotes.inPlace`/`findMoved`, ±3 pages) and their position saved.
- Notes that lost their place after a recompilation of a TeX file (`texfile.py`, section « Notes après une modification du
  texte »): `_build` calls `track_lost_notes` when `source.tex` changed. `_lost` counts the statement notes (non-empty)
  whose identifier (`tex-txt-…`) is gone and the PDF text highlights (`target.pdf` without `rect`) whose letters (NFKD,
  letters only, no digits) are no longer in the pypdf text of the new PDF; unreadable PDF: only passages already lost stay
  lost. The context where the note had its place (`_context`: statement or proof, label, TeX of the statement ≤ 2000
  characters, page) is written once in `previous.notes.json`, keyed by annotation `uid`, never overwritten; the source of the
  replaced compilation is copied to `previous.source.tex`; both are deleted when nothing is lost (`previous.structure.json`,
  format of the first beta, is removed). `report(pid)` recomputes `notesToCheck` on each call (`notes_to_check`). Page:
  line of `texChecks` (`texfile.js`, `onAdaptNotes` → `Assistant.prefill`: question written, not sent), toast after the
  reload through `sessionStorage` (`texNotes:<id>`, `Reader.notesToCheckToast`), report re-read 400 ms after an annotation
  change (`texReportSoon`). The AI side is described under "AI on annotations" below. Tests: `tests/test_texfile.py`.

- Annotated `.tex` copy (`texexport.py`): never writes in the original project; the copy is a `.zip` built in a
  temporary file (`build_zip`; the route deletes it after sending, and each call purges those older than 6 h,
  `STALE_AFTER`) with `LISEZMOI-arXivReader.txt` / `README-arXivReader.txt` inside (language of the request). Only lines are added, each one carrying `[arXivReader]`. Files
  are read as UTF-8, otherwise latin1 (`_decode`), line endings kept. Limits: `MAX_COPY_FILES` (3000), `MAX_COPY_BYTES`
  (300 MB), files read outside the project folder not copied (reported). Only the files used by the paper are copied
  (`used_files`, applied in `_walk`, the others counted in `unused`: summary, dialog, README of the copy): files of
  the source, names given to any command (`referenced`: first argument, beamer overlays allowed; second argument of
  `\import`-like commands and `\inputminted`, `SECOND_ARGUMENT`; pgfplots `\addplot table {…}`, `ADDPLOT`; plus
  `\input` targets; also those of local classes and styles, `package_texts`) with or without extension, from the
  project folder, the folder of a source file or a `\graphicspath` folder, or by base name alone (a macro that adds the
  folder); files read by the last compilation (`texfile.recorded_inputs`, latexmk's `.fls` in the build folder: absent
  after pdflatex alone or once the temporary folder is emptied, so this only adds files); `INFRA` extensions (classes,
  styles, BibTeX/biblatex/index styles: TeX finds them without a name in the source); the `.bbl` of a source file.
  Over-inclusion is preferred (a word given to a command may match a file name). `scan` classifies the source (verbatim, math,
  boxes/floats, lists, comments): comment blocks go before `\begin` of a statement or proof; the `todonotes` margin
  note (`\arxivreadertodo`, guarded by `\ifmmode\else\ifinner\else`) is never put inside verbatim, boxes, floats or
  math (the comment stays). Quotes (highlights) are found by their letters in the expanded text (`texsource.expand_with_origins`,
  `quote_variants`, `tex_letters`); the author's macros are expanded (`user_macros`, up to `MACRO_DEPTH` levels,
  `MACRO_LETTERS` letters each), so a quote cut by a macro that prints a lot or is too deeply nested is not found: it goes, approximately, to the
  first statement or section of its page (`_page_start`), otherwise to the header block. Boxes and pinned notes are placed with SyncTeX (`synctex.py`, approximate): `Plan` compares the
  mtime of the source files with `paper.synctex.gz` and warns (`staleSynctex`) when the source changed since the last
  compilation; a PDF that is not the compiled one gives no SyncTeX: those notes go after the statement that ends above them on their
  page when the structure knows it, otherwise to the header. Wide layout: left margin
  1.5 cm, notes column 4.5 to 6 cm, page widened on the right, text width and height unchanged (breaks identical to the
  original). Long notes (`is_long`) are shortened in the margin and moved to a last section through `\AtEndDocument`
  (`_end_document`): two LaTeX passes are needed (page numbers show « ?? » after one). Notes are converted to LaTeX
  (`inline_tex`, `long_tex`, `short_tex`: formulas, amsmath environments only if the document loads amsmath, Unicode symbols;
  long formulas become « [...] » in the margin); numbers written in notes become `\eqref`, `\ref`, `\cite` through
  `references`, from `labels` of structure.json and the `.aux` numbers, only for labels still present in the source.
  Tests: `tests/test_texexport.py` (`tests/test_synctex.py` for the SyncTeX reader).

- Paper versions (`library.py`, `db.py`): the main row (id without version) always carries the latest installed
  version; an older version is a full paper row `<id>v<k>` (`archive_key`) with `in_library = 0` and `archived_of` = id of
  the main paper, its own folder `papers/<id>v<k>`, its own notes and AI exchanges. When `_import` gets another version,
  `_replace_version` renames the old folder to the archive folder, writes the archive row (`db.write_paper(connection, …)`,
  inside the caller's transaction), moves notes and exchanges to it (`_move_notes`: the unique `general`, `block`, `proof`
  rows are merged), then renames the new folder in. The database work is one SQLite transaction; the renames are outside
  it: any failure puts the old folder back, and `recover_interrupted()` (called by `main.lifespan`) undoes a half-done
  rename and clears hidden staging folders at startup. A version that already exists as an archive (notes imported from
  another computer) is absorbed back into the main paper (`_absorb`, marker `.absorbed`). `ensure_version` /
  `import_version` download a missing version (`arxiv.org/html/<id>v<k>` only, then TeX source and PDF); a failed download
  removes the pending row unless it holds imported notes. `versions_of` builds the menu (`latest_version`: one arXiv
  request, kept `LATEST_KEEP` = 1 h, answer within `LATEST_WAIT` = 20 s or `offline`); `open_version` returns
  `{id, running}` (latest version: `update_in_background`; otherwise `ensure_version`). Routes:
  `GET /api/papers/{id}/versions`, `POST /api/papers/{id}/versions/{version}` (502 with code `offline` on `ArxivError`);
  refresh refused for an archive; `GET /api/papers/{id}` adds `mainVersion` for an archive and answers 404 for a failed
  one. An archive never enters the library (`set_in_library` refuses), is never repaired by re-import and is removed with its
  paper (`remove_paper`, which takes the paper's `paper_lock` like an import or a re-analysis, so a deletion waits for them; never call it
  from inside a `paper_lock` block: plain `Lock`; the archives are removed before, each under its own lock); `clean_cache` keeps a main paper whose archive has notes. Notes imported from another computer go
  to their version (`notesync._version_of_notes`, `archived_of` in `PAPER_FIELDS`); an archive's export is named after its id
  (`export.filename`). Tab left open on a replaced version: the page sends `X-ArXivReader-Version` on the reader's writes
  (`api.setReadingVersion`, `versioned()`); `main._same_version` (`READER_WRITE`: annotation POST, proofs PUT/DELETE, `ai`,
  `ai/book`, position) answers 409 `stale_version`, shown with "Reload" (`util.toastError`, key `reader.stale`). Editing an
  existing annotation by id is not checked (the note followed its text). Page: `reader.versionChip` (menu `contextMenuAt`,
  polls `api.importing`), version notice, tab title "(vk)", library toggle hidden for an archive; `library.updatePaper`
  chooses the toast (`library.updatedMarks` when the previous version has notes or answers). Tests: `tests/test_versions.py`.

- New versions in the feed: `library.new_versions(force)` lists library papers with a newer arXiv version of the last
  `NEW_VERSIONS_DAYS` (45) that is not installed; arXiv asked at most every `NEW_VERSIONS_KEEP` (6 h), `force` from the
  feed's Refresh (`GET /api/feed/new-versions?force=true`; forced calls that wait for each other, double click or two windows, ask arXiv once:
  a forced call that finds an answer newer than its own start reuses it). `arxiv._paced` holds `_api_lock` during the whole `patient_get` on
  purpose (it is what guarantees 3 s between two arXiv requests): a slow arXiv can therefore delay the other arXiv requests, an import's
  metadata included, for up to a few minutes. The answer is kept in the setting `feed.newVersions` (shown at once
  and offline); an empty arXiv answer erases nothing and a paper missing from a batch keeps its entry; old-style ids are
  compared through `bare` (arXiv answers `math/0601001` for `math.DG/0601001`). Page: `loadNewVersions` runs apart from the
  feed (arXiv can be slow), with a counter (`versionsAsked`) against out-of-order answers; `showNewVersions` redraws only this
  section and keeps the reading position; `newVersionCard` updates through `library.updatePaper(…, {expectNew: true})` and a
  module-level `updating` set, so the spinner survives redraws; the end of an update, and a change of `feedNewVersions` in
  the settings, dispatch `arxivreader:feed-sections`. Settings `feedNewVersions` and `newVersionsCollapsed` (`allowed` and
  `BOOLEAN_SETTINGS`). Foldable feed sections are folded by default (`foldableSection`, `collapsedByDefault = true`); the
  reader's choice stays in `citingCollapsed`, `followedCollapsed`, `newVersionsCollapsed`.

- Full-text search of the library (`fulltext.py`, `GET /api/library/search?q=`, query cut to 300 characters): no index.
  Each search reads `content.html` (or `text.json` of an imported PDF or a PDF-only arXiv paper) of the library papers, most recently opened first;
  folded text cached in `_cache` (keyed on (path, `mtime_ns`), oldest dropped beyond `CACHED_CHARS` characters). `fold` removes accents and case through a 1:1
  character mapping (`_Folding`), so a position in the folded text is valid in the original: a new equivalence must keep
  one character for one. `_pieces`: `<math>` replaced by its `alttext` between `$…$`, blocks separate words; literal
  dollars become `PLAIN_DOLLAR` (U+FF04), so a remaining `$` always delimits a formula. `_locate`: one hit per paragraph or
  statement (LaTeXML ids; a statement is `div.ltx_theorem`), `MAX_PER_PAPER` = 3; `_snippet` never cuts a word or a formula.
  Annotations of library papers and of their archived versions (`ANNOTATED_PAPERS`) come first, notes before highlights,
  bookmark names counting as notes. Target of a hit: element id, `top` (title, abstract), or `annotation-<id>` (highlight,
  free note, anything placed in the PDF, label "PDF, p. n"), opened in place by `reader.showAnnotation` (bookmark, PDF
  annotation through `annotator.revealPdf`, HTML mark through `showElement`). `MIN_QUERY` 3, `MAX_RESULTS` 200. Page:
  `searchText` / `drawTextHits` (`library.js`, debounced 300 ms, `typesetTex`): snippets are set as text, never as HTML.
  `reader.navigate` also targets `.ltx_figure`, `.ltx_table`, `.ltx_proof`. Tests: `tests/test_fulltext.py`.

- AI on annotations (`ai.py`, task `ask` only, in `build_prompt`). Three kinds of request are recognised in the question
  by regular expressions; each one has its sentences in `tests/test_ai.py` and `tests/test_versions.py`: change a
  regular expression only with a test for the sentence that made it necessary.
  - Notes carried over: `asks_adaptation(question, tex)` = `_asks_other_version` (`_ADAPT_CLEAR`, `_ADAPT`, `_NOTES`,
    `_NOTES_ALL`, `_MENTIONS_VERSION`; sentence split that ignores "Thm. 3", "v. 1"; TeX file: `_MENTIONS_COMPILATION`) or
    `_asks_view_change` (`_VIEW_CHANGE`: a verb of `_TRANSFER`, notes `_OBJECT_NOTES`, destination `_TO_VIEW` html|pdf).
    Retries: `REDO_ADAPTING`, `_adaptation_redone`. `_adapting` returns (instruction, blocks): same version →
    `SAME_VERSION_PROMPT` + `PLACES_PROMPT` with `_annotations_block(paper)`; another version (`_version_to_adapt`: the
    named one, otherwise the most recent previous annotated one) → `<previous_version>` (cut by `_cut_middle` to the room
    left) and `<previous_annotations>`, `ADAPT_PROMPT` + `_views_prompt` (`VIEWS_PROMPT` and `DEFAULT_VIEW` when the source
    has notes in both views, `ONE_VIEW` for a single-view paper) + `PLACES_PROMPT`; recompiled TeX file
    (`_previous_compilation`) → `TEX_ADAPT_PROMPT` with `_tex_annotations_block`. `_annotations_block` gives the place
    (statement or proof, through `export._structure`), `surface="html|pdf"`, `zone="yes"`, an `<anchor>` (first `ANCHOR`
    = 200 characters of the paragraph) for a note pinned to the HTML, and `author=assistant` (`ASSISTANT_NOTE`).
    `_views(paper)` mirrors `texOnly` / `viewSwitch` of `reader.js`: change both together.
  - Deletions: `asks_deletion` (`_DELETING`: `_DELETE_VERB` near `_DELETE_OBJECT`, per sentence); `_deleting` also applies
    when the previous question of the thread was a deletion (answer to a clarifying question); a deletion bypasses
    `_versions_compared`. `_annotations_block(version, current=True)` → `<current_annotations>` with the real ids, without
    the `general` note, notes cut at `DELETE_NOTE` (600); `DELETE_PROMPT`. In the page, `parseProposals` accepts a `delete` item only for an exchange of task `ask` (the only
    one whose prompt carries `DELETE_PROMPT` and the numbers): anywhere else it could only come from the paper's text.
    Tests of pure functions of `static/js` without loading the module (it imports the DOM): `tests/jsfunction.py`.
  - Questions on another version: `version_asked` (named version, "latest version", or the previous one when asking what
    changed; a bare "v1" only with a version or change word, `_VERSION_WORD`); `_other_version` downloads it
    (`library.ensure_version`, then `ensure_ready`, bounded wait); `_versions_compared` adds `<other_version>` and
    `VERSION_PROMPT` before `TASK_PROMPTS["ask"]`; not for an adaptation or a deletion.
  Text put in these blocks goes through `_neutral` (`PREVIOUS_TAGS` and `GUARD_TAGS`): a new tag used in a block is added to
  `GUARD_TAGS` (and to `PREVIOUS_TAGS` if it is a tag of the quoted history). The key of a `\label` and a review written by an
  AI are neutralised too (`_plain_label` in `_tex_context`, `_review_block`). Page (`ai.js`): `parseProposals` reads the proposals (`quote`, `note`, `surface`, `zone`, `pin`, `page`,
  `replaces`, `delete`; a `pin` needs a note; `delete` accepts numeric ids). `applyProposals` applies deletions first
  (`DELETABLE`: highlight, note, block, bookmark; `deletedItem` / `itemOf` follow `annotator.restoredIds` after an undo),
  then the placements: `surfaceOf`, `nearOf` (replaced place, otherwise page hint); in the PDF through
  `reader.pdfQuote(quote, near)` (quote found in the pdf.js text layer: `proseOf` strips formulas, references, footnotes;
  `lettersOf`; pieces within 400 letters; whole-word edges; ambiguous without `near` → null) and `PdfNotes.placeOf` (rectangle
  around the passage's lines, one column, rotation-aware, 4 pt margin), `addZone` (with its image), `addPin`; in the HTML,
  `Annotator.addPin` (inside a statement or proof: added to its note; existing free note: appended after `---`, put back by
  `setFreeNote` on undo). A paper read in HTML loads a hidden PDF (`reader.createPdf` / `ensurePdf`); "Apply" stays disabled
  while `exchange.found` holds an undefined entry (`ai.proposals.locating`). `exchange.unchecked` keeps unticked boxes
  across redraws. Authorship: when the question matches `ABOUT_NOTES`, proposals with author "reader" stay the reader's
  (no "**Claude** —" header; `ASSISTANT_HEADER` avoids a double header). `replaces`: the new note replaces the old one
  (same kind only, never a region) and copies its reading state; Undo goes through `Annotator.restore`, which re-posts `uid`
  and `created_at` (`annotations.create` accepts an unused 32-hex `uid`).

- Notes tab (`annotations.js`): located annotations split into `inHtml` (document order, `compareDocumentPosition`) and
  `inPdf` (`placePdf`: page, then height in the page; `PdfNotes.readingHeights` measures heights in the pdf.js text layer
  and re-sorts the cards when the measurements arrive; stored `rect` / `top` before that). `singleView` (`reader.texOnly`):
  one group `all` without title. Groups `notesGroup`, card tools `cardTools` (checkbox and trash), state `selecting`,
  `selected`, `groupsOpen`; `refreshPanel` lets `reader.notesFollowView` open the current view's section without a full
  redraw. `remove(id)` handles statement notes (`deleteBlock`, reading state lost), bookmarks, highlights and notes, and keeps
  a copy for `restore` (region image as a data URL, `imageData`); `removeMany` serialises deletions and gathers those within
  6.5 s under one Undo; `quietly` holds `onChange` during a batch. A section's trash appears only when some of its cards are
  ticked. Bookmarks and the reading notes are not cards. `Reader.showElement` goes back to the HTML view
  (`setView("html", { save: false, restore: false })`) before showing an HTML mark. Export: `export.zone_images` → `.zip` with
  `images/` (`paper_export`, `library_zip`), route `GET /api/papers/{id}/notes` (the old `/notes.md` still answers).
  Tests: `tests/test_export.py`.

- Bibliography links (`reader.js`): `findLinks` builds the line ("Look up on arXiv" when the entry has no arXiv number, DOI,
  MathSciNet, zbMATH; citation stripped of DOI, URL and arXiv ids, cut to 500 code points); `addFindLinks` (after
  `linkArxivReferences`) appends `div.bib-links` at the **end** of each `.ltx_bibitem`, so highlight anchors counted from the
  start do not move; `.bib-links` is in the UI selector of `annotations.js`, removed from previews by `clean`, and
  `user-select: none`. `loadCrossLinks` puts "Open the paper" at the start of the line and removes "Look up on arXiv".
  `zbmathLink`: filled search, resolved once on `pointerenter` / `focus` through `POST /api/references/zbmath`, only a
  `https://zbmath.org/` address accepted. `lookUpArxiv`: in a browser, `window.open("", "_blank")` during the click
  (otherwise blocked), `opener` set to null, then sent to the route; closed on error; native apps: `openRoute(route, true,
  front)`. Route `#/search?terms=<JSON>` fully percent-encoded (also `! ' ( ) *`): the openTab regex of `macos/main.swift`
  and `windows/shell/MainForm.cs` only accepts `\?[A-Za-z0-9._%~=&-]*`; `search.presetTerms` checks the terms (≤ 6, known
  `FIELDS`), runs the search and replaces the address by `#/search` (Back does not rerun it). Paper header:
  `paperFindLinks` (arXiv id without `vN`: zbMATH finds nothing with it). Tests: `tests/test_localpdf.py`.

- Fonts unknown to arXiv's LaTeXML (`\EuScript`, `\scr`, `\EuFrak`, `\Bbb`…: left as `<mtext class="undefined">` before the
  letter): `math.undefinedFonts(root)` (called in `reader.finishLayout` before MathJax and in `typesetFragment`) replaces the
  command (with an optional invisible `<mo>`) and the letter, alone or base of `msub`/`msup`…, by the Unicode letter
  (`ALPHABETS`, with the letters encoded elsewhere) and arXiv's font class (`FONT_CLASS`), as for `\mathcal`; an unknown
  case stays visible. `MACROS` define `\EuScript`, `\scr`, `\EuFrak` for TeX. Links inside formula text (`\text{by
  \eqref{…}}`): when MathJax refuses a formula because of HTML in `<mtext>`, `convert()` retries on a clone through
  `linksInText` (links as `href` on mtext pieces, `ltx_font_*` kept, `\rule` as `<mspace>`, gives up on svg and img), and
  `restoreLinks` puts arXiv's `ltx_` classes back on the `<a>` and opens external links in the browser. Verified in WebKit
  and Chrome (section 4).
- Equations aligned left or right (`latexml.css`): `.ltx_eqn_left_padleft` and `.ltx_eqn_right_padright` are 1px wide, not
  0. With every fixed-width column at 0, WebKit divides by zero when sharing the shrink and the column jumps to 2^25 px
  (blank in place of the equation; theorem 2.3 of arXiv 2611.10775). Do not set them back to 0.
- Statement titles as plain text (`util.texToText`): accents become combining characters (`TEX_ACCENTS`), and `accented()`
  draws each one as a separate, absolutely positioned span (`SPACING_ACCENTS`, `.tex-accent`, `.tex-accent-mark`): math
  fonts misplace combining characters (the tilde of ∇̃ disappeared). `\left`/`\right`/`\big…` dropped, `\frac` → a/b.

- Semantic Scholar with a key: 1 request/s for the whole application. Every new request goes through
  `s2.paced(send, api_key)` (`app/s2.py`), which spaces out the requests made with a key regardless of the thread; without a key, nothing
  is shared: each module keeps its own pauses (`related.UNKEYED_PAUSE`, `citations`), as before the key.

- The embedded Python keeps hard-coded paths from the computer where the application was built (time zones,
  SSL certificates): on another Mac, they do not exist. The launcher (`macos/main.swift`) therefore sets
  `PYTHONTZPATH=/usr/share/zoneinfo` and `SSL_CERT_FILE` (the application's certificates), and `feed._zone` falls back on
  the macOS time zones. To simulate another Mac: `PYTHONTZPATH=/introuvable` with the application's Python.

- The Documents folder is synced by iCloud: build the application elsewhere
  (`~/Library/Caches/arXivReader-build`), otherwise the signature breaks; Python environment in `.venv.nosync`
  (section 8). A background job (launchd) cannot read
  Documents: it uses the code of the installed application, without writing bytecode (`PYTHONDONTWRITEBYTECODE`).
- A synthetic `DataTransfer` cannot set `dropEffect`: drags must be tested for real in the
  application.
- New setting: add it to the `allowed` list of `/api/settings` (and to `BOOLEAN_SETTINGS` or
  `_valid_setting`), otherwise it is not saved.
- The middleware requires `Content-Type: application/json` for every write except `DELETE` and the upload of a PDF (`POST /api/papers/pdf` as `application/pdf`): a `POST` with no body
  must send `{}`.
- arXiv titles contain TeX; Semantic Scholar duplicates are compared after `related._norm`
  (without TeX commands). Semantic Scholar sometimes damages titles: prefer arXiv's. Zotero, for its part, links
  items by arXiv identifier (`zotero.arxiv_of`, index `zotero_index`), never by title.
- The Zotero synchronisation merges three states (here, Zotero, the previous time): any change to
  `_sync` must keep the tests of `tests/test_zotero.py` (write refused, duplicates merged, deep
  subcollection, folder deleted during an addition, trash, mass removals). A folder deleted here, or in Zotero while
  the item still carries its subcollection, never takes its papers with it: they are put back at the root
  "arXivReader" (`rescued`), even after an interrupted pass.
- **No request from the page may wait for long** (WebKit may abandon a request that has gone about
  one minute without a response): an import always goes to the background (`POST /api/papers`, update
  `POST /api/papers/{id}/refresh` tracked through `…/importing`), a server-side wait is capped at 45 s (`ensure_ready(..., timeout=45)`, also in `ai._other_version`: the question to the AI is silent until the prompt is built)
  and the page asks again; the arXiv API has a patient second attempt (`arxiv.patient_get`,
  `search._fetch`); the daily feed comes from RSS, the rest (followed authors, journals, citations) in the background; the graph of related papers is
  built in the background (`POST /api/related`, tracked by `GET /api/related/status`, stopped by `POST /api/related/cancel`:
  checkpoints `related._step` and `related._wait` between two requests).
- One-click add (bookmark in the feed, in search, "Add", drop onto a folder): `library.quick_add` registers
  the paper immediately (`source = "pending"`, based on what the page knows about it) and imports it in the background;
  `library.ensure_ready` makes its opening wait (or redoes the import if it had failed). The background import keeps
  the place in the library as it is when the import ends (removed in the meantime: stays removed).
- Figures: the import keeps the original address of each image (`assets.json`); when a paper is opened,
  the missing ones are requested again (`library.repair_assets`, through these addresses, otherwise through a new import that
  may rename the files), and the reader follows the repair (`/api/papers/{id}/figures`) and then reloads
  the paper. An image that does not arrive leaves a line in `server.log`. A repair by new import (`_reimport_installed`)
  passes the installed source (`_import(…, same_source)`): if the new text comes from another source, nothing is replaced
  and the paper goes into `_repair_refused` (in memory: not retried before a restart); images missing from the new import
  are copied from the old `assets/`. An ar5iv paper is re-imported only if its installed version is the latest (ar5iv serves
  its own version); archived versions are never repaired this way.
- People's names serve as examples in the interface: do not put real names or real
  identifiers there ("Your name", "arxiv.org/a/…").
- **Daily copy and integrity check** (`app/safety.py`): `safety.prepare` runs in `db.init()` before anything opens the
  database; `main._startup_maintenance` makes the day's copy at startup (`safety.snapshot`, one file
  `backups/arxivreader-YYYY-MM-DD.db`, 7 kept, vector cache left out; `ARXIVREADER_NO_SNAPSHOT` for tests), then prunes the
  feed. The check opens the file normally (not read-only, so that SQLite replays a leftover journal) and only a proven
  corruption (`malformed`, `not a database`, `corrupt`) sets the file aside as `backups/corrupt-<date>.db` and puts the
  latest sound copy back (`recovery.json` says what happened, shown in Settings); a busy or unreadable database (rights,
  disk) raises instead and is never moved. A database whose `PRAGMA user_version` is above `safety.DB_VERSION` is refused
  (`NewerLibrary`), never downgraded: raise `DB_VERSION` when a release changes the schema in a way the previous code
  could not read. Every `sqlite3.connect` of the code is `connect(timeout=30)` (`db.connect`, `safety.BUSY_TIMEOUT`): a
  writer waits for another (the `VACUUM` of the pruning) instead of failing with "database is locked"; `feedprune.prune`
  opens the connection of its `VACUUM` by hand and must keep that timeout.
- **Feed pruning** (`app/feedprune.py`, at startup): `feed_items` older than `KEEP_DAYS` = 60 days are deleted, except
  papers in the library, with a status (read, 👍 / 👎), in the last recommendation (`s2_rank`), citing the reader's papers
  (`citing`); embedding vectors are stored as float32 bytes (`pack` / `unpack`, which still reads the old JSON text). The
  file is compacted with `VACUUM` once at least `VACUUM_FROM` = 1000 rows are gone. A pruned day is forgotten from
  `feed.days` (otherwise the page would show it as loaded and empty); a day that kept some papers is thinned: it is
  listed in the setting `feed.partialDays`; the day route (`main.py`) then fetches it again when the OAI-PMH is available
  (`feed.oai_available`), and `feed.py` updates the mark when a day is fetched whole. Anything new that stores feed rows must say whether pruning may delete them.
- **Review checked on demand**: `AIRequest.check` (`app/main.py`) is the id of a finished review (single or cross-review)
  whose critical and serious points the same assistant tries to refute (`ai.verify_review`; the checker is `ai.verifier`:
  a lighter model, `sonnet` for Claude, unless the reader chose another). It goes through `ai.build_prompt` like any other
  request, and rewrites the review: points come out confirmed, uncertain or withdrawn. It is not a mode of the review
  request but a separate action on a finished review (the first review stays visible). A review whose answer was cut
  (`cut`) or whose text was partly sent (`coverage`) is never taken as complete.
- **Cut answers and coverage**: `ai_exchanges.coverage` (JSON: total, sent, partial, pages) and `cut` record what the
  assistant read and whether its answer stopped on a limit; the answer being written is saved every few seconds with
  `status = 'running'` (`ai.stop_running` closes whatever is still `running` as `stopped` at startup, in `main.lifespan`; `GET /api/ai/running` tells the Mac shell
  what is in progress, to ask before quitting). Block tags added to a prompt go through `ai._neutral`.
- **Notes between computers**: a deleted annotation leaves a row in `annotation_deletions` (uid, paper, date), carried by
  the export so that an import does not bring it back (the latest change wins; forgotten after about a year); `db._one_row_per_statement` merges
  duplicates once and creates the unique partial indexes `annotations_single` and `annotations_general`: a new kind of
  annotation that must exist once per statement has to be excluded from, or added to, those indexes.
- **Reading position** (`papers.position`, 200 characters): `id`, `id@pixels` or `pdf:page:top`
  (`position.js`). An old app reads the PDF form as an unknown id and ignores it; keep the forms prefix-compatible. The
  history of jumps (`trail.js`) stores anchors (element and offset, or PDF page and height), never raw pixels, since the layout changes with the
  text size. `j` / `k` in the PDF of an arXiv paper stop only on statements whose page is known.
- **BibTeX** (`app/bibtex.py`): a wrong entry is worse than none. `@article` only when the journal is known, no volume,
  pages or year invented, a name split into "Last, First" only when the split is certain (a capitalised "Van" is not taken for a particle: "Nguyen Van Minh" stays whole; "De", "Von"… are); the button on a bibliography
  entry is shown only when `crossrefs.match_by` returns `id` or `doi`, never `title`. The WebKit clipboard takes a write
  only after a gesture: `copyBibtex` passes the answer as a promise to `ClipboardItem`.
- **Subject-class identifiers** (`math.DG/0211159`): normalised without the class (`math/0211159`) on entry
  (`arxiv.strip_subject_class`, `crossrefs._arxiv`); `library.stored_key` finds an older record stored with the class.
- **DataCite DOI of an arXiv paper** (`10.48550/arXiv.2401.12345`, what Zotero, Crossref and bibliographies carry since 2022): read as
  `arXiv:2401.12345` (`arxiv.DATACITE`, in `parse_arxiv_id` and `extract_ids`; `ID_RE` itself is unchanged).
- **LaTeX 2.09 sources** (`\documentstyle[12pt]{article}`, most arXiv papers before about 2000): `texsource.DOCUMENTCLASS` accepts
  `\documentstyle` as `\documentclass` (main file, layout analysis, import of a local `.tex` in `texfile.main_file`, single gzipped file
  in `latexml._extract`).
- **Pages and menus** (`app.js`, `check.js`, `bookmarks.js`): every `render…` of a route that waits for the network checks
  that its page is still the current one before drawing (`isCurrent` = `token === routeToken`; `renderCheck` too);
  `contextMenuAt` gives the focus back to the element that opened it when it closes by Escape or by a choice (never after
  a click elsewhere: that click chooses the new focus).
- **Colours**: text must reach 4.5:1 on its real background (tints included), non-text 3:1 (`tests/test_contrast.py`).
  Light `--k-conjecture` and `--k-problem` are `#a35700` (5.36:1 on white, 4.67:1 on their own 10 % tint; `#c26a00`
  gave 3.48:1 on the pressed `.kind-filter`). Known and accepted exceptions: `--border-strong` (about 2:1) on field
  borders, and the dark `--accent` on `--surface-hover` (4.17:1) on hover states: changing them is a visual change to
  make with the owner.
- **Cached cross-reference index**: `crossrefs.nodes_of` reads another paper's `structure.json` as stored, cached by
  (date, size); it never re-analyses, which would rewrite every other paper's structure when one is opened.
- **Sanitiser** (`app/sanitize.py`): every `PreformattedString` is removed (comments, but also `<![CDATA[…]]>`, which
  HTML5 reads as a comment closed at the first `>`), and the `style` filters (`UNSAFE_STYLE`, `PINNED`) look at the
  style as the browser reads it (`_normal_css`: CSS comments removed, escapes such as `fi\78 ed` resolved). A
  `content.html` stored before these rules gets the same treatment when read (`clean_stored`, in the `/content` route;
  a page without `<!`, `<?` or an escaped style is returned as is, without parsing).
  It also drops what belongs to the interface (ids `view`, `toast`, `settings-dialog`, `settings-button`, `setting-*`;
  classes `cross-open`, `cross-badge`, `ai-ref`; hrefs beginning with `#/`): an article must never address the
  application's own elements or routes. `toast()` and `openSettingsDialog()` look their element up as `body > #id`,
  for an article stored before this rule.
- **Error codes of an AI provider** (`ProviderError.code`, kept by `AIError` and by the cross-review of `ai.py`): `login`,
  `missing`, `limit`, `reasoning`, `offline` (local server not running: "start Ollama or LM Studio") and `network`
  (Gemini unreachable: only the network is blamed). Each code has a key in `ERROR_KEYS` (`ai.js`) and a text in both
  languages (`ai.error.*`); a test checks it.

## 10. Logs and diagnostics

- Server: `server.log` in the library (`~/Library/Application Support/arXivReader`). No logging configuration: only `WARNING` and
  above reach it (`log.info` is invisible). A background step that fails and is retried later (`feed._complete_refresh`,
  `feed._quietly`, `main._quietly`, `journals.fetch_later`) is swallowed but leaves one `log.warning` line (type and message,
  never a key).
- Services check: `check.json` (same folder) and `~/Library/Logs/arXivReader-check.log`.
- Zotero synchronisation: last state in the settings (`zoteroLastSync`, `zoteroLastError`,
  `zoteroLastErrorCode`, `zoteroReport`), visible in Settings > Zotero.
