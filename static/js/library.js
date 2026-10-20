// Page « Bibliothèque » : import par identifiant arXiv et liste des articles.

import { setFindHandler } from "./find.js";
import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { debounce, draggablePaper, droppedPapers, formatDate, h, icons, journalChip, localDrag, mayBePaper, tidyTitle, timeAgo, toast, toastError, withMath } from "./util.js";
import { addToLibrary, dragEnded, dragGroup, fileInFolder, forgetSelected, selectButton, selectionDropped, syncSelection, useSelectionBar } from "./selection.js";

// Recherche et filtres, gardés d'une visite à l'autre (le temps de la session)
const view = { search: "", categories: new Set(), reading: new Set(), years: new Set(), folders: new Set(), source: "any", publication: "any", sort: "opened",
  alphaKey: "title", alphaDir: "asc", openFolder: null, folderSearch: "",
  folder: { sort: "published", alphaKey: "title", alphaDir: "asc" }, // tri du dossier ouvert (mêmes options, et la pertinence)
  folderFilters: null }; // filtres du dossier ouvert (remis à zéro à chaque ouverture)
// Dossiers ({id, name, papers}), relus avec la liste des articles
let folderList = [];
const READING = ["unread", "annotated", "understood", "claude"];
const SORTS = ["opened", "added", "published", "alpha"];

// Comparaison sans accents ni majuscules
const fold = (text) => String(text || "").replace(/\\["'`^~=.uvHc]\{?(\w)\}?/g, "$1") // accents TeX : K\"ahler → Kahler
  .normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function readingStates(paper) {
  const reading = paper.reading || {};
  return {
    unread: !paper.opened_at,
    annotated: Boolean(reading.highlights || reading.notes),
    understood: Boolean(reading.done),
    claude: Boolean(reading.answers),
  };
}

// tous les mots cherchés dans le titre, les auteurs, le résumé, l'identifiant ou les catégories
function matchesWords(paper, query) {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const haystack = fold([paper.title, paper.authors.join(" "), paper.abstract, paper.arxiv_id, paper.categories.join(" ")].join(" "));
  return words.every((word) => haystack.includes(word));
}

// filtres de la bibliothèque (`view`) ou du dossier ouvert (`view.folderFilters`, mêmes options sans « Dossiers »)
function passes(paper, state) {
  if (state.categories.size && !paper.categories.some((c) => state.categories.has(c))) return false;
  if (state.folders.size && !folderList.some((f) => state.folders.has(f.id) && f.papers.includes(paper.id))) return false;
  if (state.years.size && !state.years.has((paper.published || "").slice(0, 4))) return false;
  const pdf = paper.source === "none" || paper.source === "tex" || paper.source === "local-pdf"; // lu dans le PDF
  if (state.source === "html" && pdf) return false;
  if (state.source === "pdf" && !pdf) return false;
  if (state.publication === "journal" && !paper.publishedIn) return false;
  if (state.publication === "none" && paper.publishedIn) return false;
  if (state.reading.size) {
    const states = readingStates(paper);
    if (![...state.reading].some((reading) => states[reading])) return false;
  }
  return true;
}

function matches(paper) {
  return matchesWords(paper, view.search) && passes(paper, view);
}

// Ordre alphabétique : titre (sans les formules ni la ponctuation du début) ou nom du premier auteur
const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });
const titleKey = (paper) => fold(paper.title).replace(/\$[^$]*\$/g, " ").replace(/^[^\p{L}\p{N}]+/u, "");
const authorKey = (paper) => {
  const name = fold(paper.authors?.[0] || "").trim();
  const parts = name.split(/\s+/);
  return parts.length > 1 ? `${parts.at(-1)} ${parts.slice(0, -1).join(" ")}` : name; // nom, puis prénom
};
const alphabetical = (state) => (a, b) => {
  const order = state.alphaKey === "author" ? collator.compare(authorKey(a), authorKey(b)) || collator.compare(titleKey(a), titleKey(b))
    : collator.compare(titleKey(a), titleKey(b));
  return state.alphaDir === "desc" ? -order : order;
};
const SORTERS = {
  opened: (a, b) => (b.opened_at || b.added_at || "").localeCompare(a.opened_at || a.added_at || ""),
  added: (a, b) => (b.added_at || "").localeCompare(a.added_at || ""),
  published: (a, b) => (b.published || "").localeCompare(a.published || ""),
};
// Ordre choisi (bibliothèque ou dossier ouvert) ; pertinence pour le profil : dossiers seulement
const sorterFor = (state, scores = null) => state.sort === "relevance"
  ? (a, b) => ((scores?.[b.id] ?? -1) - (scores?.[a.id] ?? -1)) || SORTERS.published(a, b)
  : state.sort === "alpha" ? alphabetical(state) : SORTERS[state.sort] || SORTERS.opened;
const sortLabel = (state) => state.sort === "alpha"
  ? `${t("library.sort.alpha")} (${t(state.alphaKey === "author" ? "library.sort.alphaAuthor" : "library.sort.alphaTitle")}, ${state.alphaDir === "desc" ? "Z → A" : "A → Z"})`
  : t(`library.sort.${state.sort}`);
// dossiers : ordre de création, ou par nom avec le tri alphabétique des articles (même sens)
const folderOrder = (folders) => view.sort !== "alpha" ? folders
  : [...folders].sort((a, b) => (view.alphaDir === "desc" ? -1 : 1) * collator.compare(a.name, b.name));

// le tri n'est pas un filtre : il ne compte pas, et « Tout réinitialiser » le garde
const activeCount = (state = view) => state.categories.size + state.reading.size + state.years.size + state.folders.size
  + (state.source !== "any" ? 1 : 0) + (state.publication !== "any" ? 1 : 0);

function resetFilters(state = view) {
  state.categories.clear();
  state.reading.clear();
  state.years.clear();
  state.folders.clear();
  state.source = "any";
  state.publication = "any";
}
const emptyFilters = () => ({ categories: new Set(), reading: new Set(), years: new Set(), folders: new Set(), source: "any", publication: "any" });

// Grande bibliothèque : les cartes sont affichées par lots (les suivantes au fil du défilement), et chaque
// carte n'est construite qu'une fois tant que l'article ne change pas (une recherche ne fait que les trier)
const CHUNK = 150;

// ce qu'une carte montre de l'article : si rien n'a changé, la même carte resert
const cardSignature = (paper, folders) => JSON.stringify([paper.title, paper.authors, paper.version, paper.published, paper.categories,
  paper.primary_category, paper.opened_at, paper.position, paper.reading, paper.source, paper.in_library, paper.publishedIn, folders]);

