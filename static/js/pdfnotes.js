// Surlignages, notes et questions à l'assistant sur un passage du PDF (pdf.js, dans un cadre de même origine).
// Un passage est repéré par sa page et sa position dans le texte de la page (couche de texte de pdf.js) :
// {pdf: {page, start, endPage, end}}, indépendant du zoom. Les surlignages sont redessinés à chaque rendu d'une
// page (défilement, zoom) dans une couche placée sous le texte ; ils figurent dans l'onglet Notes avec les autres.

import { api } from "./api.js";
import { COLORS } from "./annotations.js";
import { t } from "./i18n.js";
import { h, icons, toast } from "./util.js";


const isPdf = (annotation) => annotation.kind === "highlight" && annotation.target?.pdf;

export class PdfNotes {
  // `annotator` : celui du lecteur (mêmes notes, même onglet) ; `view` : le PdfView ; `onAskAI(quote)`
  constructor({ annotator, view, onAskAI }) {
    this.annotator = annotator;
    this.view = view;
    this.onAskAI = onAskAI;
    this.toolbar = null;
    annotator.pdf = this; // l'annotateur nous prévient (couleur changée, passage supprimé) et nous délègue l'accès
    this.attach();
  }

  get items() { return this.annotator.items.filter(isPdf); }

  async attach() {
    const app = await this.view.ready();
    if (!app || this.destroyed) return;
    this.app = app;
    this.doc = this.view.frame.contentDocument;
    app.eventBus.on("textlayerrendered", ({ pageNumber }) => this.paintPage(pageNumber));
    this.doc.querySelectorAll(".page[data-page-number]").forEach((page) => this.paintPage(Number(page.dataset.pageNumber)));
    this.doc.addEventListener("mouseup", (event) => setTimeout(() => this.onUp(event), 0));
    // la barre et la note ouverte sont dans la page de l'application : fermées par un clic ou Échap dans le PDF
    // (l'annotateur n'écoute que sa page), et la barre au défilement du PDF ou à un clic ailleurs dans l'application
    this.doc.addEventListener("mousedown", () => { this.hideToolbar(); this.annotator.closePopover(); });
    this.doc.addEventListener("keydown", (event) => { if (event.key === "Escape") { this.hideToolbar(); this.annotator.closePopover(); } });
    this.doc.getElementById("viewerContainer")?.addEventListener("scroll", () => this.hideToolbar(), { passive: true });
    this.onParentDown = (event) => { if (this.toolbar && !this.toolbar.contains(event.target)) this.hideToolbar(); };
    document.addEventListener("mousedown", this.onParentDown);
  }

