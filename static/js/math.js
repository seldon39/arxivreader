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
  // polices de paquets que LaTeXML (arXiv) ne connaît pas : euscript, mathrsfs ancien, eufrak (voir undefinedFonts)
  EuScript: ["\\mathcal{#1}", 1], scr: ["\\mathscr{#1}", 1], EuFrak: ["\\mathfrak{#1}", 1],
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
      // adaptiveCSS: false : les règles de tous les caractères posées au départ, d'un coup. Sinon MathJax en ajoute à
      // chaque caractère nouveau (première réponse de l'IA affichée…) et WebKit recalcule tout l'article (~1 s)
      chtml: { fontURL: `${BASE}/fonts/woff-v2`, scale: 1, matchFontHeight: false, mtextInheritFont: true, adaptiveCSS: false },
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

// Commande de police inconnue de LaTeXML (\EuScript du paquet euscript, \scr…) : arXiv la laisse en texte brut
// (<mtext class="undefined">\EuScript</mtext>), suivie parfois d'un séparateur invisible, puis de la lettre, seule ou
// base d'un indice ou d'un exposant (\EuScript F_i). On retire la commande et on écrit la lettre comme arXiv écrit
// celles de \mathcal, \mathscr, \mathfrak et \mathbb (caractère Unicode, classe de police) : le reste suit, avec
// MathJax (restoreCalligraphic) comme avec le MathML du navigateur. Euler Script (\EuScript) : calligraphique.
const FONT_MACROS = {
  "\\EuScript": "cal", "\\EuScr": "cal", "\\scr": "scr", "\\rsfs": "scr",
  "\\EuFrak": "frak", "\\frak": "frak", "\\goth": "frak", "\\mathgoth": "frak",
  "\\Bbb": "bb", "\\mathbbm": "bb", "\\mathds": "bb", "\\bbold": "bb",
};
// premier caractère des majuscules et minuscules de chaque alphabet, et lettres déjà codées ailleurs dans Unicode
const ALPHABETS = {
  script: [0x1d49c, 0x1d4b6, { B: "ℬ", E: "ℰ", F: "ℱ", H: "ℋ", I: "ℐ", L: "ℒ", M: "ℳ", R: "ℛ", e: "ℯ", g: "ℊ", o: "ℴ" }],
  frak: [0x1d504, 0x1d51e, { C: "ℭ", H: "ℌ", I: "ℑ", R: "ℜ", Z: "ℨ" }],
  bb: [0x1d538, 0x1d552, { C: "ℂ", H: "ℍ", N: "ℕ", P: "ℙ", Q: "ℚ", R: "ℝ", Z: "ℤ" }],
};
const FONT_CLASS = { cal: "ltx_font_mathcaligraphic", scr: "ltx_font_mathscript" };

function styledLetter(letter, font) {
  const [upper, lower, exceptions] = ALPHABETS[font === "cal" || font === "scr" ? "script" : font];
  if (exceptions[letter]) return exceptions[letter];
  if (/^[A-Z]$/.test(letter)) return String.fromCodePoint(upper + letter.charCodeAt(0) - 65);
  if (/^[a-z]$/.test(letter)) return String.fromCodePoint(lower + letter.charCodeAt(0) - 97);
  if (font === "bb" && /^[0-9]$/.test(letter)) return String.fromCodePoint(0x1d7d8 + Number(letter));
  return null;
}

const INVISIBLE = /^[\s\u200b\u2061-\u2064]*$/;  // espace nulle, application, × et séparateur invisibles

export function undefinedFonts(root) {
  root.querySelectorAll("math mtext.undefined").forEach((mtext) => {
    const font = FONT_MACROS[mtext.textContent.trim()];
    if (!font) return;
    const after = mtext.nextElementSibling;
    const gap = after?.localName === "mo" && INVISIBLE.test(after.textContent) ? after : null;
    const next = (gap || mtext).nextElementSibling;
    const letter = /^(msub|msup|msubsup|munder|mover|munderover)$/.test(next?.localName || "") ? next.firstElementChild : next;
    const styled = letter?.localName === "mi" && styledLetter(letter.textContent.trim(), font);
    if (!styled) return;  // pas une lettre seule : la commande reste visible, comme sur arXiv
    letter.textContent = styled;
    if (FONT_CLASS[font]) letter.classList.add(FONT_CLASS[font]);
    letter.removeAttribute("mathvariant");  // une lettre seule est en italique par défaut : pas de variante à garder
    mtext.remove();
    gap?.remove();
  });
}

