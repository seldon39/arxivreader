// Fichiers TeX de l'ordinateur (article ou livre en cours d'écriture) : fenêtre d'import (étapes de la compilation,
// .bib introuvable à choisir), vérifications en tête de l'onglet Énoncés du lecteur.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { chooseFiles, h, toastError } from "./util.js";

const NEEDS_PROOF = new Set(["theorem", "proposition", "lemma", "corollary", "claim"]);
// chemin collé (navigateur) : sans les guillemets de « Copier en tant que chemin » (Windows)
export const unquote = (text) => (text || "").trim().replace(/^"(.*)"$/, "$1").trim();
const fileName = (path) => (path || "").split(/[\\/]/).pop();
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");
const day = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "");

// .bib choisi dans la fenêtre de l'application (dans un navigateur : chemin tapé)
async function pickBib(name) {
  const paths = await chooseFiles("bib");
  if (paths === null) return unquote(prompt(t("tex.bibPrompt", { name }))) || null;
  return paths[0] || null;
}

// Étapes d'une compilation (fenêtre d'import) : [{state: ok | warn | error, text, detail, action}]
function texSteps(report, { onChooseBib = null } = {}) {
  const steps = [];
  const included = Math.max(0, report.files - 1);
  steps.push({ state: "ok", text: t("tex.sourceRead", { main: fileName(report.main), files: included, figures: report.figures }) });
  for (const bib of report.bibs) {
    steps.push({ state: "ok", text: t("tex.bibFound", { name: bib.name, entries: bib.entries, cited: report.cited }), detail: bib.path, mono: true });
  }
  for (const name of report.missingBibs) {
    steps.push({ state: "warn", text: t("tex.bibMissing", { name }), detail: t("tex.bibMissingHint", { cited: report.cited }),
      action: onChooseBib && { label: t("tex.chooseBib"), run: () => onChooseBib(name) } });
  }
  const undefinedCount = report.undefinedRefs.length + report.undefinedCites.length;
  if (!report.pdf) {
    const error = report.errors[0];
    steps.push({ state: "error", text: report.pdfFrom === "none" && !error ? t("tex.noTex") : t("tex.noPdf"),
      detail: error ? `${error.file}:${error.line} — ${error.message}` : null });
  } else if (report.pdfFrom === "existing") {
    steps.push({ state: "warn", text: t("tex.pdfExisting", { pages: report.pages }),
      detail: t("tex.pdfExistingHint", { date: day(report.pdfDate) }) });
  } else {
    const key = report.pdfFrom === "chosen" ? "tex.pdfChosen" : "tex.pdfCompiled";
    steps.push({ state: undefinedCount ? "warn" : "ok", text: t(key, { tool: report.pdfFrom, pages: report.pages }),
      detail: undefinedCount ? t("tex.undefinedCount", { n: undefinedCount }) : null });
    if (report.errors.length) {
      const error = report.errors[0];
      steps.push({ state: "warn", text: t("tex.latexErrors", { n: report.errors.length }), detail: `${error.file}:${error.line} — ${error.message}` });
    }
  }
  if (report.pdf) {
    steps.push({ state: "ok", text: t(report.book ? "tex.statementsBook" : "tex.statements", { n: report.statements, k: report.withoutProof }) });
  }
  return steps;
}

function stepElement(step) {
  const mark = { ok: "✓", warn: "!", error: "×" }[step.state];
  return h("div", { class: `tex-step ${step.state}` },
    h("span", { class: `tex-state ${step.state}`, "aria-hidden": "true" }, mark),
    h("div", {},
      h("div", { class: "tex-step-text" }, step.text),
      step.detail && h("div", { class: `muted small${step.mono ? " tex-path" : ""}` }, ...[step.detail].flat()),
      step.action && h("button", { class: "button small-button tex-step-action", type: "button", onclick: step.action.run }, step.action.label)));
}

