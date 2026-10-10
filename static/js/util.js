// Petits outils communs : création d'éléments, icônes, messages.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
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
  expand: path("M4 4h6v2H6v4H4zm10 0h6v6h-2V6h-4zM4 14h2v4h4v2H4zm14 0h2v6h-6v-2h4z"),
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
  close: path("M6.4 5 12 10.6 17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4L12 13.4 6.4 19 5 17.6 10.6 12 5 6.4z"),
};

let toastTimer;
export function toast(message, { error = false, duration = 3500 } = {}) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.toggle("error", error);
  el.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("visible"), duration);
}

export function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
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

// Bouton à bascule à la YouTube (contour au repos, plein une fois activé)
export function toggleIcon(off, on, { pressed = false, label = "", className = "" } = {}) {
  return h("button", { class: `icon-button thumb ${className}`, type: "button", title: label, "aria-label": label, "aria-pressed": String(pressed) },
    h("span", { class: "thumb-off", html: off }), h("span", { class: "thumb-on", html: on }));
}
