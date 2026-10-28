// Surlignages, notes et questions à l'assistant sur un passage du PDF (pdf.js, dans un cadre de même origine).
// Un passage est repéré par sa page et sa position dans le texte de la page (couche de texte de pdf.js) :
// {pdf: {page, start, endPage, end}}, indépendant du zoom. Les surlignages sont redessinés à chaque rendu d'une
// page (défilement, zoom) dans une couche placée sous le texte ; ils figurent dans l'onglet Notes avec les autres.
// Zone (⌥ + glisser, ou bouton « Zone ») : un rectangle de la page, {pdf: {page, rect: [x0, y0, x1, y1]}} en
// fractions de la page, pour ce que la couche de texte n'a pas (formules d'un PDF numérisé, figures) ; son image
// accompagne la note (vignette) ou la question à l'assistant, s'il lit les images.

import { api } from "./api.js";
import { COLORS } from "./annotations.js";
import { closeBookmarkUi } from "./bookmarks.js";
import { t } from "./i18n.js";
import { h, icons, toast, toastError } from "./util.js";


// passage d'un document recompilé : retrouvé par son texte, aux espaces près, s'il est assez long pour être sans ambiguïté
const squash = (text) => text.replace(/\s+/g, "");
const MIN_QUOTE = 12;

// dessinés dans le PDF : passages et zones surlignés, notes libres (icône à l'endroit du clic droit)
const isPdf = (annotation) => (annotation.kind === "highlight" || annotation.kind === "note") && annotation.target?.pdf;

