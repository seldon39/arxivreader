// Page « Feed » : nouveautés arXiv, recommandations Semantic Scholar, import d'un texte collé.

import { api } from "./api.js";
import { getLanguage, t, tn } from "./i18n.js";
import { typesetTex } from "./math.js";
import { renderProfile } from "./profile.js";
import { saveSettings, settings } from "./settings.js";
import { debounce, h, hoursSince, icons, timeAgo, toast, toastError, toggleIcon, withMath } from "./util.js";

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
  const status = h("span", { class: "muted small" });
  const refresh = h("button", { class: "button", type: "button" }, h("span", { html: icons.refresh, class: "button-icon" }), t("feed.refresh"));
  const filters = h("div", { class: "feed-filters" });
  const list = h("div", { class: "feed-list" });
  const notice = h("div", {});
  body.replaceChildren(h("div", { class: "feed-toolbar" }, status, refresh), notice, filters, list);
  list.replaceChildren(h("div", { class: "busy" }, h("span", { class: "spinner" }), t("common.loading")));

  let data = await api.feed(tab, tab === "arxiv" ? memory.day : null);
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
        if (next.items.some((i) => i.in_arxiv) || last || attempt === 4) { data = next; break; }
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
  // la recherche, les catégories et les articles écartés sont ceux de la mémoire (gardés d'une visite à l'autre)
  const state = Object.assign(memory, { types: new Set(settings.feedTypes || ["new", "cross"]), sort: settings.feedSort || "relevance" });

  const doRefresh = async (automatic = false) => {
    refresh.disabled = true;
    status.replaceChildren(h("span", { class: "spinner" }), " ", t(tab === "recommended" ? "feed.loadingS2" : "feed.loadingArxiv"));
    const started = generation;
    try {
      await api.refreshFeed(tab);
      const viewing = tab === "arxiv" && data.day !== data.latestDay ? data.day : null;
      const next = await api.feed(tab, viewing);
      if (started !== generation || !document.body.contains(list)) return; // on a changé de jour entre-temps
      data = next;
      remember();
      draw();
    } catch (error) {
      notice.replaceChildren(h("p", { class: "notice" }, errorText(error.message)));
      if (!automatic) toast(errorText(error.message), { error: true, duration: 6000 });
    } finally {
      refresh.disabled = false;
      showStatus();
    }
  };
  refresh.addEventListener("click", () => { notice.replaceChildren(); doRefresh(); });

  const showStatus = () => {
    const stamp = data.refreshed?.[tab];
    let text = t("feed.updated", { when: ago(stamp) });
    if (tab === "recommended" && data.seeds) text += ` · ${t("feed.seeds", { pos: data.seeds[0], neg: data.seeds[1] })}`;
    status.replaceChildren(text);
  };

  const draw = () => {
    renderFilters(filters, tab, data, state, draw);
    const needle = state.search.trim().toLowerCase();
    const matches = (item) => (item.status !== "disliked" || state.showHidden)
      && (!needle || `${item.title} ${item.authors.join(" ")} ${item.abstract}`.toLowerCase().includes(needle));
    const inCategories = (item) => !state.categories.size || item.categories.some((c) => state.categories.has(c));
    // rien du tout (premier lancement) ; un jour sans annonce garde ses flèches
    if (!data.items.length && (tab !== "arxiv" || !data.day)) {
      list.replaceChildren(h("div", { class: "empty" }, t(tab === "recommended" ? "feed.emptyS2" : "feed.emptyArxiv")));
      return;
    }
    const byRelevance = tab === "arxiv" && state.sort === "relevance";
    const card = (item) => feedCard(item, draw, { compact: byRelevance && item.relevance < COMPACT_BELOW && !item.libraryId && item.status !== "liked" });
    const sections = [];
    let shown;
    if (tab === "arxiv") {
      // Nouveaux articles des auteurs suivis, hors des annonces de vos catégories
      const followed = data.items.filter((i) => i.followed && !i.in_arxiv && matches(i))
        .sort((a, b) => (b.announced || "").localeCompare(a.announced || ""));
      if (followed.length) {
        sections.push(h("section", { class: "feed-day followed-section" },
          h("h2", { class: "feed-date" }, `${t("feed.followedTitle")} · ${tn("feed.count", followed.length)}`),
          followed.map(card)));
      }
      shown = data.items.filter((i) => i.in_arxiv && state.types.has(i.announce_type) && inCategories(i) && matches(i));
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
    typesetTex([list]).catch(() => {});
  };

  showStatus();
  draw();
  const stale = hoursSince(data.refreshed?.[tab]) > (tab === "recommended" ? STALE_S2_HOURS : STALE_ARXIV_HOURS);
  if (stale) doRefresh(true);
  return () => removeEventListener("keydown", onKey);
}

