// Assistant de premier lancement : retrouver ses articles sur arXiv, en déduire les catégories du feed et les
// auteurs à suivre, remplir le profil. Proposé une fois (nouvelle installation : bibliothèque et profil vides),
// et relancé depuis le Profil. Rien n'est enregistré avant « Terminer ».

import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { saveSettings, settings, openSettingsDialog } from "./settings.js";
import { h, icons, toast, withMath } from "./util.js";
import { typesetTex } from "./math.js";

let dialog = null;

export async function maybeOnboard() {
  if (settings.onboarded) return;
  try {
    const [papers, profile] = await Promise.all([api.papers(), api.profile()]);
    if (papers.length || (profile.items || profile).length) { saveSettings({ onboarded: true }); return; } // déjà utilisée
  } catch { return; }
  openOnboarding();
}

// `onDone` : page à redessiner après « Terminer » (le Profil, quand l'assistant en est relancé)
export function openOnboarding({ onDone = null } = {}) {
  dialog?.remove();
  const dlg = h("dialog", { class: "settings onboarding", "aria-labelledby": "onboarding-title" });
  dialog = dlg;
  document.body.append(dlg);
  // réponse arrivée après la fermeture de l'assistant, ou quand un autre l'a remplacé : ignorée
  const stale = () => dlg !== dialog || !dlg.open;
  const state = { name: settings.profileOwner || "", identifier: "", owner: "", papers: [], chosen: new Set(), suggestions: null,
    categories: new Set(), authors: new Set(), importMine: false };
  const skip = () => { saveSettings({ onboarded: true }); dlg.close(); };
  dlg.addEventListener("cancel", () => saveSettings({ onboarded: true }));
  dlg.addEventListener("close", () => { if (dialog === dlg) dialog = null; dlg.remove(); });
  const frame = (title, body, actions) => {
    dlg.replaceChildren(h("form", { method: "dialog", onsubmit: (e) => e.preventDefault() },
      h("div", { class: "dialog-head" }, h("h2", { id: "onboarding-title" }, title),
        h("button", { class: "icon-button", type: "button", title: t("settings.close"), "aria-label": t("settings.close"), html: icons.close, onclick: skip })),
      h("div", { class: "onboarding-body" }, body),
      h("div", { class: "actions onboarding-actions" }, actions)));
    typesetTex([dlg]).catch(() => {});
    dlg.querySelector(".onboarding-body input, .onboarding-actions .primary")?.focus();
  };
  // demande en cours : bouton occupé, et les autres boutons (Retour…) attendent
  const busy = (button, label) => {
    dlg.querySelectorAll(".onboarding-actions button").forEach((b) => { b.disabled = true; });
    button.replaceChildren(h("span", { class: "spinner" }), " ", label);
  };

  // 1. qui êtes-vous ?
  function stepWho() {
    const name = h("input", { class: "text-input", type: "text", value: state.name, autocomplete: "name", placeholder: t("onboarding.namePlaceholder") });
    const ident = h("input", { class: "text-input", type: "text", value: state.identifier, spellcheck: "false", placeholder: t("onboarding.identifierPlaceholder") });
    const go = h("button", { class: "button primary", type: "submit" }, t("onboarding.find"));
    const run = async () => {
      if (go.disabled) return; // Entrée pendant la recherche : une seule
      state.name = name.value.trim();
      state.identifier = ident.value.trim();
      if (!state.name && !state.identifier) { name.focus(); return; }
      busy(go, t("onboarding.finding"));
      try {
        const found = await api.onboardingFind(state.name, state.identifier);
        if (stale()) return;
        state.papers = found.papers;
        state.total = found.total || found.papers.length;
        state.owner = found.owner || state.name;
        state.chosen = new Set(found.papers.filter((p) => p.likely).map((p) => p.arxivId));
        stepPapers();
      } catch (error) {
        if (stale()) return;
        toast(t(`onboarding.error.${error.code || "network"}`), { error: true });
        stepWho();
      }
    };
    go.addEventListener("click", run);
    // langue : l'assistant (et la page derrière) se redessinent aussitôt, ce qui est déjà tapé reste
    const languages = h("div", { class: "segmented onboarding-language", role: "radiogroup", "aria-label": "Langue / Language" },
      [["fr", "Français"], ["en", "English"]].map(([value, label]) => h("label", {},
        h("input", { type: "radio", name: "onboarding-language", value, checked: settings.language === value,
          onchange: async () => {
            state.name = name.value.trim();
            state.identifier = ident.value.trim();
            await saveSettings({ language: value });
            if (stale()) return;
            stepWho();
            dlg.querySelector(`input[name="onboarding-language"][value="${value}"]`)?.focus();
          } }), label)));
    frame(t("onboarding.welcome"), [
      h("div", { class: "onboarding-language-row" }, languages),
      h("p", {}, t("onboarding.lead")),
      h("label", { class: "row" }, h("span", {}, t("onboarding.name")), name),
      h("label", { class: "row" }, h("span", {}, t("onboarding.identifier")), ident),
      h("p", { class: "hint" }, t("onboarding.identifierHint")),
    ], [h("button", { class: "button", type: "button", onclick: skip }, t("onboarding.skip")), go]);
    [name, ident].forEach((input) => input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); run(); } }));
  }

  // 2. vos articles (à cocher : homonymes possibles quand on cherche par nom)
  function stepPapers() {
    const count = h("p", { class: "hint" });
    // aucun article (thèse en cours…) : on peut continuer, catégories et auteurs se choisissent à la main
    const recount = () => { count.textContent = tn("onboarding.chosen", state.chosen.size); };
    const next = h("button", { class: "button primary", type: "button" }, t("onboarding.next"));
    const items = state.papers.map((p) => h("label", { class: "onboarding-paper" },
      h("input", { type: "checkbox", checked: state.chosen.has(p.arxivId),
        onchange: (e) => { if (e.target.checked) state.chosen.add(p.arxivId); else state.chosen.delete(p.arxivId); recount(); } }),
      h("span", {}, h("strong", {}, withMath(p.title)), h("span", { class: "muted small" }, ` — ${p.authors.slice(0, 4).join(", ")}${p.authors.length > 4 ? " …" : ""} · ${p.year} · ${p.primary}`))));
    next.addEventListener("click", async () => {
      busy(next, t("onboarding.suggesting"));
      const chosen = state.papers.filter((p) => state.chosen.has(p.arxivId));
      try {
        state.suggestions = chosen.length ? await api.onboardingSuggest(state.owner, chosen) : { categories: [], authors: [] };
      } catch {
        state.suggestions = { categories: [], authors: [] };
      }
      if (stale()) return;
      // catégories déjà suivies : gardées et cochées, en plus des mieux placées d'après les articles
      const current = settings.feedCategories || [];
      const cats = state.suggestions.categories;
      for (const category of current) if (!cats.some((c) => c.category === category)) cats.push({ category, count: 0 });
      state.categories = new Set([...current, ...cats.filter((c, i) => c.count && i < 4 && (i === 0 || c.count >= 2)).map((c) => c.category)]);
      const coauthors = state.suggestions.authors.filter((a) => a.why === "coauthor").slice(0, 6);
      const cited = state.suggestions.authors.filter((a) => a.why === "cited").slice(0, 3);
      state.authors = new Set([...coauthors, ...cited].map((a) => a.name));
      stepChoices();
    });
    // vous (pour ne pas vous proposer comme coauteur) : modifiable si l'identifiant a désigné quelqu'un d'autre
    const owner = h("input", { class: "text-input", type: "text", value: state.owner, autocomplete: "name",
      oninput: (e) => { state.owner = e.target.value.trim(); } });
    frame(t("onboarding.papersTitle"), [
      h("p", {}, state.papers.length ? t(state.identifier ? "onboarding.papersLeadId" : "onboarding.papersLead") : t("onboarding.noPapers")),
      state.papers.length ? h("label", { class: "row" }, h("span", {}, t("onboarding.owner")), owner) : null,
      state.total > state.papers.length ? h("p", { class: "hint" }, t("onboarding.truncated", { n: state.papers.length, total: state.total })) : null,
      count, h("div", { class: "onboarding-list" }, items),
    ].filter(Boolean), [h("button", { class: "button", type: "button", onclick: stepWho }, t("onboarding.back")), next]);
    recount();
  }

  // 3. catégories du feed et auteurs à suivre
  function stepChoices() {
    const toggle = (set, value) => (e) => { if (e.target.checked) set.add(value); else set.delete(value); };
    const cats = state.suggestions.categories.map((c) => h("label", { class: "check onboarding-chip" },
      h("input", { type: "checkbox", checked: state.categories.has(c.category), onchange: toggle(state.categories, c.category) }),
      h("span", {}, c.category), c.count ? h("span", { class: "muted small" }, ` (${c.count})`) : null));
    const extra = h("input", { class: "text-input", type: "text", spellcheck: "false", placeholder: "math.AP, math-ph…" });
    const authors = state.suggestions.authors.map((a) => h("label", { class: "check onboarding-chip" },
      h("input", { type: "checkbox", checked: state.authors.has(a.name), onchange: toggle(state.authors, a.name) }),
      h("span", {}, a.name), h("span", { class: "muted small" }, ` · ${t(`onboarding.why.${a.why}`)}`)));
    const importMine = h("input", { type: "checkbox", checked: state.importMine, onchange: (e) => { state.importMine = e.target.checked; } });
    const done = h("button", { class: "button primary", type: "button" }, t("onboarding.finish"));
    done.addEventListener("click", async () => {
      const words = extra.value.split(/[\s,;]+/).map((c) => c.trim()).filter(Boolean);
      const typed = words.filter((c) => /^[a-z-]+(\.[A-Za-z-]+)?$/.test(c));
      if (typed.length < words.length) {
        toast(t("onboarding.badCategories", { list: words.filter((c) => !typed.includes(c)).join(", ") }), { error: true });
        return;
      }
      const categories = [...new Set([...state.categories, ...typed])].slice(0, 12);
      busy(done, t("onboarding.saving"));
      try {
        await api.onboardingApply({ owner: state.owner, papers: state.papers.filter((p) => state.chosen.has(p.arxivId)),
          categories, authors: [...state.authors], importMine: state.importMine });
        Object.assign(settings, { onboarded: true, ...(state.owner ? { profileOwner: state.owner } : {}), ...(categories.length ? { feedCategories: categories } : {}) });
        if (stale()) return;
        refresh();
        stepReady();
      } catch (error) {
        if (stale()) return;
        toast(t("onboarding.error.save"), { error: true });
        stepChoices();
      }
    });
    frame(t("onboarding.choicesTitle"), [
      h("h3", {}, t("onboarding.categories")), h("p", { class: "hint" }, t("onboarding.categoriesHint")),
      cats.length ? h("div", { class: "onboarding-chips" }, cats) : null,
      h("label", { class: "row" }, h("span", {}, t("onboarding.moreCategories")), extra),
      h("h3", {}, t("onboarding.authors")), h("p", { class: "hint" }, t("onboarding.authorsHint")),
      authors.length ? h("div", { class: "onboarding-chips" }, authors) : h("p", { class: "hint" }, t("onboarding.noAuthors")),
      h("label", { class: "check" }, importMine, h("span", {}, t("onboarding.importMine"))),
    ].filter(Boolean), [h("button", { class: "button", type: "button", onclick: stepPapers }, t("onboarding.back")), done]);
  }

  // pages déjà affichées remises à jour (profil, feed…) ; articles importés en arrière-plan : la bibliothèque suit
  function refresh() {
    onDone?.();
    dispatchEvent(new HashChangeEvent("hashchange"));
    if (state.importMine) [20, 60, 150].forEach((s) => setTimeout(() => dispatchEvent(new Event("arxivreader:library-refresh")), s * 1000));
  }

  // 4. la suite : assistant IA, clés facultatives
  function stepReady() {
    frame(t("onboarding.readyTitle"), [
      h("p", {}, t("onboarding.ready")),
      h("ul", { class: "onboarding-tips" }, ["ai", "s2", "zotero"].map((k) => h("li", {}, t(`onboarding.tip.${k}`)))),
    ], [h("button", { class: "button", type: "button", onclick: () => { dlg.close(); openSettingsDialog(); } }, t("onboarding.openSettings")),
      h("button", { class: "button primary", type: "button", onclick: () => { dlg.close(); location.hash = "#/feed"; } }, t("onboarding.toFeed"))]);
  }

  dlg.showModal();
  stepWho(); // après l'ouverture : le champ du nom prend le focus (pas le bouton de fermeture)
}
