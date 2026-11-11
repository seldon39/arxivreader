// Réglages de l'interface, enregistrés côté serveur (library/arxivreader.db).

import { PROVIDERS, assistantName, authorName, setupNotice } from "./aiproviders.js";
import { api } from "./api.js";
import { applyStatic, setAIName, setLanguage, t } from "./i18n.js";
import { updatesSection } from "./updates.js";
import { ZOTERO_KEY, zoteroSection } from "./zotero.js";
import { chooseFolder, h, icons, toast, toastError } from "./util.js";

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
  aiDefault: "",      // assistant sélectionné au lancement de l'application (vide : le dernier choisi)
  aiLocalUrl: "",     // vide : Ollama (http://127.0.0.1:11434/v1)
};
const LOCAL_URL = "http://127.0.0.1:11434/v1";
export const FEED_DEFAULT = ["math.DG", "math.CV", "math.SP", "math-ph"];
const WIDTHS = { narrow: "42rem", normal: "56rem", wide: "72rem", xwide: "90rem", full: "none" }; // pleine : toute la fenêtre (grands écrans)
export const READING_WIDTHS = Object.keys(WIDTHS); // de la plus étroite à la plus large (boutons du lecteur)

export const settings = { ...DEFAULTS };
const listeners = new Set();

// Langue du système (macOS, navigateur) : français s'il vient en premier parmi les deux, sinon anglais
function systemLanguage() {
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

function applySettings() {
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

// Assistant à configurer : à quoi il sert, s'il est prêt (sinon la marche à suivre), son compte ou sa clé, et pour un
// modèle local, l'adresse du serveur. Celui qui répond et son modèle se choisissent dans l'onglet IA (modelPicker).
let aiCheck = 0;
let configuring = "claude";  // assistant affiché ici (gardé quand la fenêtre est redessinée) : rien n'est enregistré
let aiSeen = false;          // section vue depuis l'ouverture : la liste des assistants prêts y est chargée
const accountsChanged = () => dispatchEvent(new Event("arxivreader:ai-accounts"));
let readySeen = {};          // assistants vus dans la fenêtre : prêts ou non (pour repérer celui qui vient de l'être)

let aiTouched = false;       // assistant affiché, clé, adresse ou défaut changés : l'onglet IA revérifie à la fermeture
const readStatus = (provider) => api.aiStatus(provider).catch(() => null);
const conclusive = (status) => Boolean(status) && !status.inconclusive;  // réseau, délai dépassé : on ne sait pas

// Rend un assistant actif (modèle local manquant : le premier installé, avant toute demande), puis fait revérifier l'onglet IA
async function activate(provider, status, values = {}) {
  const repair = provider === "local" && status?.models?.length && !status.models.includes(settings.aiLocalModel);
  await saveSettings({ ...values, aiProvider: provider, ...(repair ? { aiLocalModel: status.models[0] } : {}) });
  accountsChanged();
}

// Assistant qu'on vient de configurer : il prend la place de celui utilisé si celui-ci n'est pas prêt (sinon, rien ne répondrait)
async function adoptIfActiveNotReady(provider, status) {
  const active = settings.aiProvider;
  if (provider === active) return;
  const current = await readStatus();
  if (conclusive(current) && !current.loggedIn && settings.aiProvider === active) await activate(provider, status);  // choix fait entre-temps : il prime
}

// Retient si un assistant est prêt, même si la fenêtre en affiche déjà un autre ; agit s'il vient de changer
async function noteReadiness(provider, status) {
  if (!conclusive(status)) return;
  const ready = Boolean(status.loggedIn);
  const changed = provider in readySeen && readySeen[provider] !== ready;
  readySeen[provider] = ready;
  if (!changed) return;
  if (ready) await adoptIfActiveNotReady(provider, status);
  renderDefaultChoice();
}

// Assistant par défaut, parmi ceux qui sont prêts ; celui enregistré reste proposé s'il ne l'est plus (marqué)
let defaultCheck = 0;
let readyStatuses = {};  // états lus pour le menu (modèles locaux, au moment de choisir)
async function renderDefaultChoice() {
  const holder = document.querySelector("#settings-dialog .ai-default");
  if (!holder) return;
  const check = ++defaultCheck;
  let found = [];
  try {
    const [active, others] = await Promise.all([api.aiStatus(), api.aiPartners()]);
    found = [active, ...others].filter((status) => status.loggedIn);
  } catch { /* vérification impossible : seul le choix enregistré reste proposé */ }
  if (check !== defaultCheck || !holder.isConnected) return;
  const ready = found.map((status) => status.provider);
  readyStatuses = Object.fromEntries(found.map((status) => [status.provider, status]));
  const chosen = settings.aiDefault || "";
  const shown = PROVIDERS.filter((p) => ready.includes(p) || p === chosen);
  holder.replaceChildren(
    h("label", { class: "row" }, h("span", {}, t("settings.aiDefault")),
      h("select", { class: "text-input", name: "aiDefault" },
        h("option", { value: "", selected: !chosen }, t("settings.aiDefault.last")),
        shown.map((p) => h("option", { value: p, selected: p === chosen }, ready.includes(p) ? authorName(p) : t("settings.aiDefault.notReady", { name: authorName(p) }))))),
    h("p", { class: "hint" }, t("settings.aiDefaultHint")));
}
async function renderAISettings(holder, keepFocus = null) {
  const provider = configuring;
  const check = ++aiCheck;
  const line = h("p", { class: "hint", "aria-live": "polite" }, t("settings.aiChecking"));
  holder.replaceChildren(h("p", { class: "hint" }, t(`settings.aiHint.${provider}`)), line);
  const read = await readStatus(provider);
  noteReadiness(provider, read);  // indépendant de ce qui est dessiné ici
  if (check !== aiCheck || !holder.isConnected) return; // réglage changé entre-temps
  const status = read || { provider, available: false, loggedIn: false };
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
  ];
  const again = () => { accountsChanged(); renderAISettings(holder); };
  line.replaceWith(...fields.filter(Boolean),
    ...(status.loggedIn ? accountControls(provider, status, again) : [setupNotice({ ...status, provider }, again)]));
  if (keepFocus && !holder.contains(document.activeElement)) holder.querySelector(`[name="${keepFocus}"]`)?.focus();
}

// Assistant prêt : le compte connecté, et en changer ou s'en déconnecter (Claude, Codex : commandes de leur outil ;
// Gemini : la clé, celle d'un autre compte Google la remplace ; modèle local : sans compte)
function accountControls(provider, status, again) {
  const account = provider === "claude" ? [status.email, status.subscription && t("settings.aiPlan", { plan: status.subscription[0].toUpperCase() + status.subscription.slice(1) })].filter(Boolean).join(" · ")
    : provider === "codex" ? t("settings.aiAccount.chatgpt") : "";
  const ready = h("p", { class: "hint ai-ready" }, `✓ ${t("settings.aiReady")}`, account && h("span", { class: "muted" }, ` · ${account}`));
  if (provider === "gemini") {
    const remove = h("button", { class: "button small-button", type: "button",
      onclick: () => saveSettings({ geminiApiKey: "" }).then(again) }, t("settings.geminiRemove"));
    return [ready, h("p", { class: "hint" }, t("settings.geminiOther")), h("div", { class: "ai-setup-actions" }, remove)];
  }
  if (provider === "local") return [ready];
  const check = h("button", { class: "button small-button", type: "button", hidden: true, onclick: again }, t("ai.login.check"));
  const change = h("button", { class: "button small-button", type: "button", onclick: () => api.aiSetup(provider, "switch")
    .then(() => { toast(t("ai.setup.opened")); check.hidden = false; }, toastError) }, t("settings.aiSwitch"));
  const logout = h("button", { class: "button small-button", type: "button", onclick: () => api.aiLogout(provider).then(again, toastError) },
    t("settings.aiLogout"));
  return [ready, h("div", { class: "ai-setup-actions" }, change, logout, check),
    provider === "claude" && h("p", { class: "hint" }, t("settings.claudeShared"))].filter(Boolean);
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
  if (!dialog.open) {  // à l'ouverture : celui utilisé
    configuring = settings.aiProvider || "claude";
    aiSeen = false;
    aiTouched = false;
    readySeen = {};
  }
  dialog.onclose = () => { if (aiTouched) accountsChanged(); };  // un assistant lancé ou connecté entre-temps apparaît dans l'onglet IA
  if (section) currentSection = section;
  aiSeen ||= currentSection === "ai";  // fenêtre rouverte sur cette section
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
      texToolsRow(),
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
      h("div", { class: "ai-default" }),
      h("div", { class: "row" }, h("span", { id: "setting-aiConfigure" }, t("settings.aiProvider")),
        segmented("aiConfigure", PROVIDERS.map((p) => [p, p === "local" ? t("settings.aiProvider.local") : { claude: "Claude", codex: "Codex", gemini: "Gemini" }[p]]),
          configuring)),
      h("p", { class: "hint" }, t("settings.aiChooseHint")),
      h("div", { class: "ai-settings" }),
      aiProfileSection(settings),
    ]],
    ["feed", t("settings.feed"), [
      ...check("notifyFollowed", t("settings.notifyFollowed"), settings.notifyFollowed !== false, t("settings.notifyFollowedHint")),
      ...check("notifyCitations", t("settings.notifyCitations"), settings.notifyCitations !== false, t("settings.notifyCitationsHint")),
      ...check("feedNewVersions", t("settings.feedNewVersions"), settings.feedNewVersions !== false, t("settings.feedNewVersionsHint")),
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
    // À propos : version installée et mises à jour, puis copyright et licence
    ["about", t("settings.about"), [
      ...check("checkUpdates", t("settings.checkUpdates"), settings.checkUpdates !== false),
      updatesSection(),
      backupsLine(),
      h("p", {}, h("strong", {}, "arXivReader"), " — © 2026 ", h("a", { href: "https://github.com/seldon39", target: "_blank",
        rel: "noopener noreferrer" }, "seldon39")),
      h("p", {}, t("about.license"), " ", h("a", { href: "https://github.com/seldon39/arxivreader", target: "_blank",
        rel: "noopener noreferrer" }, "github.com/seldon39/arxivreader")),
      h("p", {}, t("about.vibe")),
    ]],
  ];
  if (!sections.some(([key]) => key === currentSection)) currentSection = "reading";
  const panes = sections.map(([key, title, nodes]) => h("section", { class: "settings-pane", id: `settings-${key}`, role: "tabpanel",
    "aria-labelledby": `settings-tab-${key}`, hidden: key !== currentSection },
    h("h3", { class: "settings-pane-title" }, title), h("div", { class: "settings-card" }, nodes.filter(Boolean))));
  const show = (key) => {
    currentSection = key;
    if (key === "ai" && !aiSeen) {  // première visite : la liste demande leur état à tous les assistants
      aiSeen = true;
      renderDefaultChoice();
    }
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
  // après une clé ou une adresse : l'assistant modifié est revérifié, même si la fenêtre en affiche déjà un autre
  const recheck = (provider, keep) => {
    if (configuring !== provider) readStatus(provider).then((status) => noteReadiness(provider, status));
    renderAISettings(form.querySelector(".ai-settings"), keep);
  };
  form.addEventListener("input", (event) => {
    const { name, value, checked, type } = event.target;
    if (!name) return;
    if (name === "aiDefault") { // choisi : sélectionné tout de suite, et à chaque lancement
      aiTouched = true;
      if (value) activate(value, readyStatuses[value], { aiDefault: value });
      else saveSettings({ aiDefault: "" });
      return;
    }
    if (name === "aiConfigure") { // seulement l'assistant affiché ici
      configuring = value;
      aiTouched = true;
      renderAISettings(form.querySelector(".ai-settings"));
      return;
    }
    if (name === "zoteroApiKey") zoteroBox.redraw();
    if (name === "feedCategories" || name === "s2ApiKey" || name === "aiLocalUrl" || name === "geminiApiKey" || name === "zoteroApiKey"
      || name === "aiProfile") return; // enregistrés à la sortie du champ
    const parsed = type === "checkbox" ? checked : name === "fontSize" ? Number(value) : value;
    if (name === "fontSize") form.querySelector("#setting-fontSize").textContent = t("settings.fontSize", { n: value });
    saveSettings({ [name]: parsed });
    if (name === "feedNewVersions") dispatchEvent(new Event("arxivreader:feed-sections"));
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
      aiTouched = true;
      saveSettings({ geminiApiKey: value.trim() }).then(() => recheck("gemini", keep));
    } else if (name === "aiProfile") {
      saveSettings({ aiProfile: value.trim() }).then(() => form.querySelector(".ai-profile-preview")?.refresh());
    } else if (name === "aiLocalUrl") {
      const url = value.trim().replace(/\/+$/, "");
      if (url && !localUrl(url)) {
        toast(t("settings.aiLocalUrlBad"), { error: true });
        return;
      }
      const keep = document.activeElement === event.target ? "aiLocalUrl" : null;
      aiTouched = true;
      saveSettings({ aiLocalUrl: url }).then(() => recheck("local", keep));
    }
  });
  renderAISettings(form.querySelector(".ai-settings"));
  dialog.replaceChildren(form);
  applyStatic(dialog);
  if (!dialog.open) dialog.showModal();
  if (aiSeen) renderDefaultChoice();  // ouverte sur cette section, ou redessinée

}

