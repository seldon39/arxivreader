// Annotations dans le lecteur : passages surlignés (avec note), notes et état de lecture des énoncés
// et preuves, fiche de lecture ; onglet « Notes » du panneau.
//
// Repérage d'un passage : (élément avec id le plus proche, décalage) au début et à la fin, le décalage
// étant compté sur un « texte logique » où chaque formule vaut un seul caractère. Ainsi le repère est le
// même que les formules soient rendues par MathJax ou par le navigateur. Le texte surligné est gardé
// pour l'afficher dans la liste des notes.

import { authorName } from "./aiproviders.js";
import { api } from "./api.js";
import { t } from "./i18n.js";
import { markdownToHtml } from "./markdown.js";
import { typesetTex } from "./math.js";
import { debounce, h, icons, toast, toastError } from "./util.js";

export const COLORS = ["yellow", "green", "blue", "pink"];
export const STATES = ["understood", "review", "verified"];
const UI = ".proof-toggle, .block-note, .state-pill, .proof-pointer, .picture-missing"; // ajouts de l'application (pas du texte)

// ---------- Texte logique ----------

// Une formule : un rendu MathJax, une formule MathML, ou un groupe de morceaux (formule longue coupée)
const MATH = ".math-group, mjx-container, math";
const isMath = (node) => node.nodeType === 1 && node.matches(MATH) && !node.parentElement?.closest(".math-group");
const mathOf = (element) => element?.closest(".math-group") || element?.closest("mjx-container, math");

function* units(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
    acceptNode(node) {
      const element = node.nodeType === 1 ? node : node.parentElement;
      if (element.closest(UI)) return NodeFilter.FILTER_REJECT;
      if (node.nodeType === 3) return element.closest(MATH) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      if (node.parentElement?.closest(MATH)) return NodeFilter.FILTER_REJECT;
      return isMath(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    },
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) yield node;
}

const size = (unit) => (unit.nodeType === 3 ? unit.data.length : 1);
const texOf = (math) => math.dataset?.tex || math.getAttribute?.("alttext") || "";

// Bloc repère d'une extrémité : l'élément à identifiant le plus proche, jamais une formule ni l'intérieur d'une
// formule (rien n'y est compté : une sélection finie dans une équation n'aurait rien eu à surligner)
function container(node, root) {
  let element = (node.nodeType === 3 ? node.parentElement : node).closest("[id]");
  const math = mathOf(element);
  if (math) element = math.parentElement?.closest("[id]");
  return element && root.contains(element) ? element : root;
}

// Décalage (en texte logique, dans `box`) d'une extrémité de sélection DOM
function offsetOf(box, node, offset, isEnd) {
  const math = mathOf(node.nodeType === 3 ? node.parentElement : node);
  let target = null;
  let inner = 0;
  if (math) { target = math; inner = isEnd ? 1 : 0; }
  else if (node.nodeType === 3) { target = node; inner = offset; }
  else {
    // extrémité entre deux enfants d'un élément : premier morceau de texte qui suit
    const child = node.childNodes[offset];
    for (const unit of units(box)) {
      if (!child ? !node.contains(unit) && (node.compareDocumentPosition(unit) & Node.DOCUMENT_POSITION_FOLLOWING)
        : child === unit || child.contains(unit) || (child.compareDocumentPosition(unit) & Node.DOCUMENT_POSITION_FOLLOWING)) {
        target = unit; inner = 0; break;
      }
    }
  }
  let total = 0;
  for (const unit of units(box)) {
    if (unit === target) return total + inner;
    total += size(unit);
  }
  return total;
}

function locate(box, offset, preferNext) {
  let total = 0;
  let last = null;
  for (const unit of units(box)) {
    const length = size(unit);
    if (offset < total + length || (!preferNext && offset === total + length)) return { unit, offset: offset - total };
    total += length;
    last = unit;
  }
  return last ? { unit: last, offset: size(last) } : null;
}

// ---------- Passage cité (par Claude) → plage du texte ----------
// Texte de l'article mis à plat, chaque caractère relié à sa place (texte ou formule). Deux versions :
// formules écrites $tex$ (comme dans le texte envoyé à Claude), ou réduites à un seul signe (repli,
// si Claude a recopié une formule autrement).
const sameChars = (text) => text.replace(/[\u2018\u2019\u2032]/g, "'").replace(/[\u201C\u201D]/g, '"')
  .replace(/[\u2013\u2014\u2212]/g, "-").replace(/\u00a0/g, " ");
const FORMULA = "\u2217";

// Texte mis à plat : `text`, et pour chaque caractère son morceau (`units`) et sa position dans le morceau
// (-1 : formule entière). Tableaux typés (un article fait un million de caractères) ; gardé en cache
// tant que le texte n'est pas modifié (surlignage ajouté ou retiré).
let textVersion = 0;
const flatCache = new WeakMap();

