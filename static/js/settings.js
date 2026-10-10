// Réglages de l'interface, enregistrés côté serveur (library/arxivreader.db).

import { PROVIDERS, assistantName, setupNotice } from "./aiproviders.js";
import { api } from "./api.js";
import { applyStatic, setAIName, setLanguage, t } from "./i18n.js";
import { updatesSection } from "./updates.js";
import { h, icons, toast } from "./util.js";

export const DEFAULT_FONT_SIZE = 18;
export const DEFAULT_AI_FONT_SIZE = 15; // réponses de l'IA

const DEFAULTS = {
  language: "fr",
  theme: "auto",
  fontSize: DEFAULT_FONT_SIZE,
  readingWidth: "normal",
  proofsCollapsed: false,
  tocOpen: true,
  panelOpen: true,
  panelWidth: null,   // largeur du panneau de droite choisie à la souris (sinon largeur par défaut)
  tocWidth: null,     // largeur du sommaire, de même
  paperFont: "auto",
  marginMarks: true,
  mathRenderer: "mathjax",
  aiProvider: "claude",
  aiLocalUrl: "",     // vide : Ollama (http://127.0.0.1:11434/v1)
};
const LOCAL_URL = "http://127.0.0.1:11434/v1";
const FEED_DEFAULT = ["math.DG", "math.CV", "math.SP", "math-ph"];
const WIDTHS = { narrow: "42rem", normal: "56rem", wide: "72rem" };

export const settings = { ...DEFAULTS };
const listeners = new Set();

export async function loadSettings() {
  try {
    Object.assign(settings, await api.settings());
  } catch {
    // le serveur répondra aux prochains enregistrements ; on garde les valeurs par défaut
  }
  applySettings();
}

export function onSettingsChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function applySettings() {
  const root = document.documentElement;
  if (settings.theme === "auto") delete root.dataset.theme;
  else root.dataset.theme = settings.theme;
  root.dataset.paperFont = settings.paperFont;
  root.style.setProperty("--font-size", `${settings.fontSize}px`);
  root.style.setProperty("--ai-font-size", `${settings.aiFontSize || DEFAULT_AI_FONT_SIZE}px`);
  root.style.setProperty("--reading-width", WIDTHS[settings.readingWidth] || WIDTHS.normal);
  setLanguage(settings.language);
  setAIName(assistantName(settings)); // après la langue : « IA locale » / « local AI »
  listeners.forEach((fn) => fn(settings));
}

export async function saveSettings(values) {
  Object.assign(settings, values);
  applySettings();
  try {
    await api.saveSettings(values);
  } catch {
    // réglage appliqué localement même si l'enregistrement échoue
  }
}

// Assistant IA choisi : à quoi il sert, s'il est prêt (sinon la marche à suivre), et pour un modèle
// local, l'adresse du serveur et le modèle
let aiCheck = 0;
async function renderAISettings(holder, keepFocus = null) {
  const provider = settings.aiProvider || "claude";
  const check = ++aiCheck;
  const line = h("p", { class: "hint", "aria-live": "polite" }, t("settings.aiChecking"));
  holder.replaceChildren(h("p", { class: "hint" }, t(`settings.aiHint.${provider}`)), line);
  let status;
  try { status = await api.aiStatus(); } catch { status = { provider, available: false, loggedIn: false }; }
  if (check !== aiCheck || !holder.isConnected) return; // réglage changé entre-temps
  const models = status.models || [];
  if (provider === "local" && models.length && !models.includes(settings.aiLocalModel)) {
    await saveSettings({ aiLocalModel: models[0] });
  }
  const fields = provider !== "local" ? [] : [
    h("label", { class: "row" }, h("span", {}, t("settings.aiLocalUrl")),
      h("input", { class: "text-input", type: "text", name: "aiLocalUrl", value: settings.aiLocalUrl || "", placeholder: LOCAL_URL,
        spellcheck: "false", autocomplete: "off" })),
    models.length > 0 && h("label", { class: "row" }, h("span", {}, t("settings.aiLocalModel")),
      h("select", { class: "text-input", name: "aiLocalModel" },
        models.map((model) => h("option", { value: model, selected: model === settings.aiLocalModel }, model)))),
  ];
  line.replaceWith(...fields.filter(Boolean),
    status.loggedIn ? h("p", { class: "hint ai-ready" }, `✓ ${t("settings.aiReady")}`)
      : setupNotice({ ...status, provider }, () => renderAISettings(holder)));
  if (keepFocus && !holder.contains(document.activeElement)) holder.querySelector(`[name="${keepFocus}"]`)?.focus();
}

// Serveur local : sur ce Mac seulement (même règle que le serveur, qui refuse le reste)
function localUrl(url) {
  try {
    const parts = new URL(url); // port invalide : exception
    return parts.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(parts.hostname)
      && !parts.username && !parts.search && !parts.hash && url === url.trim();
  } catch {
    return false;
  }
}

function segmented(name, options, current) {
  return h("div", { class: "segmented", role: "radiogroup", "aria-labelledby": `setting-${name}` },
    options.map(([value, label]) => h("label", {},
      h("input", { type: "radio", name, value, checked: value === current }),
      label,
    )),
  );
}

