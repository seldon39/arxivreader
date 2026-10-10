// Page « Bibliothèque » : import par identifiant arXiv et liste des articles.

import { setFindHandler } from "./find.js";
import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { debounce, formatDate, h, icons, tidyTitle, timeAgo, toast, toastError, withMath } from "./util.js";

// Recherche et filtres, gardés d'une visite à l'autre (le temps de la session)
const view = { search: "", categories: new Set(), reading: new Set(), years: new Set(), source: "any", sort: "opened" };
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

function matches(paper) {
  const words = fold(view.search).split(/\s+/).filter(Boolean);
  if (words.length) {
    const haystack = fold([paper.title, paper.authors.join(" "), paper.abstract, paper.arxiv_id, paper.categories.join(" ")].join(" "));
    if (!words.every((word) => haystack.includes(word))) return false;
  }
  if (view.categories.size && !paper.categories.some((c) => view.categories.has(c))) return false;
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

// le tri n'est pas un filtre : il ne compte pas, et « Tout réinitialiser » le garde
const activeCount = () => view.categories.size + view.reading.size + view.years.size + (view.source !== "any" ? 1 : 0);

function resetFilters() {
  view.categories.clear();
  view.reading.clear();
  view.years.clear();
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
      view.source !== "any" && pill(t(`library.source.${view.source}`), () => { view.source = "any"; }),
    ].filter(Boolean);
    activeRow.replaceChildren(...pills, pills.length > 1 ? h("button", { class: "link-button small", type: "button",
      onclick: () => { resetFilters(); changed(); } }, t("library.filters.reset")) : "");
    activeRow.hidden = !pills.length;
  }

  async function refreshList(reload = true) {
    if (reload) {
      try {
        papers = await api.papers();
      } catch (error) {
        const retry = h("button", { class: "button small-button", type: "button", onclick: () => refreshList() }, t("common.retry"));
        list.replaceChildren(h("p", { class: "notice" }, t("error.generic", { message: error.message }), retry));
        return;
      }
      if (menu) fillMenu();
    }
    drawActive();
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
      list.replaceChildren(h("div", { class: "cards" }, shown.map((p) => card(p, refreshList))));
    }
  }

  root.replaceChildren(h("section", { class: "library" },
    // titre et import sur une ligne
    h("div", { class: "lib-head" }, h("h1", {}, t("library.title")), form),
    status,
    tools, activeRow,
    list,
  ));
  if (matchMedia("(hover: hover)").matches) input.focus(); // pas sur téléphone : le clavier s'ouvrirait à chaque visite
  list.replaceChildren(h("div", { class: "busy" }, h("span", { class: "spinner" }), t("common.loading")));
  await refreshList();
  // ⌘F : champ de recherche de la bibliothèque
  const unsubscribeFind = setFindHandler((command) => { if (command === "open") { search.focus(); search.select(); } });
  return () => { closeMenu({ focus: false }); unsubscribeFind(); };
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

function card(paper, refreshList) {
  const href = `#/paper/${encodeURIComponent(paper.id)}`;
  const action = (icon, title, handler) => h("button", {
    class: "icon-button", type: "button", title, "aria-label": title, html: icon,
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
    ),
    h("div", { class: "card-actions" },
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