function flatten(root, withTex) {
  const cached = flatCache.get(root)?.[withTex ? "tex" : "plain"];
  if (cached && cached.version === textVersion) return cached;
  const chars = [];
  const list = [];
  let unitIndex = new Int32Array(1 << 16);
  let offsets = new Int32Array(1 << 16);
  let space = true;
  const push = (ch, unit, offset) => {
    if (/\s/.test(ch)) { if (space) return; ch = " "; space = true; } else space = false;
    if (chars.length === unitIndex.length) {
      const grow = (array) => { const next = new Int32Array(array.length * 2); next.set(array); return next; };
      unitIndex = grow(unitIndex);
      offsets = grow(offsets);
    }
    unitIndex[chars.length] = list.length - 1;
    offsets[chars.length] = offset;
    chars.push(ch);
  };
  for (const unit of units(root)) {
    list.push(unit);
    if (unit.nodeType === 3) {
      const data = sameChars(unit.data);
      for (let i = 0; i < data.length; i++) push(data[i], unit, i);
    } else {
      // par unités UTF-16, comme le texte (sinon un symbole hors BMP décalerait tout ce qui suit)
      const body = withTex ? `$${texOf(unit).replace(/\s+/g, " ").trim()}$` : FORMULA;
      for (let i = 0; i < body.length; i++) push(body[i], unit, -1);
    }
  }
  const flat = { text: chars.join(""), units: list, unitIndex, offsets, version: textVersion };
  flatCache.set(root, { ...flatCache.get(root), [withTex ? "tex" : "plain"]: flat });
  return flat;
}

function simplifyQuote(quote, withTex) {
  let text = sameChars(String(quote)).replace(/\$\$/g, "$");
  text = withTex ? text.replace(/\$([^$]*)\$/g, (_, tex) => `$${tex.replace(/\s+/g, " ").trim()}$`)
    : text.replace(/\$[^$]*\$/g, FORMULA);
  return text.replace(/\s+/g, " ").trim();
}

function rangeAt(flat, index, length) {
  const range = document.createRange();
  const startUnit = flat.units[flat.unitIndex[index]];
  const endUnit = flat.units[flat.unitIndex[index + length - 1]];
  const startOffset = flat.offsets[index];
  const endOffset = flat.offsets[index + length - 1];
  if (startOffset < 0) range.setStartBefore(startUnit); else range.setStart(startUnit, startOffset);
  if (endOffset < 0) range.setEndAfter(endUnit); else range.setEnd(endUnit, endOffset + 1);
  return range;
}

// Recherche dans l'article (⌘F) : toutes les occurrences, sans tenir compte des majuscules ni des accents.
// Les formules sont cherchées dans leur source TeX quand la recherche contient \ ou $ (« \mathcal{O} »),
// sinon elles ne comptent que pour une espace.
const foldCache = new WeakMap();
const foldChar = (ch) => {
  const base = ch.normalize("NFD")[0].toLowerCase();
  return base.length === 1 ? base : ch; // une lettre pour une lettre : positions inchangées
};
const fold = (text) => Array.from({ length: text.length }, (_, i) => foldChar(text[i])).join("");

export function findAll(root, query, limit = 2000) {
  const withTex = /[\\$]/.test(query);
  const flat = flatten(root, withTex);
  let folded = foldCache.get(flat);
  if (!folded) { folded = fold(flat.text); foldCache.set(flat, folded); }
  const needle = fold(sameChars(query).replace(/\$/g, "").replace(/\s+/g, " ").trim());
  const ranges = [];
  if (!needle) return ranges;
  for (let at = folded.indexOf(needle); at >= 0 && ranges.length < limit; at = folded.indexOf(needle, at + needle.length)) {
    const range = rangeAt(flat, at, needle.length);
    // formules touchées : MathJax les dessine sans texte, le surlignage passe par une classe
    const first = flat.unitIndex[at], last = flat.unitIndex[at + needle.length - 1];
    range.formulas = flat.units.slice(first, last + 1).filter((unit) => unit.nodeType === 1);
    ranges.push(range);
  }
  return ranges;
}

// Plage DOM d'un extrait recopié de l'article, ou null s'il est introuvable. Cherché d'abord dans
// `scopes` (l'énoncé et la preuve visés), puis dans tout l'article. Sans les formules (repli quand
// Claude a recopié une formule autrement), l'extrait doit être unique et garder assez de texte.
export function rangeForQuote(root, quote, scopes = []) {
  for (const withTex of [true, false]) {
    const needle = simplifyQuote(quote, withTex);
    if (needle.replaceAll(FORMULA, "").replace(/\s/g, "").length < (withTex ? 3 : 12)) continue;
    for (const scope of [...scopes.filter(Boolean), root]) {
      const flat = flatten(scope, withTex);
      const index = flat.text.indexOf(needle);
      if (index < 0) continue;
      if (!withTex && flat.text.indexOf(needle, index + 1) >= 0) continue; // ambigu : on ne devine pas
      return rangeAt(flat, index, needle.length);
    }
  }
  return null;
}

// ---------- Surlignage dans le DOM ----------

