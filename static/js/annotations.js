// Annotations dans le lecteur : passages surlignés (avec note), notes et état de lecture des énoncés
// et preuves, fiche de lecture ; onglet « Notes » du panneau.
//
// Repérage d'un passage : (élément avec id le plus proche, décalage) au début et à la fin, le décalage
// étant compté sur un « texte logique » où chaque formule vaut un seul caractère. Ainsi le repère est le
// même que les formules soient rendues par MathJax ou par le navigateur. Le texte surligné est gardé
// pour l'afficher dans la liste des notes.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { markdownToHtml } from "./markdown.js";
import { typesetTex } from "./math.js";
import { debounce, h, icons, toast, toastError } from "./util.js";

export const COLORS = ["yellow", "green", "blue", "pink"];
export const STATES = ["understood", "review", "verified"];
const UI = ".proof-toggle, .block-note, .state-pill, .proof-pointer";

// ---------- Texte logique ----------

const isMath = (node) => node.nodeType === 1 && node.matches("mjx-container, math");

function* units(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
    acceptNode(node) {
      const element = node.nodeType === 1 ? node : node.parentElement;
      if (element.closest(UI)) return NodeFilter.FILTER_REJECT;
      if (node.nodeType === 3) return element.closest("mjx-container, math") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      if (node.parentElement?.closest("mjx-container, math")) return NodeFilter.FILTER_REJECT;
      return isMath(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    },
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) yield node;
}

const size = (unit) => (unit.nodeType === 3 ? unit.data.length : 1);
const texOf = (math) => math.dataset?.tex || math.getAttribute?.("alttext") || "";

function container(node, root) {
  const element = (node.nodeType === 3 ? node.parentElement : node).closest("[id]");
  return element && root.contains(element) ? element : root;
}

