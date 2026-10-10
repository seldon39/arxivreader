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
  const total = [...content.querySelectorAll(".ltx_para")].reduce((n, para) => n + para.textContent.length, 0);
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
  for (const element of content.querySelectorAll("mo[minsize], mo[maxsize]")) {
    if (element.hasAttribute("minsize")) rescale(element, "minsize");
    if (element.hasAttribute("maxsize")) rescale(element, "maxsize");
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
// Caractères absents des polices de MathJax, remplacés par leur équivalent TeX
const GLYPHS = { "⟂": "⊥" };

export function fixMathML(content) {
  for (const math of content.querySelectorAll("math")) {
    const tex = math.getAttribute("alttext") || "";
    // délimiteurs voulus extensibles : \left…\right, \middle, matrices et cas (pmatrix, cases…), \binom
    const sized = /\\left\b|\\middle\b|\\right\b|\\begin\{[pbBvV]?matrix\*?\}|\\begin\{cases\}|\\[dt]?binom\b|\\genfrac\b/.test(tex);
    for (const mo of math.querySelectorAll("mo")) {
      const text = mo.textContent.trim();
      // parenthèse ouverte hors de la formule (source déséquilibrée) : LaTeXML laisse « OPEN » ou « CLOSE »
      if ((text === "OPEN" || text === "CLOSE") && mo.getAttribute("fence") === "true") { mo.remove(); continue; }
      if (GLYPHS[text]) mo.textContent = GLYPHS[text];
      const aroundTable = mo.parentElement?.querySelector(":scope > mtable, :scope > mrow > mtable");
      if (!sized && !aroundTable && FENCES.has(text) && !mo.hasAttribute("minsize") && !mo.hasAttribute("maxsize")
          && mo.getAttribute("stretchy") !== "false") {
        mo.setAttribute("stretchy", "false");
      }
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
      let next = cell.nextElementSibling;
      while (next && !next.textContent.trim()) next = next.nextElementSibling;
      if (!next || /^[=<>≤≥≈∼≃≅≡∈∉⊂⊆⊃⊇→↦⟶:]/.test(next.textContent.trim())) continue;
      last.after(document.createElementNS(last.namespaceURI, "mspace"));
      last.nextSibling.setAttribute("width", "0.167em");
    }
  }
}
