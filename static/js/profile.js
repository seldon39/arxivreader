// Onglet « Profil » du feed : ce qui sert à trier les nouveautés par pertinence.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { typesetTex } from "./math.js";
import { h, toast, withMath } from "./util.js";

const SECTIONS = [
  { key: "mine", kinds: ["mine"], resolver: "paper" },
  { key: "references", kinds: ["paper", "book"], resolver: "paper" },
  { key: "author", kinds: ["author"], resolver: "author" },
  { key: "keyword", kinds: ["keyword"], resolver: null },
];

export async function renderProfile(body) {
  let data = await api.profile();
  const container = h("div", {});

  const owner = h("input", { class: "text-input", type: "text", value: data.owner || "", placeholder: t("profile.ownerPlaceholder"), "aria-label": t("profile.owner") });
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
      toast(t("error.generic", { message: error.message }), { error: true, duration: 6000 });
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
    h("div", { class: "profile-owner" }, h("label", {}, t("profile.owner")), owner, prefill),
    container,
  );
  draw();
}

function renderSection(section, data, update) {
  const items = data.items.filter((i) => section.kinds.includes(i.kind));
  const list = h("div", { class: "profile-list" }, items.length
    ? items.map((item) => profileRow(item, update))
    : h("p", { class: "hint" }, t("profile.empty")));
  return h("section", { class: "profile-section" },
    h("h2", {}, t(`profile.section.${section.key}`)),
    h("p", { class: "hint" }, t(`profile.help.${section.key}`)),
    list,
    section.resolver ? searchForm(section, update) : keywordForm(update));
}

function profileRow(item, update) {
  const remove = h("button", { class: "icon-button", type: "button", title: t("profile.remove"), "aria-label": t("profile.remove") }, "✕");
  remove.addEventListener("click", async () => update(await api.removeProfileItem(item.id)));
  const initialsOnly = item.kind === "author" && /^\p{Lu}\.\s/u.test(item.label);
  const details = [
    item.authors?.length ? item.authors.slice(0, 4).join(", ") + (item.authors.length > 4 ? " et al." : "") : "",
    item.year,
    item.kind === "book" ? t("profile.book") : "",
    item.ref && !item.ref.startsWith("s2") ? item.ref : "",
  ].filter(Boolean).join(" · ");
  return h("div", { class: "profile-row" },
    h("div", { class: "profile-text" },
      h("span", { class: item.kind === "author" || item.kind === "keyword" ? "profile-name" : "feed-title" }, withMath(item.label)),
      details && h("span", { class: "muted small" }, details),
      initialsOnly && h("span", { class: "badge warn" }, t("profile.initialsOnly"))),
    item.source === "suggested" && h("span", { class: "badge" }, t("profile.suggested")),
    remove);
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
      toast(error.message, { error: true });
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
        const itemKind = kind || (c.detail?.includes("livre") ? "book" : "paper");
        pick.addEventListener("click", () => add({ kind: itemKind, label: c.label, ref: c.ref, authors: c.authors, year: c.year, text: c.text }));
        return h("div", { class: "profile-candidate" },
          h("div", { class: "profile-text" }, h("span", {}, withMath(c.label)), h("span", { class: "muted small" }, c.detail || "")), pick);
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
    const text = prompt(t("profile.manualText"), "") || "";
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
    update(await api.addProfileItem({ kind: "keyword", label }));
    input.value = "";
    input.focus();
  };
  add.addEventListener("click", submit);
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") submit(); });
  return h("div", { class: "profile-form" }, input, add);
}
