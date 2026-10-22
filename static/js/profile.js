// Onglet « Profil » du feed : ce qui sert à trier les nouveautés par pertinence.

import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { typesetTex } from "./math.js";
import { saveSettings, settings } from "./settings.js";
import { h, icons, toast, toastError, withMath } from "./util.js";
import { openOnboarding } from "./onboarding.js";

const SECTIONS = [
  { key: "mine", kinds: ["mine"], resolver: "paper" },
  { key: "references", kinds: ["paper", "book"], resolver: "paper" },
  { key: "author", kinds: ["author"], resolver: "author" },
  { key: "keyword", kinds: ["keyword"], resolver: null },
];

export async function renderProfile(body) {
  body.replaceChildren(h("div", { class: "busy" }, h("span", { class: "spinner" }), t("common.loading")));
  let data = await api.profile();
  const container = h("div", {});

  const owner = h("input", { id: "profile-owner", class: "text-input", type: "text", value: data.owner || "", placeholder: t("profile.ownerPlaceholder"), "aria-label": t("profile.owner") });
  const prefill = h("button", { class: "button", type: "button" }, t("profile.prefill"));
  prefill.addEventListener("click", async () => {
    if (!owner.value.trim()) { owner.focus(); return; }
    prefill.disabled = true;
    prefill.replaceChildren(h("span", { class: "spinner" }), " ", t("profile.prefilling"));
    try {
      data = await api.prefillProfile(owner.value.trim());
      draw();
      toast(t("profile.prefilled"));
    } catch (error) {
      toastError(error);
    } finally {
      prefill.disabled = false;
      prefill.replaceChildren(t("profile.prefill"));
    }
  });

  const update = (next) => { data = next; draw(); };

  const draw = () => {
    container.replaceChildren(...SECTIONS.map((section) => renderSection(section, data, update)),
      h("p", { class: "hint" }, t("profile.referencesNote", { n: data.references || 0 })));
    typesetTex([container]).catch(() => {});
  };

  body.replaceChildren(
    h("p", { class: "lead" }, t("profile.lead")),
    h("div", { class: "profile-owner" }, h("label", { for: "profile-owner" }, t("profile.owner")), owner, prefill,
      h("button", { class: "button", type: "button", onclick: openOnboarding }, t("onboarding.again"))),
    container,
  );
  draw();
}

// recherche dans chaque rubrique, gardée quand la page est redessinée (ajout, retrait)
const queries = {};
const fold = (text) => String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

// Rubrique repliable (comme les sections du feed), fermée par défaut : titre et nombre ; dépliée, son
// explication, le champ d'ajout, une recherche dans ses éléments (s'il y en a plus de trois) et la liste
function renderSection(section, data, update) {
  const items = data.items.filter((i) => section.kinds.includes(i.kind));
  const count = h("span", { class: "count" });
  const rows = h("div", { class: "profile-list" });
  const drawRows = () => {
    const words = fold(queries[section.key]).split(/\s+/).filter(Boolean);
    const shown = items.filter((item) => {
      const haystack = fold([item.label, ...(item.authors || []), item.year, item.ref, item.text].join(" "));
      return words.every((word) => haystack.includes(word));
    });
    count.textContent = shown.length === items.length ? tn("profile.count", items.length)
      : t("profile.matching", { n: shown.length, total: items.length });
    rows.replaceChildren(...(shown.length ? shown.map((item) => profileRow(item, update))
      : [h("p", { class: "hint" }, t(items.length ? "profile.noMatch" : "profile.empty"))]));
    typesetTex([rows]).catch(() => {});
  };
  const search = items.length > 3 && h("input", { class: "text-input profile-search-input", type: "search", value: queries[section.key] || "",
    placeholder: t("profile.filter"), "aria-label": t("profile.filter"), autocomplete: "off", spellcheck: "false",
    oninput: (event) => { queries[section.key] = event.target.value; drawRows(); } });
  drawRows();
  const element = h("section", { class: "profile-section foldable" });
  const toggle = h("button", { class: "section-toggle", type: "button" },
    h("span", { class: "label" }, t(`profile.section.${section.key}`), count),
    h("span", { class: "chevron", html: icons.chevronRight }));
  const body = h("div", { class: "fold-body" }, h("div", { class: "fold-inner" },
    h("p", { class: "hint" }, t(`profile.help.${section.key}`)),
    // ajout en tête (plus lisible), puis la recherche dans la liste, puis la liste
    h("div", { class: "profile-add" }, section.resolver ? searchForm(section, update) : keywordForm(update)),
    search && h("span", { class: "search-field profile-search" }, h("span", { class: "search-icon", html: icons.search }), search),
    rows));
  const setOpen = (open) => {
    element.classList.toggle("collapsed", !open);
    body.inert = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.title = t(open ? "feed.collapseSection" : "feed.expandSection");
  };
  toggle.addEventListener("click", () => {
    const open = element.classList.contains("collapsed");
    setOpen(open);
    const others = (settings.profileOpen || []).filter((key) => key !== section.key);
    saveSettings({ profileOpen: open ? [...others, section.key] : others });
    if (open && search) requestAnimationFrame(() => search.focus({ preventScroll: true }));
  });
  setOpen((settings.profileOpen || []).includes(section.key));
  element.append(h("h2", { class: "feed-date" }, toggle), body);
  return element;
}

