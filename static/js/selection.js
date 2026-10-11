// Sélection de plusieurs articles (bibliothèque, dossiers, feed, recherche) : on les glisse ensemble sur un
// dossier (aussi d'une fenêtre à l'autre), ou on les range avec « Ranger dans… » de la barre de sélection.

import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { h, icons, toast, toastError } from "./util.js";

const selected = new Map(); // identifiant arXiv → { arxivId, paperId, title }
const listeners = new Set();
let bar = null;
let barOptions = null;

const notify = () => { syncSelection(); listeners.forEach((listener) => listener()); drawBar(); };

// boutons et cartes affichés mis à jour (une carte gardée hors de la page l'est quand elle revient : syncSelection(carte))
export function syncSelection(root = document) {
  for (const button of root.querySelectorAll(".select-toggle[data-select]")) {
    const on = selected.has(button.dataset.select);
    button.setAttribute("aria-pressed", String(on));
    button.title = t(on ? "select.remove" : "select.add");
    button.setAttribute("aria-label", button.title);
    button.closest(".card, .feed-card")?.classList.toggle("selected", on);
  }
}

export const selectionSize = () => selected.size;
export const isSelected = (arxivId) => selected.has(arxivId);

// articles emportés par un glissement : toute la sélection si l'article saisi en fait partie, sinon lui seul
export const dragGroup = (info) => (selected.has(info.arxivId) ? [...selected.values()] : [info]);

export function clearSelection() {
  if (!selected.size) return;
  selected.clear();
  notify();
}

export function onSelectionChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// Bouton « sélectionner » d'une carte (avec les autres boutons de la carte) ; la carte est marquée sélectionnée
export function selectButton(info) {
  if (!info.arxivId) return null;
  const on = selected.has(info.arxivId);
  const button = h("button", { class: "icon-button select-toggle", type: "button", html: icons.check, dataset: { select: info.arxivId },
    "aria-pressed": String(on), title: t(on ? "select.remove" : "select.add"), "aria-label": t(on ? "select.remove" : "select.add") });
  button.addEventListener("click", (event) => {
    event.preventDefault();
    if (selected.has(info.arxivId)) selected.delete(info.arxivId);
    else selected.set(info.arxivId, { arxivId: info.arxivId, paperId: info.paperId || null, title: info.title || "" });
    notify();
  });
  return button;
}

// Ranger des articles dans un dossier : ceux qui ne sont pas encore dans la bibliothèque y sont importés
export async function fileInFolder(items, folder, libraryPapers = null) {
  const papers = libraryPapers || await api.papers();
  let added = 0;
  let already = 0;
  for (const item of items) {
    let paper = papers.find((p) => p.id === item.paperId) || papers.find((p) => p.arxiv_id === item.arxivId);
    if (!paper) {
      if (items.length === 1) toast(t("library.drop.importing"));
      paper = await api.importPaper(item.arxivId);
    }
    if (folder.papers.includes(paper.id)) { already++; continue; }
    await api.setInFolder(folder.id, paper.id, true);
    folder.papers.push(paper.id);
    added++;
    if (items.length === 1) item.title ||= paper.title;
  }
  if (items.length === 1 && added) {
    const title = items[0].title || "";
    toast(t("library.drop.added", { name: folder.name, title: title.length > 60 ? `${title.slice(0, 58)}…` : title }));
  } else if (added) {
    toast(t("select.filed", { n: added, name: folder.name }) + (already ? ` ${tn("select.already", already)}` : ""));
  } else {
    toast(t("library.drop.already", { name: folder.name }));
  }
  return added;
}

// Barre de sélection, en bas de la fenêtre : nombre, « Ranger dans… » (liste des dossiers), « Tout désélectionner »
// `options` : { papers() : articles de la bibliothèque déjà connus (ou null), onFiled() : après un rangement }
export function useSelectionBar(options) {
  barOptions = options;
  drawBar();
  return () => {
    if (barOptions === options) { barOptions = null; clearSelection(); bar?.remove(); bar = null; }
  };
}

function drawBar() {
  if (!barOptions || !selected.size) { bar?.remove(); bar = null; return; }
  bar ||= h("div", { class: "selection-bar", role: "region", "aria-label": t("select.bar") });
  if (!bar.isConnected) document.body.append(bar);
  const file = h("button", { class: "button small-button", type: "button", "aria-haspopup": "menu" },
    h("span", { class: "button-icon", html: icons.folder }), t("select.file"));
  file.addEventListener("click", () => openFolders(file));
  bar.replaceChildren(
    h("span", { class: "selection-count" }, tn("select.count", selected.size)),
    h("span", { class: "muted small selection-hint" }, t("select.hint")),
    h("span", { class: "spacer" }),
    file,
    h("button", { class: "button small-button", type: "button", onclick: clearSelection }, t("select.clear")));
}

async function openFolders(button) {
  bar.querySelector(".selection-menu")?.remove();
  let folders;
  try { folders = await api.folders(); } catch (error) { toastError(error); return; }
  const menu = h("div", { class: "selection-menu", role: "menu" },
    folders.length ? folders.map((folder) => h("button", { class: "selection-menu-item", type: "button", role: "menuitem",
      onclick: async () => {
        menu.remove();
        try {
          await fileInFolder([...selected.values()], folder, barOptions?.papers?.() || null);
          clearSelection();
          barOptions?.onFiled?.();
        } catch (error) { toastError(error); }
      } }, h("span", { class: "button-icon", html: icons.folder }), folder.name))
      : h("p", { class: "muted small" }, t("select.noFolder")));
  bar.append(menu);
  // juste au-dessus du bouton « Ranger dans… », aligné sur lui (sans sortir de la barre à droite)
  const left = Math.max(0, Math.min(button.offsetLeft, bar.clientWidth - menu.offsetWidth));
  menu.style.left = `${left}px`;
  const close = (event) => {
    if (menu.contains(event.target) || event.target === button) return;
    menu.remove();
    document.removeEventListener("pointerdown", close);
  };
  document.addEventListener("pointerdown", close);
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { menu.remove(); button.focus(); } });
  menu.querySelector("button")?.focus();
}
