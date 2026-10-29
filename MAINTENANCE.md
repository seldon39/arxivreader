# Maintenance d'arXivReader — instructions pour une IA

Ce fichier s'adresse à un assistant (Claude Code, Codex…) chargé de maintenir ou de faire évoluer arXivReader :
ce qu'il faut savoir avant de toucher au code, comment vérifier, comment publier, et quoi faire quand un service
extérieur change. Les README (`README.md`, `README.fr.md`) décrivent l'application pour ses utilisateurs ; ce
fichier, son entretien. Le tenir à jour quand une règle ou un piège change.

## 1. En deux mots

Lecteur d'articles arXiv (macOS, Windows ; Linux depuis le code source), pensé pour les mathématiques : serveur Python (FastAPI + SQLite) sur
127.0.0.1, page en JavaScript sans compilation (`static/`), application Mac en Swift (`macos/main.swift`, une
fenêtre WebKit par onglet) qui embarque son propre Python. Données de l'utilisateur dans
`~/Library/Application Support/arXivReader` (base `arxivreader.db`, dossier `papers/`). Dépôt public
(github.com/seldon39/arxivreader, licence MIT). Code et commentaires en français ; interface en français et en
anglais.

## 2. Règles à ne jamais enfreindre

- **Local seulement.** Le serveur écoute 127.0.0.1 ; le middleware `local_only` (`app/main.py`) refuse les hôtes
  étrangers, les requêtes d'autres sites et les écritures sans JSON. Ne pas l'affaiblir. Les seuls appels
  extérieurs sont ceux de la section 6.
- **L'IA sur un article n'a ni outils ni réseau** et ne voit que l'article : ne pas lui en donner.
- **Clés et mots de passe** (Semantic Scholar, Gemini, Zotero, comptes Claude/Codex) : c'est l'utilisateur qui les
  crée et les colle dans les réglages. Ne jamais en saisir, en écrire dans le code, les journaux ou les messages
  d'erreur.
- **Ne jamais toucher à l'application ni à la bibliothèque de l'utilisateur pendant les essais** : pas d'arrêt de
  son application ou de son serveur, pas d'écriture dans sa bibliothèque. Essais sur le port 8711 avec une copie
  (section 4).
- **Zotero : aucun élément n'est jamais supprimé ni mis à la corbeille** ; seules les collections « arXivReader »
  (et leurs sous-collections) sont modifiées, les autres collections d'un élément sont gardées.
- **Installer ou publier seulement avec l'accord de l'utilisateur**, et seulement quand l'application est
  vraiment quittée (`pgrep -f /Applications/arXivReader.app` ne renvoie rien : elle peut rester ouverte sans
  fenêtre). Publier est public (GitHub) : toujours demander.
- **Pas de téléchargement** (nouvelle bibliothèque, mise à jour de MathJax…) sans accord explicite.
- Commits : messages en anglais, terminés par la ligne `Co-Authored-By:` de l'assistant ; un commit par
  changement cohérent ; committer seulement quand l'utilisateur le veut.

## 3. Où est quoi

