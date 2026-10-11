// Synchronisation avec Zotero : rubrique des réglages (état, dernière synchronisation, « Synchroniser
// maintenant »), et suivi des synchronisations automatiques (bibliothèque affichée remise à jour).

import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { h, toast } from "./util.js";

export const ZOTERO_KEY = /^[A-Za-z0-9]{16,64}$/;

// résumé d'une synchronisation : ce qui a changé de chaque côté
function summary(report) {
  if (!report) return "";
  const parts = [
    report.toZotero && tn("zotero.toZotero", report.toZotero), report.fromZotero && tn("zotero.fromZotero", report.fromZotero),
    report.removedZotero && tn("zotero.removedZotero", report.removedZotero), report.removedHere && tn("zotero.removedHere", report.removedHere),
    report.folders && tn("zotero.folders", report.folders), report.skipped && tn("zotero.skipped", report.skipped),
    report.duplicates && tn("zotero.duplicates", report.duplicates), report.unmapped && tn("zotero.unmapped", report.unmapped),
    report.importFailed && tn("zotero.importFailed", report.importFailed),
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : t("zotero.nothing");
}
const changedHere = (report) => Boolean(report && (report.fromZotero || report.removedHere || report.folders));
const held = (report) => Boolean(report?.held);
// cause d'un échec, dans la langue de la page (le message du serveur seulement pour une erreur imprévue)
export function zoteroError(code, message = "") {
  const key = `zotero.err.${code}`;
  return t(key) !== key ? t(key) : t("zotero.err.unexpected", { message });
}

// `key()` : clé du champ (peut-être pas encore enregistrée) ; `save(key)` : l'enregistre
export function zoteroSection({ key, save }) {
  const box = h("div", { class: "zotero-settings", "aria-live": "polite" });
  const when = (iso) => new Date(iso).toLocaleString(document.documentElement.lang || undefined, { dateStyle: "short", timeStyle: "short" });
  let current = null;
  let running = false;
  const draw = () => {
    const state = current;
    const parts = [];
    if (state?.lastSync) {
      parts.push(h("p", { class: "hint ai-ready" }, `✓ ${t(state.user ? "zotero.lastUser" : "zotero.last", { date: when(state.lastSync), user: state.user })}`));
      if (state.report) parts.push(h("p", { class: "hint" }, summary(state.report)));
      if (held(state.report)) parts.push(h("p", { class: "hint zotero-error" }, t("zotero.heldHint")));
    }
    if (state?.lastError) parts.push(h("p", { class: "hint zotero-error" }, t("zotero.error", { message: zoteroError(state.lastErrorCode, state.lastError) })));
    const busy = running || state?.running;
    parts.push(h("div", {}, h("button", { class: "button small-button zotero-sync", type: "button", disabled: busy || !ZOTERO_KEY.test(key()),
      onclick: () => run() }, busy ? t("zotero.running") : t("zotero.syncNow"))));
    box.replaceChildren(...parts);
  };
  // synchronisation automatique en cours : état relu jusqu'à ce qu'elle finisse
  let polling = 0;
  const poll = () => {
    clearTimeout(polling);
    if (!box.isConnected && current) return;
    api.zoteroStatus().then((state) => {
      current = state;
      draw();
      if (state.running) polling = setTimeout(poll, 1500);
    }, () => {});
  };
  const run = async () => {
    running = true;
    draw();
    try {
      const typed = key();
      await save(typed); // clé tapée puis bouton cliqué tout de suite : enregistrée d'abord
      current = await api.zoteroSync();
      toast(held(current.report) ? t("zotero.heldToast") : t("zotero.done"));
      if (changedHere(current.report)) dispatchEvent(new Event("arxivreader:library-refresh"));
    } catch (error) {
      if (error.code === "busy") toast(t("zotero.busy"));
      else toast(zoteroError(error.code, error.message), { error: true });
      current = await api.zoteroStatus().catch(() => current);
    }
    running = false;
    draw();
    lastSeen = current?.lastSync || lastSeen;
  };
  draw();
  poll();
  box.refresh = () => { draw(); setTimeout(poll, 6000); }; // clé changée : la synchronisation automatique suit
  box.redraw = draw; // clé en cours de saisie : bouton (dés)activé
  return box;
}

// Synchronisations automatiques (en arrière-plan) : bibliothèque affichée remise à jour si elles y ont changé
// quelque chose ; des retraits mis en attente sont signalés une fois
let lastSeen = null;
export async function watchZotero() {
  if (document.hidden) return;
  let state;
  try { state = await api.zoteroStatus(); } catch { return; }
  if (!state.connected || !state.lastSync) return;
  if (lastSeen !== null && state.lastSync !== lastSeen) {
    if (changedHere(state.report)) dispatchEvent(new Event("arxivreader:library-refresh"));
    if (held(state.report)) toast(t("zotero.heldToast"), { duration: 12000 });
  }
  lastSeen = state.lastSync;
}
