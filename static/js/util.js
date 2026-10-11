// Petits outils communs : création d'éléments, icônes, messages.

import { t } from "./i18n.js";

// Attributs « vrai ou faux » : présents seulement si la valeur est vraie (0 ou "" ne les posent pas)
const BOOLEAN_ATTRIBUTES = new Set(["disabled", "checked", "hidden", "open", "selected", "required", "readonly", "multiple", "autofocus"]);

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (BOOLEAN_ATTRIBUTES.has(key)) { if (value) el.setAttribute(key, ""); continue; }
    if (key.startsWith("aria-") && typeof value === "boolean") { el.setAttribute(key, String(value)); continue; } // aria-pressed="false"
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "html") el.innerHTML = value; // réservé aux icônes internes
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const path = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;

export const icons = {
  menu: path("M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z"),
  back: path("M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20z"),
  panel: path("M4 4h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Zm10 2v12h5V6h-5ZM5 6v12h7V6H5Z"),
  minus: '<svg viewBox="0 0 24 24" aria-hidden="true"><text x="3" y="17" font-size="13" font-family="Georgia,serif">A</text><path d="M15 12h7v2h-7z"/></svg>',
  plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><text x="1" y="19" font-size="18" font-family="Georgia,serif">A</text><path d="M18 9h2v3h3v2h-3v3h-2v-3h-3v-2h3z"/></svg>',
  fold: path("M7 9l5-5 5 5-1.4 1.4L12 6.8l-3.6 3.6zm0 6 1.4-1.4 3.6 3.6 3.6-3.6L17 15l-5 5z"),
  external: path("M14 3h7v7h-2V6.4l-9.3 9.3-1.4-1.4L17.6 5H14zM5 5h6v2H5v12h12v-6h2v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z"),
  pdf: path("M6 2h9l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm8 1.5V8h4.5M8 13h2.5a1.5 1.5 0 0 1 0 3H9v2H8zm1 1v1h1.5a.5.5 0 0 0 0-1zm4-1h2a2 2 0 0 1 2 2v1a2 2 0 0 1-2 2h-2zm1 1v3h1a1 1 0 0 0 1-1v-1a1 1 0 0 0-1-1z"),
  refresh: path("M17.7 6.3A8 8 0 1 0 20 12h-2a6 6 0 1 1-1.8-4.2L13 11h7V4z"),
  trash: path("M9 3h6l1 2h4v2H4V5h4zm-3 6h12l-1 12H7zm4 2v8h1.5v-8zm3.5 0v8H15v-8z"),
  thumbOff: path("M9 21h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.58 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2zM9 9l4.34-4.34L12 10h9v2l-3 7H9V9zM1 9h4v12H1z"),
  thumbOn: path("M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"),
  bookmarkOff: path("M17 3H7c-1.1 0-1.99.9-1.99 2L5 21l7-3 7 3V5c0-1.1-.9-2-2-2zm0 15-5-2.18L7 18V5h10v13z"),
  bookmarkOn: path("M17 3H7c-1.1 0-1.99.9-1.99 2L5 21l7-3 7 3V5c0-1.1-.9-2-2-2z"),
  chevronLeft: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14.5 5.5 8 12l6.5 6.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  chevronRight: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9.5 5.5 16 12l-6.5 6.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  person: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.6" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M4.5 20c.9-3.7 3.9-5.6 7.5-5.6s6.6 1.9 7.5 5.6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  key: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="12" r="3.8" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M11.8 12H21m-3 0v3m-3-3v2" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  similar: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5c2.7-2 5.3-2 8 0s5.3 2 8 0M4 15.5c2.7-2 5.3-2 8 0s5.3 2 8 0" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  warning: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4 2.8 19.5h18.4L12 4Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M12 10v4.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="12" cy="17" r="1" fill="currentColor"/></svg>',
  undo: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7 4.5 11.5 9 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 11.5h9a5 5 0 0 1 0 10h-2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  note: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4.5h14a1.5 1.5 0 0 1 1.5 1.5v9.5L15.5 20.5H5A1.5 1.5 0 0 1 3.5 19V6A1.5 1.5 0 0 1 5 4.5Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M15.5 20.5V16a.5.5 0 0 1 .5-.5h4.5M7.5 9h9M7.5 12.5h6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  sparkle: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5c.5 3.9 2.6 6 6.5 6.5-3.9.5-6 2.6-6.5 6.5-.5-3.9-2.6-6-6.5-6.5 3.9-.5 6-2.6 6.5-6.5Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M18.5 15.5c.2 1.6 1 2.3 2.5 2.5-1.5.2-2.3 1-2.5 2.5-.2-1.5-1-2.3-2.5-2.5 1.5-.2 2.3-.9 2.5-2.5Z" fill="currentColor"/></svg>',
  add: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="m15 15 5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  sort: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M8 19l-3.5-3.5M8 19l3.5-3.5M16 19V5M16 5l-3.5 3.5M16 5l3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  filter: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="3.5" fill="none" stroke="currentColor" stroke-width="1.7"/><path class="check-mark" d="M8.2 12.4l2.6 2.6 5-5.4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  folder: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 6.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  close: path("M6.4 5 12 10.6 17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4L12 13.4 6.4 19 5 17.6 10.6 12 5 6.4z"),
};