function profileRow(item, update) {
  const remove = h("button", { class: "icon-button", type: "button", title: t("profile.remove"), "aria-label": t("profile.remove"), html: icons.close });
  remove.addEventListener("click", async () => {
    try {
      update(await api.removeProfileItem(item.id));
      const { kind, label, ref, authors, year, text, only, excluded } = item;  // auteur suivi : sa règle aussi
      toast(t("profile.removed", { label }), { action: { label: t("common.undo"),
        onClick: () => api.addProfileItem({ kind, label, ref, authors, year, text, only, excluded }).then(update, toastError) } });
    } catch (error) {
      toastError(error);
    }
  });
  const initialsOnly = item.kind === "author" && /^\p{Lu}\.\s/u.test(item.label);
  const details = [
    item.authors?.length ? item.authors.slice(0, 4).join(", ") + (item.authors.length > 4 ? ` ${t("feed.etAl")}` : "") : "",
    item.year,
    item.kind === "book" ? t("profile.book") : "",
    item.ref && !item.ref.startsWith("s2") ? item.ref : "",
  ].filter(Boolean).join(" · ");
  return h("div", { class: "profile-row" },
    h("div", { class: "profile-text" },
      h("span", { class: item.kind === "author" || item.kind === "keyword" ? "profile-name" : "feed-title" }, withMath(item.label)),
      details && h("span", { class: "muted small" }, details),
      initialsOnly && h("span", { class: "badge warn" }, t("profile.initialsOnly")),
      item.kind === "author" && item.only?.length > 0 && authorRule(item, update)),
    // sans règle : lien discret à droite, au survol (la ligne garde sa hauteur)
    item.kind === "author" && !item.only?.length && authorRule(item, update),
    item.source === "suggested" && h("span", { class: "badge" }, t("profile.suggested")),
    remove);
}

// Homonymes d'un auteur suivi : catégories où le suivre (« Seulement dans : math.CV × math.DG × + catégorie ») ;
// sans règle, un lien discret au survol
function authorRule(item, update) {
  const only = item.only || [];
  const save = async (next) => {
    try { update(await api.authorRule(item.id, { only: next })); } catch (error) { toastError(error); }
  };
  const line = h("span", { class: `author-rule${only.length ? "" : " rule-unset"}` });
  const addLink = h("button", { class: "link-button small", type: "button" }, only.length ? t("profile.addCategory") : t("profile.limitCategories"));
  addLink.addEventListener("click", () => {
    const input = h("input", { class: "text-input category-input", type: "text", placeholder: "math.DG", spellcheck: "false", "aria-label": t("profile.addCategory") });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { input.replaceWith(addLink); return; }
      if (event.key !== "Enter") return;
      event.preventDefault();
      const value = input.value.trim();
      if (!/^[A-Za-z][A-Za-z-]{1,15}(\.[A-Za-z-]{1,15})?$/.test(value)) { toast(t("profile.badCategory"), { error: true }); return; }
      save([...new Set([...only, value])]);
    });
    input.addEventListener("blur", () => { if (!input.value.trim()) input.replaceWith(addLink); });
    addLink.replaceWith(input);
    input.focus();
  });
  line.append(...(only.length ? [h("span", { class: "muted small" }, t("profile.onlyIn")), ...only.map((category) => h("span", { class: "chip rule-chip" }, category,
    h("button", { class: "rule-remove", type: "button", title: t("profile.removeCategory", { category }), "aria-label": t("profile.removeCategory", { category }),
      onclick: () => save(only.filter((c) => c !== category)) }, "×")))] : []), addLink);
  return line;
}

