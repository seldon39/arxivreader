// Réglages de l'interface, enregistrés côté serveur (library/arxivreader.db).

import { api } from "./api.js";
import { applyStatic, setLanguage, t } from "./i18n.js";
import { h } from "./util.js";

const DEFAULTS = {
  language: "fr",
  theme: "auto",
  fontSize: 18,
  readingWidth: "normal",
  proofsCollapsed: false,
  tocOpen: true,
  panelOpen: true,
  paperFont: "auto",
  marginMarks: true,
  mathRenderer: "mathjax",
};
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
  root.style.setProperty("--reading-width", WIDTHS[settings.readingWidth] || WIDTHS.normal);
  setLanguage(settings.language);
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

function segmented(name, options, current) {
  return h("div", { class: "segmented", role: "radiogroup" },
    options.map(([value, label]) => h("label", {},
      h("input", { type: "radio", name, value, checked: value === current }),
      label,
    )),
  );
}

export function openSettingsDialog() {
  const dialog = document.getElementById("settings-dialog");
  const form = h("form", { method: "dialog" },
    h("h2", {}, t("settings.title")),
    h("div", { class: "row" }, h("span", {}, t("settings.language")),
      segmented("language", [["fr", "Français"], ["en", "English"]], settings.language)),
    h("div", { class: "row" }, h("span", {}, t("settings.theme")),
      segmented("theme", [["auto", t("settings.theme.auto")], ["light", t("settings.theme.light")], ["dark", t("settings.theme.dark")]], settings.theme)),
    h("div", { class: "row" }, h("span", {}, `${t("settings.fontSize")} : `, h("output", { id: "font-size-value" }, `${settings.fontSize}px`)),
      h("input", { type: "range", name: "fontSize", min: 14, max: 24, step: 1, value: settings.fontSize })),
    h("div", { class: "row" }, h("span", {}, t("settings.width")),
      segmented("readingWidth", [["narrow", t("settings.width.narrow")], ["normal", t("settings.width.normal")], ["wide", t("settings.width.wide")]], settings.readingWidth)),
    h("div", { class: "row" }, h("span", {}, t("settings.paperFont")),
      segmented("paperFont", [["auto", t("settings.paperFont.auto")], ["charter", "Charter"], ["lm", "Computer Modern"]], settings.paperFont)),
    h("div", { class: "row" }, h("span", {}, t("settings.math")),
      segmented("mathRenderer", [["mathjax", t("settings.math.mathjax")], ["native", t("settings.math.native")]], settings.mathRenderer)),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "proofsCollapsed", checked: settings.proofsCollapsed }), t("settings.proofs")),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "marginMarks", checked: settings.marginMarks }), t("settings.marginMarks")),
    h("div", { class: "actions" }, h("button", { class: "button primary", value: "close" }, t("settings.close"))),
  );
  form.addEventListener("input", (event) => {
    const { name, value, checked, type } = event.target;
    if (!name) return;
    const parsed = type === "checkbox" ? checked : name === "fontSize" ? Number(value) : value;
    if (name === "fontSize") form.querySelector("#font-size-value").textContent = `${value}px`;
    saveSettings({ [name]: parsed });
    if (name === "language") openSettingsDialog(); // re-dessine la fenêtre dans la nouvelle langue
  });
  dialog.replaceChildren(form);
  applyStatic(dialog);
  if (!dialog.open) dialog.showModal();
}
