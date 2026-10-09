// Page « Feed » : nouveautés arXiv, recommandations Semantic Scholar, import d'un texte collé.

import { api } from "./api.js";
import { getLanguage, t } from "./i18n.js";
import { typesetTex } from "./math.js";
import { saveSettings, settings } from "./settings.js";
import { h, icons, toast } from "./util.js";

const TABS = [["arxiv", "#/feed"], ["recommended", "#/feed/recommended"], ["import", "#/feed/import"]];
const TYPES = ["new", "cross", "replace", "replace-cross"];
const STALE_ARXIV_HOURS = 3;
const STALE_S2_HOURS = 24;

export async function renderFeed(view, tab) {
  const body = h("div", { class: "feed-body" });
  view.replaceChildren(h("section", { class: "feed" },
    h("div", { class: "feed-head" },
      h("h1", {}, t("feed.title")),
      h("nav", { class: "feed-tabs", role: "tablist" }, TABS.map(([key, href]) =>
        h("a", { href, role: "tab", "aria-selected": String(key === tab) }, t(`feed.tab.${key}`))))),
    body));
  if (tab === "import") renderImport(body);
  else await renderList(body, tab);
}

// ---------- Listes (arXiv, recommandations) ----------

function hoursSince(stamp) {
  return stamp ? (Date.now() - Date.parse(`${stamp.replace(" ", "T")}Z`)) / 3.6e6 : Infinity;
}

function ago(stamp) {
  if (!stamp) return t("feed.never");
  const hours = hoursSince(stamp);
  if (hours < 1) return t("feed.minutesAgo", { n: Math.max(1, Math.round(hours * 60)) });
  if (hours < 48) return t("feed.hoursAgo", { n: Math.round(hours) });
  return t("feed.daysAgo", { n: Math.round(hours / 24) });
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

  let data = await api.feed(tab);
  const state = { search: "", categories: new Set(), types: new Set(settings.feedTypes || ["new", "cross"]), showHidden: false };

  const doRefresh = async (automatic = false) => {
    refresh.disabled = true;
    status.replaceChildren(h("span", { class: "spinner" }), " ", t(tab === "recommended" ? "feed.loadingS2" : "feed.loadingArxiv"));
    try {
      await api.refreshFeed(tab);
      data = await api.feed(tab);
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
    const shown = data.items.filter((item) => {
      if (tab === "arxiv" && !state.types.has(item.announce_type)) return false;
      if (state.categories.size && !item.categories.some((c) => state.categories.has(c))) return false;
      if (item.status === "disliked" && !state.showHidden) return false;
      return !needle || `${item.title} ${item.authors.join(" ")} ${item.abstract}`.toLowerCase().includes(needle);
    });
    if (!data.items.length) {
      list.replaceChildren(h("div", { class: "empty" }, t(tab === "recommended" ? "feed.emptyS2" : "feed.emptyArxiv")));
      return;
    }
    if (!shown.length) {
      list.replaceChildren(h("div", { class: "empty" }, t("feed.noMatch")));
      return;
    }
    const groups = new Map();
    for (const item of shown) {
      const key = tab === "arxiv" ? item.announced || "" : "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    list.replaceChildren(...[...groups].map(([date, items]) => h("section", { class: "feed-day" },
      date && h("h2", { class: "feed-date" }, capitalize(`${t("feed.announced", { date: formatDay(date) })} · ${t("feed.count", { n: items.length })}`)),
      items.map((item) => feedCard(item, draw)))));
    typesetTex([list]).catch(() => {});
  };

  showStatus();
  draw();
  const stale = hoursSince(data.refreshed?.[tab]) > (tab === "recommended" ? STALE_S2_HOURS : STALE_ARXIV_HOURS);
  if (stale) doRefresh(true);
}

function renderFilters(filters, tab, data, state, draw) {
  if (filters.dataset.ready) {
    // les compteurs changent ; le reste du formulaire est conservé
    filters.querySelector(".hidden-count").textContent = t("feed.hiddenCount", { n: data.items.filter((i) => i.status === "disliked").length });
    return;
  }
  filters.dataset.ready = "1";
  const search = h("input", { class: "text-input", type: "search", placeholder: t("feed.search"), "aria-label": t("feed.search") });
  search.addEventListener("input", () => { state.search = search.value; draw(); });
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
    row.push(h("span", { class: "filter-group" }, TYPES.map((type) => chip(t(`feed.type.${type}`), state.types.has(type), (on) => {
      if (on) state.types.add(type); else state.types.delete(type);
      saveSettings({ feedTypes: [...state.types] });
    }))));
    row.push(h("span", { class: "filter-group" }, (data.categories || []).map((category) => chip(category, false, (on) => {
      if (on) state.categories.add(category); else state.categories.delete(category);
    }))));
  }
  const hidden = h("label", { class: "check small" }, h("input", { type: "checkbox" }), h("span", { class: "hidden-count" },
    t("feed.hiddenCount", { n: data.items.filter((i) => i.status === "disliked").length })));
  hidden.querySelector("input").addEventListener("change", (event) => { state.showHidden = event.target.checked; draw(); });
  row.push(hidden);
  filters.replaceChildren(...row);
}

