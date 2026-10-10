// Corrections pour rapprocher la version HTML d'arXiv du PDF compilé (classe amsart) :
// numéros d'équation, étiquettes des listes, bibliographie amsplain, énoncés en italique.

import { h } from "./util.js";

// Numéros d'équation du côté voulu par \documentclass (reqno / leqno), que LaTeXML ignore parfois.
export function placeEquationNumbers(content, side) {
  if (side !== "left" && side !== "right") return;
  content.querySelectorAll("tr").forEach((row) => {
    const number = row.querySelector(":scope > td.ltx_eqn_eqno");
    if (!number) return;
    const cells = [...row.children];
    if (side === "right" && cells[0] === number && cells.length > 1) {
      row.append(number);
      number.classList.replace("ltx_align_left", "ltx_align_right");
    } else if (side === "left" && cells.at(-1) === number && cells.length > 1) {
      row.prepend(number);
      number.classList.replace("ltx_align_right", "ltx_align_left");
    }
  });
  content.querySelector(".ltx_document")?.classList.toggle("ltx_leqno", side === "left");
}

// Équation sur plusieurs lignes (align, gather…) dont certaines lignes seulement sont numérotées :
// les lignes sans numéro reçoivent une cellule vide à sa place, sinon leurs colonnes sont décalées
// d'un cran (numéro à gauche : « F(τ)⁻¹ = … » de la ligne suivante n'est plus aligné sur « = »).
export function alignNumberColumns(content) {
  for (const table of content.querySelectorAll("table.ltx_eqn_table")) {
    const rows = [...table.querySelectorAll(":scope > tbody > tr, :scope > tr")];
    const numbered = rows.find((row) => row.querySelector(":scope > td.ltx_eqn_eqno"));
    if (!numbered || rows.length < 2) continue;
    const left = numbered.firstElementChild?.classList.contains("ltx_eqn_eqno");
    let covered = 0; // lignes encore couvertes par un numéro qui s'étend sur plusieurs lignes (rowspan)
    for (const row of rows) {
      const number = row.querySelector(":scope > td.ltx_eqn_eqno");
      if (number) { covered = Math.max(1, Number(number.getAttribute("rowspan")) || 1) - 1; continue; }
      if (covered > 0) { covered--; continue; }
      const filler = h("td", { class: `ltx_eqn_cell ltx_eqn_eqno ltx_eqn_filler ${left ? "ltx_align_left" : "ltx_align_right"}` });
      if (left) row.prepend(filler); else row.append(filler);
    }
  }
}

const ROMAN = [[1000, "m"], [900, "cm"], [500, "d"], [400, "cd"], [100, "c"], [90, "xc"], [50, "l"], [40, "xl"], [10, "x"], [9, "ix"], [5, "v"], [4, "iv"], [1, "i"]];

function counter(kind, n) {
  if (kind === "1") return String(n);
  if (kind === "a" || kind === "A") {
    const letter = String.fromCharCode(96 + ((n - 1) % 26) + 1);
    return kind === "A" ? letter.toUpperCase() : letter;
  }
  let rest = n;
  let roman = "";
  for (const [value, digits] of ROMAN) while (rest >= value) { roman += digits; rest -= value; }
  return kind === "I" ? roman.toUpperCase() : roman;
}

// Étiquettes \begin{enumerate}[(i)] lues dans la source ; appliquées seulement si les listes
// de la source correspondent une à une à celles du HTML.
export function relabelLists(content, formats) {
  const lists = content.querySelectorAll("ol.ltx_enumerate");
  if (!formats || lists.length !== formats.length) return;
  lists.forEach((list, index) => {
    const format = formats[index];
    if (!format) return;
    const [before, kind, after] = format;
    [...list.children].filter((li) => li.classList.contains("ltx_item")).forEach((item, k) => {
      const tag = item.querySelector(":scope > .ltx_tag");
      if (tag) tag.textContent = `${before}${counter(kind, k + 1)}${after}`;
    });
  });
}

// amsthm (style plain) : dans un énoncé en italique, les étiquettes de liste sont aussi en italique.
export function markItalicStatements(content) {
  content.querySelectorAll(".ltx_theorem").forEach((theorem) => {
    const first = theorem.querySelector(".ltx_para .ltx_p");
    if (first?.firstElementChild?.classList.contains("ltx_font_italic")) theorem.classList.add("thm-italic");
  });
}

