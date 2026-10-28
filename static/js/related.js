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
const state = { query: "", graph: null, selected: null, tab: "graph", pending: null, expanded: false };
const TABS = { graph: "nodes", prior: "prior", derivative: "derivative" }; // onglet → liste du graphe
const SVG = "http://www.w3.org/2000/svg";
const W = 800;
const H = 460; // hauteur par défaut ; à côté de la liste, celle de la place disponible (graphHeight)

export async function renderRelated(root, arxivFromRoute = null) {
  const input = h("input", { class: "text-input", type: "text", value: state.query, spellcheck: "false", autocomplete: "off",
    placeholder: t("related.placeholder"), "aria-label": t("related.placeholder") });
  const build = h("button", { class: "button primary", type: "submit" }, t("related.build"));
  const pick = h("button", { class: "button", type: "button", "aria-haspopup": "menu" }, t("related.fromLibrary"), " ▾");
  // cadre pointillé : champ et boutons, et dessous, l'invitation à y glisser un article (le cadre reçoit le dépôt)
  const form = h("form", { class: "related-input" }, h("span", { class: "search-icon", html: icons.search }), input, pick, build,
    h("p", { class: "muted small related-drop-hint" }, t("related.dropHint")));
  const status = h("div", { class: "related-status", role: "status", "aria-live": "polite" });
  const result = h("div", { class: "related-result" });
  root.replaceChildren(h("section", { class: "related-page" },
    h("h1", {}, t("related.title")),
    h("p", { class: "related-lead" }, t("related.lead")),
    form, status, result));

  // Construction en arrière-plan sur le serveur : suivie chaque seconde (étape, lot), annulable
  const failure = (code) => Object.assign(new Error(code), { code });
  async function construct(pending, refresh) {
    let view = await api.related(pending.query, refresh);
    pending.id = view.id;
    while (view.state === "building") {
      if (pending.cancelled) { api.relatedCancel(view.id).catch(() => {}); throw failure("cancelled"); }
      pending.view = view;
      pending.onProgress?.(view);
      await new Promise((resolve) => setTimeout(resolve, 1000));
      if (pending.cancelled) throw failure("cancelled");
      view = await api.relatedStatus(view.id);
    }
    if (view.state === "done") return view.graph;
    throw failure(view.state === "error" ? view.code : view.state === "cancelled" ? "cancelled" : "interrupted");
  }

  function load(query, refresh = false) {
    state.query = query;
    input.value = query;
    // une autre construction encore en cours (autre article) : annulée, elle n'occuperait plus que le quota
    if (state.pending) {
      state.pending.cancelled = true;
      if (state.pending.id) api.relatedCancel(state.pending.id).catch(() => {});
    }
    // la demande vit hors de la page : quittée puis rouverte, celle-ci retrouve la construction en cours
    const pending = { query, cancelled: false };
    pending.promise = construct(pending, refresh);
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
    const step = h("span", { class: "muted small related-step" });
    const cancel = h("button", { class: "button small-button", type: "button" }, t("related.cancel"));
    cancel.addEventListener("click", () => {
      pending.cancelled = true;
      if (pending.id) api.relatedCancel(pending.id).catch(() => {});
      cancel.disabled = true;
    });
    const showStep = (view) => {
      const key = `related.step.${view.step}`;
      step.textContent = t(key) !== key ? t(key, { done: view.done, total: view.total }) : "";
    };
    pending.onProgress = showStep;
    if (pending.view) showStep(pending.view);
    status.replaceChildren(h("div", { class: "related-building" },
      h("span", { class: "busy" }, h("span", { class: "spinner" }), t("related.building")), step, cancel));
    try { await pending.promise; } catch { /* erreur montrée ci-dessous */ }
    if (!mounted || (state.pending && state.pending !== pending)) return; // page quittée : l'adresse de la page ouverte ne change pas
    build.disabled = false;
    if (pending.error?.code === "cancelled") {
      const focused = status.contains(document.activeElement);
      status.replaceChildren(h("p", { class: "muted" }, t("related.cancelled")));
      if (focused) build.focus(); // le bouton « Annuler » disparu : le focus ne retombe pas sur la page
      return;
    }
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
    const paper = droppedPapers(event.dataTransfer).find((item) => item.arxivId);  // PDF importé : pas de graphe
    if (paper) load(paper.arxivId); else toast(t("library.drop.notPaper"), { error: true });
  });
  pick.addEventListener("click", () => libraryMenu(pick, form, (arxivId) => load(arxivId)));
  let mounted = true;
  let collapse = () => {}; // graphe agrandi : réduit quand on quitte la page
  let detachKeys = () => {};
  let refreshMarks = null; // graphe affiché : marques « dans la bibliothèque » remises à jour sans tout redessiner

  // ---- Résultat : en-tête, liste (onglets) et graphe, article choisi dessous ----
  function draw() {
    detachKeys(); // graphe précédent (agrandi) : ses touches ne restent pas sur la page
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
    const detail = h("div", { class: "related-detail", "aria-live": "polite" });
    // article choisi : du graphe, ou d'une des listes (antérieurs, dérivés) ; ceux qui lui sont liés ressortent
    const byId = new Map([...graph.nodes, ...(graph.prior || []), ...(graph.derivative || [])].map((x) => [x.id, x]));
    const chosen = () => byId.get(state.selected) || graph.nodes[0];
    const marks = () => {
      const n = chosen();
      for (const item of list.querySelectorAll(".related-item")) {
        const id = item.dataset.id;
        item.classList.toggle("on", id === n.id);
        item.classList.toggle("linked", Boolean(byId.get(id)?.links?.includes(n.id) || n.links?.includes(id)));
      }
    };
    const select = (id, fromGraph = false) => {
      state.selected = id;
      marks();
      drawDetail();
      highlight();
      if (fromGraph) list.querySelector(`.related-item[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest" });
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
      typesetTex([list]);
    }
    function drawDetail() {
      const n = chosen();
      detail.replaceChildren(
        h("h3", {}, withMath(tidyTitle(n.title))),
        h("p", { class: "muted small" }, [n.authors.join(", "), n.year, tn("related.citations", n.citations),
          n.links ? relation(n) : t("related.closeness", { shared: n.coupling, cocited: n.cocited })].filter(Boolean).join(" · ")),
        h("div", { class: "related-actions" }, ...actions(n, () => { drawList(); drawDetail(); highlight(); })));
      if (!mounted) return;
      typesetTex([detail]);
    }
    // graphe : dessiné une fois la page en place, à la hauteur disponible (voir mountGraph)
    let svg = null, highlight = () => {}, zoomBy = () => {}, fit = () => {};
    // agrandi : le graphe occupe la fenêtre, l'article choisi reste à droite ; zoom par les boutons ou + − 0
    const grid = h("div", { class: "related-grid" });
    const tool = (icon, label, onclick) => h("button", { class: "icon-button", type: "button", title: label, "aria-label": label, html: icons[icon], onclick });
    const expand = tool("expand", t("related.expand"), () => setExpanded(!state.expanded));
    // agrandi : le reste de la page (couvert) devient inerte, le Tab reste dans le graphe et l'article choisi
    const covered = () => [document.querySelector(".topbar"), ...root.querySelectorAll(".related-page > :not(.related-result), .related-origin, .related-side")];
    const setExpanded = (on) => {
      state.expanded = on;
      grid.classList.toggle("expanded", on);
      document.body.classList.toggle("related-expanded", on);
      for (const element of covered()) element?.toggleAttribute("inert", on);
      expand.innerHTML = icons[on ? "shrink" : "expand"];
      expand.title = t(on ? "related.shrink" : "related.expand");
      expand.setAttribute("aria-label", expand.title);
      expand.setAttribute("aria-expanded", String(on));
      document.removeEventListener("keydown", keys);
      if (on) document.addEventListener("keydown", keys);
    };
    const zoomKeys = { "+": () => zoomBy(1.25), "=": () => zoomBy(1.25), "-": () => zoomBy(0.8), "0": () => fit() };
    const keys = (event) => {
      if (!grid.isConnected) { document.removeEventListener("keydown", keys); return; }
      // touche déjà prise (menu des dossiers), réglages ouverts, champ de saisie : rien ici
      if (event.defaultPrevented || document.querySelector("dialog[open]") || event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
      const act = event.key === "Escape" ? () => setExpanded(false) : zoomKeys[event.key];
      if (!act) return;
      event.preventDefault();
      act();
    };
    graphBox.addEventListener("keydown", (event) => { // non agrandi : + − 0 quand le graphe a le focus
      if (state.expanded || event.metaKey || event.ctrlKey || event.altKey || !zoomKeys[event.key]) return;
      event.preventDefault();
      zoomKeys[event.key]();
    });
    collapse = () => setExpanded(false);
    detachKeys = () => document.removeEventListener("keydown", keys);
    graphBox.append(h("div", { class: "related-graph-tools" },
      tool("zoomOut", t("related.zoomOut"), () => zoomBy(0.8)), tool("zoomIn", t("related.zoomIn"), () => zoomBy(1.25)),
      tool("fit", t("related.fit"), () => fit()), expand), h("div", { class: "related-legend" },
      // couleur selon l'année : barre du plus ancien au plus récent du graphe (comme shade, dans renderGraph)
      ...(() => {
        const years = graph.nodes.map((n) => n.year).filter(Boolean);
        if (years.length < 2 || Math.min(...years) === Math.max(...years)) {
          return [h("span", {}, h("i", { class: "legend-old" }), t("related.legend.old")), h("span", {}, h("i", { class: "legend-new" }), t("related.legend.new"))];
        }
        return [h("span", { class: "legend-years" }, h("b", {}, String(Math.min(...years))), h("i", { class: "legend-bar" }), h("b", {}, String(Math.max(...years))))];
      })(),
      h("span", {}, t("related.legend.size")), h("span", {}, h("i", { class: "legend-library" }), t("related.inLibrary"))));
    grid.append(h("div", { class: "related-side" }, tabs, list), graphBox, detail);
    result.replaceChildren(head, grid);
    setExpanded(state.expanded);
    drawList();
    drawDetail();
    // à côté de la liste, le graphe descend jusqu'au panneau de l'article choisi ; redessiné si la fenêtre change
    // assez de taille pour que cette hauteur change nettement
    let height = 0;
    const mountGraph = () => {
      const wanted = graphHeight(grid, graphBox, detail);
      if (svg && Math.abs(wanted - height) < 24) return;
      height = wanted;
      const old = svg;
      ({ svg, highlight, zoomBy, fit } = drawGraph(graph, chosen, select, height));
      old?.remove();
      graphBox.prepend(svg);
    };
    mountGraph();
    let resizing = 0, width = grid.clientWidth;
    new ResizeObserver(() => { // largeur seulement (le panneau de l'article choisi change de hauteur d'un article à l'autre)
      if (state.expanded || !grid.isConnected || grid.clientWidth === width) return;
      width = grid.clientWidth;
      cancelAnimationFrame(resizing);
      resizing = requestAnimationFrame(mountGraph);
    }).observe(grid);
    refreshMarks = () => { drawList(); drawDetail(); highlight(); };
    typesetTex([head]);
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
        const paper = await api.importPaper(n.arxivId, false, { title: n.title, authors: n.authors || [] });
        n.readId = paper.id;
        openRoute(`#/paper/${encodeURIComponent(paper.id)}`, newTab, event.shiftKey);
      } catch (error) { toastError(error); } finally { read.disabled = false; }
    });
    const add = h("button", { class: "button small-button", type: "button", disabled: Boolean(n.libraryId) },
      t(n.libraryId ? "related.inLibraryButton" : "feed.addToLibrary"));
    add.addEventListener("click", async () => {
      add.disabled = true;
      try {
        const paper = await api.quickAdd(n.arxivId, { title: n.title, authors: n.authors || [] });
        n.libraryId = n.readId = paper.id;
        toast(t("library.drop.addedLibrary", { title: shortTitle(n.title) }));
        refresh();
      } catch (error) { toastError(error); add.disabled = false; }
    });
    const file = h("button", { class: "button small-button", type: "button", "aria-haspopup": "menu", "aria-expanded": "false" }, h("span", { class: "button-icon", html: icons.folder }), t("select.file"));
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
      if (shown.builtAt) { // graphe non gardé (incomplet) : pas reconstruit pour si peu
        api.related(shown.origin.arxivId, false, true).then(({ graph: fresh }) => {
          if (!fresh || !mounted || state.graph !== shown || state.pending) return; // plus gardé : rien (pas de reconstruction cachée)
          if (fresh.builtAt !== shown.builtAt) { state.graph = fresh; draw(); return; }
          // même graphe : seules les marques changent (zoom, défilement et menus restent)
          const marks = new Map([fresh.origin, ...fresh.nodes, ...(fresh.prior || []), ...(fresh.derivative || [])].map((x) => [x.id, x]));
          for (const paper of [shown.origin, ...shown.nodes, ...(shown.prior || []), ...(shown.derivative || [])]) {
            const now = marks.get(paper.id);
            if (now) { paper.libraryId = now.libraryId; paper.readId = now.readId; }
          }
          refreshMarks?.();
        }, () => {});
      }
    }
  }
  return () => { mounted = false; collapse(); };
}

