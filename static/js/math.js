// Rendu des formules avec MathJax (mise en page fidèle à TeX, polices Computer Modern).
// Le MathML produit par LaTeXML est converti formule par formule, par paquets, en commençant
// par celles qui sont à l'écran ; le MathML natif reste affiché en attendant.

const BASE = "/static/vendor/mathjax";
let loading = null;

function loadMathJax() {
  loading ||= new Promise((resolve, reject) => {
    window.MathJax = {
      loader: { load: [] },
      startup: { typeset: false, ready: () => { window.MathJax.startup.defaultReady(); resolve(window.MathJax); } },
      options: { enableMenu: false, menuOptions: { settings: { assistiveMml: false } } },
      chtml: { fontURL: `${BASE}/fonts/woff-v2`, scale: 1, matchFontHeight: false, mtextInheritFont: true },
    };
    const script = document.createElement("script");
    script.src = `${BASE}/mml-chtml.js`;
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

function convert(MathJax, math) {
  const display = math.getAttribute("display") === "block";
  const node = MathJax.mathml2chtml(math.outerHTML, { display });
  node.dataset.tex = math.getAttribute("alttext") || "";
  node.classList.add("arx-math");
  math.replaceWith(node);
}

/**
 * Convertit toutes les formules de `root`.
 * - priority(math) : nombre, les plus petits d'abord (distance à l'écran) ;
 * - beforeChunk() / afterChunk(state) : permettent au lecteur de garder sa position ;
 * - isCancelled() : arrêt si l'utilisateur quitte l'article.
 */
export async function typesetMath(root, { priority = () => 0, beforeChunk = () => null, afterChunk = () => {}, isCancelled = () => false, chunk = 250 } = {}) {
  const MathJax = await loadMathJax();
  const maths = [...root.querySelectorAll("math")].filter((m) => !m.parentElement.closest("math"));
  const ordered = maths.map((m) => [priority(m), m]).sort((a, b) => a[0] - b[0]).map(([, m]) => m);
  for (let i = 0; i < ordered.length; i += chunk) {
    if (isCancelled()) return;
    const state = beforeChunk();
    for (const math of ordered.slice(i, i + chunk)) {
      if (!math.isConnected) continue;
      try {
        convert(MathJax, math);
      } catch {
        // formule non convertible : le MathML natif reste en place
      }
    }
    refreshStylesheet(MathJax);
    afterChunk(state);
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

// Formules restées en MathML natif dans un fragment copié (aperçus) : conversion immédiate.
export function typesetFragment(root) {
  if (!window.MathJax?.mathml2chtml) return;
  root.querySelectorAll("math").forEach((math) => {
    if (math.parentElement.closest("math")) return;
    try { convert(window.MathJax, math); } catch { /* MathML natif conservé */ }
  });
  refreshStylesheet(window.MathJax);
}
