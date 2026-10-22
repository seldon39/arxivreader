// Version HTML expérimentale (LaTeXML, sur l'ordinateur) d'un document lu dans son PDF : fichier TeX importé ou
// article d'arXiv sans version HTML. Faite seulement à la demande (clic sur « HTML β ») : avertissement une fois
// (masquable), conversion en arrière-plan pendant qu'on lit le PDF, puis la version HTML s'ouvre.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { h, toast, toastError } from "./util.js";

const HIDE_WARNING = "latexmlWarningHidden";
const OPEN_HTML = (id) => `htmlView:${id}`;

const stored = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
const store = (key, value) => { try { localStorage.setItem(key, value); } catch { /* stockage indisponible */ } };

// à l'ouverture : la version HTML d'abord ? (choisie dans cet onglet, ou tout juste faite) ; sinon le PDF
export function wantsHtml(paperId) {
  try { return sessionStorage.getItem(OPEN_HTML(paperId)) === "1"; } catch { return false; }
}

export function rememberView(paperId, html) {
  try { if (html) sessionStorage.setItem(OPEN_HTML(paperId), "1"); else sessionStorage.removeItem(OPEN_HTML(paperId)); } catch { /* idem */ }
}

let openPopover = null;
function popover(anchor, children) {
  openPopover?.();
  const box = h("div", { class: "html-popover", role: "dialog" }, ...children);
  document.body.append(box);
  const rect = anchor.getBoundingClientRect();
  box.style.top = `${rect.bottom + 10}px`;
  box.style.left = `${Math.max(8, Math.min(rect.right - box.offsetWidth, innerWidth - box.offsetWidth - 8))}px`;
  const close = () => {
    box.remove();
    document.removeEventListener("mousedown", outside, true);
    document.removeEventListener("keydown", escape, true);
    removeEventListener("blur", close);  // clic dans le PDF (autre document) : la fenêtre perd le focus
    removeEventListener("hashchange", close);
    if (openPopover === close) openPopover = null;
  };
  const outside = (event) => { if (!box.contains(event.target) && !anchor.contains(event.target)) close(); };
  const escape = (event) => { if (event.key === "Escape") close(); };
  setTimeout(() => {
    document.addEventListener("mousedown", outside, true);
    document.addEventListener("keydown", escape, true);
    addEventListener("blur", close);
    addEventListener("hashchange", close);
  }, 0);
  openPopover = close;
  return close;
}

function installHelp() {
  if (/Windows/.test(navigator.userAgent)) {  // plusieurs étapes (Perl, puis LaTeXML) : le guide officiel
    return [h("div", { class: "muted" }, t("latexml.windows")),
      h("a", { href: "https://math.nist.gov/~BMiller/LaTeXML/get.html", target: "_blank", rel: "noopener noreferrer" }, t("latexml.guide"))];
  }
  const command = /Mac/.test(navigator.userAgent) ? "brew install latexml" : "sudo apt install latexml";
  const copy = h("button", { class: "button small-button", type: "button", onclick: () => navigator.clipboard?.writeText(command).then(() => toast(t("latexml.copied")), () => {}) }, t("latexml.copy"));
  return [h("div", { class: "muted" }, t("latexml.terminal")), h("div", { class: "html-command" }, h("code", {}, command), copy)];
}

// Clic sur « HTML β » quand la version HTML n'est pas (ou plus) prête. `reader` : { paperId, article (zone de
// lecture, pour l'attente) }
export async function htmlFlow(anchor, reader) {
  let status;
  try { status = await api.htmlStatus(reader.paperId); } catch (error) { toastError(error); return; }
  if (!status.installed) {
    popover(anchor, [h("div", { class: "html-popover-title" }, t("latexml.missing")), ...installHelp(), h("div", { class: "muted small" }, t("latexml.thenAgain"))]);
    return;
  }
  if (status.state === "running") { convert(reader, true); return; }
  if (status.state === "failed") {
    const close = popover(anchor, [h("div", { class: "html-popover-title" }, t("latexml.failedTitle")),
      h("div", { class: "muted small html-error" }, status.error === "timeout" ? t("latexml.timeout") : status.error || ""),
      h("div", { class: "html-popover-actions" }, h("button", { class: "button small-button", type: "button", onclick: () => { close(); convert(reader); } }, t("latexml.retry")))]);
    return;
  }
  if (stored(HIDE_WARNING) === "1") { convert(reader); return; }
  const hide = h("input", { type: "checkbox" });
  const close = popover(anchor, [
    h("div", { class: "html-popover-title" }, t("latexml.title")),
    h("div", { class: "muted" }, t(status.state === "stale" ? "latexml.warningStale" : "latexml.warning")),
    h("label", { class: "muted small html-hide" }, hide, t("latexml.hide")),
    h("div", { class: "html-popover-actions" },
      h("button", { class: "button small-button", type: "button", onclick: () => close() }, t("latexml.cancel")),
      h("button", { class: "button primary small-button", type: "button", onclick: () => {
        if (hide.checked) store(HIDE_WARNING, "1");
        close();
        convert(reader);
      } }, t("latexml.make"))),
  ]);
}

// Conversion suivie ; pendant ce temps, un panneau d'attente au-dessus du PDF (« Lire le PDF » le ferme)
async function convert(reader, running = false) {
  const waiting = h("div", { class: "html-waiting" },
    h("div", { class: "html-waiting-box" }, h("span", { class: "spinner" }), h("div", { class: "html-waiting-title" }, t("latexml.converting")),
      h("div", { class: "muted small" }, t("latexml.convertingHint")),
      h("button", { class: "button small-button", type: "button", onclick: () => waiting.remove() }, t("latexml.readPdf"))));
  reader.article.append(waiting);
  let result;
  try {
    result = await (running ? api.htmlJob(reader.paperId) : api.makeHtml(reader.paperId));
  } catch (error) {
    waiting.remove();
    toast(/already running|being made/.test(error.message) ? t("latexml.busy") : t("latexml.failed"), { error: true, duration: 8000 });
    return;
  }
  const watching = waiting.isConnected;
  waiting.remove();
  if (result?.state !== "ready") { toast(t("latexml.changed"), { duration: 8000 }); return; }  // recompilé entre-temps
  // ouverte sur cet article, même si l'on est allé ailleurs entre-temps
  const open = () => {
    rememberView(reader.paperId, true);
    const route = `#/paper/${encodeURIComponent(reader.paperId)}`;
    if (location.hash === route) dispatchEvent(new HashChangeEvent("hashchange")); else location.hash = route;
  };
  if (watching) { open(); return; }
  toast(t("latexml.ready"), { duration: 12000, action: { label: t("latexml.open"), onClick: open } });
}
