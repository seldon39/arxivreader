// Articles sans version HTML : énoncés rendus à partir de leur source TeX (formules par MathJax), pour le
// panneau (énoncés, dépendances, graphe) et les aperçus. L'article lui-même se lit dans le PDF.

import { DEFAULT_MACROS, typesetTex } from "./math.js";
import { escapeHtml } from "./util.js";


// Contenu d'un groupe {…} à partir de `start` (accolade ouvrante) : [contenu, fin]
function braced(text, start) {
  let depth = 0;
  for (let i = start; i < text.length; i++) {
    if (text[i] === "\\") { i++; continue; }
    if (text[i] === "{") depth++;
    else if (text[i] === "}" && --depth === 0) return [text.slice(start + 1, i), i + 1];
  }
  return [text.slice(start + 1), text.length];
}

// Remplace \cmd{arg} (un argument) par f(arg), accolades imbriquées comprises
function replaceCommand(text, names, f) {
  const pattern = new RegExp(`\\\\(${names})\\*?\\s*(\\[[^\\]]*\\])?\\s*\\{`, "g");
  let out = "";
  let last = 0;
  for (let match; (match = pattern.exec(text));) {
    const [arg, end] = braced(text, match.index + match[0].length - 1);
    out += text.slice(last, match.index) + f(arg, match[1], match[2]?.slice(1, -1));
    last = end;
    pattern.lastIndex = end;
  }
  return out + text.slice(last);
}

// TeX d'un énoncé → HTML : formules gardées pour MathJax ($…$, \[…\], environnements), texte mis en forme
function texToHtml(tex, { labels = {}, nodes = new Map(), bib = new Map(), equations = {} } = {}) {
  let text = tex.replace(/\\label\s*(\[[^\]]*\])?\s*\{[^}]*\}/g, "");
  const refText = (keys, command) => keys.split(",").map((key) => {
    const node = nodes.get(labels[key.trim()]);
    if (!node) return equations[key.trim()]?.label?.replace(/^\((.*)\)$/, "$1") || "?";  // équation (numéro du .aux)
    const number = node.label.match(/(\S+)$/)?.[1] || node.label;
    return /^(cref|Cref|autoref)$/.test(command) ? node.label : number;
  }).join(", ");
  text = replaceCommand(text, "ref|cref|Cref|autoref|eqref", (arg, command) =>
    command === "eqref" ? arg.split(",").map((key) => equations[key.trim()]?.label || "(·)").join(", ") : refText(arg, command));
  // natbib et biblatex (\parencite, \textcite…)
  text = replaceCommand(text, "cite|citep|citet|Cite|citealp|citealt|parencite|Parencite|textcite|Textcite|autocite|Autocite|footcite", (arg, _, note) => {
    const labelsText = arg.split(",").map((key) => bib.get(`bib.${key.trim()}`)?.label || "?").join(", ");
    return `[${labelsText}${note ? `, ${note}` : ""}]`;
  });
  // formules mises de côté (elles ne sont pas mises en forme comme du texte)
  const maths = [];
  const keep = (match) => { maths.push(match); return `\u0000${maths.length - 1}\u0000`; };
  text = text.replace(/\\begin\{(equation|align|gather|multline|eqnarray|flalign|alignat)(\*?)\}[\s\S]*?\\end\{\1\2\}/g, keep)
    .replace(/\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$(?:\\.|[^$\\])+\$/g, keep);
  text = escapeHtml(text);
  text = replaceCommand(text, "emph|textit|textsl", (arg) => `<em>${arg}</em>`);
  text = replaceCommand(text, "textbf", (arg) => `<strong>${arg}</strong>`);
  text = replaceCommand(text, "textrm|textup|text|mbox|textnormal|textsc", (arg) => arg);
  text = text.replace(/\\begin\{(itemize|enumerate)\}(\[[^\]]*\])?/g, (_, kind) => (kind === "itemize" ? "<ul>" : "<ol>"))
    .replace(/\\end\{(itemize|enumerate)\}/g, (_, kind) => (kind === "itemize" ? "</ul>" : "</ol>"))
    .replace(/\\item\s*(\[[^\]]*\])?/g, (_, label) => `<li>${label ? `<span class="tex-item-label">${label.slice(1, -1)}</span> ` : ""}`)
    .replace(/\\(?:noindent|medskip|smallskip|bigskip|par|newline|hfill|qed|quad|qquad)\b/g, " ")
    .replace(/\\\\/g, "<br>")
    .replace(/~/g, " ")
    .replace(/\\(?:begin|end)\{[^}]*\}/g, " ")
    .replace(/\n\s*\n/g, "</p><p>");
  text = text.replace(/\u0000(\d+)\u0000/g, (_, i) => escapeHtml(maths[Number(i)]));
  return `<p>${text}</p>`;
}

// Énoncé (au format des énoncés LaTeXML, pour réutiliser styles et aperçus)
export function statementElement(node, context) {
  const element = document.createElement("div");
  element.className = `ltx_theorem ltx_theorem_${node.kind}`;
  if (node.id) element.id = node.id;
  element.dataset.kind = node.kind;
  element.innerHTML = `<h6 class="ltx_title ltx_title_theorem"><span class="ltx_tag ltx_tag_theorem">${escapeHtml(node.label)}</span>`
    + `${node.name ? ` (${escapeHtml(node.name)})` : ""}.</h6><div class="ltx_para">${texToHtml(node.tex || "", context)}</div>`;
  return element;
}

// Macros de l'auteur, connues de MathJax pour la suite (\newcommand vaut pour les formules suivantes). Changement
// d'article, ou page sans article (macros = []) : les macros courantes (\R, \abs…) reprennent d'abord leur sens,
// au cas où l'article précédent les aurait redéfinies
let defined = "";
export async function defineMacros(macros = []) {
  const key = macros.join("");
  if (key === defined) return;
  defined = key;
  const holder = document.createElement("div");
  holder.className = "tex-macros";
  holder.textContent = `\\(${[...DEFAULT_MACROS, ...macros].join(" ")}\\)`;
  document.body.append(holder);
  await typesetTex([holder]);
  holder.remove();
}

export function statementsContainer(structure) {
  const nodes = new Map(structure.nodes.map((n) => [n.id, n]));
  const bib = new Map((structure.bibliography || []).map((b) => [b.id, b]));
  const context = { labels: structure.labels || {}, nodes, bib, equations: structure.equations || {} };
  const container = document.createElement("div");
  container.className = "paper-content tex-statements";
  container.setAttribute("aria-hidden", "true");
  structure.nodes.forEach((node) => container.append(statementElement(node, context)));
  return container;
}
