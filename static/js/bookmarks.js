// Marque-pages : endroits nommés de l'article, posés en HTML (un paragraphe ou un énoncé) ou dans le PDF (page et
// hauteur, en points du PDF). Annotation {kind: "bookmark", quote: nom, target: {id} ou {pdf: {page, top}}, version}.
// Un ruban dans la marge, dans la vue où il a été posé ; tous dans l'onglet Notes, où un clic y ramène (et change de
// vue au besoin). Posé sur une version antérieure de l'article : signalé (le texte a pu bouger).

import { t } from "./i18n.js";
import { h, icons } from "./util.js";

const isBookmark = (annotation) => annotation.kind === "bookmark";
export const inPdf = (annotation) => Boolean(annotation.target?.pdf);

// version de l'article quand le marque-page a été posé, si elle n'est plus la même
export function staleVersion(annotation, paper) {
  const was = annotation.target?.version;
  return was && paper.version && was !== paper.version ? was : null;
}

// Petite fenêtre : nom du marque-page (création ou renommage), sous `rect` (rectangle dans la page de l'application).
// `onSave(nom)`, `onDelete()` facultatif. Largeur ajustée à son contenu (pas celle, plus grande, des notes).
let open = null;
export function bookmarkBox({ heading, value, rect, onSave, onDelete = null, saveLabel = t("bookmarks.add") }) {
  open?.close();
  const input = h("input", { class: "text-input", type: "text", value, "aria-label": t("bookmarks.name"), placeholder: t("bookmarks.name") });
  const box = h("form", { class: "bookmark-box", role: "dialog", "aria-label": heading },
    h("div", { class: "bookmark-box-head" }, heading),
    input,
    h("div", { class: "bookmark-box-actions" },
      onDelete && h("button", { class: "button small-button danger", type: "button", onclick: () => { close(); onDelete(); } }, t("bookmarks.delete")),
      h("span", { class: "spacer" }),
      h("button", { class: "button small-button", type: "button", onclick: () => close() }, t("library.folder.cancel")),
      h("button", { class: "button small-button primary", type: "submit" }, saveLabel)));
  box.addEventListener("submit", (event) => { event.preventDefault(); close(); onSave(input.value.trim()); });
  box.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } });
  const outside = (event) => { if (!box.contains(event.target)) close(); };
  function close() {
    box.remove();
    document.removeEventListener("mousedown", outside, true);
    if (open?.box === box) open = null;
  }
  document.body.append(box);
  const width = box.offsetWidth;
  box.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, rect.left + rect.width / 2 - width / 2))}px`;
  box.style.top = `${Math.min(innerHeight - box.offsetHeight - 8, rect.bottom + 8)}px`;
  setTimeout(() => document.addEventListener("mousedown", outside, true), 0);
  input.focus();
  input.select();
  open = { box, close };
  return close;
}

// Petit menu à l'endroit d'un clic droit (dans le PDF, où le menu de l'annotateur n'existe pas) : [[libellé, action]]
let closeMenu = null;
export function contextMenuAt(point, items) {
  closeMenu?.();
  const close = () => {
    menu.remove();
    document.removeEventListener("mousedown", outside, true);
    if (closeMenu === close) closeMenu = null;
  };
  closeMenu = close;
  const outside = (event) => { if (!menu.contains(event.target)) close(); };
  const menu = h("div", { class: "context-menu bookmark-menu", role: "menu" }, items.map(([label, action]) =>
    h("button", { type: "button", class: "context-menu-item", role: "menuitem", onclick: () => { close(); action(); } }, label)));
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
  document.body.append(menu);
  menu.style.left = `${Math.max(8, Math.min(point.left, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(point.top, innerHeight - menu.offsetHeight - 8))}px`;
  menu.querySelector("button")?.focus({ preventScroll: true });
  setTimeout(() => document.addEventListener("mousedown", outside, true), 0);
}

// Clic, défilement ou Échap dans le PDF (un autre document : la page de l'application ne les voit pas) : menu du
// clic droit et fenêtre du nom fermés
export function closeBookmarkUi() {
  closeMenu?.();
  open?.close();
}

// Onglet Notes : la liste, HTML dans l'ordre de l'article puis PDF par page. `find(id)` : élément du HTML ;
// `where(annotation, element)` : page ou section ; `go(annotation)`.
export function bookmarkSection(items, { paper, find, where, go }) {
  const marks = items.filter(isBookmark).map((a) => ({ a, element: inPdf(a) ? null : find(a.target.id) }));
  if (!marks.length) return null;
  marks.sort((x, y) => {
    if (inPdf(x.a) !== inPdf(y.a)) return inPdf(x.a) ? 1 : -1;
    if (inPdf(x.a)) return x.a.target.pdf.page - y.a.target.pdf.page || (y.a.target.pdf.top ?? 0) - (x.a.target.pdf.top ?? 0);
    if (!x.element || !y.element) return !x.element ? 1 : -1;
    return x.element.compareDocumentPosition(y.element) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
  });
  return h("div", { class: "panel-section" },
    h("h3", {}, t("bookmarks.title", { n: marks.length })),
    h("div", { class: "bookmark-list" }, marks.map(({ a, element }) => {
      const lost = !inPdf(a) && !element;
      const was = staleVersion(a, paper);
      return h("button", { class: `bookmark-row${lost ? " orphan" : ""}`, type: "button", disabled: lost,
        title: lost ? t("bookmarks.lost") : was ? t("bookmarks.staleHint", { was, now: paper.version }) : t("panel.showInText"),
        onclick: () => go(a) },
        h("span", { class: "bookmark-icon", html: icons.ribbon }),
        h("span", { class: "bookmark-name" }, a.quote || t("bookmarks.untitled")),
        (lost || was) && h("span", { class: "badge warn" }, lost ? t("bookmarks.lostShort") : t("bookmarks.stale", { was })),
        h("span", { class: "muted small bookmark-where" }, where(a, element)));
    })));
}