export async function renderLibrary(root, { restoreTo = 0 } = {}) {
  const input = h("input", {
    class: "text-input", type: "text", name: "query", autocomplete: "off", spellcheck: "false",
    placeholder: t("library.placeholder"), "aria-label": t("library.placeholder"),
  });
  const submit = h("button", { class: "button", type: "submit" }, t("library.import"));
  const status = h("div", { class: "busy", hidden: true }, h("span", { class: "spinner" }), t("library.importing"));
  // PDF de l'ordinateur (hors arXiv) : à côté d'« Importer » ; aussi par glisser-déposer sur la bibliothèque
  const pdfInput = h("input", { type: "file", accept: "application/pdf,.pdf", multiple: true, hidden: true });
  const pdfButton = h("button", { class: "button", type: "button", title: t("library.importPdfHint"), onclick: () => pdfInput.click() },
    t("library.importPdf"));
  pdfInput.addEventListener("change", () => { importPdfs([...pdfInput.files]); pdfInput.value = ""; });
  const form = h("form", { class: "import-box" }, input, submit, pdfButton, pdfInput);
  const list = h("div", {});
  const heading = h("h2", {});
  const search = h("input", {
    class: "text-input library-search", type: "search", value: view.search, placeholder: t("library.search"),
    "aria-label": t("library.search"), autocomplete: "off", spellcheck: "false",
  });
  const filterButton = h("button", { class: "button filter-button", type: "button", "aria-haspopup": "dialog", "aria-expanded": "false",
    "aria-controls": "library-filters" });
  const sortButton = h("button", { class: "button filter-button", type: "button", "aria-haspopup": "dialog", "aria-expanded": "false",
    "aria-controls": "library-filters" });
  const activeRow = h("div", { class: "active-filters" });
  // recherche, tri et filtres : sous le titre, au-dessus des dossiers (la recherche et les filtres portent sur
  // toute la bibliothèque) ; les menus Tri et Filtres s'ouvrent sous cette ligne
  const tools = h("div", { class: "library-search-bar" },
    h("div", { class: "library-search-row" },
      h("span", { class: "search-field" }, h("span", { class: "search-icon", html: icons.search }), search),
      sortButton, filterButton));
  // titre de la liste (« Autres articles », ou les résultats d'une recherche)
  const listHead = h("div", { class: "library-tools" }, heading);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = input.value.trim();
    if (!query) return;
    submit.disabled = true;
    status.hidden = false;
    try {
      const paper = await api.importPaper(query);
      input.value = "";
      toast(t("library.imported", { title: paper.title }));
      await refreshList();
    } catch (error) {
      toastError(error);
    } finally {
      submit.disabled = false;
      status.hidden = true;
    }
  });
  // PDF importés un par un ; version arXiv trouvée : proposée (toutes les fonctions : HTML, énoncés, dépendances)
  // PDF téléchargé d'arXiv (mention « arXiv:… ») : importer la version arXiv à la place ? → "arxiv" | "pdf" | ""
  function askArxivFirst(name, arxivId) {
    return new Promise((resolve) => {
      const dialog = h("dialog", { class: "meta-dialog arxiv-first" },
        h("form", { method: "dialog" },
          h("h3", {}, t("library.arxivFirstTitle")),
          h("p", {}, t("library.arxivFirst", { file: name, id: arxivId })),
          h("div", { class: "dialog-actions" },
            h("button", { class: "button", value: "pdf" }, t("library.arxivFirstKeep")),
            h("button", { class: "button primary", value: "arxiv", autofocus: true }, t("library.arxivFirstImport")))));
      dialog.addEventListener("close", () => { dialog.remove(); resolve(dialog.returnValue); });
      document.body.append(dialog);
      dialog.showModal();
    });
  }

  async function importPdfs(files) {
    const pdfs = files.filter((file) => file.type === "application/pdf" || /\.pdf$/i.test(file.name));
    if (!pdfs.length) return;
    status.hidden = false;
    const imported = [];
    const fromArxiv = [];  // versions arXiv importées à la place d'un PDF d'arXiv
    const kept = new Set();  // PDF d'arXiv gardés tels quels : version arXiv pas reproposée après l'import
    let failed = null;
    try {
      for (const file of pdfs) {  // un fichier refusé n'arrête pas les suivants
        try {
          let result = await api.uploadPdf(file, { check: true });
          if (!result.paper && result.arxiv?.arxivId) {
            const choice = await askArxivFirst(file.name, result.arxiv.arxivId);
            if (choice === "arxiv") { fromArxiv.push(await api.importPaper(result.arxiv.arxivId)); continue; }
            if (choice !== "pdf") continue;  // fermé (Échap) : rien n'est importé
            result = await api.uploadPdf(file);
            kept.add(result.paper.id);
          }
          imported.push(result.paper);
        } catch (error) { failed = new Error(`${file.name} : ${error.message}`); }
      }
    } finally {
      status.hidden = true;
      await refreshList();
    }
    if (failed) { toastError(failed); return; }  // le refus reste affiché (pas remplacé par la proposition arXiv)
    const count = imported.length + fromArxiv.length;
    if (count > 1) toast(t("library.pdfsImported", { count }));
    else if (fromArxiv.length) toast(t("library.arxivImported", { id: fromArxiv[0].arxiv_id || fromArxiv[0].id }));
    else if (imported.length) toast(t("library.pdfImported", { title: imported[0].title }));
    // version arXiv cherchée ensuite (Semantic Scholar peut être lent) ; proposée pour la première trouvée
    for (const paper of imported.filter((p) => !kept.has(p.id))) {
      const arxiv = await api.pdfArxivMatch(paper.id).then((r) => r.arxiv).catch(() => null);
      if (!arxiv?.arxivId) continue;
      toast(t("library.pdfOnArxiv", { id: arxiv.arxivId }), { duration: 15000, action: { label: t("library.pdfOpenArxiv"), icon: icons.external, onClick: async () => {
        try {
          const added = await api.importPaper(arxiv.arxivId);
          location.hash = `#/paper/${encodeURIComponent(added.id)}`;
        } catch (error) { toastError(error); }
      } } });
      break;
    }
  }
  search.addEventListener("input", debounce(() => { view.search = search.value; refreshList(false); }, 120));

  let papers = [];
  let menu = null;
  let menuButton = null; // bouton qui a ouvert le menu (Filtres ou Tri)
  let fillOpen = null;   // et de quoi le remplir

  // ---- Menus « Filtres » et « Tri » ----
  const closeMenu = ({ focus = true } = {}) => {
    if (!menu) return;
    menu.remove();
    menu = null;
    menuButton.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onOutside);
    document.removeEventListener("keydown", onEscape);
    if (focus) menuButton.focus();
  };
  const onOutside = (event) => { if (menu && !menu.contains(event.target) && !menuButton.contains(event.target)) closeMenu({ focus: false }); };
  const onEscape = (event) => { if (event.key === "Escape") closeMenu(); };
  const changed = () => { refreshList(false); if (menu) fillOpen(); };

  const chip = (label, active, toggle, focusId, after = changed) => h("button", { class: "kind-filter", type: "button", "aria-pressed": active,
    dataset: { focus: focusId }, onclick: () => { toggle(); after(); } }, label);
  const toggleIn = (set, value) => () => { if (set.has(value)) set.delete(value); else set.add(value); };
  const choice = (name, options, current, pick, after = changed) => h("div", { class: "segmented filter-segmented", role: "radiogroup", "aria-labelledby": `filter-${name}` },
    options.map(([value, label]) => h("label", {},
      h("input", { type: "radio", name: `filter-${name}`, value, checked: value === current, dataset: { focus: `${name}:${value}` },
        onchange: () => { pick(value); after(); } }), label)));
  const section = (name, title, body) => h("div", { class: "filter-section" }, h("h3", { id: `filter-${name}` }, title), body);

  // Menu « Filtres » de la bibliothèque, ou du dossier ouvert (ses articles, sans « Dossiers »)
  function fillMenu() { fillFiltersFor(view, papers, changed, true); }
  function fillFiltersFor(state, list, after, withFolders) {
    const categories = [...new Set(list.flatMap((p) => p.categories))].sort();
    const years = [...new Set(list.map((p) => (p.published || "").slice(0, 4)).filter(Boolean))].sort().reverse();
    const scroll = menu.querySelector(".filter-body")?.scrollTop || 0;
    const focused = menu.contains(document.activeElement) ? document.activeElement.dataset.focus || document.activeElement.dataset.role : null;
    menu.replaceChildren(
      h("div", { class: "filter-body" },
        withFolders && folderList.length > 0 && section("folders", t("library.filters.folders"), h("div", { class: "filter-chips" },
          folderList.map((f) => chip(f.name, state.folders.has(f.id), toggleIn(state.folders, f.id), `folder:${f.id}`, after)))),
        section("reading", t("library.filters.reading"), h("div", { class: "filter-chips" },
          READING.map((key) => chip(t(`library.reading.${key}`), state.reading.has(key), toggleIn(state.reading, key), `reading:${key}`, after)))),
        categories.length > 1 && section("categories", t("library.filters.categories"), h("div", { class: "filter-chips" },
          categories.map((c) => chip(c, state.categories.has(c), toggleIn(state.categories, c), `category:${c}`, after)))),
        years.length > 1 && section("years", t("library.filters.years"), h("div", { class: "filter-chips" },
          years.map((y) => chip(y, state.years.has(y), toggleIn(state.years, y), `year:${y}`, after)))),
        section("source", t("library.filters.source"), choice("source",
          [["any", t("library.source.any")], ["html", t("library.source.html")], ["pdf", t("library.source.pdf")]], state.source, (v) => { state.source = v; }, after)),
        section("publication", t("journal.filter"), choice("publication",
          [["any", t("journal.any")], ["journal", t("journal.yesShort")], ["none", t("journal.noShort")]], state.publication, (v) => { state.publication = v; }, after)),
        h("p", { class: "hint filter-hint" }, t("journal.filterHint"))),
      h("div", { class: "filter-foot" },
        h("button", { class: "button small-button", type: "button", disabled: !activeCount(state), dataset: { role: "reset" },
          onclick: () => { resetFilters(state); after(); } }, t("library.filters.reset")),
        h("span", { class: "spacer" }),
        h("button", { class: "button small-button primary", type: "button", dataset: { role: "done" }, onclick: () => closeMenu() }, t("library.filters.done"))));
    menu.querySelector(".filter-body").scrollTop = scroll;
    // le menu est redessiné : le focus revient sur la même commande (ou sur « Fermer » si elle a disparu)
    if (focused) (menu.querySelector(`[data-focus="${CSS.escape(focused)}"], [data-role="${CSS.escape(focused)}"]:not(:disabled)`)
      || menu.querySelector('[data-role="done"]')).focus();
  }

  // Menu « Tri » : ordre des articles (de la bibliothèque, ou du dossier ouvert : même menu, plus la
  // pertinence) ; l'ordre alphabétique (titre ou premier auteur, A→Z ou Z→A) range aussi les dossiers par nom
  function fillSortFor(state, keys, after, hint) {
    const focused = menu.contains(document.activeElement) ? document.activeElement.dataset.focus || document.activeElement.dataset.role : null;
    menu.replaceChildren(
      h("div", { class: "filter-body" },
        section("sort", t("library.filters.sort"), choice("sort", keys.map((key) => [key, t(`library.sort.${key}`)]), state.sort, (v) => { state.sort = v; }, after)),
        state.sort === "alpha" && section("alphaKey", t("library.sort.alphaKey"), choice("alphaKey",
          [["title", t("library.sort.alphaTitle")], ["author", t("library.sort.alphaAuthor")]], state.alphaKey, (v) => { state.alphaKey = v; }, after)),
        state.sort === "alpha" && section("alphaDir", t("library.sort.alphaDir"), choice("alphaDir",
          [["asc", "A → Z"], ["desc", "Z → A"]], state.alphaDir, (v) => { state.alphaDir = v; }, after)),
        state.sort === "alpha" && hint && h("p", { class: "hint" }, hint)),
      h("div", { class: "filter-foot" }, h("span", { class: "spacer" }),
        h("button", { class: "button small-button primary", type: "button", dataset: { role: "done" }, onclick: () => closeMenu() }, t("library.filters.done"))));
    if (focused) (menu.querySelector(`[data-focus="${CSS.escape(focused)}"], [data-role="${CSS.escape(focused)}"]`)
      || menu.querySelector('[data-role="done"]')).focus();
  }

  const fillSort = () => fillSortFor(view, SORTS, changed, t("library.sort.alphaHint"));

  const openMenu = (button, fill, label, extraClass = "", container = tools) => {
    if (menu) closeMenu({ focus: false }); // l'autre menu ouvert : remplacé
    menuButton = button;
    fillOpen = fill;
    menu = h("div", { class: `filter-menu ${extraClass}`.trim(), id: "library-filters", role: "dialog", "aria-label": label });
    fill();
    container.append(menu);
    button.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onOutside); // aussi au doigt (iPhone)
    // Tab hors du menu : il se ferme, sans reprendre le focus
    menu.addEventListener("focusout", (event) => {
      if (event.relatedTarget && !menu?.contains(event.relatedTarget) && !button.contains(event.relatedTarget)) closeMenu({ focus: false });
    });
    document.addEventListener("keydown", onEscape);
    (menu.querySelector("input:checked") || menu.querySelector("button, input"))?.focus();
  };
  const toggleMenu = (button, fill, label, extraClass, container) => {
    if (menu && menuButton === button) { closeMenu(); return; } // second clic sur le même bouton : fermé
    openMenu(button, fill, label, extraClass, container);
  };
  filterButton.addEventListener("click", () => toggleMenu(filterButton, fillMenu, t("library.filters.title")));
  sortButton.addEventListener("click", () => toggleMenu(sortButton, fillSort, t("library.filters.sort"), "sort-menu library-sort"));

  // Pastilles des filtres actifs, chacune retirable ; bouton « Filtres » avec leur nombre
  function drawActive() {
    const count = activeCount();
    filterButton.replaceChildren(h("span", { class: "button-icon", html: icons.filter }), t("library.filters.title"),
      count ? h("span", { class: "filter-count" }, String(count)) : "");
    sortButton.replaceChildren(h("span", { class: "button-icon", html: icons.sort }), t("library.sortLabel"));
    sortButton.title = t("library.sortTooltip", { order: sortLabel(view) });
    const pill = (label, remove) => h("span", { class: "active-filter" }, label,
      h("button", { type: "button", class: "active-filter-remove", title: t("library.filters.remove"), "aria-label": t("library.filters.removeOne", { label }),
        html: icons.close, onclick: (event) => {
          // focus sur la pastille suivante (ou le bouton Filtres) une fois celle-ci retirée
          const buttons = [...activeRow.querySelectorAll(".active-filter-remove")];
          const index = buttons.indexOf(event.currentTarget);
          remove();
          changed();
          const rest = [...activeRow.querySelectorAll(".active-filter-remove")];
          (rest[Math.min(index, rest.length - 1)] || filterButton).focus();
        } }));
    const pills = [
      ...[...view.reading].map((state) => pill(t(`library.reading.${state}`), () => view.reading.delete(state))),
      ...[...view.categories].map((c) => pill(c, () => view.categories.delete(c))),
      ...[...view.years].map((y) => pill(y, () => view.years.delete(y))),
      ...[...view.folders].map((id) => folderList.find((f) => f.id === id))
        .filter(Boolean).map((f) => pill(f.name, () => view.folders.delete(f.id))),
      view.source !== "any" && pill(t(`library.source.${view.source}`), () => { view.source = "any"; }),
      view.publication !== "any" && pill(t(`journal.${view.publication === "journal" ? "yes" : "no"}`), () => { view.publication = "any"; }),
    ].filter(Boolean);
    activeRow.replaceChildren(...pills, pills.length > 1 ? h("button", { class: "link-button small", type: "button",
      onclick: () => { resetFilters(); changed(); } }, t("library.filters.reset")) : "");
    activeRow.hidden = !pills.length;
  }

  // cartes déjà construites : id de l'article → { signature, carte }
  const built = new Map();
  let moreObserver = null;
  let onMoreScroll = null; // aussi au défilement : l'observateur ne suffit pas partout (fenêtre en arrière-plan)
  let restoring = restoreTo; // retour sur la page : assez de cartes pour revenir à l'endroit quitté
  let shownQuery = null; // recherche, filtres et tri de la liste affichée
  let shownCount = 0;     // et combien de cartes y étaient déjà affichées
  function showCards(shown) {
    moreObserver?.disconnect();
    if (onMoreScroll) removeEventListener("scroll", onMoreScroll);
    // même recherche, mêmes filtres, même tri (retour dans l'application, article supprimé ou rangé) : au moins
    // autant de cartes qu'avant, ajoutées d'un coup (sinon la page raccourcit et remonte)
    const query = JSON.stringify([view.search, [...view.categories], [...view.reading], [...view.years], [...view.folders],
      view.source, view.publication, view.sort, view.alphaKey, view.alphaDir]);
    const keep = query === shownQuery ? shownCount : 0;
    shownQuery = query;
    const names = new Map(); // dossiers de chaque article, une fois pour toutes les cartes
    for (const folder of folderList) for (const id of folder.papers) names.set(id, [...(names.get(id) || []), folder.name]);
    const cardFor = (paper) => {
      const signature = cardSignature(paper, names.get(paper.id) || []);
      const known = built.get(paper.id);
      if (known?.signature === signature) { syncSelection(known.card); return known.card; } // sélection changée entre-temps
      const fresh = card(paper, refreshList, cardContext);
      built.set(paper.id, { signature, card: fresh });
      return fresh;
    };
    const box = h("div", { class: "cards" });
    const more = h("button", { class: "button small-button", type: "button" });
    const moreRow = h("div", { class: "library-more" }, more);
    let count = 0;
    let observer = null;
    let onScroll = null;
    const next = (size = CHUNK) => {
      if (!moreRow.isConnected && count) return; // liste remplacée depuis : rien à y ajouter
      box.append(...shown.slice(count, count + size).map(cardFor));
      count = Math.min(shown.length, count + size);
      shownCount = count;
      if (count >= shown.length) { moreRow.remove(); observer?.disconnect(); if (onScroll) removeEventListener("scroll", onScroll); return; }
      more.textContent = t("library.more", { n: shown.length - count });
      // fin de liste encore visible (grand écran) : lot suivant aussitôt après
      if (!restoring && moreRow.isConnected && moreRow.getBoundingClientRect().top < innerHeight + 600) setTimeout(() => next());
    };
    more.addEventListener("click", () => next());
    list.replaceChildren(box, moreRow);
    next(Math.max(CHUNK, keep));
    // retour sur la page : lots ajoutés tout de suite jusqu'à l'endroit quitté (rétabli juste après l'affichage)
    while (restoring && count < shown.length && document.documentElement.scrollHeight < restoring + innerHeight) next();
    restoring = 0;
    if (count < shown.length) {
      observer = moreObserver = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) next(); }, { rootMargin: "600px" });
      observer.observe(moreRow);
      onScroll = onMoreScroll = () => { if (moreRow.isConnected && moreRow.getBoundingClientRect().top < innerHeight + 600) next(); };
      addEventListener("scroll", onScroll, { passive: true });
    }
    // articles retirés de la bibliothèque : leurs cartes ne sont plus gardées
    if (built.size > papers.length + CHUNK) {
      const ids = new Set(papers.map((p) => p.id));
      for (const id of built.keys()) if (!ids.has(id)) built.delete(id);
    }
  }

  async function refreshList(reload = true) {
    if (reload) {
      try {
        [papers, folderList] = await Promise.all([api.papers(), api.folders().catch(() => folderList)]);
        // dossier ou filtre disparu (supprimé ailleurs) : oublié
        if (!folderList.some((f) => f.id === view.openFolder)) view.openFolder = null;
        for (const id of view.folders) if (!folderList.some((f) => f.id === id)) view.folders.delete(id);
      } catch (error) {
        const retry = h("button", { class: "button small-button", type: "button", onclick: () => refreshList() }, t("common.retry"));
        list.replaceChildren(h("p", { class: "notice" }, t("error.generic", { message: error.message }), retry));
        return;
      }
      if (menu && !panel.contains(menuButton)) fillOpen(); // menu ouvert (Filtres ou Tri) : redessiné tel quel
    }
    drawActive();
    drawFolders();
    // une fois des articles rangés, la liste générale ne garde que les autres (« Autres articles ») ; une
    // recherche ou un filtre porte sur toute la bibliothèque
    // article sélectionné puis retiré de la bibliothèque : plus sélectionné (sinon « Ranger dans… » le réimporterait)
    forgetSelected((info) => !info.paperId || papers.some((p) => p.id === info.paperId));
    const filed = new Set(folderList.flatMap((f) => f.papers).filter((id) => papers.some((p) => p.id === id)));
    const everywhere = Boolean(view.search.trim()) || activeCount() > 0;
    const others = filed.size && !everywhere ? papers.filter((p) => !filed.has(p.id)) : papers;
    const shown = others.filter(matches).sort(sorterFor(view));
    // recherche ou filtre : les résultats juste sous le champ, au-dessus des dossiers ; sinon les dossiers d'abord
    if (everywhere ? list.nextElementSibling !== foldersBox : listHead.previousElementSibling !== foldersBox) {
      if (everywhere) list.after(foldersBox); else listHead.before(foldersBox);
    }
    heading.textContent = everywhere ? t("library.papersShown", { n: shown.length, total: papers.length })
      : others !== papers ? t("library.others", { n: others.length }) : t("library.papers", { n: papers.length });
    if (!papers.length) {
      const example = h("code", {}, "2608.24719");
      const text = t("library.empty", { example: "\u0000" }).split("\u0000");
      list.replaceChildren(h("div", { class: "empty" }, text[0], example, text[1]));
    } else if (!shown.length && !everywhere) {
      list.replaceChildren(h("div", { class: "empty" }, t("library.allFiled")));
    } else if (!shown.length) {
      list.replaceChildren(h("div", { class: "empty" }, t("library.noMatch"), " ",
        h("button", { class: "link-button", type: "button", onclick: () => { resetFilters(); view.search = ""; search.value = ""; changed(); } }, t("library.showAll"))));
    } else {
      showCards(shown);
    }
  }

  // ---- Dossiers : rangée de cartes, dossier ouvert dessous (déroulé), menu « Ranger dans… » des articles ----
  // le dossier ouvert reste le même élément : il se déroule et s'enroule (transition), sans être recréé
  // en-tête : titre, et au bout de la ligne « Nouveau dossier » (hors de la grille : le dossier ouvert se déroule
  // juste sous les dossiers, sans ce bouton entre les deux)
  const foldersTitle = h("h2", { id: "folders-title" });
  const folderAdd = h("div", { class: "folders-add" });
  const folderGrid = h("div", { class: "folder-grid" });
  const panel = h("div", { class: "folder-panel", id: "folder-panel" }, h("div", { class: "folder-panel-inner" }));
  const foldersBox = h("section", { class: "folders", "aria-labelledby": "folders-title" },
    h("div", { class: "folders-head" }, foldersTitle, folderAdd), folderGrid, panel);
  let creating = false;
  const inLibrary = (folder) => folder.papers.filter((id) => papers.some((p) => p.id === id));
  // dossiers changés : liste relue (cartes, pastilles des articles, dossier ouvert)
  const reloadFolders = async () => { folderList = await api.folders(); refreshList(false); };

  function nameForm(value, onSave, onCancel, label) {
    const field = h("input", { class: "text-input", type: "text", value, maxlength: 80, placeholder: t("library.folder.name"), "aria-label": t("library.folder.name") });
    const save = async (event) => {
      event.preventDefault();
      const name = field.value.trim();
      if (!name) return onCancel();
      try { await onSave(name); } catch (error) { toastError(error); field.focus(); }
    };
    const box = h("form", { class: "folder-name-form", onsubmit: save },
      field, h("button", { class: "button small-button", type: "submit" }, label),
      h("button", { class: "link-button", type: "button", onclick: onCancel }, t("library.folder.cancel")));
    field.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.stopPropagation(); onCancel(); } });
    requestAnimationFrame(() => { field.focus(); field.select(); });
    return box;
  }

  // formulaires de nom (création, renommage) gardés d'un dessin à l'autre : une recherche ou un filtre
  // pendant la saisie ne les recrée pas (le texte tapé et le focus restent)
  let createForm = null;
  let renameForm = null;
  // focus gardé sur le dossier cliqué (le dessin remplace les boutons)
  const focusFolder = (id) => folderGrid.querySelector(`[data-folder="${id}"]`)?.focus();

  function drawFolders() {
    const cards = folderOrder(folderList).map((folder) => {
      const count = inLibrary(folder).length;
      return dropTarget(h("button", { class: "folder", type: "button", "aria-expanded": String(view.openFolder === folder.id), "aria-controls": "folder-panel",
        dataset: { folder: folder.id }, title: t("library.drop.hint"), onclick: () => toggleFolder(folder.id) },
        h("span", { class: "folder-icon", html: icons.folder }),
        h("span", { class: "folder-text" }, h("span", { class: "folder-name" }, folder.name), h("span", { class: "folder-count" }, tn("feed.count", count)))),
        () => folder.id);
    });
    if (!creating) createForm = null;
    createForm ||= creating && nameForm("", async (name) => {
      const folder = await api.createFolder(name);
      creating = false;
      openFolder(folder.id);
      panelOpening = true;
      await reloadFolders();
    }, () => { creating = false; drawFolders(); folderAdd.querySelector("button")?.focus(); }, t("library.folder.create")); // annulé : retour au bouton
    const add = creating
      ? createForm
      : h("button", { class: "button small-button", type: "button", onclick: () => { creating = true; drawFolders(); } },
        h("span", { class: "button-icon", html: icons.folderAdd }), t("library.folder.new"));
    foldersTitle.textContent = folderList.length ? t("library.foldersCount", { n: folderList.length }) : t("library.folders");
    if (folderAdd.firstChild !== add) folderAdd.replaceChildren(add); // formulaire gardé : la saisie continue
    folderGrid.replaceChildren(...cards);
    keepFocus(drawPanel); // redessin au retour dans l'application, après une action : la frappe en cours continue
  }

  // Article glissé sur un dossier (sa carte, ou le dossier ouvert) : rangé dedans ; venu du feed, d'une autre
  // fenêtre ou d'un lien arXiv et pas encore dans la bibliothèque : importé d'abord
  function dropTarget(element, folderId) {
    element.addEventListener("dragover", (event) => {
      if (!folderId() || !mayBePaper(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      element.classList.add("drop-target");
    });
    element.addEventListener("dragleave", (event) => { if (!element.contains(event.relatedTarget)) element.classList.remove("drop-target"); });
    element.addEventListener("drop", (event) => {
      if (!folderId()) return;
      event.preventDefault();
      element.classList.remove("drop-target");
      fileDropped(droppedPapers(event.dataTransfer), folderId());
    });
    return element;
  }
  async function fileDropped(dropped, folderId) {
    const folder = folderList.find((f) => f.id === folderId);
    if (!folder) return;
    if (!dropped.length) { toast(t("library.drop.notPaper"), { error: true }); return; }
    try {
      await fileInFolder(dropped, folder, papers);
      selectionDropped(); // sélection de cette fenêtre glissée ici : rangée, donc effacée (d'une autre fenêtre : dragEnded)
    } catch (error) {
      toastError(error);
    }
    await refreshList();
  }
  dropTarget(panel, () => view.openFolder);

  // Article déposé sur la liste générale (« Autres articles ») : ajouté à la bibliothèque, hors dossier.
  // Déjà dans la bibliothèque (une carte glissée sur sa propre liste) : rien à faire, sans message.
  function libraryDropTarget(element) {
    const files = (event) => [...(event.dataTransfer?.types || [])].includes("Files");  // PDF glissés depuis l'ordinateur
    element.addEventListener("dragover", (event) => {
      if (!mayBePaper(event) && !files(event)) return;
      // carte de la bibliothèque glissée (vers un dossier) : la liste n'est pas une cible
      if (localDrag?.every((item) => papers.some((p) => p.id === item.paperId || p.arxiv_id === item.arxivId))) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      list.classList.add("drop-target");
    });
    element.addEventListener("dragleave", (event) => { if (!element.contains(event.relatedTarget)) list.classList.remove("drop-target"); });
    element.addEventListener("drop", async (event) => {
      event.preventDefault();
      list.classList.remove("drop-target");
      if (event.dataTransfer.files?.length) { importPdfs([...event.dataTransfer.files]); return; }
      const dropped = droppedPapers(event.dataTransfer);
      if (!dropped.length) { toast(t("library.drop.notPaper"), { error: true }); return; }
      try {
        if (await addToLibrary(dropped, papers)) await refreshList();
        selectionDropped();
      } catch (error) {
        toastError(error);
        await refreshList();
      }
    });
  }
  libraryDropTarget(list);
  libraryDropTarget(listHead);

  let renaming = false;
  // pertinence des articles de la bibliothèque : demandée au serveur la première fois qu'un dossier est
  // trié ainsi pendant cette visite (le profil a pu changer entre deux visites)
  let scores = null;
  let scoring = null;
  let rescored = false; // articles ajoutés depuis le calcul : un seul nouveau calcul par visite
  let redrawMembers = null; // liste du dossier ouvert seule (pas son en-tête ni le champ de recherche)
  const loadScores = () => {
    scoring ||= api.papersRelevance()
      .then((result) => { scores = result; if (root.isConnected) redrawMembers?.(); })
      .catch((error) => {
        toastError(error);
        scoring = null;
        if (view.folder.sort === "relevance") view.folder.sort = "published"; // pas de nouvel essai en boucle
        if (root.isConnected) keepFocus(drawPanel);
      });
  };
  const openFolder = (id) => {
    view.openFolder = id;
    view.folderSearch = ""; // recherche et filtres propres au dossier ouvert
    view.folderFilters = emptyFilters();
  };
  // les commandes du dossier sont redessinées : le focus (et le curseur dans le champ) reviennent au même endroit
  function keepFocus(draw) {
    const active = document.activeElement;
    const key = active?.dataset?.focus;
    const caret = active?.type === "search" ? [active.selectionStart, active.selectionEnd] : null;
    draw();
    const field = key && panel.querySelector(`[data-focus="${CSS.escape(key)}"]`);
    if (!field || field === active) return;
    field.focus();
    if (caret && field.type === "search") field.setSelectionRange(...caret);
  }
  function drawPanel() {
    const folder = folderList.find((f) => f.id === view.openFolder);
    const inner = panel.firstChild;
    if (menu && inner.contains(menuButton)) closeMenu({ focus: false }); // menu du dossier : redessiné avec lui
    if (!folder) {
      if (panel.classList.contains("open")) {
        panel.classList.remove("open", "settled");
        clearTimeout(settleTimer);
        // contenu retiré une fois le dossier refermé (fin du déroulé)
        const clear = () => { if (!panel.classList.contains("open")) inner.replaceChildren(); };
        panel.addEventListener("transitionend", clear, { once: true });
        setTimeout(clear, 400);
      }
      redrawMembers = null;
      return;
    }
    view.folderFilters ||= emptyFilters();
    const all = papers.filter((p) => folder.papers.includes(p.id));
    if (scores && !rescored && all.some((p) => !(p.id in scores))) { rescored = true; scores = scoring = null; }
    const searching = all.length > 1 || Boolean(view.folderSearch) || activeCount(view.folderFilters) > 0; // gardé tant qu'un filtre s'applique
    const busy = h("span", { class: "busy", "aria-live": "polite" }, h("span", { class: "spinner" }), t("library.folder.scoring"));
    if (searching && view.folder.sort === "relevance" && !scores) loadScores();
    // articles du dossier : redessinés seuls pendant la frappe (le champ de recherche garde le focus)
    const count = h("span", { class: "muted small" });
    const cards = h("div", {});
    const drawMembers = () => {
      busy.hidden = !(view.folder.sort === "relevance" && !scores);
      const members = all.filter((p) => matchesWords(p, view.folderSearch) && passes(p, view.folderFilters)).sort(sorterFor(view.folder, scores));
      folderSort.title = t("library.sortTooltip", { order: sortLabel(view.folder) });
      const active = activeCount(view.folderFilters);
      folderFilter.replaceChildren(h("span", { class: "button-icon", html: icons.filter }), t("library.filters.title"),
        active ? h("span", { class: "filter-count" }, String(active)) : "");
      count.textContent = members.length === all.length ? tn("feed.count", all.length)
        : t("library.folder.matching", { n: members.length, total: all.length });
      cards.replaceChildren(members.length
        ? h("div", { class: "cards" }, members.map((p, i) => { const c = card(p, refreshList, cardContext); c.style.setProperty("--i", i); return c; }))
        : h("p", { class: "hint folder-empty" }, t(all.length ? "library.folder.noMatch" : "library.folder.empty")));
    };
    const search = h("input", { class: "text-input folder-search-input", type: "search", value: view.folderSearch,
      placeholder: t("library.folder.search"), "aria-label": t("library.folder.search"), autocomplete: "off", spellcheck: "false",
      dataset: { focus: "folder-search" }, oninput: () => { view.folderSearch = search.value; drawMembers(); } });
    redrawMembers = drawMembers;
    const folderSort = h("button", { class: "button filter-button", type: "button", "aria-haspopup": "dialog", "aria-expanded": "false",
      dataset: { focus: "folder-sort" } }, h("span", { class: "button-icon", html: icons.sort }), t("library.sortLabel"));
    const sortChanged = () => {
      if (view.folder.sort === "relevance" && !scores) loadScores();
      drawMembers();
      if (menu) fillOpen();
    };
    const fillFolderSort = () => fillSortFor(view.folder, ["relevance", ...SORTS], sortChanged, null);
    const folderFilter = h("button", { class: "button filter-button", type: "button", "aria-haspopup": "dialog", "aria-expanded": "false",
      dataset: { focus: "folder-filter" } });
    const fillFolderFilters = () => fillFiltersFor(view.folderFilters, all, sortChanged, false);
    const sort = searching && h("div", { class: "folder-sort" },
      h("span", { class: "search-field folder-search" }, h("span", { class: "search-icon", html: icons.search }), search),
      busy, folderSort, folderFilter);
    folderFilter.addEventListener("click", () => toggleMenu(folderFilter, fillFolderFilters, t("library.filters.title"), "", sort));
    folderSort.addEventListener("click", () => toggleMenu(folderSort, fillFolderSort, t("library.filters.sort"), "sort-menu library-sort folder-sort-menu", sort));
    if (!renaming) renameForm = null;
    renameForm ||= renaming && nameForm(folder.name, async (name) => { await api.renameFolder(folder.id, name); renaming = false; await reloadFolders(); },
      () => { renaming = false; drawPanel(); }, t("library.folder.save"));
    const title = renaming ? renameForm : h("h3", {}, folder.name);
    inner.replaceChildren(
      h("div", { class: "folder-panel-head" },
        h("span", { class: "folder-icon", html: icons.folder }), title,
        count,
        !renaming && h("button", { class: "link-button", type: "button", onclick: () => { renaming = true; drawPanel(); } }, t("library.folder.rename")),
        h("button", { class: "link-button", type: "button", onclick: () => removeFolder(folder) }, t("library.folder.delete")),
        h("button", { class: "link-button", type: "button", onclick: () => { toggleFolder(folder.id); focusFolder(folder.id); } }, t("library.folder.close"))),
      sort || "",
      cards);
    drawMembers();
    if (!panel.classList.contains("open")) {
      if (panelOpening) {
        void panel.offsetHeight; // point de départ (replié) avant le déroulé
        // articles qui apparaissent l'un après l'autre : à l'ouverture seulement (pas à chaque recherche)
        panel.classList.add("opening");
        setTimeout(() => panel.classList.remove("opening"), 900);
      } else {
        // dossier resté ouvert (retour sur la page) : affiché d'emblée, sans déroulé qui décalerait la page
        panel.classList.add("no-anim");
        requestAnimationFrame(() => requestAnimationFrame(() => panel.classList.remove("no-anim")));
      }
      panel.classList.add("open");
      // une fois déroulé, le menu « Tri » du dossier peut dépasser du cadre (sinon coupé)
      clearTimeout(settleTimer);
      const settle = () => { if (panel.classList.contains("open")) panel.classList.add("settled"); };
      const onEnd = (event) => {
        if (event.target !== panel || event.propertyName !== "grid-template-rows") return;
        panel.removeEventListener("transitionend", onEnd);
        settle();
      };
      panel.addEventListener("transitionend", onEnd);
      settleTimer = setTimeout(() => { panel.removeEventListener("transitionend", onEnd); settle(); }, 450);
    }
    panelOpening = false;
  }

  let panelOpening = false;
  let settleTimer = null;
  function toggleFolder(id) {
    openFolder(view.openFolder === id ? null : id);
    panelOpening = view.openFolder !== null;
    renaming = false;
    drawFolders();
    focusFolder(id);
  }

  async function removeFolder(folder) {
    try {
      const removed = await api.deleteFolder(folder.id);
      view.openFolder = null;
      view.folders.delete(folder.id);
      await reloadFolders();
      toast(t("library.folder.deleted", { name: removed.name }), { action: { label: t("common.undo"),
        onClick: () => api.createFolder(removed.name, removed.papers).then((again) => {
          openFolder(again.id);
          panelOpening = true;
          return reloadFolders();
        }, toastError) } });
    } catch (error) {
      toastError(error);
    }
  }

  // Menu « Ranger dans… » d'un article : dossiers à cocher, et création d'un dossier
  let fileMenu = null;
  const closeFileMenu = () => {
    fileMenu?.remove();
    fileMenu = null;
    document.removeEventListener("pointerdown", onFileOutside);
    document.removeEventListener("keydown", onFileEscape, true);
  };
  const onFileOutside = (event) => { if (fileMenu && !fileMenu.contains(event.target) && !event.target.closest?.(".folder-action")) closeFileMenu(); };
  const onFileEscape = (event) => { if (event.key === "Escape" && fileMenu) { event.stopPropagation(); fileMenu.opener?.focus(); closeFileMenu(); } };
  function openFileMenu(paper, button) {
    if (fileMenu?.paperId === paper.id) { closeFileMenu(); return; }
    closeFileMenu();
    // le menu peut avoir été fermé (ou un autre ouvert) pendant un enregistrement : rien n'est alors redessiné
    let menu = null;
    const fill = () => {
      if (!menu || fileMenu !== menu) return;
      menu.replaceChildren(
        h("h4", {}, t("library.folder.fileTitle")),
        ...folderList.map((folder) => h("label", { class: "check" },
          h("input", { type: "checkbox", checked: folder.papers.includes(paper.id), onchange: async (event) => {
            try { await api.setInFolder(folder.id, paper.id, event.target.checked); await reloadFolders(); fill(); } catch (error) { toastError(error); }
          } }), folder.name)),
        h("form", { class: "folder-menu-new", onsubmit: async (event) => {
          event.preventDefault();
          const name = event.currentTarget.elements.name.value.trim();
          if (!name) return;
          try { await api.createFolder(name, [paper.id]); await reloadFolders(); fill(); } catch (error) { toastError(error); }
        } },
          h("input", { class: "text-input", type: "text", name: "name", maxlength: 80, placeholder: t("library.folder.newName"), "aria-label": t("library.folder.newName") }),
          h("button", { class: "button small-button", type: "submit" }, t("library.folder.create"))));
    };
    fileMenu = menu = h("div", { class: "folder-menu", role: "dialog", "aria-label": t("library.folder.fileTitle") });
    Object.assign(fileMenu, { paperId: paper.id, opener: button });
    fill();
    document.body.append(fileMenu);
    // sous le bouton, aligné à droite ; au-dessus s'il n'y a pas la place
    const rect = button.getBoundingClientRect();
    const height = fileMenu.offsetHeight;
    const top = rect.bottom + 6 + height > innerHeight ? rect.top - 6 - height : rect.bottom + 6;
    Object.assign(fileMenu.style, { top: `${Math.max(8, top)}px`, left: `${Math.max(8, rect.right - fileMenu.offsetWidth)}px` });
    document.addEventListener("pointerdown", onFileOutside);
    document.addEventListener("keydown", onFileEscape, true);
    // défilement voulu (molette, doigt) : le menu se ferme ; pas quand la page se recale toute seule
    // (dossier qui s'agrandit après un rangement)
    addEventListener("wheel", closeFileMenu, { once: true, passive: true });
    addEventListener("touchmove", closeFileMenu, { once: true, passive: true });
    fileMenu.querySelector("input")?.focus();
  }
  const cardContext = { folders: () => folderList, openFileMenu };

  root.replaceChildren(h("section", { class: "library" },
    // titre et import sur une ligne
    h("div", { class: "lib-head" }, h("h1", {}, t("library.title")), form),
    status,
    tools, activeRow,
    foldersBox,
    listHead,
    list,
  ));
  if (matchMedia("(hover: hover)").matches) input.focus(); // pas sur téléphone : le clavier s'ouvrirait à chaque visite
  list.replaceChildren(h("div", { class: "busy" }, h("span", { class: "spinner" }), t("common.loading")));
  await refreshList();
  // ⌘F : champ de recherche de la bibliothèque
  const unsubscribeFind = setFindHandler((command) => { if (command === "open") { search.focus(); search.select(); } });
  // plusieurs articles sélectionnés : barre en bas (« Ranger dans… »), glissés ensemble sur un dossier
  const stopSelection = useSelectionBar({ papers: () => papers, onFiled: () => refreshList() });
  // retour sur l'onglet (application Mac) : liste et dossiers relus, changés peut-être dans un autre onglet
  const onRefresh = () => refreshList();
  addEventListener("arxivreader:library-refresh", onRefresh);
  return () => { closeMenu({ focus: false }); closeFileMenu(); unsubscribeFind(); removeEventListener("arxivreader:library-refresh", onRefresh);
    moreObserver?.disconnect(); if (onMoreScroll) removeEventListener("scroll", onMoreScroll); stopSelection(); };
}

// « Ouvert il y a 2 jours · 3 notes · 5 énoncés compris · 2 réponses de Claude »
function readingLine(paper) {
  const reading = paper.reading || {};
  const parts = [
    paper.opened_at ? t("library.opened", { when: timeAgo(paper.opened_at) }) : t("library.notOpened"),
    reading.highlights ? tn("library.highlights", reading.highlights) : "",
    reading.notes ? tn("library.notes", reading.notes) : "",
    reading.done ? tn("library.done", reading.done) : "",
    reading.answers ? tn("library.answers", reading.answers) : "",
  ].filter(Boolean);
  // dernière position de lecture dans une section numérotée (identifiants LaTeXML « S3… ») : lien pour y revenir
  const section = paper.position?.match(/^S(\d+)/)?.[1];
  return h("div", { class: "card-reading muted small" }, parts.join(" · "),
    section && [" · ", h("a", { class: "resume-link", href: `#/paper/${encodeURIComponent(paper.id)}` }, t("library.resume", { n: section }))]);
}

// Dossiers d'un article, en fin de ligne de métadonnées
function folderChip(paper, context) {
  const names = (context?.folders() || []).filter((f) => f.papers.includes(paper.id)).map((f) => f.name);
  return names.length ? h("span", { class: "chip folder-chip" }, h("span", { class: "folder-chip-icon", html: icons.folder }), names.join(", ")) : null;
}

function card(paper, refreshList, context = null) {
  const href = `#/paper/${encodeURIComponent(paper.id)}`;
  const action = (icon, title, handler, className = "") => h("button", {
    class: `icon-button ${className}`.trim(), type: "button", title, "aria-label": title, html: icon,
    onclick: (event) => handler(event.currentTarget),
  });
  const authors = paper.authors.length > 6 ? [...paper.authors.slice(0, 6), "…"] : paper.authors;
  // Le titre est un vrai lien (⌘-clic, copier le lien…) qui couvre toute la carte ; les boutons restent au-dessus
  return draggablePaper(h("article", { class: "card" },
    h("h3", { class: "card-title" }, h("a", { class: "card-link", href }, withMath(tidyTitle(paper.title)))),
    h("div", { class: "card-authors" }, authors.join(", ")),
    readingLine(paper),
    h("div", { class: "card-meta" },
      paper.primary_category && h("span", { class: "chip category" }, paper.primary_category),
      paper.arxiv_id && h("span", { class: "chip" }, `arXiv:${paper.arxiv_id}v${paper.version}`),
      paper.published && h("span", { class: "chip" }, formatDate(paper.published)),
      paper.source === "local-pdf" && h("span", { class: "chip", title: paper.comment || "" }, t("library.localPdf")),
      paper.source === "none" && h("span", { class: "chip" }, t("library.pdfOnly")),
      paper.source === "tex" && h("span", { class: "chip", title: t("library.pdfTexHint") }, t("library.pdfTex")),
      journalChip(paper.publishedIn),
      folderChip(paper, context),
    ),
    h("div", { class: "card-actions" },
      selectButton({ arxivId: paper.arxiv_id, paperId: paper.id, title: paper.title }),
      context && action(icons.folder, t("library.folder.file"), (button) => context.openFileMenu(paper, button), "folder-action"),
      paper.arxiv_id && h("a", { class: "icon-button", href: `#/related/${encodeURIComponent(paper.arxiv_id)}`, title: t("related.fromCard"),
        "aria-label": t("related.fromCard"), html: icons.graph }),
      paper.arxiv_id && h("a", { class: "icon-button", href: `https://arxiv.org/abs/${paper.arxiv_id}`, target: "_blank", rel: "noopener noreferrer",
        title: "arXiv", "aria-label": "arXiv", html: icons.external }),
      paper.arxiv_id && action(icons.refresh, t("library.refresh"), async (button) => {
        button.disabled = true;
        button.classList.add("spinning");
        try {
          await api.refresh(paper.id); // en arrière-plan : on suit l'import (aucune longue requête)
          for (let i = 0; i < 200 && (await api.importing(paper.id)).running; i++) await new Promise((r) => setTimeout(r, 1500));
          toast(t("library.updated"));
          refreshList();
        }
        catch (error) { toastError(error); }
        finally { button.disabled = false; button.classList.remove("spinning"); }
      }),
      // Retirer de la bibliothèque (annulable) : l'article reste en cache, ses notes et réponses sont gardées
      action(icons.trash, t("library.delete"), async () => {
        try {
          await api.setInLibrary(paper.id, false);
          refreshList();
          toast(t("library.deleted"), { action: { label: t("common.undo"),
            onClick: () => api.setInLibrary(paper.id, true).then(() => refreshList(), toastError) } });
        } catch (error) {
          toastError(error);
        }
      }),
    ),
  ), { arxivId: paper.arxiv_id, paperId: paper.id, version: paper.version, title: paper.title }, dragGroup, dragEnded);
}
