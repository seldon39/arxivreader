// Lecteur d'article : texte, sommaire, aperçus des renvois, preuves repliables, panneau des énoncés.

import { api, setReadingVersion } from "./api.js";
import { closure, renderGraph } from "./graph.js";
import { Annotator, findAll, noteEditor } from "./annotations.js";
import { Assistant } from "./ai.js";
import { alignNumberColumns, amsBibliography, dropMacroDebris, fixMathML, texQuotes, splitLongInlineMath, markItalicStatements, moveTitleNotes, normalizeFontScale, placeEquationNumbers, relabelLists } from "./amsfix.js";
import { currentModel } from "./aiproviders.js";
import { emptyPictures, layoutFigures, openFigure, prepareFigures, sizeToText } from "./figures.js";
import { findBar, setFindHandler } from "./find.js";
import { PdfView } from "./pdfview.js";
import { PdfNotes } from "./pdfnotes.js";
import { LazyPaint } from "./lazypaint.js";
import { bookmarkBox, bookmarkSection, contextMenuAt, inPdf as bookmarkInPdf, staleVersion } from "./bookmarks.js";
import { htmlToPdf, htmlWords, pdfToHtml, wordTop } from "./pdfsync.js";
import { defineMacros, statementElement, statementsContainer } from "./texview.js";
import { typesetFragment, typesetMath, typesetTex, undefinedFonts } from "./math.js";

import { t, tn } from "./i18n.js";
import { DEFAULT_FONT_SIZE, READING_WIDTHS, onSettingsChange, saveSettings, settings } from "./settings.js";
import { debounce, escapeRegExp, formatDate, h, icons, metaDialog, nativeBridge, navigation, openRoute, plainTitle, titleTex, toast, toastError, toggleIcon, wantsNewTab, withMath } from "./util.js";
import { chooseBibFor, texChecks } from "./texfile.js";
import { htmlFlow, rememberView, wantsHtml } from "./latexml.js";

const NEEDS_PROOF = new Set(["theorem", "proposition", "lemma", "corollary", "claim"]);
const WIDE = 1280;
// Blocs qu'on peut désigner comme passage de preuve (mêmes classes que PASSAGE_BLOCKS dans app/structure.py)
const PICKABLE = [".ltx_para", ".ltx_theorem", ".ltx_proof", ".ltx_equation", ".ltx_equationgroup", ".ltx_figure", ".ltx_table"]
  .map((c) => `${c}[id]`).join(", ");
const MEDIUM = 900;
const AI_PANEL_W = 420; // largeur par défaut du panneau dans l'onglet IA (réponses plus lisibles), sans largeur choisie