  // ---- texte d'une page : nœuds de la couche de texte, dans l'ordre ----
  textNodes(page) {
    const layer = this.doc.querySelector(`.page[data-page-number="${page}"] .textLayer`);
    if (!layer) return [];
    const walker = this.doc.createTreeWalker(layer, NodeFilter.SHOW_TEXT, {
      acceptNode: (node) => (node.parentElement.closest(".endOfContent") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    const nodes = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node);
    return nodes;
  }

  // position (page, décalage dans le texte de la page) d'une extrémité de sélection ; extrémité sur un élément
  // (autour des <br> de pdf.js) : `offset` y compte les enfants, la position est avant l'enfant n° offset
  spot(node, offset) {
    const element = node.nodeType === 3 ? node.parentElement : node;
    const pageDiv = element?.closest?.(".page[data-page-number]");
    if (!pageDiv || !element.closest(".textLayer")) return null;
    const page = Number(pageDiv.dataset.pageNumber);
    const texts = this.textNodes(page);
    let total = 0;
    if (node.nodeType === 3) {
      for (const text of texts) {
        if (text === node) return { page, offset: total + Math.min(offset, text.data.length) };
        total += text.data.length;
      }
      return { page, offset: total };
    }
    const next = node.childNodes[offset] || null;
    for (const text of texts) {  // dans l'ordre du document : on compte ce qui précède l'extrémité
      const before = next ? next.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_PRECEDING
        : node.contains(text) || node.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_PRECEDING;
      if (!before) break;
      total += text.data.length;
    }
    return { page, offset: total };
  }

  // ---- dessin ----
  paintPage(page) {
    const pageDiv = this.doc?.querySelector(`.page[data-page-number="${page}"]`);
    if (!pageDiv) return;
    pageDiv.querySelector(".arx-hl-layer")?.remove();  // couche et couche de texte : dans la page, bordure exclue
    const here = this.items.filter((a) => a.target.pdf.page <= page && (a.target.pdf.endPage || a.target.pdf.page) >= page);
    if (!here.length) return;
    const nodes = this.textNodes(page);
    if (!nodes.length) return;
    const layer = this.doc.createElement("div");
    layer.className = "arx-hl-layer";
    const outer = pageDiv.getBoundingClientRect();
    const box = { left: outer.left + pageDiv.clientLeft, top: outer.top + pageDiv.clientTop,
      width: pageDiv.clientWidth, height: pageDiv.clientHeight };
    const length = nodes.reduce((n, node) => n + node.data.length, 0);
    for (const annotation of here) {
      const { page: first, start, endPage = first, end } = annotation.target.pdf;
      for (const rect of this.bands(nodes, first === page ? start : 0, endPage === page ? end : length)) {
        const mark = this.doc.createElement("div");
        mark.className = `arx-hl arx-hl-${annotation.color || "yellow"}`;
        mark.dataset.annotation = annotation.id;
        Object.assign(mark.style, {
          left: `${(100 * (rect.left - box.left)) / box.width}%`, top: `${(100 * (rect.top - box.top)) / box.height}%`,
          width: `${(100 * rect.width) / box.width}%`, height: `${(100 * rect.height) / box.height}%`,
        });
        layer.append(mark);
      }
    }
    pageDiv.querySelector(".textLayer")?.before(layer);
  }

  // rectangles du passage, une bande par ligne : seulement le texte (les cadres des <span> doubleraient la couleur),
  // morceaux d'une même ligne réunis (formules : indices, exposants et blancs entre morceaux ne font pas de trous)
  bands(nodes, start, end) {
    const rects = [];
    let total = 0;
    for (const node of nodes) {
      const next = total + node.data.length;
      if (next > start && total < end) {
        const range = this.doc.createRange();
        range.setStart(node, Math.max(0, start - total));
        range.setEnd(node, Math.min(node.data.length, end - total));
        for (const r of range.getClientRects()) if (r.width >= 1 && r.height >= 1) rects.push(r);
      }
      total = next;
      if (total >= end) break;
    }
    const lines = [];
    for (const r of rects.sort((a, b) => a.left - b.left)) {
      const line = lines.find((l) => Math.min(l.bottom, r.bottom) - Math.max(l.top, r.top) >= 0.5 * Math.min(l.bottom - l.top, r.height)
        && r.left - l.right < 1.5 * (l.bottom - l.top) && l.left - r.right < 1.5 * (l.bottom - l.top));
      if (!line) { lines.push({ left: r.left, right: r.right, top: r.top, bottom: r.bottom }); continue; }
      line.left = Math.min(line.left, r.left);
      line.right = Math.max(line.right, r.right);
      if (r.height >= 0.7 * (line.bottom - line.top)) {  // indice, exposant : élargit la bande, sans la grandir
        line.top = Math.min(line.top, r.top);
        line.bottom = Math.max(line.bottom, r.bottom);
      }
    }
    return lines.map((l) => ({ left: l.left, top: l.top, width: l.right - l.left, height: l.bottom - l.top }));
  }

  // tous les passages (notes chargées après l'ouverture du PDF)
  repaintAll() {
    this.doc?.querySelectorAll(".page[data-page-number]").forEach((page) => this.paintPage(Number(page.dataset.pageNumber)));
  }

  repaint(annotation) {
    const { page, endPage = page } = annotation.target.pdf;
    for (let p = page; p <= endPage; p++) this.paintPage(p);
  }

  // ---- sélection : barre (couleurs, note, assistant, copie), ou clic sur un passage surligné ----
  onUp(event) {
    const selection = this.doc.getSelection();
    if (!selection.rangeCount || selection.isCollapsed) {
      if (event.target.closest?.("a, .annotationLayer section")) return;  // lien du PDF : suivi, sans ouvrir de note
      const hit = this.hitTest(event.clientX, event.clientY);
      if (hit) this.annotator.openHighlight(hit.id, this.anchor(hit.rect));
      return;
    }
    const range = selection.getRangeAt(0);
    const first = this.spot(range.startContainer, range.startOffset);
    const last = this.spot(range.endContainer, range.endOffset);
    const text = selection.toString().replace(/\s+/g, " ").trim();  // fins de ligne (<br>) comptées comme espaces
    if (!first || !last || !text) return;
    this.showToolbar(range, { page: first.page, start: first.offset, endPage: last.page, end: last.offset }, text);
  }

  // passage surligné sous le pointeur (les surlignages ne captent pas les clics : le texte reste sélectionnable)
  hitTest(x, y) {
    for (const mark of this.doc.querySelectorAll(".arx-hl")) {
      const rect = mark.getBoundingClientRect();
      if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) return { id: Number(mark.dataset.annotation), rect };
    }
    return null;
  }

  // rectangle du cadre traduit dans la page de l'application (pour placer une barre ou une note)
  anchor(rect) {
    const frame = this.view.frame.getBoundingClientRect();
    const r = { left: rect.left + frame.left, top: rect.top + frame.top, width: rect.width, height: rect.height };
    r.right = r.left + r.width;
    r.bottom = r.top + r.height;
    return { getBoundingClientRect: () => r };
  }

  showToolbar(range, target, text) {
    this.hideToolbar();
    const rect = this.anchor(range.getBoundingClientRect()).getBoundingClientRect();
    const create = (color, withNote) => this.create(target, text, color, withNote);
    this.toolbar = h("div", { class: "hl-toolbar", role: "toolbar", "aria-label": t("notes.highlight") },
      ...COLORS.map((color) => h("button", { type: "button", class: `hl-dot hl-${color}`, title: t(`notes.color.${color}`), "aria-label": t(`notes.color.${color}`),
        onclick: () => create(color, false) })),
      h("span", { class: "hl-sep" }),
      h("button", { type: "button", class: "hl-note-button", title: t("notes.addNote"), "aria-label": t("notes.addNote"), html: icons.note,
        onclick: () => create("yellow", true) }),
      this.onAskAI && h("button", { type: "button", class: "hl-note-button", title: t("ai.askPassage"), "aria-label": t("ai.askPassage"), html: icons.sparkle,
        onclick: () => { this.doc.getSelection().removeAllRanges(); this.hideToolbar(); this.onAskAI(text.slice(0, 12000)); } }),
      h("button", { type: "button", class: "hl-note-button hl-tex-button", title: t("notes.copyText"), "aria-label": t("notes.copyText"),
        onclick: async () => { await navigator.clipboard?.writeText(text).catch(() => {}); this.hideToolbar(); toast(t("notes.textCopied")); } }, "Aa"));
    document.body.append(this.toolbar);
    const width = this.toolbar.offsetWidth;
    this.toolbar.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, rect.left + rect.width / 2 - width / 2))}px`;
    this.toolbar.style.top = `${Math.max(64, rect.top - this.toolbar.offsetHeight - 8)}px`;
  }

  hideToolbar() { this.toolbar?.remove(); this.toolbar = null; }

  async create(target, text, color, withNote) {
    this.doc.getSelection().removeAllRanges();
    this.hideToolbar();
    try {
      const annotation = await api.addAnnotation(this.annotator.paperId, { kind: "highlight", target: { pdf: target }, color, quote: text.slice(0, 1500) });
      this.annotator.items.push(annotation);
      this.repaint(annotation);
      this.annotator.onChange();
      if (withNote) {
        const mark = this.doc.querySelector(`.arx-hl[data-annotation="${annotation.id}"]`);
        if (mark) this.annotator.openHighlight(annotation.id, this.anchor(mark.getBoundingClientRect()), true);
      }
    } catch (error) {
      toast(t("error.generic", { message: error.message }), { error: true });
    }
  }

  // appelés par l'annotateur
  recolor(id) { const a = this.annotator.items.find((x) => x.id === id); if (a && isPdf(a)) this.repaint(a); }
  removed(annotation) { if (isPdf(annotation)) this.repaint(annotation); }

  // onglet Notes : aller au passage (page, puis le passage au centre, brièvement encadré)
  async reveal(annotation) {
    const { page } = annotation.target.pdf;
    await this.view.goTo(page);
    for (let i = 0; i < 30; i++) {
      const mark = this.doc?.querySelector(`.arx-hl[data-annotation="${annotation.id}"]`);
      if (mark) {
        mark.scrollIntoView({ block: "center" });
        this.doc.querySelectorAll(`.arx-hl[data-annotation="${annotation.id}"]`).forEach((m) => {
          m.classList.add("flash");
          setTimeout(() => m.classList.remove("flash"), 1600);
        });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }

  destroy() {
    this.destroyed = true;
    this.hideToolbar();
    if (this.onParentDown) document.removeEventListener("mousedown", this.onParentDown);
  }
}