export class PdfNotes {
  // `annotator` : celui du lecteur (mêmes notes, même onglet) ; `view` : le PdfView ; `onAskAI(quote)` ;
  // zones : `readsImages()` (l'assistant lit-il les images ?), `onAskZone(zone)`, `onTranscribe(zone)`
  // marque-pages : `onAddBookmark(at)` (clic droit, ⌘D), `onBookmark(annotation, rect)` (clic sur un ruban)
  constructor({ annotator, view, onAskAI, readsImages = () => false, onAskZone = null, onTranscribe = null,
    onAddBookmark = null, onBookmark = null, onContextMenu = null, relocate = false }) {
    this.annotator = annotator;
    this.view = view;
    this.onAskAI = onAskAI;
    this.readsImages = readsImages;
    this.onAskZone = onAskZone;
    this.onTranscribe = onTranscribe;
    this.onAddBookmark = onAddBookmark;
    this.onBookmark = onBookmark;
    this.onContextMenu = onContextMenu;
    this.relocate = relocate;
    this.moved = new Map();  // passages dont le texte n'est plus à sa place (document recompilé) : cherchés ailleurs
    this.zoneMode = false;
    this.drawing = null;
    this.draft = null;
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
    app.eventBus.on("pagerendered", ({ pageNumber }) => this.paintPage(pageNumber));  // zones : même sans texte
    this.addZoneButton();
    this.doc.addEventListener("mousedown", (event) => this.zoneStart(event), true);
    this.doc.addEventListener("mousemove", (event) => this.zoneMove(event), true);
    this.doc.addEventListener("mouseup", (event) => this.zoneEnd(event), true);
    this.doc.querySelectorAll(".page[data-page-number]").forEach((page) => this.paintPage(Number(page.dataset.pageNumber)));
    this.doc.addEventListener("mouseup", (event) => setTimeout(() => this.onUp(event), 0));
    this.doc.addEventListener("selectionchange", () => this.paintSelection());
    this.doc.addEventListener("contextmenu", (event) => this.bookmarkMenu(event));
    // la barre et la note ouverte sont dans la page de l'application : fermées par un clic ou Échap dans le PDF
    // (l'annotateur n'écoute que sa page), et la barre au défilement du PDF ou à un clic ailleurs dans l'application
    this.doc.addEventListener("mousedown", () => { this.hideToolbar(); this.annotator.closePopover(); closeBookmarkUi(); });
    this.doc.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { this.hideToolbar(); this.annotator.closePopover(); this.setZoneMode(false); closeBookmarkUi(); }
      // ⌘D (Ctrl+D) : marque-page, comme dans le reste du lecteur
      if (event.key.toLowerCase() === "d" && (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && this.onAddBookmark) {
        event.preventDefault();
        this.onAddBookmark();
      }
    });
    this.doc.getElementById("viewerContainer")?.addEventListener("scroll", () => { this.hideToolbar(); closeBookmarkUi(); }, { passive: true });
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
    pageDiv.querySelector(".arx-hl-layer:not(.arx-sel-layer)")?.remove();
    this.paintBookmarks(pageDiv, page);
    const nodes = this.textNodes(page);
    if (this.relocate && nodes.length) {
      // passage d'une page qui n'existe plus (document raccourci) : cherché sur les dernières pages
      const count = this.app?.pagesCount || Infinity;
      for (const annotation of this.items) {
        const { page: first, endPage = first, rect } = annotation.target.pdf;
        if (first > count && first === endPage && !rect && annotation.kind === "highlight" && squash(annotation.quote || "").length >= MIN_QUOTE) {
          this.moved.set(annotation.id, annotation);
        }
      }
      this.findMoved(page, nodes);
    }
    const here = this.items.filter((a) => a.target.pdf.page <= page && (a.target.pdf.endPage || a.target.pdf.page) >= page
      && !(this.relocate && nodes.length && !this.inPlace(a, page, nodes)));
    if (!here.length) return;
    if (!nodes.length && !here.some((a) => a.target.pdf.rect || a.kind === "note")) return;
    const length = nodes.reduce((n, node) => n + node.data.length, 0);
    const box = this.boxOf(pageDiv);
    const rects = here.flatMap((annotation) => {
      const className = `arx-hl arx-hl-${annotation.color || "yellow"}`;
      const { page: first, start, endPage = first, end, rect } = annotation.target.pdf;
      if (annotation.kind === "note") {  // note libre : son icône dans la marge droite, à la hauteur du clic
        const view = this.app?.pdfViewer.getPageView(page - 1);
        if (!view?.viewport) return [];
        const [, y] = view.viewport.convertToViewportPoint(annotation.target.pdf.left ?? 0, annotation.target.pdf.top ?? 0);
        return [{ left: box.left + box.width - 40, top: box.top + y * (box.height / view.viewport.height), width: 20, height: 20,
          className: "arx-hl arx-pin", id: annotation.id }];
      }
      if (rect) {  // zone : le rectangle tel quel
        return [{ left: box.left + rect[0] * box.width, top: box.top + rect[1] * box.height, width: (rect[2] - rect[0]) * box.width,
          height: (rect[3] - rect[1]) * box.height, className: `${className} arx-zone`, id: annotation.id }];
      }
      return this.bands(nodes, first === page ? start : 0, endPage === page ? end : length)
        .map((r) => ({ ...r, className, id: annotation.id }));
    });
    const layer = this.layerFor(pageDiv, "arx-hl-layer", rects);
    const textLayer = pageDiv.querySelector(".textLayer");
    if (textLayer) textLayer.before(layer); else pageDiv.append(layer);
  }

  // ---- document recompilé (fichier TeX) : un passage surligné sur une seule page dont le texte n'est plus à sa
  // place n'est pas dessiné (plutôt qu'au mauvais endroit) ; il est cherché sur sa page et jusqu'à 3 pages autour,
  // et sa nouvelle place enregistrée ----
  inPlace(annotation, page, nodes) {
    const { page: first, endPage = first, start, end, rect } = annotation.target.pdf;
    if (annotation.kind !== "highlight" || rect || first !== endPage || first !== page || !annotation.quote) return true;
    const here = squash(nodes.map((node) => node.data).join("").slice(start, end));
    const quote = squash(annotation.quote);
    if (here && (here === quote || here.startsWith(quote))) return true;
    if (quote.length < MIN_QUOTE) return true;  // trop court pour être retrouvé sans ambiguïté : laissé où il est
    this.moved.set(annotation.id, annotation);
    // cherché sur sa page, puis sur les pages voisines déjà affichées (les suivantes le chercheront à leur tour)
    for (const near of [page, page - 1, page + 1, page - 2, page + 2, page - 3, page + 3]) {
      if (!this.moved.has(annotation.id)) break;
      const texts = near === page ? nodes : this.textNodes(near);
      if (texts.length) this.findMoved(near, texts, page);
    }
    return !this.moved.has(annotation.id) && annotation.target.pdf.page === page;
  }

  // passages déplacés retrouvés sur la page `page` ; `painting` : page en cours de dessin (les autres pages
  // concernées sont redessinées ensuite)
  findMoved(page, nodes, painting = page) {
    if (!this.moved.size) return;
    const raw = nodes.map((node) => node.data).join("");
    let squashed = "";
    const position = [];  // position dans le texte de la page de chaque caractère gardé
    for (let index = 0; index < raw.length; index++) {
      if (/\s/.test(raw[index])) continue;
      squashed += raw[index];
      position.push(index);
    }
    for (const annotation of [...this.moved.values()]) {
      const old = annotation.target.pdf;
      if (Math.abs(old.page - page) > 3) continue;
      const quote = squash(annotation.quote);
      const hits = [];
      for (let at = squashed.indexOf(quote); at >= 0 && hits.length < 50; at = squashed.indexOf(quote, at + 1)) hits.push(at);
      if (!hits.length) continue;
      // plusieurs fois sur la page : le plus près de l'ancienne place ; passage descendu sur une page suivante : le
      // premier ; remonté : le dernier
      const at = old.page === page ? hits.reduce((best, hit) => (Math.abs(position[hit] - old.start) < Math.abs(position[best] - old.start) ? hit : best))
        : old.page < page ? hits[0] : hits[hits.length - 1];
      annotation.target.pdf = { page, start: position[at], endPage: page, end: position[at + quote.length - 1] + 1 };
      this.moved.delete(annotation.id);
      api.updateAnnotation(annotation.id, { pdf: annotation.target.pdf }).catch(() => {});
      if (old.page !== painting) setTimeout(() => this.paintPage(old.page), 0);
      if (page !== painting) setTimeout(() => this.paintPage(page), 0);
    }
  }

  // rubans des marque-pages de la page, dans sa marge droite, à leur hauteur (points du PDF → fraction de la page)
  paintBookmarks(pageDiv, page) {
    pageDiv.querySelectorAll(".arx-bm").forEach((ribbon) => ribbon.remove());
    const marks = this.annotator.items.filter((a) => a.kind === "bookmark" && a.target?.pdf?.page === page);
    const view = marks.length && this.app?.pdfViewer.getPageView(page - 1);
    if (!view?.viewport) return;
    for (const annotation of marks) {
      const [, y] = view.viewport.convertToViewportPoint(0, annotation.target.pdf.top ?? view.viewport.viewBox[3]);
      const ribbon = this.doc.createElement("button");
      ribbon.type = "button";
      ribbon.className = "arx-bm";
      ribbon.title = annotation.quote || t("bookmarks.untitled");
      ribbon.setAttribute("aria-label", ribbon.title);
      ribbon.style.top = `${Math.max(0, Math.min(97, (100 * y) / view.viewport.height))}%`;
      ribbon.addEventListener("click", () => this.onBookmark?.(annotation, this.anchor(ribbon.getBoundingClientRect()).getBoundingClientRect()));
      pageDiv.append(ribbon);
    }
  }

  repaintBookmarks() {
    this.doc?.querySelectorAll(".page[data-page-number]").forEach((pageDiv) => this.paintBookmarks(pageDiv, Number(pageDiv.dataset.pageNumber)));
  }

  // clic droit sur une page, sans sélection (sinon le menu habituel, pour copier) : « Ajouter un marque-page »
  bookmarkMenu(event) {
    const pageDiv = event.target.closest?.(".page[data-page-number]");
    const keep = !this.doc.getSelection().isCollapsed || event.target.closest?.("a, input, select, textarea");
    if (keep) return;  // copier une sélection, lien, champ (numéro de page, recherche) : menu habituel
    event.preventDefault();  // ailleurs, pas de menu du navigateur (Précédent, Recharger…), sans objet ici
    if (!pageDiv || !this.onAddBookmark) return;
    const page = Number(pageDiv.dataset.pageNumber);
    const view = this.app.pdfViewer.getPageView(page - 1);
    const box = this.boxOf(pageDiv);
    const [, top] = view.viewport.convertToPdfPoint(event.clientX - box.left, Math.max(0, event.clientY - box.top - 8));
    const [left, at] = view.viewport.convertToPdfPoint(event.clientX - box.left - 10, event.clientY - box.top - 10);
    const point = this.anchor({ left: event.clientX, top: event.clientY, width: 0, height: 0 }).getBoundingClientRect();
    this.onContextMenu?.(point, [
      [t("notes.addFreeNote"), () => this.annotator.createFreeNote({ pdf: { page, top: at, left } }, { getBoundingClientRect: () => point })],
      [t("bookmarks.addHereMenu"), () => this.onAddBookmark({ pdf: { page, top }, rect: point })],
    ]);
  }

  // boîte de la page sans sa bordure (celle de la couche de texte), en coordonnées du cadre
  boxOf(pageDiv) {
    const outer = pageDiv.getBoundingClientRect();
    return { left: outer.left + pageDiv.clientLeft, top: outer.top + pageDiv.clientTop, width: pageDiv.clientWidth, height: pageDiv.clientHeight };
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

  // sélection en cours, dessinée en bandes (même calcul que les surlignages) ; extrémité hors de la couche de
  // texte : la sélection du navigateur reste visible
  paintSelection() {
    this.doc.querySelectorAll(".arx-sel-layer").forEach((layer) => layer.remove());
    this.doc.querySelectorAll(".textLayer.arx-selecting").forEach((layer) => layer.classList.remove("arx-selecting"));
    const selection = this.doc.getSelection();
    if (!selection.rangeCount || selection.isCollapsed) return;
    const range = selection.getRangeAt(0);
    const first = this.spot(range.startContainer, range.startOffset);
    const last = this.spot(range.endContainer, range.endOffset);
    if (!first || !last) return;
    for (let page = first.page; page <= last.page; page++) {
      const pageDiv = this.doc.querySelector(`.page[data-page-number="${page}"]`);
      const textLayer = pageDiv?.querySelector(".textLayer");
      const nodes = this.textNodes(page);
      if (!textLayer || !nodes.length) continue;
      const length = nodes.reduce((n, node) => n + node.data.length, 0);
      const rects = this.bands(nodes, page === first.page ? first.offset : 0, page === last.page ? last.offset : length);
      textLayer.before(this.layerFor(pageDiv, "arx-hl-layer arx-sel-layer", rects.map((rect) => ({ ...rect, className: "arx-sel" }))));
      textLayer.classList.add("arx-selecting");
    }
  }

  // couche de rectangles ({left, top, width, height, className, id?}), en % de la page, bordure exclue (comme la
  // couche de texte de pdf.js) : suit le zoom
  layerFor(pageDiv, className, rects) {
    const box = this.boxOf(pageDiv);
    const layer = this.doc.createElement("div");
    layer.className = className;
    for (const rect of rects) {
      const mark = this.doc.createElement("div");
      mark.className = rect.className;
      if (rect.id != null) mark.dataset.annotation = rect.id;
      Object.assign(mark.style, {
        left: `${(100 * (rect.left - box.left)) / box.width}%`, top: `${(100 * (rect.top - box.top)) / box.height}%`,
        width: `${(100 * rect.width) / box.width}%`, height: `${(100 * rect.height) / box.height}%`,
      });
      layer.append(mark);
    }
    return layer;
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

  hideToolbar() {
    this.toolbar?.remove();
    this.toolbar = null;
    this.draft?.remove();  // zone tracée sans suite
    this.draft = null;
  }

  // ---- zones : ⌥ + glisser, ou bouton « Zone » de la barre (une zone, puis retour au texte) ----
  addZoneButton() {
    const middle = this.doc.getElementById("toolbarViewerMiddle");
    if (!middle || this.doc.getElementById("arxZoneButton")) return;
    this.zoneButton = this.doc.createElement("button");
    Object.assign(this.zoneButton, { id: "arxZoneButton", className: "toolbarButton", type: "button", title: t("pdf.zone") });
    this.zoneButton.setAttribute("aria-label", t("pdf.zone"));
    this.zoneButton.setAttribute("aria-pressed", "false");
    this.zoneButton.addEventListener("click", () => this.setZoneMode(!this.zoneMode));
    middle.append(this.zoneButton);
  }

  setZoneMode(on) {
    this.zoneMode = on;
    this.zoneButton?.classList.toggle("toggled", on);
    this.zoneButton?.setAttribute("aria-pressed", String(on));
    this.doc?.body.classList.toggle("arx-zone-mode", on);
  }

  zoneStart(event) {
    if (event.button !== 0 || !(event.altKey || this.zoneMode)) return;
    const pageDiv = event.target.closest?.(".page[data-page-number]");
    if (!pageDiv) return;
    event.preventDefault();  // pas de sélection de texte, ni les clics de pdf.js
    event.stopPropagation();
    this.hideToolbar();
    this.annotator.closePopover();
    this.doc.getSelection().removeAllRanges();
    const box = this.boxOf(pageDiv);
    const at = [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height].map((v) => Math.min(1, Math.max(0, v)));
    this.draft = this.doc.createElement("div");
    this.draft.className = "arx-zone-draft";
    pageDiv.append(this.draft);
    this.drawing = { pageDiv, page: Number(pageDiv.dataset.pageNumber), from: at, to: at };
    this.drawDraft();
  }

  zoneMove(event) {
    if (!this.drawing) return;
    event.preventDefault();
    const box = this.boxOf(this.drawing.pageDiv);
    this.drawing.to = [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height].map((v) => Math.min(1, Math.max(0, v)));
    this.drawDraft();
  }

  drawDraft() {
    const { from, to } = this.drawing;
    Object.assign(this.draft.style, {
      left: `${100 * Math.min(from[0], to[0])}%`, top: `${100 * Math.min(from[1], to[1])}%`,
      width: `${100 * Math.abs(to[0] - from[0])}%`, height: `${100 * Math.abs(to[1] - from[1])}%`,
    });
  }

  zoneEnd(event) {
    if (!this.drawing) return;
    event.preventDefault();
    event.stopPropagation();  // pas de sélection ni de clic sur un surlignage (mouseup ordinaire)
    const { pageDiv, page, from, to } = this.drawing;
    this.drawing = null;
    this.setZoneMode(false);
    const rect = [Math.min(from[0], to[0]), Math.min(from[1], to[1]), Math.max(from[0], to[0]), Math.max(from[1], to[1])];
    const box = this.boxOf(pageDiv);
    if ((rect[2] - rect[0]) * box.width < 8 || (rect[3] - rect[1]) * box.height < 8) { this.hideToolbar(); return; }  // simple clic
    this.showZoneToolbar({ page, rect });
  }

  showZoneToolbar(zone) {
    const draft = this.draft;
    this.toolbar?.remove();
    const create = (color, withNote) => this.createZone(zone, color, withNote);
    const withImage = (handler) => async () => {
      const image = await this.zoneImage(zone).catch(() => null);
      this.hideToolbar();
      if (image) handler({ image, page: zone.page, quote: this.zoneText(zone) });
      else toast(t("error.generic", { message: "image" }), { error: true });
    };
    const ai = this.readsImages() && this.onAskZone;
    this.toolbar = h("div", { class: "hl-toolbar", role: "toolbar", "aria-label": t("notes.highlight") },
      ...COLORS.map((color) => h("button", { type: "button", class: `hl-dot hl-${color}`, title: t(`notes.color.${color}`), "aria-label": t(`notes.color.${color}`),
        onclick: () => create(color, false) })),
      h("span", { class: "hl-sep" }),
      h("button", { type: "button", class: "hl-note-button", title: t("notes.addNote"), "aria-label": t("notes.addNote"), html: icons.note,
        onclick: () => create("yellow", true) }),
      ai && h("button", { type: "button", class: "hl-note-button", title: t("ai.askPassage"), "aria-label": t("ai.askPassage"), html: icons.sparkle,
        onclick: withImage(this.onAskZone) }),
      ai && this.onTranscribe && h("button", { type: "button", class: "hl-note-button hl-tex-button", title: t("ai.transcribe"), "aria-label": t("ai.transcribe"),
        onclick: withImage(this.onTranscribe) }, "TeX"));
    this.draft = draft;  // gardée affichée tant que la barre l'est
    document.body.append(this.toolbar);
    const rect = this.anchor(draft.getBoundingClientRect()).getBoundingClientRect();
    const width = this.toolbar.offsetWidth;
    this.toolbar.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, rect.left + rect.width / 2 - width / 2))}px`;
    this.toolbar.style.top = `${Math.max(64, rect.top - this.toolbar.offsetHeight - 8)}px`;
  }

  // image de la zone, rendue par pdf.js à une résolution lisible (formules : ~1200 px de large au plus)
  async zoneImage(zone) {
    const page = await this.app.pdfDocument.getPage(zone.page);
    const rotation = (page.rotate + (this.app.pdfViewer.pagesRotation || 0)) % 360;
    const [x0, y0, x1, y1] = zone.rect;
    const base = page.getViewport({ scale: 1, rotation });
    const scale = Math.min(4, Math.max(1.5, 1200 / ((x1 - x0) * base.width)), 3000 / ((y1 - y0) * base.height));
    const viewport = page.getViewport({ scale, rotation });
    const canvas = this.doc.createElement("canvas");
    canvas.width = Math.max(1, Math.round((x1 - x0) * viewport.width));
    canvas.height = Math.max(1, Math.round((y1 - y0) * viewport.height));
    const context = canvas.getContext("2d");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvasContext: context, viewport, annotationMode: 0,
      transform: [1, 0, 0, 1, -x0 * viewport.width, -y0 * viewport.height] }).promise;
    return canvas.toDataURL("image/png");
  }

  // texte que la couche de texte donne dans la zone (souvent faux ou absent pour une formule numérisée)
  zoneText(zone) {
    const pageDiv = this.doc.querySelector(`.page[data-page-number="${zone.page}"]`);
    if (!pageDiv) return "";
    const box = this.boxOf(pageDiv);
    const [x0, y0, x1, y1] = zone.rect;
    const inside = (r) => {
      const x = (r.left + r.width / 2 - box.left) / box.width, y = (r.top + r.height / 2 - box.top) / box.height;
      return x >= x0 && x <= x1 && y >= y0 && y <= y1;
    };
    return this.textNodes(zone.page).filter((node) => inside(node.parentElement.getBoundingClientRect()))
      .map((node) => node.data).join(" ").replace(/\s+/g, " ").trim().slice(0, 1500);
  }

  async createZone(zone, color, withNote) {
    try {
      const image = await this.zoneImage(zone).catch(() => null);
      const quote = this.zoneText(zone);
      this.hideToolbar();
      const annotation = await api.addAnnotation(this.annotator.paperId, { kind: "highlight", target: { pdf: zone }, color,
        quote: quote || null, image });
      this.annotator.items.push(annotation);
      this.repaint(annotation);
      this.annotator.onChange();
      if (withNote) {
        const mark = this.doc.querySelector(`.arx-hl[data-annotation="${annotation.id}"]`);
        if (mark) this.annotator.openHighlight(annotation.id, this.anchor(mark.getBoundingClientRect()), true);
      }
    } catch (error) {
      this.hideToolbar();
      toastError(error);
    }
  }

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
      toastError(error);
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