// lien avec l'article (graphe) ou avec le graphe (travaux antérieurs et dérivés)
const relation = (n) => (n.links ? tn(n.rel === "cite" ? "related.citesGraph" : "related.citedByGraph", n.links.length) : t(`related.rel.${n.rel}`));
const authorLine = (authors) => !authors?.length ? "" : authors.length > 3 ? `${authors.slice(0, 2).join(", ")} ${t("feed.etAl")}` : authors.join(", ");
const shortTitle = (title) => (title.length > 60 ? `${title.slice(0, 58)}…` : title);
const lastName = (name) => String(name || "").trim().split(/\s+/).at(-1) || "";

// Hauteur du graphe (unités du dessin, largeur W) : à côté de la liste, la place entre le haut de la liste et le
// panneau de l'article choisi ; en colonne unique (fenêtre étroite), la hauteur par défaut
function graphHeight(grid, box, detail) {
  if (getComputedStyle(grid).gridTemplateColumns.split(" ").length < 2 || !box.clientWidth) return H;
  const side = grid.querySelector(".related-side").getBoundingClientRect().height;
  const room = side - detail.getBoundingClientRect().height - parseFloat(getComputedStyle(grid).rowGap || 0) - 2; // bordures du cadre
  return Math.round(Math.min(1.1 * W, Math.max(H, W * room / box.clientWidth)));
}

