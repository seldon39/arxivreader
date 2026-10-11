// Onglet « Papiers liés » (à la manière de Connected Papers) : les articles les plus proches d'un article,
// d'après Semantic Scholar (références communes et co-citations), en liste et en graphe, avec à côté les
// travaux antérieurs (les plus cités par le graphe) et dérivés (ceux qui citent le plus d'articles du graphe).
// On choisit l'article par son lien arXiv, dans la bibliothèque, ou en le glissant dans le cadre.

import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { fileInFolder } from "./selection.js";
import { draggablePaper, droppedPapers, h, icons, mayBePaper, navigation, openRoute, plainTitle, tidyTitle, toast, toastError, wantsNewTab, withMath } from "./util.js";
import { typesetTex } from "./math.js";

// dernier graphe et choix, gardés le temps de la session (retour depuis le lecteur : tout est là)
// `pending` : construction en cours (gardée si l'on quitte la page : le graphe s'affichera au retour)
const state = { query: "", graph: null, selected: null, tab: "graph", pending: null };
const TABS = { graph: "nodes", prior: "prior", derivative: "derivative" }; // onglet → liste du graphe
const SVG = "http://www.w3.org/2000/svg";
const W = 800;
const H = 460;

export async function renderRelated(root, arxivFromRoute = null) {
  const input = h("input", { class: "text-input", type: "text", value: state.query, spellcheck: "false", autocomplete: "off",
    placeholder: t("related.placeholder"), "aria-label": t("related.placeholder") });
  const build = h("button", { class: "button primary", type: "submit" }, t("related.build"));
  const pick = h("button", { class: "button", type: "button", "aria-haspopup": "menu" }, t("related.fromLibrary"), " ▾");
  const form = h("form", { class: "related-input" }, h("span", { class: "search-icon", html: icons.search }), input, pick, build);
  const status = h("div", { class: "related-status" });
  const result = h("div", { class: "related-result" });
  root.replaceChildren(h("section", { class: "related-page" },
    h("h1", {}, t("related.title")),
    h("p", { class: "related-lead" }, t("related.lead")),
    form, h("p", { class: "muted small related-drop-hint" }, t("related.dropHint")), status, result));

  function load(query, refresh = false) {
    state.query = query;
    input.value = query;
    // la demande vit hors de la page : quittée puis rouverte, celle-ci retrouve la construction en cours
    const pending = { query, promise: api.related(query, refresh) };
    state.pending = pending;
    pending.promise.then((graph) => {
      if (state.pending !== pending) return;
      state.pending = null;
      state.graph = graph;
      state.selected = graph.nodes[0]?.id || null;
    }, (error) => {
      if (state.pending !== pending) return;
      state.pending = null;
      pending.error = error;
    });
    follow(pending);
  }
  // affichage d'une construction (lancée ici ou avant de quitter la page)
  async function follow(pending) {
    build.disabled = true;
    status.replaceChildren(h("span", { class: "busy" }, h("span", { class: "spinner" }), t("related.building")));
    try { await pending.promise; } catch { /* erreur montrée ci-dessous */ }
    if (!root.isConnected || (state.pending && state.pending !== pending)) return;
    build.disabled = false;
    if (pending.error) {
      const code = pending.error.code || pending.error.message;
      const key = `related.error.${code}`;
      status.replaceChildren(h("p", { class: "notice" }, t(key) !== key ? t(key) : t("error.generic", { message: pending.error.message })));
      return;
    }
    status.replaceChildren();
    // adresse de la page : celle de ce graphe (retour du lecteur, changement de langue, « Partir de cet article »)
    const hash = `#/related/${encodeURIComponent(state.graph.origin.arxivId)}`;
    if (location.hash !== hash) history.replaceState(null, "", hash);
    navigation.listHash = hash;
    draw();
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (input.value.trim()) load(input.value.trim());
    else input.focus();
  });
  // article glissé (bibliothèque, feed, autre fenêtre, lien arXiv) sur le cadre
  form.addEventListener("dragover", (event) => {
    if (!mayBePaper(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    form.classList.add("drop-target");
  });
  form.addEventListener("dragleave", (event) => { if (!form.contains(event.relatedTarget)) form.classList.remove("drop-target"); });
  form.addEventListener("drop", (event) => {
    event.preventDefault();
    form.classList.remove("drop-target");
    const [paper] = droppedPapers(event.dataTransfer);
    if (paper) load(paper.arxivId); else toast(t("library.drop.notPaper"), { error: true });
  });
  pick.addEventListener("click", () => libraryMenu(pick, form, (arxivId) => load(arxivId)));
  let mounted = true;

  // ---- Résultat : en-tête, liste (onglets) et graphe, article choisi dessous ----
  function draw() {
    const graph = state.graph;
    if (!graph) { result.replaceChildren(); return; }
    const o = graph.origin;
    const head = h("div", { class: "related-origin" },
      h("h2", {}, withMath(tidyTitle(o.title))),
      h("span", { class: "muted small" }, [authorLine(o.authors), o.year, o.citationsCapped ? t("related.citationsCapped") : tn("related.citations", o.citations),
        tn("related.count", graph.nodes.length)].filter(Boolean).join(" · ")),
      h("span", { class: "spacer" }),
      h("button", { class: "link-button small", type: "button", title: t("related.rebuildHint", { date: (graph.builtAt || "").slice(0, 10) }),
        onclick: () => load(o.arxivId, true) }, t("related.rebuild")));
    if (!graph.nodes.length) {
      result.replaceChildren(head, h("p", { class: "empty" }, t("related.none")));
      return;
    }
    const tabs = h("div", { class: "segmented search-segmented related-tabs", role: "radiogroup", "aria-label": t("related.show") },
      Object.keys(TABS).map((key) => h("label", { title: t(`related.tabHint.${key}`) }, h("input", { type: "radio", name: "related-tab", value: key,
        checked: state.tab === key, onchange: () => { state.tab = key; drawList(); } }), t(`related.tab.${key}`))));
    const list = h("div", { class: "related-list" });
    const graphBox = h("div", { class: "related-graph" });
    const detail = h("div", { class: "related-detail" });
    // article choisi : du graphe, ou d'une des listes (antérieurs, dérivés) ; ceux qui lui sont liés ressortent
    const papers = () => [...graph.nodes, ...(graph.prior || []), ...(graph.derivative || [])];
    const chosen = () => papers().find((x) => x.id === state.selected) || graph.nodes[0];
    const marks = () => {
      const n = chosen();
      for (const item of list.querySelectorAll(".related-item")) {
        item.classList.toggle("on", item.dataset.id === n.id);
        const other = papers().find((x) => x.id === item.dataset.id);
        item.classList.toggle("linked", Boolean(other?.links?.includes(n.id)));
      }
    };
    const select = (id) => {
      state.selected = id;
      marks();
      drawDetail();
      highlight();
    };
    function drawList() {
      const shown = graph[TABS[state.tab]] || [];
      if (!shown.length) {
        list.replaceChildren(h("p", { class: "muted small related-empty" }, t(`related.emptyTab.${state.tab}`)));
        return;
      }
      list.replaceChildren(...shown.map((n) => {
        // titre : un lien (pour le glisser sur un dossier) qui choisit l'article ; sans arXiv, l'élément se choisit au clavier
        const title = n.arxivId ? h("a", { class: "related-item-title", href: `https://arxiv.org/abs/${n.arxivId}`,
          onclick: (e) => { e.preventDefault(); e.stopPropagation(); select(n.id); } }, withMath(tidyTitle(n.title)))
          : h("span", { class: "related-item-title" }, withMath(tidyTitle(n.title)));
        const item = h("div", { class: `related-item${n.id === state.selected ? " on" : ""}`, dataset: { id: n.id }, onclick: () => select(n.id),
          tabindex: n.arxivId ? null : 0, onkeydown: (e) => { if (!n.arxivId && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(n.id); } } }, title,
          h("span", { class: "muted small" }, [authorLine(n.authors), n.year, tn("related.citations", n.citations), relation(n)].filter(Boolean).join(" · "),
            n.libraryId ? h("span", { class: "related-in-library" }, ` · ${t("related.inLibrary")}`) : ""));
        // glissé (par son titre) sur un dossier de la bibliothèque, comme les cartes du feed
        return draggablePaper(item, { arxivId: n.arxivId, paperId: n.libraryId, title: n.title });
      }));
      marks();
      typesetTex([list]).catch(() => {});
    }
    function drawDetail() {
      const n = chosen();
      detail.replaceChildren(
        h("h3", {}, withMath(tidyTitle(n.title))),
        h("p", { class: "muted small" }, [n.authors.join(", "), n.year, tn("related.citations", n.citations),
          n.links ? relation(n) : t("related.closeness", { shared: n.coupling, cocited: n.cocited })].filter(Boolean).join(" · ")),
        h("div", { class: "related-actions" }, ...actions(n, () => { drawList(); drawDetail(); highlight(); })));
      if (!mounted) return;
      typesetTex([detail]).catch(() => {});
    }
    const { svg, highlight } = drawGraph(graph, chosen, select);
    graphBox.append(svg, h("div", { class: "related-legend" },
      h("span", {}, h("i", { class: "legend-old" }), t("related.legend.old")), h("span", {}, h("i", { class: "legend-new" }), t("related.legend.new")),
      h("span", {}, t("related.legend.size")), h("span", {}, h("i", { class: "legend-library" }), t("related.inLibrary"))));
    result.replaceChildren(head, h("div", { class: "related-grid" },
      h("div", { class: "related-side" }, tabs, list), graphBox, detail));
    drawList();
    drawDetail();
    typesetTex([head]).catch(() => {});
  }

  // Actions sur l'article choisi : lire, ajouter à la bibliothèque, ranger, repartir de lui
  function actions(n, refresh) {
    if (!n.arxivId) {
      return [h("a", { class: "button small-button", href: `https://www.semanticscholar.org/paper/${n.id}`, target: "_blank", rel: "noopener noreferrer" },
        h("span", { class: "button-icon", html: icons.external }), t("related.onS2"))];
    }
    const read = h("button", { class: "button small-button primary", type: "button" }, t("feed.read"));
    read.addEventListener("click", async (event) => {
      const newTab = wantsNewTab(event);
      const id = n.libraryId || n.readId;
      if (id) { openRoute(`#/paper/${encodeURIComponent(id)}`, newTab, event.shiftKey); return; }
      read.disabled = true;
      try {
        const paper = await api.importPaper(n.arxivId, false);
        n.readId = paper.id;
        openRoute(`#/paper/${encodeURIComponent(paper.id)}`, newTab, event.shiftKey);
      } catch (error) { toastError(error); } finally { read.disabled = false; }
    });
    const add = h("button", { class: "button small-button", type: "button", disabled: Boolean(n.libraryId) },
      t(n.libraryId ? "related.inLibraryButton" : "feed.addToLibrary"));
    add.addEventListener("click", async () => {
      add.disabled = true;
      try {
        const paper = await api.importPaper(n.arxivId);
        n.libraryId = n.readId = paper.id;
        toast(t("library.drop.addedLibrary", { title: shortTitle(n.title) }));
        refresh();
      } catch (error) { toastError(error); add.disabled = false; }
    });
    const file = h("button", { class: "button small-button", type: "button", "aria-haspopup": "menu" }, h("span", { class: "button-icon", html: icons.folder }), t("select.file"));
    file.addEventListener("click", () => folderMenu(file, async (folder) => {
      file.disabled = true;
      try {
        await fileInFolder([{ arxivId: n.arxivId, paperId: n.libraryId, title: n.title }], folder);
        const papers = await api.papers();
        n.libraryId = papers.find((p) => p.arxiv_id === n.arxivId)?.id || n.libraryId;
        refresh();
      } catch (error) { toastError(error); } finally { file.disabled = false; }
    }));
    const from = h("a", { class: "button small-button", href: `#/related/${encodeURIComponent(n.arxivId)}` }, t("related.startHere"));
    return [read, add, file, from];
  }

  const same = (a, b) => (a || "").replace(/^([a-z-]+)\.[A-Z]{2}\//, "$1/") === (b || "");
  if (state.pending) follow(state.pending); // construction lancée avant de quitter la page
  else if (arxivFromRoute && !same(arxivFromRoute, state.graph?.origin?.arxivId)) load(arxivFromRoute);
  else {
    draw();
    if (state.graph) {
      const hash = `#/related/${encodeURIComponent(state.graph.origin.arxivId)}`;
      if (location.hash !== hash) history.replaceState(null, "", hash); // adresse de ce graphe
      navigation.listHash = hash;
      // retour sur la page : « dans la bibliothèque » remis à jour (graphe gardé par le serveur : pas de nouvelle requête)
      const shown = state.graph;
      api.related(shown.origin.arxivId).then((fresh) => {
        if (!mounted || state.graph !== shown || state.pending) return;
        state.graph = fresh;
        draw();
      }, () => {});
    }
  }
  return () => { mounted = false; };
}

// lien avec l'article (graphe) ou avec le graphe (travaux antérieurs et dérivés)
const relation = (n) => (n.links ? tn(n.rel === "cite" ? "related.citesGraph" : "related.citedByGraph", n.links.length) : t(`related.rel.${n.rel}`));
const authorLine = (authors) => !authors?.length ? "" : authors.length > 3 ? `${authors.slice(0, 2).join(", ")} ${t("feed.etAl")}` : authors.join(", ");
const shortTitle = (title) => (title.length > 60 ? `${title.slice(0, 58)}…` : title);
const lastName = (name) => String(name || "").trim().split(/\s+/).at(-1) || "";

// ---- Graphe : disposition par forces (rapprochés selon leur proximité), recentrée dans le cadre ----
function layout(graph) {
  const nodes = [{ ...graph.origin, id: "__origin", origin: true }, ...graph.nodes.map((n) => ({ ...n }))];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const links = graph.edges.map(([a, b, s]) => ({ a: byId.get(a), b: byId.get(b), s })).filter((l) => l.a && l.b);
  const origin = byId.get("__origin");
  for (const n of nodes) if (!n.origin) links.push({ a: origin, b: n, s: Math.max(1, n.score / 10) });
  // départ déterministe : en spirale, les plus proches au centre
  nodes.forEach((n, i) => { const r = 30 + 14 * i, a = i * 2.4; n.x = Math.cos(a) * r; n.y = Math.sin(a) * r; });
  origin.x = origin.y = 0;
  for (let step = 0; step < 600; step++) {
    const cool = 1 - step / 700;
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const p = nodes[i], q = nodes[j], dx = p.x - q.x, dy = p.y - q.y, d2 = dx * dx + dy * dy + 1, d = Math.sqrt(d2);
      const push = Math.min(5, 3000 / d2) * cool;
      p.x += dx / d * push; p.y += dy / d * push; q.x -= dx / d * push; q.y -= dy / d * push;
    }
    for (const l of links) {
      const dx = l.b.x - l.a.x, dy = l.b.y - l.a.y, d = Math.hypot(dx, dy) || 1;
      const target = l.a.origin ? 320 / Math.sqrt(1 + l.s / 5) : 240 / Math.sqrt(1 + l.s / 4);
      const pull = (d - target) * 0.012 * cool;
      l.a.x += dx / d * pull; l.a.y += dy / d * pull; l.b.x -= dx / d * pull; l.b.y -= dy / d * pull;
    }
  }
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const k = Math.min((W - 110) / Math.max(1, x1 - x0), (H - 80) / Math.max(1, y1 - y0));
  for (const n of nodes) {
    n.x = 55 + (n.x - x0) * k + (W - 110 - (x1 - x0) * k) / 2;
    n.y = 32 + (n.y - y0) * k + (H - 80 - (y1 - y0) * k) / 2;
  }
  return { nodes, links };
}

function drawGraph(graph, selected, select) {
  const { nodes, links } = layout(graph);
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "group"); // les points restent atteignables (VoiceOver, clavier)
  svg.setAttribute("aria-label", t("related.graphLabel"));
  const el = (tag, attrs, parent) => { const e = document.createElementNS(SVG, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent?.append(e); return e; };
  const view = el("g", {}, svg);
  const years = graph.nodes.map((n) => n.year).filter(Boolean);
  const y0 = years.length ? Math.min(...years) : 2000, y1 = years.length ? Math.max(...years) : 2000;
  const shade = (n) => `color-mix(in srgb, var(--accent) ${Math.round(22 + 70 * (((n.year || y0) - y0) / Math.max(1, y1 - y0)))}%, var(--surface))`;
  const radius = (n) => (n.origin ? 14 : 4 + Math.min(18, Math.sqrt(n.citations || 0) * 0.36));
  for (const l of links) if (!l.a.origin) el("line", { x1: l.a.x, y1: l.a.y, x2: l.b.x, y2: l.b.y, class: "related-edge", "stroke-width": Math.min(2.4, 0.4 + l.s / 12) }, view);
  const groups = new Map();
  for (const n of nodes) {
    const g = el("g", { class: `related-node${n.origin ? " origin" : ""}${n.libraryId ? " in-library" : ""}`, tabindex: n.origin ? -1 : 0 }, view);
    // couleur en style : WebKit (l'application) n'accepte pas var() dans l'attribut fill
    el("circle", { cx: n.x, cy: n.y, r: radius(n), style: `fill: ${n.origin ? "var(--accent)" : shade(n)}` }, g);
    const label = el("text", { x: n.x, y: n.y + radius(n) + 11, "text-anchor": "middle" }, g);
    label.textContent = `${lastName(n.authors?.[0])} ${n.year || ""}`.trim();
    el("title", {}, g).textContent = `${plainTitle(n.title)} (${n.year || "?"})`;
    if (!n.origin) {
      g.addEventListener("click", () => select(n.id));
      g.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(n.id); } });
    }
    groups.set(n.id, g);
  }
  // choisi (ou, pour un travail antérieur ou dérivé, les articles du graphe qui lui sont liés), et « dans la
  // bibliothèque » (un article ajouté depuis le panneau prend aussitôt son cercle)
  const highlight = () => {
    const chosen = selected();
    svg.classList.toggle("focus-links", Boolean(chosen.links)); // les autres articles s'effacent
    for (const [id, g] of groups) {
      g.classList.toggle("selected", id === chosen.id);
      g.classList.toggle("linked", Boolean(chosen.links?.includes(id === "__origin" ? "origin" : id)));
      const paper = graph.nodes.find((n) => n.id === id);
      if (paper) g.classList.toggle("in-library", Boolean(paper.libraryId));
    }
  };
  highlight();
  // zoom à la molette (ou au pincement) et déplacement en faisant glisser le fond
  let zoom = 1, panX = 0, panY = 0;
  const apply = () => view.setAttribute("transform", `translate(${panX} ${panY}) scale(${zoom})`);
  svg.addEventListener("wheel", (event) => {
    if (!event.ctrlKey && !event.metaKey) return; // pincement du trackpad (ctrlKey) ou ⌘ + molette ; sinon la page défile
    event.preventDefault();
    const box = svg.getBoundingClientRect();
    const mx = (event.clientX - box.left) * (W / box.width), my = (event.clientY - box.top) * (H / box.height);
    const next = Math.min(4, Math.max(0.6, zoom * Math.exp(-event.deltaY * 0.0015)));
    panX = mx - (mx - panX) * (next / zoom); panY = my - (my - panY) * (next / zoom);
    zoom = next;
    apply();
  }, { passive: false });
  let drag = null;
  svg.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".related-node")) return;
    drag = { x: event.clientX, y: event.clientY, panX, panY };
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const box = svg.getBoundingClientRect();
    panX = drag.panX + (event.clientX - drag.x) * (W / box.width);
    panY = drag.panY + (event.clientY - drag.y) * (H / box.height);
    apply();
  });
  svg.addEventListener("pointerup", () => { drag = null; });
  svg.addEventListener("pointercancel", () => { drag = null; });
  svg.addEventListener("lostpointercapture", () => { drag = null; });
  svg.addEventListener("dblclick", (event) => { if (event.target.closest(".related-node")) return; zoom = 1; panX = panY = 0; apply(); });
  return { svg, highlight };
}

