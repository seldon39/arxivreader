// Corrections pour rapprocher la version HTML d'arXiv du PDF compilé (classe amsart) :
// numéros d'équation, étiquettes des listes, bibliographie amsplain, énoncés en italique.

import { t } from "./i18n.js";
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
  // délimiteurs : seule la taille égale à l'agrandissement global est retirée ; \big, \Big… gardent la leur.
  // En indice, LaTeXML exprime cet agrandissement dans la taille des indices (1,44 / 0,7 ≈ 2,05 em) :
  // un ⟨Θ⟩ sous un ∑ deviendrait aussi haut que la formule
  const isScale = (value, element) => {
    const [, number, unit] = String(value || "").match(/^([\d.]+)(em|%)$/) || [];
    if (!number) return false;
    const ratio = (unit === "%" ? number / 100 : Number(number)) / (scale / 100);
    const factors = inScript(element) ? [1, 1 / 0.7, 1 / 0.5] : [1];
    return factors.some((factor) => Math.abs(ratio / factor - 1) < 0.01);
  };
  for (const element of content.querySelectorAll("mo[minsize], mo[maxsize]")) {
    if (isScale(element.getAttribute("minsize"), element) && (!element.hasAttribute("maxsize") || isScale(element.getAttribute("maxsize"), element))) {
      element.removeAttribute("minsize");
      element.removeAttribute("maxsize");
    }
  }
}

// Élément placé en indice ou en exposant (y compris sous ou sur un grand opérateur)
const SCRIPTS = new Set(["msub", "msup", "msubsup", "munder", "mover", "munderover", "mmultiscripts"]);
function inScript(element) {
  for (let node = element; node?.parentElement && node.localName !== "math"; node = node.parentElement) {
    if (SCRIPTS.has(node.parentElement.localName) && node !== node.parentElement.firstElementChild) return true;
  }
  return false;
}

// Affiliations et adresses des auteurs (notes d'auteur de LaTeXML, cachées, et \thanks d'un auteur, affichés en
// ligne avec « thanks: ») : une ligne par auteur, son nom (et sa marque) en tête (« Nom† — Université · adresse@… »).
// Un bloc regroupant plusieurs auteurs (« A, B et C ») : les notes d'une même sorte (adresse, courriel…) aussi
// nombreuses que les noms vont aux auteurs dans l'ordre (comme les \address qui suivent chaque \author), les autres
// sont communes et sans nom. LaTeXML rattache parfois les \address et \email de plusieurs auteurs au dernier : si ces
// notes se coupent en autant de groupes « adresse…, courriel » que d'auteurs, chacun reçoit le sien. Plus de huit
// lignes : repliées.
const MANY_AFFILIATIONS = 8;
const INITIAL = /^\p{Lu}\.(-?\p{Lu}\.)*$/u;

// Notes à la suite (\address, \email d'amsart) : un groupe par auteur, fini par son courriel ; null si elles ne se
// coupent pas ainsi en `count` groupes qui ont chacun une adresse
function emailGroups(notes, count) {
  const groups = [[]];
  notes.forEach((note, i) => { groups.at(-1).push(note); if (note.role === "email" && i < notes.length - 1) groups.push([]); });
  const complete = groups.every((group) => group.at(-1).role === "email" && group.some((note) => note.role !== "email"));
  return groups.length === count && complete ? groups : null;
}

// Texte d'une note sans son étiquette (« Address: », « thanks: ») ni la marque de l'auteur
function noteCopy(note, marks) {
  const copy = note.cloneNode(true);
  copy.querySelectorAll(".ltx_contact_name, .ltx_note_type, .ltx_note_mark").forEach((element) => element.remove());
  copy.querySelectorAll("sup").forEach((sup) => { if (marks.includes(sup.textContent.trim())) sup.remove(); });
  // « M.˜Nom » : LaTeXML rend parfois l'espace insécable ~ d'un \thanks par un tilde
  const walker = document.createTreeWalker(copy, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) node.data = node.data.replace(/(\S)˜(?=\S)/g, "$1\u00a0");
  return copy;
}

