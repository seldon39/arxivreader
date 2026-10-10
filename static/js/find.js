// Recherche dans la page : ⌘F (Ctrl+F), ⌘G / ⇧⌘G pour l'occurrence suivante / précédente.
// La page affichée choisit quoi faire (lecteur : barre de recherche dans l'article ; bibliothèque : son
// champ de recherche) ; sans page intéressée, le navigateur garde sa propre recherche.
// Application Mac : le menu Édition → Rechercher envoie l'événement « app:find ».

import { t } from "./i18n.js";
import { h, icons } from "./util.js";

let handler = null;

export function setFindHandler(fn) {
  handler = fn;
  return () => { if (handler === fn) handler = null; };
}

function handleFind(command) {
  if (!handler) return false;
  handler(command);
  return true;
}

document.addEventListener("keydown", (event) => {
  if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
  const key = event.key.toLowerCase();
  const command = key === "f" && !event.shiftKey ? "open" : key === "g" ? (event.shiftKey ? "previous" : "next") : null;
  if (command && handleFind(command)) event.preventDefault();
});
document.addEventListener("app:find", (event) => handleFind(event.detail));

const ARROW_UP = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 14.5 12 8.5l6 6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ARROW_DOWN = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9.5 12 15.5l6-6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// Barre de recherche : `search(texte)` renvoie les plages trouvées (dans l'ordre du texte),
// `reveal(plage)` amène l'occurrence courante à l'écran. Occurrences surlignées par l'API
// CSS Custom Highlight (le texte de l'article n'est pas modifié).
export function findBar({ host, search, reveal, firstVisible }) {
  let ranges = [];
  let index = -1;
  let timer = 0;
  let pending = false; // texte tapé, recherche pas encore faite
  const input = h("input", { class: "find-input", type: "search", placeholder: t("find.placeholder"), "aria-label": t("find.placeholder"),
    spellcheck: "false", autocomplete: "off" });
  const count = h("span", { class: "find-count", "aria-live": "polite" });
  const button = (icon, key, onclick) => h("button", { class: "icon-button small", type: "button", title: t(key), "aria-label": t(key), html: icon, onclick });
  const bar = h("div", { class: "find-bar", role: "search", hidden: true },
    h("span", { class: "find-icon", html: icons.search }), input, count,
    button(ARROW_UP, "find.previous", () => step(-1)), button(ARROW_DOWN, "find.next", () => step(1)), button(icons.close, "find.close", close));
  host.prepend(h("div", { class: "find-dock" }, bar));

  let marked = [];
  function paint() {
    marked.forEach((formula) => formula.classList.remove("find-formula", "find-formula-current"));
    marked = ranges.flatMap((range) => range.formulas || []);
    marked.forEach((formula) => formula.classList.add("find-formula"));
    ranges[index]?.formulas?.forEach((formula) => formula.classList.add("find-formula-current"));
    if (!window.CSS?.highlights || typeof Highlight === "undefined") return;
    CSS.highlights.set("find", new Highlight(...ranges));
    if (ranges[index]) CSS.highlights.set("find-current", new Highlight(ranges[index]));
    else CSS.highlights.delete("find-current");
  }

  function update() {
    const query = input.value.trim();
    count.textContent = !query ? "" : ranges.length ? `${index + 1} / ${ranges.length}` : t("find.none");
    bar.classList.toggle("no-match", Boolean(query) && !ranges.length);
    paint();
    if (ranges[index]) reveal(ranges[index]);
  }

  function run() {
    clearTimeout(timer);
    pending = false;
    const query = input.value.trim();
    ranges = query ? search(query) : [];
    index = ranges.length ? Math.max(0, firstVisible(ranges)) : -1; // première occurrence à partir de l'endroit lu
    update();
  }

  function step(direction) {
    if (!ranges.length) return run();
    index = (index + direction + ranges.length) % ranges.length;
    update();
  }

  function open() {
    bar.hidden = false;
    input.focus();
    input.select();
    if (input.value.trim() && !ranges.length) run();
  }

  function close() {
    bar.hidden = true;
    ranges = [];
    index = -1;
    paint();
    window.CSS?.highlights?.delete("find");
    window.CSS?.highlights?.delete("find-current");
  }

  input.addEventListener("input", () => { clearTimeout(timer); pending = true; timer = setTimeout(run, 150); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (pending) run();
      else step(event.shiftKey ? -1 : 1);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation(); // ne ferme pas aussi les volets
      close();
    }
  });

  return {
    command(name) {
      if (name === "open" || bar.hidden) open();
      else step(name === "previous" ? -1 : 1);
    },
    refresh() { if (!bar.hidden && input.value.trim()) run(); },
    close,
  };
}
