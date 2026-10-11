// Page « Recherche » : recherche dans tout arXiv, avec les options de la recherche avancée d'arXiv
// (champs, ET / OU / SAUF, catégories, articles croisés, période, tri) ; résultats présentés comme le feed.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { typesetTex } from "./math.js";
import { FEED_DEFAULT, settings } from "./settings.js";
import { h, toastError } from "./util.js";
import { feedCard } from "./feed.js";

const FIELDS = ["all", "title", "author", "abstract", "comments", "journal", "category", "report", "id"];
const OPERATORS = ["and", "or", "not"];
const CATEGORY = /^[a-z-]+(\.[A-Za-z-]+)?$/;

// Critères, options et résultats gardés le temps de la session (retour depuis le lecteur : tout est là)
const state = {
  terms: [{ field: "all", value: "", op: "and" }],
  categories: new Set(),
  extraCategories: [],
  cross: true,
  dates: { mode: "all", year: new Date().getFullYear(), from: "", to: "" },
  sort: "relevance",
  page: 0,
  results: null, // { items, total, start, pageSize, request }
};

export async function renderSearch(root) {
  const termsBox = h("div", { class: "search-terms" });
  const optionsBox = h("div", { class: "search-options" });
  const go = h("button", { class: "button primary", type: "submit" }, t("search.go"));
  const busy = h("span", { class: "busy", hidden: true }, h("span", { class: "spinner" }), t("search.searching"));
  const resultsBox = h("div", { class: "search-results" });
  let running = 0;

  // ---- Critères ----
  const select = (kind, options, value, onchange) => h("select", { class: "search-select", "aria-label": t(`search.${kind}Label`), onchange },
    options.map((key) => h("option", { value: key, selected: key === value }, t(`search.${kind}.${key}`))));
  function drawTerms(focusIndex = null) {
    termsBox.replaceChildren(...state.terms.map((term, i) => {
      const input = h("input", { class: "text-input", type: "text", value: term.value, spellcheck: "false", autocomplete: "off",
        placeholder: t(term.field === "author" ? "search.placeholderAuthor" : term.field === "id" ? "search.placeholderId" : "search.placeholder"),
        "aria-label": t("search.valueLabel"), oninput: (event) => { term.value = event.target.value; } });
      if (i === focusIndex) requestAnimationFrame(() => input.focus());
      return h("div", { class: "search-term" },
        i === 0 ? h("span", { class: "search-first" }, t("search.first"))
          : select("op", OPERATORS, term.op, (event) => { term.op = event.target.value; }),
        select("field", FIELDS, term.field, (event) => { term.field = event.target.value; drawTerms(); }),
        input,
        h("button", { class: "icon-button search-remove", type: "button", title: t("search.remove"), "aria-label": t("search.remove"),
          disabled: state.terms.length === 1 && !term.value,
          onclick: () => {
            if (state.terms.length === 1) term.value = "";
            else state.terms.splice(i, 1);
            drawTerms(Math.max(0, i - 1));
          } }, "×"));
    }), h("button", { class: "link-button search-add", type: "button", onclick: () => {
      state.terms.push({ field: "all", value: "", op: "and" });
      drawTerms(state.terms.length - 1);
    } }, t("search.add")));
  }

  // ---- Options : catégories, articles croisés, période, tri ----
  const segmented = (name, options, current, pick) => h("span", { class: "segmented search-segmented", role: "radiogroup", "aria-label": t(`search.${name}`) },
    options.map(([value, label]) => h("label", {},
      h("input", { type: "radio", name: `search-${name}`, value, checked: value === current, onchange: () => { pick(value); drawOptions(); } }), label)));
  function drawOptions() {
    const offered = [...new Set([...(settings.feedCategories || FEED_DEFAULT), ...state.extraCategories, ...state.categories])];
    const chip = (category) => h("button", { class: "kind-filter", type: "button", "aria-pressed": String(state.categories.has(category)),
      onclick: () => { if (state.categories.has(category)) state.categories.delete(category); else state.categories.add(category); drawOptions(); } }, category);
    const other = h("input", { class: "text-input search-other", type: "text", placeholder: t("search.otherCategory"), "aria-label": t("search.otherCategory"),
      spellcheck: "false", hidden: true, onkeydown: (event) => {
        if (event.key === "Escape") { other.hidden = true; addOther.hidden = false; return; }
        if (event.key !== "Enter") return;
        event.preventDefault();
        const value = other.value.trim();
        if (!CATEGORY.test(value)) { toastError(new Error(t("search.badCategory"))); return; }
        if (!offered.includes(value)) state.extraCategories.push(value);
        state.categories.add(value);
        drawOptions();
      } });
    const addOther = h("button", { class: "kind-filter", type: "button", onclick: () => { addOther.hidden = true; other.hidden = false; other.focus(); } }, t("search.addCategory"));
    const { dates } = state;
    const yearInput = h("input", { class: "text-input search-year", type: "number", min: 1991, max: new Date().getFullYear(), value: dates.year,
      "aria-label": t("search.year"), oninput: (event) => { dates.year = Number(event.target.value); } });
    const dateInput = (key) => h("input", { class: "text-input search-date", type: "date", value: dates[key], "aria-label": t(`search.${key}`),
      oninput: (event) => { dates[key] = event.target.value; } });
    optionsBox.replaceChildren(
      h("div", { class: "search-row" },
        h("span", { class: "search-label" }, t("search.categories")),
        h("span", { class: "search-group" }, offered.map(chip), addOther, other),
        h("label", { class: "check search-cross" }, h("input", { type: "checkbox", checked: state.cross, disabled: !state.categories.size,
          onchange: (event) => { state.cross = event.target.checked; } }), t("search.cross"))),
      h("div", { class: "search-row" },
        h("span", { class: "search-label" }, t("search.dates")),
        segmented("dates", [["all", t("search.dates.all")], ["months", t("search.dates.months")], ["year", t("search.dates.year")],
          ["range", t("search.dates.range")]], dates.mode, (value) => { dates.mode = value; }),
        dates.mode === "year" && yearInput,
        dates.mode === "range" && h("span", { class: "search-group" }, dateInput("from"), "–", dateInput("to")),
        h("span", { class: "search-label search-sort-label" }, t("search.sort")),
        segmented("sort", [["relevance", t("search.sort.relevance")], ["profile", t("search.sort.profile")],
          ["newest", t("search.sort.newest")], ["oldest", t("search.sort.oldest")]],
          state.sort, (value) => { state.sort = value; })));
  }

  // ---- Recherche et résultats ----
  async function run(page = 0) {
    const request = {
      terms: state.terms.filter((term) => term.value.trim()).map(({ field, value, op }) => ({ field, value, op })),
      categories: [...state.categories], cross: state.cross, dates: state.dates, sort: state.sort, page,
    };
    if (!request.terms.length && !request.categories.length) { toastError(new Error(t("search.empty"))); return; }
    const token = ++running;
    go.disabled = true;
    busy.hidden = false;
    try {
      const results = await api.search(request);
      if (token !== running || !root.isConnected) return;
      state.page = page;
      state.results = { ...results, request };
      drawResults();
      resultsBox.scrollIntoView({ block: "start", behavior: page ? "smooth" : "auto" });
    } catch (error) {
      if (token === running) toastError(error);
    } finally {
      if (token === running) { go.disabled = false; busy.hidden = true; }
    }
  }

  function drawResults() {
    const results = state.results;
    if (!results) { resultsBox.replaceChildren(); return; }
    const { items, total, start, pageSize } = results;
    const last = Math.min(start + pageSize, total);
    const sortName = t(`search.sortedBy.${results.request.sort}`);
    const pager = h("div", { class: "search-pager" },
      h("button", { class: "button small-button", type: "button", disabled: start === 0, onclick: () => run(state.page - 1) }, t("search.previous")),
      h("button", { class: "button small-button", type: "button", disabled: last >= total, onclick: () => run(state.page + 1) }, t("search.next")));
    resultsBox.replaceChildren(
      h("div", { class: "search-results-head" },
        h("h2", {}, t("search.results")),
        h("span", { class: "muted small", title: results.pool ? t(results.profile ? "search.poolHint" : "search.poolHintCross", { pool: results.pool }) : null }, !total ? ""
          // « pour vous » : les meilleurs résultats d'arXiv, reclassés selon votre profil
          : results.pool ? t("search.rangePool", { from: start + 1, to: last, total, arxiv: results.arxivTotal, sort: sortName })
            : t("search.range", { from: start + 1, to: last, total, sort: sortName }))),
      items.length
        ? h("div", { class: "feed-day" }, items.map((item) => feedCard(item, () => {}, { showDate: true })))
        : h("div", { class: "empty" }, t(total ? "search.noneOnPage" : "search.none")),
      total > pageSize ? pager : "");
    typesetTex([resultsBox]).then(() => resultsBox.querySelectorAll(".feed-abstract").forEach((a) => a.updateMore?.())).catch(() => {});
  }

  const form = h("form", { class: "search-form", onsubmit: (event) => { event.preventDefault(); run(0); } },
    termsBox, optionsBox, h("div", { class: "search-go" }, go, h("span", { class: "muted small" }, t("search.hint")), busy));
  root.replaceChildren(h("section", { class: "search-page" }, h("h1", {}, t("search.title")), form, resultsBox));
  drawTerms(state.results ? null : 0);
  drawOptions();
  drawResults();
  return () => { running++; };
}
