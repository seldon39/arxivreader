// Page « Feed » : nouveautés arXiv, recommandations Semantic Scholar, import d'un texte collé.

import { api } from "./api.js";
import { getLanguage, t, tn } from "./i18n.js";
import { typesetTex } from "./math.js";
import { renderProfile } from "./profile.js";
import { saveSettings, settings } from "./settings.js";
import { debounce, h, hoursSince, icons, openRoute, tidyTitle, timeAgo, toast, toastError, toggleIcon, wantsNewTab, withMath } from "./util.js";

const TABS = [["arxiv", "#/feed"], ["recommended", "#/feed/recommended"], ["profile", "#/feed/profile"], ["import", "#/feed/import"]];
const COMPACT_BELOW = 0.12;
const TYPES = ["new", "cross", "replace", "replace-cross"];
const STALE_ARXIV_HOURS = 3;
const STALE_S2_HOURS = 24;
// Ce qu'on retrouve en revenant d'un article : jour affiché, recherche, catégories, articles écartés visibles
const memory = { day: null, search: "", categories: new Set(), showHidden: false };

export async function renderFeed(view, tab) {
  const body = h("div", { class: "feed-body" });
  view.replaceChildren(h("section", { class: "feed" },
    h("div", { class: "feed-head" },
      h("h1", {}, t("feed.title")),
      h("nav", { class: "feed-tabs" }, TABS.map(([key, href]) =>
        h("a", { href, "aria-current": key === tab ? "page" : undefined }, t(`feed.tab.${key}`))))),
    body));
  if (tab === "import") return renderImport(body);
  if (tab === "profile") return renderProfile(body);
  return renderList(body, tab);
}

// Touche ←/→ pendant la saisie, dans un menu ou avec la fenêtre des réglages ouverte : pas pour nous
function typing(event) {
  return event.target.closest?.("input, textarea, select, [contenteditable]") || document.querySelector("dialog[open]")
    || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey;
}

// ---------- Listes (arXiv, recommandations) ----------

function ago(stamp) {
  return stamp ? timeAgo(stamp) : t("feed.never");
}

function errorText(message) {
  const known = { "rate-limited": "feed.error.rateLimited", "no-seed": "feed.error.noSeed", "bad-key": "feed.error.badKey" };
  return known[message] ? t(known[message]) : t("error.generic", { message });
}

