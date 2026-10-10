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
// Position de défilement de chaque page quittée (bibliothèque, feed), rétablie au retour
const scrollMemory = new Map();
let currentHash = location.hash;
history.scrollRestoration = "manual";

async function route() {
  const token = ++routeToken;
  scrollMemory.set(currentHash, scrollY);
  currentHash = location.hash;
  cleanup?.();
  cleanup = null;
  scrollTo({ top: 0, behavior: "instant" });
  const match = location.hash.match(/^#\/paper\/(.+)$/);
  const feedMatch = location.hash.match(/^#\/feed(?:\/(recommended|import|profile))?$/);
  const section = match ? "paper" : feedMatch ? "#/feed" : "#/";
  document.querySelectorAll(".topnav a").forEach((a) => {
    const active = a.getAttribute("href") === section;
    a.classList.toggle("active", active);
    if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  try {
    const dispose = feedMatch ? await renderFeed(view, feedMatch[1] || "arxiv")
      : match ? await renderReader(view, decodeURIComponent(match[1]))
        : await renderLibrary(view);
    if (token !== routeToken) { dispose?.(); return; } // page déjà quittée pendant le chargement
    cleanup = dispose || null;
    // le lecteur reprend lui-même à l'endroit où l'on s'était arrêté
    if (!match && scrollMemory.has(location.hash)) scrollTo({ top: scrollMemory.get(location.hash), behavior: "instant" });
  } catch (error) {
    if (token !== routeToken) return;
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
