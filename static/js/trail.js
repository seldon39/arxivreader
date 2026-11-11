// Un seul historique de sauts pour le HTML et le PDF (« Retour », ⌥← / ⌥→, ⌘[ / ⌘]) et les touches de lecture.
// Fonctions pures (testées hors navigateur : tests/test_trail.py) ; reader.js fait le défilement.
//
// Un endroit est une ancre, jamais un nombre de pixels (la mise en page change avec la taille de la police) :
//   { kind: "html", id, off, y }   élément, décalage dans l'élément ; y : hauteur, en secours (élément masqué)
//   { kind: "pdf", page, top }     page et hauteur dans la page (coordonnées du PDF)

const LIMIT = 60;  // sauts gardés
const CLOSE = 40;  // deux endroits plus proches que cela (pixels HTML, points PDF) sont le même

export function samePlace(a, b) {
  if (!a || !b || a.kind !== b.kind) return false;
  if (a.kind === "pdf") return a.page === b.page && Math.abs((a.top ?? 0) - (b.top ?? 0)) < CLOSE;
  return a.id === b.id && Math.abs((a.off ?? 0) - (b.off ?? 0)) < CLOSE;
}

export class Trail {
  constructor() {
    this.past = [];
    this.future = [];
  }

  get canBack() { return this.past.length > 0; }
  get canForward() { return this.future.length > 0; }

  // saut depuis `place` : on pourra y revenir ; ce qui était « devant » est oublié (comme un navigateur)
  visit(place) {
    if (!place) return;
    this.future = [];
    if (samePlace(this.past.at(-1), place)) return;
    this.past.push(place);
    if (this.past.length > LIMIT) this.past.shift();
  }

  // → endroit où revenir (ou null) ; `here` : l'endroit quitté, qu'un « suivant » retrouvera
  back(here) {
    const place = this.past.pop() || null;
    if (place && here && !samePlace(this.future.at(-1), here)) this.future.push(here);
    return place;
  }

  forward(here) {
    const place = this.future.pop() || null;
    if (place && here && !samePlace(this.past.at(-1), here)) this.past.push(here);
    return place;
  }
}

// Touche d'historique : -1 (⌥←, ⌘[), +1 (⌥→, ⌘]), 0 sinon. Les touches avec un autre modificateur ne comptent pas.
export function trailKey(event) {
  if (event.shiftKey || event.isComposing) return 0;
  if (event.altKey && !event.metaKey && !event.ctrlKey) return event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : 0;
  if (event.metaKey && !event.altKey && !event.ctrlKey) {
    if (event.code === "BracketLeft" || event.key === "[") return -1;
    if (event.code === "BracketRight" || event.key === "]") return 1;
  }
  return 0;
}

// Touche de lecture sans modificateur : "next" / "prev" (énoncé suivant, précédent), "proof" (preuve de l'énoncé lu),
// "help" (liste des touches), sinon null. Une seule constante : facile à retirer.
// j / k : mêmes gestes sur tout clavier (« ] » et « [ » demandent ⌥⇧ sur un clavier AZERTY)
export const READING_KEYS = { j: "next", k: "prev", "]": "next", "[": "prev", p: "proof", "?": "help" };
export function readingKey(event) {
  if (event.altKey || event.metaKey || event.ctrlKey || event.isComposing) return null;
  return READING_KEYS[event.key] || null;
}

// Énoncé voisin de la ligne de lecture. `items` : [{ id, y }] dans l'ordre de l'article, `line` : hauteur de la
// ligne de lecture dans la même échelle. `from` : énoncé où le dernier saut a mené (défilement doux encore en cours :
// la ligne de lecture n'y est pas encore). Après un saut, l'énoncé arrive 12 px au-dessus de la ligne : « précédent »
// ne le reprend donc pas (marge 24).
export function neighbourStatement(items, line, dir, from = null) {
  if (from) {
    const i = items.findIndex((item) => item.id === from);
    if (i >= 0) return items[i + dir] || null;
  }
  if (dir > 0) return items.find((item) => item.y > line + 4) || null;
  for (let i = items.length - 1; i >= 0; i--) if (items[i].y < line - 24) return items[i];
  return null;
}

// Énoncé dont la preuve contient l'élément : `owners` (id d'un bloc de preuve → énoncé) pour les blocs, `passages`
// ([{ node, start, end }], éléments) pour un passage indiqué. Remonte du plus proche au plus lointain.
export function proofOwnerOf(element, owners, passages = []) {
  for (let el = element; el; el = el.parentElement) {
    if (el.id && owners.has(el.id)) return owners.get(el.id);
  }
  for (const { node, start, end } of passages) {
    if (!start || !end) continue;
    const after = start === element || start.contains(element) || start.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING;
    const before = end === element || end.contains(element) || end.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_PRECEDING;
    if (after && before) return node;
  }
  return null;
}