// `at` : énoncé ou paragraphe à afficher à l'ouverture (« #/paper/<id>/<identifiant> », renvoi depuis un autre article,
// résultat d'une recherche dans le texte)
export async function renderReader(view, paperId, at, isCurrent = () => true) {
  if (typeof at === "function") [at, isCurrent] = [undefined, at];
  view.replaceChildren(h("div", { class: "library" }, h("div", { class: "busy" }, h("span", { class: "spinner" }))));
  // article ajouté d'un clic dont l'import n'est pas fini : chaque demande attend au plus 45 s côté serveur
  // (une requête restée longtemps sans réponse risquerait d'être abandonnée par WebKit) ; on redemande
  let { paper, structure } = await api.paper(paperId);
  for (let tries = 0; paper.source === "pending" && tries < 12 && isCurrent(); tries++) {
    view.replaceChildren(h("div", { class: "library" }, h("div", { class: "busy" }, h("span", { class: "spinner" }), t("reader.preparing"))));
    ({ paper, structure } = await api.paper(paperId));
  }
  setReadingVersion(paper.id, paper.version);
  // lu dans le PDF faute de HTML, sauf version HTML faite par LaTeXML (paper.html « ready »)
  const html = ["none", "tex", "local-pdf", "local-tex"].includes(paper.source) && paper.html !== "ready" ? "" : await api.content(paperId);
  if (!isCurrent()) return () => {}; // page déjà quittée pendant le chargement : rien n'est affiché
  document.title = plainTitle(paper.title) + (paper.archived_of ? ` (v${paper.version})` : ""); // titre de l'onglet
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
    // version HTML expérimentale possible (LaTeXML, à la demande) : fichier TeX importé, article d'arXiv lu depuis sa source
    this.experimental = ["tex", "local-tex"].includes(paper.source);
    // pas de version HTML : toujours dans le PDF
    this.texOnly = ["tex", "local-pdf", "local-tex"].includes(paper.source) && !(this.experimental && paper.html === "ready");
    this.texFile = paper.source === "local-tex";  // fichier TeX de l'ordinateur : recompilé à la demande
    this.onlyIds = null;  // vérifications du fichier TeX : seulement ces énoncés dans la liste
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
    this.viewToken = 0;

    this.build(view, html);
    // version HTML expérimentale : le PDF d'abord, sauf si on vient de la choisir (ou de la faire)
    if (!this.texOnly && this.viewSwitch && (this.experimental ? !wantsHtml(paper.id) : settings.readerView === "pdf")) this.setView("pdf", { save: false });
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
        if (this.texFile) this.texReportSoon();  // notes à replacer : compte du bilan tenu à jour
      },
      aiActions: (id) => this.assistant.actionsFor(id, { compact: true }),
      onAskAI: (quote) => this.assistant.setPassage(quote),
    });
    if (this.pdf && !this.pdfNotes) this.pdfNotes = this.makePdfNotes();
    if (this.texFile) this.loadTexReport();
    // onglet Notes : passage surligné dans le PDF, lu en HTML → vue PDF d'abord
    this.annotator.version = paper.version;  // notes libres : version de l'article où elles sont posées
    // clic droit dans l'article : marque-page sur le paragraphe cliqué
    this.annotator.onBookmarkAt = (element, rect) => this.addBookmark({ element, rect });
    // onglet Notes : marque-pages en tête
    this.annotator.renderBookmarks = () => bookmarkSection(this.annotator.items, {
      paper: this.paper, find: (id) => this.find(id), go: (annotation) => this.goToBookmark(annotation),
      where: (annotation, element) => (bookmarkInPdf(annotation) ? t("bookmarks.pageShort", { page: annotation.target.pdf.page }) : this.sectionTag(element)),
    });
    this.annotator.revealPdf = async (annotation) => {
      if (!this.pdfMode && !this.texOnly) await this.setView("pdf", { save: false });
      if (!this.destroyed) this.pdfNotes?.reveal(annotation);
    };
    this.assistant = new Assistant({
      paper, structure: this.structure, find: (id) => this.find(id), annotator: this.annotator,
      navigate: (id) => this.navigate(id),
      onChange: (options) => this.onAssistantChange(options),
      inPdf: () => this.pdfMode,
      pdfQuote: this.texOnly ? (quote, near) => this.pdfQuote(quote, near) : null,
      contextPages: (center, signal) => this.contextPages(center, signal),
      currentPage: () => this.pdf?.frame.contentWindow?.PDFViewerApplication?.pdfViewer?.currentPageNumber || null,
      readingChapter: () => this.readingChapter(),
      equationIds: () => [...this.content.querySelectorAll(".ltx_tag_equation")].map((tag) => {
        const row = tag.closest("tr[id], table[id], .ltx_equation[id]");
        return row ? [tag.textContent.trim(), row.id] : null;
      }).filter(Boolean),
    });
    this.finishLayout();
  }

  // Formules en MathJax avant d'afficher le texte (moins d'une seconde), puis reprise de la lecture
  async finishLayout() {
    undefinedFonts(this.content);  // \EuScript… laissées en texte par arXiv : aussi avec le MathML du navigateur
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
    this.paintBookmarks();
    this.pdfNotes?.repaintAll(); // passages surlignés dans le PDF, connus seulement maintenant
    this.renderPanel(); // avec les états de lecture, chargés seulement maintenant
    this.anchors = null;
    if (!this.texOnly) this.lazyPaint = new LazyPaint(this.content); // sections loin de l'écran : pas dessinées
    if (this.texOnly) {
      await defineMacros(this.structure.macros);
      await typesetTex([this.content]);
      if (this.destroyed) return;
      if (this.startAt) this.showInPdf(this.startAt);
      this.loadCrossLinks();
      this.notesToCheckToast();
      return;
    }
    if (this.pdfMode) {
      if (this.startAt) this.showInPdf(this.startAt);
    } else if (this.startAt && this.find(this.startAt)) {
      this.reveal(this.find(this.startAt));
      requestAnimationFrame(() => this.navigate(this.startAt));
    } else if (this.startAt === "top") {
      scrollTo({ top: 0, behavior: "instant" }); // résultat d'une recherche dans le résumé (qui n'a pas d'identifiant)
    } else {
      this.restorePosition();
    }
    this.loadCrossLinks();
  }

  // ---------- Lecture en HTML ou dans le PDF ----------

  // Endroit du PDF d'un élément : énoncé, section, preuve (celui de son énoncé), sinon section qui le
  // contient. { page, top } ; top : hauteur dans la page (coordonnées du PDF), quand hyperref la donne.
  pdfSpot(id) {
    if (this.texOnly) {
      const item = this.byId.get(id) || this.structure.toc.find((s) => s.id === id)
        || (this.structure.proofBlocks || []).find((p) => p.id === id)
        || this.structure.equations?.[id] || this.bib.get(id);  // fichier TeX : équations et bibliographie (ancres)
      return item?.page ? { page: item.page, top: item.top ?? null } : null;
    }
    const pages = this.pages || {};
    const spot = (key) => ({ page: pages[key], top: this.tops?.[key] ?? null });
    if (pages[id]) return spot(id);
    const owner = this.nodes.find((n) => n.proofs.includes(id));
    if (owner && pages[owner.id]) return spot(owner.id);
    for (let element = this.find(id); element; element = element.parentElement?.closest("[id]")) {
      const statement = element.closest(".ltx_theorem[id]");
      if (statement && pages[statement.id]) return spot(statement.id);
      if (pages[element.id]) return spot(element.id);
    }
    return null;
  }

  // Élément du HTML à l'endroit lu dans le PDF : le dernier énoncé ou la dernière section qui commence
  // avant le haut de la fenêtre (ou juste en dessous). Pages en ordre du document ; une page qui recule
  // (titre introuvable dans le PDF, page 1 par défaut) est ignorée.
  htmlSpot(location) {
    if (!location?.page || !this.pages) return null;
    if (!this.pdfAnchors) {
      this.pdfAnchors = Object.keys(this.pages).map((id) => this.find(id)).filter(Boolean)
        .sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
    }
    let found = null;
    let last = 0;
    for (const element of this.pdfAnchors) {
      const page = this.pages[element.id];
      if (page < last) continue;
      last = page;
      const top = this.tops?.[element.id];
      const before = page < location.page
        || (page === location.page && (top == null || location.top == null || top >= location.top - 40));
      if (!before) break;
      found = element;
    }
    return found;
  }

  async loadPages() {
    if (this.pages) return;
    try {
      const result = await api.pages(this.paper.id);
      this.pages = result.pages || {};
      this.tops = result.tops || {};
    } catch {
      this.pages = {};
      this.tops = {};
    }
  }

  async setView(view, { save = true } = {}) {
    if (this.texOnly || (view === "pdf") === this.pdfMode) return;
    // jeton : une bascule plus récente (ou un énoncé choisi entre-temps) l'emporte sur les suites de celle-ci
    const token = ++this.viewToken;
    const current = () => token === this.viewToken && !this.destroyed;
    if (save && this.experimental) rememberView(this.paper.id, view === "html");  // réglage général inchangé
    else if (save) saveSettings({ readerView: view });
    this.viewSwitch?.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
    if (view === "pdf") {
      // page où l'on en était dans le HTML (pas à l'ouverture : rien n'est encore lu)
      if (save) this.htmlScroll = scrollY;
      // à l'ouverture : l'énoncé demandé (renvoi), sinon la dernière position de lecture ; ensuite, l'endroit
      // lu dans le HTML (à l'ouverture, l'article n'est pas encore dans la page : recherche dans son contenu)
      const position = this.startAt || this.paper.position;
      const anchor = save ? this.currentAnchor()
        : position ? this.find(position) || this.content.querySelector(`#${CSS.escape(position)}`) : null;
      const anchorId = anchor?.id ?? (save ? null : position);
      // texte du HTML et mot lu (avant de masquer le HTML : il faut sa mise en page)
      this.htmlText = htmlWords(this.content);
      const from = this.wordAt(this.htmlText, anchor, save);
      this.pdfMode = true;
      this.root.dataset.view = "pdf";
      if (!this.pdf) {
        this.pdf = new PdfView(this.pdfUrl(), this.paper.title);
        // à l'ouverture (vue PDF gardée), l'annotateur n'existe pas encore : PdfNotes créé juste après lui
        if (this.annotator) this.pdfNotes = this.makePdfNotes();
        this.pdfView = h("div", { class: "paper pdf-mode pdf-view" }, this.pdf.frame);
        this.scroller.append(this.pdfView);
        this.followPdf();
      }
      scrollTo({ top: 0, behavior: "instant" });
      await this.loadPages();
      if (!current()) return;
      // d'abord l'estimation des ancres (immédiate), puis l'endroit exact, retrouvé par le texte
      const spot = anchorId ? this.pdfSpot(anchorId) : null;
      this.pdf.goTo(spot?.page || 1, spot?.top ?? null);
      const text = await this.pdf.text();
      if (!text || from < 0 || !current()) return;
      const exact = htmlToPdf(this.htmlText, text, from, spot?.page ?? null);
      if (exact) this.pdf.goTo(exact.page, exact.top);
    } else {
      // retour au HTML : au texte lu en haut du PDF ; sinon à l'énoncé estimé par les ancres, sinon là où
      // l'on en était dans le HTML
      const location = this.pdf?.location();
      const estimate = this.htmlSpot(location);
      this.pdfMode = false;
      delete this.root.dataset.view;
      this.anchors = null;
      // le HTML a pu être mis en page caché (article ouvert dans le PDF, fenêtre redimensionnée) :
      // figures et formules larges recalculées maintenant qu'il est affiché
      this.relayout(() => {
        layoutFigures(this.figures);
        this.markWideMath();
      });
      const text = location ? await this.pdf.text() : null;
      if (!current()) return;
      const html = this.htmlText = htmlWords(this.content);
      const estimateTop = estimate?.getBoundingClientRect().top;
      const word = text && pdfToHtml(text, html, location, (entry) => (estimateTop == null ? 0 : Math.abs(wordTop(entry) - estimateTop)));
      // défilement immédiat (la mise en page est recalculée à la lecture des positions)
      if (word) {
        this.reveal(word.node.parentElement);
        scrollTo({ top: Math.round(scrollY + wordTop(word) - this.viewTop() - 24), behavior: "instant" });
      } else if (estimate) {
        this.reveal(estimate);
        this.scrollToElement(estimate, false);
      } else if (this.htmlScroll !== undefined) scrollTo({ top: this.htmlScroll, behavior: "instant" });
      else this.restorePosition();
    }
  }

  // Mot du HTML d'où chercher dans le PDF : le premier visible sous la ligne de lecture (dans l'élément
  // courant ou après), ou le premier de l'élément quand la page n'est pas encore en place (ouverture)
  wordAt(html, anchor, visible) {
    const sequence = html.sequence;
    if (!anchor) return -1;
    const after = (node) => anchor.contains(node) || Boolean(anchor.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING);
    let low = 0;
    let high = sequence.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (after(sequence[mid].node)) high = mid; else low = mid + 1;
    }
    if (!visible) return low < sequence.length ? low : -1;
    const line = this.viewTop() + 24;
    for (let i = low; i < Math.min(sequence.length, low + 400); i++) {
      const top = wordTop(sequence[i]);
      if (top && top >= line - 6) return i;
    }
    return low < sequence.length ? low : -1;
  }

  // Lecture dans le PDF : la position de lecture suit (même article rouvert plus tard, dans le HTML ou le PDF)
  followPdf() {
    const follow = debounce(async (location) => {
      if (!this.pdfMode || this.destroyed) return;
      const text = await this.pdf.text();
      const word = text && this.htmlText && pdfToHtml(text, this.htmlText, location);
      const target = word?.node.parentElement?.closest("[id]") || this.htmlSpot(location);
      if (target && this.content.contains(target)) {
        this.readingId = target.id;
        this.savePosition(target.id);
        this.followStatementsChapter();
      }
    }, 800);
    this.pdf.onMove(follow);
  }

  // Article lu dans le PDF : page de l'énoncé, de la section ou de la preuve, et énoncé choisi dans le panneau
  showInPdf(id) {
    this.viewToken++; // un recalage en attente (bascule HTML → PDF) ne déplace plus le PDF ensuite
    const node = this.byId.get(id);
    const spot = this.pdfSpot(id);
    if (spot && this.pdf) this.pdf.goTo(spot.page, spot.top);
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
      item.append(h("a", { class: "cross-badge cross-open", href: `#/paper/${encodeURIComponent(entry.paper)}`,
        title: `${entry.title}\n${t("reader.crossOpenTab")}` }, t("reader.crossOpen")));
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
      class: `reader${this.texOnly ? " tex-only" : ""}`, // sans version HTML : taille du texte sans objet
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
      // retour à la page d'où l'article a été ouvert (bibliothèque ou feed)
      h("a", { class: "icon-button", href: navigation.listHash, html: icons.back,
        ...(() => { const label = t(navigation.listHash.startsWith("#/feed") ? "reader.backFeed" : navigation.listHash === "#/search" ? "reader.backSearch" : navigation.listHash.startsWith("#/related") ? "reader.backRelated" : "reader.back");
          return { title: label, "aria-label": label }; })() }),
      this.toolbarTitle = h("div", { class: "title", title: paper.title }, titleTex(paper.title)),
      button(icons.minus, "reader.smaller", () => this.changeFont(-1), { class: "wide-only html-only" }),
      this.zoomButton = h("button", { class: "zoom-reset wide-only html-only", type: "button", onclick: () => { if (settings.fontSize !== DEFAULT_FONT_SIZE) saveSettings({ fontSize: DEFAULT_FONT_SIZE }); } }),
      button(icons.plus, "reader.larger", () => this.changeFont(1), { class: "wide-only html-only" }),
      this.narrowerButton = button(icons.narrower, "reader.narrower", () => this.changeWidth(-1), { class: "wide-only html-only" }),
      this.widerButton = button(icons.wider, "reader.wider", () => this.changeWidth(1), { class: "wide-only html-only" }),
      this.foldButton,
      // choix HTML / PDF après les réglages du texte HTML (masqués dans le PDF) : il ne bouge pas quand on change
      // version HTML expérimentale (LaTeXML) : « HTML β » ; pas encore faite, un clic la propose
      this.viewSwitch = ((!this.texOnly && paper.source !== "none") || this.experimental) && h("div", { class: "view-switch", role: "group", "aria-label": t("reader.view") },
        [["html", "HTML"], ["pdf", "PDF"]].map(([view, label]) => h("button", { type: "button", dataset: { view }, "aria-pressed": String(this.texOnly ? view === "pdf" : view === "html"),
          title: this.experimental && view === "html" ? t("latexml.buttonTitle") : t(`reader.view.${view}`),
          onclick: (event) => (this.texOnly && view === "html" ? htmlFlow(event.currentTarget, { paperId: paper.id, article: this.article })
            : this.setView(view)) }, label, this.experimental && view === "html" ? h("sup", { class: "beta" }, "β") : null))),
      // pas de version HTML : à la place du choix HTML / PDF, une petite mention (explication au survol)
      paper.source === "local-pdf" && h("span", { class: "no-html-badge", title: t("reader.localPdf") }, "PDF"),
      this.texFile && (this.recompileButton = button(icons.refresh, "tex.recompile", () => this.recompile())),
      h("span", { class: "sep wide-only" }),
      !paper.archived_of && this.libraryToggle(), // une version archivée n'entre pas dans la bibliothèque
      paper.arxiv_id && link(icons.external, "arXiv", `https://arxiv.org/abs/${paper.arxiv_id}v${paper.version}`),

      this.panelButton,
    );
    // formules du titre composées par MathJax (une fois la page en place : typesetTex attend son tour)
    typesetTex([this.toolbarTitle]);

    this.toc = h("aside", { class: "toc", "aria-label": t("reader.contents") });
    this.panel = h("aside", { class: "panel", "aria-label": t("reader.panel") });
    this.content = h("div", { class: "paper-content" });
    this.article = h("article", { class: "paper", dataset: { marks: settings.marginMarks ? "on" : "off" } });
    this.backButton = h("button", { class: "back-button", type: "button", onclick: () => this.goBack() }, h("span", { class: "reason-icon", html: icons.undo }), t("reader.returnTo"));
    this.scroller = h("div", { class: "paper-scroll" }, this.article, this.backButton);

    const meta = h("div", { class: "paper-meta" },
      paper.primary_category && h("span", { class: "chip category" }, paper.primary_category),
      ...paper.categories.filter((c) => c !== paper.primary_category).map((c) => h("span", { class: "chip" }, c)),
      paper.arxiv_id && this.versionChip(),
      (paper.updated || paper.published) && h("span", { class: "chip" }, formatDate(paper.updated || paper.published)),
      paper.source === "local-pdf" && paper.comment && h("span", { class: "chip", title: t("reader.localPdf") }, paper.comment),
      (paper.source === "local-pdf" || this.texFile) && h("button", { class: "chip chip-button", type: "button", onclick: () => this.editMeta() }, t("reader.editMeta")),
      paper.source !== "none" && h("span", { class: "chip" }, t(`reader.source.${paper.source}`)),
      paper.doi && h("a", { class: "chip", href: `https://doi.org/${paper.doi}`, target: "_blank", rel: "noopener noreferrer" }, `DOI ${paper.doi}`),
      ...this.paperFindLinks(paper),
    );
    this.article.append(meta);
    if (paper.archived_of) {
      this.article.append(h("p", { class: "version-notice" }, t(paper.version > paper.mainVersion ? "reader.archivedNewer" : "reader.archived", { k: paper.version }), " ",
        h("a", { href: `#/paper/${encodeURIComponent(paper.archived_of)}` }, t("reader.version", { v: paper.mainVersion }))));
    }

    if (this.structure.hiddenText > 0) {
      // « Afficher » : le texte invisible apparaît, encadré, et on y va (il peut être loin : bloc des adresses, fin de
      // l'article) ; « Suivant » passe aux autres endroits ; « Précédent » (barre) ramène où l'on lisait
      const toggle = h("button", { type: "button" }, t("reader.reveal"));
      const next = h("button", { type: "button", hidden: true });
      let spots = [];
      let index = 0;
      // un seul « Précédent » ramène à la lecture : les sauts suivants ne s'empilent pas
      const go = (remember) => {
        next.textContent = t("reader.nextHidden", { i: index + 1, n: spots.length });
        next.hidden = spots.length < 2;
        if (spots[index]) this.navigateElement(spots[index], { remember });
      };
      toggle.addEventListener("click", () => {
        let on;
        this.relayout(() => { on = this.article.classList.toggle("reveal-hidden"); this.anchors = null; });
        toggle.textContent = on ? t("reader.conceal") : t("reader.reveal");
        if (!on || this.pdfMode) { next.hidden = true; return; }
        // un endroit par paragraphe (texte caché lettre par lettre : une seule étape), dans l'ordre de l'article
        const seen = new Set();
        // seulement ce qui est à l'écran (pas une preuve repliée ni une page en cours de composition)
        spots = [...this.content.querySelectorAll(".arx-hidden-text")].filter((element) => element.getClientRects().length).filter((element) => {
          const block = element.closest(".ltx_para, p, li, td, figure, .ltx_theorem, svg, section") || element;
          if (seen.has(block)) return false;
          seen.add(block);
          return true;
        });
        index = 0;
        go(true);
      });
      next.addEventListener("click", () => { index = (index + 1) % spots.length; go(false); });
      this.article.append(h("div", { class: "notice" }, h("span", { class: "reason-icon", html: icons.warning }),
        t("reader.hiddenText", { n: this.structure.hiddenText }), toggle, next));
    }

    if (this.texOnly) {
      // Pas de version HTML : lecture dans le PDF ; énoncés (rendus depuis la source TeX, aucun pour un PDF importé).
      // Fichier TeX : PDF de la dernière compilation (adresse changée à chaque fois : jamais l'ancien gardé en cache)
      this.pdf = new PdfView(this.pdfUrl(), paper.title);
      const spot = this.startAt ? this.pdfSpot(this.startAt) : null;
      const recompiled = this.recompiledPage();  // toujours lue : pas gardée pour une ouverture suivante
      this.pdf.load(spot?.page || recompiled, spot?.top ?? null);
      this.article.classList.add("pdf-mode");
      this.article.append(this.pdf.frame);
      this.content = statementsContainer(this.structure);
      this.article.append(this.content);
    } else if (paper.source === "none") {
      this.article.append(
        h("div", { class: "notice" }, t("reader.noHtml")),
        this.paperTitle = h("h1", { class: "ltx_title_document" }, titleTex(paper.title)),
        h("div", { class: "ltx_authors" }, paper.authors.join(", ")),
        h("div", { class: "ltx_abstract" }, h("h6", { class: "ltx_title_abstract" }, t("reader.abstract")), h("p", {}, paper.abstract)),
      );
      typesetTex([this.paperTitle]);
    } else {
      this.content.innerHTML = html; // HTML nettoyé côté serveur ; scripts bloqués par la CSP
      this.article.append(this.content);
      // figure absente (import incomplet) : le serveur la redemande à arXiv (parfois par un nouvel import, sous
      // d'autres noms) ; on suit la réparation et l'article est rechargé (à la même place) quand elle est finie
      let watching = false;
      this.content.addEventListener("error", (event) => {
        if (watching || event.target.tagName !== "IMG" || !event.target.getAttribute("src")?.includes("/assets/")) return;
        watching = true;
        const started = Date.now();
        const check = async () => {
          if (!this.content.isConnected || Date.now() - started > 180000) return;
          const state = await api.figures(this.paper.id).catch(() => null);
          if (state && !state.repairing && state.missing === 0) { dispatchEvent(new HashChangeEvent("hashchange")); return; }
          setTimeout(check, 4000);
        };
        setTimeout(check, 3000);
      }, true);
    }

    this.root.append(toolbar, this.toc, this.paneResizer("toc"), this.scroller, this.panel, this.paneResizer("panel"),
      h("div", { class: "scrim", onclick: () => this.closeDrawers() }));
    this.applyPanelWidth();
    if (settings.tocWidth) this.root.style.setProperty("--toc-w", `${settings.tocWidth}px`);
    // Police « comme l'article » : celle du paquet de police de la source TeX
    if (settings.paperFont === "auto") this.root.dataset.paperFont = this.structure.tex?.font || "lm";
    view.replaceChildren(this.root);
  }

  // Poignées de largeur : bord droit du sommaire, bord gauche du panneau. Glisser, flèches du clavier
  // (Maj : pas plus grand), double-clic : largeur par défaut.
  // Pendant le glissement, la grille ne bouge pas : le volet s'étend par-dessus l'article (marge négative),
  // ce qui ne recompose que le volet (moins d'une milliseconde, contre plus de 100 ms pour l'article
  // entier). L'article n'est recomposé qu'une fois, au relâchement, à la même position de lecture.
  // Onglet IA : le fil des réponses suit la poignée ; seules les réponses visibles sont recomposées (CSS
  // `.reader.resizing .ai-answer`, les autres gardent leur hauteur), sinon ~50 ms par image.
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
        if (isPanel) this.applyPanelWidth(width);
        else if (width === null) this.root.style.removeProperty(variable);
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
      // dès l'appui (pas au premier mouvement) : la mise en place (~0,2 s, réponses de l'IA hors écran mises de côté)
      // a lieu avant que rien ne bouge ; voile sur la fenêtre : curseur, et rien d'autre ne réagit
      this.root.classList.add("resizing");
      const shield = document.body.appendChild(h("div", { class: "resize-shield" }));
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
        if (!frame) frame = requestAnimationFrame(draw); // au plus un calcul par image
      };
      const up = () => {
        removeEventListener("pointermove", move);
        removeEventListener("pointerup", up);
        removeEventListener("pointercancel", up);
        if (frame) { cancelAnimationFrame(frame); draw(); }
        shield.remove();
        if (width !== null) commit(width);
        else this.root.classList.remove("resizing"); // simple clic
      };
      // sur la fenêtre : reçus même si la capture du pointeur échoue (le voile serait sinon la cible, sans fin du glissement)
      addEventListener("pointermove", move);
      addEventListener("pointerup", up);
      addEventListener("pointercancel", up);
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

  // « arXiv:ID · version n » : menu des versions de l'article (chacune a ses notes) ; la pastille tourne pendant
  // un téléchargement ou une mise à jour, faits en arrière-plan
  versionChip() {
    const paper = this.paper;
    const spinner = h("span", { class: "spinner" });
    const chip = h("button", { class: "chip chip-button version-chip", type: "button", "aria-haspopup": "menu", title: t("reader.versions.title"),
      onclick: () => menu() }, `arXiv:${paper.arxiv_id} · ${t("reader.version", { v: paper.version })}`);
    const busy = (on) => {
      chip.disabled = on;
      chip.setAttribute("aria-busy", String(on));
      if (on) chip.append(spinner); else spinner.remove();
    };
    const line = (entry, latest) => [
      t("reader.versions.number", { v: entry.version }),
      entry.current && t("reader.versions.reading"),
      entry.notes && tn("reader.versions.notes", entry.notes),
      entry.answers && tn("reader.versions.answers", entry.answers),
      entry.version === latest && t("reader.versions.latest"),
      !entry.id && t(entry.running ? "reader.versions.downloading" : "reader.versions.toDownload"),
    ].filter(Boolean).join(" · ");
    const choose = async (entry) => {
      if (entry.current) return;
      const from = location.hash;
      busy(true);
      try {
        let { id, running } = await api.openVersion(paper.id, entry.version);
        if (running) {
          for (let i = 0; i < 200 && running; i++) {
            await new Promise((r) => setTimeout(r, 1500));
            ({ running } = await api.importing(id).catch(() => ({ running: false }))); // échec : fiche retirée, 404
          }
          id = (await api.versions(paper.id)).versions.find((v) => v.version === entry.version)?.id;
          if (!id) throw new Error(t("reader.versions.failed"));
        }
        if (location.hash !== from) return; // page quittée entre-temps
        if (id === paper.id) dispatchEvent(new HashChangeEvent("hashchange")); // mise à jour de l'article affiché
        else location.hash = `#/paper/${encodeURIComponent(id)}`;
      } catch (error) {
        toastError(error);
      } finally {
        busy(false);
      }
    };
    const menu = async () => {
      busy(true);
      let info;
      try {
        info = await api.versions(paper.id);
      } catch (error) {
        toastError(error);
        return;
      } finally {
        busy(false);
      }
      if (!chip.isConnected) return; // page quittée pendant l'attente (arXiv lent)
      const box = chip.getBoundingClientRect();
      contextMenuAt({ left: box.left, top: box.bottom + 6 }, [
        ...[...info.versions].reverse().map((entry) => [line(entry, info.latest), () => choose(entry)]),
        ...(info.offline ? [[t("reader.versions.offline"), () => {}]] : []),
      ]);
    };
    return chip;
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
        toastError(error);
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
    // dessins absents de la version HTML (LaTeXML n'a gardé que les étiquettes) : avertissement qui renvoie au PDF
    for (const [first, ...rest] of emptyPictures(this.content)) {
      rest.forEach((picture) => picture.remove());
      first.replaceWith(this.pictureNotice());
    }
    // corps du document (option 11pt, 12pt de \documentclass) : référence de la taille des figures
    const size = (this.structure?.tex?.options || []).map((option) => option.match(/^(\d+(?:\.\d+)?)pt$/)).find(Boolean);
    sizeToText(this.content, size ? parseFloat(size[1]) : 10);
    this.figures = prepareFigures(this.content);
    // Figures cliquables (et au clavier) : affichage en grand par-dessus l'article
    // (une image placée dans un dessin s'ouvre avec le dessin entier)
    this.content.querySelectorAll(".figure-card, img.ltx_graphics:not(.figure-card img)").forEach((figure) => {
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
    this.linkArxivReferences();
    this.addFindLinks();

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
    dropMacroDebris(this.content);
    texQuotes(this.content);
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
      if (line.textContent.includes("$")) typesetTex([line]);
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
        toastError(error);
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
      toastError(error);
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
  // applied : changement déjà fait (taille du texte, fenêtre redimensionnée) ; la place de l'élément lu avant lui est
  // alors celle des positions retenues (une mesure donnerait la nouvelle mise en page, sections masquées encore à
  // l'ancienne hauteur). Le recalage de WebKit (ancrage du défilement) est suspendu : il s'ajouterait au nôtre.
  keepReadingPosition(change = () => {}, { applied = false } = {}) {
    const kept = applied && this.anchors;
    const anchor = this.currentAnchor();
    const entry = kept && kept.find((a) => a.el === anchor);
    const top = entry ? entry.y - scrollY : anchor?.getBoundingClientRect().top;
    const root = document.documentElement;
    root.style.overflowAnchor = "none";
    requestAnimationFrame(() => requestAnimationFrame(() => { root.style.overflowAnchor = ""; }));
    this.relayout(() => {
      change();
      this.anchors = null;
      layoutFigures(this.figures);
      this.markWideMath();
    });
    if (anchor) scrollBy({ top: anchor.getBoundingClientRect().top - top, behavior: "instant" });
  }

  // Nouvelle mise en page de tout l'article : sections lointaines affichées le temps du calcul (lazypaint.js)
  relayout(change) {
    return this.lazyPaint ? this.lazyPaint.relayout(change) : change();
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

  // Largeur du texte, d'un cran (mêmes choix que dans les réglages) ; boutons inactifs aux deux bouts
  changeWidth(delta) {
    const at = Math.max(0, READING_WIDTHS.indexOf(settings.readingWidth));
    const next = READING_WIDTHS[Math.min(READING_WIDTHS.length - 1, Math.max(0, at + delta))];
    if (next !== settings.readingWidth) saveSettings({ readingWidth: next });
  }

  updateWidthButtons() {
    const at = Math.max(0, READING_WIDTHS.indexOf(settings.readingWidth));
    const current = t(`settings.width.${READING_WIDTHS[at]}`);
    for (const [button, key, end] of [[this.narrowerButton, "reader.narrower", at === 0], [this.widerButton, "reader.wider", at === READING_WIDTHS.length - 1]]) {
      const label = `${t(key)} (${current})`;
      button.title = label;
      button.setAttribute("aria-label", label);
      button.setAttribute("aria-disabled", String(end));
    }
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
    if (target) this.navigateElement(target.closest(".ltx_theorem, .ltx_para, .ltx_bibitem, .ltx_equation, .eq-scroll, .ltx_figure, .ltx_table, .ltx_proof, section") || target, options);
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
    element.getBoundingClientRect(); // recalcul : l'animation repart (offsetWidth n'existe pas pour un élément SVG)
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
    this.followStatementsChapter();
    // Énoncé courant dans la liste
    const theorem = anchor.closest(".ltx_theorem");
    this.panel.querySelectorAll(".node-item.current").forEach((el) => el.classList.remove("current"));
    if (theorem) this.panel.querySelector(`.node-item[data-id="${CSS.escape(theorem.id)}"]`)?.classList.add("current");
    this.savePosition(anchor.id);
  }

  // Livre : chapitre de l'endroit lu (onglet IA : résumé, relecture de ce chapitre par défaut) ; dans le PDF : aucun
  readingChapter() {
    const ids = new Set((this.structure.chapters || []).map((c) => c.id));
    for (let element = this.pdfMode ? this.find(this.readingId || this.startAt) : this.currentAnchor(); element; element = element.parentElement?.closest("[id]")) {
      if (ids.has(element.id)) return element.id;
    }
    return null;
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
    const id = link.dataset.ref || link.getAttribute("href")?.slice(1);  // renvoi d'une réponse de l'IA : son identifiant
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
    const open = h("a", { class: "button small-button cross-open", href: `#/paper/${encodeURIComponent(entry.paper)}${node ? `/${encodeURIComponent(node.id)}` : ""}`,
      title: t("reader.crossOpenTab") }, node ? t("reader.crossOpenStatement", { label: node.label }) : t("reader.crossOpen"));
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
          await typesetTex([holder]);
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
    copy.querySelectorAll(".proof-toggle, .block-note, .state-pill, .proof-pointer, .bib-links").forEach((el) => el.remove());
    copy.classList.remove("selected", "flash");
    return copy;
  }

  // Liens qui retrouvent une référence (DOI, MathSciNet, zbMATH) : dans le navigateur, seulement au clic
  findLinks(entry, { doi = true } = {}) {
    const plain = (entry.text || "").replace(/\b(?:doi:\s*|https?:\/\/|arXiv:\s*)\S+/gi, "").replace(/\s+/g, " ").trim();
    const citation = encodeURIComponent([...plain].slice(0, 500).join(""));  // par caractères : jamais une moitié de symbole
    const link = (label, href) => h("a", { href, target: "_blank", rel: "noopener noreferrer", title: t("reader.openOutside") }, label);
    return [doi && entry.doi && link("DOI", `https://doi.org/${entry.doi.split("/").map(encodeURIComponent).join("/")}`),
      citation && link("MathSciNet", `https://mathscinet.ams.org/mref?ref=${citation}&dataType=mathscinet`),
      citation && link("zbMATH", `https://zbmath.org/citationmatching/?q=${citation}`)].filter(Boolean);
  }

  // L'article lui-même dans zbMATH et MathSciNet (puces de l'en-tête, à côté du DOI). zbMATH : par DOI, sinon par
  // numéro arXiv (zbMATH recense aussi beaucoup de prépublications). MathSciNet : sa recherche de citations (gratuite),
  // avec auteurs, titre, revue et DOI connus. Ce sont des recherches : le site dit lui-même s'il ne trouve rien
  paperFindLinks(paper) {
    const chip = (label, href) => h("a", { class: "chip", href, target: "_blank", rel: "noopener noreferrer", title: t("reader.openOutside") }, label);
    const journal = (paper.journal_ref || "").trim();
    const arxivId = (paper.arxiv_id || "").replace(/v\d+$/, "");  // avec « v2 », zbMATH ne trouve rien
    const zbQuery = paper.doi ? `doi:${paper.doi}` : arxivId ? `arxiv:${arxivId}` : "";
    const citation = paper.title && [(paper.authors || []).join(", "), paper.title, journal, paper.doi && `doi:${paper.doi}`]
      .filter(Boolean).join(", ").replace(/\s+/g, " ").trim();
    return [citation && chip("MathSciNet", `https://mathscinet.ams.org/mref?ref=${encodeURIComponent([...citation].slice(0, 500).join(""))}&dataType=mathscinet`),
      zbQuery && chip("zbMATH", `https://zbmath.org/?q=${encodeURIComponent(zbQuery)}`)]
      .filter(Boolean);
  }

  // Ligne de liens sous chaque entrée de la bibliographie, en fin d'entrée : les ancres des surlignages, comptées
  // depuis le début, ne bougent pas ; hors sélection, et retirée des aperçus (clean)
  addFindLinks() {
    for (const item of this.content.querySelectorAll(".ltx_bibitem[id]")) {
      const entry = this.bib.get(item.id);
      if (!entry || item.querySelector(".bib-links")) continue;
      const links = this.findLinks(entry, { doi: !item.querySelector('a[href*="doi.org/"]') });
      if (links.length) item.append(h("div", { class: "bib-links" }, links));
    }
  }

  // Référence : l'importer depuis arXiv, ou la retrouver
  bibActions(entry) {
    if (!entry) return null;
    return h("span", { class: "bib-actions" }, (entry.arxiv || []).map((arxivId) => h("button", {
      type: "button",
      onclick: async () => {
        try {
          const paper = await api.importPaper(arxivId);
          toast(`${t("reader.import")} ✓ ${paper.title}`);
        } catch (error) {
          toastError(error);
        }
      },
    }, `${t("reader.import")} arXiv:${arxivId}`)), ...this.findLinks(entry));
  }

  // Identifiants arXiv des références (repérés par l'analyse) : liens qui ouvrent l'article dans l'application
  linkArxivReferences() {
    for (const item of this.content.querySelectorAll(".ltx_bibitem[id]")) {
      const ids = this.bib.get(item.id)?.arxiv || [];
      if (!ids.length) continue;
      for (const link of item.querySelectorAll('a[href*="arxiv.org/"]')) {
        const id = ids.find((arxivId) => link.href.includes(arxivId));
        if (!id) continue;
        link.dataset.arxiv = id;
        link.title = t("reader.readArxiv");
      }
      // ancien identifiant : écrit aussi avec sa sous-catégorie dans le texte (math.DG/0404494 pour math/0404494)
      const written = (id) => escapeRegExp(id).replace(/^([a-z-]+)\\\//, "$1(?:\\.[A-Z]{2})?\\/");
      const pattern = new RegExp(`(?:arXiv:\\s*)?(?<![\\w.])(${ids.map(written).join("|")})(?:v\\d+)?(?![\\w])`, "g");
      const walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT,
        { acceptNode: (node) => (node.parentElement.closest("a, math") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
      const texts = [];
      for (let node = walker.nextNode(); node; node = walker.nextNode()) texts.push(node);
      for (const text of texts) {
        const matches = [...text.data.matchAll(pattern)];
        if (!matches.length) continue;
        const parts = [];
        let last = 0;
        for (const match of matches) {
          const id = match[1].replace(/^([a-z-]+)\.[A-Z]{2}\//, "$1/");
          parts.push(text.data.slice(last, match.index), h("a", { href: `https://arxiv.org/abs/${id}`, title: t("reader.readArxiv"), dataset: { arxiv: id } }, match[0]));
          last = match.index + match[0].length;
        }
        text.replaceWith(...parts, text.data.slice(last));
      }
      // identifiant pas affiché (mise en page AMS, qui ne reprend pas les « External Links » de LaTeXML ; lien
      // seulement sur le titre) : « , arXiv:… » en fin de référence
      const shown = [...item.querySelectorAll("a[data-arxiv]")].filter((link) => link.textContent.replace(/^(.*?[a-z-]+)\.[A-Z]{2}\//, "$1/").includes(link.dataset.arxiv)).map((link) => link.dataset.arxiv);
      const block = item.querySelector(".ams-bib") || [...item.querySelectorAll(".ltx_bibblock:not(.ltx_bib_cited)")].at(-1);
      for (const id of ids.filter((arxivId) => !shown.includes(arxivId))) {
        if (!block) break;
        const end = block.lastChild?.nodeType === Node.TEXT_NODE ? block.lastChild : null;
        const stop = end && /\.\s*$/.test(end.data) ? "." : "";
        if (stop) end.data = end.data.replace(/\.\s*$/, "");
        block.append(", ", h("a", { href: `https://arxiv.org/abs/${id}`, title: t("reader.readArxiv"), dataset: { arxiv: id } }, `arXiv:${id}`), stop);
      }
    }
  }

  // Dessin absent de la version HTML : avertissement, et le PDF à cet endroit
  pictureNotice() {
    const open = h("button", { type: "button" }, t("reader.pictureInPdf"));
    // ajout de l'application dans l'article : ni surligné ni compté dans les surlignages, et dans la langue de l'interface
    const notice = h("div", { class: "notice picture-missing", lang: document.documentElement.lang || null },
      h("span", { class: "reason-icon", html: icons.warning }), t("reader.pictureMissing"), open);
    open.addEventListener("click", () => {
      notice.scrollIntoView({ block: "start", behavior: "instant" }); // la bascule ouvre le PDF à l'endroit lu
      this.setView("pdf");
    });
    return notice;
  }

  // Article de la bibliothèque cité (« Ouvrir l'article », « Ouvrir Theorem 4.3 » : aperçu, bibliographie) : dans un
  // nouvel onglet, l'article lu reste dans le sien ; au premier plan, ⌘-clic en arrière-plan (⇧ : au premier plan quand
  // même). Clic du milieu : le gestionnaire commun de l'application (app.js). Appelé par interceptLinks : la pastille
  // est aussi recopiée dans les aperçus de la bibliographie (clean), sans les écouteurs de l'original
  openCross(link, event) {
    event.preventDefault();
    this.hidePreview();
    openRoute(link.getAttribute("href"), true, !wantsNewTab(event) || event.shiftKey);
  }

  // Article cité ouvert dans l'application, dans un nouvel onglet (l'article lu reste à sa place dans le sien) :
  // au premier plan ; ⌘-clic, en arrière-plan (⇧ : au premier plan quand même)
  async readArxiv(link, event) {
    const front = !wantsNewTab(event) || event.shiftKey;
    if (link.dataset.loading) return;
    link.dataset.loading = "1";
    try {
      const paper = await api.importPaper(link.dataset.arxiv, false);
      const app = Boolean(nativeBridge); // navigateur : pas de fenêtre ouverte après un await
      openRoute(`#/paper/${encodeURIComponent(paper.id)}`, app, front);
    } catch (error) {
      toastError(error);
    } finally {
      delete link.dataset.loading;
    }
  }

  // Lien « #… » cliqué hors de l'article lui-même (énoncé recopié, aperçu) : navigation interne
  interceptLinks = (event) => {
    const arxiv = event.target.closest("a[data-arxiv]");
    if (arxiv && !event.defaultPrevented) {
      event.preventDefault();
      this.hidePreview();
      this.readArxiv(arxiv, event);
      return;
    }
    const cross = event.target.closest("a.cross-open");
    if (cross && !event.defaultPrevented) {
      this.openCross(cross, event);
      return;
    }
    const link = event.target.closest('a[href^="#"]');
    if (!link || event.defaultPrevented || link.matches(".ai-ref") || link.closest(".tabs")) return;
    if (link.getAttribute("href").startsWith("#/")) { this.hidePreview(); return; } // autre page de l'application (autre article)
    event.preventDefault();
    this.hidePreview();
    this.navigate(decodeURIComponent(link.getAttribute("href").slice(1)));
  };

  showPreview(link) {
    if (this.picking || !link.isConnected) return; // lien remplacé entre-temps (réponse de l'IA en cours d'écriture)
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
    // défilement le curseur sur l'aperçu : son contenu s'il dépasse, jamais l'article. Sinon (bulle fixe qui ne défile
    // pas, ou bout du contenu atteint), le geste passait au document : l'article défilait, l'aperçu se fermait et
    // l'élan du geste continuait sur l'article, d'un coup. Pincer (Ctrl, ⌘) : laissé au navigateur.
    box.addEventListener("wheel", (event) => {
      if (event.ctrlKey || event.metaKey) return;
      for (let element = event.target; element && box.contains(element); element = element.parentElement) {
        if (canScroll(element, event.deltaX, event.deltaY)) return;
      }
      event.preventDefault();
    }, { passive: false });
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
      : active?.matches(".node-item[data-id]") ? `.node-item[data-id="${CSS.escape(active.dataset.id)}"]`
      : active?.matches(".ai-view") ? '.ai-view[aria-pressed="true"]'   // sous-onglet de l'IA
      : active?.closest(".ai-bottom") ? ".ai-question, .ai-launch" : null; // saisie de l'IA (envoi, arrêt)

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

  // Onglet IA : marqué sur le panneau, pas sur la racine (tout l'article serait recalculé : ~180 ms à chaque passage) ;
  // panneau plus large si l'on n'a pas choisi de largeur, sans perdre l'endroit où l'on lit
  setPanelWide(wide) {
    if ((this.panel.dataset.tab === "ai") === wide) return;
    const apply = () => {
      if (wide) this.panel.dataset.tab = "ai"; else delete this.panel.dataset.tab;
      this.applyPanelWidth();
    };
    if (!settings.panelWidth && this.figures && this.root.dataset.panel === "open" && innerWidth >= MEDIUM) this.keepReadingPosition(apply);
    else apply();
  }

  // Largeur du panneau : celle choisie à la poignée (`width`, sinon celle des réglages), sinon celle par défaut
  applyPanelWidth(width = settings.panelWidth) {
    const value = width || (this.panel.dataset.tab === "ai" ? AI_PANEL_W : null);
    if (value) this.root.style.setProperty("--panel-w", `${value}px`);
    else this.root.style.removeProperty("--panel-w");
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
    if (this.texFile) {
      const checks = this.texChecks();
      if (checks) body.append(checks);
    }
    if (!this.nodes.length) {
      body.append(h("p", { class: "hint" }, t(this.paper.source === "local-pdf" ? "panel.noStatementsPdf" : "panel.noStatements")));
      return;
    }
    const search = h("input", { class: "text-input", type: "search", value: this.search, placeholder: t("panel.filter"), "aria-label": t("panel.filter") });
    const kinds = [...new Set(this.nodes.map((n) => n.kind))];
    const list = h("div", {});
    // Livre : un chapitre à la fois (par défaut celui qu'on lit ; celui de l'énoncé choisi), ou tout le livre
    let chapterMenu = null;
    if (this.structure.book && !this.onlyIds && this.bookChapters().size > 1) {
      // tant qu'on n'a pas choisi dans le menu, il suit la lecture (followStatementsChapter)
      if (!this.statementsChosen) this.statementsChapter = this.chapterOfReading() || this.statementsChapter || this.chapters.keys().next().value;
      // énoncé choisi ailleurs (texte, graphe) : son chapitre, une fois ; un choix fait ensuite dans le menu est gardé
      const selected = this.selected && this.selected !== this.statementsFollowed && this.chapterOfNode(this.selected);
      this.statementsFollowed = this.selected;
      if (selected && this.statementsChapter) this.statementsChapter = selected;
      chapterMenu = h("select", { class: "chapter-select statements-chapter", "aria-label": t("graph.chapter"), title: t("graph.chapter"),
        onchange: () => {
          this.statementsChapter = chapterMenu.value;
          this.statementsChosen = true;
          this.chosenWhileReading = this.chapterOfReading();
          draw();
          chapterMenu.focus();
        } },
      h("option", { value: "" }, t("panel.allChapters", { n: this.nodes.length })),
      [...this.chapters].map(([id, chapter]) => h("option", { value: id }, `${chapter.title || id} (${chapter.ids.size})`)));
      chapterMenu.value = this.statementsChapter || "";
    }
    const draw = () => {
      const needle = this.search.trim().toLowerCase();
      // une recherche porte sur tout le livre (résultats rangés par chapitre) ; sinon, le chapitre du menu
      const chapter = chapterMenu && !needle && this.statementsChapter ? this.chapters.get(this.statementsChapter)?.ids : null;
      const shown = this.nodes.filter((n) => (!chapter || chapter.has(n.id)) && (!this.kindFilter.size || this.kindFilter.has(n.kind))
        && (!needle || `${n.label} ${n.name}`.toLowerCase().includes(needle)) && (!this.onlyIds || this.onlyIds.has(n.id)));
      const sections = new Map(this.structure.toc.map((s) => [s.id, s]));
      const groups = new Map();
      for (const node of shown) {
        const top = this.topSection(node.section, sections);
        if (!groups.has(top)) groups.set(top, []);
        groups.get(top).push(node);
      }
      // le menu prend la place du titre du chapitre montré (tout le livre : au-dessus des chapitres)
      const menu = chapterMenu && !needle;
      list.replaceChildren(...(menu && !chapter ? [chapterMenu] : []), ...[...groups].map(([sectionId, items]) => h("div", {},
        menu && chapter ? chapterMenu : h("div", { class: "node-group" }, withMath(sections.get(sectionId)?.title ?? "")),
        h("div", { class: "node-list" }, items.map((n) => this.nodeItem(n))))));
      if (menu && chapter && !groups.size) list.replaceChildren(chapterMenu);  // filtres : aucun énoncé du chapitre
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
    // vérifications d'un fichier TeX : seulement les énoncés concernés, jusqu'à « Tout afficher »
    const showAll = this.onlyIds && h("button", { class: "kind-filter", type: "button", "aria-pressed": "true",
      onclick: () => { this.onlyIds = null; this.renderPanel(); } }, t("tex.showAll"));
    if (showAll) filters.prepend(showAll);
    body.append(filters, list);
    draw();
  }

  // section de premier niveau (chapitre d'un livre) qui contient la section `sectionId`
  topSection(sectionId, sections) {
    const top = this.structure.book ? 0 : 1;
    let id = sectionId;
    while (id) {
      const entry = sections.get(id);
      if (entry && entry.level <= top) return id;
      const parent = this.find(id)?.parentElement?.closest("section[id]");
      if (!parent && entry) break;  // lu dans le PDF (pas de HTML) : d'après l'ordre du sommaire
      id = parent?.id;
    }
    const toc = this.structure.toc;
    for (let index = toc.findIndex((entry) => entry.id === sectionId); index >= 0; index--) {
      if (toc[index].level <= top) return toc[index].id;
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

  // Livre : graphe d'un chapitre à la fois (celui de l'énoncé choisi, sinon le premier qui a des énoncés), avec les
  // énoncés d'autres chapitres qu'il utilise directement ; un graphe de tout le livre serait illisible
  graphNodes() {
    if (!this.structure.book) return this.sectionGraph();
    this.bookChapters();
    const selected = this.selected && this.byId.get(this.selected);
    if (selected && (!this.graphChapter || !this.chapters.get(this.graphChapter)?.ids.has(selected.id))) this.graphChapter = this.chapterOfNode(selected.id);
    if (!this.chapters.has(this.graphChapter)) this.graphChapter = this.chapters.keys().next().value;
    if (!this.chapterNodes.has(this.graphChapter)) {
      const own = this.chapters.get(this.graphChapter)?.ids || new Set();
      const used = new Set(this.nodes.filter((n) => own.has(n.id)).flatMap((n) => n.uses));
      this.chapterNodes.set(this.graphChapter, this.nodes.filter((n) => own.has(n.id) || used.has(n.id)).map((n) => (own.has(n.id)
        ? { ...n, uses: n.uses.filter((id) => own.has(id) || used.has(id)), usedBy: n.usedBy.filter((id) => own.has(id)) }
        : { ...n, uses: [], usedBy: n.usedBy.filter((id) => own.has(id)) })));
    }
    return this.chapterNodes.get(this.graphChapter);
  }

  // Article : tout l'article (par défaut), ou une section : ses énoncés, ceux qui y sont prouvés et ceux d'autres sections
  // qu'ils utilisent directement (mêmes tableaux d'un affichage à l'autre, comme pour un livre : disposition gardée)
  sectionGraph() {
    if (this.graphSection && this.selected) {  // énoncé choisi ailleurs : sa section
      const selected = this.byId.get(this.selected);
      if (selected && !this.sectionIds(this.graphSection).has(selected.id)) this.graphSection = selected.chapter || "";
    }
    const section = this.graphSection;
    if (!section) return this.nodes;
    if (this.sectionNodes?.source !== this.nodes) this.sectionNodes = { source: this.nodes, graphs: new Map() };
    if (!this.sectionNodes.graphs.has(section)) {
      const own = this.sectionIds(section);
      const used = new Set(this.nodes.filter((n) => own.has(n.id)).flatMap((n) => n.uses));
      this.sectionNodes.graphs.set(section, this.nodes.filter((n) => own.has(n.id) || used.has(n.id)).map((n) => (own.has(n.id)
        ? { ...n, uses: n.uses.filter((id) => own.has(id) || used.has(id)), usedBy: n.usedBy.filter((id) => own.has(id)) }
        : { ...n, uses: [], usedBy: n.usedBy.filter((id) => own.has(id)) })));
    }
    return this.sectionNodes.graphs.get(section);
  }

  sectionIds(section) {
    return new Set(this.nodes.filter((n) => n.chapter === section || n.provedIn?.includes(section)).map((n) => n.id));
  }

  // Article : sections qui ont au moins un énoncé
  graphSections() {
    return (this.structure.sections || []).filter((s) => this.nodes.some((n) => n.chapter === s.id || n.provedIn?.includes(s.id)));
  }

  // Livre : énoncés par chapitre (section de premier niveau), dans l'ordre du livre ; onglets Énoncés et Graphe
  bookChapters() {
    if (!this.chapters) {
      const sections = new Map(this.structure.toc.map((s) => [s.id, s]));
      this.chapters = new Map();
      for (const node of this.nodes) {
        const id = this.topSection(node.section, sections);
        if (!this.chapters.has(id)) this.chapters.set(id, { title: sections.get(id)?.title || "", ids: new Set() });
        this.chapters.get(id).ids.add(node.id);
      }
      this.chapterNodes = new Map();  // mêmes tableaux d'un affichage à l'autre : disposition du graphe gardée
    }
    return this.chapters;
  }

  chapterOfNode(id) {
    for (const [chapter, { ids }] of this.bookChapters()) if (ids.has(id)) return chapter;
    return null;
  }

  // Livre, onglet Énoncés : le menu suit le chapitre lu, en défilant (HTML ou PDF). Un chapitre choisi dans le menu
  // reste affiché tant qu'on lit le même chapitre ; passé à un autre chapitre, le menu suit de nouveau la lecture.
  // « Tout le livre » choisi, ou une recherche en cours (tout le livre aussi) : rien ne change.
  followStatementsChapter() {
    if (this.tab !== "statements" || !this.structure.book || !this.chapters || this.chapters.size < 2 || this.onlyIds
      || this.search.trim()) return;
    const chapter = this.chapterOfReading();
    if (!chapter) return;
    if (this.statementsChosen) {
      if (!this.statementsChapter || chapter === this.chosenWhileReading) return;
      this.statementsChosen = false;
    }
    if (chapter === this.statementsChapter) return;
    this.statementsChapter = chapter;
    this.renderPanel();
  }

  // chapitre de l'endroit lu (dans le PDF : dernier endroit retrouvé dans le HTML), s'il a des énoncés
  chapterOfReading() {
    const element = this.find(this.pdfMode ? this.readingId || this.startAt : this.currentAnchor()?.id);
    const section = element?.closest("section[id]")?.id;
    if (!section) return null;
    const chapter = this.topSection(section, new Map(this.structure.toc.map((s) => [s.id, s])));
    return this.bookChapters().has(chapter) ? chapter : null;
  }

  chapterSelect() {
    if (!this.structure.book) {
      const sections = this.graphSections();
      if (sections.length < 2) return null;
      const menu = h("select", { class: "chapter-select", "aria-label": t("graph.chapter"), title: t("graph.chapter") },
        h("option", { value: "" }, t("panel.wholePaper", { n: this.nodes.length })),
        sections.map((s) => h("option", { value: s.id }, plainTitle(s.title))));
      menu.value = this.graphSection || "";
      menu.addEventListener("change", () => { this.graphSection = menu.value; this.selected = null; this.renderPanel(); });
      return menu;
    }
    if (!this.chapters || this.chapters.size < 2) return null;
    const select = h("select", { class: "chapter-select", "aria-label": t("graph.chapter") },
      [...this.chapters].map(([id, chapter]) => h("option", { value: id }, chapter.title || id)));
    select.value = this.graphChapter;
    select.addEventListener("change", () => { this.graphChapter = select.value; this.selected = null; this.renderPanel(); });
    return select;
  }

  renderGraphTab(body) {
    const graph = renderGraph(this.graphNodes(), {
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
      h("div", { class: "graph-tools" }, this.chapterSelect(), h("label", {}, isolated, t("graph.isolated")), h("label", {}, remarks, t("graph.remarks")), expand),
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
    const draw = () => renderGraph(this.graphNodes(), {
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
    // ⌘F : recherche dans le texte HTML, ou celle de pdf.js quand on lit le PDF
    this.cleanups.push(setFindHandler((command) => {
      if (this.pdfMode) { if (command === "open") this.pdf?.find(); } else this.finder.command(command);
    }), () => this.finder.close());

    const zoom = (event) => {
      const figure = event.target.closest?.(".figure-card") || event.target.closest?.("img.ltx_graphics");
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

    // aperçu au survol d'un renvoi : dans l'article, et dans les réponses de l'IA (énoncés, équations, sections, références)
    const SHOW_DELAY = 220; // ms : un passage de la souris ne l'ouvre pas
    const hoverPreviews = (host, selector, accept = () => true) => {
      host.addEventListener("mouseover", (event) => {
        const link = event.target.closest(selector);
        if (!link || !accept(link) || link.contains(event.relatedTarget)) return; // dans le même renvoi
        if (this.previewBox && this.previewLink === link) { clearTimeout(this.hideTimer); return; }
        clearTimeout(this.hideTimer);
        clearTimeout(this.showTimer);
        this.showTimer = setTimeout(() => this.showPreview(link), SHOW_DELAY);
      });
      host.addEventListener("mouseout", (event) => {
        const link = event.target.closest(selector);
        if (link && !link.contains(event.relatedTarget)) {
          clearTimeout(this.showTimer);
          this.scheduleHide();
        }
      });
    };
    hoverPreviews(this.scroller, 'a[href^="#"], .ltx_note_mark', (link) => this.content.contains(link));
    hoverPreviews(this.panel, "a.ai-ref");

    const onKey = (event) => {
      if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
      if (event.key === "Escape") { this.hidePreview(); this.closeDrawers(); }
      if (event.key.toLowerCase() === "d" && (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey) {
        event.preventDefault();  // ⌘D : marque-page ici (pas le signet du navigateur)
        this.addBookmark();
      }
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
      }, { applied: true });
    }, 200);
    document.addEventListener("keydown", onKey);
    addEventListener("resize", onResize);
    // Taille, largeur ou police changées : même passage à l'écran après la nouvelle mise en page
    let look = `${settings.fontSize}/${settings.readingWidth}/${settings.paperFont}`;
    let assistant = currentModel(settings);
    this.updateZoomButton();
    this.updateWidthButtons();
    const unsubscribe = onSettingsChange(() => {
      if (currentModel(settings) !== assistant) { // assistant IA changé (un autre onglet de lecture, menu de l'onglet IA) : à jour
        assistant = currentModel(settings);
        if (this.tab === "ai") this.renderPanel();
      }
      this.updateZoomButton();
      this.updateWidthButtons();
      this.article.dataset.marks = settings.marginMarks ? "on" : "off";
      const now = `${settings.fontSize}/${settings.readingWidth}/${settings.paperFont}`;
      if (now !== look) { look = now; this.keepReadingPosition(undefined, { applied: true }); } else this.anchors = null;
    });
    // réglages de l'IA quittés, compte changé : assistants prêts revérifiés (onglet IA fermé : celui utilisé, pour la prochaine demande)
    const onAccounts = () => { if (this.tab === "ai") this.renderPanel(); else this.assistant?.refreshStatus(); };
    addEventListener("arxivreader:ai-accounts", onAccounts);
    this.cleanups.push(() => document.removeEventListener("keydown", onKey), () => removeEventListener("resize", onResize), unsubscribe,
      () => removeEventListener("arxivreader:ai-accounts", onAccounts));
  }

  // surlignages et zones du PDF ; une zone peut partir vers l'assistant s'il lit les images (Claude, Gemini)
  makePdfNotes() {
    setTimeout(() => { if (this.assistant && !this.assistant.status) this.assistant.refreshStatus(); }, 0);
    return new PdfNotes({
      annotator: this.annotator, view: this.pdf, onAskAI: this.annotator.onAskAI,
      readsImages: () => Boolean(this.assistant?.readsImages()),
      onAskZone: (zone) => this.assistant?.setZone(zone),
      onTranscribe: (zone) => this.assistant?.ask({ task: "transcribe", zone }),
      onAddBookmark: (at) => this.addBookmark(at),
      onBookmark: (annotation, rect) => this.editBookmark(annotation, rect),
      onContextMenu: (point, items) => contextMenuAt(point, items),
      relocate: this.texFile,  // fichier TeX recompilé : passages surlignés retrouvés s'ils ont bougé
    });
  }

  // ---------- Marque-pages (bookmarks.js) ----------

  // `at` : endroit d'un clic droit ({element} en HTML, {pdf: {page, top}} dans le PDF, et `rect` du clic) ; sinon
  // (⌘D) l'endroit lu : paragraphe en haut de l'article, page et hauteur dans le PDF
  async addBookmark(at = null) {
    const paper = this.paper;
    let target, name, rect = at?.rect;
    if (at?.pdf || (!at && this.pdfMode)) {
      const spot = at?.pdf || this.pdf?.location();
      if (!spot) return;
      target = { pdf: { page: spot.page, top: spot.top } };
      name = t("bookmarks.page", { page: spot.page });
      rect ||= this.toolbar.getBoundingClientRect();  // fenêtre du nom sous la barre du lecteur
    } else {
      const read = this.currentAnchor();
      const anchor = at?.element || read?.closest(".ltx_theorem[id]") || read;  // dans un énoncé : l'énoncé
      if (!anchor) return;
      target = { id: anchor.id };
      name = this.bookmarkName(anchor);
      if (!rect) {  // ⌘D : la fenêtre du nom sous le haut du paragraphe (visible même s'il est long)
        const box = anchor.getBoundingClientRect();
        const top = Math.max(box.top, this.viewTop());
        rect = { left: box.left, width: box.width, top, bottom: top + 24 };
      }
    }
    bookmarkBox({ heading: t("bookmarks.new"), value: name, rect, onSave: async (label) => {
      try {
        const annotation = await api.addAnnotation(paper.id, { kind: "bookmark", quote: (label || name).slice(0, 300),
          target: { ...target, version: paper.version ?? null } });
        this.annotator.items.push(annotation);
        this.paintBookmarks();
        this.annotator.onChange();
        toast(t("bookmarks.added"));
      } catch (error) { toastError(error); }
    } });
  }

  // clic sur un ruban : renommer ou supprimer
  // posé sur une version antérieure : l'enregistrer le confirme pour la version actuelle (le signalement disparaît)
  editBookmark(annotation, rect) {
    const was = staleVersion(annotation, this.paper);
    bookmarkBox({ heading: was ? `${t("bookmarks.edit")} · ${t("bookmarks.stale", { was })}` : t("bookmarks.edit"),
      value: annotation.quote || "", rect, saveLabel: t("library.folder.save"),
      onSave: async (label) => {
        try {
          Object.assign(annotation, await api.updateAnnotation(annotation.id, { quote: label || annotation.quote || t("bookmarks.untitled"),
            ...(was ? { version: this.paper.version } : {}) }));
          this.paintBookmarks();
          this.annotator.onChange();
        } catch (error) { toastError(error); }
      },
      onDelete: async () => {
        try {
          await api.deleteAnnotation(annotation.id);
          this.annotator.items = this.annotator.items.filter((a) => a !== annotation);
          this.paintBookmarks();
          this.annotator.onChange();
        } catch (error) { toastError(error); }
      } });
  }

  // rubans : dans la marge de l'article (HTML) et dans celle des pages (PDF)
  paintBookmarks() {
    this.content.querySelectorAll(".bm-ribbon").forEach((ribbon) => ribbon.remove());
    this.content.querySelectorAll(".bm-anchor").forEach((element) => element.classList.remove("bm-anchor"));
    for (const annotation of this.annotator.items.filter((a) => a.kind === "bookmark" && !bookmarkInPdf(a))) {
      const element = this.find(annotation.target?.id);
      if (!element) continue;
      const label = annotation.quote || t("bookmarks.untitled");
      const ribbon = h("button", { class: "bm-ribbon", type: "button", title: label, "aria-label": label,
        onclick: (event) => { event.stopPropagation(); this.editBookmark(annotation, event.currentTarget.getBoundingClientRect()); } });
      element.classList.add("bm-anchor");
      element.prepend(ribbon);
    }
    this.pdfNotes?.repaintBookmarks();
  }

  // onglet Notes : y aller, dans la vue où il a été posé
  async goToBookmark(annotation) {
    if (bookmarkInPdf(annotation)) {
      if (!this.pdfMode && !this.texOnly) await this.setView("pdf", { save: false });
      this.pdf?.goTo(annotation.target.pdf.page, annotation.target.pdf.top ?? null);
      return;
    }
    if (this.pdfMode && !this.texOnly) await this.setView("html", { save: false });
    const element = this.find(annotation.target.id);
    if (element) this.navigateElement(element);
  }

  // nom proposé : l'énoncé (« Theorem 2.3 ») ou la section (« §3 Strongly pseudoconvex domains »)
  bookmarkName(anchor) {
    const theorem = anchor.closest(".ltx_theorem[id]");
    const node = theorem && this.nodes.find((n) => n.id === theorem.id);
    if (node) return node.label;
    const title = anchor.closest("section")?.querySelector(":scope > .ltx_title");
    return title ? title.textContent.replace(/\s+/g, " ").trim().slice(0, 80) : t("bookmarks.untitled");
  }

  // section d'un paragraphe, en bref (« §3.2 »), pour la liste
  sectionTag(element) {
    const tag = element?.closest("section")?.querySelector(":scope > .ltx_title .ltx_tag");
    const text = tag?.textContent.trim().replace(/[.\s]+$/, "");
    return text ? (/^\d/.test(text) ? `§${text}` : text) : "";
  }

  // Pages du PDF autour de `center` (page affichée si null) : 7 avant, 3 après, en JPEG d'environ 1 400 px, pour
  // l'assistant quand l'article n'a pas de texte (scan) ; rendues à la demande, rien n'est gardé
  async contextPages(center, signal = null) {
    const app = await this.pdf?.ready();
    const doc = app?.pdfDocument;
    if (!doc) return [];
    center = center || app.pdfViewer.currentPageNumber || 1;
    const pages = [];
    for (let number = Math.max(1, center - 7); number <= Math.min(doc.numPages, center + 3) && !signal?.aborted; number++) {
      const page = await doc.getPage(number);
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: 1400 / Math.max(base.width, base.height) });
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(viewport.width);
      canvas.height = Math.round(viewport.height);
      const context = canvas.getContext("2d");
      context.fillStyle = "#fff";  // fond blanc (JPEG sans transparence)
      context.fillRect(0, 0, canvas.width, canvas.height);
      await page.render({ canvasContext: context, canvas, viewport }).promise;
      pages.push({ page: number, image: canvas.toDataURL("image/jpeg", 0.75) });
      canvas.width = canvas.height = 0;  // mémoire de l'image rendue libérée tout de suite
    }
    return pages;
  }

  // adresse du PDF ; fichier TeX : celle de sa dernière compilation (jamais l'ancien PDF gardé en cache)
  pdfUrl() {
    const stamp = this.texFile && this.structure.compiledAt ? `?v=${encodeURIComponent(this.structure.compiledAt)}` : "";
    return `/api/papers/${encodeURIComponent(this.paper.id)}/pdf${stamp}`;
  }

  // ---- Fichier TeX de l'ordinateur ----
  async loadTexReport() {
    this.texReport = (await api.texReport(this.paper.id).catch(() => ({}))).report || null;
    if (!this.destroyed && this.tab === "statements") this.renderPanel();
  }

  // note créée, modifiée ou supprimée : bilan relu un peu après (plusieurs changements d'affilée : une seule lecture)
  // panneau redessiné seulement si le compte change (sinon une note en cours de saisie serait perdue)
  texReportSoon() {
    clearTimeout(this.texReportTimer);
    this.texReportTimer = setTimeout(async () => {
      if (this.destroyed) return;
      const before = this.texReport?.notesToCheck || 0;
      const report = (await api.texReport(this.paper.id).catch(() => ({}))).report;
      if (!report || this.destroyed) return;
      this.texReport = report;
      if (this.tab === "statements" && (report.notesToCheck || 0) !== before) this.renderPanel();
    }, 400);
  }

  texChecks() {
    return texChecks(this.texReport, this.nodes, {
      hasProof: (node) => this.hasProof(node),
      show: (ids) => { this.onlyIds = new Set(ids); this.renderPanel(); },
      onAdaptNotes: () => this.adaptNotes(),
      onChooseBib: (name) => chooseBibFor(this.paper.id, name, (result) => this.afterCompile(result)),
    });
  }

  // source relue et recompilée ; l'article est réaffiché à la même page du PDF
  async recompile() {
    if (this.recompiling) return;
    this.recompiling = true;
    this.recompileButton?.classList.add("spinning");
    this.recompileButton?.setAttribute("aria-busy", "true");
    try { this.afterCompile(await api.recompile(this.paper.id)); } catch (error) { toastError(error); } finally {
      this.recompiling = false;
      this.recompileButton?.classList.remove("spinning");
      this.recompileButton?.removeAttribute("aria-busy");
    }
  }

  afterCompile(result) {
    if (this.destroyed) return;
    if (!result.report.pdf) {  // échec : l'ancien PDF reste affiché, l'onglet Énoncés dit pourquoi
      this.texReport = result.report;
      this.tab = "statements";
      this.renderPanel();
      toast(t("tex.lastFailed"), { error: true });
      return;
    }
    const page = this.pdf?.frame.contentWindow?.PDFViewerApplication?.pdfViewer?.currentPageNumber;
    try {
      if (page) sessionStorage.setItem(`texPage:${this.paper.id}`, String(page));
      if (result.report.notesToCheck) sessionStorage.setItem(`texNotes:${this.paper.id}`, String(result.report.notesToCheck));
      else sessionStorage.removeItem(`texNotes:${this.paper.id}`);
    } catch { /* stockage indisponible */ }
    dispatchEvent(new HashChangeEvent("hashchange"));
  }

  // texte modifié alors que l'article a des notes (texfile.track_lost_notes) : message après le rechargement, et dans le
  // bilan de l'onglet Énoncés ; l'IA les adapte sur demande (onglet Questions, question écrite d'avance, non envoyée)
  notesToCheckToast() {
    let count = 0;
    try {
      count = Number(sessionStorage.getItem(`texNotes:${this.paper.id}`)) || 0;
      sessionStorage.removeItem(`texNotes:${this.paper.id}`);
    } catch { /* stockage indisponible */ }
    if (count) toast(t("tex.notesToCheck", { n: count }), { duration: 15000, action: { label: t("tex.notesAskAI"), icon: icons.sparkle, onClick: () => this.adaptNotes() } });
  }

  adaptNotes() { this.assistant?.prefill(t("tex.adaptQuestion")); }

  // Passage proposé par l'assistant, cherché dans le PDF par ses mots (lettres et chiffres seulement, sans accents : la
  // source TeX et le texte du PDF diffèrent par les formules, les commandes, la ponctuation et les césures) :
  // { target, text } ou null. Formules et renvois (\ref, \cite) sautés : les morceaux de texte entre eux doivent se
  // suivre de près (d'une page à la suivante aussi), le premier et le dernier sur des mots entiers. `near` (place d'un
  // passage remplacé, {page, start}) : l'occurrence la plus proche ; sans lui, un passage présent plusieurs fois n'est
  // pas choisi au hasard (null).
  async pdfQuote(quote, near = null) {
    const segments = proseOf(quote);
    const kept = segments.map((piece, index) => ({ piece, index })).filter(({ piece }) => piece.length >= 3);
    const pieces = kept.map(({ piece }) => piece);
    // assez de mots pour être sans ambiguïté (les morceaux très courts, souvent pris dans un autre mot, ne comptent pas)
    if (pieces.filter((piece) => piece.length >= 4).join("").length < 12) return null;
    const doc = await this.pdfLetters();
    if (!doc) return null;
    const { text, page, starts, ends, raws, breaks } = doc;
    const letters = pieces.reduce((sum, piece) => sum + piece.length, 0);
    // mot coupé à cette position du texte d'une page ? (pdf.js colle les morceaux de texte, d'une ligne à l'autre
    // aussi : leur jonction sépare deux mots)
    const inWord = (number, at) => /[\p{L}\p{N}]/u.test(raws[number][at - 1] || "") && /[\p{L}\p{N}]/u.test(raws[number][at] || "")
      && !breaks[number].has(at);
    // bord de la citation vérifié seulement s'il est du texte (« $n$th » : « th » peut être collé à la formule ; un
    // morceau trop court, sauté, laisse le premier gardé au milieu d'un mot)
    const checkFirst = kept[0].index === 0;
    const checkLast = kept[kept.length - 1].index === segments.length - 1;
    const found = new Map();  // fin du passage → début le plus proche (le plus serré)
    let tries = 0;
    for (let from = text.indexOf(pieces[0]); from >= 0 && found.size < 50 && tries++ < 5000; from = text.indexOf(pieces[0], from + 1)) {
      // début et fin sur des mots entiers du texte du PDF
      if (checkFirst && inWord(page[from], starts[from])) continue;
      let end = from + pieces[0].length;
      let gaps = 0;
      for (const piece of pieces.slice(1)) {
        // formule entre deux morceaux : 400 lettres au plus (recherche bornée à cette fenêtre)
        const at = text.slice(end, end + 400 + piece.length).indexOf(piece);
        if (at < 0) { end = -1; break; }
        gaps += at;
        end += at + piece.length;
      }
      if (end < 0 || gaps > 2 * letters + 100 || (checkLast && inWord(page[end - 1], ends[end - 1]))) continue;
      found.set(end, from);
    }
    const spans = [...found].map(([end, from]) => [from, end]);
    if (!spans.length || (spans.length > 1 && !near)) return null;
    const distance = ([from]) => Math.abs(page[from] - near.page) * 1e7 + Math.abs(starts[from] - (near.start ?? 0));
    const [from, end] = spans.length > 1 ? spans.reduce((best, span) => (distance(span) < distance(best) ? span : best)) : spans[0];
    const first = page[from];
    const last = page[end - 1];
    const start = starts[from];
    const stop = ends[end - 1];
    // texte lu comme une sélection à la main : fin de ligne et changement de page comptés comme une espace
    const parts = [];
    for (let number = first; number <= last; number++) {
      parts.push(doc.textOf(number, number === first ? start : 0, number === last ? stop : raws[number].length));
    }
    return { target: { page: first, start, endPage: last, end: stop }, text: parts.join(" ").replace(/\s+/g, " ").trim() };
  }

  // Texte de tout le PDF réduit à ses lettres (lettersOf), avec la page et la position dans la page de chacune ; mêmes
  // réglages que la couche de texte de pdf.js : positions identiques à celles des passages surlignés à la main. Lu une
  // fois (la promesse est gardée : deux recherches simultanées ne relisent pas le PDF ; un échec est réessayé)
  pdfLetters() {
    this.pdfLettersDone ||= this.readPdfLetters().then((result) => {
      if (!result) this.pdfLettersDone = null;
      return result;
    }, (error) => {
      this.pdfLettersDone = null;
      throw error;
    });
    return this.pdfLettersDone;
  }

  async readPdfLetters() {
    const app = await this.pdf?.ready();
    const doc = app?.pdfDocument;
    if (!doc) return null;
    const result = { text: "", page: [], starts: [], ends: [], raws: [], breaks: [], eols: [] };
    const parts = [];
    for (let number = 1; number <= doc.numPages; number++) {
      const content = await (await doc.getPage(number)).getTextContent({ includeMarkedContent: true, disableNormalization: true });
      const raw = content.items.map((item) => item.str || "").join("");
      const { text, starts, ends } = lettersOf(raw);
      result.raws[number] = raw;
      result.breaks[number] = new Set();  // débuts des morceaux de texte
      result.eols[number] = new Set();  // fins de ligne (hasEOL de pdf.js)
      content.items.reduce((at, item) => {
        const next = at + (item.str || "").length;
        result.breaks[number].add(at);
        if (item.hasEOL) result.eols[number].add(next);
        return next;
      }, 0);
      parts.push(text);
      for (let i = 0; i < text.length; i++) {
        result.page.push(number);
        result.starts.push(starts[i]);
        result.ends.push(ends[i]);
      }
    }
    result.text = parts.join("");
    // texte d'une page entre deux positions, une espace à chaque fin de ligne
    result.textOf = (number, from, to) => {
      let out = "";
      for (let at = from; at < to; at++) {
        if (at > from && result.eols[number].has(at)) out += " ";
        out += result.raws[number][at];
      }
      return out;
    };
    return result;
  }

  recompiledPage() {
    try {
      const page = Number(sessionStorage.getItem(`texPage:${this.paper.id}`)) || 1;
      sessionStorage.removeItem(`texPage:${this.paper.id}`);
      return page;
    } catch { return 1; }
  }

  // PDF importé : titre et auteurs (lus dans le fichier, parfois mal), corrigés à la main
  async editMeta() {
    const edited = await metaDialog({ title: this.paper.title, authors: this.paper.authors });
    if (edited) {
      try {
        const paper = await api.updateMeta(this.paper.id, edited.title, edited.authors);
        Object.assign(this.paper, paper);
        this.toolbarTitle.replaceChildren(titleTex(paper.title));
        this.toolbarTitle.title = paper.title;
        document.title = plainTitle(paper.title);
        toast(t("reader.metaSaved"));
      } catch (error) {
        toastError(error);
      }
    }
  }

  destroy() {
    this.destroyed = true;
    document.body.classList.remove("reading");
    this.annotator?.destroy();
    this.pdfNotes?.destroy();
    this.assistant?.destroy();
    this.stopPick();
    this.hidePreview();
    this.overlay?.remove();
    this.lazyPaint?.destroy();
    this.cleanups.forEach((fn) => fn());
  }
}

// l'élément peut-il encore défiler dans le sens du geste ?
function canScroll(element, dx, dy) {
  if (!(element instanceof HTMLElement)) return false;
  const style = getComputedStyle(element);
  if (dy && /auto|scroll/.test(style.overflowY) && element.scrollHeight > element.clientHeight + 1
    && (dy < 0 ? element.scrollTop > 0 : element.scrollTop + element.clientHeight < element.scrollHeight - 1)) return true;
  return Boolean(dx && /auto|scroll/.test(style.overflowX) && element.scrollWidth > element.clientWidth + 1
    && (dx < 0 ? element.scrollLeft > 0 : element.scrollLeft + element.clientWidth < element.scrollWidth - 1));
}

// Lettres et chiffres d'un texte, en minuscules, avec la position de chacun dans le texte (passages cherchés dans le
// PDF). Ligatures du PDF (ﬁ) et lettres mathématiques (𝑥) ramenées à leurs lettres simples (NFKC), à la même position
function lettersOf(raw) {
  let text = "";
  const starts = [];  // début et fin dans `raw` du caractère d'où vient chaque lettre
  const ends = [];
  for (let index = 0; index < raw.length;) {
    const char = String.fromCodePoint(raw.codePointAt(index));
    // NFKD : ligatures (ﬁ) en lettres, accents (é, e + accent combinant, accent seul d'un PDF en OT1 : « e´ ») ôtés
    for (const letter of char.normalize("NFKD").toLowerCase()) {
      if (!/[\p{L}\p{N}]/u.test(letter)) continue;
      text += letter;
      starts.push(index);
      ends.push(index + char.length);
    }
    index += char.length;
  }
  return { text, starts, ends };
}

// Morceaux de texte d'un extrait de source TeX (lettres et chiffres), dans l'ordre, sans ses formules, ses renvois,
// ses notes de bas de page ni ses commentaires ; une commande de mise en forme (\emph{…}, \textbf{…}) garde son texte,
// une autre commande coupe le morceau (le texte qu'elle imprime n'est pas dans la source) et perd son premier argument
// (\textcolor{red}{…}, \vspace{2pt}) ; un lien (\href{url}{texte}) garde son texte, une adresse (\url{…}) coupe ; un
// accent (\'e) laisse sa lettre. Morceaux vides gardés : le premier (dernier) est vide si l'extrait commence (finit) par
// une formule ou une commande, ce que pdfQuote regarde pour vérifier ses bords
const DISPLAY = "equation|align|alignat|flalign|gather|multline|eqnarray|displaymath|math";
function proseOf(quote) {
  const text = dropBalanced(String(quote)
    .replace(/\\url\{[^}]*\}/g, "\u0000")
    .replace(/\\href\{[^}]*\}/g, "")
    .replace(/(^|[^\\])((?:\\\\)*)%.*$/gm, "$1$2")
    .replace(/\\\\\*?(?:\[[^\]]*\])?/g, " "), /\\(?:footnote|footnotetext|thanks)(?![A-Za-z])(?:\[[^\]]*\])?\s*\{/g);
  return text
    .replace(new RegExp(`\\\\begin\\{(${DISPLAY})(\\*?)\\}[\\s\\S]*?\\\\end\\{\\1\\2\\}`, "g"), "\u0000")
    .replace(/\$\$[\s\S]*?\$\$|\$[^$]*\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]/g, "\u0000")
    .replace(/\\(?:begin|end)\{[^}]*\}(?:\[[^\]]*\])?/g, "\u0000")
    .replace(/\\(?:eq|page|v|auto|name|c|C)?ref\*?\{[^}]*\}|\\hyperref\[[^\]]*\]|\\(?:cite[a-zA-Z]*|label|index)\*?(?:\[[^\]]*\])*\{[^}]*\}/g, "\u0000")
    .replace(/\\["'`^~=.]|\\[cvuHkr](?![A-Za-z])/g, "")
    .replace(/\\(?:emph|text(?:bf|it|sc|rm|sf|tt|up|md|sl|normal)?|underline|mbox)\s*(?=\{)/g, "")
    .replace(/\\[A-Za-z@]+\*?(?:\[[^\]]*\])*(?:\{[^{}]*\})?/g, "\u0000")
    .split("\u0000")
    .map((piece) => lettersOf(piece).text);
}

// Commandes trouvées par `opening` (qui finit sur l'accolade ouvrante) ôtées avec leur argument, accolades équilibrées
// (une note de bas de page peut en contenir plusieurs niveaux) ; remplacées par une coupure de morceau
function dropBalanced(text, opening) {
  let out = "";
  let last = 0;
  for (const match of text.matchAll(opening)) {
    if (match.index < last) continue;
    let depth = 1;
    let at = match.index + match[0].length;
    for (; at < text.length && depth; at++) {
      if (text[at] === "\\") at++;
      else if (text[at] === "{") depth++;
      else if (text[at] === "}") depth--;
    }
    out += text.slice(last, match.index) + "\u0000";
    last = at;
  }
  return out + text.slice(last);
}
