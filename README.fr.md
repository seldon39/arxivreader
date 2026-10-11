# arXivReader

[English](README.md) · **Français**

Lecteur d'articles arXiv pour Mac, pensé pour les mathématiques : mise en page « papier AMS », énoncés et
preuves navigables, graphe des dépendances, notes et surlignages, feed des nouveautés trié selon votre profil,
et un assistant IA (Claude, Codex, Gemini ou un modèle local) qui ne voit que le texte de l'article.
Tout reste sur votre Mac : le serveur n'écoute que sur 127.0.0.1. Interface en français et en anglais.

## Installer

Télécharger `arXivReader.dmg` depuis la page [Releases](https://github.com/seldon39/arxivreader/releases)
(Mac Apple Silicon, macOS 13 ou plus), l'ouvrir et glisser arXivReader dans Applications. L'application est
autonome (Python et ses paquets sont inclus) ; rien d'autre à installer.

- **Premier lancement** : l'application n'est pas signée par un compte développeur Apple, macOS la bloque.
  Réglages Système → Confidentialité et sécurité → *Ouvrir quand même* (une seule fois).
- **Mises à jour** : l'application vérifie une fois par jour s'il existe une nouvelle version sur GitHub
  (réglable dans Réglages → Mises à jour) ; une pastille « Mise à jour » apparaît alors en haut à droite.
  Remplacer l'application dans Applications ; la bibliothèque est conservée.

## Application Mac : construction

`macos/build.sh` construit `dist/arXivReader.dmg` (~200 Mo) : une application avec sa fenêtre (WebKit, comme
Safari), qui embarque Python et ses paquets. `macos/release.sh` la publie comme nouvelle version (après avoir
augmenté `VERSION` dans `app/version.py`) : étiquette Git, envoi sur GitHub, image disque jointe.

- Quitter (⌘Q) arrête aussi le serveur.
- Onglets : la barre d'onglets du Mac est toujours affichée, son « + » (ou ⌘T) ouvre la bibliothèque dans
  un nouvel onglet ; ⌘-clic (ou clic du milieu) sur un article, dans la bibliothèque, le feed ou un renvoi,
  l'ouvre dans un nouvel onglet en arrière-plan (⇧⌘-clic : au premier plan). Un onglet glissé hors de la
  barre devient une fenêtre (deux articles côte à côte) ; ⌘N ouvre une nouvelle fenêtre.
- **Bibliothèque** : `~/Library/Application Support/arXivReader` (menu arXivReader → Afficher la bibliothèque
  dans le Finder). À la première ouverture, l'application propose de recopier une bibliothèque existante (le
  dossier `library/` de ce projet) : quitter d'abord l'ancienne version. Ensuite, l'ancienne et la nouvelle ne
  partagent plus rien.