async function renderList(body, tab) {
  const status = h("div", { class: "feed-status muted small", "aria-live": "polite", hidden: true }); // pendant une mise à jour
  const refresh = h("button", { class: "button tool-button", type: "button", "aria-label": t("feed.refresh"), title: t("feed.refresh"), html: icons.refresh });
  const list = h("div", { class: "feed-list" });
  const notice = h("div", {});
  // la recherche, les catégories et les articles écartés sont ceux de la mémoire (gardés d'une visite à l'autre)
  // tri propre à chaque onglet : proximité au profil, ou ordre d'origine (arXiv, Semantic Scholar)
  const sortSetting = tab === "recommended" ? "feedSortS2" : "feedSort";
  const state = Object.assign(memory, { types: new Set(settings.feedTypes || ["new", "cross"]), sort: settings[sortSetting] || "relevance", sortSetting });
  let data = { items: [], categories: [] };
  const tools = feedTools({ tab, state, data: () => data, refresh, draw: () => { try { draw(); } catch { /* liste pas encore chargée */ } } });
  body.replaceChildren(tools.toolbar, tools.active, status, notice, list);
  list.replaceChildren(h("div", { class: "busy" }, h("span", { class: "spinner" }), t("common.loading")));

  const [first, alerts] = await Promise.all([api.feed(tab, tab === "arxiv" ? memory.day : null),
    tab === "arxiv" ? api.alerts().catch(() => ({ unseen: [] })) : { unseen: [] }]);
  data = first;
  const fresh = new Set(alerts.unseen); // articles d'auteurs suivis jamais vus : signalés cette fois-ci
  // marqués vus seulement une fois affichés (voir draw)
  const sent = new Set(); // déjà signalés au serveur (pas de nouvel envoi à chaque redessin)
  const markSeen = (keys) => {
    keys = keys.filter((key) => !sent.has(key));
    if (!keys.length) return;
    keys.forEach((key) => sent.add(key));
    api.alertsSeen(keys).then(() => dispatchEvent(new Event("arxivreader:alerts"))).catch(() => {});
  };
  // vus : les nouveaux articles affichés, hors d'une section repliée (marqués à son ouverture)
  const markSeenShown = () => markSeen(data.items.filter((i) => {
    const shownCard = fresh.has(i.key) && list.querySelector(`.feed-card.fresh[data-key="${CSS.escape(i.key)}"]`);
    return shownCard && !shownCard.closest(".foldable.collapsed");
  }).map((i) => i.key));
  addEventListener("arxivreader:section-opened", markSeenShown);
  let loadingDay = false;
  let generation = 0; // change à chaque changement de jour : une mise à jour plus ancienne n'écrase rien
  const remember = () => { memory.day = data.day && data.day !== data.latestDay ? data.day : null; };

  // Jour d'annonce précédent ou suivant (du lundi au vendredi ; jours fériés sautés au chargement)
  const shiftDay = (iso, step) => {
    const date = new Date(`${iso}T12:00:00Z`);
    do date.setUTCDate(date.getUTCDate() + step); while ([0, 6].includes(date.getUTCDay()));
    return date.toISOString().slice(0, 10);
  };
  const goToDay = async (step) => {
    if (loadingDay || !data.day) return;
    let target = shiftDay(data.day, step);
    if (step > 0 && data.latestDay && target > data.latestDay) return;
    loadingDay = true;
    generation++;
    // Barre de jour collée en haut (on lisait la liste) : on ira au début de la liste du nouveau jour ;
    // sinon, la page ne bouge pas.
    const nav = list.querySelector(".day-nav");
    const topbar = document.querySelector(".topbar").getBoundingClientRect().height;
    const wasReading = nav && nav.getBoundingClientRect().top <= topbar + 1 && nav.parentElement.getBoundingClientRect().top < topbar;
    const keepY = scrollY;
    dayLabel?.replaceChildren(h("span", { class: "spinner" }), " ", t("feed.loadingDay"));
    try {
      // jour sans annonce (férié) : sauté, dans les deux sens ; en avant, jamais au-delà du dernier jour
      for (let attempt = 0; attempt < 5; attempt++) {
        const next = await api.feed(tab, target);
        const last = step > 0 && (!data.latestDay || shiftDay(target, step) > data.latestDay);
        if (next.items.some((i) => i.in_arxiv && i.announced === target) || last || attempt === 4) { data = next; break; }
        target = shiftDay(target, step);
      }
      remember();
    } catch (error) {
      toastError(error);
    } finally {
      loadingDay = false;
      draw();
      const section = list.querySelector(".day-nav")?.parentElement;
      if (wasReading && section) {
        window.scrollTo({ top: section.getBoundingClientRect().top + scrollY - topbar, behavior: "instant" });
      } else {
        window.scrollTo({ top: keepY, behavior: "instant" });
      }
    }
  };
  let dayLabel = null;
  const onKey = (event) => {
    if (tab !== "arxiv" || typing(event)) return;
    if (event.key === "ArrowLeft") goToDay(-1);
    if (event.key === "ArrowRight") goToDay(1);
  };
  addEventListener("keydown", onKey);

  const doRefresh = async (automatic = false) => {
    refresh.disabled = true;
    refresh.classList.add("spinning");
    status.hidden = false;
    status.replaceChildren(h("span", { class: "spinner" }), " ", t(tab === "recommended" ? "feed.loadingS2" : "feed.loadingArxiv"));
    const started = generation;
    try {
      await api.refreshFeed(tab);
      const viewing = tab === "arxiv" && data.day !== data.latestDay ? data.day : null;
      const next = await api.feed(tab, viewing);
      if (started !== generation || !document.body.contains(list)) return; // on a changé de jour entre-temps
      data = next;
      remember();
      if (tab === "arxiv") (await api.alerts().catch(() => ({ unseen: [] }))).unseen.forEach((key) => fresh.add(key)); // apportés par la mise à jour
      draw();
    } catch (error) {
      notice.replaceChildren(h("p", { class: "notice" }, errorText(error.message)));
      if (!automatic) toast(errorText(error.message), { error: true, duration: 6000 });
    } finally {
      refresh.disabled = false;
      refresh.classList.remove("spinning");
      status.hidden = true;
      showStatus();
    }
  };
  refresh.addEventListener("click", () => { notice.replaceChildren(); doRefresh(); });

  const showStatus = () => {
    const stamp = data.refreshed?.[tab];
    let text = t("feed.updated", { when: ago(stamp) });
    if (tab === "recommended" && data.seeds) text += ` · ${t("feed.seeds", { pos: data.seeds[0], neg: data.seeds[1] })}`;
    refresh.title = `${t("feed.refresh")} · ${text}`; // date de la dernière mise à jour : au survol du bouton
  };

  const draw = () => {
    abstractObserver.disconnect(); // cartes précédentes : plus observées (elles vont être remplacées)
    tools.update();
    const needle = state.search.trim().toLowerCase();
    const matches = (item) => (item.status !== "disliked" || state.showHidden)
      && (!needle || `${item.title} ${item.authors.join(" ")} ${item.abstract}`.toLowerCase().includes(needle));
    const inCategories = (item) => !state.categories.size || item.categories.some((c) => state.categories.has(c));
    // rien du tout (premier lancement) ; un jour sans annonce garde ses flèches
    if (!data.items.length && (tab !== "arxiv" || !data.day)) {
      list.replaceChildren(h("div", { class: "empty" }, t(tab === "recommended" ? "feed.emptyS2" : "feed.emptyArxiv")));
      return;
    }
    const byRelevance = state.sort === "relevance";
    const card = (item) => feedCard(item, draw, { fresh: fresh.has(item.key), showDate: Boolean(item.followed && item.in_arxiv && item.announced !== data.day),
      compact: byRelevance && item.relevance < COMPACT_BELOW && !item.libraryId && item.status !== "liked" && !fresh.has(item.key)
        && !item.citing?.length });
    const sections = [];
    let shown;
    if (tab === "arxiv") {
      // Nouveaux articles des auteurs suivis, hors des annonces de vos catégories
      // tous les articles récents des auteurs suivis, sauf ceux de l'annonce affichée (ils y sont déjà)
      const inDayList = (i) => i.in_arxiv && i.announced === data.day && state.types.has(i.announce_type) && inCategories(i);
      // Articles qui citent les vôtres (Semantic Scholar), trouvés ces dernières semaines
      const citing = data.items.filter((i) => i.citing?.length && !inDayList(i) && matches(i))
        .sort((a, b) => (b.cited_found || "").localeCompare(a.cited_found || "") || (b.announced || "").localeCompare(a.announced || ""));
      // repliée par défaut : un clic sur l'intertitre l'ouvre
      if (citing.length) sections.push(foldableSection("citingCollapsed", t("feed.citingTitle"), citing.map(card), { extraClass: "citing-section", collapsedByDefault: true }));
      const followed = data.items.filter((i) => i.followed && !i.citing?.length && !inDayList(i) && matches(i))
        .sort((a, b) => (b.announced || "").localeCompare(a.announced || ""));
      if (followed.length) sections.push(foldableSection("followedCollapsed", t("feed.followedTitle"), followed.map(card)));
      shown = data.items.filter((i) => inDayList(i) && matches(i));
    } else {
      shown = data.items.filter((i) => inCategories(i) && matches(i));
    }
    if (byRelevance) shown.sort((a, b) => b.relevance - a.relevance);
    if (tab === "arxiv") {
      const close = shown.filter((i) => i.relevance >= 0.25).length;
      dayLabel = h("span", { class: "day-label" }, data.day
        ? capitalize(`${t("feed.announced", { date: formatDay(data.day) })} · ${tn("feed.count", shown.length)}${close ? ` · ${tn("feed.closeCount", close)}` : ""}`)
        : t("feed.emptyArxiv"));
      const previous = h("button", { class: "icon-button day-arrow", type: "button", title: t("feed.previousDay"), "aria-label": t("feed.previousDay"), html: icons.chevronLeft });
      const next = h("button", { class: "icon-button day-arrow", type: "button", title: t("feed.nextDay"), "aria-label": t("feed.nextDay"),
        disabled: !data.day || data.day >= data.latestDay, html: icons.chevronRight });
      previous.addEventListener("click", () => goToDay(-1));
      next.addEventListener("click", () => goToDay(1));
      sections.push(h("section", { class: "feed-day" },
        h("div", { class: "day-nav" }, previous, dayLabel, next),
        shown.length ? shown.map(card) : h("div", { class: "empty" }, t("feed.noMatch"))));
    } else if (shown.length) {
      sections.push(h("section", { class: "feed-day" }, shown.map(card)));
    }
    list.replaceChildren(...(sections.length ? sections : [h("div", { class: "empty" }, t("feed.noMatch"))]));
    markSeenShown();
    typesetTex([list]).then(() => list.querySelectorAll(".feed-abstract").forEach((a) => a.updateMore?.())).catch(() => {});
  };

  showStatus();
  draw();
  const stale = hoursSince(data.refreshed?.[tab]) > (tab === "recommended" ? STALE_S2_HOURS : STALE_ARXIV_HOURS);
  if (stale) doRefresh(true);
  return () => {
    removeEventListener("keydown", onKey);
    removeEventListener("arxivreader:section-opened", markSeenShown);
    abstractObserver.disconnect();
    tools.close({ focus: false });
  };
}

