// Lecteur d'article : texte, sommaire, aperçus des renvois, preuves repliables, panneau des énoncés.

import { api } from "./api.js";
import { closure, renderGraph } from "./graph.js";
import { Annotator, findAll, noteEditor } from "./annotations.js";
import { Assistant } from "./ai.js";
import { alignNumberColumns, amsBibliography, fixMathML, splitLongInlineMath, markItalicStatements, moveTitleNotes, normalizeFontScale, placeEquationNumbers, relabelLists } from "./amsfix.js";
import { currentModel } from "./aiproviders.js";
import { layoutFigures, openFigure, prepareFigures } from "./figures.js";
import { findBar, setFindHandler } from "./find.js";
import { defineMacros, statementElement, statementsContainer } from "./texview.js";
import { typesetFragment, typesetMath, typesetTex } from "./math.js";

const typesetTexSafe = (element) => typesetTex([element]).catch(() => {});
import { t } from "./i18n.js";
import { DEFAULT_FONT_SIZE, onSettingsChange, saveSettings, settings } from "./settings.js";
import { debounce, formatDate, h, icons, toast, toggleIcon, withMath } from "./util.js";

const NEEDS_PROOF = new Set(["theorem", "proposition", "lemma", "corollary", "claim"]);
const WIDE = 1280;
// Blocs qu'on peut désigner comme passage de preuve (mêmes classes que PASSAGE_BLOCKS dans app/structure.py)
const PICKABLE = [".ltx_para", ".ltx_theorem", ".ltx_proof", ".ltx_equation", ".ltx_equationgroup", ".ltx_figure", ".ltx_table"]
  .map((c) => `${c}[id]`).join(", ");
const MEDIUM = 900;

// `at` : énoncé à afficher à l'ouverture (« #/paper/<id>/<énoncé> », renvoi depuis un autre article)
export async function renderReader(view, paperId, at, isCurrent = () => true) {
  if (typeof at === "function") [at, isCurrent] = [undefined, at];
  view.replaceChildren(h("div", { class: "library" }, h("div", { class: "busy" }, h("span", { class: "spinner" }))));
  const { paper, structure } = await api.paper(paperId);
  const html = paper.source === "none" || paper.source === "tex" ? "" : await api.content(paperId);
  if (!isCurrent()) return () => {}; // page déjà quittée pendant le chargement : rien n'est affiché
  const reader = new Reader(view, paper, structure || { toc: [], nodes: [], bibliography: [], hiddenText: 0 }, html, at);
  return () => reader.destroy();
}

// Énoncé visé par la note d'une citation : « [12, Theorem 4.3] », « [12, Thm. 4.3(2)] », « [12, Lemma 2.1] »
const CITE_KINDS = [
  [/^(?:theorems?|thm|th[ée]or[èe]mes?)$/i, "theorem"], [/^(?:lemmas?|lem|lemmes?|lemmata)$/i, "lemma"],
  [/^(?:propositions?|prop)$/i, "proposition"], [/^(?:corollar(?:y|ies)|cor|corollaires?)$/i, "corollary"],
  [/^(?:definitions?|def|d[ée]finitions?)$/i, "definition"], [/^(?:remarks?|rem|remarques?)$/i, "remark"],
  [/^(?:examples?|ex|exemples?)$/i, "example"], [/^(?:claims?)$/i, "claim"], [/^(?:conjectures?|conj)$/i, "conjecture"],
];

function citedStatement(link, nodes) {
  let note = "";
  for (let next = link.nextSibling; next && !(next.nodeType === 1 && next.matches("a")); next = next.nextSibling) {
    note += next.textContent;
    if (note.includes("]") || note.includes(";")) break;
  }
  const match = note.match(/^\s*,?\s*([A-Za-zÀ-ÿ]+)\.?\s*(\d+(?:\.\d+)*)/);
  if (!match) return null;
  const kind = CITE_KINDS.find(([pattern]) => pattern.test(match[1]))?.[1];
  const number = match[2];
  const numbered = nodes.filter((n) => (n.label.match(/(\d+(?:\.\d+)*)\s*$/) || [])[1] === number);
  if (kind) return numbered.find((n) => n.kind === kind) || null;
  return numbered.length === 1 ? numbered[0] : null; // « [12, 4.3] » : seulement si le numéro est sans ambiguïté
}

class Reader {
  constructor(view, paper, structure, html, at = null) {
    this.paper = paper;
    this.startAt = at;
    this.texOnly = paper.source === "tex"; // pas de version HTML : toujours dans le PDF
    this.pdfMode = this.texOnly;
    this.structure = structure;
    this.nodes = structure.nodes;
    this.byId = new Map(this.nodes.map((n) => [n.id, n]));
    this.bib = new Map(structure.bibliography.map((b) => [b.id, b]));
    this.selected = null;
    this.tab = "statements";
    this.showIsolated = false;
    this.showRemarks = false;
    this.kindFilter = new Set();
    this.search = "";
    this.backStack = [];
    this.anchors = null;
    this.cleanups = [];

    this.build(view, html);
    if (!this.texOnly && this.viewSwitch && settings.readerView === "pdf") this.setView("pdf", { save: false });
    this.enhanceContent();
    this.renderToc();
    this.renderPanel();
    this.bindEvents();
    this.annotator = new Annotator({
      paperId: paper.id, content: this.content, nodes: this.nodes,
      navigate: (target) => (typeof target === "string" ? this.navigate(target) : this.navigateElement(target)),
      // une note enregistrée ne redessine pas le panneau (l'éditeur en cours de saisie resterait perdu) :
      // seules les pastilles d'état sont mises à jour, sauf dans l'onglet Notes qui résume tout
      onChange: () => {
        if (this.destroyed) return;
        if (this.tab === "notes") this.renderPanel();
        else this.refreshNodeItems();
      },
      aiActions: (id) => this.assistant.actionsFor(id, { compact: true }),
      onAskAI: (quote) => this.assistant.setPassage(quote),
    });
    this.assistant = new Assistant({
      paper, structure: this.structure, find: (id) => this.find(id), annotator: this.annotator,
      navigate: (id) => this.navigate(id),
      onChange: (options) => this.onAssistantChange(options),
      equationIds: () => [...this.content.querySelectorAll(".ltx_tag_equation")].map((tag) => {
        const row = tag.closest("tr[id], table[id], .ltx_equation[id]");
        return row ? [tag.textContent.trim(), row.id] : null;
      }).filter(Boolean),
    });
    this.finishLayout();
  }

  // Formules en MathJax avant d'afficher le texte (moins d'une seconde), puis reprise de la lecture
  async finishLayout() {
    if (settings.mathRenderer !== "native" && this.content.querySelector("math")) {
      this.article.classList.add("typesetting");
      const note = h("div", { class: "typesetting-note busy" }, h("span", { class: "spinner" }), t("reader.typesetting"));
      this.scroller.prepend(note);
      await new Promise((resolve) => setTimeout(resolve, 30)); // laisse s'afficher l'indicateur
      try {
        await typesetMath(this.content, { isCancelled: () => this.destroyed });
      } catch {
        // MathJax indisponible : le MathML du navigateur reste affiché
      }
      note.remove();
      this.article.classList.remove("typesetting");
    }
    if (this.destroyed) return;
    layoutFigures(this.figures);
    this.markWideMath();
    await Promise.all([this.annotator.load(), this.assistant.load()]);
    if (this.destroyed) return;
    this.annotator.apply();
    this.renderPanel(); // avec les états de lecture, chargés seulement maintenant
    this.anchors = null;
    if (this.texOnly) {
      await defineMacros(this.structure.macros);
      await typesetTexSafe(this.content);
      if (this.destroyed) return;
      if (this.startAt) this.showInPdf(this.startAt);
      this.loadCrossLinks();
      return;
    }
    if (this.pdfMode) {
      if (this.startAt) this.showInPdf(this.startAt);
    } else if (this.startAt && this.find(this.startAt)) {
      this.reveal(this.find(this.startAt));
      requestAnimationFrame(() => this.navigate(this.startAt));
    } else {
      this.restorePosition();
    }
    this.loadCrossLinks();
  }

