# arXivReader

[English](README.md) · **Français**

Lecteur d'articles arXiv pensé pour les mathématiques : mise en page « papier AMS », énoncés et preuves navigables,
graphe des dépendances, notes et surlignages, feed des nouveautés trié selon votre profil, graphes de papiers liés, et
un assistant IA (Claude, Codex, Gemini ou un modèle local) qui ne voit que le texte de l'article. Interface en
français et en anglais.

Il fonctionne sous **macOS** et **Windows** (applications à télécharger) et sous **Linux** (depuis le code source).
Tout reste sur votre ordinateur : le serveur n'écoute que sur 127.0.0.1.

![Démo d'arXivReader : feed trié par pertinence, bibliothèque, lecteur avec aperçu des énoncés, surlignage et copie LaTeX, assistant IA, graphe des dépendances, articles liés](docs/demo.gif)

**Pour contribuer :** [CONTRIBUTING.md](CONTRIBUTING.md) et la [feuille de route](ROADMAP.md) (en anglais).

## Installer

| Système | Téléchargement | Configuration |
|---|---|---|
| macOS | `arXivReader.dmg` dans la [dernière version](https://github.com/seldon39/arxivreader/releases/latest) | Mac Apple Silicon, macOS 13 ou plus |
| Windows | `arXivReader-setup.exe` (installateur) ou `arXivReader-windows.zip` (portable) dans la [dernière version](https://github.com/seldon39/arxivreader/releases/latest) | Windows 10 ou 11, 64 bits |
| Linux | depuis le code source : voir [Linux](#linux) | Python 3.12 ou plus |

Les applications sont autonomes (Python et ses paquets sont inclus) : rien d'autre à installer. Elles vérifient une
fois par jour s'il existe une nouvelle version (Réglages → Mises à jour) et affichent alors une pastille « Mise à
jour » en haut à droite ; la bibliothèque est conservée lors d'une mise à jour.

### macOS

Ouvrir `arXivReader.dmg` et glisser arXivReader dans Applications. Pour mettre à jour, remplacer l'application dans
Applications (la quitter d'abord). La première fois, macOS bloque l'application une fois : voir ci-dessous.

#### Si macOS refuse d'ouvrir l'application

L'application est libre, mais elle n'est pas signée par un compte développeur Apple (payant) ni « notarisée »
par Apple. Sur un autre Mac que celui qui l'a construite, la première ouverture affiche un message du type
*« arXivReader » n'a pas été ouvert — Apple n'a pas pu confirmer que « arXivReader » ne contenait pas de logiciel
malveillant…*. C'est le message habituel de macOS pour toute application non notarisée ; pour l'ouvrir quand
même :

1. Double-cliquer sur arXivReader dans Applications ; au message, cliquer sur **Terminé** (pas sur *Placer dans
   la corbeille*).
2. Ouvrir **Réglages Système → Confidentialité et sécurité** et descendre jusqu'à la section **Sécurité**.
3. À côté de *« arXivReader » a été bloqué pour protéger votre Mac*, cliquer sur **Ouvrir quand même**, confirmer
   avec le mot de passe ou Touch ID, puis **Ouvrir**.

macOS s'en souvient ; après une mise à jour (nouvelle copie téléchargée), il peut le redemander une fois. Sous
macOS 14 ou avant, clic droit (ou Contrôle-clic) sur l'application dans Applications → **Ouvrir** → **Ouvrir**
suffit aussi.

Ou, dans le Terminal, retirer la marque « téléchargé depuis Internet » de l'application (seulement pour une
application téléchargée depuis cette page) :

```bash
xattr -dr com.apple.quarantine /Applications/arXivReader.app
```

Rendre cette étape inutile est dans la [feuille de route](https://github.com/seldon39/arxivreader/issues/1).

### Windows

Lancer **`arXivReader-setup.exe`** : il installe arXivReader pour votre compte (sans droits d'administrateur),
l'ajoute au menu Démarrer (et au bureau si vous cochez la case) et le fait apparaître dans **Paramètres →
Applications**, avec un bouton **Désinstaller**. Pour mettre à jour, lancer le nouvel installateur : il met
l'application à jour sur place. Vous préférez ne rien installer (sur une clé USB, par exemple) ? Prenez
**`arXivReader-windows.zip`** : clic droit → **Extraire tout…**, puis double-cliquer sur `arXivReader.exe` dans le
dossier `arXivReader` ; pour mettre à jour, remplacer ce dossier.

L'application s'ouvre dans sa propre fenêtre, avec des onglets comme sur le Mac (Ctrl+T, Ctrl-clic sur un lien, le **+** de la barre d'onglets) ; les liens
vers d'autres sites s'ouvrent dans votre navigateur habituel. La bibliothèque est gardée dans
`%LOCALAPPDATA%\arXivReader`.

- Si Windows affiche *Windows a protégé votre ordinateur* : **Informations complémentaires**, puis **Exécuter quand
  même** (l'application est libre mais pas signée). L'antivirus peut aussi demander confirmation la première fois.
- L'application s'appuie sur WebView2, le moteur d'Edge inclus dans Windows 10 et 11 (s'il manque, sur un Windows
  allégé, l'application indique où l'installer).
- Une mise à jour ou une désinstallation ne touche jamais à la bibliothèque, rangée ailleurs.

### Linux

Pas encore d'application empaquetée : arXivReader tourne depuis le code source, dans le navigateur. Il faut Python 3.12
ou plus avec son module `venv` (sous Debian/Ubuntu, le paquet `python3-venv`), puis :

```bash
git clone https://github.com/seldon39/arxivreader.git && cd arxivreader && ./arxivreader
```

La première fois, le script crée l'environnement Python (`.venv/`, ~1 min), puis ouvre http://127.0.0.1:8710/ avec
`xdg-open` ; Ctrl+C arrête l'application. Autre Python : `PYTHON=python3.14 ./arxivreader`. Mise à jour : `git pull`,
puis `./arxivreader` de nouveau (l'application signale les nouvelles versions). Les tests tournent sous Linux avec
Python 3.12, 3.13 et 3.14 à chaque poussée.

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
  dans le Finder). Menu Fichier → Recopier une bibliothèque… : une bibliothèque existante (le dossier `library/`
  de ce projet, celle d'un autre Mac) remplace l'actuelle, mise de côté dans un dossier voisin ; quitter d'abord
  l'autre version. Ensuite, les deux ne partagent plus rien.
- **Connexion des assistants IA** : bouton *Se connecter…* / *Lancer dans le Terminal* dans l'onglet IA.
- Construire : outils en ligne de commande Xcode (`swiftc`), miniforge (`conda`, Python pris dans son cache) et
  `.venv` à jour (ses paquets sont recopiés). Construction dans `~/Library/Caches/arXivReader-build` (hors d'iCloud).

## Lancer depuis le code source

Sous Linux, voir [Linux](#linux) plus haut. Sur un Mac (développement) :

- **Double-clic** sur `ArXivReader.command` dans le Finder : une fenêtre Terminal s'ouvre, puis le navigateur.
  Fermer la fenêtre Terminal (ou Ctrl+C) arrête l'application.
- **Terminal** : `./arxivreader` depuis ce dossier.

La première fois, le script crée l'environnement Python (`.venv/`, ~1 min). L'adresse est
http://127.0.0.1:8710/ ; le serveur n'écoute que sur cet ordinateur (pas sur le Wi-Fi).

## Ce que fait la version actuelle (étape 1 : lecteur structuré)

- Import par identifiant ou lien arXiv (`2608.24719`, `arXiv:2607.17203v1`, `https://arxiv.org/abs/…`).
- L'article est lu dans sa **version HTML officielle** (arxiv.org/html, sinon ar5iv), produite par LaTeXML
  à partir du TeX : formules en MathML, énoncés, preuves et renvois balisés.
  Sans version HTML, l'article se lit dans son PDF (voir plus bas).
- **Mise en page « papier AMS »** (classe amsart) : texte en Charter ou Computer Modern, formules
  rendues par **MathJax** (règles de TeX, polices Computer Modern, même rendu dans Safari, Firefox et
  Chrome) ; le MathML du navigateur reste disponible dans les réglages.
- **Dessin absent de la version HTML** (PSTricks… : LaTeXML n'en garde que les étiquettes) : un avertissement
  à sa place, avec un bouton « Voir dans le PDF ».
- **Affiliations des auteurs** sous le résumé, une ligne par auteur (nom, adresse, courriel), avec les notes de
  première page (financements, mots-clés, classification).
- **Formules copiées en LaTeX** : ⌘C sur une sélection copie ses formules en `$…$` (en ligne) et `\[…\]` (hors
  texte) au lieu des symboles de MathJax ; un bouton « TeX » fait de même dans la barre de sélection ; ⌥-clic sur
  une formule : son TeX seul.
- **Clic droit** sur une sélection ou une formule : surligner, surligner et ajouter une note, copier en LaTeX,
  copier le texte, ou demander à l'assistant IA (ailleurs, le menu du système reste).
- **Aperçu au survol** de tout renvoi (théorème, équation, référence, note) ; clic = aller à l'endroit,
  avec un bouton « Retour ».
- **Preuves repliables** ; les preuves écrites à la main (« *Proof.* … □ ») sont repérées automatiquement.
- **Panneau Structure** : liste des énoncés (filtres par type), dépendances d'un énoncé
  (utilise / utilisé par / cite, chaîne complète), **graphe** des dépendances (plein écran possible).
- Signalement du **texte invisible** (blanc, minuscule, masqué) éventuellement présent dans l'article.
- Reprise de la lecture à l'endroit quitté ; réglages : langue FR/EN, thème clair/sombre/système,
  taille du texte, largeur de lecture (de « Étroite » à « Pleine » pour les grands écrans), preuves repliées par
  défaut ; taille et largeur du texte aussi dans la barre du lecteur (A−/A+ et deux boutons de largeur).
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

## Papiers liés

L'onglet « Papiers liés » (à la manière de Connected Papers) montre les 40 articles les plus proches d'un
article, choisi par son lien arXiv, dans la bibliothèque (menu, ou bouton ⊙ de chaque carte), ou glissé dans le
cadre. Candidats : ses références, les articles qui le citent et ceux que ces voisins citent souvent (Semantic
Scholar) ; proximité : références communes et articles qui les citent ensemble, rapportées à la taille des
listes (un classique cité partout n'écrase pas un travail proche). Onglets : le graphe, les travaux antérieurs
(les plus cités par les articles du graphe : souvent les travaux fondateurs) et les travaux dérivés (ceux qui
citent le plus d'articles du graphe : synthèses, travaux récents) ; en choisir un fait ressortir les articles
du graphe qui lui sont liés. Graphe : plus deux articles
sont proches, plus ils sont rapprochés ; seuls les liens les plus forts de chaque article sont tracés, tous ceux de l'article choisi apparaissent ; taille selon les citations, couleur selon l'année (années dans la légende), pastille cochée pour ceux de la
bibliothèque ; pincement du trackpad (ou ⌘ + molette, ou boutons − +) pour zoomer, glisser le fond pour se déplacer ; le graphe s'agrandit à toute la fenêtre (Échap pour revenir, touches + − 0). L'article choisi se lit, s'ajoute à la
bibliothèque, se range dans un dossier (ou s'y glisse), ou devient le point de départ d'un nouveau graphe. Un
graphe est gardé quinze jours (« Reconstruire » pour le refaire). La construction prend de quelques secondes à
plusieurs minutes sans clé d'API Semantic Scholar (quota partagé) ; elle se fait en arrière-plan, son étape s'affiche, et
« Annuler » l'arrête.

## Dossiers

Dans la bibliothèque, une rangée de dossiers précède la liste complète des articles (qui reste affichée).
Un clic sur un dossier le déroule juste dessous, avec ses articles ; « Renommer », « Supprimer le dossier »
(annulable ; ses articles restent dans la bibliothèque) et « Fermer » ; ses boutons « Tri » et « Filtres » proposent les mêmes
choix que ceux de la bibliothèque (sans le filtre par dossier), le tri ajoutant la pertinence pour votre profil (calculée comme pour le feed, un article n'étant
pas comparé à lui-même), et une recherche par mots réduit la liste du dossier (titre, auteurs, résumé,
identifiant, catégories). Le bouton dossier de chaque article
ouvre « Ranger dans… » : dossiers à cocher, ou création d'un dossier. On peut aussi glisser un article (par son
titre) sur un dossier ou dans le dossier ouvert, depuis la bibliothèque, le feed, la recherche, une autre fenêtre
d'arXivReader, ou un lien arXiv venu d'ailleurs (Safari…) ; un article pas encore dans la bibliothèque y est
d'abord importé. Déposé sur la liste générale (« Autres articles »), un article du feed ou de la recherche
est ajouté à la bibliothèque, hors dossier. Un article glissé qui s'attarde sur un onglet de la barre d'onglets ouvre
cet onglet (pour le déposer dans sa page) ; déposé sur « Bibliothèque » en haut, il y est ajouté et la bibliothèque
s'ouvre dans un nouvel onglet ; sur « Papiers liés », son graphe s'ouvre dans un nouvel onglet. Le bouton ☐ de chaque carte (bibliothèque, dossiers, feed, recherche) sélectionne plusieurs
articles : une barre en bas propose « Ranger dans… », et glisser l'un d'eux les emporte tous. Une fois des
articles rangés, la liste générale devient « Autres articles » (ceux d'aucun dossier) ; le champ de recherche et
« Filtres », au-dessus des dossiers, portent sur toute la bibliothèque, et leurs résultats passent avant les dossiers. Un article peut être dans plusieurs
dossiers, affichés en fin de sa ligne ; « Filtres » permet aussi de réduire la liste à un ou plusieurs dossiers.
« Tri » ordonne les articles par dernière lecture, ajout, publication, ou par ordre alphabétique (titre ou
premier auteur, A → Z ou Z → A, les dossiers étant alors rangés par nom dans le même sens).

## Zotero

Réglages, rubrique « Zotero » : avec une clé personnelle de l'API web de Zotero (créée sur
zotero.org/settings/keys/new, accès à la bibliothèque en écriture), la bibliothèque et les dossiers se
synchronisent dans les deux sens avec une collection « arXivReader » de Zotero, dont chaque dossier est une
sous-collection. Un article ajouté, rangé ou retiré d'un côté l'est de l'autre ; un élément Zotero rangé dans
« arXivReader » avec un identifiant arXiv est importé d'arXiv. Aucun élément Zotero n'est supprimé : un article
retiré est seulement sorti de ces collections (ses autres collections restent), et un article déjà dans Zotero est
repris, pas dupliqué. Supprimer un dossier supprime sa sous-collection (pas ses éléments), et inversement.
Synchronisation automatique (réglage « Synchroniser automatiquement ») peu après chaque changement et toutes les dix
minutes, ou « Synchroniser maintenant ». Par prudence, une synchronisation automatique qui retirerait beaucoup
d'articles d'un coup (ou verrait disparaître des sous-collections) ne change rien et attend « Synchroniser
maintenant ». `ARXIVREADER_NO_ALERTS=1` coupe aussi la synchronisation automatique (essais).

## Parus en revue

Dans la bibliothèque et le feed, « Filtres » → *Parution en revue* garde les articles parus en revue, ou ceux qui
ne sont pas signalés comme tels ; la revue s'affiche sur la carte (« Paru : … »). D'après la référence (ou le
DOI) indiquée sur arXiv par les auteurs, sinon d'après Semantic Scholar, interrogé par lots et redemandé après
30 jours pour un article encore non signalé. Beaucoup d'auteurs ne mettent pas arXiv à jour : un article non
signalé n'est pas forcément inédit.

## Premier lancement

L'interface démarre dans la langue de macOS (le français s'il vient en premier, sinon l'anglais) ; l'assistant
ci-dessous permet de passer aussitôt du français à l'anglais, comme les réglages ensuite.
Quand la bibliothèque et le profil sont vides (installation neuve), un assistant retrouve vos articles sur arXiv
(identifiant d'auteur arXiv ou ORCID, le plus sûr ; ou nom : les homonymes au prénom différent ou d'un autre
domaine sont décochés d'office, à vous de décocher les autres), propose les catégories du feed
(d'après vos articles) et les auteurs à suivre (coauteurs, auteurs les plus cités par vos articles), puis remplit
le profil ; il peut aussi ajouter vos articles à la bibliothèque. Relancé depuis le Profil (« Assistant de
démarrage »).

## Feed (étape 2)

Menu **Feed** en haut, quatre onglets :
- **Nouveautés arXiv** : annonces quotidiennes des catégories suivies (math.DG, math.CV, math.SP,
  math-ph par défaut, modifiables dans les réglages), **triées par pertinence pour votre profil**,
  avec la raison affichée (« auteur suivi », « proche de … », mot-clé) ; les articles peu pertinents
  sont réduits à une ligne (clic pour déplier). Un clic sur le titre ouvre l'article sans l'ajouter à la
  bibliothèque (cache vidé après 30 jours sans lecture) ; le **signet** l'ajoute ou l'en retire
  (aussi dans la barre du lecteur). En tête : les nouveaux articles des **auteurs suivis**,
  même hors de vos catégories ; un homonyme (même nom, autre personne) ? 👎 sur son article → « Pas ce … (homonyme) » :
  l'article n'est plus signalé, et l'auteur peut n'être suivi que dans les catégories des articles gardés (modifiables
  sous son nom, dans le Profil). Tri « Ordre arXiv » possible. 👍 / 👎 affinent le tri. Les flèches de part et d'autre de la date
  d'annonce mènent aux jours précédents ; au-delà d'une semaine, ils viennent de l'API de recherche
  d'arXiv, qui donne les nouveaux articles et les croisés mais pas les nouvelles versions.
- **Recommandations** : Semantic Scholar (API publique, sans compte), à partir du profil et des 👍.
- **Profil** : vos articles, articles et livres de référence, auteurs suivis, mots-clés, en rubriques
  repliables (fermées par défaut, état gardé) avec une recherche dans chacune.
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
Les identifiants arXiv de la bibliographie (« arXiv:2101.00001 ») ouvrent l'article cité dans un nouvel onglet
(⌘-clic : en arrière-plan), qu'il soit dans la bibliothèque ou non ; l'article lu reste dans le sien.

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

## PDF de l'ordinateur

Les articles absents d'arXiv (versions publiées, thèses, notes) peuvent rejoindre la bibliothèque : bouton
« Importer un PDF… » à côté d'« Importer », ou glisser des fichiers PDF sur la barre d'import ou la bibliothèque. Titre et auteurs
viennent du PDF (ses métadonnées, sinon sa première page), sont proposés à la vérification à l'import, et se
corrigent ensuite depuis le lecteur ou la carte de l'article (« Modifier titre et auteurs »). Le PDF se lit dans le lecteur avec surlignages, notes et Claude (questions sur un passage
sélectionné, ou sur tout l'article à partir de son texte extrait). Pas d'énoncés, de dépendances ni de
bibliographie (pas de source TeX). Un PDF téléchargé d'arXiv (mention « arXiv:… » sur sa première page)
n'est pas importé d'emblée : l'application propose d'abord d'importer à la place sa version arXiv, qui offre
toutes les fonctions (pas de doublon). Sinon, si son DOI ou son titre est retrouvé sur arXiv (Semantic
Scholar), elle propose d'ouvrir la version arXiv après l'import. Le fichier reste sur l'ordinateur, dans le
dossier de la bibliothèque.

## Fichiers TeX (articles ou livres en cours d'écriture)

Le même bouton (« Importer un PDF ou un fichier TeX… ») prend aussi le fichier `.tex` principal d'un document en
cours d'écriture, pour le relire avec toutes les fonctions du lecteur ; dans l'application, on peut aussi glisser le
fichier sur la barre d'import ou la bibliothèque (avec le `.pdf` du même nom, celui-ci est lu tel quel au lieu de
compiler). L'application le compile avec latexmk (sinon
pdflatex, BibTeX ou Biber, puis pdflatex), dans un dossier de travail à elle : rien n'est écrit dans votre dossier.
Énoncés, preuves, dépendances et bibliographie sont lus dans la source, et Claude reçoit la source TeX. Les `.bib`
sont cherchés dans le dossier du fichier, puis dans votre installation TeX ; un `.bib` introuvable est signalé et
peut être choisi à la main (retenu). **Recompiler et recharger** (barre du lecteur) relit la source : les notes
d'un énoncé le suivent (retrouvé par son `\label`, sinon par son texte), et les passages surlignés sont retrouvés là où ils ont bougé. L'onglet
Énoncés commence par des vérifications : erreurs LaTeX, renvois et citations non définis, énoncés sans preuve
trouvée, lemmes jamais utilisés. Un livre (avec `\chapter`) a ses énoncés classés par chapitre, et le graphe montre
un chapitre à la fois. Sans TeX (MacTeX, MiKTeX ou TeX Live), le PDF compilé à côté du fichier (`main.pdf` pour
`main.tex`) est lu tel quel. Le fichier se choisit dans la fenêtre de l'application (son chemin est gardé pour le
recompiler). Comme latexmk dans un terminal, la compilation suit le `latexmkrc` du dossier s'il en a un : n'importez
que des documents de confiance.

## Version HTML expérimentale (LaTeXML)

Un fichier TeX importé, ou un article d'arXiv sans version HTML mais avec sa source TeX, s'ouvre dans son PDF. Le
bouton **HTML β** en fait une version HTML sur votre ordinateur avec [LaTeXML](https://math.nist.gov/~BMiller/LaTeXML/)
(l'outil qu'arXiv utilise pour ses propres pages HTML), à la demande seulement. La première fois, une courte note dit
qu'elle est expérimentale et peut contenir des erreurs (formules, figures, numérotation) : le PDF fait foi. La
conversion se fait en arrière-plan (de quelques secondes à quelques minutes) pendant qu'on lit le PDF ; la version
HTML s'ouvre ensuite, avec les mêmes notes sur les énoncés que dans le PDF. Elle est gardée jusqu'à la prochaine
compilation du fichier TeX ou une nouvelle version sur arXiv. Les figures pgfplots (courbes, surfaces), que LaTeXML
met très longtemps à calculer, sont remplacées par une mention renvoyant au PDF. Les figures au format PDF sont
d'abord converties en images (par macOS lui-même, ou par le Ghostscript fourni avec TeX sous Windows) ; une
bibliographie perdue est refaite d'après le `.bbl` (celui de la compilation, ou celui que fait BibTeX). Il faut LaTeXML : `brew install
latexml` sur Mac, `apt install latexml` sous Linux ; sous Windows, quelques étapes (Perl, puis LaTeXML), voir
[son guide](https://math.nist.gov/~BMiller/LaTeXML/get.html).

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
- **Dans le PDF** (articles lus dans leur PDF, PDF importés) : la même barre sur un passage sélectionné, plus
  « Demander à Claude » et « Copier le texte » ; ces surlignages figurent dans l'onglet Notes avec leur page.
- **Zones d'un PDF** (formules, figures, articles numérisés dont les formules n'ont pas de texte sélectionnable) :
  ⌥ + glisser (Alt + glisser sous Windows), ou le bouton zone de la barre du PDF, puis la même barre : surlignage
  et note (vignette dans l'onglet Notes), « Demander à Claude » avec l'image de la zone, ou « Copier la zone en LaTeX »
  (Claude la transcrit). Ces deux derniers seulement avec un assistant qui lit les images (Claude, Codex, Gemini).
  Dans un PDF sans texte (scan), chaque question part aussi avec les pages autour, en images (7 avant, 3 après la
  zone, ou la page affichée) : l'assistant connaît ainsi les notations et les résultats qui précèdent.
- **Documents longs** (livres, au-delà de 150 pages environ) : l'assistant reçoit le début (sommaire, introduction,
  notations) et une fenêtre autour de la question (page de la zone ou page qu'on lit, sinon énoncé ou passage cité),
  plus avant qu'après ; les pages omises sont signalées. Un article part toujours en entier.
- **Énoncés et preuves** : l'icône de note en marge (au survol) ouvre une note et, pour un énoncé,
  l'état de lecture *compris / à revoir / vérifié* (affiché dans le titre et la liste des énoncés).
- **Notes sans passage** : clic droit → « Ajouter une note », en HTML (sur le paragraphe ; dans un énoncé ou une
  preuve, sa note) ou dans le PDF (une icône dans la marge de la page, à cette hauteur) ; une note laissée vide disparaît.
- **Marque-pages** : clic droit → « Ajouter un marque-page » (ou ⌘D), avec un nom au choix. Un ruban rouge marque l'endroit dans la marge ; tous sont listés en tête de l'onglet Notes
  (un clic y ramène, en passant du HTML au PDF au besoin). Un marque-page posé sur une version arXiv antérieure est
  signalé (« posé sur la v1 ») ; l'ouvrir et l'enregistrer le confirme pour la version actuelle.
- **Onglet Notes** du panneau : progression de lecture, fiche de lecture de l'article, toutes les
  annotations dans l'ordre de l'article (un clic y ramène).
- Notes en Markdown (**gras**, *italique*, listes, citations) avec formules `$…$`, `$$…$$`,
  enregistrées automatiquement. Un article annoté n'est jamais effacé du cache.
- **Import** : Réglages > Lecture > « Importer des notes… » relit les fichiers exportés par arXivReader (un `.md` ou le
  `.zip`, qui liste aussi toute la bibliothèque et ses dossiers), sur cet ordinateur ou un autre : les articles et
  dossiers absents sont ajoutés, et une note déjà présente
  n'est remplacée que par une version plus récente. Le dossier d'export peut être changé (Réglages > Lecture > Dossier
  d'export), par exemple pour un dossier synchronisé par iCloud Drive ou Dropbox : l'archive y est alors écrite, en
  remplaçant la précédente. Chaque export est signé par l'ordinateur qui l'a fait : un fichier
  qui ne vient pas d'arXivReader, ou modifié depuis, est refusé ; un autre ordinateur est accepté une fois (son
  empreinte est dans ses réglages).
- **Export** : « Exporter les notes (Markdown) » en bas de l'onglet Notes (fiche de lecture, énoncés et preuves
  avec leur état, passages surlignés par section, formules en LaTeX) ; Réglages > Lecture : toutes les notes,
  un fichier par article, dans une archive .zip (dossier Téléchargements).

## Claude sur l'article (étape 4)

- **Connexion, une seule fois** : double-clic sur `Connecter Claude.command` (ou `./arxivreader login`).
  La page de connexion Claude s'ouvre dans le navigateur ; c'est votre abonnement Claude qui est utilisé,
  sans clé d'API (l'usage compte dans ses limites).
- **Énoncé** (onglet Dépendances, ou note en marge) : *Expliquer*. **Preuve** : *Détailler la preuve*
  (étapes numérotées, arguments ajoutés marqués **[added]**, points douteux **⚠**) et *Auditer la preuve*
  (verdict, problèmes avec leur gravité, hypothèses utilisées, dépendances).
- **Passage** : le sélectionner, puis ✦ ; on peut ajouter une question.
- **Une conversation en trois fils** : *Questions* (questions sur l'article ; *↩ Répondre* sous une réponse pose
  une question de suite, rappelée au-dessus du champ et dans la question), *Résumé* et *Relecture* (le résumé
  structuré ou la relecture critique, lancés depuis la barre du bas, puis les questions posées dessus, dans le même
  onglet). Le plus ancien en haut, la saisie en bas ; Entrée envoie, Maj+Entrée va à la ligne. Boutons ↑ et ↓ en
  haut et en bas à droite : début ou fin du fil ; chaque onglet se rouvre là où on l'a laissé. *⋯* sous une
  réponse : copier l'échange, ou le supprimer (question et réponse ; un résumé ou une relecture part avec les
  questions posées dessus). Le bouton ↗ en haut (*Envoyer à Claude*, ou à ChatGPT, à Gemini) ouvre une nouvelle
  conversation dans l'application ou le site de l'assistant, avec les références de l'article, et copie son texte
  complet dans le presse-papiers.
- **Relecture croisée**, quand un deuxième assistant est prêt (Claude, Codex, Gemini ou un modèle local) :
  *Relecture simple* | *Relecture croisée* au-dessus du bouton de l'onglet *Relecture*, puis l'assistant, le modèle et le niveau
  de réflexion de chaque relecteur. Les deux assistants relisent l'article chacun de leur côté, le second compare
  les deux relectures, le premier répond à cette comparaison et rédige la synthèse : chaque point porte l'étiquette
  *Les deux*, le nom de l'assistant qui l'a seul relevé, *Désaccord* (les deux positions, et ce qui trancherait) ou
  *Retiré*. Les étapes précédentes restent repliées au-dessus. 4 demandes ; l'article part chez les deux
  assistants, sans son texte invisible. Une relecture faite, *Refaire* rouvre ce même choix (simple ou croisée,
  relecteurs), avec *Annuler* pour revenir aux questions.
- Réponses en direct, formules rendues, renvois (« Lemma 2.3 », « (2.11) ») cliquables ; *Ajouter à mes
  notes* les copie dans la note de l'énoncé ou de la preuve (sinon dans la fiche de lecture). Assistant (parmi
  ceux connectés), modèle (Opus, Fable, Sonnet, Haiku… selon votre abonnement) et niveau de réflexion au choix sous
  le champ de chaque sous-onglet de l'IA, un même choix partagé avec les réglages ; de même pour Codex et Gemini,
  d'après la liste de votre compte ou de votre clé. Les réponses sont conservées avec l'article.
- **Annotations proposées par Claude** : bouton *Annoter* sur un énoncé, ou toute demande du type « surligne… /
  annote… ». Claude joint une liste d'annotations (passages cités mot pour mot, couleur, note ; notes sur un
  énoncé ou une preuve) : chaque passage est retrouvé dans l'article, vous cochez ce que vous gardez,
  *Appliquer* les crée (marquées « Claude ») et *Annuler* les retire. Claude n'écrit jamais lui-même.
- **Liens dans les réponses** : énoncés, équations, sections (« Section 3.2 », « §2.1 », « Appendix A ») et
  références (« [12] », « Smith and Jones (2007) ») cités par l'assistant sont des liens, avec le même aperçu au
  survol que dans l'article.
- **Réponses adaptées à vous** : chaque demande emporte votre profil (vos articles et le début de leur résumé,
  mots-clés, auteurs suivis, articles et livres de référence, catégories du feed) et ce que vous écrivez de vous
  dans *Réglages → Assistant IA → Votre profil* (domaine, niveau, ce qu'il faut détailler). Les réglages montrent
  exactement ce qui est envoyé. Le profil adapte le niveau et signale les vrais liens avec vos travaux ; il n'adoucit
  jamais une relecture.
- Claude ne reçoit que le texte de l'article (le texte invisible est retiré), votre profil et la demande : aucun
  outil, ni fichiers, ni réseau, ni commandes, ni réglages Claude Code personnels ; rien n'est gardé côté Claude Code.

### Autres assistants (Réglages → Assistant IA)

Pour un collègue sans abonnement Claude, l'onglet IA fonctionne aussi avec :

- **Codex** (OpenAI, compte ChatGPT) : `npm install -g @openai/codex`, puis `codex login`. Appelé par
  `codex exec` sans commandes ni recherche web, sans réglages personnels ni session enregistrée, dans un
  dossier vide, bac à sable en lecture seule.
- **Gemini** (Google) : par l'API Gemini, avec une clé créée sur [Google AI Studio](https://aistudio.google.com/apikey)
  et collée dans Réglages → Assistant IA ; le modèle se choisit parmi ceux que la clé permet. Une simple
  requête, sans aucun outil. Offre gratuite limitée (et, sur celle-ci, Google peut utiliser les échanges pour
  améliorer ses modèles), sinon payante à l'usage. Gemini CLI n'accepte plus les comptes Google gratuits
  depuis juin 2026.
- **Modèle local** (Ollama, LM Studio, llama.cpp… : tout serveur compatible OpenAI sur `127.0.0.1`) : rien ne
  quitte l'ordinateur. Prévoir un contexte d'au moins 32 000 jetons (`OLLAMA_CONTEXT_LENGTH=32768 ollama serve`) ;
  seul le début des longs articles (~120 000 caractères) est transmis. Les petits modèles sont nettement moins
  fiables en mathématiques.

Codex est un agent : ses outils sont coupés par ses propres réglages, une garantie moins forte que pour
Claude et Gemini (appelés sans aucun outil). Son intégration suit sa documentation (version d'octobre 2026)
mais n'a pas été essayée avec un vrai compte.

## Sécurité

- Serveur sur `127.0.0.1` seulement ; noms d'hôte étrangers et requêtes d'autres sites refusés.
- HTML des articles nettoyé (scripts, formulaires, liens `javascript:` retirés) et politique CSP qui
  interdit tout script venant de l'article.
- Données (base SQLite + un dossier par article) dans `~/Library/Application Support/arXivReader` pour
  l'application, dans `library/` depuis le code source. Ailleurs, seulement ce que vous demandez : notes
  exportées (dossier Téléchargements) et, si vous activez la vérification hebdomadaire, son journal
  (`~/Library/Logs`) et sa tâche (`~/Library/LaunchAgents`).
- Connexions sortantes : arXiv (articles, nouveautés), Semantic Scholar (recommandations, papiers liés), GitHub
  (une fois par jour, numéro de la dernière version ; désactivable), l'assistant IA choisi, Zotero si une clé est
  donnée ; `./arxivreader check` appelle aussi chacun d'eux.

## Maintenance

L'application dépend de services qu'elle ne contrôle pas (arXiv et ses pages LaTeXML, Semantic Scholar, Zotero,
les assistants IA, GitHub) et de WebKit, qui change avec macOS. Les tests (`tests/`) simulent ces services ;
`./arxivreader check` les appelle vraiment, une requête minimale chacun, et vérifie la forme des réponses (✓ bon,
✗ changé ou en panne, – non vérifié : pas de clé, quota atteint). Elle signale aussi un changement de macOS ou de
WebKit depuis la fois précédente, avec les points à revoir à la main. `./arxivreader check --weekly` la lance
chaque lundi à 10 h par l'application installée (notification macOS en cas de problème ; journal dans
`~/Library/Logs/arXivReader-check.log`) ; `--no-weekly` l'arrête.

Versions : `requirements.txt` liste les paquets voulus, `requirements.lock` leurs versions exactes, testées, que
`.venv` installe et que l'application embarque ; `macos/build.sh` refuse de construire si `.venv` en diffère. Pour
mettre à jour un paquet, exprès et un à la fois : `.venv/bin/pip install -U <paquet>`, les tests, puis
`./arxivreader lock` (et committer). MathJax et pdf.js sont figés dans `static/vendor`.

Pour un assistant IA chargé de l'entretien : `MAINTENANCE.md` (règles, boucle de travail, publication, que faire
quand un service change, pièges connus), lu automatiquement par Claude Code (`CLAUDE.md`) et Codex (`AGENTS.md`).

## Organisation du code

```
app/            serveur Python (FastAPI)
  arxiv.py      identifiants, métadonnées (API arXiv), téléchargement du HTML
  sanitize.py   nettoyage du HTML
  structure.py  sommaire, énoncés, preuves, dépendances, bibliographie
  texsource.py  lecture de la source TeX (options de classe, police, étiquettes de listes)
  feed.py       nouveautés arXiv (RSS, OAI-PMH), recommandations Semantic Scholar, repérage d'identifiants
  journals.py   revue où un article est paru (arXiv, Semantic Scholar)
  search.py     recherche dans tout arXiv (onglet Recherche) ; related.py : papiers liés (graphe) ; zotero.py : synchronisation avec Zotero ; check.py : vérification des services extérieurs ; onboarding.py : assistant de premier lancement ; export.py : export des notes ; notesync.py : export signé et import des notes d'un ordinateur à l'autre
  folders.py    dossiers de la bibliothèque
  profile.py    profil de recherche, pré-remplissage, auteurs suivis (homonymes) ; relevance.py : tri par pertinence
  annotations.py  surlignages, notes, état de lecture
  ai.py         texte de l'article et consignes pour Claude, appel sans outils (SDK Claude Agent) ; pages d'un
                scan en images, fenêtre autour de la question pour les livres longs
  crossrefs.py  références qui sont des articles lus ici ; citations.py : articles qui citent les vôtres
  texstructure.py  structure tirée de la source TeX et pages du PDF (articles sans version HTML)
  providers.py  autres assistants : Codex (en ligne de commande), Gemini (API), modèle local (Ollama, LM Studio)
  localpdf.py   PDF importés de l'ordinateur (texte, titre, version arXiv)
  texfile.py    fichiers TeX de l'ordinateur (compilation, bibliographie, vérifications, identifiants stables)
  latexml.py    version HTML expérimentale faite par LaTeXML (fichiers TeX, articles d'arXiv sans HTML)
  library.py    import d'un article ; db.py : base SQLite ; main.py : routes web
macos/          application Mac : main.swift (fenêtre, onglets, serveur), icon.swift, build.sh, release.sh
windows/        application Windows : shell/ (fenêtre à onglets en C#, WebView2), launcher.py (serveur, construit
                par PyInstaller), smoke.ps1 (essai de l'application construite), build-requirements.txt
.github/        workflows : construction, tests et publication Windows (windows.yml), tests Linux (linux.yml)
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