// Note qui commence par le nom de l'auteur (« Nom: Université… », « Nom, Université… », « Nom ↵ Université… », « N. Nom: … ») :
// ce nom est retiré, il est déjà en tête de ligne
function dropLeadingName(copy, name) {
  const words = name.split(/\s+/);
  const first = [...copy.childNodes].find((node) => node.textContent.trim());
  if (first?.nodeType !== Node.TEXT_NODE) return;
  const [, lead, rest] = first.data.match(/^\s*([^:,\n]{1,60}?)\s*(?::|,|\n|$)([\s\S]*)$/) || [];
  const tokens = (lead || "").split(/\s+/);
  if (tokens.at(-1) !== words.at(-1) || !tokens.slice(0, -1).every((token) => words.includes(token) || INITIAL.test(token))) return;
  first.data = rest;
  while (copy.firstChild && (copy.firstChild.localName === "br" || (copy.firstChild.nodeType === Node.TEXT_NODE && !copy.firstChild.data.trim()))) copy.firstChild.remove();
}

function authorLines(content) {
  const lines = [];
  const line = (lead, parts) => {
    const element = h("div", { class: "ltx_titlenote ltx_affiliation" });
    if (lead) element.append(lead);
    parts.forEach((part, i) => { if (i) element.append(" · "); element.append(...part.childNodes); });
    lines.push(element);
  };
  const named = (name, notes, marks = "") => {
    notes.forEach((note) => dropLeadingName(note.copy, name));
    line(`${name}${marks} — `, notes.map((note) => note.copy));
  };
  let waiting = []; // auteurs sans notes juste avant
  for (const creator of content.querySelectorAll(".ltx_authors .ltx_creator")) {
    const person = creator.querySelector(".ltx_personname");
    const marks = person ? [...person.querySelectorAll("sup")].map((sup) => sup.textContent.trim()).filter(Boolean) : [];
    const plain = person?.cloneNode(true);
    plain?.querySelectorAll("sup").forEach((sup) => sup.remove());
    const name = (plain?.textContent || "").replace(/\s+/g, " ").replace(/\s+,/g, ",").trim();
    const names = name.split(/\s*,\s*|\s+and\s+|\s+et\s+(?!al\.)/).map((part) => part.trim()).filter(Boolean);
    const contacts = [...creator.querySelectorAll(".ltx_author_notes .ltx_contact")];
    const thanks = [...creator.querySelectorAll(".ltx_thanks_note .ltx_note_content")];
    const notes = [...contacts.map((contact) => ({ role: contact.className.match(/ltx_role_(\w+)/)?.[1], copy: noteCopy(contact, marks) })),
      ...thanks.map((note) => ({ role: "thanks", copy: noteCopy(note, marks) }))].filter((note) => note.copy.textContent.trim());
    creator.querySelectorAll(".ltx_author_notes, .ltx_thanks_note").forEach((element) => element.remove());
    if (!notes.length) { waiting.push(...names); continue; }
    const everyone = [...waiting, ...names];
    waiting = [];
    const groups = everyone.length > 1 ? emailGroups(notes, everyone.length) : null;
    if (groups) { groups.forEach((group, i) => named(everyone[i], group)); continue; }
    if (names.length <= 1) {
      if (name) named(name, notes, marks.join(""));
      else line("", notes.map((note) => note.copy));
      continue;
    }
    const shared = [];
    const own = names.map(() => []);
    for (const role of new Set(notes.map((note) => note.role))) {
      const group = notes.filter((note) => note.role === role);
      if (group.length !== names.length) shared.push(...group);
      else group.forEach((note, i) => own[i].push(note));
    }
    shared.forEach((note) => line(marks.length === 1 ? `${marks[0]} ` : "", [note.copy]));
    own.forEach((parts, i) => { if (parts.length) named(names[i], parts); });
  }
  lines.forEach((element) => element.normalize());
  if (lines.length <= MANY_AFFILIATIONS) return lines;
  return [h("details", { class: "ltx_titlenote ltx_affiliations" }, h("summary", {}, t("reader.affiliations", { n: lines.length })), ...lines)];
}

