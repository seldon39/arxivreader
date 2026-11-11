// Position de lecture gardée avec l'article (colonne papers.position, 200 caractères) :
//   « S3.Thm1 »        identifiant d'un élément (anciennes valeurs : toujours valables)
//   « S3.Thm1@120 »    identifiant et décalage en pixels sous le début de l'élément
//   « pdf:47:312.5 »   page et hauteur dans la page (coordonnées du PDF), pour un article sans HTML
// Un identifiant HTML ne contient ni « @ » ni « : » en tête de forme « pdf: » : une ancienne version de
// l'application lit ces valeurs comme un identifiant inconnu et n'en fait rien.

export function encodePosition(id, offset = 0) {
  const px = Math.round(offset);
  return px > 0 && px < 100000 ? `${id}@${px}` : id;
}

export function encodePdfPosition(page, top = null) {
  return `pdf:${Math.max(1, Math.round(page))}:${top == null ? "" : Math.round(top * 10) / 10}`;
}

// → { id, offset } | { pdf: { page, top } } | null
export function decodePosition(value) {
  if (!value || typeof value !== "string") return null;
  const pdf = /^pdf:(\d+):(-?\d+(?:\.\d+)?)?$/.exec(value);
  if (pdf) return { pdf: { page: Number(pdf[1]), top: pdf[2] === undefined ? null : Number(pdf[2]) } };
  const at = value.lastIndexOf("@");
  if (at > 0 && /^\d+$/.test(value.slice(at + 1))) return { id: value.slice(0, at), offset: Number(value.slice(at + 1)) };
  return { id: value, offset: 0 };
}

// Enregistrement différé (le défilement envoie sans cesse une position) : `save(valeur)` attend `delay` ms de calme,
// `flush()` envoie tout de suite ce qui attend (fermeture de l'onglet, changement de page) ; une valeur déjà envoyée
// n'est pas renvoyée
export function positionSaver(send, delay = 1500) {
  let timer = null;
  let pending = null;
  let sent = null;
  const flush = () => {
    clearTimeout(timer);
    timer = null;
    const value = pending;
    pending = null;
    if (value == null || value === sent) return;
    sent = value;
    send(value);
  };
  return {
    save(value) {
      pending = value;
      clearTimeout(timer);
      timer = setTimeout(flush, delay);
    },
    flush,
  };
}