let toastTimer;
// Message bref en bas de l'écran ; `action` ajoute un bouton (« Annuler » une suppression…)
export function toast(message, { error = false, duration = 3500, action = null } = {}) {
  const el = document.getElementById("toast");
  el.replaceChildren(h("span", {}, message));
  if (action) {
    const button = h("button", { class: "toast-action", type: "button" }, h("span", { html: icons.undo }), action.label);
    button.addEventListener("click", () => { el.classList.remove("visible"); action.onClick(); });
    el.append(button);
  }
  el.classList.toggle("error", error);
  el.classList.toggle("has-action", Boolean(action));
  el.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("visible"), action ? Math.max(duration, 7000) : duration);
}

export function toastError(error) {
  toast(t("error.generic", { message: error?.message || String(error) }), { error: true, duration: 6000 });
}

export function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

// Horodatage SQLite (UTC, « 2026-10-10 06:30:00 ») → heures écoulées
export function hoursSince(stamp) {
  return stamp ? (Date.now() - Date.parse(`${stamp.replace(" ", "T")}Z`)) / 3.6e6 : Infinity;
}

export function timeAgo(stamp) {
  const hours = hoursSince(stamp);
  if (hours < 1) return t("time.minutesAgo", { n: Math.max(1, Math.round(hours * 60)) });
  if (hours < 48) return t("time.hoursAgo", { n: Math.round(hours) });
  return t("time.daysAgo", { n: Math.round(hours / 24) });
}

export function formatDate(iso) {
  if (!iso) return "";
  return iso.slice(0, 10);
}

// Petites formules TeX des titres ($m$, $\mathbb{C}^n$…) : rendu Unicode approximatif.
const TEX_SYMBOLS = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", zeta: "ζ", eta: "η",
  theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ", lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π",
  rho: "ρ", sigma: "σ", tau: "τ", upsilon: "υ", phi: "φ", varphi: "φ", chi: "χ", psi: "ψ", omega: "ω",
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π", Sigma: "Σ", Phi: "Φ", Psi: "Ψ",
  Omega: "Ω", infty: "∞", partial: "∂", nabla: "∇", times: "×", cdot: "·", to: "→", leq: "≤", geq: "≥",
  ell: "ℓ", hbar: "ℏ", otimes: "⊗", oplus: "⊕", subset: "⊂", in: "∈", sim: "∼", cong: "≅",
};
const BLACKBOARD = { C: "ℂ", R: "ℝ", Z: "ℤ", Q: "ℚ", N: "ℕ", P: "ℙ", H: "ℍ" };

function texToText(tex) {
  return tex
    .replace(/\\mathbb\{?([A-Z])\}?/g, (_, c) => BLACKBOARD[c] || c)
    .replace(/\\(?:mathcal|mathrm|mathbf|mathfrak|operatorname|text|mathit)\{([^}]*)\}/g, "$1")
    .replace(/\\(?:rm|it|bf|sf|tt|cal|scriptstyle|displaystyle)\b\s*/g, "")
    .replace(/\\([A-Za-z]+)/g, (_, name) => TEX_SYMBOLS[name] ?? name)
    .replace(/\\[,;! ]/g, " ");
}

// Indices et exposants (x_{n}, e^{-f}) en <sub>/<sup>, le reste en texte.
function scripted(tex) {
  const nodes = [];
  const pattern = /([_^])(?:\{([^{}]*)\}|(\\?.))/g;
  let last = 0;
  for (const match of tex.matchAll(pattern)) {
    nodes.push(tex.slice(last, match.index).replace(/[{}]/g, ""));
    nodes.push(h(match[1] === "_" ? "sub" : "sup", {}, (match[2] ?? match[3]).replace(/[{}]/g, "")));
    last = match.index + match[0].length;
  }
  nodes.push(tex.slice(last).replace(/[{}]/g, ""));
  return nodes;
}