// Import d'un fichier TeX : fenêtre qui montre la compilation puis son bilan ; renvoie l'article (ou null)
export function importTexFile(path, pdf = null) {
  return new Promise((resolve) => {
    let paper = null;
    const body = h("div", { class: "tex-steps" }, h("div", { class: "tex-step" }, h("span", { class: "spinner" }), t("tex.compiling")));
    const actions = h("div", { class: "dialog-actions" });
    const dialog = h("dialog", { class: "meta-dialog tex-dialog" },
      h("form", { method: "dialog" },
        h("h3", { class: "meta-heading" }, t("tex.importTitle")),
        h("p", { class: "muted small meta-note tex-path" }, path),
        body, actions));
    const show = (result) => {
      paper = result.paper || paper;
      const report = result.report;
      body.replaceChildren(...texSteps(report, { onChooseBib: paper && chooseBib }).map(stepElement));
      actions.replaceChildren(paper
        ? h("button", { class: "button primary", value: "open" }, t(report.missingBibs.length ? "tex.openWithoutBib" : "tex.open"))
        : h("button", { class: "button", value: "close" }, t("tex.close")));
    };
    const run = async (work) => {
      body.replaceChildren(h("div", { class: "tex-step" }, h("span", { class: "spinner" }), t("tex.compiling")));
      actions.replaceChildren();
      try { show(await work()); } catch (error) {
        body.replaceChildren(stepElement({ state: "error", text: t("tex.failed"), detail: error.message }));
        actions.replaceChildren(h("button", { class: "button", value: "close" }, t("tex.close")));
      }
    };
    async function chooseBib(name) {
      const chosen = await pickBib(name);
      if (chosen) run(() => api.chooseBib(paper.id, name, chosen));
    }
    dialog.addEventListener("cancel", (event) => { if (!actions.childElementCount) event.preventDefault(); });  // pendant la compilation
    dialog.addEventListener("close", () => { dialog.remove(); resolve(dialog.returnValue === "open" ? paper : null); });
    document.body.append(dialog);
    dialog.showModal();
    run(() => api.importTex(path, pdf));
  });
}

// Vérifications en tête de l'onglet Énoncés : compilation, bibliographie, renvois, preuves, lemmes inutilisés.
// `show(ids)` : n'afficher que ces énoncés ; `onChooseBib(name)` ; `hasProof(node)`
export function texChecks(report, nodes, { show, onChooseBib, hasProof }) {
  if (!report) return null;
  const lines = [];
  const line = (state, text, action) => lines.push(h("div", { class: `tex-check ${state}` }, h("span", { class: "tex-dot", "aria-hidden": "true" }, "●"),
    h("span", {}, text), action && h("button", { class: "link-button", type: "button", onclick: action.run }, action.label)));
  for (const name of report.missingBibs) line("warn", t("tex.bibMissing", { name }), { label: t("tex.chooseBib"), run: () => onChooseBib(name) });
  if (!report.pdf) line("error", t("tex.lastFailed"));
  const error = report.errors[0];
  if (error) line(report.pdf ? "warn" : "error", `${error.file}:${error.line} — ${error.message}`);
  if (report.undefinedRefs.length) line("error", t("tex.undefinedRefs", { n: report.undefinedRefs.length, keys: report.undefinedRefs.slice(0, 3).join(", ") }));
  if (report.undefinedCites.length && !report.missingBibs.length) {
    line("warn", t("tex.undefinedCites", { n: report.undefinedCites.length, keys: report.undefinedCites.slice(0, 3).join(", ") }));
  }
  // comme le panneau : ni preuve, ni justification dans le texte, ni citation, ni choix à la main
  const unproved = nodes.filter((n) => NEEDS_PROOF.has(n.kind) && !hasProof(n) && !n.cited?.length && !n.justifiedBy && !n.manual);
  if (unproved.length) line("warn", t("tex.withoutProof", { n: unproved.length }), { label: t("tex.show"), run: () => show(unproved.map((n) => n.id)) });
  const unused = nodes.filter((n) => (n.kind === "lemma" || n.kind === "claim") && !n.usedBy.length);
  if (unused.length) {
    const text = unused.length === 1 ? t("tex.unusedOne", { label: unused[0].label }) : t("tex.unused", { n: unused.length });
    line("warn", text, { label: t("tex.show"), run: () => show(unused.map((n) => n.id)) });
  }
  const when = report.pdfFrom === "existing" ? t("tex.pdfFrom", { date: day(report.pdfDate) }) : t("tex.compiledAt", { time: clock(report.compiledAt) });
  return h("div", { class: "tex-checks" },
    h("div", { class: "tex-checks-head muted small", title: report.main }, `${fileName(report.main)} · ${when}`),
    lines.length ? lines : h("div", { class: "tex-check ok" }, h("span", { class: "tex-dot", "aria-hidden": "true" }, "●"), t("tex.allGood")));
}

// .bib choisi depuis le lecteur : recompilation, puis `done(result)`
export async function chooseBibFor(paperId, name, done) {
  const chosen = await pickBib(name);
  if (!chosen) return;
  try { done(await api.chooseBib(paperId, name, chosen)); } catch (error) { toastError(error); }
}
