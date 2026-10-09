// Graphe des dépendances : disposition en couches (les énoncés de base en haut).

import { getLanguage, t } from "./i18n.js";

const SVG = "http://www.w3.org/2000/svg";
const NODE_W = 92;
const NODE_H = 28;
const GAP_X = 16;
const GAP_Y = 58;
const MARGIN = 20;

const ABBREVIATIONS = {
  fr: { theorem: "Th.", proposition: "Prop.", lemma: "Lem.", corollary: "Cor.", definition: "Déf.", conjecture: "Conj.",
    claim: "Ass.", remark: "Rem.", example: "Ex.", notation: "Not.", problem: "Q.", other: "" },
  en: { theorem: "Thm", proposition: "Prop", lemma: "Lem", corollary: "Cor", definition: "Def", conjecture: "Conj",
    claim: "Claim", remark: "Rem", example: "Ex", notation: "Not", problem: "Q", other: "" },
};

export function shortLabel(node) {
  const number = node.label.match(/(\d+(?:\.\d+)*[a-z]?|[A-Z](?:\.\d+)+)\s*$/);
  if (!number) return node.label.length > 12 ? `${node.label.slice(0, 11)}…` : node.label;
  const abbreviation = ABBREVIATIONS[getLanguage()][node.kind] ?? "";
  return `${abbreviation} ${number[1]}`.trim();
}

// Fermeture transitive le long de « uses » (dépendances) ou « usedBy » (conséquences).
export function closure(byId, startId, key) {
  const seen = new Set();
  const stack = [...(byId.get(startId)?.[key] || [])];
  while (stack.length) {
    const id = stack.pop();
    if (seen.has(id) || id === startId || !byId.has(id)) continue;
    seen.add(id);
    stack.push(...byId.get(id)[key]);
  }
  return seen;
}

function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  children.forEach((child) => el.append(child));
  return el;
}

function layout(nodes, byId) {
  const shown = new Set(nodes.map((n) => n.id));
  const layer = new Map();
  const visiting = new Set();
  const depth = (id) => {
    if (layer.has(id)) return layer.get(id);
    if (visiting.has(id)) return 0; // cycle de renvois : on l'ignore
    visiting.add(id);
    const uses = byId.get(id).uses.filter((u) => shown.has(u));
    const value = uses.length ? 1 + Math.max(...uses.map(depth)) : 0;
    visiting.delete(id);
    layer.set(id, value);
    return value;
  };
  nodes.forEach((n) => depth(n.id));

  const layers = [];
  nodes.forEach((n) => { (layers[layer.get(n.id)] ||= []).push(n.id); });
  // Ordre dans chaque couche : barycentre des voisins, pour limiter les croisements
  const position = new Map();
  const index = () => layers.forEach((ids) => ids.forEach((id, i) => position.set(id, i)));
  index();
  const barycenter = (id, key, fallback) => {
    const neighbours = byId.get(id)[key].filter((n) => position.has(n));
    return neighbours.length ? neighbours.reduce((s, n) => s + position.get(n), 0) / neighbours.length : fallback;
  };
  for (let pass = 0; pass < 4; pass++) {
    const down = pass % 2 === 0;
    const order = down ? layers.map((_, i) => i) : layers.map((_, i) => layers.length - 1 - i);
    for (const i of order) {
      if (!layers[i]) continue;
      const key = down ? "uses" : "usedBy";
      const scored = layers[i].map((id, j) => [id, barycenter(id, key, j)]);
      scored.sort((a, b) => a[1] - b[1]);
      layers[i] = scored.map(([id]) => id);
      index();
    }
  }

  const widest = Math.max(...layers.map((ids) => ids.length));
  const width = MARGIN * 2 + widest * NODE_W + (widest - 1) * GAP_X;
  const coords = new Map();
  layers.forEach((ids, row) => {
    const rowWidth = ids.length * NODE_W + (ids.length - 1) * GAP_X;
    const start = (width - rowWidth) / 2;
    ids.forEach((id, i) => coords.set(id, { x: start + i * (NODE_W + GAP_X), y: MARGIN + row * (NODE_H + GAP_Y) }));
  });
  const height = MARGIN * 2 + layers.length * NODE_H + (layers.length - 1) * GAP_Y;
  return { coords, width, height };
}

export function renderGraph(allNodes, { selected = null, showIsolated = false, onSelect = () => {} } = {}) {
  const byId = new Map(allNodes.map((n) => [n.id, n]));
  const nodes = allNodes.filter((n) => showIsolated || n.uses.length || n.usedBy.length);
  if (!nodes.length) return null;
  const { coords, width, height } = layout(nodes, byId);

  const ancestors = selected ? closure(byId, selected, "uses") : new Set();
  const descendants = selected ? closure(byId, selected, "usedBy") : new Set();
  const root = svg("svg", {
    class: `graph${selected && coords.has(selected) ? " focus" : ""}`,
    width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": t("tabs.graph"),
  });
  root.append(svg("defs", {},
    svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" },
      svg("path", { d: "M0 0 10 5 0 10z", fill: "currentColor", class: "arrow-head" }))));

  const edges = svg("g", {});
  for (const node of nodes) {
    for (const used of node.uses) {
      if (!coords.has(used)) continue;
      const a = coords.get(used);
      const b = coords.get(node.id);
      const x1 = a.x + NODE_W / 2;
      const y1 = a.y + NODE_H;
      const x2 = b.x + NODE_W / 2;
      const y2 = b.y;
      const bend = Math.max(24, Math.abs(y2 - y1) / 2);
      let cls = "edge";
      if (selected && (node.id === selected || ancestors.has(node.id)) && (ancestors.has(used))) cls += " up";
      if (selected && (used === selected || descendants.has(used)) && descendants.has(node.id)) cls += " down";
      edges.append(svg("path", { class: cls, d: `M${x1} ${y1} C${x1} ${y1 + bend} ${x2} ${y2 - bend} ${x2} ${y2 - 2}`, "marker-end": "url(#arrow)" }));
    }
  }
  root.append(edges);

  for (const node of nodes) {
    const { x, y } = coords.get(node.id);
    let cls = "node";
    if (node.id === selected) cls += " selected";
    else if (ancestors.has(node.id) || descendants.has(node.id)) cls += " related";
    const group = svg("g", { class: cls, "data-kind": node.kind, transform: `translate(${x} ${y})`, tabindex: 0, role: "button" },
      svg("title", {}, document.createTextNode(node.name ? `${node.label} — ${node.name}` : node.label)),
      svg("rect", { width: NODE_W, height: NODE_H, rx: 6 }),
      svg("text", { x: NODE_W / 2, y: NODE_H / 2 + 4, "text-anchor": "middle" }, document.createTextNode(shortLabel(node))));
    group.addEventListener("click", () => onSelect(node.id));
    group.addEventListener("keydown", (event) => { if (event.key === "Enter") onSelect(node.id); });
    root.append(group);
  }
  return root;
}
