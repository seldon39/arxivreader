// Point d'entrée : réglages, navigation entre la bibliothèque et le lecteur.

import { WINDOWS, t, tn } from "./i18n.js";
import { renderFeed } from "./feed.js";
import { renderLibrary } from "./library.js";
import { renderReader } from "./reader.js";
import { defineMacros } from "./texview.js";
import { renderRelated } from "./related.js";
import { renderSearch } from "./search.js";
import { loadSettings, onSettingsChange, openSettingsDialog, settings } from "./settings.js";
import { refreshUpdates } from "./updates.js";
import { api } from "./api.js";
import { droppedPapers, h, icons, mayBePaper, nativeBridge, navigation, openRoute, toast, toastError, wantsNewTab } from "./util.js";
import { addToLibrary, selectionDropped } from "./selection.js";
import { watchZotero } from "./zotero.js";
import { maybeOnboard } from "./onboarding.js";

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
  const searchMatch = location.hash === "#/search";
  const relatedMatch = location.hash.match(/^#\/related(?:\/(.+))?$/);
  const section = match ? "paper" : feedMatch ? "#/feed" : searchMatch ? "#/search" : relatedMatch ? "#/related" : "#/";
  if (!match) {
    navigation.listHash = location.hash || "#/";
    defineMacros([]).catch(() => {});  // macros de l'article quitté : plus valables dans le feed, la recherche…
  }
  // titre de l'onglet (et de la fenêtre) : la page ; le lecteur y met le titre de l'article
  document.title = match ? "arXivReader" : feedMatch ? t("feed.title") : searchMatch ? t("search.title") : relatedMatch ? t("related.title") : t("library.title");
  document.querySelectorAll(".topnav a").forEach((a) => {
    const active = a.getAttribute("href") === section;
    a.classList.toggle("active", active);
    if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  try {
    const dispose = feedMatch ? await renderFeed(view, feedMatch[1] || "arxiv")
      : searchMatch ? await renderSearch(view)
      : relatedMatch ? await renderRelated(view, relatedMatch[1] ? decodeURIComponent(relatedMatch[1]) : null)
      : match ? await renderReader(view, ...decodeURIComponent(match[1]).split("/"), () => token === routeToken)
        : await renderLibrary(view, { restoreTo: scrollMemory.get(location.hash) || 0 });
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

// Article glissé sur « Bibliothèque » (ajouté, puis la bibliothèque dans un nouvel onglet) ou sur « Papiers liés »
// (son graphe dans un nouvel onglet ; trois au plus pour une sélection : un graphe se construit à la fois)
function navDrop(selector, dropped) {
  const link = document.querySelector(selector);
  link.addEventListener("dragover", (event) => {
    if (!mayBePaper(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    link.classList.add("drop-target");
  });
  link.addEventListener("dragleave", () => link.classList.remove("drop-target"));
  link.addEventListener("drop", async (event) => {
    event.preventDefault();
    link.classList.remove("drop-target");
    const papers = droppedPapers(event.dataTransfer);
    if (!papers.length) { toast(t("library.drop.notPaper"), { error: true }); return; }
    selectionDropped();
    try { await dropped(papers); } catch (error) { toastError(error); }
  });
}
navDrop('.topnav a[href="#/"]', async (papers) => {
  await addToLibrary(papers, await api.papers());
  openRoute("#/", true, true);
});
navDrop('.topnav a[href="#/related"]', async (papers) => {
  // PDF importé (sans identifiant arXiv) : pas de graphe
  const withId = papers.filter((paper) => paper.arxivId);
  for (const paper of withId.slice(0, 3)) openRoute(`#/related/${encodeURIComponent(paper.arxivId)}`, true, paper === withId[0]);
});

// Pastille sur « Feed » : nouveaux articles d'auteurs suivis pas encore vus
export async function refreshAlerts() {
  let count = 0;
  try { count = (await api.alerts()).unseen.length; } catch { return; }
  const link = document.querySelector('.topnav a[href="#/feed"]');
  link.querySelectorAll(".nav-badge").forEach((badge) => badge.remove());
  if (count) {
    link.append(h("span", { class: "nav-badge", title: tn("alerts.badge", count), "aria-hidden": "true" }, String(count)),
      h("span", { class: "visually-hidden nav-badge" }, tn("alerts.badge", count)));
  }
}
addEventListener("arxivreader:alerts", refreshAlerts);

await loadSettings();
let rendering = `${settings.language}/${settings.mathRenderer}`;
onSettingsChange(() => {
  // Langue ou moteur de formules changé : la page est redessinée
  if (`${settings.language}/${settings.mathRenderer}` !== rendering) {
    rendering = `${settings.language}/${settings.mathRenderer}`;
    route();
    refreshAlerts();
  }
});
document.getElementById("settings-button").addEventListener("click", () => openSettingsDialog());
// Windows dans Edge (sans la fenêtre de l'application) : le « + » d'Edge ouvre sa page d'accueil ; celui-ci, arXivReader
if (WINDOWS && !nativeBridge) {
  const newTab = h("button", { class: "icon-button", type: "button", title: t("nav.newTab"), "aria-label": t("nav.newTab"),
    "data-i18n-title": "nav.newTab", html: icons.add, onclick: () => openRoute("#/", true, true) });  // suit la langue
  document.getElementById("settings-button").before(newTab);
}
addEventListener("arxivreader:settings", (event) => openSettingsDialog({ section: event.detail }));
addEventListener("hashchange", route);
// ⌘-clic ou clic du milieu sur un lien de l'application (article, renvoi) : dans un nouvel onglet
for (const type of ["click", "auxclick"]) {
  document.addEventListener(type, (event) => {
    const link = event.target.closest?.('a[href^="#/"]');
    if (!link || event.defaultPrevented || !wantsNewTab(event) || (type === "auxclick" && event.button !== 1)) return;
    event.preventDefault();
    openRoute(link.getAttribute("href"), true, event.shiftKey);
  });
}
// retour sur un onglet : réglages changés dans un autre onglet (thème, taille du texte…) appliqués sans
// recharger la page (langue ou moteur de formules : page redessinée, comme plus haut) ; la bibliothèque
// est redessinée (dossiers, articles ajoutés ailleurs)
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState !== "visible") return;
  await loadSettings();
  // bibliothèque : liste et dossiers relus sur place (sans redessiner la page : saisie, menus et import en cours restent)
  if (!location.hash || location.hash === "#/") dispatchEvent(new Event("arxivreader:library-refresh"));
  refreshAlerts();
});
route();
maybeOnboard(); // nouvelle installation : assistant de premier lancement
refreshAlerts();
setInterval(refreshAlerts, 10 * 60 * 1000);
watchZotero();
setInterval(watchZotero, 30 * 1000); // synchronisation automatique avec Zotero : bibliothèque affichée remise à jour
// nouvelle version sur GitHub : vérifiée au lancement puis toutes les 6 heures (le serveur n'interroge GitHub qu'une fois par jour)
refreshUpdates();
setInterval(refreshUpdates, 6 * 3600 * 1000);
