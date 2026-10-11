// Sélection de plusieurs articles (bibliothèque, dossiers, feed, recherche) : on les glisse ensemble sur un
// dossier (aussi d'une fenêtre à l'autre), ou on les range avec « Ranger dans… » de la barre de sélection.

import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { h, icons, toast, toastError } from "./util.js";

const selected = new Map(); // identifiant arXiv → { arxivId, paperId, title }
const listeners = new Set();
let bar = null;
const barStack = []; // pages qui affichent la barre (la dernière l'emporte ; une page quittée en retard ne l'éteint pas)
let filing = false;  // rangement en cours : la barre ne relance rien
let draggingSelection = false; // glissement en cours de toute la sélection (effacée s'il aboutit)
const barOptions = () => barStack.at(-1) || null;

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
export const dragGroup = (info) => {
  draggingSelection = selected.has(info.arxivId);
  return draggingSelection ? [...selected.values()] : [info];
};
// fin du glissement (dans cette fenêtre ou une autre) : sélection déposée → effacée
export function dragEnded(event) {
  if (draggingSelection && event.dataTransfer?.dropEffect && event.dataTransfer.dropEffect !== "none") clearSelection();
  draggingSelection = false;
}

// sélection glissée puis déposée dans cette même fenêtre : effacée sans attendre la fin du glissement (selon
// les cas, WebKit n'y indique pas que le dépôt a réussi)
export function selectionDropped() {
  if (!draggingSelection) return;
  draggingSelection = false;
  clearSelection();
}

// articles sélectionnés qui ne sont plus disponibles (retirés de la bibliothèque) : oubliés
export function forgetSelected(keep) {
  let changed = false;
  for (const [id, info] of selected) if (!keep(info)) { selected.delete(id); changed = true; }
  if (changed) notify();
}

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
  items = items.filter((item, i) => items.findIndex((other) => other.arxivId === item.arxivId) === i); // sans doublons
  const missing = items.filter((item) => !papers.some((p) => p.id === item.paperId || p.arxiv_id === item.arxivId)).length;
  let added = 0;
  let already = 0;
  let imported = 0;
  try {
    for (const item of items) {
      let paper = papers.find((p) => p.id === item.paperId) || papers.find((p) => p.arxiv_id === item.arxivId);
      if (!paper) {
        imported++;
        toast(missing > 1 ? t("select.importing", { k: imported, n: missing }) : t("library.drop.importing"), { duration: 60000 });
        paper = await api.importPaper(item.arxivId);
        papers.push(paper); // connu désormais (un second rangement ne le réimporte pas)
      }
      if (folder.papers.includes(paper.id)) { already++; continue; }
      await api.setInFolder(folder.id, paper.id, true);
      folder.papers.push(paper.id);
      added++;
      if (items.length === 1) item.title ||= paper.title;
    }
  } catch (error) {
    // rangement interrompu : dire ce qui a déjà été fait
    if (added) error.message = `${error.message} ${t("select.partial", { n: added, total: items.length })}`;
    throw error;
  }
  if (items.length === 1 && added) {
    const title = items[0].title || "";
    toast(t("library.drop.added", { name: folder.name, title: title.length > 60 ? `${title.slice(0, 58)}…` : title }));
  } else if (added) {
    toast(tn("select.filed", added, { name: folder.name }) + (already ? ` ${tn("select.already", already)}` : ""));
  } else {
    toast(t("library.drop.already", { name: folder.name }));
  }
  return added;
}