- **Connexion des assistants IA** : bouton *Se connecter…* / *Lancer dans le Terminal* dans l'onglet IA.
- Construire : outils en ligne de commande Xcode (`swiftc`), miniforge (`conda`, Python pris dans son cache) et
  `.venv` à jour (ses paquets sont recopiés). Construction dans `~/Library/Caches/arXivReader-build` (hors d'iCloud).

## Lancer depuis le code source

- **Double-clic** sur `ArXivReader.command` dans le Finder : une fenêtre Terminal s'ouvre, puis le navigateur.
  Fermer la fenêtre Terminal (ou Ctrl+C) arrête l'application.
- **Terminal** : `./arxivreader` depuis ce dossier.

La première fois, le script crée l'environnement Python (`.venv/`, ~1 min). L'adresse est
http://127.0.0.1:8710/ ; le serveur n'écoute que sur ce Mac (pas sur le Wi-Fi).

## Ce que fait la version actuelle (étape 1 : lecteur structuré)

- Import par identifiant ou lien arXiv (`2608.24719`, `arXiv:2607.17203v1`, `https://arxiv.org/abs/…`).
- L'article est lu dans sa **version HTML officielle** (arxiv.org/html, sinon ar5iv), produite par LaTeXML
  à partir du TeX : formules en MathML, énoncés, preuves et renvois balisés.
  Sans version HTML, l'article se lit dans son PDF (voir plus bas).
- **Mise en page « papier AMS »** (classe amsart) : texte en Charter ou Computer Modern, formules
  rendues par **MathJax** (règles de TeX, polices Computer Modern, même rendu dans Safari, Firefox et
  Chrome) ; le MathML du navigateur reste disponible dans les réglages.
- **Aperçu au survol** de tout renvoi (théorème, équation, référence, note) ; clic = aller à l'endroit,
  avec un bouton « Retour ».
- **Preuves repliables** ; les preuves écrites à la main (« *Proof.* … □ ») sont repérées automatiquement.
- **Panneau Structure** : liste des énoncés (filtres par type), dépendances d'un énoncé
  (utilise / utilisé par / cite, chaîne complète), **graphe** des dépendances (plein écran possible).
- Signalement du **texte invisible** (blanc, minuscule, masqué) éventuellement présent dans l'article.
- Reprise de la lecture à l'endroit quitté ; réglages : langue FR/EN, thème clair/sombre/système,
  taille du texte, largeur de lecture, preuves repliées par défaut.
- Mise en page adaptée à la largeur : 3 colonnes (≥ 1280 px), 2 (≥ 900 px), 1 colonne avec tiroirs.

Les dépendances viennent des renvois (`\ref`, `\eqref`, `\cite`) dans l'énoncé et dans sa preuve :
un argument utilisé sans renvoi explicite n'apparaît pas.

## Recherche

L'onglet « Recherche » interroge tout arXiv (interface publique export.arxiv.org/api), avec les options de
sa recherche avancée : critères par champ (tous les champs, titre, auteur, résumé, commentaires, référence de
journal, catégorie, numéro de rapport, identifiant) reliés par ET / OU / SAUF, « expression exacte » entre
guillemets, catégories (vos catégories du feed proposées, d'autres ajoutées), articles croisés compris ou
non, période de soumission (12 derniers mois, une année, une période), tri par pertinence, par date, ou
« Pour vous » (les 200 résultats les plus pertinents pour arXiv, reclassés selon votre profil comme le feed),
pages de 25 résultats. Les résultats se présentent comme le feed (pertinence pour votre profil, avis, ajout
à la bibliothèque, ⌘-clic pour un nouvel onglet). Non proposés, faute de prise en charge par l'interface
d'arXiv : DOI, ORCID, classes MSC/ACM, licence. Une requête au plus toutes les 3 s, comme arXiv le demande.

## Dossiers

Dans la bibliothèque, une rangée de dossiers précède la liste complète des articles (qui reste affichée).
Un clic sur un dossier le déroule juste dessous, avec ses articles ; « Renommer », « Supprimer le dossier »
(annulable ; ses articles restent dans la bibliothèque) et « Fermer » ; ses articles se trient par pertinence
pour votre profil (calculée comme pour le feed, un article n'étant pas comparé à lui-même) ou par date de
publication (plus récents, plus anciens). Le bouton dossier de chaque article
ouvre « Ranger dans… » : dossiers à cocher, ou création d'un dossier. Un article peut être dans plusieurs
dossiers, affichés en fin de sa ligne ; « Filtres » permet aussi de réduire la liste à un ou plusieurs dossiers.

## Feed (étape 2)

Menu **Feed** en haut, quatre onglets :
- **Nouveautés arXiv** : annonces quotidiennes des catégories suivies (math.DG, math.CV, math.SP,
  math-ph par défaut, modifiables dans les réglages), **triées par pertinence pour votre profil**,
  avec la raison affichée (« auteur suivi », « proche de … », mot-clé) ; les articles peu pertinents
  sont réduits à une ligne (clic pour déplier). Un clic sur le titre ouvre l'article sans l'ajouter à la
  bibliothèque (cache vidé après 30 jours sans lecture) ; le **signet** l'ajoute ou l'en retire
  (aussi dans la barre du lecteur). En tête : les nouveaux articles des **auteurs suivis**,
  même hors de vos catégories. Tri « Ordre arXiv » possible. 👍 / 👎 affinent le tri. Les flèches de part et d'autre de la date
  d'annonce mènent aux jours précédents ; au-delà d'une semaine, ils viennent de l'API de recherche
  d'arXiv, qui donne les nouveaux articles et les croisés mais pas les nouvelles versions.
- **Recommandations** : Semantic Scholar (API publique, sans compte), à partir du profil et des 👍.
- **Profil** : vos articles, articles et livres de référence, auteurs suivis, mots-clés.
  « Pré-remplir » le construit à partir de la bibliothèque et des bibliographies de vos articles
  (auteurs cités au moins deux fois, prénom complet retrouvé sur arXiv ; livres cités ; mots-clés).
  Les références de vos articles servent aussi, en arrière-plan.
- **Importer** : coller un e-mail de Scholar Inbox ou de Semantic Scholar, une liste de liens ou un
  export BibTeX ; les identifiants arXiv sont repérés puis importés en lot.

Calcul de la pertinence (app/relevance.py) : proximité de vocabulaire (TF-IDF des titres et résumés)
et proximité de sens (vecteurs SPECTER de Semantic Scholar, comparés au lot du jour), meilleure
référence du profil ; + auteurs suivis, mots-clés ; − proximité avec les articles 👎.
Une clé d'API Semantic Scholar (gratuite, facultative) évite les limitations de requêtes.

## Où sont les preuves

Une preuve est rattachée à son énoncé quand elle le suit (environnement *proof* ou « *Proof.* … □ »),
quand son titre y renvoie (« Proof of Lemma 4.4 »), ou quand une section entière lui est consacrée
(« 4.2. Proof of Theorem 1.3 » ; l'énoncé affiche alors « → Preuve au § 4.2 »). Un énoncé dont le titre
cite une référence (« Theorem 3.2 ([12, Theorem 4.3]) ») est marqué *cité* ; un énoncé justifié par la
phrase qui le précède (« The following corollary is a consequence of (2.6) ») ou qui le suit
(« This follows from Lemma 2.1 ») est marqué *justifié dans le texte*. Sinon : *preuve non repérée*.
Si la détection se trompe ou ne trouve rien : onglet Dépendances, **Indiquer la preuve** — une section
de l'article, un passage choisi dans le texte (cliquer le premier puis le dernier paragraphe), *immédiat*
ou *admis* ; *Détection automatique* annule ce choix. Le choix est gardé avec les notes de l'article et
compte pour les dépendances et pour Claude.
Les articles déjà importés sont ré-analysés automatiquement quand cette analyse change.

## D'un article à l'autre

Quand une référence de la bibliographie est un article lu ici (même identifiant arXiv, même DOI, ou titre
retrouvé dans la référence), « [12, Theorem 4.3] » affiche au survol le Theorem 4.3 de cet article, avec un
bouton pour l'ouvrir à cet endroit ; dans la bibliographie, une pastille « Ouvrir l'article ».

## Articles sans version HTML

Quand arXiv ne propose pas de version HTML, l'article se lit dans son PDF, affiché dans le lecteur.
Le PDF est affiché par pdf.js (Mozilla), intégré à l'application : il indique la page lue, si bien que
le passage du PDF au HTML (sélecteur « HTML | PDF ») revient au même endroit, et que la position de
lecture est gardée aussi en lisant le PDF. Les scripts contenus dans les PDF ne sont jamais exécutés.
Énoncés, preuves, dépendances, graphe et bibliographie sont tirés de la source TeX (environnements
`\newtheorem`, numérotation simulée, `\ref`/`\eqref`/`\cref`) ; un énoncé choisi mène à sa page du PDF
(ancres de hyperref, sinon recherche du texte). Comparé à la version HTML sur 42 articles : 99 % des
énoncés et 97 % des liens retrouvés. Un article importé avant cette version : bouton « Mettre à jour
depuis arXiv » de sa carte dans la bibliothèque.

## Citations de vos articles

Une fois par jour, Semantic Scholar donne les articles qui citent ceux du profil (« Mes articles ») ; les
nouveaux apparaissent dans « Citent vos articles » sur le feed, comptent dans la pastille et, si le réglage
le permet, déclenchent une notification macOS.

## Auteurs suivis : alertes

Tant qu'arXivReader est lancé, il vérifie toutes les 3 heures (et une minute après le lancement) les
nouveaux articles des auteurs suivis du profil : notification macOS pour chacun (réglage « Me notifier
quand un auteur suivi publie »), pastille sur « Feed », et mention « Non vu » sur la carte jusqu'à ce
qu'elle ait été affichée. Un clic sur la notification macOS ouvre l'Éditeur de script (limite d'osascript) :
il suffit de la fermer et d'aller dans le feed.

## Notes et annotations (étape 3)

- **Surligner** : sélectionner un passage (formules comprises) ; une barre propose 4 couleurs et
  « ajouter une note ». Un clic sur un surlignage permet de changer sa couleur, sa note, ou de le supprimer.
- **Énoncés et preuves** : l'icône de note en marge (au survol) ouvre une note et, pour un énoncé,
  l'état de lecture *compris / à revoir / vérifié* (affiché dans le titre et la liste des énoncés).
- **Onglet Notes** du panneau : progression de lecture, fiche de lecture de l'article, toutes les
  annotations dans l'ordre de l'article (un clic y ramène).
- Notes en Markdown (**gras**, *italique*, listes, citations) avec formules `$…$`, `$$…$$`,
  enregistrées automatiquement. Un article annoté n'est jamais effacé du cache.

## Claude sur l'article (étape 4)

- **Connexion, une seule fois** : double-clic sur `Connecter Claude.command` (ou `./arxivreader login`).
  La page de connexion Claude s'ouvre dans le navigateur ; c'est votre abonnement Claude qui est utilisé,
  sans clé d'API (l'usage compte dans ses limites).
- **Énoncé** (onglet Dépendances, ou note en marge) : *Expliquer*. **Preuve** : *Détailler la preuve*
  (étapes numérotées, arguments ajoutés marqués **[added]**, points douteux **⚠**) et *Auditer la preuve*
  (verdict, problèmes avec leur gravité, hypothèses utilisées, dépendances).
- **Passage** : le sélectionner, puis ✦ ; on peut ajouter une question. **Article entier** : résumé
  structuré, relecture critique, ou question libre ; *Question de suite* pour poursuivre un échange.
- Réponses en direct, formules rendues, renvois (« Lemma 2.3 », « (2.11) ») cliquables ; *Ajouter à mes
  notes* les copie dans la note de l'énoncé ou de la preuve (sinon dans la fiche de lecture). Modèle
  Opus ou Sonnet au choix dans l'onglet IA. Les réponses sont conservées avec l'article.
- **Annotations proposées par Claude** : bouton *Annoter* sur un énoncé, ou toute demande du type « surligne… /
  annote… ». Claude joint une liste d'annotations (passages cités mot pour mot, couleur, note ; notes sur un
  énoncé ou une preuve) : chaque passage est retrouvé dans l'article, vous cochez ce que vous gardez,
  *Appliquer* les crée (marquées « Claude ») et *Annuler* les retire. Claude n'écrit jamais lui-même.
- Claude ne reçoit que le texte de l'article (le texte invisible est retiré) et la demande : aucun outil,
  ni fichiers, ni réseau, ni commandes, ni réglages Claude Code personnels ; rien n'est gardé côté Claude Code.

### Autres assistants (Réglages → Assistant IA)

Pour un collègue sans abonnement Claude, l'onglet IA fonctionne aussi avec :

- **Codex** (OpenAI, compte ChatGPT) : `npm install -g @openai/codex`, puis `codex login`. Appelé par
  `codex exec` sans commandes ni recherche web, sans réglages personnels ni session enregistrée, dans un
  dossier vide, bac à sable en lecture seule.
- **Gemini** (Google, compte Google) : `npm install -g @google/gemini-cli`, puis lancer `gemini` une fois pour
  se connecter. Appelé avec ses outils, extensions et serveurs MCP désactivés, consignes d'arXivReader à la
  place des siennes.
- **Modèle local** (Ollama, LM Studio, llama.cpp… : tout serveur compatible OpenAI sur `127.0.0.1`) : rien ne
  quitte l'ordinateur. Prévoir un contexte d'au moins 32 000 jetons (`OLLAMA_CONTEXT_LENGTH=32768 ollama serve`) ;
  seul le début des longs articles (~120 000 caractères) est transmis. Les petits modèles sont nettement moins
  fiables en mathématiques.

Codex et Gemini sont des agents : leurs outils sont coupés par leurs propres réglages, une garantie moins
forte que pour Claude (appelé sans aucun outil). Leur intégration suit leur documentation (versions
d'octobre 2026) mais n'a pas été essayée avec un vrai compte.

## Sécurité

- Serveur sur `127.0.0.1` seulement ; noms d'hôte étrangers et requêtes d'autres sites refusés.
- HTML des articles nettoyé (scripts, formulaires, liens `javascript:` retirés) et politique CSP qui
  interdit tout script venant de l'article.
- Données (base SQLite + un dossier par article) dans `~/Library/Application Support/arXivReader` pour
  l'application, dans `library/` depuis le code source ; jamais ailleurs.
- Connexions sortantes : arXiv (articles, nouveautés), Semantic Scholar (recommandations), GitHub (une fois par
  jour, numéro de la dernière version ; désactivable), et l'assistant IA choisi.

## Organisation du code

```
app/            serveur Python (FastAPI)
  arxiv.py      identifiants, métadonnées (API arXiv), téléchargement du HTML
  sanitize.py   nettoyage du HTML
  structure.py  sommaire, énoncés, preuves, dépendances, bibliographie
  texsource.py  lecture de la source TeX (options de classe, police, étiquettes de listes)
  feed.py       nouveautés arXiv (RSS, OAI-PMH), recommandations Semantic Scholar, repérage d'identifiants
  search.py     recherche dans tout arXiv (onglet Recherche)
  folders.py    dossiers de la bibliothèque
  profile.py    profil de recherche, pré-remplissage, auteurs suivis ; relevance.py : tri par pertinence
  annotations.py  surlignages, notes, état de lecture
  ai.py         texte de l'article et consignes pour Claude, appel sans outils (SDK Claude Agent)
  crossrefs.py  références qui sont des articles lus ici ; citations.py : articles qui citent les vôtres
  texstructure.py  structure tirée de la source TeX et pages du PDF (articles sans version HTML)
  providers.py  autres assistants : Codex, Gemini (en ligne de commande), modèle local (Ollama, LM Studio)
  library.py    import d'un article ; db.py : base SQLite ; main.py : routes web
macos/          application Mac : main.swift (fenêtre, onglets, serveur), icon.swift, build.sh, release.sh
static/         interface (HTML, CSS, JavaScript sans compilation) ;
                vendor/mathjax (MathJax 3.2.2), vendor/pdfjs (pdf.js 6.3.289, Apache 2.0)
                et fonts/ (Latin Modern) copiés localement
tests/          tests hors ligne : .venv/bin/python -m unittest discover tests
```

## Inspiration

arXivReader s'inspire en partie d'[arXivpecker](https://github.com/MingchenXia/arXivpecker) de Mingchen Xia,
un lecteur d'articles de mathématiques local et assisté par IA. Les deux projets ne partagent pas de code :
arXivReader a été écrit indépendamment (Python, HTML LaTeXML d'arXiv et MathJax), avec ses propres choix —
rendu de l'HTML officiel, feed quotidien trié selon votre profil, assistant IA sans outils ni réseau,
application Mac installable.

## Licence

MIT.