function renderFilters(filters, tab, data, state, draw) {
  const disliked = data.items.filter((i) => i.status === "disliked").length;
  if (filters.dataset.ready) {
    // les compteurs changent ; le reste du formulaire est conservé
    filters.querySelector(".hidden-count").textContent = t("feed.hiddenCount", { n: disliked });
    filters.querySelector(".hidden-toggle").hidden = !disliked && !state.showHidden;
    return;
  }
  filters.dataset.ready = "1";
  const search = h("input", { class: "text-input", type: "search", value: state.search, placeholder: t("feed.search"), "aria-label": t("feed.search") });
  const redraw = debounce(draw, 150);
  search.addEventListener("input", () => { state.search = search.value; redraw(); });
  const chip = (label, active, onToggle) => {
    const button = h("button", { class: "kind-filter", type: "button", "aria-pressed": String(active) }, label);
    button.addEventListener("click", () => {
      const now = button.getAttribute("aria-pressed") !== "true";
      button.setAttribute("aria-pressed", String(now));
      onToggle(now);
      draw();
    });
    return button;
  };
  const row = [search];
  if (tab === "arxiv") {
    const keys = ["relevance", "arxiv"];
    const sortButtons = keys.map((key) => {
      const button = h("button", { class: "kind-filter", type: "button", "aria-pressed": String(state.sort === key) }, t(`feed.sort.${key}`));
      button.addEventListener("click", () => {
        state.sort = key;
        sortButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(keys[i] === key)));
        saveSettings({ feedSort: key });
        draw();
      });
      return button;
    });
    row.push(h("span", { class: "filter-group" }, h("span", { class: "muted small sort-label" }, t("feed.sortLabel")), sortButtons));
  }
  const hidden = h("label", { class: "check small hidden-toggle", hidden: !disliked && !state.showHidden }, h("input", { type: "checkbox", checked: state.showHidden }),
    h("span", { class: "hidden-count" }, t("feed.hiddenCount", { n: disliked })));
  hidden.querySelector("input").addEventListener("change", (event) => { state.showHidden = event.target.checked; draw(); });
  if (tab !== "arxiv") {
    filters.replaceChildren(...row, hidden);
    return;
  }
  // Types d'annonce et catégories : repliés sur petit écran, avec le résumé des filtres actifs
  const summary = h("span", { class: "muted small" });
  const describe = () => {
    const types = state.types.size === TYPES.length ? t("feed.allTypes")
      : state.types.size ? TYPES.filter((type) => state.types.has(type)).map((type) => t(`feed.type.${type}`)).join(", ") : t("feed.noType");
    const categories = state.categories.size ? [...state.categories].join(", ") : t("feed.allCategories");
    summary.textContent = `${types} · ${categories}`;
  };
  const more = h("details", { class: "feed-more", open: innerWidth >= 700 },
    h("summary", {}, h("span", { class: "feed-more-title" }, t("feed.filters")), summary),
    h("div", { class: "feed-more-body" },
      h("span", { class: "filter-group" }, TYPES.map((type) => chip(t(`feed.type.${type}`), state.types.has(type), (on) => {
        if (on) state.types.add(type); else state.types.delete(type);
        saveSettings({ feedTypes: [...state.types] });
        describe();
      }))),
      h("span", { class: "filter-group" }, (data.categories || []).map((category) => chip(category, state.categories.has(category), (on) => {
        if (on) state.categories.add(category); else state.categories.delete(category);
        describe();
      }))),
      hidden));
  describe();
  filters.replaceChildren(...row, more);
}