// Ajouter des articles à la bibliothèque (hors dossier) : ceux qui n'y sont pas encore y sont importés
// (déposés sur « Autres articles »). Renvoie le nombre d'articles ajoutés.
export async function addToLibrary(items, libraryPapers) {
  items = items.filter((item, i) => items.findIndex((other) => other.arxivId === item.arxivId) === i);
  const missing = items.filter((item) => !libraryPapers.some((p) => p.id === item.paperId || p.arxiv_id === item.arxivId));
  let added = 0;
  try {
    for (const item of missing) {
      toast(missing.length > 1 ? t("select.importing", { k: added + 1, n: missing.length }) : t("library.drop.importing"), { duration: 60000 });
      const paper = await api.importPaper(item.arxivId);
      libraryPapers.push(paper);
      item.title ||= paper.title;
      added++;
    }
  } catch (error) {
    if (added) error.message = `${error.message} ${t("select.partialLibrary", { n: added, total: missing.length })}`;
    throw error;
  }
  if (added === 1) toast(t("library.drop.addedLibrary", { title: missing[0].title.length > 60 ? `${missing[0].title.slice(0, 58)}…` : missing[0].title }));
  else if (added) toast(tn("select.addedLibrary", added));
  return added;
}

// Barre de sélection, en bas de la fenêtre : nombre, « Ranger dans… » (liste des dossiers), « Tout désélectionner »
// `options` : { papers() : articles de la bibliothèque déjà connus (ou null), onFiled() : après un rangement }
export function useSelectionBar(options) {
  barStack.push(options);
  drawBar();
  return () => {
    const index = barStack.indexOf(options);
    if (index < 0) return;
    const current = index === barStack.length - 1;
    barStack.splice(index, 1);
    if (current) clearSelection(); // la page qui l'affichait est quittée : sélection effacée
    drawBar();
  };
}

function drawBar() {
  document.body.classList.toggle("has-selection-bar", Boolean(barOptions() && selected.size));
  if (!barOptions() || !selected.size) { bar?.remove(); bar = null; return; }
  bar ||= h("div", { class: "selection-bar", role: "region", "aria-label": t("select.bar") });
  if (!bar.isConnected) document.body.append(bar);
  const file = h("button", { class: "button small-button", type: "button", "aria-haspopup": "menu", disabled: filing },
    h("span", { class: "button-icon", html: icons.folder }), t("select.file"));
  file.addEventListener("click", () => openFolders(file));
  bar.replaceChildren(
    h("span", { class: "selection-count" }, tn("select.count", selected.size)),
    h("span", { class: "muted small selection-hint" }, tn("select.hint", selected.size)),
    h("span", { class: "spacer" }),
    file,
    h("button", { class: "button small-button", type: "button", disabled: filing, onclick: clearSelection }, t("select.clear")));
}

let opening = false;
async function openFolders(button) {
  if (opening || filing) return; // double clic : un seul menu
  bar?.querySelector(".selection-menu")?.remove();
  opening = true;
  let folders;
  try { folders = await api.folders(); } catch (error) { toastError(error); return; } finally { opening = false; }
  if (!bar || !button.isConnected) return; // barre disparue pendant la demande
  const menu = h("div", { class: "selection-menu", role: "menu" },
    folders.length ? folders.map((folder) => h("button", { class: "selection-menu-item", type: "button", role: "menuitem",
      onclick: async () => {
        menu.remove();
        document.removeEventListener("pointerdown", close);
        const options = barOptions();
        filing = true;
        drawBar();
        try {
          await fileInFolder([...selected.values()], folder, options?.papers?.() || null);
          clearSelection();
        } catch (error) {
          toastError(error);
        } finally {
          filing = false;
          drawBar();
          options?.onFiled?.(); // même interrompu : la page montre ce qui a été rangé
        }
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
  // flèches haut/bas entre les dossiers ; les touches ne vont pas plus loin (← → changent de jour dans le feed)
  menu.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Escape") { menu.remove(); document.removeEventListener("pointerdown", close); button.focus(); return; }
    const items = [...menu.querySelectorAll("button")];
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (!step || !items.length) return;
    event.preventDefault();
    items[(items.indexOf(document.activeElement) + step + items.length) % items.length].focus();
  });
  menu.querySelector("button")?.focus();
}
