# arXivReader 2.0 bêta — note d'essai

Version expérimentale `2.0.0-beta.1`, locale : branche `beta` (jamais poussée), application
`~/Applications/arXivReader Beta.app`, bibliothèque à part (`~/Library/Application Support/arXivReader Beta`, copie de
la bibliothèque stable du 11/11/2026, sans clés). La version 1.12 et sa bibliothèque ne sont pas touchées. Les
documents du dépôt (README, MAINTENANCE…) seront mis à jour seulement si la bêta est intégrée.

## Ce qui change

### Rigueur de l'assistant
- **Relectures et audits** : chaque point critique ou sérieux cite la ligne exacte concernée et, pour une étape jugée
  fausse ou douteuse, donne le plus petit cas à refaire à la main. Nouvelle rubrique « Results taken from the
  literature » : chaque résultat extérieur dont dépendent les preuves, avec son renvoi tel qu'imprimé et un statut —
  conforme à l'énoncé connu (de mémoire, à confirmer), semble mal cité (pourquoi), non vérifiable ici (quoi vérifier).
- **Vérifier par le calcul** (énoncé, passage sélectionné, zone du PDF) : l'assistant écrit un script Sage ou SymPy
  qui teste la formule sur des cas particuliers ; bouton « Copier le code ». L'application n'exécute jamais rien.
- **Ce que l'assistant a lu** : bandeau « Lu : n % du texte seulement (pages … non envoyées) » quand l'article a été
  coupé, et « Réponse coupée par l'assistant » quand la réponse s'arrête sur une limite. Une relecture coupée n'est
  plus reprise comme complète. La réponse en cours est enregistrée toutes les quelques secondes.
- **Relecture vérifiée** (onglet IA → Relecture → « Sorte de relecture ») : le même assistant, avec un modèle au choix
  (plus léger par défaut), tente de réfuter chaque point grave d'après le texte ; les points sortent « Confirmé »,
  retirés ou « Incertain ». Ligne de coût « ≈ n demandes de la taille de l'article » avant les lancements multiples.

### Lecture
- **Position jamais perdue** : aussi dans les PDF importés et les fichiers TeX, à la ligne près en HTML, enregistrée
  à la fermeture de l'onglet.
- **Retour / avant** : ⌥← et ⌥→ ramènent à l'endroit quitté, en HTML comme dans le PDF (liens internes du PDF compris).
- **Touches de lecture** : `j` / `k` énoncé suivant / précédent (aussi `]` / `[`), `p` plier ou déplier la preuve de
  l'énoncé lu, `?` la liste des raccourcis.
- **Bandeau « Preuve du … »** pendant la lecture d'une preuve (survol : l'énoncé ; clic : y aller), et l'énoncé
  correspondant marqué dans le panneau.

### Écrire et citer
- **BibTeX** : « Copier le BibTeX » pour l'article lu, sur les cartes de la bibliothèque, pour une sélection ou un
  dossier (« Exporter en .bib »), et sur une référence de bibliographie reconnue par son identifiant arXiv ou son DOI.
  Construit hors ligne ; `@article` seulement si la revue est connue ; rien n'est inventé.
- **Références** : « + Bibliothèque » sur une référence qui a un identifiant arXiv ; « Ajouter les n références arXiv
  à un dossier » (une par une, avec confirmation, arrêt possible).
- **Recherche de la bibliothèque** : plusieurs mots (tous présents dans l'article), "expression exacte", formules
  reconnues malgré espaces et accolades, articles lus dans leur PDF avec source TeX inclus.

### Sécurité des données
- **Copie quotidienne** de la base dans `backups/` (7 gardées, sans le cache des vecteurs), contrôle d'intégrité au
  démarrage (une base réellement abîmée est mise de côté et la dernière copie saine remise ; une base seulement
  occupée n'est jamais déplacée), refus clair d'une base écrite par une version plus récente. Réglages : date de la
  dernière copie et dossier.
- **Notes entre ordinateurs** : images des zones du PDF importées, suppressions transmises (la modification la plus
  récente gagne), une seule ligne par énoncé (doublons fusionnés sans perte).

### Application Mac
- Confirmation avant de quitter si une réponse de l'assistant est en cours ; onglets de la dernière session rouverts
  au lancement (seul l'onglet affiché se charge) ; « Rouvrir l'onglet fermé » ⌘⇧T ; ⌘+ ⌘− ⌘0 (taille du texte, ou zoom
  du PDF) ; ⌘1…⌘9 ; téléchargements sans passage au Finder ; message et bouton après un plantage répété de la page.

### Défauts corrigés (extraits)
Identifiants arXiv anciens (`math.DG/0211159`) normalisés ; alertes de citation au-delà de 100 citations ; index des
renvois entre articles mis en cache (plus de relecture de toute la bibliothèque à chaque ouverture) ; cache du feed
élagué après 60 jours ; erreurs hors ligne du PDF ; messages au-dessus des fenêtres modales ; échec d'enregistrement
d'un réglage signalé.

## Vérifié
- 579 tests automatiques ; migration d'une copie de la vraie base 1.12 sans perte (54 notes identiques).
- Dans WebKit (copie de l'application, bibliothèque d'essai) : touches j / k / ?, retour ⌥←, bandeau de preuve,
  position restaurée, BibTeX, recherche à deux mots, copie quotidienne, rendu d'une relecture et d'un script simulés
  (rubrique des résultats cités, bandeaux « coupée » et « lu à 62 % »), session de quatre onglets rouverte dans l'ordre.
- Relecture contradictoire de tout le diff : 35 remarques, corrigées (deux graves sur les données, refaites à la main).

## À vérifier à la main (non testé avec un vrai clic ou une vraie IA)
- « Copier le code » et « Copier le BibTeX » avec un vrai clic (mon clic simulé n'a pas accès au presse-papiers).
- Une vraie relecture : la rubrique des résultats cités, les extraits de ligne, la relecture vérifiée, la ligne de coût.
- « Vérifier par le calcul » sur un énoncé, puis le script dans Sage.
- ⌘Q pendant une réponse en cours ; ⌘⇧T ; ⌘+ / ⌘− dans le PDF ; téléchargement d'un export.
- « + Bibliothèque » et l'ajout groupé de références (requêtes réelles à arXiv).

## Limites connues
- Dans la vue PDF d'un article arXiv, `j` / `k` ne s'arrêtent que sur les énoncés dont la page est connue.
- ⌘[ / ⌘] restent l'historique des pages (menu Aller), pas le retour à l'endroit quitté (⌥← / ⌥→).
- Une image de zone d'une archive de notes n'est pas couverte par la signature de l'archive.
- Laissés de côté pour cette passe : PDF à côté du texte, réponses de l'assistant détachées de l'onglet.

## Revenir en arrière
Supprimer `~/Applications/arXivReader Beta.app` et `~/Library/Application Support/arXivReader Beta` ; la branche
`beta` se supprime avec `git branch -D beta`.