export function openSettingsDialog({ section = null } = {}) {
  const dialog = document.getElementById("settings-dialog");
  const form = h("form", { method: "dialog" },
    h("div", { class: "dialog-head" }, h("h2", {}, t("settings.title")),
      h("button", { class: "icon-button", value: "close", title: t("settings.close"), "aria-label": t("settings.close"), html: icons.close })),
    h("div", { class: "row" }, h("span", { id: "setting-language" }, t("settings.language")),
      segmented("language", [["fr", "Français"], ["en", "English"]], settings.language)),
    h("div", { class: "row" }, h("span", { id: "setting-theme" }, t("settings.theme")),
      segmented("theme", [["auto", t("settings.theme.auto")], ["light", t("settings.theme.light")], ["dark", t("settings.theme.dark")]], settings.theme)),
    h("div", { class: "row" }, h("span", { id: "setting-fontSize" }, t("settings.fontSize", { n: settings.fontSize })),
      h("div", { class: "range-row" },
        h("input", { type: "range", name: "fontSize", min: 14, max: 24, step: 1, value: settings.fontSize, "aria-labelledby": "setting-fontSize" }),
        h("button", { class: "button small-button", type: "button", name: "resetFontSize", onclick: (event) => {
          const range = form.querySelector('input[name="fontSize"]');
          range.value = DEFAULT_FONT_SIZE;
          range.dispatchEvent(new Event("input", { bubbles: true }));
          event.currentTarget.blur();
        } }, t("settings.default")))),
    h("div", { class: "row" }, h("span", { id: "setting-readingWidth" }, t("settings.width")),
      segmented("readingWidth", [["narrow", t("settings.width.narrow")], ["normal", t("settings.width.normal")], ["wide", t("settings.width.wide")]], settings.readingWidth)),
    h("div", { class: "row" }, h("span", { id: "setting-paperFont" }, t("settings.paperFont")),
      segmented("paperFont", [["auto", t("settings.paperFont.auto")], ["charter", "Charter"], ["lm", "Computer Modern"]], settings.paperFont)),
    h("div", { class: "row" }, h("span", { id: "setting-mathRenderer" }, t("settings.math")),
      segmented("mathRenderer", [["mathjax", t("settings.math.mathjax")], ["native", t("settings.math.native")]], settings.mathRenderer)),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "proofsCollapsed", checked: settings.proofsCollapsed }), t("settings.proofs")),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "marginMarks", checked: settings.marginMarks }), t("settings.marginMarks")),
    h("h3", { class: "settings-section" }, t("settings.ai")),
    h("div", { class: "row" }, h("span", { id: "setting-aiProvider" }, t("settings.aiProvider")),
      segmented("aiProvider", PROVIDERS.map((p) => [p, p === "local" ? t("settings.aiProvider.local") : { claude: "Claude", codex: "Codex", gemini: "Gemini" }[p]]),
        settings.aiProvider || "claude")),
    h("div", { class: "ai-settings" }),
    h("h3", { class: "settings-section" }, t("settings.feed")),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "notifyFollowed", checked: settings.notifyFollowed !== false }), t("settings.notifyFollowed")),
    h("p", { class: "hint", style: "margin: -10px 0 0" }, t("settings.notifyFollowedHint")),
    h("label", { class: "row" }, h("span", {}, t("settings.feedCategories")),
      h("input", { class: "text-input", type: "text", name: "feedCategories", value: (settings.feedCategories || FEED_DEFAULT).join(", "), spellcheck: "false" })),
    h("label", { class: "row" }, h("span", {}, t("settings.s2Key")),
      // texte masqué plutôt que « password » : Safari ne propose pas d'enregistrer un mot de passe
      h("input", { class: "text-input secret-input", type: "text", name: "s2ApiKey", value: settings.s2ApiKey || "", autocomplete: "off",
        spellcheck: "false", placeholder: t("settings.s2KeyPlaceholder") })),
    h("p", { class: "hint", style: "margin: -6px 0 0" }, t("settings.s2KeyHint")),
    h("h3", { class: "settings-section" }, t("settings.updates")),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "checkUpdates", checked: settings.checkUpdates !== false }), t("settings.checkUpdates")),
    updatesSection(),
    h("div", { class: "actions" }, h("button", { class: "button primary", value: "close" }, t("settings.close"))),
  );
  form.addEventListener("input", (event) => {
    const { name, value, checked, type } = event.target;
    if (!name) return;
    if (name === "feedCategories" || name === "s2ApiKey" || name === "aiLocalUrl") return; // enregistrés à la sortie du champ
    const parsed = type === "checkbox" ? checked : name === "fontSize" ? Number(value) : value;
    if (name === "fontSize") form.querySelector("#setting-fontSize").textContent = t("settings.fontSize", { n: value });
    const saved = saveSettings({ [name]: parsed });
    if (name === "aiProvider") saved.then(() => renderAISettings(form.querySelector(".ai-settings")));
    if (name === "language") {
      openSettingsDialog(); // re-dessine la fenêtre dans la nouvelle langue
      dialog.querySelector('input[name="language"]:checked')?.focus();
    }
  });
  form.addEventListener("change", (event) => {
    const { name, value } = event.target;
    if (name === "feedCategories") {
      const list = value.split(/[\s,;]+/).map((c) => c.trim()).filter(Boolean);
      saveSettings({ feedCategories: list.length ? list : FEED_DEFAULT });
      toast(t("settings.feedCategoriesSaved"));
    } else if (name === "s2ApiKey") {
      saveSettings({ s2ApiKey: value.trim() });
    } else if (name === "aiLocalUrl") {
      const url = value.trim().replace(/\/+$/, "");
      if (url && !localUrl(url)) {
        toast(t("settings.aiLocalUrlBad"), { error: true });
        return;
      }
      saveSettings({ aiLocalUrl: url }).then(() => renderAISettings(form.querySelector(".ai-settings"), "aiLocalUrl"));
    }
  });
  renderAISettings(form.querySelector(".ai-settings"));
  dialog.replaceChildren(form);
  applyStatic(dialog);
  if (!dialog.open) dialog.showModal();
  if (section) dialog.querySelector(`#settings-${section}`)?.scrollIntoView({ block: "center" });
}