function searchForm(section, update) {
  const kind = section.key === "mine" ? "mine" : section.resolver === "author" ? "author" : null;
  const input = h("input", { class: "text-input", type: "text", placeholder: t(`profile.placeholder.${section.key}`), "aria-label": t(`profile.placeholder.${section.key}`) });
  const search = h("button", { class: "button", type: "button" }, t("profile.search"));
  const direct = h("button", { class: "button", type: "button" }, t(section.resolver === "author" ? "profile.addAsTyped" : "profile.addManual"));
  const results = h("div", { class: "profile-candidates" });
  const add = async (item) => {
    try {
      update(await api.addProfileItem(item));
      input.value = "";
      results.replaceChildren();
    } catch (error) {
      toastError(error);
    }
  };
  search.addEventListener("click", async () => {
    if (!input.value.trim()) return;
    search.disabled = true;
    results.replaceChildren(h("div", { class: "busy" }, h("span", { class: "spinner" }), t("profile.searching")));
    try {
      const candidates = await api.resolveProfile(section.resolver, input.value.trim());
      results.replaceChildren(...(candidates.length ? candidates.map((c) => {
        const pick = h("button", { class: "button small-button", type: "button" }, t("profile.add"));
        const itemKind = kind || (c.book ? "book" : "paper");
        pick.addEventListener("click", () => add({ kind: itemKind, label: c.label, ref: c.ref, authors: c.authors, year: c.year, text: c.text }));
        const detail = [c.detail, !c.detail && c.paperCount !== undefined ? tn("profile.paperCount", c.paperCount) : "", c.book ? t("profile.book") : ""]
          .filter(Boolean).join(" · ");
        return h("div", { class: "profile-candidate" },
          h("div", { class: "profile-text" }, h("span", {}, withMath(c.label)), h("span", { class: "muted small" }, detail)), pick);
      }) : [h("p", { class: "hint" }, t("profile.noResult"))]));
      typesetTex([results]).catch(() => {});
    } catch (error) {
      results.replaceChildren(h("p", { class: "notice" }, error.message.includes("429") ? t("feed.error.rateLimited") : t("error.generic", { message: error.message })));
    } finally {
      search.disabled = false;
    }
  });
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") search.click(); });
  direct.addEventListener("click", () => {
    const label = input.value.trim();
    if (!label) { input.focus(); return; }
    if (section.resolver === "author") { add({ kind: "author", label }); return; }
    // Saisie à la main d'un article ou d'un livre : titre et, si possible, un résumé
    const text = prompt(t("profile.manualText"), "");
    if (text === null) return; // « Annuler » : rien n'est ajouté
    add({ kind: kind || "book", label, text });
  });
  return h("div", { class: "profile-form" }, input, search, direct, results);
}

function keywordForm(update) {
  const input = h("input", { class: "text-input", type: "text", placeholder: t("profile.placeholder.keyword"), "aria-label": t("profile.placeholder.keyword") });
  const add = h("button", { class: "button", type: "button" }, t("profile.add"));
  const submit = async () => {
    const label = input.value.trim();
    if (!label) return;
    try {
      update(await api.addProfileItem({ kind: "keyword", label }));
      input.value = "";
      input.focus();
    } catch (error) {
      toastError(error);
    }
  };
  add.addEventListener("click", submit);
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") submit(); });
  return h("div", { class: "profile-form" }, input, add);
}
