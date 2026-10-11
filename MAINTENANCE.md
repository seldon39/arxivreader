# Maintenance d'arXivReader — instructions pour une IA

Ce fichier s'adresse à un assistant (Claude Code, Codex…) chargé de maintenir ou de faire évoluer arXivReader :
ce qu'il faut savoir avant de toucher au code, comment vérifier, comment publier, et quoi faire quand un service
extérieur change. Les README (`README.md`, `README.fr.md`) décrivent l'application pour ses utilisateurs ; ce
fichier, son entretien. Le tenir à jour quand une règle ou un piège change.

## 1. En deux mots

Lecteur d'articles arXiv pour Mac, pensé pour les mathématiques : serveur Python (FastAPI + SQLite) sur
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
| Notes, surlignages, état de lecture, export | `annotations.py`, `export.py` | `annotations.js`, `markdown.js` |
| Bibliothèque, dossiers, sélection | `library.py`, `folders.py` | `library.js`, `selection.js` |
| Feed, profil, pertinence, alertes, citations | `feed.py`, `profile.py`, `relevance.py`, `alerts.py`, `citations.py`, `journals.py` | `feed.js`, `profile.js` |
| Recherche arXiv | `search.py` | `search.js` |
| Papiers liés (graphe) | `related.py` | `related.js` |
| IA | `ai.py` (Claude, SDK Agent), `providers.py` (Codex, Gemini, local) | `ai.js`, `aiproviders.js` |
| Zotero | `zotero.py` | `zotero.js`, `settings.js` |
| Premier lancement | `onboarding.py` | `onboarding.js` |
| Mises à jour, vérification des services | `updates.py`, `check.py` | `updates.js` |
| Formules, construction du DOM | `feed.tex_text` (TeX des titres et résumés) | `math.js` (MathJax), `amsfix.js`, `util.js` (`h`, `withMath`, `tidyTitle`) |
| Réglages | `main.py` (liste `allowed` et `_valid_setting` de `/api/settings`) | `settings.js` |
| Textes de l'interface | — | `i18n.js` (blocs FR et EN : mêmes clés) |
| Application Mac | — | `macos/main.swift` (onglets, glisser sur un onglet, téléchargements, essais), `macos/build.sh`, `macos/release.sh` |

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
| Semantic Scholar (graph, batch, recommendations) | `related.py`, `relevance.py`, `journals.py`, `citations.py`, `feed.py`, `onboarding.py` | quota partagé sans clé (429), champs | attente et nouvel essai ; fonctions dégradées sans lui |
| Zotero web API v3 | `zotero.py` | en-têtes (`Total-Results`, `Last-Modified-Version`, `Backoff`), écritures multiples (`successful`/`failed`), `since`/`/deleted` | garde-fous : écriture refusée réessayée, retraits massifs en attente |
| Claude (SDK Agent et son CLI) | `ai.py` | options du SDK, liste des modèles (`get_server_info`), connexion | message de connexion dans les réglages |
| Codex CLI, API Gemini, modèle local | `providers.py` | options du CLI (`-m`, `model_reasoning_effort`), `~/.codex/models_cache.json`, `thinkingLevel` de Gemini | — (la CLI Gemini n'accepte plus les comptes Google gratuits : API par clé) |
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