// ---- Graphe : disposition par forces (rapprochés selon leur proximité), recentrée dans le cadre ----
function layout(graph, H) {
  const nodes = [{ ...graph.origin, id: "__origin", origin: true }, ...graph.nodes.map((n) => ({ ...n }))];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  // tous les liens ; la disposition ne suit que ceux dessinés d'emblée (les plus forts de chaque article)
  const links = graph.edges.map(([a, b, s, drawn = 1]) => ({ a: byId.get(a), b: byId.get(b), s, drawn: Boolean(drawn) })).filter((l) => l.a && l.b);
  const origin = byId.get("__origin");
  for (const n of nodes) if (!n.origin) links.push({ a: origin, b: n, s: Math.max(1, n.score / 10), drawn: true });
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
      if (!l.drawn) continue;
      const dx = l.b.x - l.a.x, dy = l.b.y - l.a.y, d = Math.hypot(dx, dy) || 1;
      const target = l.a.origin ? 320 / Math.sqrt(1 + l.s / 5) : 240 / Math.sqrt(1 + l.s / 4);
      const pull = (d - target) * 0.012 * cool;
      l.a.x += dx / d * pull; l.a.y += dy / d * pull; l.b.x -= dx / d * pull; l.b.y -= dy / d * pull;
    }
  }
  // nuage tourné pour que sa plus grande dimension soit horizontale (le cadre est plus large que haut)
  const mx = nodes.reduce((sum, n) => sum + n.x, 0) / nodes.length, my = nodes.reduce((sum, n) => sum + n.y, 0) / nodes.length;
  let sxx = 0, syy = 0, sxy = 0;
  for (const n of nodes) { const dx = n.x - mx, dy = n.y - my; sxx += dx * dx; syy += dy * dy; sxy += dx * dy; }
  const angle = -0.5 * Math.atan2(2 * sxy, sxx - syy), cos = Math.cos(angle), sin = Math.sin(angle);
  for (const n of nodes) { const dx = n.x - mx, dy = n.y - my; n.x = dx * cos - dy * sin; n.y = dx * sin + dy * cos; }
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  // recadré : le nuage, plutôt rond, est un peu étiré pour occuper le cadre, plus large que haut
  const fx = (W - 110) / Math.max(1, x1 - x0), fy = (H - 80) / Math.max(1, y1 - y0);
  const kx = Math.min(fx, 1.5 * fy), ky = Math.min(fy, 1.5 * fx);
  for (const n of nodes) {
    n.x = 55 + (n.x - x0) * kx + (W - 110 - (x1 - x0) * kx) / 2;
    n.y = 32 + (n.y - y0) * ky + (H - 80 - (y1 - y0) * ky) / 2;
  }
  return { nodes, links };
}