// Liens dans le texte d'une formule (\text{\eqref{…}}, \text{\cite{…}}) : arXiv y met du HTML (<a><span>…</span></a>,
// <em>…</em>), que MathJax refuse (toute la formule restait alors en MathML du navigateur). Pour MathJax seulement (copie,
// après un premier refus), chaque <mtext> devient une suite de morceaux de texte :
// - un lien, d'un seul tenant, porté par l'attribut href (MathJax en fait un vrai <a>) ;
// - le reste, avec les classes de police d'arXiv de ses éléments (ltx_font_italic de \emph…), que MathJax garde ;
// - les traits de \rule (ltx_rule, souvent des appuis de hauteur invisibles) en <mspace> de mêmes dimensions.
// Un dessin (svg, img) : rien n'est touché, MathML du navigateur.
const KEEP_SPACE = (text) => text.replace(/^\s+|\s+$/g, "\u00a0");  // MathJax retire les espaces en bord de <mtext>
const fontClasses = (element) => [...element.classList].filter((name) => name.startsWith("ltx_font_"));

// \rule[décalage]{largeur}{hauteur} : style « width:…; height:…; vertical-align:… » (pt), fond de la couleur du trait
function ruleSpace(rule, ns) {
  const length = (name) => parseFloat(rule.style.getPropertyValue(name)) || 0;
  const [width, height, raise] = [length("width"), length("height"), length("vertical-align")];
  const space = document.createElementNS(ns, "mspace");
  space.setAttribute("width", `${Math.max(0, width)}pt`);
  space.setAttribute("height", `${Math.max(0, raise + height)}pt`);
  space.setAttribute("depth", `${Math.max(0, -raise)}pt`);
  if (width > 0) space.setAttribute("mathbackground", rule.style.getPropertyValue("--ltx-bg-color").trim() || "currentColor");
  return space;
}

function linksInText(math) {
  for (const mtext of math.querySelectorAll("mtext")) {
    if (!mtext.firstElementChild) continue;
    if (mtext.querySelector("svg, img, math")) return false;
    const ns = mtext.namespaceURI;
    const pieces = [];
    const push = (text, link, fonts) => {
      if (!text) return;
      const last = pieces[pieces.length - 1];
      // un lien reste d'un seul tenant (une seule zone de survol) ; hors lien, même police : même morceau
      if (last && !last.rule && last.link === link && (link || last.fonts === fonts)) last.text += text;
      else pieces.push({ text, link, fonts });
    };
    const walk = (node, link, fonts) => {
      for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) push(child.data, link, fonts);
        else if (child.nodeType !== Node.ELEMENT_NODE) continue;
        else if (child.classList.contains("ltx_rule")) pieces.push({ rule: child });
        else if (child.localName === "a" && child.getAttribute("href") && !link) walk(child, child, fonts);
        else walk(child, link, [...new Set([...fonts.split(" "), ...fontClasses(child)])].filter(Boolean).join(" "));
      }
    };
    walk(mtext, null, "");
    const row = document.createElementNS(ns, "mrow");
    for (const { text, link, fonts, rule } of pieces) {
      if (rule) { row.append(ruleSpace(rule, ns)); continue; }
      const piece = document.createElementNS(ns, "mtext");
      for (const { name, value } of mtext.attributes) if (name !== "id") piece.setAttribute(name, value);
      piece.textContent = KEEP_SPACE(text);
      if (fonts) piece.classList.add(...fonts.split(" "));
      if (link) {
        piece.setAttribute("href", link.getAttribute("href"));
        // classes du lien d'arXiv (ltx_ref…) : MathJax les pose sur le texte, restoreLinks les remet sur le lien
        piece.classList.add(...[...link.classList].filter((name) => name.startsWith("ltx_")), "math-link");
      }
      row.append(piece);
    }
    mtext.replaceWith(row);
  }
  return true;
}

// Après MathJax : le lien reprend les classes du lien d'arXiv (couleur, aperçus des renvois et des citations) ; un lien
// vers l'extérieur s'ouvre dans le navigateur, comme les autres (sanitize.py)
function restoreLinks(node) {
  for (const link of node.querySelectorAll("a[href]")) {
    const inner = link.querySelector(".math-link");
    if (!inner) continue;
    const classes = [...inner.classList].filter((name) => name.startsWith("ltx_") && !name.startsWith("ltx_font_"));
    inner.classList.remove(...classes, "math-link");
    link.classList.add(...classes);
    if (!link.getAttribute("href").startsWith("#")) Object.assign(link, { target: "_blank", rel: "noopener noreferrer" });
  }
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
  undefinedFonts(math);
  restoreCalligraphic(math);
  slashedLetters(math);
  relationSpacing(math);
  const display = math.getAttribute("display") === "block";
  let node;
  try {
    node = MathJax.mathml2chtml(math.outerHTML, { display });
  } catch (error) {
    // refusée à cause du HTML dans son texte (liens) : nouvel essai sur une copie sans HTML ; sinon l'original reste
    const copy = math.querySelector("mtext *") && math.cloneNode(true);
    if (!copy || !linksInText(copy)) throw error;
    node = MathJax.mathml2chtml(copy.outerHTML, { display });
    restoreLinks(node);
  }
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
  undefinedFonts(root);  // aussi avec le MathML du navigateur
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
