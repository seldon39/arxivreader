# arXivReader 1.14.0-beta.6 — copie .tex annotée

Bêta locale, issue de la 1.13.3 : seulement sur cet ordinateur, rien sur GitHub. Elle a sa propre bibliothèque
(`~/Library/Application Support/arXivReader Beta`) et ne touche pas à l'application habituelle.

## Ce qui est nouveau

Pour un **fichier TeX importé** (pas pour les articles d'arXiv) : onglet **Notes** → **Copie .tex annotée…**.

On obtient une **copie du dossier du projet**, téléchargée en .zip, dans laquelle toutes les notes de l'article sont
insérées à côté de ce qu'elles concernent. Le projet d'origine n'est **jamais modifié** : seules des lignes sont
ajoutées, et seulement dans la copie.

Deux formes au choix :
- **Commentaires %** : chaque note devient un bloc `% [arXivReader] … % [/arXivReader]`. Le PDF compilé ne change pas.
- **Notes en marge** : les mêmes commentaires, plus une note en marge courte (paquet todonotes, chargé dans le
  préambule de la copie). Chaque ligne ajoutée porte la marque `[arXivReader]`.

**Nouveau en beta.2 — « Agrandir la marge des notes »** (case cochée par défaut, mode notes en marge) : le texte est
décalé vers la gauche (marge gauche de 1,5 cm), la colonne des notes fait de 4,5 à 6 cm, et la page est élargie à
droite si la place manque ; les notes sont en 7 pt. La largeur et la hauteur du texte ne changent pas : les lignes et
les pages se coupent comme dans l'original. Une page élargie s'imprime en « ajuster à la page ». Décocher la case
redonne la mise en page de la beta.1.

**Nouveau en beta.3 — « Renvoyer les notes longues en fin de document »** (case cochée par défaut, mode notes en
marge) : une note trop longue pour la marge y montre son début suivi de « … → note N, p. X » ; son texte entier est
sur une dernière page « Notes de relecture (arXivReader) », en taille normale (paragraphes, listes, formules), avec un
renvoi vers la page de la note (liens cliquables si le document charge hyperref). Les pages d'avant ne changent pas.
Il faut deux passes de LaTeX (latexmk les fait) : après une seule, les numéros de page affichent « ?? ».

**Nouveau en beta.4 — formules des notes** : dans les notes de fin de document, les formules sont rendues en LaTeX,
y compris les matrices et systèmes (`pmatrix`, `cases`, `aligned`… si le document charge amsmath), et le passage
cité aussi (il était recopié en code brut). Les lettres grecques et symboles tapés en Unicode (λ, ≤, ℝ, ²…) deviennent
des symboles. En marge, une longue formule impossible à recopier devient « [...] » au lieu de son code.

**Nouveau en beta.5 — renvois** : dans les notes en marge et en fin de document, les numéros écrits dans les
notes deviennent des commandes du document : « (4.75) » → `\eqref{…}`, « Lemma 4.2 » → `Lemma~\ref{…}`, « [2] » ou
« [Berndtsson03, Theorem 3] » → `\cite[Theorem 3]{Berndtsson03}` (et un `\cite`, `\ref`, `\eqref` écrit dans une note
reste une commande). La correspondance vient de la dernière compilation, et seulement pour les étiquettes encore
présentes dans la source : si vous renumérotez, les renvois suivent ; un numéro inconnu reste du texte. Dans le bloc de
commentaires, une ligne « Renvois : (4.75) = \eqref{…} ; … » donne les étiquettes correspondantes. Les numéros sont lus
tels que la note les écrit : une note qui parle du « Lemma 4.2 » d'un autre article, sans le citer entre crochets,
pointerait vers le vôtre.

**Corrigé en beta.5** (existe aussi en 1.13.3) : un énoncé qui contient une liste avec des `\item\label{…}` prenait
le numéro du dernier point (« Theorem 2 » au lieu de « Theorem 1.3 »). Les fichiers TeX importés sont ré-analysés à
l'ouverture ; si le numéro reste faux, cliquer sur **Recompiler**.

**Nouveau en beta.6 — Mistral** : Réglages → Assistant IA → **Mistral**, avec une clé d'API de Mistral AI (bouton
« Créer une clé (console Mistral) », à coller dans le champ). Comme Gemini : une simple requête sans outil, liste des
modèles lue avec la clé (les « -latest », Mistral Large par défaut), zones du PDF seulement avec les modèles qui lisent
les images, relecture croisée, « Envoyer à Le Chat ». Offre gratuite (Experiment) : vos échanges servent par défaut à
entraîner les modèles de Mistral, refusable dans sa console (menu Privacy) ; c'est rappelé dans les réglages.

Où vont les notes :
- note ou état d'un énoncé, ou d'une preuve : juste avant son `\begin{…}` ;
- passage surligné : retrouvé par son texte (avec l'aide de SyncTeX quand le même texte revient plusieurs fois) ;
- zone encadrée et note épinglée dans le PDF : grâce à SyncTeX (position approchée) ;
- fiche de lecture, marque-pages, et notes impossibles à placer : bloc en tête du fichier principal. Aucune note n'est
  perdue.

Avant de télécharger, la fenêtre dit combien de notes seront placées exactement, approximativement ou en tête.
L'archive contient aussi `LISEZMOI-arXivReader.txt` (résumé, avertissements, comment retirer les notes : chercher
`[arXivReader]`).

**SyncTeX** : la compilation produit désormais le fichier SyncTeX. Pour un fichier TeX déjà importé, cliquer
une fois sur **Recompiler** pour que les zones et notes épinglées soient bien placées.

## À essayer

1. Ouvrir un de vos fichiers TeX importés, cliquer sur Recompiler.
2. Ajouter quelques notes (énoncés, passages, zones, notes épinglées), éventuellement une proposition de l'IA acceptée.
3. Notes → Copie .tex annotée… : comparer les deux formes, ouvrir la copie dans votre éditeur et la compiler.

## Limites connues

- Les zones et notes épinglées sont placées à la ligne la plus proche selon SyncTeX : position approchée, surtout
  dans les marges ou près du pied de page.
- Un passage surligné qui contient surtout des formules peut ne pas être retrouvé : il va alors en tête du fichier.
- Les notes en marge sont omises dans les boîtes, les flottants et les formules (le commentaire reste) ; un texte
  très mathématique y est simplifié (« [...] »), et les émojis y deviennent « ? » (le commentaire garde tout).
- Une note sur un tableau ou une figure `[H]` déplacé à la page suivante peut apparaître en bas de la page d'avant.
- Les passages et zones placés approximativement le disent (« emplacement approximatif »), dans le commentaire comme
  dans la note en marge ; les notes proposées par l'IA gardent la mention « Claude (proposé, non vérifié) ».
- Les fichiers lus hors du dossier du projet (`\input{../…}`) ne sont pas copiés : la fenêtre le signale.
- Dossier du projet limité à 3000 fichiers / 300 Mo : placer le fichier .tex dans un dossier à part sinon.