// Barre du feed : recherche, puis trois boutons (tri, filtres, mise à jour), comme la bibliothèque ;
// menus « Trier » et « Filtres » en fenêtres déroulantes, filtres actifs en pastilles retirables
const DEFAULT_TYPES = ["new", "cross"];

function feedTools({ tab, state, data, draw, refresh }) {
  const search = h("input", { class: "text-input library-search", type: "search", value: state.search, placeholder: t("feed.search"),
    "aria-label": t("feed.search"), autocomplete: "off", spellcheck: "false" });
  const redraw = debounce(draw, 150);
  search.addEventListener("input", () => { state.search = search.value; redraw(); });
  // « Tri » et « Filtres » : boutons à icône et texte, comme « Filtres » dans la bibliothèque
  const menuButton = () => h("button", { class: "button filter-button", type: "button", "aria-haspopup": "dialog", "aria-expanded": "false" });
  const sortButton = menuButton();
  const filterButton = menuButton();
  const toolbar = h("div", { class: "library-tools feed-tools" },
    h("div", { class: "library-search-row feed-search-row" },
      h("span", { class: "search-field" }, h("span", { class: "search-icon", html: icons.search }), search),
      sortButton, filterButton, refresh));
  const active = h("div", { class: "active-filters", hidden: true });

  let menu = null;
  let owner = null;
  const onOutside = (event) => { if (menu && !menu.contains(event.target) && !owner.contains(event.target)) close({ focus: false }); };
  const onEscape = (event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } };
  function close({ focus = true } = {}) {
    if (!menu) return;
    menu.remove();
    menu = null;
    owner.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onOutside);
    document.removeEventListener("keydown", onEscape, true);
    if (focus) owner.focus();
  }
  const open = (button, fill) => {
    const same = owner === button && menu;
    close({ focus: false });
    if (same) return;
    owner = button;
    menu = h("div", { class: `filter-menu feed-menu${button === sortButton ? " sort-menu" : ""}`, role: "dialog", "aria-label": t(button === sortButton ? "feed.sortTitle" : "feed.filters") });
    fill();
    toolbar.append(menu);
    button.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onOutside);
    document.addEventListener("keydown", onEscape, true);
    menu.addEventListener("focusout", (event) => {
      if (event.relatedTarget && !menu?.contains(event.relatedTarget) && !owner.contains(event.relatedTarget)) close({ focus: false });
    });
    (menu.querySelector("input:checked, button, input") || menu).focus();
  };
  const changed = () => { draw(); if (menu && owner === filterButton) fillFilters(); };
  const section = (title, body) => h("div", { class: "filter-section" }, h("h3", {}, title), body);
  const chip = (label, on, toggle, focusId) => h("button", { class: "kind-filter", type: "button", "aria-pressed": String(on), dataset: { focus: focusId },
    onclick: () => { toggle(); changed(); } }, label);
  const foot = (resettable, reset) => h("div", { class: "filter-foot" },
    reset && h("button", { class: "button small-button", type: "button", disabled: !resettable, dataset: { role: "reset" },
      onclick: () => { reset(); changed(); } }, t("library.filters.reset")),
    h("span", { class: "spacer" }),
    h("button", { class: "button small-button primary", type: "button", dataset: { role: "done" }, onclick: () => close() }, t("library.filters.done")));

  // ---- Trier ----
  const sortKeys = ["relevance", tab === "recommended" ? "s2" : "arxiv"];
  const fillSort = () => {
    menu.replaceChildren(h("div", { class: "filter-body" },
      section(t("feed.sortTitle"), h("div", { class: "segmented filter-segmented", role: "radiogroup" },
        sortKeys.map((key) => h("label", {}, h("input", { type: "radio", name: "feed-sort", value: key, checked: state.sort === key,
          onchange: () => { state.sort = key; saveSettings({ [state.sortSetting]: key }); update(); draw(); } }), t(`feed.sort.${key}`)))))),
    foot(false, null));
  };
  sortButton.addEventListener("click", () => open(sortButton, fillSort));

  // ---- Filtres ----
  const toggleIn = (set, value, after = () => {}) => () => { if (set.has(value)) set.delete(value); else set.add(value); after(); };
  const saveTypes = () => saveSettings({ feedTypes: [...state.types] });
  const typesChanged = () => state.types.size !== DEFAULT_TYPES.length || DEFAULT_TYPES.some((type) => !state.types.has(type));
  const activeCount = () => (tab === "arxiv" ? state.categories.size + (typesChanged() ? 1 : 0) : 0) + (state.showHidden ? 1 : 0);
  const resetFilters = () => {
    state.categories.clear();
    state.showHidden = false;
    if (tab === "arxiv" && typesChanged()) { state.types = new Set(DEFAULT_TYPES); saveTypes(); }
  };
  function fillFilters() {
    const items = data().items;
    const disliked = items.filter((i) => i.status === "disliked").length;
    const focused = menu.contains(document.activeElement) ? document.activeElement.dataset.focus || document.activeElement.dataset.role : null;
    menu.replaceChildren(
      h("div", { class: "filter-body" },
        tab === "arxiv" && section(t("feed.filter.types"), h("div", { class: "filter-chips" },
          TYPES.map((type) => chip(t(`feed.type.${type}`), state.types.has(type), toggleIn(state.types, type, saveTypes), `type:${type}`)))),
        tab === "arxiv" && (data().categories || []).length > 0 && section(t("library.filters.categories"), h("div", { class: "filter-chips" },
          data().categories.map((c) => chip(c, state.categories.has(c), toggleIn(state.categories, c), `category:${c}`)))),
        section(t("feed.filter.hidden"), h("label", { class: "check small" },
          h("input", { type: "checkbox", checked: state.showHidden, dataset: { focus: "hidden" },
            onchange: (event) => { state.showHidden = event.target.checked; changed(); } }),
          t("feed.hiddenCount", { n: disliked })))),
      foot(activeCount() > 0, resetFilters));
    if (focused) (menu.querySelector(`[data-focus="${CSS.escape(focused)}"], [data-role="${CSS.escape(focused)}"]:not(:disabled)`)
      || menu.querySelector('[data-role="done"]')).focus();
  }
  filterButton.addEventListener("click", () => open(filterButton, fillFilters));

  // Pastilles des filtres actifs, et nombre sur le bouton Filtres ; tri en cours dans l'infobulle
  function update() {
    const count = activeCount();
    filterButton.replaceChildren(h("span", { class: "button-icon", html: icons.filter }), t("feed.filters"),
      count ? h("span", { class: "filter-count" }, String(count)) : "");
    sortButton.replaceChildren(h("span", { class: "button-icon", html: icons.sort }), t("feed.sortLabel"));
    filterButton.title = count ? `${t("feed.filters")} (${count})` : t("feed.filters");
    sortButton.title = `${t("feed.sortTitle")} : ${t(`feed.sort.${state.sort}`)}`;
    const pill = (label, remove) => h("span", { class: "active-filter" }, label,
      h("button", { type: "button", class: "active-filter-remove", title: t("library.filters.remove"), "aria-label": t("library.filters.removeOne", { label }),
        html: icons.close, onclick: () => { remove(); changed(); } }));
    const types = TYPES.filter((type) => state.types.has(type)).map((type) => t(`feed.type.${type}`)).join(", ");
    const pills = [
      tab === "arxiv" && typesChanged() && pill(types || t("feed.noType"), () => { state.types = new Set(DEFAULT_TYPES); saveTypes(); }),
      ...(tab === "arxiv" ? [...state.categories].map((c) => pill(c, () => state.categories.delete(c))) : []),
      state.showHidden && pill(t("feed.hiddenShown"), () => { state.showHidden = false; }),
    ].filter(Boolean);
    active.replaceChildren(...pills, pills.length > 1 ? h("button", { class: "link-button small", type: "button",
      onclick: () => { resetFilters(); changed(); } }, t("library.filters.reset")) : "");
    active.hidden = !pills.length;
  }

  return { toolbar, active, update, close, search };
}