// \thanks d'amsart : LaTeXML les met dans le titre ; ils rejoignent les notes de première page
// (mots-clés, classification), sous le résumé. De même pour les notes sans appel du début de l'article
// (\footnotetext : financements, adresses), que LaTeXML numérote « 0 » et place avant le titre.
export function moveTitleNotes(content) {
  const firstSection = content.querySelector("section.ltx_section, .ltx_section, h2.ltx_title_section");
  const before = (element) => !firstSection || Boolean(element.compareDocumentPosition(firstSection) & Node.DOCUMENT_POSITION_FOLLOWING);
  for (const note of content.querySelectorAll(".ltx_note.ltx_role_footnotetext")) {
    if (!before(note)) continue;
    const text = note.querySelector(".ltx_note_content");
    if (!text) { note.remove(); continue; }
    text.querySelectorAll(":scope > .ltx_note_mark, :scope > .ltx_note_type").forEach((element) => element.remove());
    const pubnote = document.createElement("span");
    pubnote.className = "ltx_pubnote";
    pubnote.append(...text.childNodes);
    note.replaceWith(pubnote);
    pubnote.dataset.frontNote = "1";
  }
  const notes = [...content.querySelectorAll(".ltx_title_document .ltx_pubnote, .ltx_pubnote[data-front-note]")];
  const affiliations = authorLines(content);
  if (!notes.length && !affiliations.length) return;
  const after = [...content.querySelectorAll(".ltx_abstract, .ltx_keywords, .ltx_classification, .ltx_dates, .ltx_authors")].at(-1);
  const holder = document.createElement("div");
  holder.className = "ltx_titlenotes";
  holder.append(...affiliations);
  for (const note of notes) {
    note.querySelector(".ltx_note_name")?.remove();
    const line = document.createElement("div");
    line.className = "ltx_titlenote";
    line.append(...note.childNodes);
    holder.append(line);
    if (note.dataset.frontNote) note.remove(); // note de début d'article : vidée, place libérée
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
    // F^{{}^{\prime}} : base vide dans l'exposant même ; son voisin est la base F, à laisser en place
    if (SCRIPTS.has(script.parentElement?.localName)) continue;
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
    // « := » coupé en deux par LaTeXML (« : » puis « = », parfois séparés par un groupe vide) : sans espace entre eux
    const tokens = [...math.querySelectorAll("mi, mn, mo, mtext, ms")];
    tokens.forEach((token, i) => {
      const next = tokens[i + 1];
      if (token.localName === "mo" && token.textContent.trim() === ":" && next?.localName === "mo" && next.textContent.trim() === "=") {
        token.setAttribute("rspace", "0em");
        next.setAttribute("lspace", "0em");
      }
    });
    for (const mo of math.querySelectorAll("mo")) {
      const text = mo.textContent.trim();
      // parenthèse ouverte hors de la formule (source déséquilibrée) : LaTeXML laisse « OPEN » ou « CLOSE »,
      // en lettres mathématiques (« 𝑂𝑃𝐸𝑁 ») dans un énoncé en italique : comparé après normalisation NFKC
      const marker = text.normalize("NFKC");
      if ((marker === "OPEN" || marker === "CLOSE") && mo.getAttribute("fence") === "true") { mo.remove(); continue; }
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
      // « &:= » coupé en « : » puis « = » : même espace avant (attribut lspace, que MathJax respecte en tête)
      else if (tokens[0]?.localName === "mo" && tokens[0].textContent.trim() === ":" && tokens[1]?.textContent.trim() === "="
        && !tokens[0].hasAttribute("lspace")) tokens[0].setAttribute("lspace", "0.2778em");
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
