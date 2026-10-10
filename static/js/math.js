// Rendu des formules avec MathJax (mise en page fidèle à TeX, polices Computer Modern).
// Toutes les formules sont converties avant l'affichage de l'article : la conversion prend
// moins d'une seconde, et rien ne bouge ensuite pendant la lecture.

const BASE = "/static/vendor/mathjax";
let loading = null;

function loadMathJax() {
  loading ||= new Promise((resolve, reject) => {
    window.MathJax = {
      loader: { load: [], paths: { mathjax: BASE } },
      // TeX des titres et résumés du feed ($L^2$, \(\mathbb{C}^n\)…)
      tex: { inlineMath: [["$", "$"], ["\\(", "\\)"]], displayMath: [["$$", "$$"], ["\\[", "\\]"]], processEscapes: true,
        noundefined: { color: "" } },  // macro propre à l'auteur (\db…) : affichée telle quelle, sans rouge
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
    const latin = scriptToLatin(mi.textContent.trim());
    if (!latin) return;
    mi.textContent = latin;
    mi.setAttribute("mathvariant", "-tex-calligraphic");
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
  relationSpacing(math);
  const display = math.getAttribute("display") === "block";
  const node = MathJax.mathml2chtml(math.outerHTML, { display });
  node.dataset.tex = math.getAttribute("alttext") || "";
  node.classList.add("arx-math");
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

// Texte contenant du TeX entre $…$ (titres, résumés, notes) : mise en forme par MathJax.
// Les demandes passent une à une (MathJax garde un état partagé), les éléments déjà retirés de la page
// sont ignorés, et MathJax oublie ensuite les formules traitées (sinon sa liste grossit sans fin).
let queue = Promise.resolve();
export function typesetTex(elements) {
  queue = queue.then(async () => {
    const live = elements.filter((element) => element?.isConnected);
    if (!live.length) return;
    const MathJax = await loadMathJax();
    await MathJax.typesetPromise(live);
    MathJax.typesetClear?.(live);
  }).catch(() => {});
  return queue;
}
