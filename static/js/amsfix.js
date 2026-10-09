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
