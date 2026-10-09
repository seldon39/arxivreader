// Rendu des formules avec MathJax (mise en page fidèle à TeX, polices Computer Modern).
// Toutes les formules sont converties avant l'affichage de l'article : la conversion prend
// moins d'une seconde, et rien ne bouge ensuite pendant la lecture.

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