function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function formatDay(iso) {
  const date = new Date(`${iso}T12:00:00`);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString(getLanguage() === "fr" ? "fr-FR" : "en-GB", { weekday: "long", day: "numeric", month: "long" });
}

export function feedCard(item, onChange = () => {}) {
  const authors = item.authors.length > 8 ? [...item.authors.slice(0, 8), t("feed.etAl")] : item.authors;
  const abstract = h("p", { class: "feed-abstract" }, item.abstract || "");
  abstract.addEventListener("click", () => abstract.classList.toggle("open"));

  const statusButton = (value, label, icon) => {
    const button = h("button", { class: "icon-button", type: "button", title: label, "aria-label": label, "aria-pressed": String(item.status === value) }, icon);
    button.addEventListener("click", async () => {
      const next = item.status === value ? null : value;
      try {
        await api.feedStatus(item.key, next);
        item.status = next;
        button.closest(".feed-card").querySelectorAll("[aria-pressed]").forEach((b) => b.setAttribute("aria-pressed", "false"));
        if (next) button.setAttribute("aria-pressed", "true");
        if (next === "disliked") onChange();
      } catch (error) {
        toast(error.message, { error: true });
      }
    });
    return button;
  };

  const read = item.arxiv_id && h("button", { class: "button primary small-button", type: "button" }, item.libraryId ? t("feed.open") : t("feed.read"));
  read?.addEventListener("click", async () => {
    if (item.libraryId) { location.hash = `#/paper/${encodeURIComponent(item.libraryId)}`; return; }
    read.disabled = true;
    read.replaceChildren(h("span", { class: "spinner" }), " ", t("feed.importing"));
    try {
      const paper = await api.importPaper(item.arxiv_id);
      location.hash = `#/paper/${encodeURIComponent(paper.id)}`;
    } catch (error) {
      toast(t("error.generic", { message: error.message }), { error: true, duration: 6000 });
      read.disabled = false;
      read.replaceChildren(t("feed.read"));
    }
  });

  return h("article", { class: `feed-card${item.libraryId ? " in-library" : ""}` },
    h("h3", { class: "feed-title" }, item.title),
    h("div", { class: "feed-authors" }, authors.join(", ")),
    h("div", { class: "card-meta" },
      item.primary_category && h("span", { class: "chip category" }, item.primary_category),
      ...item.categories.filter((c) => c !== item.primary_category).map((c) => h("span", { class: "chip" }, c)),
      item.announce_type && h("span", { class: `chip type-${item.announce_type}` }, t(`feed.badge.${item.announce_type}`)),
      item.arxiv_id && h("span", { class: "chip" }, `arXiv:${item.arxiv_id}`),
      !item.announce_type && item.announced && h("span", { class: "chip" }, item.announced),
      item.announce_type && item.s2_rank !== null && item.s2_rank !== undefined && h("span", { class: "chip recommended-chip", title: t("feed.recommendedHint") }, t("feed.recommended")),
      item.libraryId && h("span", { class: "chip in-library-chip" }, t("feed.inLibrary"))),
    item.abstract && abstract,
    h("div", { class: "feed-actions" },
      read,
      statusButton("liked", t("feed.like"), "👍"),
      statusButton("disliked", t("feed.dislike"), "👎"),
      h("span", { class: "spacer" }),
      item.link && h("a", { class: "icon-button", href: item.link, target: "_blank", rel: "noopener noreferrer", title: item.arxiv_id ? "arXiv" : "Semantic Scholar", html: icons.external }),
      item.arxiv_id && h("a", { class: "icon-button", href: `https://arxiv.org/pdf/${item.arxiv_id}`, target: "_blank", rel: "noopener noreferrer", title: "PDF", html: icons.pdf })));
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
    progress.replaceChildren(failures.length ? t("import.failures", { list: failures.join(", ") }) : t("import.done", { n: chosen.length }));
    label();
  });
  results.replaceChildren(
    h("h2", { class: "import-heading" }, t("import.found", { n: papers.length })),
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