  // ---------- Lecture en HTML ou dans le PDF ----------

  // Page du PDF d'un élément : énoncé, section, preuve (page de son énoncé), sinon section qui le contient
  pageFor(id) {
    if (this.texOnly) {
      const item = this.byId.get(id) || this.structure.toc.find((s) => s.id === id)
        || (this.structure.proofBlocks || []).find((p) => p.id === id);
      return item?.page || null;
    }
    const pages = this.pages || {};
    if (pages[id]) return pages[id];
    const owner = this.nodes.find((n) => n.proofs.includes(id));
    if (owner && pages[owner.id]) return pages[owner.id];
    for (let element = this.find(id); element; element = element.parentElement?.closest("[id]")) {
      const statement = element.closest(".ltx_theorem[id]");
      if (statement && pages[statement.id]) return pages[statement.id];
      if (pages[element.id]) return pages[element.id];
    }
    return null;
  }

  async setView(view, { save = true } = {}) {
    if (this.texOnly || (view === "pdf") === this.pdfMode) return;
    if (save) saveSettings({ readerView: view });
    this.viewSwitch?.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
    if (view === "pdf") {
      // page où l'on en était dans le HTML
      this.htmlScroll = scrollY;
      // à l'ouverture : dernière position de lecture enregistrée ; sinon, l'endroit lu dans le HTML
      const anchorId = save ? this.currentAnchor()?.id : this.paper.position;
      this.pdfMode = true;
      this.root.dataset.view = "pdf";
      if (!this.pdfView) {
        this.pdfUrl = `/api/papers/${encodeURIComponent(this.paper.id)}/pdf`;
        this.pdfFrame = h("iframe", { class: "pdf-frame", title: this.paper.title });
        this.pdfView = h("div", { class: "paper pdf-mode pdf-view" }, this.pdfFrame);
        this.scroller.append(this.pdfView);
      }
      scrollTo({ top: 0, behavior: "instant" });
      if (!this.pages) {
        this.pdfFrame.src = `${this.pdfUrl}#page=1`;
        try { this.pages = await api.pages(this.paper.id); } catch { this.pages = {}; }
        if (this.destroyed || !this.pdfMode) return;
      }
      const page = anchorId ? this.pageFor(anchorId) : null;
      this.pdfFrame.src = `${this.pdfUrl}?page=${page || 1}#page=${page || 1}`;
    } else {
      this.pdfMode = false;
      delete this.root.dataset.view;
      this.anchors = null;
      if (this.htmlScroll !== undefined) scrollTo({ top: this.htmlScroll, behavior: "instant" });
      else this.restorePosition();
    }
  }

  // Article lu dans le PDF : page de l'énoncé, de la section ou de la preuve, et énoncé choisi dans le panneau
  showInPdf(id) {
    const node = this.byId.get(id);
    const page = this.pageFor(id);
    if (page && this.pdfFrame) this.pdfFrame.src = `${this.pdfUrl}?page=${page}#page=${page}`; // nouvelle adresse : le PDF va à la page
    if (node) this.select(id, { openPanel: true });
  }

  // ---------- Renvois vers les autres articles lus ici ----------

  // « [12, Theorem 4.3] » : la référence [12] est un article de la bibliothèque ; son « Theorem 4.3 »
  // s'affiche au survol et s'ouvre d'un clic. La référence elle-même, dans la bibliographie, mène à l'article.
  async loadCrossLinks() {
    try { this.crosslinks = await api.crosslinks(this.paper.id); } catch { return; }
    if (this.destroyed || !Object.keys(this.crosslinks).length) return;
    for (const link of this.content.querySelectorAll('a.ltx_ref[href^="#bib"]')) {
      const entry = this.crosslinks[link.getAttribute("href").slice(1)];
      if (!entry) continue;
      link.classList.add("cross-cite");
      link.dataset.crossPaper = entry.paper;
      const node = citedStatement(link, entry.nodes);
      if (node) link.dataset.crossNode = node.id;
    }
    for (const [bibId, entry] of Object.entries(this.crosslinks)) {
      const item = this.find(bibId);
      if (!item || item.querySelector(".cross-badge")) continue;
      item.append(h("a", { class: "cross-badge", href: `#/paper/${encodeURIComponent(entry.paper)}`, title: entry.title }, t("reader.crossOpen")));
    }
  }

  // ---------- Construction ----------

