// Page « Bibliothèque » : import par identifiant arXiv et liste des articles.

import { setFindHandler } from "./find.js";
import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { debounce, formatDate, h, icons, tidyTitle, timeAgo, toast, toastError, withMath } from "./util.js";

// Recherche et filtres, gardés d'une visite à l'autre (le temps de la session)
const view = { search: "", categories: new Set(), reading: new Set(), years: new Set(), folders: new Set(), source: "any", sort: "opened",
  openFolder: null, folderSort: "newest", folderSearch: "" };
// Dossiers ({id, name, papers}), relus avec la liste des articles
let folderList = [];
const READING = ["unread", "annotated", "understood", "claude"];
const SORTS = ["opened", "added", "published", "title"];

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

function matches(paper) {
  if (!matchesWords(paper, view.search)) return false;
  if (view.categories.size && !paper.categories.some((c) => view.categories.has(c))) return false;
  if (view.folders.size && !folderList.some((f) => view.folders.has(f.id) && f.papers.includes(paper.id))) return false;
  if (view.years.size && !view.years.has((paper.published || "").slice(0, 4))) return false;
  const pdf = paper.source === "none" || paper.source === "tex"; // lu dans le PDF (énoncés tirés du TeX ou non)
  if (view.source === "html" && pdf) return false;
  if (view.source === "pdf" && !pdf) return false;
  if (view.reading.size) {
    const states = readingStates(paper);
    if (![...view.reading].some((state) => states[state])) return false;
  }
  return true;
}

const SORTERS = {
  opened: (a, b) => (b.opened_at || b.added_at || "").localeCompare(a.opened_at || a.added_at || ""),
  added: (a, b) => (b.added_at || "").localeCompare(a.added_at || ""),
  published: (a, b) => (b.published || "").localeCompare(a.published || ""),
  title: (a, b) => fold(a.title).localeCompare(fold(b.title)),
};

// Tri d'un dossier ouvert : pertinence pour le profil (comme le feed) ou date de publication
const FOLDER_SORTS = ["relevance", "newest", "oldest"];
const folderSorter = (scores) => ({
  relevance: (a, b) => ((scores?.[b.id] ?? -1) - (scores?.[a.id] ?? -1)) || SORTERS.published(a, b),
  newest: SORTERS.published,
  oldest: (a, b) => SORTERS.published(b, a),
}[view.folderSort]);

// le tri n'est pas un filtre : il ne compte pas, et « Tout réinitialiser » le garde
const activeCount = () => view.categories.size + view.reading.size + view.years.size + view.folders.size + (view.source !== "any" ? 1 : 0);

function resetFilters() {
  view.categories.clear();
  view.reading.clear();
  view.years.clear();
  view.folders.clear();
  view.source = "any";
}