// Bibliographie au format amsplain : « Auteurs, Titre (italique), Revue Vol (gras) (Année), no. N, pages. »
export function amsBibliography(content) {
  for (const item of content.querySelectorAll("li.ltx_bibitem")) {
    const part = (name) => item.querySelector(`.ltx_bib_${name}`);
    const author = part("author");
    const title = part("title");
    if (!author || !title) continue;
    const nodes = (element) => [...element.cloneNode(true).childNodes];
    const year = (part("year")?.textContent || "").replace(/[()\s]/g, "");
    const journal = part("journal");
    const volume = part("volume");
    const number = part("number");
    const pages = part("pages");
    const link = item.querySelector("a.ltx_bib_external[href]");
    const blocks = [...item.querySelectorAll(":scope > .ltx_bibblock")];

    const entry = h("span", { class: "ltx_bibblock ams-bib" }, nodes(author), ", ");
    entry.append(link
      ? h("a", { class: "ams-title", href: link.href, target: "_blank", rel: "noopener noreferrer" }, nodes(title))
      : h("span", { class: "ams-title" }, nodes(title)));

    const clean = (text) => text.replace(/\s+/g, " ").trim().replace(/[.,\s]+$/, "");
    if (item.classList.contains("ltx_bib_article") && journal) {
      entry.append(", ", ...nodes(journal));
      if (volume) entry.append(" ", h("b", {}, nodes(volume)));
      if (year) entry.append(` (${year})`);
      if (number) entry.append(`, no. ${clean(number.textContent)}`);
      if (pages) entry.append(`, ${clean(pages.textContent).replace(/^pp?\.\s*/, "")}`);
      entry.append(".");
    } else {
      // Livres, actes, chapitres : « in: Ouvrage, Collection, vol. N, Éditeur, Lieu, Année, pp. x–y. »
      const pieces = [];
      const add = (text) => {
        const value = clean(text || "").replace(/^In:?\s+in:/i, "in:").replace(/^In:/, "in:");
        if (value && !pieces.some((piece) => piece.includes(value))) pieces.push(value);
      };
      add(part("inbook")?.textContent);
      const series = part("series");
      if (series) add(volume ? `${clean(series.textContent)}, vol. ${clean(volume.textContent)}` : series.textContent);
      ["edition", "publisher", "place", "address", "school", "institution", "note"].forEach((name) => add(part(name)?.textContent));
      if (!pieces.length) {
        const rest = blocks.find((block) => !block.contains(author) && !block.contains(title)
          && !block.querySelector(".ltx_bib_links") && !block.classList.contains("ltx_bib_cited"));
        add(rest?.textContent);
      }
      // Une collection déjà citée dans le titre de l'ouvrage n'est pas répétée
      const merged = pieces.filter((piece, i) => !pieces.some((other, j) => j !== i && other.includes(piece.split(",")[0]) && other.length > piece.length));
      if (year) merged.push(year);
      if (pages) merged.push(clean(pages.textContent));
      entry.append(merged.map((piece) => `, ${piece}`).join("") + ".");
    }
    blocks.forEach((block) => block.remove());
    item.append(entry);
  }
}