// Texte avec formules $…$ → nœuds (formules en italique mathématique).
export function withMath(text) {
  return String(text).split(/(\$[^$]+\$)/).filter(Boolean).map((part) =>
    part.startsWith("$") && part.endsWith("$") ? h("span", { class: "tex", title: part }, scripted(texToText(part.slice(1, -1)))) : part);
}

// Titre écrit tout en majuscules (certaines sources, Semantic Scholar…) : remis en casse de titre
// usuelle, formules $…$ intactes. Les titres normaux ne sont pas touchés.
const SMALL_WORDS = new Set(["a", "an", "the", "of", "on", "in", "and", "or", "for", "to", "with", "via", "by", "at", "from", "as", "into", "over", "under"]);
// Sigles gardés tels quels (on ne peut pas les deviner dans un titre tout en majuscules)
const ACRONYMS = new Set(["QFT", "CFT", "ADS", "PDE", "PDES", "ODE", "ODES", "SU", "SO", "SL", "GL", "SP", "PSL", "CR", "BPS", "QCD",
  "TQFT", "KAM", "WKB", "BV", "BRST", "KDV", "NLS", "RCD", "CAT", "HKR", "DG", "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII"]);

export function tidyTitle(title) {
  const text = String(title || "");
  const letters = text.replace(/\$[^$]*\$/g, "").match(/\p{L}/gu) || [];
  const upper = letters.filter((ch) => ch !== ch.toLowerCase()).length;
  if (letters.length < 12 || upper < 0.85 * letters.length) return text;
  let first = true;
  return text.split(/(\$[^$]*\$)/).map((part) => {
    if (part.startsWith("$")) { first = false; return part; }
    return part.replace(/[\p{L}\d][\p{L}\d'’]*/gu, (word, offset, whole) => {
      const sentenceStart = first || /[:?!.—–]\s*$/.test(whole.slice(0, offset));
      first = false;
      // sigle, numéro, ou groupe noté SL(2,R), CP^N, H_1 : casse d'origine
      if (/\d/.test(word) || ACRONYMS.has(word.toUpperCase()) || /^[(^_]/.test(whole.slice(offset + word.length))) return word;
      const lower = word.toLowerCase();
      if (SMALL_WORDS.has(lower) && !sentenceStart) return lower;
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    });
  }).join("");
}

// Titre d'article pour MathJax : formules $…$ ou \(…\) gardées, hors formules les tirets et espaces
// de TeX (« -- », « --- », « \ », « ~ ») remplacés par les caractères correspondants
export function titleTex(title) {
  return tidyTitle(title).split(/(\$[^$]*\$|\\\(.*?\\\))/).map((part, i) => (i % 2 ? part
    : part.replace(/---/g, "—").replace(/--/g, "–").replace(/\\ /g, " ").replace(/~/g, " "))).join("");
}

// Dernière page de liste visitée (bibliothèque ou onglet du feed) : où revient la flèche du lecteur
export const navigation = { listHash: "#/" };

// Titre d'article en texte simple (onglets, titre de la fenêtre) : formules réduites à leurs symboles
const SUPERSCRIPTS = { 0: "⁰", 1: "¹", 2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶", 7: "⁷", 8: "⁸", 9: "⁹", n: "ⁿ" };
// Glisser-déposer d'un article (bibliothèque, feed, recherche) vers un dossier, aussi d'une fenêtre de
// l'application à l'autre : son identifiant (type propre à arXivReader) et un lien arXiv ordinaire
export const PAPER_DRAG = "application/x-arxivreader-paper";
// articles glissés depuis cette fenêtre (le temps du glissement) ; d'une autre fenêtre : inconnus ici
export let localDrag = null;

// On le saisit par son titre (un lien, glissable de lui-même) : le reste de la carte garde la sélection de
// texte (résumé du feed). Bibliothèque : le lien du titre couvre toute la carte.
// `group(info)` : articles emportés (la sélection, si l'article saisi en fait partie)
export function draggablePaper(element, { arxivId, paperId = null, version = null, title = "" }, group = (info) => [info], onEnd = null) {
  if (!arxivId) return element;
  element.addEventListener("dragstart", (event) => {
    if (!event.target.closest?.("a")) return; // texte sélectionné glissé ailleurs dans la carte : rien de spécial
    const papers = group({ arxivId, paperId, title });
    localDrag = papers;
    const urls = papers.map((p) => `https://arxiv.org/abs/${p.arxivId}${p.arxivId === arxivId && version ? `v${version}` : ""}`);
    event.dataTransfer.setData(PAPER_DRAG, JSON.stringify(papers.map((p) => ({ arxivId: p.arxivId, paperId: p.paperId || null, title: p.title || "" }))));
    event.dataTransfer.setData("text/uri-list", urls.join("\r\n"));
    event.dataTransfer.setData("text/plain", urls.join("\n"));
    event.dataTransfer.effectAllowed = "copyLink";
    element.classList.add("dragging");
  });
  element.addEventListener("dragend", (event) => { element.classList.remove("dragging"); localDrag = null; onEnd?.(event); });
  return element;
}

// Articles déposés : glissés depuis arXivReader (un ou plusieurs), ou liens (ou identifiants) arXiv venus
// d'ailleurs, un par ligne ; [] si rien de reconnu
export function droppedPapers(dataTransfer) {
  try {
    const own = dataTransfer.getData(PAPER_DRAG);
    if (own) {
      const data = [JSON.parse(own)].flat().filter((p) => typeof p?.arxivId === "string")
        .map((p) => ({ arxivId: p.arxivId, paperId: typeof p.paperId === "string" ? p.paperId : null, title: typeof p.title === "string" ? p.title : "" }));
      if (data.length) return data;
    }
  } catch { /* données d'une autre application : on regarde les liens */ }
  const text = dataTransfer.getData("text/uri-list") || dataTransfer.getData("text/plain") || "";
  const ids = [];
  for (const line of text.split(/\r?\n/)) {
    const value = line.trim();
    if (!value || value.startsWith("#")) continue;
    const found = value.match(/arxiv\.org\/(?:abs|pdf|html)\/((?:[a-z-]+(?:\.[A-Z]{2})?\/\d{7})|\d{4}\.\d{4,5})/i)
      || value.match(/^(?:arXiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$/i);
    if (found && !ids.includes(found[1])) ids.push(found[1]);
  }
  return ids.map((arxivId) => ({ arxivId, paperId: null, title: "" }));
}

export const mayBePaper = (event) => [...(event.dataTransfer?.types || [])].some((type) =>
  type === PAPER_DRAG || type === "text/uri-list" || type === "text/plain");

// Revue où l'article est paru (arXiv : indiquée par les auteurs ; sinon Semantic Scholar) : étiquette courte,
// référence complète et source au survol
export function journalChip(publishedIn) {
  if (!publishedIn?.text) return null;
  const text = publishedIn.text;
  return h("span", { class: "chip journal-chip", title: t(`journal.hint.${publishedIn.source === "s2" ? "s2" : "arxiv"}`, { ref: text }) },
    t("journal.chip", { ref: text.length > 48 ? `${text.slice(0, 46)}…` : text }));
}

export function plainTitle(title) {
  return titleTex(title).split(/(\$[^$]*\$|\\\(.*?\\\))/).map((part, i) => {
    if (!(i % 2)) return part;
    return texToText(part.replace(/^\$|\$$|^\\\(|\\\)$/g, ""))
      .replace(/\^\{?([0-9n])\}?/g, (_, c) => SUPERSCRIPTS[c]).replace(/[_^{}]/g, "");
  }).join("").replace(/\s+/g, " ").trim();
}

// Ouvre une page de l'application (« #/paper/… ») ; dans un nouvel onglet si demandé (⌘-clic, clic du
// milieu) : onglet de l'application Mac (en arrière-plan, au premier plan avec ⇧), sinon du navigateur
export function openRoute(hash, newTab = false, front = false) {
  const bridge = window.webkit?.messageHandlers?.arxivreader;
  if (!newTab) location.hash = hash;
  else if (bridge) bridge.postMessage({ action: "openTab", route: hash, front });
  else window.open(`${location.pathname}${hash}`, "_blank", "noopener");
}
export const wantsNewTab = (event) => Boolean(event.metaKey || event.ctrlKey || event.button === 1);

// Bouton à bascule à la YouTube (contour au repos, plein une fois activé)
export function toggleIcon(off, on, { pressed = false, label = "", className = "" } = {}) {
  return h("button", { class: `icon-button thumb ${className}`, type: "button", title: label, "aria-label": label, "aria-pressed": String(pressed) },
    h("span", { class: "thumb-off", html: off }), h("span", { class: "thumb-on", html: on }));
}
