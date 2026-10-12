// Même endroit dans le HTML et dans le PDF : on retrouve le texte lui-même, mot à mot.
// Les ancres de hyperref ne suffisent pas (noms imprévisibles, thmtools, aucune ancre pour un paragraphe).
// De part et d'autre : suite des mots de prose de 3 lettres ou plus, en minuscules, sans accents ni tirets
// (les formules ne laissent que des lettres isolées, écartées ; les coupures « embed-/ding » sont recollées).
// Une fenêtre de quelques mots consécutifs est cherchée dans l'autre version ; parmi plusieurs endroits,
// le plus proche de l'estimation donnée par les ancres l'emporte.

const MIN_LETTERS = 3;
const WINDOWS = [6, 5, 4]; // longueurs essayées, de la plus sûre à la plus courte

export function words(text) {
  return text.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/(\p{L})[-‐‑](\p{L})/gu, "$1$2")
    .split(/[^a-z]+/).filter((word) => word.length >= MIN_LETTERS);
}

// Index : mot → positions dans la suite
function indexOf(sequence) {
  const index = new Map();
  sequence.forEach((entry, i) => {
    const list = index.get(entry.w);
    if (list) list.push(i); else index.set(entry.w, [i]);
  });
  return index;
}

// Positions où commence la fenêtre `needle` (liste de mots) dans `sequence`
function matches(sequence, index, needle) {
  return (index.get(needle[0]) || []).filter((start) => needle.every((w, k) => sequence[start + k]?.w === w));
}

// Position de `target` alignée sur le mot `from` de `source`. Des fenêtres de quelques mots consécutifs,
// prises à partir de `from`, sont cherchées dans `target` ; chaque endroit trouvé vote pour un
// alignement (position dans `target` moins décalage dans `source`). L'alignement qui réunit le plus de
// fenêtres l'emporte : un titre repris dans la table des matières, ou une phrase banale, ne pèse
// qu'une voix, le texte suivi en pèse plusieurs. `segment(i)` : une fenêtre n'enjambe pas une formule
// (ses mots manquent en face) ; `distance(entry)` départage deux alignements à égalité.
function align(source, from, target, index, { segment = () => 0, distance = () => 0 } = {}) {
  const votes = [];
  let matched = 0;
  for (let k = 0; k < 80 && from + k < source.length && matched < 12; k++) {
    for (const size of WINDOWS) {
      const slice = source.slice(from + k, from + k + size);
      if (slice.length < size || segment(from + k) !== segment(from + k + size - 1)) continue;
      const found = matches(target, index, slice.map((entry) => entry.w));
      if (!found.length) continue;
      if (found.length <= 8) { // fenêtre trop fréquente : sans information
        matched++;
        for (const position of found) {
          const shift = position - k;
          let vote = votes.find((v) => Math.abs(v.shift - shift) <= 20);
          if (!vote) votes.push(vote = { shift, count: 0, position, k });
          vote.count++;
          if (k < vote.k) Object.assign(vote, { position, k });
        }
      }
      break;
    }
  }
  if (!votes.length) return -1;
  votes.sort((a, b) => b.count - a.count || distance(target[a.position]) - distance(target[b.position]));
  return Math.max(0, Math.min(target.length - 1, votes[0].position - votes[0].k));
}

// ---------- Texte du PDF (pdf.js) ----------

// Suite des mots du PDF avec leur page et leur hauteur ; en-têtes et pieds de page répétés retirés
export async function pdfWords(app) {
  const pages = [];
  for (let n = 1; n <= app.pdfDocument.numPages; n++) {
    const page = await app.pdfDocument.getPage(n);
    const content = await page.getTextContent();
    const lines = [];
    let carry = "";
    for (const item of content.items) {
      if (typeof item.str !== "string") continue;
      let text = carry + item.str;
      carry = "";
      if (item.hasEOL && /[A-Za-z][-‐‑]$/.test(text)) { carry = text.slice(0, -1); continue; } // mot coupé en fin de ligne
      const y = item.transform[5];
      const size = Math.abs(item.transform[3]) || 10;
      lines.push({ text, y, top: y + size, page: n });
    }
    if (carry) lines.push({ text: carry, y: 0, top: 0, page: n });
    pages.push(lines);
  }
  // ligne revenant sur au moins trois pages à la même hauteur (titre courant, numéro…) : écartée
  const key = (line) => `${Math.round(line.y / 4)}|${words(line.text).join(" ")}`;
  const seen = new Map();
  for (const lines of pages) for (const k of new Set(lines.map(key))) seen.set(k, (seen.get(k) || 0) + 1);
  const sequence = [];
  for (const lines of pages) {
    for (const line of lines) {
      if (pages.length >= 3 && seen.get(key(line)) >= 3) continue;
      for (const w of words(line.text)) sequence.push({ w, page: line.page, top: line.top });
    }
  }
  return { sequence, index: indexOf(sequence) };
}

// ---------- Texte du HTML ----------

const SKIP = "mjx-container, math, .ltx_note_outer, .ltx_tag_equation, .eq-number, .proof-toggle, .block-note, .tex-statements, .tex-macros";

// Suite des mots de prose de l'article, chacun avec son nœud de texte ; « segment » change à chaque formule
export function htmlWords(root) {
  const sequence = [];
  let segment = 0;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
    acceptNode: (node) => (node.nodeType === 1 && node.matches(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.nodeType === 1) continue;
    // formule juste avant ce texte : nouveau segment
    const previous = node.previousSibling;
    if (previous?.nodeType === 1 && previous.matches?.(SKIP)) segment++;
    const re = /[\p{L}\-‐‑]+/gu;
    for (let m = re.exec(node.data); m; m = re.exec(node.data)) {
      for (const w of words(m[0])) sequence.push({ w, node, offset: m.index, segment });
    }
    if (node.nextSibling?.nodeType === 1 && node.nextSibling.matches?.(SKIP)) segment++;
  }
  return { sequence, index: indexOf(sequence) };
}

// Hauteur à l'écran d'un mot du HTML
export function wordTop(entry) {
  const range = document.createRange();
  range.setStart(entry.node, Math.min(entry.offset, entry.node.length));
  range.setEnd(entry.node, Math.min(entry.offset + 1, entry.node.length));
  return range.getBoundingClientRect().top;
}

// ---------- Correspondances ----------

// HTML → PDF : à partir du mot `from` du HTML ; { page, top } dans le PDF, ou null.
// `expected` : page estimée par les ancres (départage)
export function htmlToPdf(html, pdf, from, expected = null) {
  const at = align(html.sequence, from, pdf.sequence, pdf.index, {
    segment: (i) => html.sequence[i]?.segment,
    distance: (entry) => (expected == null ? 0 : Math.abs(entry.page - expected)),
  });
  if (at < 0) return null;
  const entry = pdf.sequence[at];
  return { page: entry.page, top: entry.top + 4 };
}

// PDF → HTML : à partir de l'endroit lu { page, top } ; mot du HTML, ou null.
// `near(entry)` : distance à l'estimation donnée par les ancres (départage)
export function pdfToHtml(pdf, html, location, near = () => 0) {
  // premier mot du PDF sous le haut de la fenêtre
  const from = pdf.sequence.findIndex((entry) => entry.page > location.page
    || (entry.page === location.page && (location.top == null || entry.top <= location.top + 2)));
  if (from < 0) return null;
  const at = align(pdf.sequence, from, html.sequence, html.index, { distance: near });
  return at < 0 ? null : html.sequence[at];
}