// Décalage (en texte logique, dans `box`) d'une extrémité de sélection DOM
function offsetOf(box, node, offset, isEnd) {
  const math = (node.nodeType === 3 ? node.parentElement : node).closest("mjx-container, math");
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

function flatten(root, withTex) {
  const chars = [];
  const where = [];
  let space = true;
  const push = (ch, unit, offset) => {
    if (/\s/.test(ch)) { if (space) return; ch = " "; space = true; } else space = false;
    chars.push(ch);
    where.push([unit, offset]);
  };
  for (const unit of units(root)) {
    if (unit.nodeType === 3) {
      const data = sameChars(unit.data);
      for (let i = 0; i < data.length; i++) push(data[i], unit, i);
    } else {
      const body = withTex ? `$${texOf(unit).replace(/\s+/g, " ").trim()}$` : FORMULA;
      for (const ch of body) push(ch, unit, -1);
    }
  }
  return { text: chars.join(""), where };
}

function simplifyQuote(quote, withTex) {
  let text = sameChars(String(quote)).replace(/\$\$/g, "$");
  text = withTex ? text.replace(/\$([^$]*)\$/g, (_, tex) => `$${tex.replace(/\s+/g, " ").trim()}$`)
    : text.replace(/\$[^$]*\$/g, FORMULA);
  return text.replace(/\s+/g, " ").trim();
}

// Plage DOM d'un extrait recopié de l'article, ou null s'il est introuvable
export function rangeForQuote(root, quote) {
  for (const withTex of [true, false]) {
    const needle = simplifyQuote(quote, withTex);
    if (needle.length < 3) return null;
    const { text, where } = flatten(root, withTex);
    const index = text.indexOf(needle);
    if (index < 0) continue;
    const [startUnit, startOffset] = where[index];
    const [endUnit, endOffset] = where[index + needle.length - 1];
    const range = document.createRange();
    if (startOffset < 0) range.setStartBefore(startUnit); else range.setStart(startUnit, startOffset);
    if (endOffset < 0) range.setEndAfter(endUnit); else range.setEnd(endUnit, endOffset + 1);
    return range;
  }
  return null;
}

// ---------- Surlignage dans le DOM ----------

function paint(root, annotation) {
  const { start, end } = annotation.target || {};
  const startBox = start && (start.id ? root.querySelector(`[id="${CSS.escape(start.id)}"]`) : root);
  const endBox = end && (end.id ? root.querySelector(`[id="${CSS.escape(end.id)}"]`) : root);
  if (!startBox || !endBox) return false;
  const from = locate(startBox, start.offset, true);
  const to = locate(endBox, end.offset, false);
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
  if (!segments.length) return false;
  const mark = () => h("mark", { class: `hl hl-${annotation.color || "yellow"}`, dataset: { annotation: annotation.id } });
  for (const { unit, a, b } of segments) {
    if (unit.nodeType === 3) {
      if (!unit.data.slice(a, b).trim() && /^(TABLE|TBODY|TR|THEAD)$/.test(unit.parentElement.tagName)) continue;
      let piece = unit;
      if (b < unit.data.length) unit.splitText(b);
      if (a > 0) piece = unit.splitText(a);
      const wrapper = mark();
      piece.replaceWith(wrapper);
      wrapper.append(piece);
    } else {
      const wrapper = mark();
      unit.replaceWith(wrapper);
      wrapper.append(unit);
    }
  }
  return true;
}

function unpaint(root, id) {
  root.querySelectorAll(`mark.hl[data-annotation="${id}"]`).forEach((mark) => {
    const parent = mark.parentNode;
    mark.replaceWith(...mark.childNodes);
    parent.normalize();
  });
}

function quoteOf(range, limit = 1500) {
  const parts = [];
  const box = range.commonAncestorContainer.nodeType === 3 ? range.commonAncestorContainer.parentElement : range.commonAncestorContainer;
  for (const unit of units(box)) {
    if (!range.intersectsNode(unit)) continue;
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

// Annotation proposée par Claude (et acceptée) : signalée comme telle
function authorBadge(annotation) {
  return annotation.author === "claude"
    ? h("span", { class: "badge ai-badge", title: t("ai.byClaude") }, h("span", { class: "ai-icon", html: icons.sparkle }), "Claude")
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
    this.items.filter((a) => a.kind === "highlight").forEach((a) => { a.orphan = !paint(this.content, a); });
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
    this.content.addEventListener("click", (event) => {
      const mark = event.target.closest("mark.hl");
      if (!mark || !getSelection().isCollapsed) return;
      event.preventDefault();
      this.openHighlight(Number(mark.dataset.annotation), mark);
    });
    this.onDocumentDown = (event) => {
      if (this.toolbar && !this.toolbar.contains(event.target)) this.hideToolbar();
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
    const create = async (color, withNote) => {
      const saved = range.cloneRange();
      selection.removeAllRanges();
      this.hideToolbar();
      try {
        const annotation = await this.highlightRange(saved, { color });
        if (withNote) {
          const mark = this.content.querySelector(`mark.hl[data-annotation="${annotation.id}"]`);
          if (mark) this.openHighlight(annotation.id, mark, true);
        }
      } catch (error) {
        toast(t("error.generic", { message: error.message }), { error: true });
      }
    };
    const rect = range.getBoundingClientRect();
    this.toolbar = h("div", { class: "hl-toolbar", role: "toolbar", "aria-label": t("notes.highlight") },
      ...COLORS.map((color) => h("button", { type: "button", class: `hl-dot hl-${color}`, title: t(`notes.color.${color}`), "aria-label": t(`notes.color.${color}`),
        onclick: () => create(color, false) })),
      h("span", { class: "hl-sep" }),
      h("button", { type: "button", class: "hl-note-button", title: t("notes.addNote"), "aria-label": t("notes.addNote"), html: icons.note,
        onclick: () => create("yellow", true) }),
      this.onAskAI && h("button", { type: "button", class: "hl-note-button", title: t("ai.askPassage"), "aria-label": t("ai.askPassage"), html: icons.sparkle,
        onclick: () => { const quote = quoteOf(range, 12000); selection.removeAllRanges(); this.hideToolbar(); this.onAskAI(quote); } }));
    document.body.append(this.toolbar);
    const width = this.toolbar.offsetWidth;
    this.toolbar.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, rect.left + rect.width / 2 - width / 2))}px`;
    this.toolbar.style.top = `${Math.max(64, rect.top - this.toolbar.offsetHeight - 8)}px`;
  }

  hideToolbar() { this.toolbar?.remove(); this.toolbar = null; }

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
    this.items = this.items.filter((a) => a.id !== id);
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
    body.append(h("div", { class: "panel-section" },
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
    located.sort((x, y) => (!x.element ? 1 : !y.element ? -1 : x.element.compareDocumentPosition(y.element) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
    const list = h("div", { class: "notes-list" }, located.length ? located.map(({ a, element }) => this.noteCard(a, element))
      : h("p", { class: "hint" }, t("notes.none")));
    body.append(h("div", { class: "panel-section" }, h("h3", {}, t("notes.annotations", { n: located.length })), list,
      h("p", { class: "hint" }, t("notes.howTo"))));
    typesetTex([body]).catch(() => {});
  }

  noteCard(annotation, element) {
    const go = () => {
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
    return h("div", { class: `note-card${element ? "" : " orphan"}`, role: "button", tabindex: 0, onclick: go,
      onkeydown: (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); go(); } }, title: element ? t("panel.showInText") : t("notes.orphan") },
      head, annotation.note && note, authorBadge(annotation), !element && h("span", { class: "badge warn" }, t("notes.orphan")));
  }

  destroy() {
    this.hideToolbar();
    this.closePopover();
    if (this.onDocumentDown) document.removeEventListener("mousedown", this.onDocumentDown);
    if (this.onKey) document.removeEventListener("keydown", this.onKey);
    if (this.onSelection) document.removeEventListener("selectionchange", this.onSelection);
  }
}