function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function formatDay(iso) {
  const date = new Date(`${iso}T12:00:00`);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString(getLanguage() === "fr" ? "fr-FR" : "en-GB", { weekday: "long", day: "numeric", month: "long" });
}

export function feedCard(item, onChange = () => {}, { compact = false, fresh = false, showDate = false } = {}) {
  const authors = item.authors.length > 8 ? [...item.authors.slice(0, 8), t("feed.etAl")] : item.authors;
  // Résumé coupé à deux lignes : clic, Entrée ou Espace pour le déplier, ou le lien « Afficher tout le
  // résumé » dessous (seulement quand il est coupé)
  const abstract = h("p", { class: "feed-abstract", tabindex: 0, role: "button", "aria-expanded": "false" }, item.abstract || "");
  const more = h("button", { class: "link-button abstract-more", type: "button", hidden: true,
    onclick: (event) => { event.stopPropagation(); toggleAbstract(); } });
  const updateMore = () => {
    const open = abstract.classList.contains("open");
    more.textContent = t(open ? "feed.abstractLess" : "feed.abstractMore");
    more.hidden = !open && abstract.scrollHeight <= abstract.clientHeight + 1;
  };
  abstract.updateMore = updateMore; // aussi après la mise en forme des formules (la hauteur change)
  abstractObserver.observe(abstract);
  const toggleAbstract = () => {
    abstract.setAttribute("aria-expanded", String(abstract.classList.toggle("open")));
    updateMore();
  };
  abstract.addEventListener("click", toggleAbstract);
  abstract.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleAbstract(); } });

  const thumbs = [];
  const statusButton = (value, label, icon) => {
    // Pouce à la YouTube : contour au repos, plein une fois choisi
    const button = toggleIcon(icons.thumbOff, icons.thumbOn, { pressed: item.status === value, label, className: `thumb-${icon}` });
    thumbs.push(button);
    const set = async (next) => {
      await api.feedStatus(item.key, next);
      item.status = next;
      thumbs.forEach((b) => b.setAttribute("aria-pressed", "false"));
      if (next) button.setAttribute("aria-pressed", "true");
    };
    button.addEventListener("click", async () => {
      const next = item.status === value ? null : value;
      try {
        await set(next);
        if (next === "disliked") {
          onChange(); // la carte disparaît de la liste…
          toast(t("feed.hiddenOne"), { action: { label: t("common.undo"), onClick: () => set(null).then(onChange, toastError) } }); // …annulable
        }
      } catch (error) {
        toastError(error);
      }
    });
    return button;
  };

  // Clic sur le titre : ouvre l'article sans l'ajouter à la bibliothèque (il est seulement mis en cache).
  // Article sans version arXiv (recommandation) : sa page Semantic Scholar.
  const known = () => item.libraryId || item.readId;
  const title = item.arxiv_id
    ? h("a", { class: "feed-title-link", href: known() ? `#/paper/${encodeURIComponent(known())}` : `#/feed`, title: t("feed.read") }, tidyTitle(item.title))
    : h("a", { class: "feed-title-link", href: item.link, target: "_blank", rel: "noopener noreferrer" }, tidyTitle(item.title));
  if (item.arxiv_id) {
    const open = async (event) => {
      if (event.type === "auxclick" && event.button !== 1) return;
      event.preventDefault();
      event.stopPropagation();
      const newTab = wantsNewTab(event); // ⌘-clic ou clic du milieu : dans un nouvel onglet
      const front = event.shiftKey;
      if (known()) { openRoute(`#/paper/${encodeURIComponent(known())}`, newTab, front); return; }
      if (title.dataset.loading) return;
      title.dataset.loading = "1";
      const spinner = h("span", { class: "spinner title-spinner", "aria-label": t("feed.importing") });
      title.after(spinner);
      try {
        const paper = await api.importPaper(item.arxiv_id, false);
        item.readId = paper.id;
        openRoute(`#/paper/${encodeURIComponent(paper.id)}`, newTab, front);
      } catch (error) {
        toastError(error);
      } finally {
        delete title.dataset.loading;
        spinner.remove();
      }
    };
    title.addEventListener("click", open);
    title.addEventListener("auxclick", open);
  }

  const reasons = (item.reasons || []).map((reason) => h("span", { class: `reason reason-${reason.kind}` },
    h("span", { class: "reason-icon", html: icons[reason.kind === "author" ? "person" : reason.kind === "keyword" ? "key" : "similar"] }),
    h("span", { class: "reason-text" },
      reason.kind === "author" ? t("feed.reason.author", { name: reason.label })
        : reason.kind === "keyword" ? reason.label
          : [`${t("feed.reason.similar")} `, h("i", {}, withMath(tidyTitle(reason.label.length > 90 ? `${reason.label.slice(0, 88)}…` : reason.label))),
            reason.via === "reference" ? ` (${t("feed.reason.reference")})` : reason.via === "liked" ? ` (${t("feed.reason.liked")})` : ""])));
  const level = item.relevance === undefined ? null : item.relevance >= 0.6 ? "high" : item.relevance >= 0.25 ? "medium" : item.relevance >= COMPACT_BELOW ? "low" : "none";
  const meter = level && level !== "none" && h("span", { class: `relevance relevance-${level}`, title: t("feed.relevanceHint", { n: Math.round(item.relevance * 100) }) },
    h("span", { class: "relevance-bar" }, h("span", { style: `width: ${Math.round(item.relevance * 100)}%` })), t(`feed.level.${level}`));

  const linker = item.arxiv_id ? null : arxivLinker(item, () => card, (next) => feedCard(next, onChange, { fresh }));
  const card = h("article", { class: `feed-card${compact ? " compact" : ""}${fresh ? " fresh" : ""}`, dataset: { key: item.key } },
    h("div", { class: "feed-title-row" }, h("h3", { class: "feed-title" }, fresh && h("span", { class: "fresh-badge" }, t("alerts.new")), title), meter),
    h("div", { class: "feed-authors" }, authors.join(", ")),
    h("div", { class: "card-meta" },
      item.primary_category && h("span", { class: "chip category" }, item.primary_category),
      ...item.categories.filter((c) => c !== item.primary_category).map((c) => h("span", { class: "chip" }, c)),
      item.announce_type && h("span", { class: `chip type-${item.announce_type}` }, t(`feed.badge.${item.announce_type}`)),
      item.arxiv_id && h("span", { class: "chip" }, `arXiv:${item.arxiv_id}`),
      (!item.announce_type || showDate) && item.announced && h("span", { class: "chip" }, item.announced),
      item.announce_type && item.s2_rank !== null && item.s2_rank !== undefined && h("span", { class: "chip recommended-chip", title: t("feed.recommendedHint") }, t("feed.recommended")),
      !item.arxiv_id && h("span", { class: "chip off-arxiv", title: t("feed.offArxivHint") }, t("feed.offArxiv")),
      ...(item.citing || []).map((cited) => h("span", { class: "chip citing-chip", title: t("feed.citingHint", { title: cited }) },
        t("feed.cites", { title: cited.length > 48 ? `${cited.slice(0, 46)}…` : cited }))),
    ),
    reasons.length ? h("div", { class: "reasons" }, reasons) : null,
    item.abstract && abstract,
    item.abstract && more,
    linker?.form,
    h("div", { class: "feed-actions" },
      statusButton("liked", t("feed.like"), "up"),
      statusButton("disliked", t("feed.dislike"), "down"),
      item.arxiv_id && libraryButton(item),
      linker?.toggle,
      h("span", { class: "spacer" }),
      item.link && h("a", { class: "icon-button", href: item.link, target: "_blank", rel: "noopener noreferrer", title: item.arxiv_id ? "arXiv" : "Semantic Scholar", html: icons.external }),
      item.arxiv_id && h("a", { class: "icon-button", href: `https://arxiv.org/pdf/${item.arxiv_id}`, target: "_blank", rel: "noopener noreferrer", title: "PDF", html: icons.pdf })));
  // Article peu pertinent : une ligne ; un clic sur le titre déplie la carte
  // Carte réduite à une ligne : le titre ouvre l'article, un clic ailleurs sur la ligne la déplie
  if (compact) {
    const expand = () => { card.classList.remove("compact"); card.removeAttribute("tabindex"); card.removeAttribute("aria-expanded"); };
    card.tabIndex = 0;
    card.setAttribute("aria-expanded", "false");
    card.addEventListener("click", (event) => { if (!event.target.closest("a, button")) expand(); });
    card.addEventListener("keydown", (event) => { if (event.target === card && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); expand(); } });
  }
  return card;
}