// Taille de texte imposée à tout l'article par la source (\large, \fontsize…) : LaTeXML l'écrit sur chaque
// morceau de texte (style="font-size:144%") mais pas sur les citations, titres d'abstract, mots-clés…,
// qui paraissent alors minuscules. Si une même taille couvre l'essentiel des paragraphes, elle devient la
// taille normale et les autres tailles sont gardées en proportion.
export function normalizeFontScale(content) {
  const percent = (element) => Number(element.style.fontSize.match(/^([\d.]+)%$/)?.[1]) || null;
  // vraie propriété font-size (pas la variable --font-size de la page), et seulement dans l'article
  const sizedParent = (element) => {
    for (let parent = element.parentElement; parent && parent !== content; parent = parent.parentElement) {
      if (parent.style.fontSize) return parent;
    }
    return null;
  };
  const sized = [...content.querySelectorAll('[style*="font-size"]')].filter((element) => element.style.fontSize);
  const outermost = sized.filter((element) => percent(element) && element.closest(".ltx_para") && !sizedParent(element));
  const share = new Map();
  for (const element of outermost) share.set(percent(element), (share.get(percent(element)) || 0) + element.textContent.length);
  // texte des paragraphes hors formules (les formules ne sont jamais dans les morceaux agrandis)
  let total = 0;
  for (const para of content.querySelectorAll(".ltx_para")) {
    const walker = document.createTreeWalker(para, NodeFilter.SHOW_TEXT,
      { acceptNode: (node) => (node.parentElement.closest("math") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
    while (walker.nextNode()) total += walker.currentNode.data.length;
  }
  const [scale, covered] = [...share].sort((a, b) => b[1] - a[1])[0] || [];
  if (!scale || scale === 100 || covered < 0.6 * total) return;
  for (const element of sized) {
    const value = percent(element);
    if (!value || sizedParent(element)) continue; // une taille imbriquée est relative à son parent : inchangée
    const next = (100 * value) / scale;
    if (Math.abs(next - 100) < 1) element.style.removeProperty("font-size");
    else element.style.fontSize = `${Math.round(next)}%`;
  }
  // Les formules portent le même agrandissement : mathsize="1.440em" sur chaque symbole, et
  // minsize/maxsize="1.440em" sur les délimiteurs (parenthèses alors trop hautes) : idem
  const rescale = (element, attribute) => {
    const [, number, unit] = element.getAttribute(attribute).match(/^([\d.]+)(em|%)$/) || [];
    if (!number) return;
    const ratio = (unit === "%" ? number / 100 : Number(number)) / (scale / 100);
    if (Math.abs(ratio - 1) < 0.01) element.removeAttribute(attribute);
    else element.setAttribute(attribute, `${ratio.toFixed(3)}em`);
  };
  for (const element of content.querySelectorAll("[mathsize]")) {
    if (!element.parentElement?.closest("[mathsize]")) rescale(element, "mathsize"); // imbriqué : relatif au parent
  }
  // délimiteurs : seule la taille égale à l'agrandissement global est retirée ; \big, \Big… gardent la leur
  const isScale = (value) => {
    const [, number, unit] = String(value || "").match(/^([\d.]+)(em|%)$/) || [];
    return number && Math.abs((unit === "%" ? number / 100 : Number(number)) - scale / 100) < 0.01;
  };
  for (const element of content.querySelectorAll("mo[minsize], mo[maxsize]")) {
    if (isScale(element.getAttribute("minsize")) && (!element.hasAttribute("maxsize") || isScale(element.getAttribute("maxsize")))) {
      element.removeAttribute("minsize");
      element.removeAttribute("maxsize");
    }
  }
}

// \thanks d'amsart : LaTeXML les met dans le titre ; ils rejoignent les notes de première page
// (mots-clés, classification), sous le résumé.
export function moveTitleNotes(content) {
  const notes = [...content.querySelectorAll(".ltx_title_document .ltx_pubnote")];
  if (!notes.length) return;
  const after = [...content.querySelectorAll(".ltx_abstract, .ltx_keywords, .ltx_classification, .ltx_dates, .ltx_authors")].at(-1);
  const holder = document.createElement("div");
  holder.className = "ltx_titlenotes";
  for (const note of notes) {
    note.querySelector(".ltx_note_name")?.remove();
    const line = document.createElement("div");
    line.className = "ltx_titlenote";
    line.append(...note.childNodes);
    holder.append(line);
  }
  content.querySelectorAll(".ltx_title_document .ltx_pubnotes").forEach((element) => element.remove());
  (after || content.querySelector(".ltx_title_document"))?.after(holder);
}

// Délimiteurs : en TeX, ( ) [ ] ⟨ ⟩ | / ne grandissent qu'avec \left…\right (ou \middle).
// LaTeXML les laisse parfois « extensibles » (attribut absent, ou stretchy="true" pour / et ⟨ ⟩) :
// MathJax les agrandit alors selon leur contenu (« Ad(K) », « G/P_Θ » aux parenthèses trop hautes).
// Dans une formule sans \left ni \middle, ils reprennent leur taille fixe. Les tailles explicites
// (\big, \bigl… : attributs minsize/maxsize) ne sont pas touchées.
const FENCES = new Set(["(", ")", "[", "]", "{", "}", "⟨", "⟩", "|", "‖", "∥", "/", "⌊", "⌋", "⌈", "⌉"]);
const RELATION_RE = /^(?:[=<>≤≥≠≈≡∼≃≅∝∈∉∋⊂⊃⊆⊇≺≻⪯⪰≪≫→←↔⟶⟵↦⟼⇒⇐⇔⟹⟸⟺≔]|:=)$/;
// Caractères absents des polices de MathJax, remplacés par leur équivalent TeX
const GLYPHS = { "⟂": "⊥" };

// Délimiteurs d'une matrice ou d'un tableau : toujours extensibles. (Pas les fractions : sans \left, TeX
// garde de petites parenthèses autour d'une fraction, et les agrandir élargirait les équations.)
const TALL = ":scope > mtable, :scope > mrow > mtable";

// `macros` : macros de l'auteur qui produisent \left…\right (lues dans la source TeX à l'import)
// Formule que LaTeXML n'a pas su analyser (souvent dans gathered, multline…) : indices et exposants
// laissés sur une base vide (<msub><mrow/>…) après le symbole, d'où « Ψ ₛ », « exp ₓ₀ ^Vₛ » décalés.
// Rattachés au symbole (ou à la parenthèse fermante) qui précède ; indice puis exposant réunis en
// <msubsup>. Un préfixe voulu ({}^{t}A), placé après un opérateur ou en tête, n'est pas touché.
const CLOSERS = new Set([")", "]", "}", "|", "‖", "⟩", "′", "″", "*", "∗", "!"]);
// Grands opérateurs : indices et exposants dessous et dessus (en display, comme \sum_{r\ge1} en TeX),
// à côté en ligne (movablelimits) ; intégrales : toujours à côté
const LIMIT_OPERATORS = new Set(["∑", "∏", "∐", "⋃", "⋂", "⨁", "⨂", "⨆", "⋁", "⋀", "⨄"]);
const SIDE_OPERATORS = new Set(["∫", "∬", "∭", "∮", "⨌"]);

function attachScripts(math) {
  const ns = math.namespaceURI;
  for (const script of [...math.querySelectorAll("msub, msup, msubsup")]) {
    const base = script.firstElementChild;
    if (!base || base.localName !== "mrow" || base.childElementCount || base.textContent.trim()) continue;
    const previous = script.previousElementSibling;
    if (!previous || previous.localName === "mspace") continue;
    const merged = previous.getAttribute("data-attached");
    // rôles (indice, exposant) du script courant
    const [sub, sup] = script.localName === "msub" ? [script.children[1], null]
      : script.localName === "msup" ? [null, script.children[1]] : [script.children[1], script.children[2]];
    if (merged) {
      // x_a^b, ∑_{a}^{b} : premier script déjà rattaché, le second se joint à lui
      const [prevBase, ...prevScripts] = [...previous.children];
      const limits = previous.localName.startsWith("mu") || previous.localName === "mover";
      const prevSub = ["msub", "munder"].includes(previous.localName) ? prevScripts[0] : null;
      const prevSup = ["msup", "mover"].includes(previous.localName) ? prevScripts[0] : null;
      if ((prevSub && sub) || (prevSup && sup) || prevScripts.length > 1) continue;
      const both = document.createElementNS(ns, limits ? "munderover" : "msubsup");
      both.append(prevBase, prevSub || sub, prevSup || sup);
      both.setAttribute("data-attached", "1");
      previous.replaceWith(both);
      script.remove();
      continue;
    }
    const text = previous.localName === "mo" ? previous.textContent.trim() : "";
    if (previous.localName === "mo" && !CLOSERS.has(text) && !LIMIT_OPERATORS.has(text) && !SIDE_OPERATORS.has(text)) continue;
    if (LIMIT_OPERATORS.has(text)) {
      const kind = { msub: "munder", msup: "mover", msubsup: "munderover" }[script.localName];
      const limits = document.createElementNS(ns, kind);
      limits.append(previous.cloneNode(true), ...[...script.children].slice(1));
      limits.setAttribute("data-attached", "1");
      previous.replaceWith(limits);
      script.remove();
      continue;
    }
    base.replaceWith(groupFences(previous) || previous);
    script.setAttribute("data-attached", "1");
  }
}

// « \left( … \right)^2 » : l'exposant porte sur tout le groupe, comme en TeX. Rattaché à la seule
// parenthèse fermante, MathJax le placerait d'après sa taille normale (exposant à mi-hauteur d'une
// parenthèse agrandie). Le groupe, de la parenthèse ouvrante assortie (même niveau) à la fermante,
// est réuni dans un <mrow> ; sans ouvrante assortie, null (exposant sur la fermante seule).
const OPENER_OF = { ")": "(", "]": "[", "}": "{", "⟩": "⟨" };

function groupFences(closer) {
  const close = closer.localName === "mo" ? closer.textContent.trim() : "";
  const open = OPENER_OF[close];
  if (!open) return null;
  // remontée parmi les voisins, toutes sortes de délimiteurs comptées : l'ouvrante qui répond doit être
  // de la même sorte (« [0,1)×(0,1]^2 » : pas de groupe, l'exposant reste sur le crochet)
  let depth = 0;
  for (let node = closer.previousElementSibling; node; node = node.previousElementSibling) {
    const text = node.localName === "mo" ? node.textContent.trim() : "";
    if (text in OPENER_OF) depth++;
    else if (Object.values(OPENER_OF).includes(text) && depth-- === 0) {
      if (text !== open) return null;
      const row = document.createElementNS(closer.namespaceURI, "mrow");
      node.before(row);
      while (row.nextElementSibling !== closer) row.append(row.nextElementSibling);
      row.append(closer);
      return row;
    }
  }
  return null;
}

export function fixMathML(content, macros = []) {
  const authorSized = macros.length ? new RegExp(`\\\\(${macros.join("|")})(?![A-Za-z])`) : null;
  for (const math of content.querySelectorAll("math")) {
    attachScripts(math);
    // Lignes d'un gathered/split/aligned : LaTeXML met rowspacing="0pt", TeX laisse \jot (3 pt ≈ 0,3 em)
    math.querySelectorAll('mtable[displaystyle="true"][rowspacing="0pt"]').forEach((table) => table.setAttribute("rowspacing", "0.3em"));
    const tex = math.getAttribute("alttext") || "";
    // délimiteurs voulus extensibles : \left…\right, \middle, matrices et cas, \binom, \choose…, macros de l'auteur
    const sized = /\\left\b|\\middle\b|\\right\b|\\begin\{[pbBvV]?matrix\*?\}|\\begin\{cases\}|\\[dt]?binom\b|\\genfrac\b|\\choose\b|\\brace\b|\\brack\b|\\atopwithdelims\b/.test(tex)
      || Boolean(authorSized?.test(tex));
    for (const mo of math.querySelectorAll("mo")) {
      const text = mo.textContent.trim();
      // parenthèse ouverte hors de la formule (source déséquilibrée) : LaTeXML laisse « OPEN » ou « CLOSE »
      if ((text === "OPEN" || text === "CLOSE") && mo.getAttribute("fence") === "true") { mo.remove(); continue; }
      if (GLYPHS[text]) mo.textContent = GLYPHS[text];
      const aroundTable = mo.parentElement?.querySelector(TALL);
      if (!sized && !aroundTable && FENCES.has(text) && !mo.hasAttribute("minsize") && !mo.hasAttribute("maxsize")
          && mo.getAttribute("stretchy") !== "false") {
        mo.setAttribute("stretchy", "false");
      }
    }
    // Relation au bord d'une cellule alignée (« &= », « &\leq », ou « = » seul dans sa colonne) : en TeX,
    // l'espace d'une relation (\thickmuskip) est mise des deux côtés ; MathJax l'omet au bord de la
    // formule. Espaces rétablies par des <mspace> (que MathJax respecte toujours).
    if (math.closest("td.ltx_eqn_cell")) {
      const tokens = [...math.querySelectorAll("mi, mo, mn, mtext")].filter((token) => token.textContent.trim());
      const isRelation = (token) => token?.localName === "mo" && RELATION_RE.test(token.textContent.trim());
      const space = (token, where) => {
        if (token.parentElement?.localName === "semantics") { // <mo> seul dans la formule : entouré d'un <mrow>
          const row = document.createElementNS(token.namespaceURI, "mrow");
          token.replaceWith(row);
          row.append(token);
        }
        if (!["mrow", "mstyle", "mpadded"].includes(token.parentElement?.localName)) return;
        const gap = document.createElementNS(token.namespaceURI, "mspace");
        gap.setAttribute("width", "0.2778em");
        token[where](gap);
      };
      if (isRelation(tokens[0])) space(tokens[0], "before");
      if (isRelation(tokens.at(-1)) && tokens.length === 1) space(tokens[0], "after");
    }
    for (const mi of math.querySelectorAll("mi")) {
      if (GLYPHS[mi.textContent.trim()]) mi.textContent = GLYPHS[mi.textContent.trim()];
    }
    // Virgule en fin de cellule alignée (« h_s(Y,x_m),& h_s(Y',x'_m) » dans split) : la ligne continue
    // dans la cellule suivante, mais l'espace après la virgule se perdait ; espace fine (\,) rétablie
    for (const cell of math.querySelectorAll("mtd")) {
      const tokens = [...cell.querySelectorAll("mi, mo, mn, mtext")].filter((token) => token.textContent.trim());
      const last = tokens.at(-1);
      if (last?.localName !== "mo" || last.textContent.trim() !== ",") continue;
      if (!["mrow", "mtd", "mstyle", "mpadded"].includes(last.parentElement?.localName)) continue;
      let next = cell.nextElementSibling;
      while (next && !next.textContent.trim()) next = next.nextElementSibling;
      if (!next || /^[=<>≤≥≈∼≃≅≡∈∉⊂⊆⊃⊇→↦⟶:]/.test(next.textContent.trim())) continue;
      last.after(document.createElementNS(last.namespaceURI, "mspace"));
      last.nextSibling.setAttribute("width", "0.167em");
    }
  }
}

// Retours à la ligne dans les formules en ligne : comme TeX, une longue formule peut passer à la ligne
// après une relation (=, ∈, →…) ou un + / − binaire, au premier niveau (hors parenthèses). Elle est
// découpée en morceaux réunis dans un <span class="math-group"> qui compte pour UNE formule (repères
// des surlignages, texte cité) ; <wbr> entre les morceaux : le navigateur peut y couper la ligne.
const BREAK_AFTER = /^(?:[=<>≤≥≠≈≡∼≃≅∝∈∉∋⊂⊃⊆⊇→←↔⟶⟵↦⟼⇒⇐⇔⟹⟸⟺≔]|:=|[+−])$/;
const OPEN_FENCES = new Set(["(", "[", "{", "⟨", "⌊", "⌈"]);
const CLOSE_FENCES = new Set([")", "]", "}", "⟩", "⌋", "⌉"]);
const MATHML = "http://www.w3.org/1998/Math/MathML";

export function splitLongInlineMath(content) {
  for (const math of [...content.querySelectorAll('math[display="inline"]')]) {
    if (!math.closest(".ltx_p") || math.closest("td, .ltx_title, .figure-card, .ltx_note")) continue;
    const tex = math.getAttribute("alttext") || "";
    if (tex.length < 30) continue;
    // barres \left| … \right| (ou \middle) : les deux moitiés n'auraient plus la même hauteur
    if (/\\left\s*(\||\\[|]|\\[lV]?vert|\\Vert|\\lVert)|\\middle/.test(tex)) continue;
    let row = math.querySelector(":scope > semantics")?.firstElementChild;
    while (row?.localName === "mrow" && row.childElementCount === 1 && row.firstElementChild.localName === "mrow") row = row.firstElementChild;
    if (row?.localName !== "mrow") continue;
    const children = [...row.children];
    const pieces = [[]];
    let depth = 0;
    children.forEach((child, i) => {
      pieces.at(-1).push(child);
      const text = child.localName === "mo" ? child.textContent.trim() : "";
      if (OPEN_FENCES.has(text)) depth++;
      else if (CLOSE_FENCES.has(text)) depth = Math.max(0, depth - 1);
      const unary = (text === "+" || text === "−") && (i === 0 || children[i - 1].localName === "mo");
      if (!depth && !unary && BREAK_AFTER.test(text) && i < children.length - 1 && i > 0) pieces.push([]);
    });
    if (pieces.length < 2) continue;
    const group = document.createElement("span");
    group.className = "math-group";
    group.dataset.tex = tex;
    pieces.forEach((nodes, index) => {
      const piece = document.createElementNS(MATHML, "math");
      piece.setAttribute("display", "inline");
      piece.setAttribute("class", math.getAttribute("class") || "");
      if (index === 0 && math.id) piece.id = math.id;
      const semantics = document.createElementNS(MATHML, "semantics");
      const mrow = document.createElementNS(MATHML, "mrow");
      mrow.append(...nodes);
      // relation ou opérateur en fin de morceau : son espace de droite (MathJax l'omet en bord de formule)
      const last = nodes.at(-1);
      if (index < pieces.length - 1 && last?.localName === "mo") {
        const gap = document.createElementNS(MATHML, "mspace");
        gap.setAttribute("width", /^[+−]$/.test(last.textContent.trim()) ? "0.2222em" : "0.2778em");
        mrow.append(gap);
      }
      semantics.append(mrow);
      piece.append(semantics);
      if (index) group.append(document.createElement("wbr"));
      group.append(piece);
    });
    math.replaceWith(group);
  }
}