function paint(root, annotation) {
  textVersion++;
  const { start, end } = annotation.target || {};
  let startBox = start && (start.id ? root.querySelector(`[id="${CSS.escape(start.id)}"]`) : root);
  let endBox = end && (end.id ? root.querySelector(`[id="${CSS.escape(end.id)}"]`) : root);
  if (!startBox || !endBox) return false;
  let startOffset = start.offset;
  let endOffset = end.offset;
  // repère enregistré sur une formule (versions précédentes, sélection finie dans une équation) : le bloc qui la
  // contient, et la formule entière
  const outside = (box, isEnd) => {
    const math = mathOf(box);
    if (!math) return null;
    const outer = container(math, root);
    return [outer, offsetOf(outer, math, 0, isEnd)];
  };
  [startBox, startOffset] = outside(startBox, false) || [startBox, startOffset];
  [endBox, endOffset] = outside(endBox, true) || [endBox, endOffset];
  const from = locate(startBox, startOffset, true);
  const to = locate(endBox, endOffset, false);
  if (!from || !to) return false;
  const segments = [];
  let inside = false;
  // parcours limité au plus petit bloc qui contient le passage, pas à tout l'article
  let scope = startBox;
  while (scope !== root && !scope.contains(endBox)) scope = scope.parentElement;
  for (const unit of units(scope)) {
    if (unit === from.unit) inside = true;
    if (inside) {
      const a = unit === from.unit ? from.offset : 0;
      const b = unit === to.unit ? to.offset : size(unit);
      if (b > a) segments.push({ unit, a, b });
    }
    if (unit === to.unit) break;
  }
  // blancs au début ou à la fin du passage (l'espace avant le premier mot) : pas marqués
  const blank = ({ unit, a, b }) => unit.nodeType === 3 && !unit.data.slice(a, b).trim();
  while (segments.length && blank(segments[0])) segments.shift();
  while (segments.length && blank(segments.at(-1))) segments.pop();
  if (!segments.length) return false;
  const mark = () => h("mark", { class: `hl hl-${annotation.color || "yellow"}`, dataset: { annotation: annotation.id } });
  for (const { unit, a, b } of segments) {
    if (unit.nodeType === 3) {
      // blanc de mise en page (entre deux blocs, autour d'une équation hors texte) : pas de marque (elle s'afficherait
      // en barre verticale de la hauteur d'une ligne) ; une espace entre deux mots ou formules reste marquée
      const text = unit.data.slice(a, b);
      if (!text.trim() && (text.includes("\n") || /^(TABLE|TBODY|TR|THEAD|TD|DIV)$/.test(unit.parentElement.tagName))) continue;
      let piece = unit;
      if (b < unit.data.length) unit.splitText(b);
      if (a > 0) piece = unit.splitText(a);
      const wrapper = mark();
      piece.replaceWith(wrapper);
      wrapper.append(piece);
    } else {
      const wrapper = mark();
      // formule hors texte (un bloc) : la marque l'épouse (une marque en ligne ne colorerait pas son fond)
      if (unit.matches("mjx-container[display='true'], math[display='block']") || unit.querySelector?.("mjx-container[display='true']")) wrapper.classList.add("hl-display");
      unit.replaceWith(wrapper);
      wrapper.append(unit);
    }
  }
  return true;
}

function unpaint(root, id) {
  textVersion++;
  root.querySelectorAll(`mark.hl[data-annotation="${id}"]`).forEach((mark) => {
    const parent = mark.parentNode;
    mark.replaceWith(...mark.childNodes);
    parent.normalize();
  });
}

// Texte caché de l'article (blanc, minuscule : marqué par l'analyse) et débris de macros : jamais dans une
// citation, qui peut partir à l'assistant (« Demander à l'IA sur ce passage ») — comme pour l'article entier
const HIDDEN = ".arx-hidden-text, .arx-macro-debris";

function quoteOf(range, limit = 1500) {
  const parts = [];
  const box = range.commonAncestorContainer.nodeType === 3 ? range.commonAncestorContainer.parentElement : range.commonAncestorContainer;
  for (const unit of units(box)) {
    if (!range.intersectsNode(unit)) continue;
    if ((unit.nodeType === 3 ? unit.parentElement : unit).closest(HIDDEN)) continue;
    if (unit.nodeType === 3) {
      const a = unit === range.startContainer ? range.startOffset : 0;
      const b = unit === range.endContainer ? range.endOffset : unit.data.length;
      parts.push(unit.data.slice(a, b));
    } else {
      parts.push(`$${texOf(unit)}$`);
    }
  }
  return parts.join("").replace(/\s+/g, " ").trim().slice(0, limit);
}

