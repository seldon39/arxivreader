// Dessins que LaTeXML n'a pas su convertir (PSTricks, \special… : il ne reste que les étiquettes, sans un trait) :
// remplacés par la même zone du PDF, rendue par pdf.js. Le texte voisin du dessin est retrouvé dans le PDF mot à
// mot ; le dessin est juste au-dessus du texte qui le suit (sa légende, ou le paragraphe suivant), ou, s'il n'y a
// pas la place (texte suivant en haut de la page d'après), juste sous le texte qui le précède. La zone a la hauteur
// du dessin ; elle ne doit contenir aucun mot de prose du PDF hors des étiquettes du dessin (sinon ce n'est pas
// lui), puis ses marges blanches sont rognées. Rien de sûr (ou PDF illisible) : un avertissement renvoie au PDF.

import { htmlToPdf, pdfWords, words } from "./pdfsync.js";

const PDFJS = "/static/vendor/pdfjs/build/pdf.mjs";
const WORKER = "/static/vendor/pdfjs/build/pdf.worker.mjs";
const DRAWING = "path, line, rect, circle, ellipse, polygon, polyline, image, use, img"; // img : image placée dans un dessin TikZ
const SCALE = 3; // pixels par point du PDF (net sur un écran Retina, et zoomable)
const MARGIN = 8; // points ajoutés à la hauteur du dessin (celle de sa boîte TeX chez LaTeXML)
const LINE = 12; // hauteur d'une ligne de texte, sous le haut de la dernière ligne qui précède le dessin
const COUNT = 40; // mots de texte voisin cherchés dans le PDF
const SKIP = "math, mjx-container, .ltx_note_outer, .ltx_tag_equation";
const RENDER_TIMEOUT = 15000;

// Page affichée : un onglet en arrière-plan ne dessine pas (le rendu attendrait sans fin)
const visible = () => (document.hidden ? new Promise((resolve) => {
  const shown = () => { if (!document.hidden) { document.removeEventListener("visibilitychange", shown); resolve(); } };
  document.addEventListener("visibilitychange", shown);
}) : Promise.resolve());

const svgOf = (picture) => (picture.matches("svg") ? picture : picture.querySelector("svg"));

// Dessins vides : une vraie taille, des étiquettes peut-être, aucun trait
export function emptyPictures(root) {
  return [...root.querySelectorAll(".ltx_picture")].filter((picture) => {
    const svg = svgOf(picture);
    if (!svg || svg.querySelector(DRAWING)) return false;
    return Number(svg.getAttribute("width")) > 20 && Number(svg.getAttribute("height")) > 20;
  });
}

function textOf(element) {
  const copy = element.cloneNode(true);
  copy.querySelectorAll(SKIP).forEach((node) => node.remove());
  return copy.textContent;
}

// Mots de prose avant (`after` faux) ou après `anchor` dans l'article, hors formules ; parcours à partir de lui
function proseAround(anchor, root, after) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (anchor.contains(node) || node.parentElement.closest(SKIP) ? NodeFilter.FILTER_SKIP : NodeFilter.FILTER_ACCEPT),
  });
  walker.currentNode = anchor;
  const found = [];
  const step = () => (after ? walker.nextNode() : walker.previousNode());
  for (let node = step(); node && found.length < COUNT; node = step()) {
    if (after) found.push(...words(node.data));
    else found.unshift(...words(node.data));
  }
  return after ? found.slice(0, COUNT) : found.slice(-COUNT);
}

const sequence = (list) => ({ sequence: list.map((w) => ({ w, segment: 0 })) });

// Mots de prose du PDF dans la bande [bottom, top] de la page, hors étiquettes du dessin
function strangers(text, page, bottom, top, labels) {
  return text.sequence.filter((entry) => entry.page === page && entry.top > bottom && entry.top <= top && !labels.has(entry.w)).length;
}

// Endroit du dessin dans le PDF : { page, bottom } (bas de la zone, en points), ou null
async function place(picture, root, pdf, text) {
  const svg = svgOf(picture);
  const height = Number(svg.getAttribute("height")) * 0.75 + MARGIN; // px de LaTeXML → points
  const labels = new Set(words(textOf(svg)));
  const caption = picture.closest("figure")?.querySelector(".ltx_caption");
  const anchor = caption ? caption.closest("figure") : picture;
  const fits = async (page, bottom) => {
    const [, , , pageTop] = (await pdf.getPage(page)).view;
    return bottom >= 0 && bottom + height <= pageTop && strangers(text, page, bottom, bottom + height, labels) === 0;
  };
  // au-dessus du texte qui suit (la légende d'abord)
  const next = proseAround(anchor, root, true);
  const below = htmlToPdf(sequence(caption ? [...words(textOf(caption)), ...next].slice(0, COUNT) : next), text, 0);
  if (below && await fits(below.page, below.top)) return { page: below.page, bottom: below.top, height };
  // sous le texte qui précède : sa dernière ligne
  const previous = proseAround(anchor, root, false);
  if (previous.length < 4) return null;
  const start = htmlToPdf(sequence(previous), text, 0);
  if (!start) return null;
  const last = text.sequence[start.index + previous.length - 1];
  if (last?.w !== previous.at(-1)) return null;
  const bottom = last.top - LINE - height;
  return await fits(last.page, bottom) ? { page: last.page, bottom, height } : null;
}

