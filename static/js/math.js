// Rendu des formules avec MathJax (mise en page fidèle à TeX, polices Computer Modern).
// Toutes les formules sont converties avant l'affichage de l'article : la conversion prend
// moins d'une seconde, et rien ne bouge ensuite pendant la lecture.

const BASE = "/static/vendor/mathjax";
let loading = null;

// Macros courantes que les auteurs laissent dans leurs titres et résumés (arXiv ne les développe pas) ;
// les \newcommand d'un article (defineMacros) les remplacent pour ses propres énoncés
const op = (name) => `\\operatorname{${name}}`;
const MACROS = {
  R: "\\mathbb{R}", N: "\\mathbb{N}", Z: "\\mathbb{Z}", Q: "\\mathbb{Q}", C: "\\mathbb{C}",
  RR: "\\mathbb{R}", NN: "\\mathbb{N}", ZZ: "\\mathbb{Z}", QQ: "\\mathbb{Q}", CC: "\\mathbb{C}",
  eps: "\\varepsilon", veps: "\\varepsilon",
  abs: ["\\left\\lvert #1 \\right\\rvert", 1], norm: ["\\left\\lVert #1 \\right\\rVert", 1],
  supp: op("supp"), Hom: op("Hom"), End: op("End"), Aut: op("Aut"), Tr: op("Tr"), tr: op("tr"),
  id: op("id"), Id: op("Id"), rank: op("rank"), diag: op("diag"), dist: op("dist"), vol: op("vol"),
  sgn: op("sgn"), Spec: op("Spec"),
  // lettre barrée par la barre de \not et de ≠ (⧸, U+29F8 : même glyphe que U+0338 mais non combinant), sans largeur,
  // au début de la lettre comme en TeX (slashedLetters) ; \slashed (notation de Feynman) est inconnue de MathJax
  arxivslash: ["\\rlap{\\mathrm{\u29F8}}#1", 1], slashed: ["\\arxivslash{#1}", 1],
};
// Les mêmes en \def : rétablies quand on quitte un article qui les avait redéfinies (MathJax garde les \newcommand
// d'une page à l'autre)
export const DEFAULT_MACROS = Object.entries(MACROS).map(([name, value]) =>
  Array.isArray(value) ? `\\def\\${name}#1{${value[0]}}` : `\\def\\${name}{${value}}`);

function loadMathJax() {
  loading ||= new Promise((resolve, reject) => {
    window.MathJax = {
      loader: { load: [], paths: { mathjax: BASE } },
      // TeX des titres et résumés du feed ($L^2$, \(\mathbb{C}^n\)…)
      tex: { inlineMath: [["$", "$"], ["\\(", "\\)"]], displayMath: [["$$", "$$"], ["\\[", "\\]"]], processEscapes: true,
        macros: MACROS,
        noundefined: { color: "" },  // macro propre à l'auteur (\db…) : affichée telle quelle, sans rouge
        // \href, \style, \class, \cssId désactivés : un résumé ou un titre (texte venu d'arXiv) pourrait y
        // cacher un lien vers n'importe quelle adresse, invisible et étendu à toute la fenêtre. Ni chargés à la
        // demande (autoload), ni par \require{html} : le paquet require est retiré, et html.js n'est pas livré
        packages: { "[-]": ["require", "html"] },
        autoload: { html: [] } },
      startup: { typeset: false, ready: () => { window.MathJax.startup.defaultReady(); resolve(window.MathJax); } },
      options: { enableMenu: false, menuOptions: { settings: { assistiveMml: false } } },
      chtml: { fontURL: `${BASE}/fonts/woff-v2`, scale: 1, matchFontHeight: false, mtextInheritFont: true },
    };
    const script = document.createElement("script");
    script.src = `${BASE}/tex-mml-chtml.js`;
    script.async = true;
    script.onerror = () => reject(new Error("MathJax indisponible"));
    document.head.append(script);
  });
  return loading;
}

// MathJax complète sa feuille de style sur place (règles ajoutées une à une) : il ne faut
// jamais la retirer du document, seulement l'y insérer la première fois.
function refreshStylesheet(MathJax) {
  const sheet = MathJax.chtmlStylesheet();
  if (!sheet.isConnected) document.head.append(sheet);
}

// \mathcal : LaTeXML écrit des lettres « script » Unicode (ℋ, 𝒪…) ; on les remet en
// calligraphique Computer Modern (variante -tex-calligraphic de MathJax), comme dans le PDF.
const SCRIPT_EXCEPTIONS = { "ℬ": "B", "ℰ": "E", "ℱ": "F", "ℋ": "H", "ℐ": "I", "ℒ": "L", "ℳ": "M", "ℛ": "R" };

function scriptToLatin(text) {
  if (SCRIPT_EXCEPTIONS[text]) return SCRIPT_EXCEPTIONS[text];
  const code = text.codePointAt(0);
  return text.length <= 2 && code >= 0x1d49c && code <= 0x1d4b5 ? String.fromCharCode(65 + code - 0x1d49c) : null;
}

function restoreCalligraphic(math) {
  math.querySelectorAll("mi.ltx_font_mathcaligraphic").forEach((mi) => {
    const text = mi.textContent.trim();
    // lettre barrée par \not (\not{\mathcal{D}} : opérateur de Dirac) : LaTeXML laisse la lettre latine suivie de la
    // barre combinante U+0338 ; elle reste calligraphique (pas le script de mathvariant="script")
    const latin = scriptToLatin(text) || (/^[A-Z]\u0338?$/.test(text) ? text : null);
    if (!latin) return;
    mi.textContent = latin;
    mi.setAttribute("mathvariant", "-tex-calligraphic");
  });
}

