// Réglages : synchronisation avec Zotero (état, dernière synchronisation, « Synchroniser maintenant »)

import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { h, toast } from "./util.js";

// résumé d'une synchronisation : ce qui a changé de chaque côté
function summary(report) {
  if (!report) return "";
  const parts = [
    report.toZotero && tn("zotero.toZotero", report.toZotero), report.fromZotero && tn("zotero.fromZotero", report.fromZotero),
    report.removedZotero && tn("zotero.removedZotero", report.removedZotero), report.removedHere && tn("zotero.removedHere", report.removedHere),
    report.folders && tn("zotero.folders", report.folders), report.skipped && tn("zotero.skipped", report.skipped),
    report.held && tn("zotero.held", report.held),
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : t("zotero.nothing");
}

export function zoteroSection(hasKey) {
  const box = h("div", { class: "zotero-settings", "aria-live": "polite" });
  const when = (iso) => new Date(iso).toLocaleString(document.documentElement.lang || undefined, { dateStyle: "short", timeStyle: "short" });
  const draw = (state, running = false) => {
    const parts = [];
    if (state?.lastSync) {
      parts.push(h("p", { class: "hint ai-ready" }, `✓ ${t("zotero.last", { date: when(state.lastSync), user: state.user || "" })}`));
      if (state.report) parts.push(h("p", { class: "hint" }, summary(state.report)));
    }
    if (state?.lastError) parts.push(h("p", { class: "hint zotero-error" }, t("zotero.error", { message: state.lastError })));
    parts.push(h("div", {}, h("button", { class: "button small-button", type: "button", disabled: running || !hasKey(),
      onclick: () => run() }, running ? t("zotero.running") : t("zotero.syncNow"))));
    box.replaceChildren(...parts);
  };
  let current = null;
  const run = async () => {
    draw(current, true);
    try {
      current = await api.zoteroSync();
      toast(t("zotero.done"));
      dispatchEvent(new Event("arxivreader:library-refresh")); // bibliothèque affichée : mise à jour
    } catch (error) {
      current = await api.zoteroStatus().catch(() => current);
      if (error.code === "busy") toast(t("zotero.busy"));
    }
    draw(current);
  };
  draw(null);
  api.zoteroStatus().then((state) => { current = state; draw(state); }, () => {});
  box.refresh = () => draw(current); // clé saisie ou effacée : bouton (dés)activé
  return box;
}
