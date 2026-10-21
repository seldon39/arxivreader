// Réglages de l'interface, enregistrés côté serveur (library/arxivreader.db).

import { PROVIDERS, assistantName, modelControls, setupNotice } from "./aiproviders.js";
import { api } from "./api.js";
import { applyStatic, setAIName, setLanguage, t } from "./i18n.js";
import { updatesSection } from "./updates.js";
import { ZOTERO_KEY, zoteroSection } from "./zotero.js";
import { chooseFolder, h, icons, toast } from "./util.js";

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
export const FEED_DEFAULT = ["math.DG", "math.CV", "math.SP", "math-ph"];
const WIDTHS = { narrow: "42rem", normal: "56rem", wide: "72rem", xwide: "90rem", full: "none" }; // pleine : toute la fenêtre (grands écrans)
export const READING_WIDTHS = Object.keys(WIDTHS); // de la plus étroite à la plus large (boutons du lecteur)

export const settings = { ...DEFAULTS };
const listeners = new Set();

// Langue du système (macOS, navigateur) : français s'il vient en premier parmi les deux, sinon anglais
export function systemLanguage() {
  const first = (navigator.languages || [navigator.language || ""]).find((tag) => /^(fr|en)\b/i.test(tag)) || "";
  return /^fr/i.test(first) ? "fr" : "en";
}

