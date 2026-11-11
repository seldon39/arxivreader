// Menus de l'application Mac. Le menu envoie ses commandes à la page (évènement « app:command », nom de la commande) ;
// la page lui envoie son état (message « menuState ») pour griser ce qui ne s'applique pas et changer les libellés
// (zoom dans le PDF, sommaire affiché ou masqué, preuves repliées…). Le lecteur s'inscrit (setMenuReader) et fait les
// commandes qui le concernent (Reader.menuCommand) ; les autres sont faites ici. Dans un navigateur : rien n'est envoyé.

import { api } from "./api.js";
import { saveBibtex } from "./bibtex.js";
import { t, tn } from "./i18n.js";
import { h, toast, toastError } from "./util.js";

const mac = () => window.webkit?.messageHandlers?.arxivreader || null;
const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

let reader = null;       // lecteur affiché (null : autre page)
let readerState = null;  // son dernier état connu

export function setMenuReader(value) { reader = value; }
// lecteur quitté : oublié, sauf si le suivant s'est déjà inscrit
export function releaseMenuReader(value) {
  if (reader !== value) return;
  reader = null;
  reportMenu(null);
}

// page de l'application, d'après l'adresse (comme le routeur d'app.js)
function pageOf(hash) {
  if (/^#\/paper\//.test(hash)) return "paper";
  if (/^#\/feed/.test(hash)) return "feed";
  if (/^#\/search/.test(hash)) return "search";
  if (/^#\/related/.test(hash)) return "related";
  return "library";
}

// État du lecteur (null : plus de lecteur) ; envoyé tout de suite au menu
export function reportMenu(state) {
  readerState = state;
  send();
}

function send() {
  const bridge = mac();
  if (!bridge) return;
  try { bridge.postMessage({ action: "menuState", page: pageOf(location.hash), ...(readerState || { reader: false }) }); } catch { /* page qui se ferme */ }
}

addEventListener("hashchange", () => {
  if (pageOf(location.hash) !== "paper") readerState = null;  // le lecteur suivant enverra le sien
  send();
});
send();

// Texte copié par l'application (le presse-papiers de WebKit refuse une écriture qui ne suit pas un clic dans la page)
export function copyFromMenu(text) {
  const bridge = mac();
  if (bridge) bridge.postMessage({ action: "copyText", text });
  else return navigator.clipboard.writeText(text);
  return Promise.resolve();
}

// La bibliothèque, puis l'élément voulu une fois la page affichée (au plus 5 s)
async function onLibrary(selector) {
  if (pageOf(location.hash) !== "library") location.hash = "#/";
  for (let i = 0; i < 50; i++) {
    const element = document.querySelector(selector);
    if (element && pageOf(location.hash) === "library") return element;
    await wait(100);
  }
  return null;
}

const PAGES = { "go.library": "#/", "go.feed": "#/feed", "go.search": "#/search", "go.related": "#/related" };

async function command(name) {
  if (PAGES[name]) { if (location.hash !== PAGES[name]) location.hash = PAGES[name]; return; }
  if (name === "help.keys") { showKeys(); return; }
  if (name === "import.arxiv") {
    const input = await onLibrary('.import-box input[name="query"]');
    input?.focus();
    input?.select();
    return;
  }
  if (name === "import.file") { (await onLibrary(".import-box .import-file"))?.click(); return; }
  if (name === "edit.searchLibrary") {
    const search = await onLibrary(".library-search");
    search?.focus();
    search?.select();
    return;
  }
  if (name === "export.bib") { saveBibtex(() => api.libraryBibtex(), "/api/library.bib"); return; }
  if (reader && !reader.destroyed) reader.menuCommand(name);
}

document.addEventListener("app:command", (event) => {
  if (typeof event.detail !== "string") return;
  command(event.detail).catch(toastError);
});

// Raccourcis : ceux du lecteur, et dans l'application Mac ceux des menus ; renvoie la fenêtre (le lecteur la ferme
// s'il est quitté pendant qu'elle est ouverte)
export function showKeys() {
  document.querySelector("dialog.keys-dialog")?.close();
  const opener = document.activeElement;
  const close = h("button", { class: "button", type: "button" }, t("graph.close"));
  const row = (keys, text) => h("tr", {}, h("th", { scope: "row" }, h("kbd", {}, keys)), h("td", {}, t(text)));
  const app = mac() ? [
    h("tr", {}, h("th", { colspan: 2, class: "keys-group" }, t("keys.app"))),
    row("⌘F", "keys.find"), row("⌥⌘F", "keys.searchLibrary"), row("⌘D", "keys.bookmark"), row("⌥⌘P", "keys.view"),
    row("⌃⌘S", "keys.toc"), row("⌥⌘1 … ⌥⌘5", "keys.tabs"), row("⌥⌘0", "keys.panel"), row("⌃⌘1 … ⌃⌘4", "keys.pages"),
    row("⌘O", "keys.import"),
  ] : [];
  const dialog = h("dialog", { class: "keys-dialog", "aria-label": t("reader.keys") },
    h("h3", {}, t("reader.keys")),
    h("table", {}, h("tbody", {},
      row("⌥←", "reader.keyBack"), row("⌥→", "reader.keyForward"),
      row("j  ·  ]", "reader.keyNext"), row("k  ·  [", "reader.keyPrev"), row("p", "reader.keyProof"), row("?", "reader.keyHelp"),
      ...app)),
    close);
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => { dialog.remove(); opener?.focus?.({ preventScroll: true }); });
  document.body.append(dialog);
  dialog.showModal();
  close.focus();
  return dialog;
}

// BibTeX de l'article lu, copié depuis le menu
export async function copyPaperBibtex(paperId) {
  try {
    const data = await api.paperBibtex(paperId);
    await copyFromMenu(data.bibtex);
    toast(tn("bibtex.copied", data.count || 1));
  } catch (error) {
    toastError(error);
  }
}