// Section repliable (« Citent vos articles », « Nouveaux articles de vos auteurs suivis ») : l'intertitre
// porte une flèche à droite ; l'état replié est retenu dans les réglages (clé settingKey)
// Lien « Afficher tout le résumé » recalculé quand la largeur change ; un seul observateur pour tout le feed,
// vidé à chaque nouvel affichage (voir draw)
const abstractObserver = new ResizeObserver((entries) => entries.forEach((entry) => entry.target.updateMore?.()));

function foldableSection(settingKey, title, cards, { extraClass = "", collapsedByDefault = false } = {}) {
  const section = h("section", { class: `feed-day followed-section foldable ${extraClass}`.trim() });
  const toggle = h("button", { class: "section-toggle", type: "button",
    onclick: () => { const collapsed = !section.classList.contains("collapsed"); setCollapsed(collapsed); saveSettings({ [settingKey]: collapsed }); } },
    h("span", { class: "label" }, title, h("span", { class: "count" }, tn("feed.count", cards.length))),
    h("span", { class: "chevron", html: icons.chevronRight }));
  // articles dans un conteneur qui se déroule et s'enroule (hauteur animée, comme les dossiers de la bibliothèque)
  const inner = h("div", { class: "fold-inner" }, cards);
  const body = h("div", { class: "fold-body" }, inner);
  const setCollapsed = (collapsed, animate = true) => {
    // à l'ouverture, les articles apparaissent l'un après l'autre (pas au premier affichage du feed)
    cards.forEach((card, i) => card.style.setProperty("--i", Math.min(i, 8)));
    section.classList.toggle("opening", animate && !collapsed);
    if (animate && !collapsed) setTimeout(() => section.classList.remove("opening"), 900);
    section.classList.toggle("collapsed", collapsed);
    body.inert = collapsed; // repliée : ses liens ne reçoivent plus le focus
    if (animate && !collapsed) dispatchEvent(new Event("arxivreader:section-opened")); // nouveaux articles vus
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.title = t(collapsed ? "feed.expandSection" : "feed.collapseSection");
  };
  setCollapsed(settings[settingKey] ?? collapsedByDefault, false);
  section.append(h("h2", { class: "feed-date" }, toggle), body);
  return section;
}