// Profil pour l'IA : texte libre, joint à chaque demande avec les éléments de l'onglet Profil (app/ai.py, reader_profile) ;
// l'aperçu montre exactement ce qui part
function aiProfileSection(settings) {
  const preview = h("pre", { class: "ai-profile-text" });
  const details = h("details", { class: "ai-profile-preview" }, h("summary", {}, t("settings.aiProfilePreview")), preview);
  details.refresh = async () => {
    if (!details.open) return;
    const { text } = await api.aiProfile().catch(() => ({ text: "" }));
    preview.textContent = text || t("settings.aiProfileEmpty");
  };
  details.addEventListener("toggle", details.refresh);
  return h("div", { class: "ai-profile" },
    h("label", { for: "setting-aiProfile" }, t("settings.aiProfile")),
    h("textarea", { class: "text-input", id: "setting-aiProfile", name: "aiProfile", rows: 4, maxlength: 2000,
      placeholder: t("settings.aiProfilePlaceholder") }, settings.aiProfile || ""),
    h("p", { class: "hint" }, t("settings.aiProfileHint")),
    details);
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
    const separator = exportFolder.path.includes("\\") ? "\\" : "/";  // Windows : « …\\A\\B »
    const parts = exportFolder.path.split(/[\\/]/).filter(Boolean);
    label.textContent = exportFolder.path ? `${parts.length > 2 ? `…${separator}` : ""}${parts.slice(-2).join(separator)}` : t("settings.notesFolderDownloads");
    label.title = exportFolder.path;
    clear.hidden = !exportFolder.path;
  };
  const save = async (path) => {
    try { exportFolder.path = (await api.setNotesFolder(path)).folder; show(); }
    catch (error) { toast(t("error.generic", { message: error.message }), { error: true }); }
  };
  clear.addEventListener("click", () => save(""));
  exportFolder.path = "";  // jusqu'à la réponse du serveur : pas l'état d'une ouverture précédente des réglages
  api.notesDevice().then(({ folder }) => { exportFolder.path = folder || ""; show(); }).catch(() => {});
  show();
  return h("div", { class: "row folder-row" }, h("span", {}, t("settings.notesFolder")),
    h("div", { class: "folder-choice" }, label, clear,
      h("button", { class: "button small-button", type: "button", onclick: async () => {
        const path = await chooseFolder(t("settings.notesFolderPrompt"));
        if (path) save(path);
      } }, t("settings.notesFolderChoose"))));
}

