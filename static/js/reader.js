// Lecteur d'article : texte, sommaire, aperçus des renvois, preuves repliables, panneau des énoncés.

import { api } from "./api.js";
import { closure, renderGraph } from "./graph.js";
import { typesetFragment, typesetMath } from "./math.js";
import { t } from "./i18n.js";
import { onSettingsChange, saveSettings, settings } from "./settings.js";
import { debounce, formatDate, h, icons, toast, withMath } from "./util.js";

const NEEDS_PROOF = new Set(["theorem", "proposition", "lemma", "corollary", "claim"]);
const WIDE = 1280;
const MEDIUM = 900;

export async function renderReader(view, paperId) {
  view.replaceChildren(h("div", { class: "library" }, h("div", { class: "busy" }, h("span", { class: "spinner" }))));
  const { paper, structure } = await api.paper(paperId);
  const html = paper.source === "none" ? "" : await api.content(paperId);
  const reader = new Reader(view, paper, structure || { toc: [], nodes: [], bibliography: [], hiddenText: 0 }, html);
  return () => reader.destroy();
}

class Reader {
  constructor(view, paper, structure, html) {
    this.paper = paper;
    this.structure = structure;
    this.nodes = structure.nodes;
    this.byId = new Map(this.nodes.map((n) => [n.id, n]));
    this.bib = new Map(structure.bibliography.map((b) => [b.id, b]));
    this.selected = null;
    this.tab = "statements";
    this.showIsolated = false;
    this.kindFilter = new Set();
    this.search = "";
    this.backStack = [];
    this.anchors = null;
    this.cleanups = [];

    this.build(view, html);
    this.enhanceContent();
    this.renderToc();
    this.renderPanel();
    this.bindEvents();
    this.restorePosition();
    if (settings.mathRenderer !== "native") setTimeout(() => this.renderMath(), 0);
  }

  // Formules en MathJax, de l'écran vers le reste de l'article, sans faire sauter la lecture
  renderMath() {
    const view = this.scroller.getBoundingClientRect();
    const middle = view.top + view.height / 2;
    typesetMath(this.content, {
      priority: (math) => {
        const r = math.getBoundingClientRect();
        return r.height === 0 && r.width === 0 ? 1e9 : Math.abs(r.top - middle);
      },
      beforeChunk: () => {
        this.anchors = null;
        const anchor = this.currentAnchor();
        return anchor ? { anchor, top: anchor.getBoundingClientRect().top } : null;
      },
      afterChunk: (state) => {
        this.anchors = null;
        if (state?.anchor.isConnected) this.scroller.scrollTop += state.anchor.getBoundingClientRect().top - state.top;
      },
      isCancelled: () => this.destroyed,
    }).catch(() => {});
  }

  // ---------- Construction ----------