// Recommandation sans version arXiv connue : on peut coller son lien arXiv (champ déplié sous la carte).
// Une fois reliée, la carte devient une carte arXiv ordinaire (lecture, signet, PDF).
function arxivLinker(item, currentCard, rebuild) {
  const input = h("input", { class: "text-input", type: "text", placeholder: t("feed.arxivPaste"), "aria-label": t("feed.arxivPaste"),
    autocomplete: "off", spellcheck: "false" });
  const go = h("button", { class: "button small-button primary", type: "submit" }, t("feed.arxivLink"));
  const cancel = h("button", { class: "button small-button", type: "button" }, t("panel.cancel"));
  const form = h("form", { class: "arxiv-finder-form", hidden: true }, input, go, cancel);
  const toggle = h("button", { class: "link-button small arxiv-add", type: "button", "aria-expanded": "false" },
    h("span", { class: "button-icon", html: icons.add }), t("feed.arxivAdd"));
  const show = (open) => {
    form.hidden = !open;
    toggle.hidden = open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) input.focus(); else toggle.focus();
  };
  toggle.addEventListener("click", () => show(true));
  cancel.addEventListener("click", () => show(false));
  input.addEventListener("keydown", (event) => { if (event.key === "Escape") show(false); });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!input.value.trim()) { input.focus(); return; }
    go.disabled = true;
    try {
      const { arxiv_id: arxivId, title } = await api.linkArxiv(item.key, input.value.trim());
      item.arxiv_id = arxivId;
      item.link = `https://arxiv.org/abs/${arxivId}`;
      const next = rebuild(item);
      currentCard().replaceWith(next);
      next.querySelector(".feed-title-link")?.focus();
      typesetTex([next]).catch(() => {});
      toast(t("feed.arxivLinked", { id: arxivId, title }), { duration: 6000 });
    } catch (error) {
      toastError(error);
    } finally {
      go.disabled = false;
    }
  });
  return { toggle, form };
}