  build(view, html) {
    // fond de page = fond de l'article : une zone pas encore dessinée pendant un défilement rapide
    // apparaît blanche (comme l'article) et non grise
    document.body.classList.add("reading");
    const wide = innerWidth >= WIDE;
    const medium = innerWidth >= MEDIUM;
    this.root = h("div", {
      class: `reader${this.paper.source === "tex" ? " tex-only" : ""}`, // sans version HTML : taille du texte sans objet
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
    this.foldButton = button(icons.fold, "reader.foldAll", () => this.foldAll(), { class: "wide-only html-only" });
    const link = (icon, title, href) => h("a", { class: "icon-button", href, target: "_blank", rel: "noopener noreferrer", title, "aria-label": title, html: icon });

    const toolbar = this.toolbar = h("div", { class: "reader-toolbar" },
      this.tocButton,
      h("a", { class: "icon-button", href: "#/", title: t("reader.back"), "aria-label": t("reader.back"), html: icons.back }),
      h("div", { class: "title", title: paper.title }, withMath(paper.title)),
      button(icons.minus, "reader.smaller", () => this.changeFont(-1), { class: "wide-only html-only" }),
      this.zoomButton = h("button", { class: "zoom-reset wide-only html-only", type: "button", onclick: () => { if (settings.fontSize !== DEFAULT_FONT_SIZE) saveSettings({ fontSize: DEFAULT_FONT_SIZE }); } }),
      button(icons.plus, "reader.larger", () => this.changeFont(1), { class: "wide-only html-only" }),
      this.foldButton,
      // choix HTML / PDF après les réglages du texte HTML (masqués dans le PDF) : il ne bouge pas quand on change
      this.viewSwitch = paper.source !== "tex" && paper.source !== "none" && h("div", { class: "view-switch", role: "group", "aria-label": t("reader.view") },
        [["html", "HTML"], ["pdf", "PDF"]].map(([view, label]) => h("button", { type: "button", dataset: { view }, "aria-pressed": String(view === "html"),
          title: t(`reader.view.${view}`), onclick: () => this.setView(view) }, label))),
      // pas de version HTML : à la place du choix HTML / PDF, une petite mention (explication au survol)
      paper.source === "tex" && h("span", { class: "no-html-badge", title: t("reader.pdfMode") }, "No HTML"),
      h("span", { class: "sep wide-only" }),
      this.libraryToggle(),
      link(icons.external, "arXiv", `https://arxiv.org/abs/${paper.arxiv_id}v${paper.version}`),

      this.panelButton,
    );

    this.toc = h("aside", { class: "toc", "aria-label": t("reader.contents") });
    this.panel = h("aside", { class: "panel", "aria-label": t("reader.panel") });
    this.content = h("div", { class: "paper-content" });
    this.article = h("article", { class: "paper", dataset: { marks: settings.marginMarks ? "on" : "off" } });
    this.backButton = h("button", { class: "back-button", type: "button", onclick: () => this.goBack() }, h("span", { class: "reason-icon", html: icons.undo }), t("reader.returnTo"));
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
      this.article.append(h("div", { class: "notice" }, h("span", { class: "reason-icon", html: icons.warning }), t("reader.hiddenText", { n: this.structure.hiddenText }), toggle));
    }

    if (paper.source === "tex") {
      // Pas de version HTML : lecture dans le PDF ; énoncés (rendus depuis la source TeX) pour le panneau
      this.pdfUrl = `/api/papers/${encodeURIComponent(paper.id)}/pdf`;
      this.pdfFrame = h("iframe", { class: "pdf-frame", title: paper.title, src: `${this.pdfUrl}#page=1` });
      this.article.classList.add("pdf-mode");
      this.article.append(this.pdfFrame);
      this.content = statementsContainer(this.structure);
      this.article.append(this.content);
    } else if (paper.source === "none") {
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

    this.root.append(toolbar, this.toc, this.paneResizer("toc"), this.scroller, this.panel, this.paneResizer("panel"),
      h("div", { class: "scrim", onclick: () => this.closeDrawers() }));
    // Police « comme l'article » : celle du paquet de police de la source TeX
    if (settings.panelWidth) this.root.style.setProperty("--panel-w", `${settings.panelWidth}px`);
    if (settings.tocWidth) this.root.style.setProperty("--toc-w", `${settings.tocWidth}px`);
    if (settings.paperFont === "auto") this.root.dataset.paperFont = this.structure.tex?.font || "lm";
    view.replaceChildren(this.root);
  }

  // Poignées de largeur : bord droit du sommaire, bord gauche du panneau. Glisser, flèches du clavier
  // (Maj : pas plus grand), double-clic : largeur par défaut.
  // Pendant le glissement, la grille ne bouge pas : le volet s'étend par-dessus l'article (marge négative),
  // ce qui ne recompose que le volet (moins d'une milliseconde, contre plus de 100 ms pour l'article
  // entier). L'article n'est recomposé qu'une fois, au relâchement, à la même position de lecture.
  paneResizer(which) {
    const isPanel = which === "panel";
    const pane = isPanel ? this.panel : this.toc;
    const variable = isPanel ? "--panel-w" : "--toc-w";
    const key = isPanel ? "panelWidth" : "tocWidth";
    const margin = isPanel ? "marginLeft" : "marginRight";
    const handle = h("div", { class: `pane-resizer ${which}-resizer`, role: "separator", tabindex: 0, "aria-orientation": "vertical",
      "aria-label": t(`reader.resize.${which}`), title: t(`reader.resizeHint.${which}`) });
    const limits = () => {
      const otherOpen = isPanel ? this.root.dataset.toc === "open" && innerWidth >= WIDE : this.root.dataset.panel === "open" && innerWidth >= MEDIUM;
      const other = otherOpen ? (isPanel ? this.toc : this.panel).getBoundingClientRect().width : 0;
      const [min, max] = isPanel ? [260, 900] : [160, 520];
      return [min, Math.max(min, Math.min(max, innerWidth - other - 420))]; // l'article garde au moins 420 px
    };
    const clamp = (width) => {
      const [min, max] = limits();
      return Math.round(Math.min(max, Math.max(min, width)));
    };
    const commit = (width) => {
      this.keepReadingPosition(() => {
        pane.style[margin] = "";
        handle.style[margin] = "";
        if (width === null) this.root.style.removeProperty(variable);
        else this.root.style.setProperty(variable, `${width}px`);
        this.root.classList.remove("resizing");
      });
      handle.setAttribute("aria-valuenow", String(Math.round(pane.getBoundingClientRect().width)));
      saveSettings({ [key]: width });
    };

    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      try { handle.setPointerCapture(event.pointerId); } catch { /* pointeur déjà relâché */ }
      const box = pane.getBoundingClientRect();
      const start = box.width;
      const startX = event.clientX;
      let width = null; // rien n'est appliqué pour un simple clic
      let x = startX;
      let frame = 0;
      const draw = () => {
        frame = 0;
        width = clamp(isPanel ? box.right - x : x - box.left);
        const shift = start - width; // négatif : le volet s'étend par-dessus l'article
        pane.style[margin] = `${shift}px`;
        handle.style[margin] = `${shift - 5}px`;
      };
      const move = (e) => {
        x = e.clientX;
        if (width === null && Math.abs(x - startX) <= 2) return;
        if (width === null) this.root.classList.add("resizing");
        if (!frame) frame = requestAnimationFrame(draw); // au plus un calcul par image
      };
      const up = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.removeEventListener("pointercancel", up);
        if (frame) { cancelAnimationFrame(frame); draw(); }
        if (width !== null) commit(width);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
      handle.addEventListener("pointercancel", up);
    });
    handle.addEventListener("keydown", (event) => {
      const step = event.shiftKey ? 80 : 24;
      const grow = isPanel ? { ArrowLeft: step, ArrowRight: -step } : { ArrowRight: step, ArrowLeft: -step };
      if (!grow[event.key]) return;
      event.preventDefault();
      commit(clamp(pane.getBoundingClientRect().width + grow[event.key]));
    });
    handle.addEventListener("dblclick", () => commit(null));
    return handle;
  }