export async function renderLibrary(root) {
  const input = h("input", {
    class: "text-input", type: "text", name: "query", autocomplete: "off", spellcheck: "false",
    placeholder: t("library.placeholder"), "aria-label": t("library.placeholder"),
  });
  const submit = h("button", { class: "button", type: "submit" }, t("library.import"));
  const status = h("div", { class: "busy", hidden: true }, h("span", { class: "spinner" }), t("library.importing"));
  const form = h("form", { class: "import-box" }, input, submit);
  const list = h("div", {});
  const heading = h("h2", {});
  const search = h("input", {
    class: "text-input library-search", type: "search", value: view.search, placeholder: t("library.search"),
    "aria-label": t("library.search"), autocomplete: "off", spellcheck: "false",
  });
  const filterButton = h("button", { class: "button filter-button", type: "button", "aria-haspopup": "dialog", "aria-expanded": "false",
    "aria-controls": "library-filters" });
  const activeRow = h("div", { class: "active-filters" });
  const tools = h("div", { class: "library-tools" }, heading,
    h("div", { class: "library-search-row" },
      h("span", { class: "search-field" }, h("span", { class: "search-icon", html: icons.search }), search),
      filterButton));

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
  search.addEventListener("input", debounce(() => { view.search = search.value; refreshList(false); }, 120));

  let papers = [];
  let menu = null;

  // ---- Menu « Filtres » ----
  const closeMenu = ({ focus = true } = {}) => {
    if (!menu) return;
    menu.remove();
    menu = null;
    filterButton.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onOutside);
    document.removeEventListener("keydown", onEscape);
    if (focus) filterButton.focus();
  };
  const onOutside = (event) => { if (menu && !menu.contains(event.target) && !filterButton.contains(event.target)) closeMenu({ focus: false }); };
  const onEscape = (event) => { if (event.key === "Escape") closeMenu(); };
  const changed = () => { refreshList(false); if (menu) fillMenu(); };

  const chip = (label, active, toggle, focusId) => h("button", { class: "kind-filter", type: "button", "aria-pressed": active,
    dataset: { focus: focusId }, onclick: () => { toggle(); changed(); } }, label);
  const toggleIn = (set, value) => () => { if (set.has(value)) set.delete(value); else set.add(value); };
  const choice = (name, options, current, pick) => h("div", { class: "segmented filter-segmented", role: "radiogroup", "aria-labelledby": `filter-${name}` },
    options.map(([value, label]) => h("label", {},
      h("input", { type: "radio", name: `filter-${name}`, value, checked: value === current, dataset: { focus: `${name}:${value}` },
        onchange: () => { pick(value); changed(); } }), label)));
  const section = (name, title, body) => h("div", { class: "filter-section" }, h("h3", { id: `filter-${name}` }, title), body);

  function fillMenu() {
    const categories = [...new Set(papers.flatMap((p) => p.categories))].sort();
    const years = [...new Set(papers.map((p) => (p.published || "").slice(0, 4)).filter(Boolean))].sort().reverse();
    const scroll = menu.querySelector(".filter-body")?.scrollTop || 0;
    const focused = menu.contains(document.activeElement) ? document.activeElement.dataset.focus || document.activeElement.dataset.role : null;
    menu.replaceChildren(
      h("div", { class: "filter-body" },
        folderList.length > 0 && section("folders", t("library.filters.folders"), h("div", { class: "filter-chips" },
          folderList.map((f) => chip(f.name, view.folders.has(f.id), toggleIn(view.folders, f.id), `folder:${f.id}`)))),
        section("reading", t("library.filters.reading"), h("div", { class: "filter-chips" },
          READING.map((state) => chip(t(`library.reading.${state}`), view.reading.has(state), toggleIn(view.reading, state), `reading:${state}`)))),
        categories.length > 1 && section("categories", t("library.filters.categories"), h("div", { class: "filter-chips" },
          categories.map((c) => chip(c, view.categories.has(c), toggleIn(view.categories, c), `category:${c}`)))),
        years.length > 1 && section("years", t("library.filters.years"), h("div", { class: "filter-chips" },
          years.map((y) => chip(y, view.years.has(y), toggleIn(view.years, y), `year:${y}`)))),
        section("source", t("library.filters.source"), choice("source",
          [["any", t("library.source.any")], ["html", t("library.source.html")], ["pdf", t("library.source.pdf")]], view.source, (v) => { view.source = v; })),
        section("sort", t("library.filters.sort"), choice("sort",
          SORTS.map((key) => [key, t(`library.sort.${key}`)]), view.sort, (v) => { view.sort = v; }))),
      h("div", { class: "filter-foot" },
        h("button", { class: "button small-button", type: "button", disabled: !activeCount(), dataset: { role: "reset" },
          onclick: () => { resetFilters(); changed(); } }, t("library.filters.reset")),
        h("span", { class: "spacer" }),
        h("button", { class: "button small-button primary", type: "button", dataset: { role: "done" }, onclick: () => closeMenu() }, t("library.filters.done"))));
    menu.querySelector(".filter-body").scrollTop = scroll;
    // le menu est redessiné : le focus revient sur la même commande (ou sur « Fermer » si elle a disparu)
    if (focused) (menu.querySelector(`[data-focus="${CSS.escape(focused)}"], [data-role="${CSS.escape(focused)}"]:not(:disabled)`)
      || menu.querySelector('[data-role="done"]')).focus();
  }

  filterButton.addEventListener("click", () => {
    if (menu) { closeMenu(); return; }
    menu = h("div", { class: "filter-menu", id: "library-filters", role: "dialog", "aria-label": t("library.filters.title") });
    fillMenu();
    tools.append(menu);
    filterButton.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onOutside); // aussi au doigt (iPhone)
    // Tab hors du menu : il se ferme, sans reprendre le focus
    menu.addEventListener("focusout", (event) => {
      if (event.relatedTarget && !menu?.contains(event.relatedTarget) && !filterButton.contains(event.relatedTarget)) closeMenu({ focus: false });
    });
    document.addEventListener("keydown", onEscape);
    menu.querySelector("button, input")?.focus();
  });

  // Pastilles des filtres actifs, chacune retirable ; bouton « Filtres » avec leur nombre
  function drawActive() {
    const count = activeCount();
    filterButton.replaceChildren(h("span", { class: "button-icon", html: icons.filter }), t("library.filters.title"),
      count ? h("span", { class: "filter-count" }, String(count)) : "");
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
    ].filter(Boolean);
    activeRow.replaceChildren(...pills, pills.length > 1 ? h("button", { class: "link-button small", type: "button",
      onclick: () => { resetFilters(); changed(); } }, t("library.filters.reset")) : "");
    activeRow.hidden = !pills.length;
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
      if (menu) fillMenu();
    }
    drawActive();
    drawFolders();
    const shown = papers.filter(matches).sort(SORTERS[view.sort]);
    heading.textContent = shown.length === papers.length ? t("library.papers", { n: papers.length })
      : t("library.papersShown", { n: shown.length, total: papers.length });
    if (!papers.length) {
      const example = h("code", {}, "2608.24719");
      const text = t("library.empty", { example: "\u0000" }).split("\u0000");
      list.replaceChildren(h("div", { class: "empty" }, text[0], example, text[1]));
    } else if (!shown.length) {
      list.replaceChildren(h("div", { class: "empty" }, t("library.noMatch"), " ",
        h("button", { class: "link-button", type: "button", onclick: () => { resetFilters(); view.search = ""; search.value = ""; changed(); } }, t("library.showAll"))));
    } else {
      list.replaceChildren(h("div", { class: "cards" }, shown.map((p) => card(p, refreshList, cardContext))));
    }
  }

  // ---- Dossiers : rangée de cartes, dossier ouvert dessous (déroulé), menu « Ranger dans… » des articles ----
  // le dossier ouvert reste le même élément : il se déroule et s'enroule (transition), sans être recréé
  const foldersTitle = h("h2", { id: "folders-title" });
  const folderGrid = h("div", { class: "folder-grid" });
  const panel = h("div", { class: "folder-panel", id: "folder-panel" }, h("div", { class: "folder-panel-inner" }));
  const foldersBox = h("section", { class: "folders", "aria-labelledby": "folders-title" }, foldersTitle, folderGrid, panel);
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
    const cards = folderList.map((folder) => {
      const count = inLibrary(folder).length;
      return h("button", { class: "folder", type: "button", "aria-expanded": String(view.openFolder === folder.id), "aria-controls": "folder-panel",
        dataset: { folder: folder.id }, onclick: () => toggleFolder(folder.id) },
        h("span", { class: "folder-icon", html: icons.folder }),
        h("span", { class: "folder-text" }, h("span", { class: "folder-name" }, folder.name), h("span", { class: "folder-count" }, tn("feed.count", count))));
    });
    if (!creating) createForm = null;
    createForm ||= creating && h("div", { class: "folder folder-new editing" }, nameForm("", async (name) => {
      const folder = await api.createFolder(name);
      creating = false;
      view.openFolder = folder.id;
      panelOpening = true;
      await reloadFolders();
    }, () => { creating = false; drawFolders(); }, t("library.folder.create")));
    const add = creating
      ? createForm
      : h("button", { class: "folder folder-new", type: "button", onclick: () => { creating = true; drawFolders(); } },
        h("span", { class: "folder-icon", html: icons.add }), t("library.folder.new"));
    foldersTitle.textContent = folderList.length ? t("library.foldersCount", { n: folderList.length }) : t("library.folders");
    folderGrid.replaceChildren(...cards, add);
    drawPanel();
  }

  let renaming = false;
  // pertinence des articles de la bibliothèque : demandée au serveur la première fois qu'un dossier est
  // trié ainsi pendant cette visite (le profil a pu changer entre deux visites)
  let scores = null;
  let scoring = null;
  const loadScores = () => {
    scoring ||= api.papersRelevance()
      .then((result) => { scores = result; })
      .catch((error) => { toastError(error); scoring = null; view.folderSort = "newest"; }) // pas de nouvel essai en boucle
      .finally(() => { if (root.isConnected) keepFocus(drawPanel); });
  };
  const keepFocus = (draw) => {
    const key = document.activeElement?.dataset?.focus;
    draw();
    const field = key && panel.querySelector(`[data-focus="${CSS.escape(key)}"]`);
    field?.focus();
    if (field?.type === "search") field.setSelectionRange(field.value.length, field.value.length);
  };
  function drawPanel() {
    const folder = folderList.find((f) => f.id === view.openFolder);
    const inner = panel.firstChild;
    if (!folder) {
      if (panel.classList.contains("open")) {
        panel.classList.remove("open");
        // contenu retiré une fois le dossier refermé (fin du déroulé)
        const clear = () => { if (!panel.classList.contains("open")) inner.replaceChildren(); };
        panel.addEventListener("transitionend", clear, { once: true });
        setTimeout(clear, 400);
      }
      return;
    }
    const waiting = view.folderSort === "relevance" && !scores;
    if (waiting) loadScores();
    const all = papers.filter((p) => folder.papers.includes(p.id));
    // articles du dossier : redessinés seuls pendant la frappe (le champ de recherche garde le focus)
    const count = h("span", { class: "muted small" });
    const cards = h("div", {});
    const drawMembers = () => {
      const members = all.filter((p) => matchesWords(p, view.folderSearch)).sort(folderSorter(scores));
      count.textContent = members.length === all.length ? tn("feed.count", all.length)
        : t("library.folder.matching", { n: members.length, total: all.length });
      cards.replaceChildren(members.length
        ? h("div", { class: "cards" }, members.map((p, i) => { const c = card(p, refreshList, cardContext); c.style.setProperty("--i", i); return c; }))
        : h("p", { class: "hint folder-empty" }, t(all.length ? "library.folder.noMatch" : "library.folder.empty")));
    };
    const search = h("input", { class: "text-input folder-search-input", type: "search", value: view.folderSearch,
      placeholder: t("library.folder.search"), "aria-label": t("library.folder.search"), autocomplete: "off", spellcheck: "false",
      dataset: { focus: "folder-search" }, oninput: () => { view.folderSearch = search.value; drawMembers(); } });
    const sort = all.length > 1 && h("div", { class: "folder-sort" },
      h("span", { class: "folder-sort-label", id: "folder-sort-label" }, t("library.folder.sort")),
      h("span", { class: "segmented search-segmented", role: "radiogroup", "aria-labelledby": "folder-sort-label" }, FOLDER_SORTS.map((key) => h("label", {},
        h("input", { type: "radio", name: "folder-sort", value: key, checked: key === view.folderSort, dataset: { focus: `folder-sort:${key}` },
          onchange: () => { view.folderSort = key; keepFocus(drawPanel); } }), t(`library.folderSort.${key}`)))),
      waiting && h("span", { class: "busy" }, h("span", { class: "spinner" }), t("library.folder.scoring")),
      h("span", { class: "search-field folder-search" }, h("span", { class: "search-icon", html: icons.search }), search));
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
    }
    panelOpening = false;
  }

  let panelOpening = false;
  function toggleFolder(id) {
    view.openFolder = view.openFolder === id ? null : id;
    view.folderSearch = ""; // recherche propre au dossier ouvert
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
          view.openFolder = again.id;
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
    foldersBox,
    tools, activeRow,
    list,
  ));
  if (matchMedia("(hover: hover)").matches) input.focus(); // pas sur téléphone : le clavier s'ouvrirait à chaque visite
  list.replaceChildren(h("div", { class: "busy" }, h("span", { class: "spinner" }), t("common.loading")));
  await refreshList();
  // ⌘F : champ de recherche de la bibliothèque
  const unsubscribeFind = setFindHandler((command) => { if (command === "open") { search.focus(); search.select(); } });
  // retour sur l'onglet (application Mac) : liste et dossiers relus, changés peut-être dans un autre onglet
  const onRefresh = () => refreshList();
  addEventListener("arxivreader:library-refresh", onRefresh);
  return () => { closeMenu({ focus: false }); closeFileMenu(); unsubscribeFind(); removeEventListener("arxivreader:library-refresh", onRefresh); };
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
  return h("article", { class: "card" },
    h("h3", { class: "card-title" }, h("a", { class: "card-link", href }, withMath(tidyTitle(paper.title)))),
    h("div", { class: "card-authors" }, authors.join(", ")),
    readingLine(paper),
    h("div", { class: "card-meta" },
      paper.primary_category && h("span", { class: "chip category" }, paper.primary_category),
      h("span", { class: "chip" }, `arXiv:${paper.arxiv_id}v${paper.version}`),
      h("span", { class: "chip" }, formatDate(paper.published)),
      paper.source === "none" && h("span", { class: "chip" }, t("library.pdfOnly")),
      paper.source === "tex" && h("span", { class: "chip", title: t("library.pdfTexHint") }, t("library.pdfTex")),
      folderChip(paper, context),
    ),
    h("div", { class: "card-actions" },
      context && action(icons.folder, t("library.folder.file"), (button) => context.openFileMenu(paper, button), "folder-action"),
      h("a", { class: "icon-button", href: `https://arxiv.org/abs/${paper.arxiv_id}`, target: "_blank", rel: "noopener noreferrer",
        title: "arXiv", "aria-label": "arXiv", html: icons.external }),
      action(icons.refresh, t("library.refresh"), async (button) => {
        button.disabled = true;
        button.classList.add("spinning");
        try { await api.refresh(paper.id); toast(t("library.updated")); refreshList(); }
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
  );
}