// ---- Menus : articles de la bibliothèque, dossiers ----
let menuOpening = false;
async function libraryMenu(button, anchor, choose) {
  const open = anchor.querySelector(".related-menu");
  if (open) { open.remove(); return; } // second clic sur le bouton : fermé
  if (menuOpening) return;
  menuOpening = true;
  let papers;
  try { papers = await api.papers(); } catch (error) { toastError(error); return; } finally { menuOpening = false; }
  const filter = h("input", { class: "text-input", type: "search", placeholder: t("related.filterLibrary"), "aria-label": t("related.filterLibrary") });
  const items = h("div", { class: "related-menu-items" });
  const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const fill = () => {
    const words = fold(filter.value).split(/\s+/).filter(Boolean);
    const shown = papers.filter((p) => words.every((w) => fold(`${p.title} ${p.authors.join(" ")}`).includes(w))).slice(0, 60);
    items.replaceChildren(...(shown.length ? shown.map((p) => h("button", { class: "related-menu-item", type: "button",
      onclick: () => { close(); choose(p.arxiv_id); } }, h("span", {}, plainTitle(p.title)), h("span", { class: "muted small" }, authorLine(p.authors))))
      : [h("p", { class: "muted small" }, t("library.noMatch"))]));
  };
  filter.addEventListener("input", fill);
  const menu = h("div", { class: "related-menu", role: "menu" }, filter, items);
  const close = () => { menu.remove(); document.removeEventListener("pointerdown", outside); };
  const outside = (event) => { if (!menu.contains(event.target) && event.target !== button) close(); };
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { close(); button.focus(); } });
  fill();
  anchor.append(menu);
  document.addEventListener("pointerdown", outside);
  filter.focus();
}

async function folderMenu(button, choose) {
  document.querySelector(".related-folder-menu")?.remove();
  let folders;
  try { folders = await api.folders(); } catch (error) { toastError(error); return; }
  const menu = h("div", { class: "related-menu related-folder-menu", role: "menu" },
    folders.length ? folders.map((folder) => h("button", { class: "related-menu-item", type: "button", role: "menuitem",
      onclick: () => { close(); choose(folder); } }, h("span", {}, folder.name)))
      : h("p", { class: "muted small" }, t("select.noFolder")));
  const close = () => { menu.remove(); document.removeEventListener("pointerdown", outside); };
  const outside = (event) => { if (!menu.contains(event.target) && event.target !== button) close(); };
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { close(); button.focus(); } });
  button.parentElement.style.position = "relative";
  button.parentElement.append(menu);
  menu.style.left = `${button.offsetLeft}px`;
  document.addEventListener("pointerdown", outside);
  menu.querySelector("button")?.focus();
}