function drawGraph(graph, selected, select, H) {
  const { nodes, links } = layout(graph, H);
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.style.setProperty("--ratio", `${W} / ${H}`);
  svg.setAttribute("role", "group"); // les points restent atteignables (VoiceOver, clavier)
  svg.setAttribute("aria-label", t("related.graphLabel"));
  const el = (tag, attrs, parent) => { const e = document.createElementNS(SVG, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent?.append(e); return e; };
  const view = el("g", {}, svg);
  let zoom = 1, panX = 0, panY = 0;
  const years = graph.nodes.map((n) => n.year).filter(Boolean);
  const y0 = years.length ? Math.min(...years) : 2000, y1 = years.length ? Math.max(...years) : 2000;
  const shade = (n) => `color-mix(in srgb, var(--accent) ${Math.round(38 + 56 * (((n.year || y0) - y0) / Math.max(1, y1 - y0)))}%, var(--surface))`;
  const radius = (n) => (n.origin ? 14 : 5 + Math.min(28, Math.sqrt(n.citations || 0) * 0.58));
  // liens entre articles (ceux de l'article de départ ne sont pas dessinés : la distance les montre) ; les moins
  // forts n'apparaissent que pour l'article choisi
  const edgeLayer = el("g", {}, view);
  const lines = links.filter((l) => !l.a.origin).map((l) => ({ ...l, line: el("line", { x1: l.a.x, y1: l.a.y, x2: l.b.x, y2: l.b.y,
    class: `related-edge${l.drawn ? "" : " extra"}`, "stroke-width": Math.min(2.4, 0.4 + l.s / 12), "vector-effect": "non-scaling-stroke" }, edgeLayer) }));
  const groups = new Map();
  const labels = [];
  for (const n of nodes) {
    const g = el("g", { class: `related-node${n.origin ? " origin" : ""}${n.libraryId ? " in-library" : ""}`, tabindex: n.origin ? -1 : 0,
      ...(n.origin ? {} : { role: "button", "aria-label": `${plainTitle(n.title)}, ${n.year || "?"}` }) }, view);
    // couleur en style : WebKit (l'application) n'accepte pas var() dans l'attribut fill
    el("circle", { cx: n.x, cy: n.y, r: radius(n), style: `fill: ${n.origin ? "var(--accent)" : shade(n)}`, "vector-effect": "non-scaling-stroke" }, g);
    if (!n.origin) { // pastille cochée en haut à droite : discrète, sans se confondre avec le contour de l'article choisi
      const r = radius(n), bx = n.x + r * 0.75, by = n.y - r * 0.75, br = Math.max(3.6, r * 0.42);
      const badge = el("g", { class: "library-badge" }, g);
      el("circle", { cx: bx, cy: by, r: br, "vector-effect": "non-scaling-stroke" }, badge);
      el("path", { d: `M ${bx - br * 0.45} ${by} l ${br * 0.32} ${br * 0.35} l ${br * 0.6} ${-br * 0.7}`, style: `stroke-width: ${br * 0.32}` }, badge);
    }
    const label = el("text", { x: n.x, "text-anchor": "middle" }, g);
    label.textContent = `${lastName(n.authors?.[0])} ${n.year || ""}`.trim();
    labels.push({ n, label, g });
    el("title", {}, g).textContent = `${plainTitle(n.title)} (${n.year || "?"})`;
    if (!n.origin) {
      g.addEventListener("click", () => select(n.id, true));
      g.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(n.id, true); } });
    }
    groups.set(n.id, g);
  }
  // choisi (ou, pour un travail antérieur ou dérivé, les articles du graphe qui lui sont liés), et « dans la
  // bibliothèque » (un article ajouté depuis le panneau prend aussitôt son cercle)
  const highlight = () => {
    const chosen = selected();
    svg.classList.toggle("focus-links", Boolean(chosen.links)); // les autres articles s'effacent
    // liens de l'article choisi : tous, par-dessus les autres ; ses voisins ressortent
    const neighbours = new Set();
    for (const l of lines) {
      const on = !chosen.links && (l.a.id === chosen.id || l.b.id === chosen.id);
      l.line.classList.toggle("focus", on);
      if (on) { edgeLayer.append(l.line); neighbours.add(l.a.id === chosen.id ? l.b.id : l.a.id); }
    }
    for (const [id, g] of groups) {
      g.classList.toggle("selected", id === chosen.id);
      if (id !== "__origin") g.setAttribute("aria-pressed", String(id === chosen.id));
      g.classList.toggle("neighbour", neighbours.has(id));
      g.classList.toggle("linked", Boolean(chosen.links?.includes(id === "__origin" ? "origin" : id)));
      const paper = graph.nodes.find((n) => n.id === id);
      if (paper) g.classList.toggle("in-library", Boolean(paper.libraryId));
    }
    placeLabels();
  };
  // Noms : de taille fixe à l'écran (le zoom écarte les points, pas les lettres) ; un nom qui en chevaucherait un
  // autre, plus important, n'apparaît qu'au survol (sauf ceux de l'article de départ et du choisi). Ordre : l'article
  // de départ, le choisi, ses voisins, puis les plus cités.
  let placedAt = 0;
  function placeLabels(force = true) {
    const scale = zoom * (svg.getScreenCTM()?.a || 1); // pixels d'écran par unité du graphe
    if (!force && scale === placedAt) return;
    placedAt = scale;
    const kept = [];
    const rank = ({ n, g }) => (n.origin ? 4 : g.classList.contains("selected") ? 3 : g.classList.contains("neighbour") || g.classList.contains("linked") ? 2 : 0)
      * 1e6 + (n.citations || 0);
    const overlaps = (box) => kept.some((k) => box[0] < k[2] && k[0] < box[2] && box[1] < k[3] && k[1] < box[3]);
    for (const item of [...labels].sort((a, b) => rank(b) - rank(a))) {
      const { n, label } = item;
      const size = (n.origin ? 12 : 10) / scale;
      const r = radius(n), width = label.textContent.length * size * 0.58;
      label.style.fontSize = `${size}px`;
      // places essayées : dessous (l'habituelle), dessus, à droite, à gauche ; [x, ligne de base, ancrage, boîte]
      const places = [
        [n.x, n.y + r + size * 1.05, "middle"],
        [n.x, n.y - r - size * 0.35, "middle"],
        [n.x + r + size * 0.3, n.y + size * 0.35, "start"],
        [n.x - r - size * 0.3, n.y + size * 0.35, "end"],
      ].map(([x, y, anchor]) => {
        const left = anchor === "middle" ? x - width / 2 : anchor === "start" ? x : x - width;
        return [x, y, anchor, [left, y - size * 0.85, left + width, y + size * 0.2]];
      });
      const always = rank(item) >= 3e6; // l'article de départ, le choisi
      const place = places.find((p) => !overlaps(p[3])) || (always ? places[0] : null);
      const [x, y, anchor, box] = place || places[0]; // masquée (montrée au survol) : à sa place habituelle
      label.setAttribute("x", x);
      label.setAttribute("y", y);
      label.setAttribute("text-anchor", anchor);
      label.classList.toggle("minor", !place);
      if (place) kept.push(box);
    }
  }
  highlight(); // place aussi les noms
  // zoom au pincement (ou ⌘ + molette, ou boutons) et déplacement en faisant glisser le fond ; agrandi, le
  // défilement à deux doigts déplace aussi le graphe (la page, elle, ne défile plus)
  let labelsPending = false;
  const apply = () => {
    view.setAttribute("transform", `translate(${panX} ${panY}) scale(${zoom})`);
    if (!labelsPending) { labelsPending = true; requestAnimationFrame(() => { labelsPending = false; placeLabels(false); }); }
  };
  new ResizeObserver(() => placeLabels(false)).observe(svg); // affiché, agrandi, fenêtre redimensionnée
  // point de l'écran → coordonnées du graphe (le cadre agrandi garde les proportions, centré)
  const toView = (x, y) => { const m = svg.getScreenCTM(); return m ? new DOMPoint(x, y).matrixTransform(m.inverse()) : { x: W / 2, y: H / 2 }; };
  const unit = () => 1 / (svg.getScreenCTM()?.a || 1);
  const zoomAt = (mx, my, factor) => {
    const next = Math.min(6, Math.max(0.5, zoom * factor));
    panX = mx - (mx - panX) * (next / zoom); panY = my - (my - panY) * (next / zoom);
    zoom = next;
    apply();
  };
  const zoomBy = (factor) => zoomAt(W / 2, H / 2, factor);
  const fit = () => { zoom = 1; panX = panY = 0; apply(); };
  svg.addEventListener("wheel", (event) => {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      const p = toView(event.clientX, event.clientY);
      zoomAt(p.x, p.y, Math.exp(-event.deltaY * 0.0015));
    } else if (svg.closest(".expanded")) {
      event.preventDefault();
      panX -= event.deltaX * unit(); panY -= event.deltaY * unit();
      apply();
    }
  }, { passive: false });
  let drag = null;
  svg.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest(".related-node")) return;
    drag = { x: event.clientX, y: event.clientY, panX, panY };
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", (event) => {
    if (!drag) return;
    panX = drag.panX + (event.clientX - drag.x) * unit();
    panY = drag.panY + (event.clientY - drag.y) * unit();
    apply();
  });
  svg.addEventListener("pointerup", () => { drag = null; });
  svg.addEventListener("pointercancel", () => { drag = null; });
  svg.addEventListener("lostpointercapture", () => { drag = null; });
  svg.addEventListener("dblclick", (event) => { if (!event.target.closest(".related-node")) fit(); });
  return { svg, highlight, zoomBy, fit };
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
    const shown = papers.filter((p) => p.arxiv_id && words.every((w) => fold(`${p.title} ${p.authors.join(" ")}`).includes(w))).slice(0, 60);
    items.replaceChildren(...(shown.length ? shown.map((p) => h("button", { class: "related-menu-item", type: "button",
      onclick: () => { close(); choose(p.arxiv_id); } }, h("span", {}, plainTitle(p.title)), h("span", { class: "muted small" }, authorLine(p.authors))))
      : [h("p", { class: "muted small" }, t("library.noMatch"))]));
  };
  filter.addEventListener("input", fill);
  const menu = h("div", { class: "related-menu", role: "menu" }, filter, items);
  const close = () => { menu.remove(); document.removeEventListener("pointerdown", outside); };
  const outside = (event) => { if (!menu.contains(event.target) && !button.contains(event.target)) close(); };
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); button.focus(); } });
  fill();
  anchor.append(menu);
  document.addEventListener("pointerdown", outside);
  filter.focus();
}