  build(view, html) {
    const wide = innerWidth >= WIDE;
    const medium = innerWidth >= MEDIUM;
    this.root = h("div", {
      class: "reader",
      dataset: {
        toc: wide && settings.tocOpen ? "open" : "closed",
        panel: medium && settings.panelOpen ? "open" : "closed",
      },
    });
    const button = (icon, key, onclick, extra = {}) => h("button", {
      class: `icon-button ${extra.class || ""}`, type: "button", title: t(key), "aria-label": t(key), html: icon, onclick, ...extra.attrs,
    });
    const paper = this.paper;
    this.tocButton = button(icons.menu, "reader.toc", () => this.toggle("toc"), { attrs: { "aria-pressed": this.root.dataset.toc === "open" } });
    this.panelButton = button(icons.panel, "reader.panel", () => this.toggle("panel"), { attrs: { "aria-pressed": this.root.dataset.panel === "open" } });
    this.foldButton = button(icons.fold, "reader.foldAll", () => this.foldAll(), { class: "wide-only" });
    const link = (icon, title, href) => h("a", { class: "icon-button", href, target: "_blank", rel: "noopener noreferrer", title, "aria-label": title, html: icon });

    const toolbar = h("div", { class: "reader-toolbar" },
      this.tocButton,
      h("a", { class: "icon-button", href: "#/", title: t("reader.back"), "aria-label": t("reader.back"), html: icons.back }),
      h("div", { class: "title", title: paper.title }, withMath(paper.title)),
      button(icons.minus, "reader.smaller", () => this.changeFont(-1), { class: "wide-only" }),
      button(icons.plus, "reader.larger", () => this.changeFont(1), { class: "wide-only" }),
      this.foldButton,
      h("span", { class: "sep wide-only" }),
      link(icons.external, "arXiv", `https://arxiv.org/abs/${paper.arxiv_id}v${paper.version}`),
      link(icons.pdf, "PDF", `https://arxiv.org/pdf/${paper.arxiv_id}v${paper.version}`),
      this.panelButton,
    );

    this.toc = h("aside", { class: "toc", "aria-label": t("reader.contents") });
    this.panel = h("aside", { class: "panel", "aria-label": t("reader.panel") });
    this.content = h("div", { class: "paper-content" });
    this.article = h("article", { class: "paper", dataset: { marks: settings.marginMarks ? "on" : "off" } });
    this.backButton = h("button", { class: "back-button", type: "button", onclick: () => this.goBack() }, `↩ ${t("reader.returnTo")}`);
    this.scroller = h("div", { class: "paper-scroll" }, this.article, this.backButton);

    const meta = h("div", { class: "paper-meta" },
      paper.primary_category && h("span", { class: "chip category" }, paper.primary_category),
      ...paper.categories.filter((c) => c !== paper.primary_category).map((c) => h("span", { class: "chip" }, c)),
      h("span", { class: "chip" }, `arXiv:${paper.arxiv_id} · ${t("reader.version", { v: paper.version })}`),
      h("span", { class: "chip" }, formatDate(paper.updated || paper.published)),
      paper.source !== "none" && h("span", { class: "chip" }, t(`reader.source.${paper.source}`)),
      paper.doi && h("a", { class: "chip", href: `https://doi.org/${paper.doi}`, target: "_blank", rel: "noopener noreferrer" }, `DOI ${paper.doi}`),
    );
    this.article.append(meta);

    if (this.structure.hiddenText > 0) {
      const toggle = h("button", { type: "button" }, t("reader.reveal"));
      toggle.addEventListener("click", () => {
        const on = this.article.classList.toggle("reveal-hidden");
        toggle.textContent = on ? t("reader.conceal") : t("reader.reveal");
      });
      this.article.append(h("div", { class: "notice" }, `⚠ ${t("reader.hiddenText", { n: this.structure.hiddenText })}`, toggle));
    }

    if (paper.source === "none") {
      this.article.append(
        h("div", { class: "notice" }, t("reader.noHtml")),
        h("h1", { class: "ltx_title_document" }, withMath(paper.title)),
        h("div", { class: "ltx_authors" }, paper.authors.join(", ")),
        h("div", { class: "ltx_abstract" }, h("h6", { class: "ltx_title_abstract" }, t("reader.abstract")), h("p", {}, paper.abstract)),
      );
    } else {
      this.content.innerHTML = html; // HTML nettoyé côté serveur ; scripts bloqués par la CSP
      this.article.append(this.content);
    }

    this.root.append(toolbar, this.toc, this.scroller, this.panel,
      h("div", { class: "scrim", onclick: () => this.closeDrawers() }));
    view.replaceChildren(this.root);
  }