// fichiers TeX de l'ordinateur : programme qui les compile ici (latexmk, sinon pdflatex), ou comment l'installer
function texToolsRow() {
  const status = h("span", { class: "muted small" });
  api.texTools().then(({ tools }) => {
    const tool = tools.latexmk || tools.pdflatex;
    status.textContent = (tool ? t("settings.texFound", { tool: tool.split(/[\\/]/).pop().replace(/\.exe$/i, "") }) : t("settings.texMissing"))
      + (tools.latexmlc ? ` · ${t("settings.latexmlFound")}` : "");
    status.title = tool || "";
  }).catch(() => status.remove());
  return h("div", { class: "row" }, h("span", {}, t("settings.texTools")), status);
}

// copie quotidienne de la base (7 gardées) : date de la dernière et dossier (le Finder l'ouvre depuis « Afficher la bibliothèque »)
function backupsLine() {
  const line = h("p", { class: "hint muted small" });
  api.libraryBackups().then(({ path, last, count, recovery }) => {
    line.append(last ? t("settings.backups", { date: last, n: count }) : t("settings.backupsNone"), " ", t("settings.backupsPath"), " ",
      h("code", {}, path));
    if (recovery) line.append(" ", recovery.restored ? t("settings.backupsRestored", { date: recovery.at, name: recovery.restored })
      : t("settings.backupsAside", { date: recovery.at }));
  }).catch(() => line.remove());
  return line;
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
      report = await api.importNotes(files, unknown.map(([key]) => key));
    }
    const parts = [t("settings.notesImported", { added: report.added, updated: report.updated })];
    if (report.deleted) parts.push(t("settings.notesImportedDeleted", { n: report.deleted }));
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
        h("ul", {}, devices.map(([, { device, fingerprint }]) => h("li", {}, h("strong", {}, device || "?"), " ", h("span", { class: "muted small" }, fingerprint)))),
        h("p", { class: "muted small" }, t("settings.notesTrustHint")),
        h("div", { class: "dialog-actions" },
          h("button", { class: "button", type: "button", onclick: () => dialog.close("no") }, t("library.folder.cancel")),
          h("button", { class: "button primary", value: "yes" }, t("settings.notesTrustAccept")))));
    dialog.addEventListener("close", () => { dialog.remove(); resolve(dialog.returnValue === "yes"); });
    document.body.append(dialog);
    dialog.showModal();
  });
}