let closeFolderMenu = null;
async function folderMenu(button, choose) {
  const wasOpen = Boolean(button.parentElement.querySelector(".related-folder-menu"));
  closeFolderMenu?.();
  if (wasOpen) return; // second clic sur le bouton : fermé
  let folders;
  try { folders = await api.folders(); } catch (error) { toastError(error); return; }
  const menu = h("div", { class: "related-menu related-folder-menu", role: "menu" },
    folders.length ? folders.map((folder) => h("button", { class: "related-menu-item", type: "button", role: "menuitem",
      onclick: () => { close(); choose(folder); } }, h("span", {}, folder.name)))
      : h("p", { class: "muted small" }, t("select.noFolder")));
  // au-dessus du bouton (en dessous s'il n'y a pas la place : fiche en haut du graphe agrandi), dans la fenêtre
  // (ni coupé en bas de la page, ni au bord droit) ; replacé si la page défile ou si la fenêtre change
  const place = () => {
    const gap = 6;
    const margin = 8;
    const rect = button.getBoundingClientRect();
    const above = rect.top - gap - margin;
    const below = innerHeight - rect.bottom - gap - margin;
    const up = menu.scrollHeight <= above || above >= below;
    menu.style.maxHeight = `${Math.max(120, Math.min(360, up ? above : below))}px`;
    menu.style.top = `${up ? rect.top - gap - menu.offsetHeight : rect.bottom + gap}px`;
    menu.style.left = `${Math.max(margin, Math.min(rect.left, innerWidth - menu.offsetWidth - margin))}px`;
  };
  const close = () => {
    menu.remove();
    button.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", outside);
    removeEventListener("scroll", place, true);
    removeEventListener("resize", place);
    closeFolderMenu = null;
  };
  const outside = (event) => { if (!menu.contains(event.target) && !button.contains(event.target)) close(); };
  menu.tabIndex = -1; // sans dossier (un simple message) : le menu prend le focus, Échap le ferme quand même
  menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); button.focus(); } });
  menu.addEventListener("focusout", (event) => { if (event.relatedTarget && !menu.contains(event.relatedTarget) && event.relatedTarget !== button) close(); });
  closeFolderMenu = close;
  button.parentElement.append(menu); // à côté du bouton : dans le graphe agrandi aussi (fenêtre de premier plan)
  place();
  addEventListener("scroll", place, true);
  addEventListener("resize", place);
  document.addEventListener("pointerdown", outside);
  button.setAttribute("aria-expanded", "true");
  (menu.querySelector("button") || menu).focus();
}