// Lettre suivie de la barre combinante U+0338 (\not{D}, \not{\mathcal{D}}) : ce caractère combinant est placé
// différemment selon le moteur (Chrome le dessine sur la lettre, WebKit après elle, sur l'indice). On le remplace par
// la même barre non combinante de la police (⧸, U+29F8 : celle de \not et de ≠, hauteur 0,716 em, profondeur 0,215 em),
// sans largeur, au début de la lettre — exactement comme TeX place \not ; devant tout l'indice si la lettre en est la
// base (attachScripts), qui reste placé comme sans barre.
function slashedLetters(math) {
  math.querySelectorAll("mi").forEach((mi) => {
    const text = mi.textContent;
    if (!/^\p{L}\u0338$/u.test(text)) return;
    mi.textContent = text[0];
    const ns = mi.namespaceURI;
    const slash = document.createElementNS(ns, "mi");
    slash.setAttribute("mathvariant", "normal");
    slash.textContent = "\u29F8";
    const overlay = document.createElementNS(ns, "mpadded");
    overlay.setAttribute("width", "0");
    overlay.append(slash);
    const parent = mi.parentElement;
    const target = /^(msub|msup|msubsup)$/.test(parent?.localName || "") && parent.firstElementChild === mi ? parent : mi;
    const group = document.createElementNS(ns, "mrow");
    target.replaceWith(group);
    group.append(overlay, target);
  });
}

// Espacement des relations : LaTeXML réduit parfois l'espace autour de « = » (rspace="0.111em"
// devant ∂, par exemple). TeX met 0,278 em (\thickmuskip) ; on rend la main au dictionnaire
// d'opérateurs de MathJax quand l'espace indiqué est plus petit. Les espaces ajoutés par l'auteur
// (plus grands) sont conservés.
const RELATIONS = new Set("=<>≤≥≠≈≡∼≃≅∝∈∉∋⊂⊃⊆⊇⊊⊋≺≻⪯⪰≪≫→←↔⟶⟵⟷↦⟼⇒⇐⇔⟹⟸⟺∣∥⊥≔:=".split(""));

function relationSpacing(math) {
  math.querySelectorAll("mo").forEach((mo) => {
    const text = mo.textContent.trim();
    if (!RELATIONS.has(text) && text !== ":=") return;
    for (const side of ["lspace", "rspace"]) {
      const value = parseFloat(mo.getAttribute(side));
      if (!Number.isNaN(value) && value < 0.27) mo.removeAttribute(side);
    }
  });
}

function convert(MathJax, math) {
  restoreCalligraphic(math);
  slashedLetters(math);
  relationSpacing(math);
  const display = math.getAttribute("display") === "block";
  const node = MathJax.mathml2chtml(math.outerHTML, { display });
  node.dataset.tex = math.getAttribute("alttext") || "";
  math.replaceWith(node);
}

function topLevelMath(root) {
  return [...root.querySelectorAll("math")].filter((m) => !m.parentElement.closest("math"));
}

// Convertit toutes les formules de `root` (par gros paquets, pour laisser vivre l'indicateur d'attente).
export async function typesetMath(root, { isCancelled = () => false, chunk = 500 } = {}) {
  const MathJax = await loadMathJax();
  const maths = topLevelMath(root);
  for (let i = 0; i < maths.length; i += chunk) {
    if (isCancelled()) return;
    for (const math of maths.slice(i, i + chunk)) {
      try {
        convert(MathJax, math);
      } catch {
        // formule non convertible : le MathML natif reste en place
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  refreshStylesheet(MathJax);
}

// Formules restées en MathML natif dans un fragment copié (aperçus) : conversion immédiate.
export function typesetFragment(root) {
  if (!window.MathJax?.mathml2chtml) return;
  for (const math of topLevelMath(root)) {
    try { convert(window.MathJax, math); } catch { /* MathML natif conservé */ }
  }
  refreshStylesheet(window.MathJax);
}

// \not devant une lettre ou un groupe (\not{\mathcal{D}}, \not D, \not\partial : opérateur de Dirac barré) : MathJax pose
// la barre après la lettre, sur l'indice ; ici la barre de TeX (\arxivslash), comme dans le corps de l'article
// (slashedLetters). Les négations de relation (\not=, \not\in, \not\subset…) restent telles quelles.
const NOT_LETTER = /\\not\s*(?=\{|\\math(?:cal|scr|bb|rm|bf|frak|it|sf)\s*\{|\\(?:partial|nabla)(?![A-Za-z])|[A-Za-z](?![A-Za-z]))/g;
function slashLetters(element) {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.data.includes("\\not")) continue;
    node.data = node.data.replace(NOT_LETTER, "\\arxivslash ").replace(/\\arxivslash (\\math[a-z]+\s*\{[^{}]*\}|\\[a-z]+|[A-Za-z])/g, "\\arxivslash{$1}");
  }
}

// Texte contenant du TeX entre $…$ (titres, résumés, notes) : mise en forme par MathJax.
// Les demandes passent une à une (MathJax garde un état partagé), les éléments déjà retirés de la page
// sont ignorés, et MathJax oublie ensuite les formules traitées (sinon sa liste grossit sans fin).
let queue = Promise.resolve();
export function typesetTex(elements) {
  queue = queue.then(async () => {
    const live = elements.filter((element) => element?.isConnected);
    if (!live.length) return;
    live.forEach(slashLetters);
    const MathJax = await loadMathJax();
    await MathJax.typesetPromise(live);
    MathJax.typesetClear?.(live);
  }).catch(() => {});
  return queue;
}