function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function formatDay(iso) {
  const date = new Date(`${iso}T12:00:00`);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString(getLanguage() === "fr" ? "fr-FR" : "en-GB", { weekday: "long", day: "numeric", month: "long" });
}

function feedCard(item, onChange = () => {}, { compact = false } = {}) {
  const authors = item.authors.length > 8 ? [...item.authors.slice(0, 8), t("feed.etAl")] : item.authors;
  // Résumé coupé à trois lignes : clic, Entrée ou Espace pour le déplier
  const abstract = h("p", { class: "feed-abstract", tabindex: 0, role: "button", "aria-expanded": "false" }, item.abstract || "");
  const toggleAbstract = () => abstract.setAttribute("aria-expanded", String(abstract.classList.toggle("open")));
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
    ? h("a", { class: "feed-title-link", href: known() ? `#/paper/${encodeURIComponent(known())}` : `#/feed`, title: t("feed.read") }, item.title)
    : h("a", { class: "feed-title-link", href: item.link, target: "_blank", rel: "noopener noreferrer" }, item.title);
  if (item.arxiv_id) {
    title.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (known()) { location.hash = `#/paper/${encodeURIComponent(known())}`; return; }
      if (title.dataset.loading) return;
      title.dataset.loading = "1";
      const spinner = h("span", { class: "spinner title-spinner", "aria-label": t("feed.importing") });
      title.after(spinner);
      try {
        const paper = await api.importPaper(item.arxiv_id, false);
        location.hash = `#/paper/${encodeURIComponent(paper.id)}`;
      } catch (error) {
        toastError(error);
      } finally {
        delete title.dataset.loading;
        spinner.remove();
      }
    });
  }

  const reasons = (item.reasons || []).map((reason) => h("span", { class: `reason reason-${reason.kind}` },
    h("span", { class: "reason-icon", html: icons[reason.kind === "author" ? "person" : reason.kind === "keyword" ? "key" : "similar"] }),
    h("span", { class: "reason-text" },
      reason.kind === "author" ? t("feed.reason.author", { name: reason.label })
        : reason.kind === "keyword" ? reason.label
          : [`${t("feed.reason.similar")} `, h("i", {}, withMath(reason.label.length > 90 ? `${reason.label.slice(0, 88)}…` : reason.label)),
            reason.via === "reference" ? ` (${t("feed.reason.reference")})` : reason.via === "liked" ? ` (${t("feed.reason.liked")})` : ""])));
  const level = item.relevance === undefined ? null : item.relevance >= 0.6 ? "high" : item.relevance >= 0.25 ? "medium" : item.relevance >= COMPACT_BELOW ? "low" : "none";
  const meter = level && level !== "none" && h("span", { class: `relevance relevance-${level}`, title: t("feed.relevanceHint", { n: Math.round(item.relevance * 100) }) },
    h("span", { class: "relevance-bar" }, h("span", { style: `width: ${Math.round(item.relevance * 100)}%` })), t(`feed.level.${level}`));

  const card = h("article", { class: `feed-card${compact ? " compact" : ""}` },
    h("div", { class: "feed-title-row" }, h("h3", { class: "feed-title" }, title), meter),
    h("div", { class: "feed-authors" }, authors.join(", ")),
    h("div", { class: "card-meta" },
      item.primary_category && h("span", { class: "chip category" }, item.primary_category),
      ...item.categories.filter((c) => c !== item.primary_category).map((c) => h("span", { class: "chip" }, c)),
      item.announce_type && h("span", { class: `chip type-${item.announce_type}` }, t(`feed.badge.${item.announce_type}`)),
      item.arxiv_id && h("span", { class: "chip" }, `arXiv:${item.arxiv_id}`),
      !item.announce_type && item.announced && h("span", { class: "chip" }, item.announced),
      item.announce_type && item.s2_rank !== null && item.s2_rank !== undefined && h("span", { class: "chip recommended-chip", title: t("feed.recommendedHint") }, t("feed.recommended")),
    ),
    reasons.length ? h("div", { class: "reasons" }, reasons) : null,
    item.abstract && abstract,
    h("div", { class: "feed-actions" },
      statusButton("liked", t("feed.like"), "up"),
      statusButton("disliked", t("feed.dislike"), "down"),
      item.arxiv_id && libraryButton(item),
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