| Domaine | Serveur (`app/`) | Page (`static/js/`) |
|---|---|---|
| Import, HTML nettoyé, PDF | `library.py`, `arxiv.py`, `sanitize.py` | `reader.js`, `pdfview.js`, `pdfsync.js` |
| Structure (énoncés, preuves, dépendances) | `structure.py` (HTML LaTeXML), `texstructure.py`, `texsource.py` (source TeX) | `reader.js`, `graph.js`, `texview.js` |
| Notes, surlignages, état de lecture, export | `annotations.py`, `export.py`, `notesync.py` (export signé, import d'un ordinateur à l'autre) | `annotations.js`, `markdown.js`, `bookmarks.js` (marque-pages, menu du clic droit), `pdfnotes.js` (passages, zones et notes dans le PDF ; passages retrouvés après recompilation) |
| PDF et fichiers TeX de l'ordinateur | `localpdf.py` (PDF importés : texte, titre, version arXiv), `texfile.py` (fichiers TeX : compilation, bibliographie, vérifications, identifiants tirés des `\label`, tâches en arrière-plan) | `library.js` (import), `texfile.js` (fenêtre d'import, vérifications du lecteur), `reader.js` (Recompiler, livres : énoncés et graphe par chapitre) |
| Version HTML expérimentale (LaTeXML) | `latexml.py` (conversion à la demande, dossier `html/` de l'article, identifiants alignés sur la structure de la source) | `latexml.js` (bouton « HTML β », avertissement, attente), `reader.js` |
| Bibliothèque, dossiers, sélection | `library.py`, `folders.py` | `library.js`, `selection.js` |
| Feed, profil, pertinence, alertes, citations | `feed.py`, `profile.py`, `relevance.py` (`same_person`, `follows` : homonymes des auteurs suivis), `alerts.py`, `citations.py`, `journals.py` | `feed.js` (👎 → « pas cette personne »), `profile.js` (catégories gardées pour un auteur) |
| Recherche arXiv | `search.py` | `search.js` |
| Papiers liés (graphe) | `related.py` | `related.js` |
| IA | `ai.py` (Claude, SDK Agent ; scans : pages jointes en images ; livres trop longs : début et fenêtre autour de la question), `providers.py` (Codex, Gemini, local) | `ai.js` (onglet en conversation : sous-onglets Questions / Résumé / Relecture d'après la racine de chaque échange, `viewOf` ; une question sur un résumé ou une relecture a pour parent ce document, seul échange précédent transmis par `ai.py` ; positions de lecture `positions`, boutons ↑ ↓ dans la marge du fil ; renvois des réponses `buildRefs` : énoncés, équations,
sections d'après le numéro du titre (`toc`), références entre crochets ou auteur-année (`bibliography`), sauf l'énoncé d'un
article cité, « Theorem 3.1 of [12] », « [12, Theorem 3.1] » ; aperçu au survol par `reader.previewFor`), `aiproviders.js`, `reader.js` (`contextPages` : pages d'un scan rendues par pdf.js) |
| Zotero | `zotero.py` | `zotero.js`, `settings.js` |
| Premier lancement | `onboarding.py` | `onboarding.js` |
| Mises à jour, vérification des services | `updates.py`, `check.py` | `updates.js` |
| Formules, construction du DOM | `feed.tex_text` (TeX des titres et résumés) | `math.js` (MathJax), `amsfix.js`, `util.js` (`h`, `withMath`, `tidyTitle`) |
| Réglages | `main.py` (liste `allowed` et `_valid_setting` de `/api/settings`) | `settings.js` |
| Textes de l'interface | — | `i18n.js` (blocs FR et EN : mêmes clés) |
| Application Mac | — | `macos/main.swift` (onglets, glisser sur un onglet, téléchargements, essais, sélecteurs natifs de dossier et de fichiers : `chooseFolder`, `chooseFiles`), `macos/build.sh`, `macos/release.sh` |
| Application Windows | `windows/launcher.py` (serveur sans fenêtre, repli Edge) | `windows/shell/` (fenêtre C# : `MainForm.cs`, `TabStrip.cs`, `Program.cs`), `windows/smoke.ps1`, `.github/workflows/windows.yml` |

Base : `app/db.py` (`SCHEMA` en `CREATE TABLE IF NOT EXISTS`, colonnes ajoutées par `ALTER TABLE` après
`PRAGMA table_info` dans `init()`) ; modules qui ont leurs tables : `related.py`, `zotero.py`, `journals.py`
(`_ensure`).

Page : `h(tag, attrs, …)` ajoute les enfants comme texte ; `html:` est réservé aux icônes internes (`icons`). Tout
texte venu d'arXiv, de Semantic Scholar, de Zotero ou d'une IA passe par du texte ou `withMath`, jamais par
`html:` ni `innerHTML` (la politique CSP interdit de toute façon les scripts de l'article).

## 4. Boucle de travail

- **Tests** (sans réseau : tout service extérieur y est simulé ; un test qui appelle le vrai réseau est un défaut) :
  `.venv/bin/python -m unittest discover -s tests`. Pour s'assurer qu'aucun test ne sort, les relancer avec
  `HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9` (exception connue : les tests Gemini utilisent un
  faux serveur local que ce proxy intercepte).
- **Serveur d'essai**, jamais celui de l'utilisateur, sur une copie **sans ses clés** (sinon une action sur la
  copie, ou « Synchroniser maintenant », écrirait dans son vrai Zotero ou userait ses quotas) :
  ```bash
  LIB=$(mktemp -d)   # dossier privé (0700) : la copie contient ses données
  sqlite3 -readonly ~/Library/Application\ Support/arXivReader/arxivreader.db ".backup $LIB/arxivreader.db"
  sqlite3 "$LIB/arxivreader.db" "DELETE FROM settings WHERE key LIKE 'zotero%' OR key IN ('s2ApiKey', 'geminiApiKey')"
  ARXIVREADER_LIBRARY=$LIB ARXIVREADER_PORT=8711 ARXIVREADER_NO_ALERTS=1 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8711
  ```
  `ARXIVREADER_NO_ALERTS=1` est **obligatoire pour tout essai** (serveur comme application) : il coupe les
  boucles de fond (auteurs suivis et citations, `alerts.loop` ; synchronisation Zotero, `zotero.loop`) ; ce que la
  page déclenche elle-même (revues sur Semantic Scholar, vérification des mises à jour) tourne quand même.
  Ajouter `HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9` si l'essai n'a pas besoin du réseau. La
  copie n'a pas `papers/` : un article ouvert est retéléchargé d'arXiv ; pour lire un article sans réseau, copier
  aussi son dossier (`papers/<id>`). Bibliothèque vide (installation neuve, assistant de premier lancement) : un
  `mktemp -d` vide. Effacer la copie après.
- **Vérifier dans le navigateur** (la page recharge ses fichiers : forcer `location.reload()`), en clair et en
  sombre, à plusieurs largeurs (une colonne sous 900 px). L'utilisateur lit dans WebKit (Safari) : c'est le moteur
  qui compte.
- **Essais dans l'application Mac** (construite par `macos/build.sh` dans `~/Library/Caches/arXivReader-build`,
  sans l'installer) : toujours `ARXIVREADER_NO_ALERTS=1` et `ARXIVREADER_LIBRARY` (copie sans clés, comme
  ci-dessus), `ARXIVREADER_START=#/paper/…,#/feed` (pages ouvertes, la
  suivante en onglet), `ARXIVREADER_SNAPSHOT=fichier.png` (image de la page), `ARXIVREADER_WINDOWSHOT=fichier.png`
  (fenêtre entière et relevé dans `fichier.png.txt`), `ARXIVREADER_EVAL` (JavaScript exécuté au chargement),
  `ARXIVREADER_SNAPSHOT_DELAY`. Ne pas prendre la main sur l'écran de l'utilisateur.
- **Avant de rendre la main** : tests verts, README FR et EN à jour si le comportement visible change, clés
  `i18n.js` présentes dans les deux langues (pluriels : `.one` et `.other`).

## 5. Publier une version

1. Augmenter `VERSION` dans `app/version.py` (nouvelle fonction : mineure ; correction : correctif), committer.
2. Accord de l'utilisateur ; application quittée (`pgrep -f /Applications/arXivReader.app` vide).
3. Notes de version en anglais dans un fichier **hors du dépôt** (une ligne par changement visible), par exemple
   `$(mktemp -d)/notes.md` : dans le dépôt, le fichier le rendrait « modifié » et le script refuserait.
4. `macos/release.sh /chemin/absolu/notes.md` : il exige la branche `main`, un dépôt sans modification non
   committée, `gh` connecté à GitHub et une étiquette `vX.Y.Z` encore libre ; puis tests, construction (refusée
   si `.venv` diffère de `requirements.lock`), étiquette, poussée, publication du `.dmg` sur GitHub.
5. Installer : `rm -rf /Applications/arXivReader.app && ditto ~/Library/Caches/arXivReader-build/arXivReader.app /Applications/arXivReader.app`,
   puis vérifier `defaults read /Applications/arXivReader.app/Contents/Info.plist CFBundleShortVersionString` et
   `codesign --verify --deep --strict /Applications/arXivReader.app`.

L'application n'a qu'une signature ad hoc (pas de notarisation) : sur un autre Mac, ouverture par clic droit >
Ouvrir.

### Version Windows

Construite par GitHub Actions (`.github/workflows/windows.yml`, machine `windows-latest`) **à chaque poussée sur
`main`** : installation de `requirements.lock` et de `windows/build-requirements.txt` (PyInstaller, `tzdata` pour les
fuseaux horaires, versions figées), **tous les tests** (ceux propres au Mac sont sautés), serveur
`server\arXivReader-server.exe` (PyInstaller `--onedir`), fenêtre `arXivReader.exe` (`dotnet build`, SDK WebView2 à
version figée), zip, puis `windows/smoke.ps1` : serveur sur une bibliothèque vide, type des modules JavaScript, import
de 1603.04246, captures par Edge sans fenêtre, puis la fenêtre de l'application sur deux onglets (titre vérifié),
serveur tué (la fenêtre doit rester et proposer de le relancer), et aucun serveur restant une fois la fenêtre fermée.
Captures dans `shots/` (artefact de l'exécution, gardé 14 jours, avec le zip).

Publier : une seule version GitHub par numéro. `macos/release.sh` crée l'étiquette `vX.Y.Z` et la version avec le
`.dmg` ; l'étiquette déclenche le workflow Windows, qui vérifie que X.Y.Z est celui de `app/version.py`, construit et
essaie l'application (et l'installateur Inno Setup, `windows/installer.iss` : installation silencieuse, raccourci,
désinstallation), puis **joint `arXivReader-setup.exe` et `arXivReader-windows.zip` à la version `vX.Y.Z`** (étape à part, seule à pouvoir
écrire sur le dépôt ; relancée, elle remplace le zip). Les deux applications lisent « latest » : le Mac y prend le
`.dmg`, Windows l'installateur s'il a été installé ainsi (`unins000.exe` à côté), sinon le `.zip` (pas encore joint : rien d'annoncé, la vérification suivante le verra). Les anciennes
préversions `windows-v1.9.15`… `windows-v1.9.21` et `windows-latest` restent de l'histoire : les versions Windows
1.9.21 et antérieures ne voient pas les versions unifiées, il faut les mettre à jour une fois à la main.

- Fenêtre : `windows/shell/` (C#, .NET Framework 4.8 inclus dans Windows, WebView2), l'équivalent de
  `macos/main.swift` : une page WebView2 par onglet (historique propre à chacune), barre d'onglets dessinée façon
  macOS (`TabStrip.cs`), liens extérieurs dans le navigateur par défaut, `window.chrome.webview.postMessage` pour
  ouvrir un onglet (`nativeBridge` dans `static/js/util.js`), taille gardée dans `window.txt`. Elle lance
  `server\arXivReader-server.exe` (PyInstaller, `windows/launcher.py` avec `ARXIVREADER_NO_BROWSER=1`) dans un
  « job » Windows qui l'arrête avec elle, et lit son port dans `server.port`. Lancé seul, `launcher.py` ouvre encore
  une fenêtre Edge (repli).
- Essai : `windows/smoke.ps1` lance aussi la fenêtre sur deux onglets et capture l'écran (`shots/window.png`).
- Différences côté serveur, toutes sous `sys.platform == "win32"` : notification par PowerShell (`alerts.py`),
  connexion des assistants dans `cmd.exe` (`providers.py`), `claude.exe` du SDK (`ai.py`), le `.zip` de la dernière version
  (`updates.py`). Types MIME fixés dans `main.py` (le registre de Windows sert parfois `.js` en `text/plain`).
- Interface : `MAC` / `WINDOWS` dans `static/js/i18n.js` ; hors Mac, ⌘ ⌥ ⇧ deviennent Ctrl Alt Maj à l'affichage.
- Pas encore sous Windows : « Recopier une bibliothèque… » (menu de l'application Mac), signature du `.exe`.

## 6. Services extérieurs : ce qui peut changer, où regarder

`./arxivreader check` appelle pour de vrai ceux que liste `CHECKS` (`app/check.py` : à compléter si l'application
se met à dépendre d'un nouveau service) et vérifie la forme de la réponse (✓ bon, ✗ changé ou en panne, – non
vérifié). Lancé à la main, il lit les réglages de la bibliothèque de l'utilisateur (clés) et y écrit `check.json`
(sans toucher au repère macOS/WebKit de la tâche hebdomadaire) ; `ARXIVREADER_LIBRARY=<copie>` pour l'éviter. La
tâche hebdomadaire (`./arxivreader check --weekly`, launchd
`org.arxivreader.check`, lundi 10 h) le fait avec le Python de l'application installée et notifie en cas de
problème ; journal `~/Library/Logs/arXivReader-check.log`, résultat `check.json` dans la bibliothèque.

| Service | Utilisé par | Ce qui casse d'habitude | Repli existant |
|---|---|---|---|
| API arXiv `export.arxiv.org/api/query` | `arxiv.py`, `search.py`, `feed.py` (jours passés), `profile.py`, `onboarding.py` | lenteur, 429/503, champs Atom | une requête toutes les 3 s (`arxiv._paced`, `search._wait_turn`), `with_retry` |
| Pages HTML LaTeXML `arxiv.org/html/…` | `library.py`, `sanitize.py`, `structure.py` | classes `ltx_*`, balisage des énoncés et preuves, chemins des images (deux formes : `x1.png` ou `<id>v<n>/fig.png`, `sanitize._relative`) | ar5iv, sinon PDF + structure tirée du TeX (`texstructure.py`) |
| RSS `rss.arxiv.org` | `feed.py` (`parse_rss`) | format des annonces (types new/cross/replace) | OAI-PMH, puis API de recherche |
| OAI-PMH `oaipmh.arxiv.org/oai` | `feed.py` (jours passés) | lent au-delà d'une semaine | API de recherche (fenêtre de dépôt 14 h New York) |
| Flux d'auteur `arxiv.org/a/<id>.atom2` | `onboarding.py` | format, ORCID non lié | recherche par nom |
| Semantic Scholar (graph, batch, recommendations) | `related.py`, `relevance.py`, `journals.py`, `citations.py`, `feed.py`, `onboarding.py`, `localpdf.py` (version arXiv d'un PDF importé) | quota partagé sans clé (429), champs | attente et nouvel essai ; fonctions dégradées sans lui |
| Zotero web API v3 | `zotero.py` | en-têtes (`Total-Results`, `Last-Modified-Version`, `Backoff`), écritures multiples (`successful`/`failed`), `since`/`/deleted` | garde-fous : écriture refusée réessayée, retraits massifs en attente |
| Claude (SDK Agent et son CLI) | `ai.py` | options du SDK, liste des modèles (`get_server_info`), connexion | message de connexion dans les réglages |
| TeX de l'utilisateur (latexmk, pdflatex/xelatex, BibTeX/Biber, kpsewhich) | `texfile.py` (fichiers TeX importés) | latexmk ancien (BibTeX ne peut pas écrire dans `-outdir` : `$bibtex_fudge=1`), MiKTeX sans Perl (latexmk ne démarre pas), programmes hors du PATH d'une application lancée du Finder (`TEX_DIRS`) | compilation à la main (LaTeX, BibTeX ou Biber, LaTeX ×2) ; sans TeX, PDF compilé à côté du fichier |
| LaTeXML de l'utilisateur (`latexmlc`) | `latexml.py` (HTML β) | options de `latexmlc`, lenteur (pgfplots, gros documents), installation sous Windows (Perl) | figures pgfplots remplacées par une mention ; 10 minutes au plus, puis échec retenu (« Réessayer ») ; le PDF reste lisible |
| Codex CLI, API Gemini, modèle local | `providers.py` | options du CLI (`-m`, `model_reasoning_effort`, `--image=` après la question `-`), `~/.codex/models_cache.json`, `thinkingLevel` de Gemini | — (la CLI Gemini n'accepte plus les comptes Google gratuits : API par clé) |
| GitHub releases | `updates.py` | — | silencieux |

**Démarche quand un service a changé** : reproduire avec une requête réelle minimale (comme `check.py`) ; corriger
le module concerné ; mettre à jour la simulation des tests pour qu'elle ressemble à la nouvelle réponse (les faux
services doivent être *aussi stricts* que les vrais : un faux trop permissif a déjà caché un défaut) ; ajouter un
test du cas ; vérifier que le repli fonctionne si le service tombe.

**Caches à invalider** quand leur calcul change : `STRUCTURE_VERSION` (`structure.py`, ré-analyse des articles
importés), `FORMAT` (`related.py`, graphes gardés quinze jours), index Zotero (`zoteroIndexVersion`, remis à zéro
par `_forget_all`).

## 7. WebKit et macOS

WebKit change avec macOS (surtout la version majeure, en septembre). `check.py` signale le changement ; revoir à la
main, dans l'application installée :
- (liste à tenir identique à `WEBKIT_POINTS` dans `app/check.py`, le texte de la notification)
- glisser un article d'une fenêtre à l'autre, sur un onglet (ouverture après 0,6 s), sur « Bibliothèque » et
  « Papiers liés » ; types de glissement : `application/x-arxivreader-paper` et `text/uri-list`
  (`util.js`, `selection.js`, `TabWindow` dans `main.swift`, qui repère les onglets par les vues `NSTabButton` de
  la barre du Mac) ;
- couleurs SVG (le graphe des papiers liés met les couleurs en `style`, car WebKit refuse `var()` dans
  l'attribut `fill`), `color-mix`, `<dialog>` et `inert`, `ResizeObserver` ;
- le lecteur PDF (pdf.js figé dans `static/vendor/pdfjs`) et les formules (MathJax figé dans `static/vendor/mathjax`) ;
  le visualiseur de pdf.js interdit les styles écrits par script (CSP `style-src 'self'`) : nos ajouts passent par
  `static/css/pdfjs-extra.css` (boutons masqués, surlignages) ; les surlignages du PDF (`pdfnotes.js`) se repèrent
  dans la couche de texte (`.textLayer`, événement `textlayerrendered`) : à revérifier à chaque mise à jour de pdf.js ;
  la sélection est dessinée par `pdfnotes.js` (`enableSelectionRendering: false` dans `pdfview.js`) ; les zones
  (⌥ + glisser, bouton `#arxZoneButton` ajouté à `#toolbarViewerMiddle`) sont rendues en image par `page.render` ;
  images gardées dans `papers/<id>/zones/` (notes) et `papers/<id>/ai-images/` (questions), envoyées seulement aux
  assistants de `ai.IMAGE_PROVIDERS` (Claude : message à blocs image + texte du SDK ; Gemini : `inline_data`) ;
- notes exportées : bloc de données signé en fin de fichier (`notesync.py`, Ed25519 de `cryptography`), clé de
  l'installation dans `notes-signing.key` (dossier de la bibliothèque, jamais exportée), appareils acceptés dans
  `notes-trusted.json` ; tout changement du contenu du bloc passe par `FORMAT` (les anciens fichiers restent lisibles) ;
- le menu du clic droit du navigateur est supprimé (`app.js`) sauf sur une sélection, un champ, un lien ou une image ;
  le clic droit de l'article (`annotations.js`) et du PDF (`pdfnotes.js`) propose les marque-pages (`bookmarks.js`) ;
- les téléchargements (export des notes : dossier Téléchargements, `WKDownloadDelegate`).
Les captures (`ARXIVREADER_SNAPSHOT`) avant et après la mise à jour aident à comparer.

## 8. Dépendances Python

- `requirements.txt` : paquets voulus ; `requirements.lock` : versions exactes, testées, que `.venv` installe et
  que l'application embarque (`build.sh` copie `.venv`, et refuse de construire s'il diffère du verrou).
- Mettre à jour, exprès et un paquet à la fois : `.venv/bin/pip install -U <paquet>`, tests, essai, puis
  `./arxivreader lock` et committer `requirements.lock`. Lire les notes de version du paquet avant.
- Changer de version de Python : `.venv` refait avec la nouvelle version ; `build.sh` crée le Python embarqué
  depuis le cache de conda-forge (`miniforge`, `conda create --offline`) à la même version : il faut d'abord la
  télécharger dans ce cache (`conda create` sans `--offline`), avec l'accord de l'utilisateur.

## 9. Pièges connus

- Version HTML par LaTeXML (`latexml.py`) : rangée dans `papers/<id>/html/`, jamais dans `content.html` à la racine
  (sa présence fait d'un article un article d'arXiv lu en HTML : analyse, texte pour l'assistant). Les identifiants des
  énoncés (rapprochés dans l'ordre du document, type par type), des sections, de la bibliographie et des équations
  numérotées de la sortie sont remplacés par ceux de la structure de la source (`align_ids`) : notes et pages du PDF
  (`structure.json`, source « tex ») servent aux deux vues ; emplacement de preuve indiqué à la main en vue HTML :
  `latexml.reanalyze`. Fichier principal d'une source d'arXiv : `texsource.choose_main` (00README.json, sinon le
  plus long), le même pour l'analyse et pour LaTeXML. Conversion et compilation d'un même document : l'une à la fois
  (409). Défauts de LaTeXML 0.8.8 (Homebrew) contournés, que corrige la version de développement d'arXiv : dessins sans
  `viewBox` (`add_viewboxes`), paquets KOMA sans équivalent qui vident la bibliographie (`LAYOUT_ONLY`, paquets vides
  écrits dans le dossier de la conversion), chemins de recherche avec accents (« Thèse » : .bib et images
  introuvables) remplacés par des liens symboliques au nom simple (`_ascii`), macros de l'auteur développées dans la
  copie du `.bib` (LaTeXML le lit sans le préambule : `_expand_macros`), étiquettes des dessins TikZ sans hauteur ni
  profondeur et matrices (diagrammes) mal posées (`FIGURE_LABELS`, chargé par `--preload` : mêmes mesures que la
  sortie d'arXiv, matrices marquées `ltx_tikzmatrix_base` que `figures.js` ne corrige pas), hachures dont le motif
  est perdu (`fix_patterns`), ligne de texte seul d'un nœud devenue un `<text>` SVG (enveloppée, même module),
  renvois des dessins à largeur fixe (`_figure_refs` : numéro du `.aux`, lien gardé par `\arxivreaderref`).
  Figures PDF (Image::Magick absent : `ltx_missing_image`) converties en PNG avant LaTeXML (`_raster_figures` : sips,
  sinon Ghostscript de TeX ; largeur de la figure d'origine ; vrais PDF seulement (`%PDF-` : pas de PostScript pour
  Ghostscript), dans le dossier du document, 2400 px et 180 s au plus en tout, `page=` respecté), images réduites à quelques pixels par une longueur
  inconnue de LaTeXML (`fix_tiny_images`). Bibliographie vide (le `.bib` relu avec le préambule : pdfx, CJK… arrêtent
  LaTeXML) : seconde conversion d'après un `.bbl` (`_lost_bibliography`, `_bbl` : compilation ou BibTeX sur les
  citations ; `.bbl` biblatex changé en thebibliography, `_biblatex_bibliography`).
  Reste : lignes d'un nœud à plusieurs lignes un peu serrées (LaTeXML les empile sans interligne). Sous-figures
  (`ltx_flex_figure`) côte à côte : `latexml.css`. Taille des figures (dessins, images) rapportée au texte
  (`figures.sizeToText` : largeur LaTeXML à 100 dpi convertie en em d'après le corps du document, 10pt par défaut).
  Hachures des pages d'arXiv (LaTeXML de développement : le `<symbol>` de la tuile enferme la suite du dessin, alors
  invisible ; surface remplie en noir, juste avant ou juste après un groupe à motif sans tracé, ou après une
  redéfinition du motif seule dans un `<g>`) : réparées au nettoyage (`sanitize._repair_patterns`, avant le
  filtrage). Vérifié sur 6 articles d'arXiv trouvés en parcourant 900 pages HTML récentes. Bibliographie : le `.bib` quand tous
  sont trouvés (un `.bbl` amsrefs lui
  échappe), sinon le `.bbl` (`nobibtex`). Dépendances implicites (« the above lemma », phrase de justification,
  équations du texte courant, corollaire qui suit un énoncé) : `structure.py` et `texstructure.implicit_dependencies`.
  Étiquettes de listes inconnues de LaTeXML (« (enumi) », ex. `\greek*` de moreenum) : refaites par `relabelLists`
  (amsfix.js) d'après la source (`texsource._label_format`), renvois et infobulles compris. Texte non imprimé :
  `strip_hidden` retire aussi l'argument d'une macro vide (`\newcommand{\comment}[1]{}`). LaTeXML est lancé dans le dossier du document
  sur un chemin relatif (les espaces dans les chemins le gênent), sortie dans un dossier temporaire ; bibliographie par
  le `.bbl` de la compilation (`nobibtex`). Faite pour un état de la source (`state.json` : `compiledAt` ou version
  arXiv, `STRUCTURE_VERSION`) : sinon « stale », refaite au clic.

- Profil joint à l'IA (`ai.reader_profile`, dans la consigne système de chaque demande, toutes tâches) : catégories du
  feed, mots-clés, articles de l'utilisateur (titre, année, arXiv, début du résumé : `PROFILE_ABSTRACT`), articles et
  livres de référence, auteurs suivis (au plus `PROFILE_ITEMS` par sorte), texte libre `aiProfile` des réglages
  (`PROFILE_TEXT` caractères, `<in_their_words>`). Tout est échappé (un titre ne ferme pas le bloc). Aperçu exact :
  `GET /api/ai/profile` (Réglages → Assistant IA). Vide : aucun bloc, consigne neutre (« The reader is a researcher »).
  Essai de l'interface dans une copie de l'app : effacer ses `__pycache__` (Python embarqué : bytecode non revérifié).
  Renvois des réponses : mot-clé de section sans casse mais numéro en majuscule (« la section a été… » n'est pas l'annexe
  A), lettre seule de la table des matières seulement pour une annexe ou « A.1 » ; étiquette entre crochets entière
  d'abord (« [EGA-IV] »), puis morceaux séparés par « , », « ; » (hors entité) ou « – ».

- Fichiers glissés depuis l'ordinateur (barre d'import, liste de la bibliothèque : `library.importDropped`) : chemins
  demandés à la fenêtre (`util.droppedFilePaths`) — Mac : `DropWebView` (main.swift) retient les chemins PDF/TeX du
  glisser (draggingEntered, performDragOperation), rendus une fois (`droppedFiles` → `arxivreader:dropped`) ; Windows :
  `postMessageWithAdditionalObjects` (fichiers → `CoreWebView2File.Path`, MainForm.cs). Puis comme le bouton
  (`importChosen` : PDF par chemin, TeX compilé dans son dossier, `.bib` et `\input` relatifs à lui). Navigateur :
  contenu des PDF seulement.

- Dépendances (à chaque audit) : `.venv/bin/pip list --outdated` ; avis de sécurité :
  `gh api -X GET /advisories -f ecosystem=pip -f affects=<paquet>@<version>` pour chaque ligne de requirements.lock ;
  pdf.js et MathJax (static/vendor, versions dans leurs README) ; Python embarqué (conda-forge) et la CI Linux (3.12 →
  3.15). Mise à jour : tests, banc du texte caché, puis `./arxivreader lock`.

- Texte invisible (`structure.mark_hidden`, au début de l'analyse : `plain` ne le garde ni dans les étiquettes ni dans
  les titres) : couleur du texte blanche ou presque (luminance ≥ 0,9), transparente ou d'alpha nul (`_hiding`, `_color` :
  #rgb(a), #rrggbb(aa), rgb(a)(), quelques noms ; pas la couleur d'un fond : `--ltx-bg-color`, `background-color`,
  `--ltx-fill-color`), police minuscule, masqué, opacité < 0,1 ; marqué `.arx-hidden-text`, compté, jamais transmis à
  l'IA (`ai._render`, qui ne lit pas non plus les dessins). Exceptions (texte blanc seulement) : fond foncé posé sur lui
  ou un parent (`\colorbox`, `_dark_background`) ; dans un dessin, forme foncée qui couvre toute sa largeur, dessinée
  juste avant dans le même repère (titre d'un encadré tcolorbox, `_on_dark_shape`, contraste d'au moins 2,3). Prudent :
  une forme illisible au-dessus (déplacée, commandes relatives, image), sans surface, partielle, ou un foreignObject sans
  largeur, et le texte reste signalé ; formes lues une fois par groupe.
- Lettre barrée (`\not{\mathcal{D}}`, `\not D`, `\slashed{D}` : opérateur de Dirac) : LaTeXML laisse la lettre suivie de
  U+0338, barre combinante que **Chrome dessine sur la lettre et WebKit après elle** (sur l'indice). `math.slashedLetters`
  la remplace par la même barre non combinante de la police, ⧸ U+29F8 (barre de `\not` et de ≠ : hauteur 0,716 em,
  profondeur 0,215 em, boîte de la largeur d'un « = »), sans largeur, au début de la lettre — comme TeX place `\not` ;
  devant tout l'indice si la lettre en est la base (`attachScripts`), qui reste placé comme pour `\mathcal{D}_m`.
  Lettre gardée calligraphique (`restoreCalligraphic`). TeX des titres, résumés et notes : `\not` devant une lettre
  ou un groupe → `\arxivslash` (`\rlap{\mathrm{⧸}}#1`) ; `\not` de MathJax pose la barre sur l'indice, `\unicode`
  la prend dans une police de secours ; relations `\not=`, `\not\in`… et `\cancel` de l'auteur intacts. Vérifié
  sur 2610.36240 **dans WebKit (copie de l'app, ARXIVREADER_EVAL + WINDOWSHOT) et dans Chrome**.

- Couleurs de l'auteur (latexml.css, fin) : `--ltx-fg-color` / `--ltx-bg-color` de LaTeXML, et `mathcolor` /
  `mathbackground` des formules mis dans ces variables par `amsfix.colorVariables` (sinon MathJax ou le navigateur les
  figent : noir imposé invisible en mode sombre). Telles quelles en mode clair ; en mode sombre, oklch de même teinte,
  texte éclairci (luminosité ≥ 0,72, le noir devient blanc), fond assombri (≤ 0,35), sauf dans les dessins (fond blanc). Vérifié sur les 16 articles d'arXiv à consignes cachées recensés par arXiv:2507.06185 (versions citées) : chaque
  consigne signalée, aucune transmise, aucun autre signalement. Lecteur : « Afficher » va au premier passage, « Suivant »
  aux autres. Figures SVG insérées par `<object data>` : changées en `<img>` au nettoyage (`sanitize._objects_to_images`).

- Auteurs suivis et homonymes : une seule règle, `relevance.follows` (même personne d'après le nom, article pas écarté,
  catégories gardées), utilisée par le feed (`score_items`), la recherche des articles récents
  (`profile.followed_author_papers`) et les alertes (`alerts._who`, `forget_unfollowed`). Toute nouvelle façon de
  reconnaître un auteur suivi passe par elle. Catégories normalisées comme arXiv les écrit (`profile.clean_category`).

- Assistant et documents sans texte ou trop longs : un PDF scanné (`ai.without_text`) envoie les pages autour de la
  question en JPEG (7 avant, 3 après, 12 Mo au plus : les plus éloignées laissées de côté) ; un document au-delà de
  `PAPER_LIMIT` part réduit à son début et à une fenêtre autour de la question (`ai._focus`, `ai._window`), sauf
  résumé et relecture. Un article sous la limite part toujours entier.

- Fluidité de l'onglet IA (mesurée le 29/10/2026 dans WebKit, 21 réponses longues, 64 000 px de fil) : le fil est en
  `contain: strict` (défilement 23 → 17 ms par image) ; la variable `--ai-scrollbar` n'est posée que si elle change
  (posée à chaque défilement, elle refaisait le style de tout le fil) ; pendant le glissement de la poignée du panneau,
  le fil suit la poignée à chaque image, mais seules les réponses visibles sont recomposées : `.reader.resizing
  .ai-answer { content-visibility: auto }`, les autres gardent leur dernière hauteur (`contain-intrinsic-size: auto`),
  le fil ne saute pas (54 → 16 ms par image, écart au bord du panneau 0 px). Écartés : fil figé jusqu'au relâchement
  (1.11.12, grand vide en élargissant), recomposé toutes les 100–200 ms (le vide restait visible un instant). La mise
  en place a lieu dès l'appui ; curseur et clics bloqués par un voile (`.resize-shield`), pas par des propriétés
  héritées posées sur `.reader` (tout l'article recalculé : ~400 ms au départ du glissement) ; mouvements écoutés sur
  la fenêtre (le voile ne doit jamais rester).
  Premier passage sur l'onglet IA (1,6 s → 0,46 s) : MathJax en `adaptiveCSS: false` (`math.js`), sinon il ajoute des
  règles à sa feuille de style pour chaque caractère nouveau d'une réponse, et WebKit recalcule tout l'article (~1 s) ;
  et l'onglet IA est marqué sur le panneau (`data-tab`), pas sur la racine du lecteur (sinon ~180 ms de recalcul de
  l'article à chaque passage) ; sa largeur par défaut (`AI_PANEL_W`) n'est posée que sans largeur choisie
  (`applyPanelWidth`). Écarté :
  `content-visibility: auto` sur les réponses (aussi rapide, mais hauteurs estimées : la position gardée de chaque
  sous-onglet tomberait ailleurs, et WebKit ne compense pas les sauts en remontant). Le relâchement coûte encore
  ~250 ms : recomposition de l'article entier à la nouvelle largeur.

- Relecture croisée (`ai.cross_review`, appelée par la route de l'IA quand la demande porte `second`) : relectures
  des deux assistants en parallèle, comparaison par le second, synthèse par le premier ; toutes les demandes passent
  par `build_prompt` (même texte, sans le texte invisible ; limite d'article propre à chaque assistant). Les
  relectures jointes vont dans des balises `<review>`, déclarées « à analyser » dans `SYSTEM`. Étapes enregistrées
  dans la colonne `steps` de l'échange (la synthèse), marqueurs `[[both]]`, `[[a]]`, `[[b]]`, `[[dispute]]`,
  `[[withdrawn]]` montrés en étiquettes (`LiveAnswer.html`, posées après le Markdown) et écrits en clair dans les
  notes, la copie et l'export (`ai._marker_words`). Une erreur du second assistant porte son nom et le code
  « other » : un code « login » ferait croire le premier déconnecté. Assistants prêts : `/api/ai/partners`
  (`ai.partners`, vérifiés en parallèle ; gardés deux minutes par la page).

- Fichiers TeX importés (`texfile.py`) : compilés dans `<temp>/arxivreader-tex/<id>`, jamais dans le dossier de
  l'utilisateur (latexmk `-outdir`). latexmk retient qu'un `.bib` manquait et ne relance plus BibTeX : après un
  `.bib` manquant, son état (`.fdb_latexmk`) est effacé. Les énoncés qui ont un `\label` ont un identifiant tiré de
  lui, les autres de leur type et de leur texte (`stable_ids`, aussi à la ré-analyse : `stableIds` dans
  structure.json). Les `.aux` de la compilation donnent les numéros imprimés et les ancres exactes des `\label`
  (`apply_aux` : énoncés, équations ; `cite.<clé>` pour la bibliographie, lue aussi dans un `.bbl` de biblatex ou
  d'amsrefs) ;
  journal sans lignes coupées (`max_print_line`) ; les passages surlignés du PDF qui ont
  bougé sont retrouvés par leur texte côté page (`PdfNotes.inPlace`/`findMoved`, ±3 pages) et leur place enregistrée.

- Semantic Scholar avec une clé : 1 requête/s pour toute l'application. Toute nouvelle requête passe par
  `s2.paced(send, api_key)` (`app/s2.py`), qui espace les requêtes avec clé quel que soit le fil ; sans clé, rien
  de commun : chaque module garde ses pauses (`related.UNKEYED_PAUSE`, `citations`), comme avant la clé.

- Le Python embarqué garde en dur des chemins de l'ordinateur où l'application a été construite (fuseaux horaires,
  certificats SSL) : sur un autre Mac, ils n'existent pas. Le lanceur (`macos/main.swift`) donne donc
  `PYTHONTZPATH=/usr/share/zoneinfo` et `SSL_CERT_FILE` (certificats de l'application), et `feed._zone` se rabat sur
  les fuseaux de macOS. Pour simuler un autre Mac : `PYTHONTZPATH=/introuvable` avec le Python de l'application.

- Le dossier Documents est synchronisé par iCloud : construire l'application ailleurs
  (`~/Library/Caches/arXivReader-build`), sinon la signature casse. Une tâche de fond (launchd) ne peut pas lire
  Documents : elle utilise le code de l'application installée, sans écrire de bytecode (`PYTHONDONTWRITEBYTECODE`).
- Un `DataTransfer` synthétique ne peut pas fixer `dropEffect` : les glissements se testent pour de bon dans
  l'application.
- Réglage nouveau : l'ajouter à la liste `allowed` de `/api/settings` (et à `BOOLEAN_SETTINGS` ou
  `_valid_setting`), sinon il n'est pas enregistré.
- Le middleware exige `Content-Type: application/json` pour toute écriture sauf `DELETE` : un `POST` sans corps
  doit envoyer `{}`.
- Les titres d'arXiv contiennent du TeX ; les doublons de Semantic Scholar se comparent après `related._norm`
  (sans commandes TeX). Semantic Scholar abîme parfois des titres : préférer ceux d'arXiv. Zotero, lui, relie les
  éléments par identifiant arXiv (`zotero.arxiv_of`, index `zotero_index`), jamais par titre.
- La synchronisation Zotero fusionne trois états (ici, Zotero, la fois précédente) : toute modification de
  `_sync` doit garder les tests de `tests/test_zotero.py` (écriture refusée, doublons fusionnés, sous-collection
  profonde, dossier supprimé pendant un ajout, corbeille, retraits massifs).
- **Aucune requête de la page ne doit attendre longtemps** (WebKit peut abandonner une requête restée environ
  une minute sans réponse) : un import passe toujours en arrière-plan (`POST /api/papers`, mise à jour
  `POST /api/papers/{id}/refresh` suivie par `…/importing`), une attente côté serveur est bornée à 45 s
  (`ensure_ready`) et la page redemande ; l'API d'arXiv a un second essai patient (`arxiv.patient_get`,
  `search._fetch`) ; le feed du jour vient du RSS, le reste (auteurs suivis, revues, citations) en arrière-plan ; le graphe des papiers liés se
  construit en arrière-plan (`POST /api/related`, suivi par `GET /api/related/status`, arrêté par `POST /api/related/cancel` :
  points de contrôle `related._step` et `related._wait` entre deux requêtes).
- Ajout d'un clic (signet du feed, de la recherche, « Ajouter », dépôt sur un dossier) : `library.quick_add` inscrit
  l'article tout de suite (`source = "pending"`, d'après ce que la page en sait) et l'importe en arrière-plan ;
  `library.ensure_ready` fait attendre son ouverture (ou refait l'import s'il avait échoué). L'import de fond garde
  la place dans la bibliothèque telle qu'elle est à sa fin (retiré entre-temps : reste retiré).
- Figures : l'import garde l'adresse d'origine de chaque image (`assets.json`) ; à l'ouverture d'un article,
  celles qui manquent sont redemandées (`library.repair_assets`, par ces adresses, sinon par un nouvel import qui
  peut renommer les fichiers), et le lecteur suit la réparation (`/api/papers/{id}/figures`) puis recharge
  l'article. Une image qui ne vient pas laisse une ligne dans `server.log`.
- Les noms de personnes servent d'exemples dans l'interface : ne pas y mettre de vrais noms ni de vrais
  identifiants (« Votre nom », « arxiv.org/a/… »).

## 10. Journaux et diagnostic

- Serveur : `server.log` dans la bibliothèque (`~/Library/Application Support/arXivReader`).
- Vérification des services : `check.json` (même dossier) et `~/Library/Logs/arXivReader-check.log`.
- Synchronisation Zotero : dernier état dans les réglages (`zoteroLastSync`, `zoteroLastError`,
  `zoteroLastErrorCode`, `zoteroReport`), visible dans Réglages > Zotero.