// Signet : ajouter l'article à la bibliothèque (ou l'en retirer) sans quitter le feed ni redessiner la liste
function libraryButton(item) {
  const label = () => t(item.libraryId ? "feed.removeFromLibrary" : "feed.addToLibrary");
  const button = toggleIcon(icons.bookmarkOff, icons.bookmarkOn, { pressed: Boolean(item.libraryId), label: label(), className: "bookmark" });
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      if (item.libraryId) {
        await api.setInLibrary(item.libraryId, false);
        item.readId = item.libraryId;
        item.libraryId = null;
      } else if (item.readId) {
        await api.setInLibrary(item.readId, true);
        item.libraryId = item.readId;
      } else {
        const paper = await api.importPaper(item.arxiv_id, true);
        item.libraryId = item.readId = paper.id;
      }
      toast(t(item.libraryId ? "feed.added" : "feed.removed"));
      button.setAttribute("aria-pressed", String(Boolean(item.libraryId)));
      button.title = label();
      button.setAttribute("aria-label", label());
    } catch (error) {
      toastError(error);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

// ---------- Import d'un texte collé ou d'un fichier ----------

function renderImport(body) {
  const text = h("textarea", { class: "text-input paste-box", rows: 10, placeholder: t("import.placeholder"), "aria-label": t("import.placeholder") });
  const file = h("input", { type: "file", accept: ".bib,.txt,.eml,.html,.htm,.md,text/*", class: "visually-hidden", id: "import-file" });
  const fileLabel = h("label", { class: "button", for: "import-file" }, t("import.file"));
  const analyze = h("button", { class: "button primary", type: "button" }, t("import.analyze"));
  const results = h("div", {});
  file.addEventListener("change", async () => {
    const chosen = file.files?.[0];
    if (!chosen) return;
    text.value = await chosen.text();
    analyze.click();
  });
  analyze.addEventListener("click", async () => {
    if (!text.value.trim()) return;
    analyze.disabled = true;
    results.replaceChildren(h("div", { class: "busy" }, h("span", { class: "spinner" }), t("import.searching")));
    try {
      showResults(results, await api.lookup(text.value));
    } catch (error) {
      results.replaceChildren(h("p", { class: "notice" }, t("error.generic", { message: error.message })));
    } finally {
      analyze.disabled = false;
    }
  });
  body.replaceChildren(
    h("p", { class: "lead" }, t("import.lead")),
    text,
    h("div", { class: "import-actions" }, analyze, fileLabel, file),
    results,
  );
}

function showResults(results, { papers, missing }) {
  if (!papers.length) {
    results.replaceChildren(h("div", { class: "empty" }, t("import.none")));
    return;
  }
  const boxes = papers.map((paper) => h("input", { type: "checkbox", checked: !paper.libraryId, disabled: Boolean(paper.libraryId) }));
  const count = () => boxes.filter((b) => b.checked && !b.disabled).length;
  const go = h("button", { class: "button primary", type: "button" });
  const progress = h("div", { class: "muted small" });
  const label = () => { go.textContent = t("import.selected", { n: count() }); go.disabled = count() === 0; };
  boxes.forEach((box) => box.addEventListener("change", label));
  label();
  go.addEventListener("click", async () => {
    go.disabled = true;
    const chosen = papers.filter((_, i) => boxes[i].checked && !boxes[i].disabled);
    const failures = [];
    for (const [index, paper] of chosen.entries()) {
      progress.replaceChildren(h("span", { class: "spinner" }), " ", t("import.progress", { i: index + 1, n: chosen.length, title: paper.title }));
      try {
        const imported = await api.importPaper(paper.arxiv_id);
        paper.libraryId = imported.id;
        const box = boxes[papers.indexOf(paper)];
        box.checked = false;
        box.disabled = true;
        box.closest(".import-row").classList.add("done");
      } catch (error) {
        failures.push(`${paper.arxiv_id} (${error.message})`);
      }
    }
    progress.replaceChildren(failures.length ? t("import.failures", { list: failures.join(", ") }) : tn("import.done", chosen.length));
    label();
  });
  results.replaceChildren(
    h("h2", { class: "import-heading" }, tn("import.found", papers.length)),
    h("div", { class: "import-list" }, papers.map((paper, i) => h("label", { class: `import-row${paper.libraryId ? " done" : ""}` },
      boxes[i],
      h("span", { class: "import-text" },
        h("span", { class: "feed-title" }, paper.title),
        h("span", { class: "muted small" }, `${paper.authors.slice(0, 6).join(", ")}${paper.authors.length > 6 ? ` ${t("feed.etAl")}` : ""} · arXiv:${paper.arxiv_id} · ${paper.published}`),
        paper.libraryId && h("span", { class: "chip in-library-chip" }, t("feed.inLibrary")))))),
    missing.length ? h("p", { class: "muted small" }, t("import.missing", { list: missing.join(", ") })) : "",
    h("div", { class: "import-actions" }, go, progress),
  );
  typesetTex([results]).catch(() => {});
}