// Zone de la page rendue, rognée à son contenu ; null si elle est blanche
async function crop(page, bottom, height) {
  const viewport = page.getViewport({ scale: SCALE });
  const [, yTop] = viewport.convertToViewportPoint(0, bottom + height);
  const [, yBottom] = viewport.convertToViewportPoint(0, bottom);
  const top = Math.max(0, Math.floor(yTop));
  const rows = Math.min(viewport.height, Math.ceil(yBottom)) - top;
  if (rows <= 0) return null;
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(viewport.width);
  canvas.height = rows;
  const context = canvas.getContext("2d");
  context.fillStyle = "#fff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  // rendu bloqué (page cachée…) : abandonné, l'avertissement le remplace
  const task = page.render({ canvasContext: context, viewport, transform: [1, 0, 0, 1, 0, -top], background: "#fff" });
  const timer = setTimeout(() => task.cancel(), RENDER_TIMEOUT);
  try { await task.promise; } finally { clearTimeout(timer); }
  // contenu : pixels non blancs
  const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
  let [minX, minY, maxX, maxY] = [canvas.width, canvas.height, -1, -1];
  for (let y = 0; y < canvas.height; y++) {
    for (let x = 0; x < canvas.width; x++) {
      const i = (y * canvas.width + x) * 4;
      if (data[i] < 235 || data[i + 1] < 235 || data[i + 2] < 235) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return null;
  const pad = 2 * SCALE;
  [minX, minY] = [Math.max(0, minX - pad), Math.max(0, minY - pad)];
  [maxX, maxY] = [Math.min(canvas.width - 1, maxX + pad), Math.min(canvas.height - 1, maxY + pad)];
  const out = document.createElement("canvas");
  out.width = maxX - minX + 1;
  out.height = maxY - minY + 1;
  out.getContext("2d").drawImage(canvas, minX, minY, out.width, out.height, 0, 0, out.width, out.height);
  return out;
}

// Taille du texte courant de la page, en points (médiane des morceaux de texte, pondérée par leur longueur)
async function bodySize(page) {
  const sizes = [];
  for (const item of (await page.getTextContent()).items) {
    const size = Math.abs(item.transform?.[3] || 0);
    if (size > 4 && item.str?.trim()) sizes.push(...Array(Math.min(item.str.length, 200)).fill(size));
  }
  sizes.sort((a, b) => a - b);
  return sizes[Math.floor(sizes.length / 2)] || 10;
}

// dessin vide de taille nulle juste avant (LaTeXML en met un devant chaque dessin non converti) : retiré aussi
function replace(picture, element) {
  const before = picture.previousElementSibling;
  if (before?.matches(".ltx_picture") && !before.querySelector(DRAWING) && !Number(svgOf(before)?.getAttribute("width"))) before.remove();
  picture.replaceWith(element);
}

// Remplace les dessins vides de `root` par leur zone du PDF (`pdfUrl`), à la taille du texte du lecteur ;
// `label` : texte de l'image et de son info-bulle ; `notice()` : avertissement mis à la place d'un dessin qu'on
// n'a pas pu reprendre ; `cancelled()` : le lecteur a été fermé entre-temps. Renvoie le nombre d'images mises
export async function replaceEmptyPictures(root, pdfUrl, { label = "", notice, cancelled = () => false } = {}) {
  const pictures = emptyPictures(root);
  if (!pictures.length) return 0;
  let replaced = 0;
  let pdf = null;
  try {
    const pdfjs = await import(PDFJS);
    pdfjs.GlobalWorkerOptions.workerSrc = WORKER;
    pdf = await pdfjs.getDocument({ url: pdfUrl, isEvalSupported: false, enableXfa: false }).promise;
    const text = await pdfWords({ pdfDocument: pdf });
    await visible();
    for (const picture of [...pictures]) {
      if (cancelled()) return replaced;
      const spot = await place(picture, root, pdf, text);
      const page = spot && await pdf.getPage(spot.page);
      const image = page && await crop(page, spot.bottom, spot.height).catch(() => null);
      if (!image || cancelled()) continue;
      const img = document.createElement("img");
      img.className = "ltx_graphics pdf-picture";
      img.src = image.toDataURL("image/png");
      img.alt = label;
      img.title = label;
      img.tabIndex = 0;
      img.style.width = `${(image.width / SCALE / await bodySize(page)).toFixed(2)}em`; // texte du PDF = texte du lecteur
      replace(picture, img);
      pictures.splice(pictures.indexOf(picture), 1);
      replaced++;
    }
  } catch {
    // PDF introuvable ou illisible : avertissements pour tous
  } finally {
    pdf?.destroy();
  }
  if (!cancelled() && notice) pictures.forEach((picture) => replace(picture, notice()));
  return replaced;
}