  enhanceContent() {
    const base = `/api/papers/${encodeURIComponent(this.paper.id)}/`;
    this.content.querySelectorAll('img[src^="assets/"]').forEach((img) => { img.src = base + img.getAttribute("src"); });

    // Formules et tableaux larges : défilement horizontal plutôt que débordement
    this.content.querySelectorAll("table.ltx_eqn_table, table.ltx_tabular").forEach((table) => {
      if (table.parentElement.classList.contains("eq-scroll")) return;
      const wrap = h("div", { class: "eq-scroll" });
      table.replaceWith(wrap);
      wrap.append(table);
    });

    if (!this.content.querySelector("[lang]")) this.content.lang = "en"; // césure à l'anglaise

    // Ponctuation collée à la formule qui la précède (pas de « , » seule en début de ligne)
    this.content.querySelectorAll("math").forEach((math) => {
      const next = math.nextSibling;
      if (!next || next.nodeType !== Node.TEXT_NODE || !/^[.,;:)\]]/.test(next.data)) return;
      const length = next.data.match(/^[.,;:)\]]+/)[0].length;
      const rest = next.splitText(length);
      const wrap = h("span", { class: "nobr" });
      math.replaceWith(wrap);
      wrap.append(math, next);
      void rest;
    });

    // Comme dans amsart, la date rejoint les mots-clés et la classification (note de première page)
    const dates = this.content.querySelector(".ltx_dates");
    const frontNotes = this.content.querySelectorAll(".ltx_keywords, .ltx_classification");
    if (dates && frontNotes.length) frontNotes[frontNotes.length - 1].after(dates);
    const front = this.content.querySelectorAll(".ltx_abstract, .ltx_keywords, .ltx_classification, .ltx_dates");
    if (front.length) [...front].sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1)).at(-1).classList.add("front-last");

    // Preuves repliables ; le ∎ de LaTeXML devient le □ d'amsthm, aligné à droite
    this.content.querySelectorAll(".ltx_proof").forEach((proof) => {
      const detected = proof.classList.contains("arx-detected-proof");
      this.markQed(proof);
      const title = proof.querySelector(".ltx_title_proof")?.textContent.trim() || "Proof.";
      const state = h("span", { class: "state" });
      const toggle = h("button", { class: "proof-toggle", type: "button", "aria-expanded": "true", title: detected ? t("reader.proofDetected") : t("reader.proof") },
        h("span", { class: "caret" }, "▸"), h("span", { class: "text" }, title), state);
      toggle.addEventListener("click", () => this.setProof(proof, !proof.classList.contains("collapsed")));
      proof.prepend(toggle);
      this.setProof(proof, settings.proofsCollapsed);
    });
    this.updateFoldButton();

    // Titres d'énoncés cliquables → panneau des dépendances
    this.content.querySelectorAll(".ltx_theorem").forEach((theorem) => {
      const title = theorem.querySelector(".ltx_title_theorem, .ltx_title");
      if (!title || !this.byId.has(theorem.id)) return;
      title.title = t("tabs.dependencies");
      title.addEventListener("click", () => this.select(theorem.id, { tab: "dependencies", openPanel: true }));
    });
  }

  markQed(proof) {
    const walker = document.createTreeWalker(proof, NodeFilter.SHOW_TEXT);
    const hits = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.data.includes("∎") && !node.parentElement.closest("math")) hits.push(node);
    }
    const last = hits.at(-1);
    if (!last) return;
    last.data = last.data.replace(/\s*∎\s*$/, "").replace("∎", "");
    last.after(h("span", { class: "qed", "aria-hidden": "true" }, "□"));
  }

  setProof(proof, collapsed) {
    proof.classList.toggle("collapsed", collapsed);
    const toggle = proof.querySelector(":scope > .proof-toggle");
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.querySelector(".state").textContent = collapsed ? `(${t("reader.showProof")})` : "";
    toggle.setAttribute("aria-label", `${t("reader.proof")} — ${collapsed ? t("reader.showProof") : t("reader.hideProof")}`);
    this.anchors = null;
    this.updateFoldButton();
  }

  foldAll() {
    const proofs = [...this.content.querySelectorAll(".ltx_proof")];
    const collapse = proofs.some((p) => !p.classList.contains("collapsed"));
    proofs.forEach((p) => this.setProof(p, collapse));
  }

  updateFoldButton() {
    if (!this.foldButton) return;
    const proofs = [...this.content.querySelectorAll(".ltx_proof")];
    const anyOpen = proofs.some((p) => !p.classList.contains("collapsed"));
    const label = anyOpen ? t("reader.foldAll") : t("reader.unfoldAll");
    this.foldButton.title = label;
    this.foldButton.setAttribute("aria-label", label);
    this.foldButton.hidden = proofs.length === 0;
  }

  changeFont(delta) {
    const size = Math.min(24, Math.max(14, settings.fontSize + delta));
    saveSettings({ fontSize: size });
  }

  // ---------- Volets ----------

  toggle(which) {
    const open = this.root.dataset[which] !== "open";
    if (open && innerWidth < WIDE) this.closeDrawers(which); // un seul tiroir à la fois sur petit écran
    this.root.dataset[which] = open ? "open" : "closed";
    (which === "toc" ? this.tocButton : this.panelButton).setAttribute("aria-pressed", String(open));
    const remembered = which === "toc" ? innerWidth >= WIDE : innerWidth >= MEDIUM;
    if (remembered) saveSettings({ [which === "toc" ? "tocOpen" : "panelOpen"]: open });
    this.anchors = null;
  }

  setOpen(which, open) {
    this.root.dataset[which] = open ? "open" : "closed";
    (which === "toc" ? this.tocButton : this.panelButton).setAttribute("aria-pressed", String(open));
  }

  closeDrawers(except) {
    for (const which of ["toc", "panel"]) {
      if (which === except) continue;
      const isDrawer = which === "toc" ? innerWidth < WIDE : innerWidth < MEDIUM;
      if (isDrawer && this.root.dataset[which] === "open") {
        this.root.dataset[which] = "closed";
        (which === "toc" ? this.tocButton : this.panelButton).setAttribute("aria-pressed", "false");
      }
    }
  }

  // ---------- Sommaire ----------

  renderToc() {
    const items = [];
    const abstract = this.content.querySelector(".ltx_abstract");
    if (abstract) {
      abstract.id ||= "arx-abstract";
      items.push({ id: abstract.id, level: 1, title: t("reader.abstract") });
    }
    items.push(...this.structure.toc.filter((s) => s.level <= 3));
    this.tocLinks = new Map();
    const list = h("ol", {}, items.map((item) => {
      const a = h("a", { href: `#${item.id}` }, withMath(item.title));
      a.addEventListener("click", (event) => {
        event.preventDefault();
        this.navigate(item.id, { remember: false });
        this.closeDrawers();
      });
      this.tocLinks.set(item.id, a);
      return h("li", { class: `level-${Math.max(1, item.level)}` }, a);
    }));
    this.toc.replaceChildren(h("h3", {}, t("reader.contents")), list);
  }

  // ---------- Navigation dans l'article ----------

  find(id) {
    return id ? this.content.querySelector(`[id="${CSS.escape(id)}"]`) : null;
  }

  navigate(id, { remember = true } = {}) {
    const target = this.find(id);
    if (!target) return;
    const proof = target.closest(".ltx_proof.collapsed");
    if (proof) this.setProof(proof, false);
    if (remember) {
      this.backStack.push(this.scroller.scrollTop);
      this.backButton.classList.add("visible");
    }
    const block = target.closest(".ltx_theorem, .ltx_para, .ltx_bibitem, .ltx_equation, .eq-scroll, section") || target;
    this.scrollToElement(block);
    block.classList.remove("flash");
    void block.offsetWidth;
    block.classList.add("flash");
  }

  // Défilement doux pour les petits sauts, immédiat pour les longs (plus lisible).
  scrollToElement(element, smooth = true) {
    const offset = element.getBoundingClientRect().top - this.scroller.getBoundingClientRect().top - 12;
    const near = Math.abs(offset) < this.scroller.clientHeight * 1.5;
    this.scroller.scrollTo({ top: this.scroller.scrollTop + offset, behavior: smooth && near ? "smooth" : "instant" });
  }

  goBack() {
    const top = this.backStack.pop();
    if (top !== undefined) this.scroller.scrollTo({ top, behavior: "instant" });
    this.backButton.classList.toggle("visible", this.backStack.length > 0);
  }

  computeAnchors() {
    const top = this.scroller.getBoundingClientRect().top - this.scroller.scrollTop;
    const elements = this.content.querySelectorAll("section[id], .ltx_theorem[id], .ltx_para[id], .ltx_abstract[id]");
    this.anchors = [...elements].map((el) => ({ el, y: el.getBoundingClientRect().top - top }))
      .filter((a) => a.el.offsetParent !== null)
      .sort((a, b) => a.y - b.y);
  }

  currentAnchor() {
    if (!this.anchors) this.computeAnchors();
    const y = this.scroller.scrollTop + 24;
    let low = 0;
    let high = this.anchors.length - 1;
    let found = null;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (this.anchors[mid].y <= y) { found = this.anchors[mid]; low = mid + 1; } else high = mid - 1;
    }
    return found?.el ?? null;
  }

  onScroll() {
    const anchor = this.currentAnchor();
    if (!anchor) return;
    // Section courante dans le sommaire : la dernière dont le début est au-dessus de la ligne de lecture
    const y = this.scroller.scrollTop + 24;
    let section = null;
    for (const id of this.tocLinks.keys()) {
      const entry = this.anchors.find((a) => a.el.id === id);
      if (entry && entry.y <= y) section = id;
    }
    this.tocLinks.forEach((a, id) => a.classList.toggle("current", id === section));
    // Énoncé courant dans la liste
    const theorem = anchor.closest(".ltx_theorem");
    this.panel.querySelectorAll(".node-item.current").forEach((el) => el.classList.remove("current"));
    if (theorem) this.panel.querySelector(`.node-item[data-id="${CSS.escape(theorem.id)}"]`)?.classList.add("current");
    this.savePosition(anchor.id);
  }

  restorePosition() {
    const target = this.find(this.paper.position);
    if (!target) return;
    requestAnimationFrame(() => {
      this.scrollToElement(target, false);
      toast(t("reader.resumed"));
    });
  }

  // ---------- Aperçus au survol ----------

  previewFor(link) {
    if (link.classList.contains("ltx_note_mark") || link.closest(".ltx_note")) {
      const note = link.closest(".ltx_note")?.querySelector(".ltx_note_content");
      return note ? { body: this.clean(note) } : null;
    }
    const id = link.getAttribute("href")?.slice(1);
    const target = this.find(id);
    if (!target) return null;
    const theorem = target.closest(".ltx_theorem");
    if (theorem) return { body: this.clean(theorem), id: theorem.id, label: this.byId.get(theorem.id)?.label };
    const bibitem = target.closest(".ltx_bibitem");
    if (bibitem) return { body: this.clean(bibitem), id: bibitem.id, extra: this.bibActions(this.bib.get(bibitem.id)) };
    const equation = target.closest(".eq-scroll") || target.closest("table");
    if (equation && equation.querySelector(".ltx_tag_equation")) {
      const row = target.matches("tbody, tr") ? target : null;
      const body = row ? h("table", { class: "ltx_eqn_table" }, this.clean(row)) : this.clean(equation);
      return { body, id };
    }
    const section = target.closest("section");
    if (section && section.id === id) {
      const heading = section.querySelector("h1, h2, h3, h4, h5, h6");
      const para = section.querySelector(".ltx_para");
      return { body: h("div", {}, heading && this.clean(heading), para && this.clean(para)), id };
    }
    return { body: this.clean(target.closest(".ltx_para") || target), id };
  }

  clean(element) {
    const copy = element.cloneNode(true);
    copy.removeAttribute("id");
    copy.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
    copy.querySelectorAll(".proof-toggle").forEach((el) => el.remove());
    copy.classList.remove("selected", "flash");
    return copy;
  }

  bibActions(entry) {
    if (!entry?.arxiv?.length) return null;
    return h("span", {}, entry.arxiv.map((arxivId) => h("button", {
      type: "button",
      onclick: async () => {
        try {
          const paper = await api.importPaper(arxivId);
          toast(`${t("reader.import")} ✓ ${paper.title}`);
        } catch (error) {
          toast(error.message, { error: true });
        }
      },
    }, `${t("reader.import")} arXiv:${arxivId}`)));
  }

  showPreview(link) {
    const preview = this.previewFor(link);
    if (!preview) return;
    this.hidePreview();
    const footer = h("div", { class: "preview-footer" },
      preview.extra || h("span", {}, preview.label || ""),
      preview.id && h("button", { type: "button", onclick: () => { this.hidePreview(); this.navigate(preview.id); } }, t("reader.goTo")));
    const box = h("div", { class: "preview", role: "tooltip" }, preview.body, footer);
    box.addEventListener("mouseenter", () => clearTimeout(this.hideTimer));
    box.addEventListener("mouseleave", () => this.scheduleHide());
    document.body.append(box);
    typesetFragment(box);
    const rect = link.getBoundingClientRect();
    const width = box.offsetWidth;
    const height = box.offsetHeight;
    const left = Math.min(Math.max(12, rect.left - 20), innerWidth - width - 12);
    const below = rect.bottom + 8 + height < innerHeight;
    box.style.left = `${left}px`;
    box.style.top = `${below ? rect.bottom + 8 : Math.max(8, rect.top - height - 8)}px`;
    this.previewBox = box;
  }

  scheduleHide() {
    clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(() => this.hidePreview(), 250);
  }

  hidePreview() {
    clearTimeout(this.showTimer);
    this.previewBox?.remove();
    this.previewBox = null;
  }

  // ---------- Panneau : énoncés, dépendances, graphe ----------

  select(id, { tab, openPanel = false, scroll = false } = {}) {
    this.selected = id;
    this.content.querySelectorAll(".ltx_theorem.selected").forEach((el) => el.classList.remove("selected"));
    this.find(id)?.classList.add("selected");
    if (tab) this.tab = tab;
    if (openPanel && this.root.dataset.panel !== "open") this.toggle("panel");
    if (scroll) {
      this.navigate(id, { remember: false });
      if (innerWidth < MEDIUM) this.closeDrawers();
    }
    this.renderPanel();
  }

  renderPanel() {
    const tabs = [["statements", t("tabs.statements")], ["dependencies", t("tabs.dependencies")], ["graph", t("tabs.graph")]];
    const tabBar = h("div", { class: "tabs", role: "tablist" }, tabs.map(([key, label]) => h("button", {
      type: "button", role: "tab", "aria-selected": String(this.tab === key),
      onclick: () => { this.tab = key; this.renderPanel(); },
    }, label)));
    const body = h("div", { class: "panel-body" });
    if (this.tab === "statements") this.renderStatements(body);
    else if (this.tab === "dependencies") this.renderDependencies(body);
    else this.renderGraphTab(body);
    this.panel.replaceChildren(tabBar, body);
  }

  nodeItem(node) {
    const badges = [];
    if (NEEDS_PROOF.has(node.kind) && !node.proofs.length) badges.push(h("span", { class: "badge" }, t("panel.noProof")));
    if (node.usedBy.length) badges.push(h("span", { class: "badge" }, t("panel.usedCount", { n: node.usedBy.length })));
    return h("button", {
      class: `node-item${node.id === this.selected ? " selected" : ""}`, type: "button",
      dataset: { id: node.id, kind: node.kind },
      title: node.name ? `${node.label} — ${node.name}` : node.label,
      onclick: () => this.select(node.id, { scroll: true }),
    },
      h("span", { class: "dot" }), h("span", { class: "label" }, node.label),
      h("span", { class: "name" }, withMath(node.name)), h("span", { class: "badges" }, badges));
  }

  renderStatements(body) {
    if (!this.nodes.length) {
      body.append(h("p", { class: "hint" }, t("panel.noStatements")));
      return;
    }
    const search = h("input", { class: "text-input", type: "search", value: this.search, placeholder: t("panel.filter"), "aria-label": t("panel.filter") });
    const kinds = [...new Set(this.nodes.map((n) => n.kind))];
    const list = h("div", {});
    const draw = () => {
      const needle = this.search.trim().toLowerCase();
      const shown = this.nodes.filter((n) => (!this.kindFilter.size || this.kindFilter.has(n.kind))
        && (!needle || `${n.label} ${n.name}`.toLowerCase().includes(needle)));
      const sections = new Map(this.structure.toc.map((s) => [s.id, s]));
      const groups = new Map();
      for (const node of shown) {
        const top = this.topSection(node.section, sections);
        if (!groups.has(top)) groups.set(top, []);
        groups.get(top).push(node);
      }
      list.replaceChildren(...[...groups].map(([sectionId, items]) => h("div", {},
        h("div", { class: "node-group" }, withMath(sections.get(sectionId)?.title ?? "")),
        h("div", { class: "node-list" }, items.map((n) => this.nodeItem(n))))));
    };
    search.addEventListener("input", () => { this.search = search.value; draw(); });
    const filters = h("div", { class: "filters" }, search, kinds.map((kind) => {
      const chip = h("button", { class: "kind-filter", type: "button", dataset: { kind }, "aria-pressed": String(this.kindFilter.has(kind)) }, t(`kind.${kind}`));
      chip.addEventListener("click", () => {
        if (this.kindFilter.has(kind)) this.kindFilter.delete(kind); else this.kindFilter.add(kind);
        chip.setAttribute("aria-pressed", String(this.kindFilter.has(kind)));
        draw();
      });
      return chip;
    }));
    body.append(filters, list);
    draw();
  }

  topSection(sectionId, sections) {
    let id = sectionId;
    while (id) {
      const entry = sections.get(id);
      if (entry && entry.level <= 1) return id;
      const parent = this.find(id)?.parentElement?.closest("section[id]");
      id = parent?.id;
    }
    return sectionId;
  }

  renderDependencies(body) {
    const node = this.byId.get(this.selected);
    if (!node) {
      body.append(h("p", { class: "hint" }, t("panel.select")));
      return;
    }
    const element = this.find(node.id);
    const card = h("div", { class: "dep-card", dataset: { kind: node.kind } },
      h("div", { class: "heading" }, h("span", { class: "dot" }), node.label, node.name && h("span", { class: "muted" }, `— ${node.name}`)),
      element && h("div", { class: "statement" }, this.clean(element)),
      h("div", { class: "small" }, h("a", { href: `#${node.id}`, onclick: (e) => { e.preventDefault(); this.navigate(node.id); } }, t("panel.showInText"))),
    );
    setTimeout(() => typesetFragment(card), 0);
    const section = (title, items) => h("div", { class: "panel-section" }, h("h3", {}, title),
      items.length ? h("div", { class: "node-list" }, items) : h("p", { class: "hint" }, t("panel.none")));
    const nodesOf = (ids) => ids.filter((id) => this.byId.has(id)).map((id) => this.nodeItem(this.byId.get(id)));

    body.append(card,
      section(t("panel.uses", { n: node.uses.length }), nodesOf(node.uses)),
      section(t("panel.usedBy", { n: node.usedBy.length }), nodesOf(node.usedBy)));

    if (node.usesEquations.length) {
      body.append(h("div", { class: "panel-section" }, h("h3", {}, t("panel.equations")),
        h("p", { class: "small", style: "margin: 0 8px" }, node.usesEquations.map((eq, i) => [i ? ", " : "",
          h("a", { href: `#${eq}`, onclick: (e) => { e.preventDefault(); this.navigate(eq); } }, this.structure.equations[eq]?.label || eq)]))));
    }
    if (node.cites.length) {
      body.append(section(t("panel.cites", { n: node.cites.length }), node.cites.map((id) => {
        const entry = this.bib.get(id);
        return h("button", { class: "node-item", type: "button", title: entry?.text || "", onclick: () => this.navigate(id) },
          h("span", { class: "label" }, `[${entry?.label || "?"}]`), h("span", { class: "name" }, entry?.text || ""));
      })));
    }
    const ancestors = closure(this.byId, node.id, "uses");
    const descendants = closure(this.byId, node.id, "usedBy");
    const summary = (key, set) => set.size ? h("details", { class: "panel-section small" },
      h("summary", {}, t(key, { n: set.size })),
      h("div", { class: "node-list" }, nodesOf([...set].sort((a, b) => this.nodes.indexOf(this.byId.get(a)) - this.nodes.indexOf(this.byId.get(b)))))) : null;
    body.append(summary("panel.allDeps", ancestors) || "", summary("panel.allUsers", descendants) || "",
      h("p", { class: "hint" }, t("panel.depsNote")));
  }

  renderGraphTab(body) {
    const graph = renderGraph(this.nodes, {
      selected: this.selected, showIsolated: this.showIsolated,
      onSelect: (id) => this.select(id, { scroll: true }),
    });
    const isolated = h("input", { type: "checkbox", checked: this.showIsolated });
    isolated.addEventListener("change", () => { this.showIsolated = isolated.checked; this.renderPanel(); });
    const expand = h("button", { class: "button", type: "button", onclick: () => this.openGraphOverlay() }, t("graph.expand"));
    const kinds = [...new Set(this.nodes.map((n) => n.kind))];
    body.append(
      h("div", { class: "graph-tools" }, h("label", {}, isolated, t("graph.isolated")), expand),
      graph ? h("div", { class: "graph-wrap" }, graph) : h("p", { class: "hint" }, t("graph.empty")),
      h("div", { class: "legend" }, kinds.map((k) => h("span", { dataset: { kind: k } }, h("span", { class: "dot" }), t(`kind.${k}`)))),
      h("p", { class: "hint" }, t("graph.help")),
    );
  }

  openGraphOverlay() {
    const close = () => { overlay.remove(); this.overlay = null; document.removeEventListener("keydown", onKey); };
    const onKey = (event) => { if (event.key === "Escape") close(); };
    const draw = () => renderGraph(this.nodes, {
      selected: this.selected, showIsolated: this.showIsolated,
      onSelect: (id) => { this.select(id); wrap.replaceChildren(draw()); },
    }) || h("p", { class: "hint" }, t("graph.empty"));
    const wrap = h("div", { class: "graph-wrap" }, draw());
    const overlay = h("div", { class: "graph-overlay", role: "dialog", "aria-modal": "true" },
      h("header", {}, h("strong", {}, withMath(this.paper.title)), h("span", { style: "flex:1" }),
        h("button", { class: "button", type: "button", onclick: () => { close(); if (this.selected) this.navigate(this.selected, { remember: false }); } }, t("graph.close"))),
      wrap, h("p", { class: "hint", style: "margin: 0 16px 12px" }, t("graph.help")));
    document.body.append(overlay);
    document.addEventListener("keydown", onKey);
    this.overlay = { remove: close };
  }

  // ---------- Événements ----------

  bindEvents() {
    this.savePosition = debounce((anchor) => api.savePosition(this.paper.id, anchor).catch(() => {}), 1500);
    const onScroll = debounce(() => this.onScroll(), 120);
    this.scroller.addEventListener("scroll", () => { this.hidePreview(); onScroll(); }, { passive: true });

    this.scroller.addEventListener("click", (event) => {
      const link = event.target.closest('a[href^="#"]');
      if (!link || !this.content.contains(link)) return;
      event.preventDefault();
      this.hidePreview();
      this.navigate(link.getAttribute("href").slice(1));
    });

    const hoverable = 'a[href^="#"], .ltx_note_mark';
    this.scroller.addEventListener("mouseover", (event) => {
      const link = event.target.closest(hoverable);
      if (!link || !this.content.contains(link)) return;
      clearTimeout(this.hideTimer);
      clearTimeout(this.showTimer);
      this.showTimer = setTimeout(() => this.showPreview(link), 220);
    });
    this.scroller.addEventListener("mouseout", (event) => {
      if (event.target.closest(hoverable)) {
        clearTimeout(this.showTimer);
        this.scheduleHide();
      }
    });

    const onKey = (event) => {
      if (event.target.matches("input, textarea")) return;
      if (event.key === "Escape") { this.hidePreview(); this.closeDrawers(); }
    };
    const layout = () => (innerWidth >= WIDE ? "wide" : innerWidth >= MEDIUM ? "medium" : "narrow");
    let currentLayout = layout();
    const onResize = debounce(() => {
      this.anchors = null;
      if (layout() === currentLayout) return;
      currentLayout = layout();
      // Changement de format : volets remis dans leur état par défaut pour cette largeur
      this.setOpen("toc", currentLayout === "wide" && settings.tocOpen);
      this.setOpen("panel", currentLayout !== "narrow" && settings.panelOpen);
    }, 200);
    document.addEventListener("keydown", onKey);
    addEventListener("resize", onResize);
    const unsubscribe = onSettingsChange(() => {
      this.anchors = null;
      this.article.dataset.marks = settings.marginMarks ? "on" : "off";
    });
    this.cleanups.push(() => document.removeEventListener("keydown", onKey), () => removeEventListener("resize", onResize), unsubscribe);
  }

  destroy() {
    this.destroyed = true;
    this.hidePreview();
    this.overlay?.remove();
    this.cleanups.forEach((fn) => fn());
  }
}

