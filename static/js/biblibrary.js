// Références d'un article vers la bibliothèque : « + Bibliothèque » sur une entrée, et « tout ajouter à un dossier »
// (liste de lecture d'un survol). Un article à la fois (arXiv limite le débit), avec progression et annulation.

import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { h, toast, toastError } from "./util.js";

// Bouton d'une entrée : ajoute l'article à la bibliothèque (son import se poursuit en arrière-plan), puis devient une marque
export function addButton(arxivId) {
  const button = h("button", { class: "bib-add", type: "button", title: t("bibadd.title") }, `+ ${t("bibadd.add")}`);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await api.quickAdd(arxivId);
      button.replaceWith(h("span", { class: "bib-added" }, `✓ ${t("bibadd.in")}`));
    } catch (error) {
      button.disabled = false;
      toastError(error);
    }
  });
  return button;
}

// Choix du dossier (existant ou nouveau) après confirmation du nombre d'articles : {folder: id|null, name} ou null
function askFolder(total, fresh, folders) {
  return new Promise((resolve) => {
    const select = h("select", { "aria-label": t("bibadd.folder") },
      h("option", { value: "" }, t("library.folder.newName")),
      folders.map((f) => h("option", { value: String(f.id) }, f.name)));
    const name = h("input", { type: "text", placeholder: t("library.folder.name"), "aria-label": t("library.folder.name") });
    select.addEventListener("change", () => { name.hidden = Boolean(select.value); });
    const dialog = h("dialog", { class: "meta-dialog" },
      h("form", { method: "dialog" },
        h("h3", { class: "meta-heading" }, tn("bibadd.allTitle", total)),
        h("p", { class: "muted small meta-note" }, t("bibadd.confirm", { n: total, k: fresh })),
        h("label", {}, t("bibadd.folder"), select), name,
        h("div", { class: "dialog-actions" },
          h("button", { class: "button", type: "button", onclick: () => dialog.close("cancel") }, t("library.folder.cancel")),
          h("button", { class: "button primary", value: "ok" }, t("bibadd.start")))));
    dialog.addEventListener("close", () => {
      dialog.remove();
      const typed = name.value.trim();
      resolve(dialog.returnValue !== "ok" || (!select.value && !typed) ? null : { folder: select.value ? Number(select.value) : null, name: typed });
    });
    document.body.append(dialog);
    dialog.showModal();
    name.focus();
  });
}

// Boîte de progression : renvoie {update(k, n), done, cancelled()} ; Échap ou « Arrêter » interrompt après l'article en cours
function progressDialog(onStop) {
  const text = h("p", { class: "meta-note", "aria-live": "polite" });
  const dialog = h("dialog", { class: "meta-dialog" },
    h("form", { method: "dialog" }, text,
      h("div", { class: "dialog-actions" }, h("button", { class: "button", type: "button", onclick: onStop }, t("bibadd.stop")))));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); onStop(); });
  document.body.append(dialog);
  dialog.showModal();
  return { update: (k, n) => { text.textContent = t("bibadd.progress", { k, n }); }, close: () => dialog.remove() };
}

// `ids` : identifiants arXiv des références (sans doublon) ; `alive()` : la page est toujours ouverte
export async function addAllToFolder(ids, alive = () => true) {
  let papers, folders;
  try { [papers, folders] = await Promise.all([api.papers(), api.folders()]); } catch (error) { toastError(error); return; }
  const known = new Set(papers.map((p) => p.arxiv_id));
  const choice = await askFolder(ids.length, ids.filter((id) => !known.has(id)).length, folders);
  if (!choice) return;
  let folder = folders.find((f) => f.id === choice.folder);
  try {
    folder ||= await api.createFolder(choice.name);
  } catch (error) { toastError(error); return; }
  let stopped = false;
  const progress = progressDialog(() => { stopped = true; });
  let done = 0;
  let failed = 0;
  let streak = 0; // trois échecs de suite (réseau coupé, limite d'arXiv) : on s'arrête
  let lastError = null;
  for (const id of ids) {
    if (stopped || !alive() || streak >= 3) break;
    progress.update(done + failed + 1, ids.length);
    try {
      const paper = await api.quickAdd(id);
      await api.setInFolder(folder.id, paper.id, true);
      done++;
      streak = 0;
    } catch (error) { // une référence introuvable ne bloque pas les autres
      failed++;
      streak++;
      lastError = error;
    }
  }
  progress.close();
  if (streak >= 3) toastError(lastError);
  else toast(tn(stopped ? "bibadd.stopped" : "bibadd.done", done, { name: folder.name, total: ids.length }) + (failed ? ` ${tn("bibadd.failed", failed)}` : ""));
}

// Bouton de la bibliographie : sous son titre ; classe « bib-links » : ajout de l'application
// (hors sélection, retiré des aperçus)
export function allButton(ids, alive) {
  return h("div", { class: "bib-links bib-all" },
    h("button", { class: "bib-add", type: "button", title: t("bibadd.allHint"), onclick: () => addAllToFolder(ids, alive) },
      tn("bibadd.all", ids.length)));
}