export async function loadSettings() {
  try {
    const saved = await api.settings();
    Object.assign(settings, saved);
    // installation neuve (aucune langue choisie) : celle du système, gardée pour le serveur (IA, export des notes)
    if (!saved.language) {
      settings.language = systemLanguage();
      api.saveSettings({ language: settings.language }).catch(() => {});
    }
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
  if (provider === "gemini" && status.geminiModel && status.geminiModel !== settings.aiGeminiModel) {
    await saveSettings({ aiGeminiModel: status.geminiModel });
  }
  const geminiFields = [
    h("label", { class: "row" }, h("span", {}, t("settings.geminiKey")),
      // texte masqué plutôt que « password » : Safari ne propose pas d'enregistrer un mot de passe
      h("input", { class: "text-input secret-input", type: "text", name: "geminiApiKey", value: settings.geminiApiKey || "",
        autocomplete: "off", spellcheck: "false", placeholder: t("settings.geminiKeyPlaceholder") })),
  ];
  const fields = provider === "gemini" ? geminiFields : provider !== "local" ? [] : [
    h("label", { class: "row" }, h("span", {}, t("settings.aiLocalUrl")),
      h("input", { class: "text-input", type: "text", name: "aiLocalUrl", value: settings.aiLocalUrl || "", placeholder: LOCAL_URL,
        spellcheck: "false", autocomplete: "off" })),
    models.length > 0 && h("label", { class: "row" }, h("span", {}, t("settings.aiLocalModel")),
      h("select", { class: "text-input", name: "aiLocalModel" },
        models.map((model) => h("option", { value: model, selected: model === settings.aiLocalModel }, model)))),
  ];
  // modèle et niveau de réflexion (assistant prêt ; modèle local : son choix ci-dessus)
  const [modelChoice, effortChoice] = provider === "local" ? [null, null]
    : modelControls({ ...status, provider }, null); // le niveau suit le modèle sur place
  const choices = [
    modelChoice && h("label", { class: "row" }, h("span", {}, t("ai.model")), modelChoice),
    effortChoice && h("label", { class: "row", hidden: effortChoice.hidden }, h("span", {}, t("ai.effort")), effortChoice),
    effortChoice && h("p", { class: "hint" }, t("settings.effortHint")),
  ];
  line.replaceWith(...fields.filter(Boolean), ...choices.filter(Boolean),
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

// Section ouverte (gardée quand la fenêtre est redessinée, par ex. au changement de langue)
let currentSection = "reading";

export function openSettingsDialog({ section = null } = {}) {
  const dialog = document.getElementById("settings-dialog");
  if (section) currentSection = section;
  const check = (name, label, checked, hint = null) => [
    h("label", { class: "check" }, h("span", {}, label), h("input", { type: "checkbox", class: "switch", name, checked })),
    hint && h("p", { class: "hint" }, hint),
  ];
  // Réglages en quatre sections, une à la fois ; chaque réglage sur une ligne d'un cadre
  // état de la synchronisation, bouton ; clé lue dans le champ (tapée puis bouton cliqué : enregistrée d'abord)
  const zoteroBox = zoteroSection({
    key: () => (dialog.querySelector('input[name="zoteroApiKey"]')?.value ?? settings.zoteroApiKey ?? "").trim(),
    save: async (key) => { if (key !== (settings.zoteroApiKey || "") && ZOTERO_KEY.test(key)) await saveSettings({ zoteroApiKey: key }); },
  });
  const sections = [
    ["reading", t("settings.reading"), [
      h("div", { class: "row" }, h("span", { id: "setting-language" }, t("settings.language")),
        segmented("language", [["fr", "Français"], ["en", "English"]], settings.language)),
      h("div", { class: "row" }, h("span", { id: "setting-theme" }, t("settings.theme")),
        segmented("theme", [["auto", t("settings.theme.auto")], ["light", t("settings.theme.light")], ["dark", t("settings.theme.dark")]], settings.theme)),
      h("div", { class: "row" }, h("span", { id: "setting-fontSize" }, t("settings.fontSize", { n: settings.fontSize })),
        h("div", { class: "range-row" },
          h("input", { type: "range", name: "fontSize", min: 14, max: 24, step: 1, value: settings.fontSize, "aria-labelledby": "setting-fontSize" }),
          h("button", { class: "button small-button", type: "button", name: "resetFontSize", onclick: (event) => {
            const range = dialog.querySelector('input[name="fontSize"]');
            range.value = DEFAULT_FONT_SIZE;
            range.dispatchEvent(new Event("input", { bubbles: true }));
            event.currentTarget.blur();
          } }, t("settings.default")))),
      h("div", { class: "row" }, h("span", { id: "setting-readingWidth" }, t("settings.width")),
        segmented("readingWidth", [["narrow", t("settings.width.narrow")], ["normal", t("settings.width.normal")], ["wide", t("settings.width.wide")], ["xwide", t("settings.width.xwide")], ["full", t("settings.width.full")]], settings.readingWidth)),
      h("div", { class: "row" }, h("span", { id: "setting-paperFont" }, t("settings.paperFont")),
        segmented("paperFont", [["auto", t("settings.paperFont.auto")], ["charter", "Charter"], ["lm", "Computer Modern"]], settings.paperFont)),
      h("div", { class: "row" }, h("span", { id: "setting-mathRenderer" }, t("settings.math")),
        segmented("mathRenderer", [["mathjax", t("settings.math.mathjax")], ["native", t("settings.math.native")]], settings.mathRenderer)),
      ...check("proofsCollapsed", t("settings.proofs"), settings.proofsCollapsed),
      ...check("marginMarks", t("settings.marginMarks"), settings.marginMarks),
      h("div", { class: "row" }, h("span", {}, t("settings.notesExport")),
        h("div", {}, h("a", { class: "button small-button", download: "", href: `/api/notes.zip?lang=${settings.language || "fr"}`,
          // rien d'annoté : un message plutôt qu'une archive vide
          onclick: async (event) => {
            if (event.currentTarget.dataset.checked) return;
            event.preventDefault();
            const link = event.currentTarget;
            // archive : la bibliothèque (même sans notes) et les notes ; vide seulement sans l'un ni l'autre
            const { papers, library } = await api.notesCount().catch(() => ({ papers: 1, library: 1 }));
            if (!papers && !library) { toast(t("notes.exportNone")); return; }
            if (exportFolder.path) {  // dossier choisi : écrite là (remplace la précédente), pas téléchargée
              try {
                const { path } = await api.exportNotesToFolder(settings.language || "fr");
                toast(t("settings.notesExportedTo", { path }), { duration: 7000 });
              } catch (error) { toast(t("error.generic", { message: error.message }), { error: true }); }
              return;
            }
            link.dataset.checked = "1";
            link.click();
            delete link.dataset.checked;
          } }, t("settings.notesExportButton")),
          " ", importNotesButton())),
      folderRow(),
      h("p", { class: "hint" }, t("settings.notesExportHint")),
      deviceLine(),
    ]],
    ["ai", t("settings.ai"), [
      h("div", { class: "row" }, h("span", { id: "setting-aiProvider" }, t("settings.aiProvider")),
        segmented("aiProvider", PROVIDERS.map((p) => [p, p === "local" ? t("settings.aiProvider.local") : { claude: "Claude", codex: "Codex", gemini: "Gemini" }[p]]),
          settings.aiProvider || "claude")),
      h("div", { class: "ai-settings" }),
    ]],
    ["feed", t("settings.feed"), [
      ...check("notifyFollowed", t("settings.notifyFollowed"), settings.notifyFollowed !== false, t("settings.notifyFollowedHint")),
      ...check("notifyCitations", t("settings.notifyCitations"), settings.notifyCitations !== false, t("settings.notifyCitationsHint")),
      h("label", { class: "row" }, h("span", {}, t("settings.feedCategories")),
        h("input", { class: "text-input", type: "text", name: "feedCategories", value: (settings.feedCategories || FEED_DEFAULT).join(", "), spellcheck: "false" })),
      h("label", { class: "row" }, h("span", {}, t("settings.s2Key")),
        // texte masqué plutôt que « password » : Safari ne propose pas d'enregistrer un mot de passe
        h("input", { class: "text-input secret-input", type: "text", name: "s2ApiKey", value: settings.s2ApiKey || "", autocomplete: "off",
          spellcheck: "false", placeholder: t("settings.s2KeyPlaceholder") })),
      h("p", { class: "hint" }, t("settings.s2KeyHint")),
    ]],
    ["zotero", "Zotero", [
      h("label", { class: "row" }, h("span", {}, t("settings.zoteroKey")),
        h("input", { class: "text-input secret-input", type: "text", name: "zoteroApiKey", value: settings.zoteroApiKey || "", autocomplete: "off",
          spellcheck: "false", placeholder: t("settings.s2KeyPlaceholder") })),
      h("p", { class: "hint" }, t("settings.zoteroKeyHint"), " ",
        h("a", { href: "https://www.zotero.org/settings/keys/new", target: "_blank", rel: "noopener noreferrer" }, "zotero.org/settings/keys/new"), "."),
      h("p", { class: "hint" }, t("settings.zoteroHow")),
      ...check("zoteroAutoSync", t("settings.zoteroAuto"), settings.zoteroAutoSync !== false, t("settings.zoteroAutoHint")),
      zoteroBox,
    ]],
    ["updates", t("settings.updates"), [
      ...check("checkUpdates", t("settings.checkUpdates"), settings.checkUpdates !== false),
      updatesSection(),
    ]],
  ];
  if (!sections.some(([key]) => key === currentSection)) currentSection = "reading";
  const panes = sections.map(([key, title, nodes]) => h("section", { class: "settings-pane", id: `settings-${key}`, role: "tabpanel",
    "aria-labelledby": `settings-tab-${key}`, hidden: key !== currentSection },
    h("h3", { class: "settings-pane-title" }, title), h("div", { class: "settings-card" }, nodes.filter(Boolean))));
  const show = (key) => {
    currentSection = key;
    panes.forEach((pane) => { pane.hidden = pane.id !== `settings-${key}`; });
    nav.querySelectorAll("button").forEach((button) => {
      const on = button.dataset.section === key;
      button.setAttribute("aria-selected", String(on));
      button.tabIndex = on ? 0 : -1;
    });
  };
  const nav = h("nav", { class: "settings-nav", role: "tablist", "aria-orientation": "vertical" },
    sections.map(([key, title]) => h("button", { type: "button", role: "tab", id: `settings-tab-${key}`, dataset: { section: key },
      "aria-controls": `settings-${key}`, "aria-selected": String(key === currentSection), tabIndex: key === currentSection ? 0 : -1,
      onclick: () => show(key) }, title)));
  // flèches haut/bas (ou gauche/droite) entre les sections
  nav.addEventListener("keydown", (event) => {
    const keys = sections.map(([key]) => key);
    const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const next = keys[(keys.indexOf(currentSection) + step + keys.length) % keys.length];
    show(next);
    nav.querySelector(`[data-section="${next}"]`).focus();
  });
  const form = h("form", { method: "dialog" },
    h("div", { class: "dialog-head" }, h("h2", {}, t("settings.title")),
      h("button", { class: "icon-button", value: "close", title: t("settings.close"), "aria-label": t("settings.close"), html: icons.close })),
    h("div", { class: "settings-layout" }, nav, h("div", { class: "settings-panes" }, panes)),
    h("div", { class: "actions" }, h("button", { class: "button", value: "close" }, t("settings.close"))),
  );
  form.addEventListener("input", (event) => {
    const { name, value, checked, type } = event.target;
    if (!name || event.target.classList.contains("ai-model")) return; // modèle et niveau : enregistrés par leurs menus
    if (name === "zoteroApiKey") zoteroBox.redraw();
    if (name === "feedCategories" || name === "s2ApiKey" || name === "aiLocalUrl" || name === "geminiApiKey" || name === "zoteroApiKey") return; // enregistrés à la sortie du champ
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
    } else if (name === "zoteroApiKey") {
      const key = value.trim();
      if (key && !ZOTERO_KEY.test(key)) {
        toast(t("settings.zoteroKeyBad"), { error: true });
        event.target.value = settings.zoteroApiKey || ""; // la clé enregistrée reste celle utilisée
        zoteroBox.redraw();
        return;
      }
      saveSettings({ zoteroApiKey: key }).then(() => zoteroBox.refresh());
    } else if (name === "geminiApiKey") {
      // focus rendu au champ seulement s'il l'a encore (sortie par Tab ou clic : il va ailleurs, et la clé resterait affichée)
      const keep = document.activeElement === event.target ? "geminiApiKey" : null;
      saveSettings({ geminiApiKey: value.trim() }).then(() => renderAISettings(form.querySelector(".ai-settings"), keep));
    } else if (name === "aiLocalUrl") {
      const url = value.trim().replace(/\/+$/, "");
      if (url && !localUrl(url)) {
        toast(t("settings.aiLocalUrlBad"), { error: true });
        return;
      }
      const keep = document.activeElement === event.target ? "aiLocalUrl" : null;
      saveSettings({ aiLocalUrl: url }).then(() => renderAISettings(form.querySelector(".ai-settings"), keep));
    }
  });
  renderAISettings(form.querySelector(".ai-settings"));
  dialog.replaceChildren(form);
  applyStatic(dialog);
  if (!dialog.open) dialog.showModal();

}

// ---------- Import des notes (fichiers exportés par arXivReader, signés : app/notesync.py) ----------

// Dossier d'export : Téléchargements par défaut ; un dossier choisi (synchronisé, par exemple) reçoit l'archive
const exportFolder = { path: "" };

function folderRow() {
  const label = h("span", { class: "muted small folder-path" });
  const clear = h("button", { class: "icon-button small", type: "button", title: t("settings.notesFolderReset"), "aria-label": t("settings.notesFolderReset"),
    html: icons.close, hidden: true });
  const show = () => {
    // fin du chemin (le dossier et son parent), le chemin entier au survol
    const parts = exportFolder.path.split(/[\\/]/).filter(Boolean);
    label.textContent = exportFolder.path ? `${parts.length > 2 ? "…/" : ""}${parts.slice(-2).join("/")}` : t("settings.notesFolderDownloads");
    label.title = exportFolder.path;
    clear.hidden = !exportFolder.path;
  };
  const save = async (path) => {
    try { exportFolder.path = (await api.setNotesFolder(path)).folder; show(); }
    catch (error) { toast(t("error.generic", { message: error.message }), { error: true }); }
  };
  clear.addEventListener("click", () => save(""));
  api.notesDevice().then(({ folder }) => { exportFolder.path = folder || ""; show(); }).catch(() => {});
  show();
  return h("div", { class: "row folder-row" }, h("span", {}, t("settings.notesFolder")),
    h("div", { class: "folder-choice" }, label, clear,
      h("button", { class: "button small-button", type: "button", onclick: async () => {
        const path = await chooseFolder(t("settings.notesFolderPrompt"));
        if (path) save(path);
      } }, t("settings.notesFolderChoose"))));
}

// empreinte de la clé qui signe les notes exportées ici : à comparer quand un autre ordinateur demande confiance
function deviceLine() {
  const line = h("p", { class: "hint muted small" });
  api.notesDevice().then(({ fingerprint }) => { line.textContent = t("settings.notesDevice", { fingerprint }); }).catch(() => line.remove());
  return line;
}

function importNotesButton() {
  const input = h("input", { type: "file", accept: ".md,.zip,text/markdown,application/zip", multiple: true, hidden: true });
  input.addEventListener("change", () => { importNotes([...input.files]); input.value = ""; });
  return h("span", {}, h("button", { class: "button small-button", type: "button", onclick: () => input.click() }, t("settings.notesImportButton")), input);
}

const asBase64 = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve({ name: file.name, data: String(reader.result).split(",", 2)[1] || "" });
  reader.onerror = () => reject(reader.error);
  reader.readAsDataURL(file);
});