  // Signet : l'article lu depuis le feed n'est dans la bibliothèque que si on l'y ajoute
  libraryToggle() {
    const paper = this.paper;
    const label = () => t(paper.in_library ? "feed.removeFromLibrary" : "feed.addToLibrary");
    const button = toggleIcon(icons.bookmarkOff, icons.bookmarkOn, { pressed: Boolean(paper.in_library), label: label(), className: "bookmark" });
    button.addEventListener("click", async () => {
      try {
        const updated = await api.setInLibrary(paper.id, !paper.in_library);
        paper.in_library = updated.in_library;
        button.setAttribute("aria-pressed", String(Boolean(paper.in_library)));
        button.title = label();
        button.setAttribute("aria-label", label());
        toast(t(paper.in_library ? "feed.added" : "feed.removed"));
      } catch (error) {
        toast(error.message, { error: true });
      }
    });
    return button;
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
    this.figures = prepareFigures(this.content);
    // Figures cliquables (et au clavier) : affichage en grand par-dessus l'article
    this.content.querySelectorAll(".figure-card, img.ltx_graphics").forEach((figure) => {
      figure.tabIndex = 0;
      figure.title = t("reader.figureZoom");
      if (figure.querySelector("a")) return; // étiquettes avec renvois : pas de bouton qui contienne des liens
      figure.setAttribute("role", "button");
      const alt = figure.getAttribute("alt")?.trim();
      figure.setAttribute("aria-label", alt ? `${alt} — ${t("reader.figureZoom")}` : t("reader.figureZoom"));
    });

    normalizeFontScale(this.content);
    fixMathML(this.content, this.structure.tex?.stretchyMacros || []);
    splitLongInlineMath(this.content);

    // Ce que la version HTML perd par rapport au PDF (lu dans la source TeX à l'import)
    const tex = this.structure.tex;
    if (tex) {
      placeEquationNumbers(this.content, tex.eqno);
      relabelLists(this.content, tex.enumerate);
    }
    alignNumberColumns(this.content); // après le placement des numéros (source TeX ou non)
    markItalicStatements(this.content);
    amsBibliography(this.content);

    // Ponctuation collée à la formule qui la précède (pas de « , » seule en début de ligne)
    this.content.querySelectorAll("math:not(.math-group math), .math-group").forEach((math) => {
      const next = math.nextSibling;
      if (!next || next.nodeType !== Node.TEXT_NODE || !/^[.,;:)\]]/.test(next.data)) return;
      const length = next.data.match(/^[.,;:)\]]+/)[0].length;
      next.splitText(length); // la suite du texte reste hors du groupe
      const wrap = h("span", { class: "nobr" });
      math.replaceWith(wrap);
      wrap.append(math, next);
    });

    // Comme dans amsart, la date rejoint les mots-clés et la classification (note de première page)
    const dates = this.content.querySelector(".ltx_dates");
    const frontNotes = this.content.querySelectorAll(".ltx_keywords, .ltx_classification");
    if (dates && frontNotes.length) frontNotes[frontNotes.length - 1].after(dates);
    moveTitleNotes(this.content);
    const front = this.content.querySelectorAll(".ltx_abstract, .ltx_keywords, .ltx_classification, .ltx_dates, .ltx_titlenotes");
    if (front.length) [...front].sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1)).at(-1).classList.add("front-last");

    // Preuves repliables ; le ∎ de LaTeXML devient le □ d'amsthm, aligné à droite
    this.content.querySelectorAll(".ltx_proof").forEach((proof) => {
      const detected = proof.classList.contains("arx-detected-proof");
      this.markQed(proof);
      const title = proof.querySelector(".ltx_title_proof")?.textContent.trim() || "Proof.";
      const state = h("span", { class: "state" });
      const toggle = h("button", { class: "proof-toggle", type: "button", "aria-expanded": "true", title: detected ? t("reader.proofDetected") : t("reader.proof") },
        h("span", { class: "caret", html: icons.chevronRight }), h("span", { class: "text" }, title), state);
      toggle.addEventListener("click", () => this.setProof(proof, !proof.classList.contains("collapsed")));
      proof.prepend(toggle);
      this.setProof(proof, settings.proofsCollapsed, { update: false });
    });
    this.updateFoldButton();

    this.renderProofPointers();

    // Titres d'énoncés cliquables → panneau des dépendances
    this.content.querySelectorAll(".ltx_theorem").forEach((theorem) => {
      const title = theorem.querySelector(".ltx_title_theorem, .ltx_title");
      if (!title || !this.byId.has(theorem.id)) return;
      title.title = t("tabs.dependencies");
      title.tabIndex = 0;
      title.setAttribute("role", "button");
      const open = () => this.select(theorem.id, { tab: "dependencies", openPanel: true });
      title.addEventListener("click", open);
      title.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
    });
  }

  // Preuve placée ailleurs (section à part, passage indiqué) : renvoi discret sous l'énoncé
  renderProofPointers() {
    this.content.querySelectorAll(".proof-pointer").forEach((el) => el.remove());
    for (const node of this.nodes) {
      const theorem = this.find(node.id);
      if (!theorem) continue;
      if (node.proofSection) {
        const section = this.find(node.proofs[0]);
        if (section) theorem.append(h("a", { class: "proof-pointer", href: `#${section.id}` }, t("reader.proofIn", { where: this.sectionName(section) })));
      } else if (node.proofPassage) {
        const start = this.find(node.proofPassage.start);
        const next = theorem.nextElementSibling;
        if (start && start !== next && !next?.contains(start)) {
          theorem.append(h("a", { class: "proof-pointer", href: `#${start.id}` }, t("reader.proofPassage")));
        }
      }
    }
  }

  // Structure renvoyée par le serveur après une indication de preuve
  setStructure(structure) {
    this.structure = structure;
    this.nodes = structure.nodes;
    this.byId = new Map(this.nodes.map((n) => [n.id, n]));
    if (this.annotator) {
      this.annotator.nodes = this.nodes;
      this.annotator.byId = this.byId;
    }
    this.assistant?.setStructure(structure);
    this.renderProofPointers();
    this.anchors = null;
    this.renderPanel();
  }

  hasProof(node) {
    return node.proofs.length > 0 || Boolean(node.proofPassage);
  }

  // « § 4.2 » : numéro de la section (ou de la plus proche section numérotée qui la contient)
  sectionName(section) {
    const titles = new Map(this.structure.toc.map((entry) => [entry.id, entry.title]));
    for (let el = section; el; el = el.parentElement?.closest("section[id]")) {
      const number = titles.get(el.id)?.match(/^([0-9A-Z]+(?:\.[0-9]+)*)\.?\s/)?.[1];
      if (number) return `§ ${number}`;
    }
    return titles.get(section.id) || "";
  }

  // Où se trouve la preuve d'un énoncé : liens pour le panneau
  proofInfo(node) {
    const link = (id, text) => h("a", { href: `#${id}`, onclick: (e) => { e.preventDefault(); this.navigate(id); } }, text);
    const mine = node.manual ? [" ", h("span", { class: "muted" }, t("panel.manualNote"))] : [];
    if (node.proofs.length) {
      return [t("panel.proofLabel"), " ", node.proofs.map((id, i) => {
        const element = this.find(id);
        // titre de section tel quel ($…$ compris), mis en forme ensuite par MathJax (proofLine)
        const text = element?.matches("section") ? this.structure.toc.find((s) => s.id === id)?.title || this.sectionName(element) : t("panel.proofHere");
        return [i ? ", " : "", link(id, text)];
      }), mine];
    }
    if (node.proofPassage) return [t("panel.proofLabel"), " ", link(node.proofPassage.start, t("panel.proofPassage")), mine];
    if (node.manual === "immediate") return [t("panel.immediateInfo"), mine];
    if (node.manual === "admitted") return [t("panel.admittedInfo"), mine];
    if (node.justifiedBy) {
      const element = this.find(node.justifiedBy);
      const before = element && this.find(node.id) && element.compareDocumentPosition(this.find(node.id)) & Node.DOCUMENT_POSITION_FOLLOWING;
      return [t("panel.justifiedLabel"), " ", link(node.justifiedBy, t(before ? "panel.sentenceBefore" : "panel.sentenceAfter"))];
    }
    if (node.cited?.length) {
      return [t("panel.citedLabel"), " ", node.cited.map((id, i) => [i ? ", " : "", link(id, `[${this.bib.get(id)?.label || "?"}]`)])];
    }
    return NEEDS_PROOF.has(node.kind) ? t("panel.proofNotFound") : null;
  }

  // Ligne « Preuve : … » du panneau, avec le bouton pour indiquer (ou corriger) l'emplacement de la preuve
  proofLine(node) {
    const line = h("div", { class: "small proof-info" });
    const draw = () => {
      const info = this.proofInfo(node);
      // article lu dans le PDF : pas de texte où désigner une preuve à la main
      const editable = !this.texOnly && (NEEDS_PROOF.has(node.kind) || node.manual || this.hasProof(node));
      const button = editable && h("button", { class: "link-button", type: "button", onclick: () => line.replaceChildren(this.proofEditor(node, draw)) },
        t(node.manual ? "panel.changeProof" : "panel.setProof"));
      line.replaceChildren(h("span", {}, info), button ? " " : "", button || "");
      if (line.textContent.includes("$")) typesetTex([line]).catch(() => {});
    };
    draw();
    return line;
  }

  proofEditor(node, close) {
    const save = async (body) => {
      try {
        this.setStructure(body ? await api.setProof(this.paper.id, node.id, body) : await api.clearProof(this.paper.id, node.id));
        toast(t("pick.saved", { label: node.label }));
      } catch (error) {
        toast(t("error.generic", { message: error.message }), { error: true });
      }
    };
    const sections = this.structure.toc.filter((s) => s.id !== "bib" && !/bibliograph/i.test(s.id));
    const current = node.manual === "section" ? node.proofs[0] : "";
    const select = h("select", { class: "ai-model proof-select", "aria-label": t("panel.chooseSection") },
      h("option", { value: "" }, t("panel.chooseSection")),
      sections.map((s) => h("option", { value: s.id, selected: s.id === current },
        `${"\u2003".repeat(Math.max(0, s.level - 1))}${s.title.replace(/\$/g, "")}`)));
    const button = (label, onclick, extra = "") => h("button", { class: `button small-button ${extra}`, type: "button", onclick }, label);
    return h("div", { class: "proof-editor" },
      h("p", { class: "proof-editor-title" }, t("panel.proofWhere", { label: node.label })),
      h("div", { class: "proof-editor-row" }, select,
        button(t("panel.validate"), () => select.value ? save({ mode: "section", ref: select.value }) : select.focus())),
      h("div", { class: "proof-editor-row" }, button(t("panel.pickPassage"), () => this.startPick(node))),
      h("div", { class: "proof-editor-row" },
        button(t("panel.immediate"), () => save({ mode: "immediate" })),
        button(t("panel.admitted"), () => save({ mode: "admitted" }))),
      h("div", { class: "proof-editor-row" },
        node.manual && button(t("panel.resetProof"), () => save(null)),
        button(t("panel.cancel"), close)));
  }

  // ---------- Choix d'un passage de preuve dans le texte ----------

  startPick(node) {
    this.stopPick();
    this.closeDrawers();
    const pick = this.picking = { node, start: null, hover: null };
    this.root.classList.add("picking");
    const message = h("span", {}, t("pick.start", { label: node.label }));
    const finish = h("button", { class: "button small-button", type: "button", disabled: true, onclick: () => this.finishPick(pick.start, pick.start) }, t("pick.finish"));
    const banner = h("div", { class: "pick-banner", role: "status" }, message, finish,
      h("button", { class: "button small-button", type: "button", onclick: () => this.stopPick() }, t("pick.cancel")));
    document.body.append(banner);
    const candidate = (target) => {
      const block = target.closest?.(PICKABLE);
      return block && this.content.contains(block) ? block : null;
    };
    const onOver = (event) => {
      const block = candidate(event.target);
      if (block === pick.hover) return;
      pick.hover?.classList.remove("pick-hover");
      pick.hover = block;
      block?.classList.add("pick-hover");
    };
    const onClick = (event) => {
      const block = candidate(event.target);
      if (!block) return;
      event.preventDefault();
      event.stopPropagation();
      if (!pick.start) {
        pick.start = block;
        block.classList.add("pick-start");
        message.textContent = t("pick.end");
        finish.disabled = false;
      } else {
        this.finishPick(pick.start, block);
      }
    };
    const onKey = (event) => { if (event.key === "Escape") this.stopPick(); };
    this.content.addEventListener("mouseover", onOver);
    this.content.addEventListener("click", onClick, true);
    document.addEventListener("keydown", onKey);
    pick.cleanup = () => {
      this.content.removeEventListener("mouseover", onOver);
      this.content.removeEventListener("click", onClick, true);
      document.removeEventListener("keydown", onKey);
      banner.remove();
      this.content.querySelectorAll(".pick-hover, .pick-start").forEach((el) => el.classList.remove("pick-hover", "pick-start"));
      this.root.classList.remove("picking");
    };
  }

  stopPick() {
    this.picking?.cleanup();
    this.picking = null;
  }

  async finishPick(start, end) {
    const node = this.picking.node;
    this.stopPick();
    if (end !== start && end.compareDocumentPosition(start) & Node.DOCUMENT_POSITION_FOLLOWING) [start, end] = [end, start];
    try {
      this.setStructure(await api.setProof(this.paper.id, node.id, { mode: "passage", start: start.id, end: end.id }));
      toast(t("pick.saved", { label: node.label }));
    } catch (error) {
      toast(t("error.generic", { message: error.message }), { error: true });
    }
    this.select(node.id, { tab: "dependencies", openPanel: true });
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

  // Formule en ligne plus large que la colonne (téléphone) : défilement dans la formule
  // écritures puis lectures groupées : une seule mise en page, pas une par formule
  markWideMath() {
    const maths = [...this.content.querySelectorAll("mjx-container:not([display])")];
    maths.forEach((math) => math.classList.remove("wide-inline"));
    const width = this.content.clientWidth;
    maths.filter((math) => math.getBoundingClientRect().width > width).forEach((math) => math.classList.add("wide-inline"));
  }

  // Changement de mise en page (volet, police, largeur) : figures et formules recalées, même passage à l'écran
  keepReadingPosition(change = () => {}) {
    const anchor = this.currentAnchor();
    const top = anchor?.getBoundingClientRect().top;
    change();
    this.anchors = null;
    layoutFigures(this.figures);
    this.markWideMath();
    if (anchor) scrollBy({ top: anchor.getBoundingClientRect().top - top, behavior: "instant" });
  }

  setProof(proof, collapsed, { update = true } = {}) {
    proof.classList.toggle("collapsed", collapsed);
    const toggle = proof.querySelector(":scope > .proof-toggle");
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.querySelector(".state").textContent = collapsed ? `(${t("reader.showProof")})` : "";
    toggle.setAttribute("aria-label", `${t("reader.proof")} — ${collapsed ? t("reader.showProof") : t("reader.hideProof")}`);
    this.anchors = null;
    if (update) this.updateFoldButton();
  }

  foldAll() {
    const proofs = [...this.content.querySelectorAll(".ltx_proof")];
    const collapse = proofs.some((p) => !p.classList.contains("collapsed"));
    proofs.forEach((p) => this.setProof(p, collapse, { update: false }));
    this.updateFoldButton();
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

  // Zoom du texte par rapport à la taille par défaut ; un clic y revient
  updateZoomButton() {
    const percent = Math.round((100 * settings.fontSize) / DEFAULT_FONT_SIZE);
    const atDefault = settings.fontSize === DEFAULT_FONT_SIZE;
    this.zoomButton.textContent = `${percent} %`;
    // pas « disabled » : le focus clavier resterait perdu juste après le clic
    this.zoomButton.setAttribute("aria-disabled", String(atDefault));
    const label = atDefault ? t("reader.zoomDefault") : t("reader.zoomReset", { n: percent });
    this.zoomButton.title = label;
    this.zoomButton.setAttribute("aria-label", label);
  }

  changeFont(delta) {
    const size = Math.min(24, Math.max(14, settings.fontSize + delta));
    saveSettings({ fontSize: size });
  }

  // ---------- Volets ----------

  toggle(which) {
    const open = this.root.dataset[which] !== "open";
    if (open && innerWidth < WIDE) this.closeDrawers(which); // un seul tiroir à la fois sur petit écran
    const column = which === "toc" ? innerWidth >= WIDE : innerWidth >= MEDIUM; // colonne de la grille, pas tiroir
    const change = () => {
      this.root.dataset[which] = open ? "open" : "closed";
      (which === "toc" ? this.tocButton : this.panelButton).setAttribute("aria-pressed", String(open));
    };
    if (column) this.keepReadingPosition(change); else { change(); this.anchors = null; }
    if (column) saveSettings({ [which === "toc" ? "tocOpen" : "panelOpen"]: open });
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
    const element = id ? document.getElementById(id) : null; // accès direct, sans parcourir l'article
    return element && this.content.contains(element) ? element : null;
  }

  navigate(id, options) {
    if (this.pdfMode) { this.showInPdf(id); return; }
    const target = this.find(id);
    if (target) this.navigateElement(target.closest(".ltx_theorem, .ltx_para, .ltx_bibitem, .ltx_equation, .eq-scroll, section") || target, options);
  }

  // Élément dans une preuve repliée : la preuve est dépliée (sinon il n'a pas de position)
  reveal(element) {
    const proof = element.closest(".ltx_proof.collapsed");
    if (proof) this.setProof(proof, false);
  }

  // Bas des barres fixes (en-tête + barre d'outils) : le haut de la zone de lecture.
  viewTop() {
    return this.toolbar.getBoundingClientRect().bottom;
  }

  // Défilement doux pour les petits sauts, immédiat pour les longs (plus lisible).
  scrollToElement(element, smooth = true) {
    const offset = element.getBoundingClientRect().top - this.viewTop() - 12;
    const near = Math.abs(offset) < innerHeight * 1.5;
    scrollTo({ top: Math.round(scrollY + offset), behavior: smooth && near ? "smooth" : "instant" });
  }

  navigateElement(element, { remember = true } = {}) {
    this.reveal(element);
    if (remember) {
      this.backStack.push(scrollY);
      this.backButton.classList.add("visible");
    }
    this.scrollToElement(element);
    element.classList.remove("flash");
    void element.offsetWidth;
    element.classList.add("flash");
  }

  goBack() {
    const top = this.backStack.pop();
    if (top !== undefined) scrollTo({ top, behavior: "instant" });
    this.backButton.classList.toggle("visible", this.backStack.length > 0);
  }

  computeAnchors() {
    const top = -scrollY;
    const elements = this.content.querySelectorAll("section[id], .ltx_theorem[id], .ltx_para[id], .ltx_abstract[id]");
    this.anchors = [...elements].map((el) => ({ el, y: el.getBoundingClientRect().top - top }))
      .filter((a) => a.el.offsetParent !== null)
      .sort((a, b) => a.y - b.y);
  }

  currentAnchor() {
    if (!this.anchors) this.computeAnchors();
    const y = scrollY + this.viewTop() + 24;
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
    if (this.article.classList.contains("typesetting") || this.pdfMode) return; // article pas en page, ou lu dans le PDF
    const anchor = this.currentAnchor();
    if (!anchor) return;
    // Section courante dans le sommaire : la dernière dont le début est au-dessus de la ligne de lecture
    const y = scrollY + this.viewTop() + 24;
    let section = null;
    for (const id of this.tocLinks.keys()) {
      const entry = this.anchors.find((a) => a.el.id === id);
      if (entry && entry.y <= y) section = id;
    }
    this.tocLinks.forEach((a, id) => {
      a.classList.toggle("current", id === section);
      if (id === section) a.setAttribute("aria-current", "location"); else a.removeAttribute("aria-current");
    });
    // Énoncé courant dans la liste
    const theorem = anchor.closest(".ltx_theorem");
    this.panel.querySelectorAll(".node-item.current").forEach((el) => el.classList.remove("current"));
    if (theorem) this.panel.querySelector(`.node-item[data-id="${CSS.escape(theorem.id)}"]`)?.classList.add("current");
    this.savePosition(anchor.id);
  }

  restorePosition() {
    const target = this.find(this.paper.position);
    if (!target) return;
    this.reveal(target);
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
    if (bibitem && link.dataset.crossPaper) return this.crossPreview(link, bibitem);
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

  // Aperçu d'une référence qui est un article lu ici : l'énoncé cité (« Theorem 4.3 »), chargé à la demande
  crossPreview(link, bibitem) {
    const entry = this.crosslinks[bibitem.id];
    const nodeId = link.dataset.crossNode;
    const node = nodeId && entry.nodes.find((n) => n.id === nodeId);
    const open = h("a", { class: "button small-button", href: `#/paper/${encodeURIComponent(entry.paper)}${node ? `/${encodeURIComponent(node.id)}` : ""}` },
      node ? t("reader.crossOpenStatement", { label: node.label }) : t("reader.crossOpen"));
    const head = h("div", { class: "cross-head muted small" }, t("reader.crossIn", { title: entry.title }));
    if (!node) return { body: h("div", {}, head, this.clean(bibitem)), extra: open };
    const holder = h("div", { class: "cross-statement" }, h("div", { class: "busy small" }, h("span", { class: "spinner" })));
    return {
      body: h("div", {}, head, holder), extra: open,
      load: async () => {
        const found = await api.statement(entry.paper, node.id);
        if (!found.html && found.tex !== undefined) { // article lu dans le PDF : énoncé rendu depuis la source TeX
          const element = statementElement({ ...node, tex: found.tex || "" });
          element.removeAttribute("id");
          holder.replaceChildren(element);
          await typesetTexSafe(holder);
          return;
        }
        const html = found.html ? new DOMParser().parseFromString(found.html, "text/html").body.firstElementChild : null;
        if (html) {
          html.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
          html.removeAttribute("id");
          fixMathML(html, []);
        }
        holder.replaceChildren(html || h("p", {}, found.label));
      },
    };
  }

  clean(element) {
    const copy = element.cloneNode(true);
    copy.removeAttribute("id");
    copy.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
    copy.querySelectorAll(".proof-toggle, .block-note, .state-pill, .proof-pointer").forEach((el) => el.remove());
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

  // Lien « #… » cliqué hors de l'article lui-même (énoncé recopié, aperçu) : navigation interne
  interceptLinks = (event) => {
    const link = event.target.closest('a[href^="#"]');
    if (!link || event.defaultPrevented || link.matches(".ai-ref") || link.closest(".tabs")) return;
    if (link.getAttribute("href").startsWith("#/")) { this.hidePreview(); return; } // autre page de l'application (autre article)
    event.preventDefault();
    this.hidePreview();
    this.navigate(decodeURIComponent(link.getAttribute("href").slice(1)));
  };

  showPreview(link) {
    if (this.picking) return;
    const preview = this.previewFor(link);
    if (!preview) return;
    this.hidePreview();
    const footer = h("div", { class: "preview-footer" },
      preview.extra || h("span", {}, preview.label || ""),
      preview.id && h("button", { type: "button", onclick: () => { this.hidePreview(); this.navigate(preview.id); } }, t("reader.goTo")));
    const box = h("div", { class: "preview", role: "tooltip" }, preview.body, footer);
    box.addEventListener("mouseenter", () => clearTimeout(this.hideTimer));
    box.addEventListener("click", this.interceptLinks);
    box.addEventListener("mouseleave", () => this.scheduleHide());
    document.body.append(box);
    typesetFragment(box);
    // contenu chargé ensuite (énoncé d'un autre article) : mis en forme et la bulle recalée
    preview.load?.().then(() => {
      if (this.previewBox !== box) return;
      typesetFragment(box);
      const height = box.offsetHeight;
      const rect = link.getBoundingClientRect();
      const below = rect.bottom + 8 + height < innerHeight;
      box.style.top = `${below ? rect.bottom + 8 : Math.max(8, rect.top - height - 8)}px`;
    }).catch(() => box.querySelector(".cross-statement")?.replaceChildren(h("p", { class: "muted small" }, t("reader.crossUnavailable"))));
    const rect = link.getBoundingClientRect();
    const width = box.offsetWidth;
    const height = box.offsetHeight;
    const left = Math.min(Math.max(12, rect.left - 20), innerWidth - width - 12);
    const below = rect.bottom + 8 + height < innerHeight;
    box.style.left = `${left}px`;
    box.style.top = `${below ? rect.bottom + 8 : Math.max(8, rect.top - height - 8)}px`;
    this.previewBox = box;
    this.previewLink = link;
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

  // L'assistant demande à être affiché (nouvelle demande, passage à expliquer) ou a changé
  onAssistantChange({ show = false, focus = false } = {}) {
    if (this.destroyed) return;
    if (show || focus) {
      this.tab = "ai";
      if (this.root.dataset.panel !== "open") this.toggle("panel");
    }
    if (this.tab !== "ai") return;
    // question en cours de saisie : curseur et sélection gardés malgré le nouveau rendu
    const area = this.panel.querySelector(".ai-question");
    const selection = area && area === document.activeElement ? [area.selectionStart, area.selectionEnd] : null;
    this.renderPanel({ focus });
    if (selection) {
      const next = this.panel.querySelector(".ai-question");
      next?.focus({ preventScroll: true });
      next?.setSelectionRange(...selection);
    }
  }

  // Pastilles d'état et d'emploi des énoncés listés dans le panneau, mises à jour sur place
  refreshNodeItems() {
    const focused = this.panel.contains(document.activeElement) && document.activeElement.matches(".node-item[data-id]")
      ? document.activeElement.dataset.id : null;
    this.panel.querySelectorAll(".node-item[data-id]").forEach((element) => {
      const node = this.byId.get(element.dataset.id);
      if (node) element.replaceWith(this.nodeItem(node));
    });
    if (focused) this.panel.querySelector(`.node-item[data-id="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
  }

  renderPanel({ focus = false } = {}) {
    // Focus dans le panneau avant le nouveau rendu : il est rendu au même endroit ensuite (onglet, énoncé)
    const active = this.panel.contains(document.activeElement) ? document.activeElement : null;
    const keep = active?.matches('[role="tab"]') ? `[role="tab"][data-tab="${this.tab}"]`
      : active?.matches(".node-item[data-id]") ? `.node-item[data-id="${CSS.escape(active.dataset.id)}"]` : null;

    const keys = ["statements", "dependencies", "graph", "notes", "ai"];
    const show = (key) => { this.tab = key; this.renderPanel(); this.panel.querySelector(`[role="tab"][data-tab="${key}"]`)?.focus(); };
    // Onglets à la norme ARIA : un seul arrêt de tabulation, flèches ←/→ (Début/Fin) pour changer d'onglet
    const tabBar = h("div", { class: "tabs", role: "tablist", "aria-label": t("reader.panel") }, keys.map((key) => h("button", {
      type: "button", role: "tab", id: `panel-tab-${key}`, "aria-controls": "panel-body", dataset: { tab: key },
      "aria-selected": this.tab === key, tabindex: this.tab === key ? 0 : -1,
      onclick: () => show(key),
      onkeydown: (event) => {
        const i = keys.indexOf(key);
        const target = { ArrowRight: keys[(i + 1) % keys.length], ArrowLeft: keys[(i + keys.length - 1) % keys.length],
          Home: keys[0], End: keys[keys.length - 1] }[event.key];
        if (!target) return;
        event.preventDefault();
        show(target);
      },
    }, t(`tabs.${key}`))));
    const body = h("div", { class: "panel-body", id: "panel-body", role: "tabpanel", "aria-labelledby": `panel-tab-${this.tab}` });
    if (this.tab === "statements") this.renderStatements(body);
    else if (this.tab === "dependencies") this.renderDependencies(body);
    else if (this.tab === "notes") this.annotator?.renderPanel(body);
    else if (this.tab === "ai") this.assistant?.renderPanel(body, { focus });
    else this.renderGraphTab(body);
    this.panel.replaceChildren(tabBar, body);
    if (keep) this.panel.querySelector(keep)?.focus({ preventScroll: true });
    if (this.tab !== "ai" && this.assistant) this.assistant.live = null; // plus de rendu en direct hors de l'onglet
    this.setPanelWide(this.tab === "ai");
    const graph = this.tab === "graph" && body.querySelector(".graph-wrap");
    if (graph) this.centerGraph(graph);
  }

  // Panneau élargi pour l'onglet IA, sans perdre l'endroit où l'on lit
  setPanelWide(wide) {
    if ((this.root.dataset.tab === "ai") === wide) return;
    const apply = () => { if (wide) this.root.dataset.tab = "ai"; else delete this.root.dataset.tab; };
    if (this.figures && this.root.dataset.panel === "open" && innerWidth >= MEDIUM) this.keepReadingPosition(apply);
    else apply();
  }

  nodeItem(node) {
    const badges = [];
    if (NEEDS_PROOF.has(node.kind) && !this.hasProof(node)) {
      const [key, title] = node.manual === "immediate" ? ["panel.badgeImmediate", ""] : node.manual === "admitted" ? ["panel.badgeAdmitted", ""]
        : node.cited?.length ? ["panel.cited", node.cited.map((id) => `[${this.bib.get(id)?.label || "?"}]`).join(", ")]
        : node.justifiedBy ? ["panel.justified", ""] : ["panel.noProof", ""];
      badges.push(h("span", { class: `badge${key === "panel.noProof" ? " warn" : ""}`, title: title || undefined }, t(key)));
    }
    if (node.usedBy.length) badges.push(h("span", { class: "badge" }, t("panel.usedCount", { n: node.usedBy.length })));
    const state = this.annotator?.stateOf(node.id);
    if (state) badges.unshift(h("span", { class: `state-pill state-${state}` }, t(`notes.state.${state}`)));
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
      this.proofLine(node),
      this.assistant?.actionsFor(node.id),
    );
    setTimeout(() => typesetFragment(card), 0);
    if (this.annotator) {
      card.append(h("div", { class: "dep-notes" }, this.annotator.stateControl(node.id),
        noteEditor({ value: this.annotator.block(node.id)?.note || "", placeholder: t("notes.placeholderBlock"),
          onSave: (note) => this.annotator.saveBlock(node.id, { note }) })));
    }
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
      selected: this.selected, showIsolated: this.showIsolated, showRemarks: this.showRemarks,
      onSelect: (id) => this.select(id, { scroll: true }),
    });
    const isolated = h("input", { type: "checkbox", checked: this.showIsolated });
    isolated.addEventListener("change", () => { this.showIsolated = isolated.checked; this.renderPanel(); });
    const remarks = h("input", { type: "checkbox", checked: this.showRemarks });
    remarks.addEventListener("change", () => { this.showRemarks = remarks.checked; this.renderPanel(); });
    const expand = h("button", { class: "button", type: "button", onclick: () => this.openGraphOverlay() }, t("graph.expand"));
    const kinds = [...new Set(this.nodes.map((n) => n.kind))];
    body.append(
      h("div", { class: "graph-tools" }, h("label", {}, isolated, t("graph.isolated")), h("label", {}, remarks, t("graph.remarks")), expand),
      graph ? h("div", { class: "graph-wrap" }, graph) : h("p", { class: "hint" }, t("graph.empty")),
      h("div", { class: "legend" }, kinds.map((k) => h("span", { dataset: { kind: k } }, h("span", { class: "dot" }), t(`kind.${k}`)))),
      h("p", { class: "hint" }, t("graph.help")),
    );
  }

  // Graphe plus large que le panneau : on fait défiler jusqu'à l'énoncé choisi (sinon jusqu'au milieu)
  centerGraph(wrap) {
    const box = wrap.getBoundingClientRect();
    const target = wrap.querySelector(".node.selected")?.getBoundingClientRect();
    if (target) {
      wrap.scrollLeft += target.left - box.left - (wrap.clientWidth - target.width) / 2;
      wrap.scrollTop += target.top - box.top - (wrap.clientHeight - target.height) / 2;
    } else {
      wrap.scrollLeft = (wrap.scrollWidth - wrap.clientWidth) / 2;
    }
  }

  // Graphe en plein écran : vraie fenêtre modale (<dialog>) — le reste de la page est inerte,
  // Échap la ferme, le focus y entre puis revient au bouton qui l'a ouverte
  openGraphOverlay() {
    const opener = document.activeElement;
    const draw = () => renderGraph(this.nodes, {
      selected: this.selected, showIsolated: this.showIsolated, showRemarks: this.showRemarks,
      onSelect: (id) => { this.select(id); wrap.replaceChildren(draw()); },
    }) || h("p", { class: "hint" }, t("graph.empty"));
    const wrap = h("div", { class: "graph-wrap" }, draw());
    const closeButton = h("button", { class: "button", type: "button" }, t("graph.close"));
    const overlay = h("dialog", { class: "graph-overlay", "aria-label": t("tabs.graph") },
      h("header", {}, h("strong", {}, withMath(this.paper.title)), h("span", { style: "flex:1" }), closeButton),
      wrap, h("p", { class: "hint", style: "margin: 0 16px 12px" }, t("graph.help")));
    closeButton.addEventListener("click", () => {
      overlay.close();
      if (this.selected) this.navigate(this.selected, { remember: false });
    });
    overlay.addEventListener("close", () => {
      overlay.remove();
      this.overlay = null;
      opener?.focus?.({ preventScroll: true });
    });
    document.body.append(overlay);
    overlay.showModal();
    closeButton.focus();
    this.overlay = { remove: () => overlay.close() };
  }

  // Occurrence trouvée (⌘F) : preuve repliée dépliée, puis passage amené au tiers haut de l'écran s'il n'y est pas
  revealRange(range) {
    const node = range.startContainer;
    const element = node.nodeType === 3 ? node.parentElement : node;
    const proof = element?.closest(".collapsed");
    if (proof?.matches(".ltx_proof, [data-proof]") || proof?.querySelector(":scope > .proof-toggle")) this.setProof(proof, false);
    const rect = range.getBoundingClientRect();
    const top = this.toolbar.getBoundingClientRect().bottom;
    if (rect.top < top + 24 || rect.bottom > innerHeight - 24) {
      scrollBy({ top: rect.top - (top + (innerHeight - top) / 3), behavior: "instant" });
    }
  }

  // ---------- Événements ----------

  bindEvents() {
    this.savePosition = debounce((anchor) => api.savePosition(this.paper.id, anchor).catch(() => {}), 1500);
    const onScroll = debounce(() => this.onScroll(), 120);
    // L'article défile avec la fenêtre (et non dans une zone intérieure) : Safari ne fait plus
    // trembler le texte d'un pixel à l'arrêt du défilement.
    const onWindowScroll = () => { this.hidePreview(); this.annotator?.hideToolbar(); onScroll(); };
    addEventListener("scroll", onWindowScroll, { passive: true });
    this.cleanups.push(() => removeEventListener("scroll", onWindowScroll));

    // ⌘F : recherche dans l'article
    this.finder = findBar({
      host: this.scroller,
      search: (query) => findAll(this.content, query),
      reveal: (range) => this.revealRange(range),
      firstVisible: (ranges) => {
        const top = this.toolbar.getBoundingClientRect().bottom;
        return ranges.findIndex((range) => range.getBoundingClientRect().bottom > top);
      },
    });
    // ⌘F : recherche dans le texte HTML (dans le PDF, rien : le texte affiché n'est pas celui de la page)
    if (!this.texOnly) this.cleanups.push(setFindHandler((command) => { if (!this.pdfMode) this.finder.command(command); }), () => this.finder.close());

    const zoom = (event) => {
      const figure = event.target.closest?.(".figure-card, img.ltx_graphics");
      if (!figure || !this.content.contains(figure) || this.picking || !getSelection().isCollapsed) return;
      if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
      if (event.target.closest("a")) return; // lien dans une étiquette
      event.preventDefault();
      openFigure(figure, { closeLabel: t("reader.figureClose"), onLink: this.interceptLinks });
    };
    this.content.addEventListener("click", zoom);
    this.content.addEventListener("keydown", zoom);

    // Renvois internes (#…) dans l'article, le panneau (énoncé recopié) ou un aperçu : on reste dans le lecteur
    this.scroller.addEventListener("click", this.interceptLinks);
    this.panel.addEventListener("click", this.interceptLinks);
    // ↑/↓ d'un énoncé à l'autre dans les listes du panneau (Entrée : y aller)
    this.panel.addEventListener("keydown", (event) => {
      if (!["ArrowDown", "ArrowUp"].includes(event.key) || !event.target.matches(".node-item")) return;
      const items = [...this.panel.querySelectorAll(".node-item")];
      const next = items[items.indexOf(event.target) + (event.key === "ArrowDown" ? 1 : -1)];
      if (!next) return;
      event.preventDefault();
      next.focus();
    });

    const hoverable = 'a[href^="#"], .ltx_note_mark';
    this.scroller.addEventListener("mouseover", (event) => {
      const link = event.target.closest(hoverable);
      if (!link || !this.content.contains(link) || link.contains(event.relatedTarget)) return; // dans le même renvoi
      if (this.previewBox && this.previewLink === link) { clearTimeout(this.hideTimer); return; }
      clearTimeout(this.hideTimer);
      clearTimeout(this.showTimer);
      this.showTimer = setTimeout(() => this.showPreview(link), 220);
    });
    this.scroller.addEventListener("mouseout", (event) => {
      const link = event.target.closest(hoverable);
      if (link && !link.contains(event.relatedTarget)) {
        clearTimeout(this.showTimer);
        this.scheduleHide();
      }
    });

    const onKey = (event) => {
      if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
      if (event.key === "Escape") { this.hidePreview(); this.closeDrawers(); }
    };
    const layout = () => (innerWidth >= WIDE ? "wide" : innerWidth >= MEDIUM ? "medium" : "narrow");
    let currentLayout = layout();
    let lastWidth = innerWidth;
    const onResize = debounce(() => {
      // seule la largeur compte (sur iPhone, la hauteur change sans cesse avec les barres de Safari)
      if (innerWidth === lastWidth) return;
      lastWidth = innerWidth;
      this.keepReadingPosition(() => {
        if (layout() === currentLayout) return;
        currentLayout = layout();
        // Changement de format : volets remis dans leur état par défaut pour cette largeur
        this.setOpen("toc", currentLayout === "wide" && settings.tocOpen);
        this.setOpen("panel", currentLayout !== "narrow" && settings.panelOpen);
      });
    }, 200);
    document.addEventListener("keydown", onKey);
    addEventListener("resize", onResize);
    // Taille, largeur ou police changées : même passage à l'écran après la nouvelle mise en page
    let look = `${settings.fontSize}/${settings.readingWidth}/${settings.paperFont}`;
    let assistant = currentModel(settings);
    this.updateZoomButton();
    const unsubscribe = onSettingsChange(() => {
      if (currentModel(settings) !== assistant) { // assistant IA changé dans les réglages : onglet IA à jour
        assistant = currentModel(settings);
        if (this.tab === "ai") this.renderPanel();
      }
      this.updateZoomButton();
      this.article.dataset.marks = settings.marginMarks ? "on" : "off";
      const now = `${settings.fontSize}/${settings.readingWidth}/${settings.paperFont}`;
      if (now !== look) { look = now; this.keepReadingPosition(); } else this.anchors = null;
    });
    this.cleanups.push(() => document.removeEventListener("keydown", onKey), () => removeEventListener("resize", onResize), unsubscribe);
  }

  destroy() {
    this.destroyed = true;
    document.body.classList.remove("reading");
    this.annotator?.destroy();
    this.assistant?.destroy();
    this.stopPick();
    this.hidePreview();
    this.overlay?.remove();
    this.cleanups.forEach((fn) => fn());
  }
}

