# arXivReader 1.14.0-beta.1 — copie .tex annotée

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
  très mathématique y est simplifié, et les émojis y deviennent « ? » (le commentaire garde tout).
- Une note sur un tableau ou une figure `[H]` déplacé à la page suivante peut apparaître en bas de la page d'avant.
- Les passages et zones placés approximativement le disent (« emplacement approximatif »), dans le commentaire comme
  dans la note en marge ; les notes proposées par l'IA gardent la mention « Claude (proposé, non vérifié) ».
- Les fichiers lus hors du dossier du projet (`\input{../…}`) ne sont pas copiés : la fenêtre le signale.
- Dossier du projet limité à 3000 fichiers / 300 Mo : placer le fichier .tex dans un dossier à part sinon.
