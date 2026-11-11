// Références d'un article vers la bibliothèque : « + Bibliothèque » sur une entrée, et « tout ajouter à la bibliothèque »
// (liste de lecture d'un survol), chacun avec le choix d'un dossier (askDestination, aussi pour le signet du lecteur).
// Un article à la fois (arXiv limite le débit), avec progression et annulation.

import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { h, toast, toastError } from "./util.js";

// Bouton d'une entrée : demande où ranger l'article (bibliothèque seule, ou aussi un dossier), l'ajoute (son import se
// poursuit en arrière-plan), puis devient une marque
export function addButton(arxivId) {
  const button = h("button", { class: "bib-add", type: "button", title: t("bibadd.title") }, `+ ${t("bibadd.add")}`);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const choice = await askDestination({ title: t("dest.title") });
      if (!choice) { button.disabled = false; return; }
      const folder = await destinationFolder(choice);
      const paper = await api.quickAdd(arxivId);
      if (folder) await api.setInFolder(folder.id, paper.id, true);
      button.replaceWith(h("span", { class: "bib-added" }, `✓ ${folder ? t("bibadd.inFolder", { name: folder.name }) : t("bibadd.in")}`));
    } catch (error) {
      button.disabled = false;
      toastError(error);
    }
  });
  return button;
}

// Où ranger un ou plusieurs articles, dans un seul menu : la bibliothèque seule (choix par défaut), un dossier existant
// (l'article est aussi dans la bibliothèque), ou un nouveau dossier, dont le nom se tape dans un champ qui apparaît
// alors. Renvoie null (annulé), {folder: null} (bibliothèque seule), {folder: dossier} ou {name: nouveau nom}
export async function askDestination({ title, note = null }) {
  let folders = [];
  try { folders = await api.folders(); } catch (error) { toastError(error); return null; }
  return new Promise((resolve) => {
    const select = h("select", { "aria-label": t("dest.where") },
      h("option", { value: "library" }, t("dest.library")),
      folders.length ? h("optgroup", { label: t("dest.folders") }, folders.map((f) => h("option", { value: String(f.id) }, f.name))) : null,
      h("option", { value: "new" }, t("dest.new")));
    const name = h("input", { type: "text", placeholder: t("library.folder.name"), "aria-label": t("library.folder.name"), hidden: true });
    const ok = h("button", { class: "button primary", value: "ok" }, t("bibadd.start"));
    const sync = () => {
      name.hidden = select.value !== "new";
      ok.disabled = select.value === "new" && !name.value.trim();
    };
    select.addEventListener("change", () => { sync(); (name.hidden ? select : name).focus(); });
    name.addEventListener("input", sync);
    const dialog = h("dialog", { class: "meta-dialog dest-dialog" },
      h("form", { method: "dialog" },
        h("h3", { class: "meta-heading" }, title),
        note ? h("p", { class: "muted small meta-note" }, note) : null,
        h("label", {}, t("dest.where"), select), name,
        h("div", { class: "dialog-actions" },
          h("button", { class: "button", type: "button", onclick: () => dialog.close("cancel") }, t("library.folder.cancel")), ok)));
    dialog.addEventListener("close", () => {
      dialog.remove();
      const value = select.value;
      if (dialog.returnValue !== "ok") resolve(null);
      else if (value === "new") resolve(name.value.trim() ? { name: name.value.trim() } : null);
      else if (value === "library") resolve({ folder: null });
      else resolve({ folder: folders.find((f) => String(f.id) === value) || null });
    });
    document.body.append(dialog);
    dialog.showModal();
    select.focus();
  });
}

// Dossier choisi dans askDestination : celui existant, ou créé maintenant ; null pour la bibliothèque seule
export async function destinationFolder(choice) {
  if (choice.name) return api.createFolder(choice.name);
  return choice.folder || null;
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
export async function addAllToLibrary(ids, alive = () => true) {
  let papers;
  try { papers = await api.papers(); } catch (error) { toastError(error); return; }
  const known = new Set(papers.map((p) => p.arxiv_id));
  const choice = await askDestination({ title: tn("bibadd.allTitle", ids.length),
    note: t("bibadd.confirm", { n: ids.length, k: ids.filter((id) => !known.has(id)).length }) });
  if (!choice) return;
  let folder;
  try { folder = await destinationFolder(choice); } catch (error) { toastError(error); return; }
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
      if (folder) await api.setInFolder(folder.id, paper.id, true);
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
  else {
    const key = `bibadd.${stopped ? "stopped" : "done"}${folder ? "" : "Library"}`;
    toast(tn(key, done, { name: folder?.name, total: ids.length }) + (failed ? ` ${tn("bibadd.failed", failed)}` : ""));
  }
}

// Bouton de la bibliographie : sous son titre ; classe « bib-links » : ajout de l'application
// (hors sélection, retiré des aperçus)
export function allButton(ids, alive) {
  return h("div", { class: "bib-links bib-all" },
    h("button", { class: "bib-add", type: "button", title: t("bibadd.allHint"), onclick: () => addAllToLibrary(ids, alive) },
      tn("bibadd.all", ids.length)));
}
