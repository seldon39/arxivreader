// Point d'entrée : réglages, navigation entre la bibliothèque et le lecteur.

import { t } from "./i18n.js";
import { renderFeed } from "./feed.js";
import { renderLibrary } from "./library.js";
import { renderReader } from "./reader.js";
import { loadSettings, onSettingsChange, openSettingsDialog, settings } from "./settings.js";
import { h } from "./util.js";

const view = document.getElementById("view");
let cleanup = null;
let routeToken = 0;

async function route() {
  const token = ++routeToken;
  cleanup?.();
  cleanup = null;
  scrollTo({ top: 0, behavior: "instant" });
  const match = location.hash.match(/^#\/paper\/(.+)$/);
  const feedMatch = location.hash.match(/^#\/feed(?:\/(recommended|import|profile))?$/);
  const section = match ? "paper" : feedMatch ? "#/feed" : "#/";
  document.querySelectorAll(".topnav a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === section));
  try {
    if (feedMatch) {
      await renderFeed(view, feedMatch[1] || "arxiv");
    } else if (match) {
      const dispose = await renderReader(view, decodeURIComponent(match[1]));
      if (token === routeToken) cleanup = dispose; else dispose();
    } else {
      await renderLibrary(view);
    }
  } catch (error) {
    view.replaceChildren(h("section", { class: "library" },
      h("p", { class: "notice" }, t("error.generic", { message: error.message })),
      h("a", { href: "#/" }, t("reader.back"))));
  }
}

await loadSettings();
let rendering = `${settings.language}/${settings.mathRenderer}`;
onSettingsChange(() => {
  // Langue ou moteur de formules changé : la page est redessinée
  if (`${settings.language}/${settings.mathRenderer}` !== rendering) {
    rendering = `${settings.language}/${settings.mathRenderer}`;
    route();
  }
});
document.getElementById("settings-button").addEventListener("click", openSettingsDialog);
addEventListener("hashchange", route);
route();
