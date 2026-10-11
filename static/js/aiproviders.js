// Assistants IA possibles (réglages) : Claude, Codex, Gemini, modèle local. Noms affichés, et marche à
// suivre quand l'assistant choisi n'est pas prêt (pas installé, pas connecté, serveur local éteint).

import { api } from "./api.js";
import { t } from "./i18n.js";
import { saveSettings, settings as appSettings } from "./settings.js";
import { h, toast, toastError } from "./util.js";

export const PROVIDERS = ["claude", "codex", "gemini", "local"];
const NAMES = { claude: "Claude", codex: "Codex", gemini: "Gemini" };

// Commande à lancer : [pas connecté, pas installé] (Claude : celle du SDK, longue, non affichée)
const COMMANDS = {
  claude: [null, null],
  codex: ["codex login", "npm install -g @openai/codex"],
  gemini: [null, null], // clé d'API dans les réglages, pas de commande
  local: ["ollama pull qwen3", "OLLAMA_CONTEXT_LENGTH=32768 ollama serve"],
};

export const providerOf = (model) =>
  model === "codex" || model === "gemini" ? model : String(model || "").startsWith("local:") ? "local" : "claude";

// Modèle demandé au serveur (comme ai.current_model) : opus, sonnet, codex, gemini, local:<nom>
export function currentModel(settings) {
  if (settings.aiProvider === "codex" || settings.aiProvider === "gemini") return settings.aiProvider;
  if (settings.aiProvider === "local") return `local:${settings.aiLocalModel || ""}`;
  return /^[a-z0-9][a-z0-9.-]{0,99}$/.test(settings.aiModel || "") && !["codex", "gemini"].includes(settings.aiModel) ? settings.aiModel : "opus";
}

// Réglages du modèle et du niveau de réflexion, pour chaque assistant
const MODEL_SETTING = { claude: "aiModel", codex: "aiCodexModel", gemini: "aiGeminiModel", local: "aiLocalModel" };
const EFFORT_SETTING = { claude: "aiClaudeEffort", codex: "aiCodexEffort", gemini: "aiGeminiEffort" };

// Choix du modèle et du niveau de réflexion de l'assistant prêt, d'après la liste que le serveur donne pour
// le compte connecté (Claude, Codex) ou la clé (Gemini) ; modèle local : ses modèles installés, sans niveau.
// Renvoie [choix du modèle, choix du niveau] (vides si rien à choisir) ; `onSaved` après chaque choix.
export function modelControls(status, onSaved) {
  const provider = status?.provider;
  if (!status?.loggedIn || !MODEL_SETTING[provider]) return [null, null];
  const choices = status.choices || (status.models || []).map((name) => ({ value: name, label: name, levels: [] }));
  if (!choices.length) return [null, null];
  const chosen = status.chosen || appSettings[MODEL_SETTING[provider]] || choices[0].value;
  const levelsOf = (value) => choices.find((c) => c.value === value)?.levels || [];
  const model = h("select", { class: "ai-model", name: MODEL_SETTING[provider], "aria-label": t("ai.model"), title: t("ai.model") },
    choices.map((c) => h("option", { value: c.value, selected: c.value === chosen, title: c.description || null }, c.label)));
  const levels = levelsOf(chosen);
  const effort = EFFORT_SETTING[provider] && levels.length ? h("select", { class: "ai-model ai-effort", name: EFFORT_SETTING[provider],
    "aria-label": t("ai.effort"), title: t("ai.effort") },
    ["", ...levels].map((level) => h("option", { value: level, selected: level === (status.effort || "") },
      level ? t(`ai.effort.${level}`) : t("ai.effort.default")))) : null;
  model.addEventListener("change", async () => {
    const values = { [MODEL_SETTING[provider]]: model.value };
    // niveau que le nouveau modèle ne propose pas : celui de l'assistant par défaut
    if (EFFORT_SETTING[provider] && status.effort && !levelsOf(model.value).includes(status.effort)) values[EFFORT_SETTING[provider]] = "";
    await saveSettings(values);
    Object.assign(status, { chosen: model.value, effort: values[EFFORT_SETTING[provider]] ?? status.effort });
    onSaved?.();
  });
  effort?.addEventListener("change", async () => {
    await saveSettings({ [EFFORT_SETTING[provider]]: effort.value });
    status.effort = effort.value;
    onSaved?.();
  });
  return [model, effort];
}

// Nom de l'assistant choisi, pour {ai} dans les textes
export function assistantName(settings) {
  const provider = PROVIDERS.includes(settings.aiProvider) ? settings.aiProvider : "claude";
  return provider === "local" ? settings.aiLocalModel || t("ai.local") : NAMES[provider];
}

// Modèle d'un échange enregistré : « Claude Opus », « Codex », « qwen3 (IA locale) »…
export function modelLabel(model) {
  if (providerOf(model) === "claude" && model) return `Claude ${model[0].toUpperCase()}${model.slice(1)}`;
  if (providerOf(model) === "local") return `${model.slice(6) || "?"} (${t("ai.local")})`;
  return NAMES[providerOf(model)] || model || "Claude";
}

// Auteur d'une annotation proposée par l'IA (claude, codex, gemini, local)
export const authorName = (author) => NAMES[author] || t("ai.local");

// Page de Google AI Studio où créer une clé d'API Gemini
export const GEMINI_KEY_URL = "https://aistudio.google.com/apikey";

export function setupNotice(status, onRetry) {
  const provider = status.provider || "claude";
  const step = status.available ? "login" : "missing";
  // Gemini : une clé à créer sur Google AI Studio et à coller dans les réglages (pas de Terminal)
  const action = provider === "gemini"
    ? h("a", { class: "button small-button primary", href: GEMINI_KEY_URL, target: "_blank", rel: "noopener" }, t("ai.setup.geminiKey"))
    : h("button", { class: "button small-button primary", type: "button", onclick: () => api.aiSetup(provider, step)
      .then(() => toast(t("ai.setup.opened")), toastError) }, t(provider === "claude" ? "ai.setup.claude" : "ai.setup.terminal"));
  return h("div", { class: "notice ai-login" },
    h("strong", {}, t(`ai.${step}.title.${provider}`)),
    h("p", {}, t(`ai.${step}.text.${provider}`)),
    status.error && status.error !== "network" && h("p", { class: "hint" }, status.error),
    status.error === "network" && h("p", { class: "hint" }, t("ai.gemini.network")),
    COMMANDS[provider][status.available ? 0 : 1] && h("code", { class: "ai-command" }, COMMANDS[provider][status.available ? 0 : 1]),
    h("div", { class: "ai-setup-actions" }, action,
      onRetry && h("button", { class: "button small-button", type: "button", onclick: onRetry }, t("ai.login.check"))));
}