async function importNotes(list) {
  if (!list.length) return;
  try {
    const files = await Promise.all(list.map(asBase64));
    let report = await api.importNotes(files);
    const unknown = Object.entries(report.untrusted || {});
    if (unknown.length) {  // notes d'un autre ordinateur : l'accepter une fois, ses fichiers passent ensuite
      if (!(await trustDialog(unknown))) return;
      report = await api.importNotes(files, unknown.map(([fingerprint]) => fingerprint));
    }
    const parts = [t("settings.notesImported", { added: report.added, updated: report.updated })];
    if (report.papers) parts.push(t("settings.notesImportedPapers", { n: report.papers }));
    if (report.folders) parts.push(t("settings.notesImportedFolders", { n: report.folders }));
    if (report.rejected.length) parts.push(t("settings.notesImportRejected", { n: report.rejected.length }));
    if (report.missing.length) parts.push(t("settings.notesImportMissing", { n: report.missing.length }));
    toast(parts.join(" "), { duration: 9000, error: !report.added && !report.updated && report.rejected.length > 0 });
  } catch (error) {
    toast(t("error.generic", { message: error.message }), { error: true });
  }
}

function trustDialog(devices) {
  return new Promise((resolve) => {
    const dialog = h("dialog", { class: "meta-dialog" },
      h("form", { method: "dialog" },
        h("h3", { class: "meta-heading" }, t("settings.notesTrustTitle")),
        h("p", {}, t("settings.notesTrustText")),
        h("ul", {}, devices.map(([fingerprint, device]) => h("li", {}, h("strong", {}, device || "?"), " ", h("span", { class: "muted small" }, fingerprint)))),
        h("p", { class: "muted small" }, t("settings.notesTrustHint")),
        h("div", { class: "dialog-actions" },
          h("button", { class: "button", type: "button", onclick: () => dialog.close("no") }, t("library.folder.cancel")),
          h("button", { class: "button primary", value: "yes" }, t("settings.notesTrustAccept")))));
    dialog.addEventListener("close", () => { dialog.remove(); resolve(dialog.returnValue === "yes"); });
    document.body.append(dialog);
    dialog.showModal();
  });
}