// Sélection en texte, formules en LaTeX (« $…$ » en ligne, « \[ … \] » hors texte, sur sa ligne), paragraphes
// séparés par une ligne vide : pour la copier (⌘C, bouton « TeX ») au lieu des symboles de MathJax
function rangeTex(range) {
  const start = mathOf(range.startContainer.nodeType === 3 ? range.startContainer.parentElement : range.startContainer);
  const end = mathOf(range.endContainer.nodeType === 3 ? range.endContainer.parentElement : range.endContainer);
  if (start && start === end) return texOf(start).trim(); // sélection dans une seule formule
  const box = range.commonAncestorContainer.nodeType === 3 ? range.commonAncestorContainer.parentElement : range.commonAncestorContainer;
  let out = "";
  let block = null;
  for (const unit of units(box)) {
    if (!range.intersectsNode(unit)) continue;
    const element = unit.nodeType === 3 ? unit.parentElement : unit;
    const paragraph = element.closest(".ltx_para, p, li, .ltx_equation, .ltx_equationgroup, tr, h1, h2, h3, h4, h5, h6");
    if (block && paragraph !== block && out.trim()) out = `${out.trimEnd()}\n\n`;
    block = paragraph;
    if (unit.nodeType === 3) {
      const a = unit === range.startContainer ? range.startOffset : 0;
      const b = unit === range.endContainer ? range.endOffset : unit.data.length;
      out += unit.data.slice(a, b).replace(/\s+/g, " ");
    } else {
      const tex = texOf(unit).trim();
      const display = unit.matches("mjx-container[display='true'], math[display='block']") || unit.querySelector?.("mjx-container[display='true']");
      out += display ? `\n\\[ ${tex} \\]\n` : `$${tex}$`;
    }
  }
  return out.replace(/[ \t]+\n/g, "\n").replace(/\n[ \t]+/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch { // presse-papiers refusé (hors geste de l'utilisateur…) : ancienne méthode
    const area = h("textarea", { style: "position: fixed; opacity: 0" }, text);
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
}

const hasMath = (range) => Boolean(range.cloneContents().querySelector(MATH)
  || mathOf(range.startContainer.nodeType === 3 ? range.startContainer.parentElement : range.startContainer));

// Annotation proposée par l'IA (et acceptée) : signalée comme telle, avec le nom de l'assistant
function authorBadge(annotation) {
  return annotation.author
    ? h("span", { class: "badge ai-badge", title: t("ai.byAI", { name: authorName(annotation.author) }) },
      authorName(annotation.author))
    : null;
}

// ---------- Éditeur de note (Markdown + LaTeX) ----------

export function renderNote(element, text) {
  element.innerHTML = markdownToHtml(text);
  typesetTex([element]).catch(() => {});
}

export function noteEditor({ value = "", placeholder = "", onSave, autofocus = false }) {
  const area = h("textarea", { class: "note-input", rows: 3, placeholder, "aria-label": placeholder || t("notes.write") });
  area.value = value;
  const preview = h("div", { class: "note-preview markdown", hidden: true });
  const status = h("span", { class: "note-status muted small" });
  const write = h("button", { class: "note-tab", type: "button", "aria-pressed": "true" }, t("notes.write"));
  const show = h("button", { class: "note-tab", type: "button", "aria-pressed": "false" }, t("notes.preview"));
  const grow = () => { area.style.height = "auto"; area.style.height = `${Math.min(420, area.scrollHeight + 2)}px`; };
  let saved = value;
  const save = async () => {
    if (area.value === saved) return;
    status.textContent = t("notes.saving");
    try {
      await onSave(area.value);
      saved = area.value;
      status.textContent = t("notes.saved");
    } catch (error) {
      status.textContent = t("error.generic", { message: error.message });
    }
  };
  const later = debounce(save, 700);
  area.addEventListener("input", () => { grow(); status.textContent = ""; later(); });
  area.addEventListener("blur", save);
  write.addEventListener("click", () => {
    write.setAttribute("aria-pressed", "true"); show.setAttribute("aria-pressed", "false");
    preview.hidden = true; area.hidden = false; area.focus();
  });
  show.addEventListener("click", () => {
    show.setAttribute("aria-pressed", "true"); write.setAttribute("aria-pressed", "false");
    renderNote(preview, area.value || `*${t("notes.empty")}*`);
    area.hidden = true; preview.hidden = false;
  });
  const element = h("div", { class: "note-editor" },
    h("div", { class: "note-tabs" }, write, show, h("span", { class: "spacer" }), status), area, preview,
    h("div", { class: "hint note-help mathjax_ignore" }, t("notes.help")));  // pas de rendu MathJax pour l'aide
  requestAnimationFrame(() => { grow(); if (autofocus) area.focus(); });
  element.flush = save;
  return element;
}

// ---------- Annotateur d'un article ----------

export class Annotator {
  constructor({ paperId, content, nodes, navigate, onChange, aiActions, onAskAI }) {
    this.paperId = paperId;
    this.content = content;
    this.nodes = nodes;
    this.byId = new Map(nodes.map((n) => [n.id, n]));
    this.navigate = navigate;
    this.onChange = onChange;
    this.aiActions = aiActions;
    this.onAskAI = onAskAI;
    this.items = [];
    this.popover = null;
    this.toolbar = null;
  }

  async load() {
    try { this.items = await api.annotations(this.paperId); } catch { this.items = []; }
  }

  // Après la mise en page (formules et figures) : surlignages, boutons de note, états
  apply() {
    // passages du HTML ; ceux du PDF ({pdf: …}) sont dessinés dans le PDF (pdfnotes.js)
    this.items.filter((a) => a.kind === "highlight" && !a.target?.pdf).forEach((a) => { a.orphan = !paint(this.content, a); });
    this.addBlockButtons();
    this.refreshBadges();
    this.bind();
  }

  block(id) { return this.items.find((a) => a.kind === "block" && a.target?.id === id); }
  stateOf(id) { return this.block(id)?.state || null; }
  get general() { return this.items.find((a) => a.kind === "general"); }

  // ---- énoncés et preuves ----
  addBlockButtons() {
    this.content.querySelectorAll(".ltx_theorem[id], .ltx_proof[id]").forEach((element) => {
      if (element.querySelector(":scope > .block-note")) return;
      const button = h("button", { class: "block-note", type: "button", title: t("notes.blockNote"), "aria-label": t("notes.blockNote"),
        html: icons.note });
      button.addEventListener("click", (event) => { event.stopPropagation(); this.openBlock(element.id, button); });
      element.append(button);
    });
  }

  refreshBadges() {
    this.content.querySelectorAll(".state-pill").forEach((pill) => pill.remove());
    this.content.querySelectorAll(".ltx_theorem[id], .ltx_proof[id]").forEach((element) => {
      const annotation = this.block(element.id);
      element.querySelector(":scope > .block-note")?.classList.toggle("has-note", Boolean(annotation?.note || annotation?.state));
      if (!annotation?.state || !element.matches(".ltx_theorem")) return;
      // dans le titre (« Theorem 1.1. ») pour que l'énoncé reste sur la même ligne
      const title = element.querySelector(".ltx_title_theorem, .ltx_title");
      title?.append(h("span", { class: `state-pill state-${annotation.state}` }, t(`notes.state.${annotation.state}`)));
    });
  }

  async saveBlock(id, values) {
    const annotation = await api.addAnnotation(this.paperId, { kind: "block", target: { id }, ...values });
    this.items = this.items.filter((a) => a.id !== annotation.id).concat(annotation);
    this.refreshBadges();
    this.onChange();
    return annotation;
  }

  // Texte ajouté à la fin d'une note (énoncé ou preuve `id`, sinon fiche de lecture)
  async appendNote(id, text) {
    const join = (current) => (current?.trim() ? `${current.trimEnd()}\n\n---\n\n${text}` : text);
    if (id) return this.saveBlock(id, { note: join(this.block(id)?.note) });
    const annotation = await api.addAnnotation(this.paperId, { kind: "general", note: join(this.general?.note) });
    this.items = this.items.filter((a) => a.kind !== "general").concat(annotation);
    this.onChange();
    return annotation;
  }

  async setState(id, state) {
    const current = this.block(id);
    if (current && current.state === state) {
      const updated = await api.updateAnnotation(current.id, { clear_state: true });
      this.items = this.items.map((a) => (a.id === updated.id ? updated : a));
      this.refreshBadges();
      this.onChange();
      return updated;
    }
    return this.saveBlock(id, { state });
  }

  openBlock(id, anchor) {
    const node = this.byId.get(id);
    const element = this.content.querySelector(`[id="${CSS.escape(id)}"]`);
    const isTheorem = element?.matches(".ltx_theorem");
    const proofOf = element?.dataset.proofOf && this.byId.get(element.dataset.proofOf);
    const title = isTheorem ? node?.label || t("notes.statement") : `${t("reader.proof")}${proofOf ? ` — ${proofOf.label}` : ""}`;
    const body = [h("div", { class: "popover-title" }, title)];
    if (isTheorem) body.push(this.stateControl(id));
    body.push(noteEditor({ value: this.block(id)?.note || "", placeholder: t("notes.placeholderBlock"), autofocus: !isTheorem,
      onSave: (note) => this.saveBlock(id, { note }) }));
    const actions = this.aiActions?.(isTheorem ? id : proofOf?.id);
    if (actions) {
      actions.addEventListener("click", (event) => { if (event.target.closest("button")) this.closePopover(); });
      body.push(actions);
    }
    this.openPopover(anchor, body);
  }

  stateControl(id) {
    const group = h("div", { class: "state-control", role: "group", "aria-label": t("notes.stateLabel") });
    const draw = () => group.replaceChildren(...STATES.map((state) => {
      const button = h("button", { type: "button", class: `state-option state-${state}`, "aria-pressed": String(this.stateOf(id) === state) },
        t(`notes.state.${state}`));
      button.addEventListener("click", async () => {
        try { await this.setState(id, state); draw(); } catch (error) { toast(error.message, { error: true }); }
      });
      return button;
    }));
    draw();
    return group;
  }

  // ---- passages surlignés ----
  bind() {
    if (this.bound) return;
    this.bound = true;
    const onUp = () => setTimeout(() => this.showToolbar(), 0);
    this.content.addEventListener("mouseup", onUp);
    this.content.addEventListener("keyup", (event) => { if (event.shiftKey) onUp(); });
    // iPhone, iPad : une sélection au doigt ne déclenche pas « mouseup »
    if (matchMedia("(pointer: coarse)").matches) {
      this.onSelection = debounce(() => {
        const selection = getSelection();
        if (selection.rangeCount && !selection.isCollapsed && this.content.contains(selection.getRangeAt(0).commonAncestorContainer)) this.showToolbar();
      }, 350);
      document.addEventListener("selectionchange", this.onSelection);
    }
    this.content.addEventListener("contextmenu", (event) => this.contextMenu(event));
    this.onScrollClose = () => this.hideContextMenu();
    addEventListener("scroll", this.onScrollClose, true);
    // ⌘C d'un passage avec des formules : formules en LaTeX dans le texte copié (au lieu des symboles de MathJax)
    this.content.addEventListener("copy", (event) => {
      const selection = getSelection();
      if (!selection.rangeCount || selection.isCollapsed || !hasMath(selection.getRangeAt(0))) return;
      event.clipboardData.setData("text/plain", rangeTex(selection.getRangeAt(0)));
      event.preventDefault();
    });
    // ⌥-clic sur une formule : son TeX copié
    this.content.addEventListener("click", async (event) => {
      if (!event.altKey || !getSelection().isCollapsed) return;
      const math = mathOf(event.target.closest?.(MATH));
      const tex = math && texOf(math).trim();
      if (!tex) return;
      event.preventDefault();
      event.stopPropagation();
      await copyText(tex);
      toast(t("notes.texCopied"));
    }, true);
    this.content.addEventListener("click", (event) => {
      const mark = event.target.closest("mark.hl");
      if (!mark || !getSelection().isCollapsed) return;
      event.preventDefault();
      this.openHighlight(Number(mark.dataset.annotation), mark);
    });
    this.onDocumentDown = (event) => {
      if (this.toolbar && !this.toolbar.contains(event.target)) this.hideToolbar();
      if (this.contextMenuElement && !this.contextMenuElement.contains(event.target)) this.hideContextMenu();
      if (this.popover && !this.popover.contains(event.target) && !event.target.closest(".block-note, mark.hl")) this.closePopover();
    };
    this.onKey = (event) => { if (event.key === "Escape") { this.hideToolbar(); this.closePopover(); } };
    document.addEventListener("mousedown", this.onDocumentDown);
    document.addEventListener("keydown", this.onKey);
  }

  showToolbar() {
    const selection = getSelection();
    if (!selection.rangeCount || selection.isCollapsed) return;
    const range = selection.getRangeAt(0);
    if (!this.content.contains(range.commonAncestorContainer) || !range.toString().trim() && !range.cloneContents().querySelector("mjx-container, math")) return;
    this.hideToolbar();
    const create = (color, withNote) => this.createFrom(range, color, withNote);
    const rect = range.getBoundingClientRect();
    this.toolbar = h("div", { class: "hl-toolbar", role: "toolbar", "aria-label": t("notes.highlight") },
      ...COLORS.map((color) => h("button", { type: "button", class: `hl-dot hl-${color}`, title: t(`notes.color.${color}`), "aria-label": t(`notes.color.${color}`),
        onclick: () => create(color, false) })),
      h("span", { class: "hl-sep" }),
      h("button", { type: "button", class: "hl-note-button", title: t("notes.addNote"), "aria-label": t("notes.addNote"), html: icons.note,
        onclick: () => create("yellow", true) }),
      this.onAskAI && h("button", { type: "button", class: "hl-note-button", title: t("ai.askPassage"), "aria-label": t("ai.askPassage"), html: icons.sparkle,
        onclick: () => { const quote = quoteOf(range, 12000); selection.removeAllRanges(); this.hideToolbar(); this.onAskAI(quote); } }),
      // passage avec des formules : copié en LaTeX
      hasMath(range) && h("button", { type: "button", class: "hl-note-button hl-tex-button", title: t("notes.copyTex"), "aria-label": t("notes.copyTex"),
        onclick: async () => { await copyText(rangeTex(range)); this.hideToolbar(); toast(t("notes.texCopied")); } }, "TeX"));
    document.body.append(this.toolbar);
    const width = this.toolbar.offsetWidth;
    this.toolbar.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, rect.left + rect.width / 2 - width / 2))}px`;
    this.toolbar.style.top = `${Math.max(64, rect.top - this.toolbar.offsetHeight - 8)}px`;
  }

  hideToolbar() { this.toolbar?.remove(); this.toolbar = null; }

  // Surlignage du passage `range` (et sa note, ouverte aussitôt, si `withNote`)
  async createFrom(range, color, withNote) {
    const saved = range.cloneRange();
    getSelection().removeAllRanges();
    this.hideToolbar();
    this.hideContextMenu();
    try {
      const annotation = await this.highlightRange(saved, { color });
      if (withNote) {
        const mark = this.content.querySelector(`mark.hl[data-annotation="${annotation.id}"]`);
        if (mark) this.openHighlight(annotation.id, mark, true);
      }
    } catch (error) {
      toast(t("error.generic", { message: error.message }), { error: true });
    }
  }

  // Clic droit sur une sélection ou une formule de l'article : surligner, noter, copier en LaTeX, demander à
  // l'assistant. Ailleurs (lien, image, texte non sélectionné) : le menu du système
  contextMenu(event) {
    const selection = getSelection();
    let range = selection.rangeCount && !selection.isCollapsed ? selection.getRangeAt(0) : null;
    if (range && !this.content.contains(range.commonAncestorContainer)) range = null;
    const math = mathOf(event.target.closest?.(MATH));
    if (range && math && !range.intersectsNode(math)) range = null; // clic sur une formule hors de la sélection
    const single = !range && math; // une formule seule (pas de sélection) : son TeX nu, comme le ⌥-clic
    if (single) { range = document.createRange(); range.selectNode(math); }
    if (!range || event.target.closest("a[href], img, .figure-card")) return;
    event.preventDefault();
    this.hideToolbar();
    this.hideContextMenu();
    const item = (label, action) => h("button", { type: "button", class: "context-menu-item", role: "menuitem",
      onclick: () => { this.hideContextMenu(); action(); } }, label);
    const tex = hasMath(range);
    const menu = this.contextMenuElement = h("div", { class: "context-menu", role: "menu" },
      item(t("notes.highlight"), () => this.createFrom(range, "yellow", false)),
      item(t("notes.addNote"), () => this.createFrom(range, "yellow", true)),
      tex && item(t("notes.copyTexShort"), async () => { await copyText(single ? texOf(math).trim() : rangeTex(range)); toast(t("notes.texCopied")); }),
      item(t("notes.copyText"), async () => { await copyText(range.toString()); toast(t("notes.textCopied")); }),
      this.onAskAI && item(t("ai.askPassage"), () => { const quote = quoteOf(range, 12000); getSelection().removeAllRanges(); this.onAskAI(quote); }));
    document.body.append(menu);
    // à l'endroit du clic, dans la fenêtre
    menu.style.left = `${Math.max(8, Math.min(event.clientX, innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(event.clientY, innerHeight - menu.offsetHeight - 8))}px`;
    menu.querySelector("button")?.focus({ preventScroll: true });
    menu.addEventListener("keydown", (key) => {
      if (key.key === "Escape") { key.preventDefault(); this.hideContextMenu(); }
      if (key.key === "ArrowDown" || key.key === "ArrowUp") {
        key.preventDefault();
        const items = [...menu.querySelectorAll("button")];
        items[(items.indexOf(document.activeElement) + (key.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
      }
    });
  }

  hideContextMenu() { this.contextMenuElement?.remove(); this.contextMenuElement = null; }

  // Passage surligné à partir d'une plage du texte (sélection, ou extrait proposé par Claude)
  async highlightRange(range, { color = "yellow", note = null, author = null } = {}) {
    const startBox = container(range.startContainer, this.content);
    const endBox = container(range.endContainer, this.content);
    const target = {
      start: { id: startBox === this.content ? "" : startBox.id, offset: offsetOf(startBox, range.startContainer, range.startOffset, false) },
      end: { id: endBox === this.content ? "" : endBox.id, offset: offsetOf(endBox, range.endContainer, range.endOffset, true) },
    };
    const annotation = await api.addAnnotation(this.paperId, { kind: "highlight", target, color, quote: quoteOf(range), note, author });
    this.items.push(annotation);
    paint(this.content, annotation);
    this.onChange();
    return annotation;
  }

  async deleteBlock(annotationId) {
    await api.deleteAnnotation(annotationId);
    this.items = this.items.filter((a) => a.id !== annotationId);
    this.refreshBadges();
    this.onChange();
  }

  // Suppression d'un passage surligné (annulation des annotations de Claude)
  async removeHighlight(id) {
    await api.deleteAnnotation(id);
    unpaint(this.content, id);
    const removed = this.items.find((a) => a.id === id);
    this.items = this.items.filter((a) => a.id !== id);
    if (removed) this.pdf?.removed(removed);
    this.onChange();
  }

  openHighlight(id, anchor, focus = false) {
    const annotation = this.items.find((a) => a.id === id);
    if (!annotation) return;
    const colors = h("div", { class: "popover-colors" }, COLORS.map((color) => {
      const button = h("button", { type: "button", class: `hl-dot hl-${color}`, "aria-pressed": String(annotation.color === color),
        title: t(`notes.color.${color}`), "aria-label": t(`notes.color.${color}`) });
      button.addEventListener("click", async () => {
        let updated;
        try { updated = await api.updateAnnotation(id, { color }); } catch (error) { toastError(error); return; }
        Object.assign(annotation, updated);
        this.content.querySelectorAll(`mark.hl[data-annotation="${id}"]`).forEach((m) => { m.className = `hl hl-${color}`; });
        this.pdf?.recolor(id);
        colors.querySelectorAll(".hl-dot").forEach((b) => b.setAttribute("aria-pressed", String(b.classList.contains(`hl-${color}`))));
        this.onChange();
      });
      return button;
    }));
    const remove = h("button", { class: "button small-button danger", type: "button" }, t("notes.deleteHighlight"));
    remove.addEventListener("click", async () => {
      try { await api.deleteAnnotation(id); } catch (error) { toastError(error); return; }
      unpaint(this.content, id);
      this.items = this.items.filter((a) => a.id !== id);
      this.pdf?.removed(annotation);
      this.closePopover();
      this.onChange();
    });
    this.openPopover(anchor, [
      h("div", { class: "popover-head" }, colors, authorBadge(annotation), h("span", { class: "spacer" }), remove),
      noteEditor({ value: annotation.note || "", placeholder: t("notes.placeholderHighlight"), autofocus: focus,
        onSave: async (note) => { Object.assign(annotation, await api.updateAnnotation(id, { note })); this.onChange(); } }),
    ]);
  }

  openPopover(anchor, children) {
    this.closePopover();
    this.popover = h("div", { class: "note-popover", role: "dialog" }, children);
    document.body.append(this.popover);
    const rect = anchor.getBoundingClientRect();
    const width = this.popover.offsetWidth;
    const height = this.popover.offsetHeight;
    this.popover.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, rect.left + rect.width / 2 - width / 2))}px`;
    const below = rect.bottom + 8 + height < innerHeight;
    this.popover.style.top = `${below ? rect.bottom + 8 : Math.max(60, rect.top - height - 8)}px`;
  }

  closePopover() {
    this.popover?.querySelectorAll(".note-editor").forEach((editor) => editor.flush?.());
    this.popover?.remove();
    this.popover = null;
  }

  // ---- onglet « Notes » du panneau ----
  renderPanel(body) {
    const statements = this.nodes.filter((n) => !["remark", "example", "notation"].includes(n.kind));
    const counts = Object.fromEntries(STATES.map((s) => [s, statements.filter((n) => this.stateOf(n.id) === s).length]));
    const done = counts.understood + counts.verified;
    const bar = h("div", { class: "progress-bar", role: "img", "aria-label": t("notes.progress", { done, total: statements.length }) },
      ["verified", "understood", "review"].map((s) => h("span", { class: `state-${s}`, style: `width: ${statements.length ? (100 * counts[s]) / statements.length : 0}%` })));
    if (statements.length) body.append(h("div", { class: "panel-section" },  // sans énoncés (PDF importé) : sans objet
      h("h3", {}, t("notes.progressTitle")),
      h("p", { class: "small notes-progress" }, t("notes.progress", { done, total: statements.length })),
      bar,
      h("div", { class: "legend" }, STATES.map((s) => h("span", {}, h("span", { class: `dot state-${s}` }), `${t(`notes.state.${s}`)} : ${counts[s]}`)))));

    body.append(h("div", { class: "panel-section" }, h("h3", {}, t("notes.general")),
      noteEditor({ value: this.general?.note || "", placeholder: t("notes.placeholderGeneral"),
        onSave: async (note) => {
          const annotation = await api.addAnnotation(this.paperId, { kind: "general", note });
          this.items = this.items.filter((a) => a.kind !== "general").concat(annotation);
        } })));

    // annotations, dans l'ordre de l'article
    const located = this.items.filter((a) => a.kind !== "general" && (a.kind === "highlight" || a.note || a.state)).map((a) => {
      const element = a.kind === "highlight"
        ? this.content.querySelector(`mark.hl[data-annotation="${a.id}"]`)
        : this.content.querySelector(`[id="${CSS.escape(a.target?.id || "")}"]`);
      return { a, element };
    });
    // ordre de l'article ; passages du PDF ensuite, par page et position
    const pdfSpot = (a) => (a.target?.pdf ? a.target.pdf.page * 1e7 + a.target.pdf.start : null);
    located.sort((x, y) => {
      const [px, py] = [pdfSpot(x.a), pdfSpot(y.a)];
      if (px !== null || py !== null) return px === null ? -1 : py === null ? 1 : px - py;
      return !x.element ? 1 : !y.element ? -1 : x.element.compareDocumentPosition(y.element) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    });
    const list = h("div", { class: "notes-list" }, located.length ? located.map(({ a, element }) => this.noteCard(a, element))
      : h("p", { class: "hint" }, t("notes.none")));
    body.append(h("div", { class: "panel-section" }, h("h3", {}, t("notes.annotations", { n: located.length })), list,
      h("p", { class: "hint" }, t("notes.howTo"))));
    // export : fiche, énoncés et preuves, passages, en Markdown (dossier Téléchargements)
    if (located.length || (this.general?.note || "").trim()) {
      body.append(h("div", { class: "panel-section" }, h("a", { class: "button small-button", download: "",
        href: `/api/papers/${encodeURIComponent(this.paperId)}/notes.md?lang=${document.documentElement.lang || "fr"}` }, t("notes.export")),
        h("p", { class: "hint" }, t("notes.exportHint"))));
    }
    typesetTex([body]).catch(() => {});
  }

  noteCard(annotation, element) {
    const inPdf = Boolean(annotation.target?.pdf);
    const go = () => {
      if (inPdf) { this.revealPdf?.(annotation); return; }
      if (!element) return;
      if (annotation.kind === "highlight") this.navigate(element);
      else this.navigate(element.id);
    };
    let head;
    if (annotation.kind === "highlight") {
      head = h("blockquote", { class: `note-quote hl-border-${annotation.color || "yellow"}` }, annotation.quote || "");
    } else {
      const node = this.byId.get(annotation.target?.id);
      const proof = element?.matches?.(".ltx_proof");
      const proofOf = proof && element.dataset.proofOf && this.byId.get(element.dataset.proofOf);
      head = h("div", { class: "note-block-title" },
        h("span", { class: "label" }, node?.label || (proof ? `${t("reader.proof")}${proofOf ? ` — ${proofOf.label}` : ""}` : annotation.target?.id)),
        annotation.state && h("span", { class: `state-pill state-${annotation.state}` }, t(`notes.state.${annotation.state}`)));
    }
    const note = h("div", { class: "markdown note-body" });
    if (annotation.note) note.innerHTML = markdownToHtml(annotation.note);
    const found = element || inPdf;
    return h("div", { class: `note-card${found ? "" : " orphan"}`, role: "button", tabindex: 0, onclick: go,
      onkeydown: (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); go(); } }, title: found ? t("panel.showInText") : t("notes.orphan") },
      head, inPdf && h("span", { class: "badge" }, t("notes.pdfPage", { page: annotation.target.pdf.page })),
      annotation.note && note, authorBadge(annotation), !found && h("span", { class: "badge warn" }, t("notes.orphan")));
  }

  destroy() {
    this.hideToolbar();
    this.hideContextMenu();
    if (this.onScrollClose) removeEventListener("scroll", this.onScrollClose, true);
    this.closePopover();
    if (this.onDocumentDown) document.removeEventListener("mousedown", this.onDocumentDown);
    if (this.onKey) document.removeEventListener("keydown", this.onKey);
    if (this.onSelection) document.removeEventListener("selectionchange", this.onSelection);
  }
}
