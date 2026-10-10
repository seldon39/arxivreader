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

// Disposition en couches (méthode de Sugiyama, simplifiée) :
// 1. couche = plus long chemin depuis les énoncés de base ; un énoncé de base descend juste au-dessus
//    de son premier utilisateur ;
// 2. une flèche qui saute des couches passe par des points intermédiaires (elle contourne les boîtes) ;
// 3. ordre dans chaque couche : ordre de l'article, puis barycentre des voisins (moins de croisements) ;
// 4. abscisses : chaque boîte se place sous la moyenne de ses voisins, sans chevauchement.
function layout(nodes, byId) {
  const shown = new Set(nodes.map((n) => n.id));
  const uses = (id) => byId.get(id).uses.filter((u) => shown.has(u) && u !== id);
  const users = (id) => byId.get(id).usedBy.filter((u) => shown.has(u) && u !== id);
  const layer = new Map();
  const visiting = new Set();
  const depth = (id) => {
    if (layer.has(id)) return layer.get(id);
    if (visiting.has(id)) return 0; // cycle de renvois : on l'ignore
    visiting.add(id);
    const list = uses(id);
    const value = list.length ? 1 + Math.max(...list.map(depth)) : 0;
    visiting.delete(id);
    layer.set(id, value);
    return value;
  };
  nodes.forEach((n) => depth(n.id));
  // énoncés de base utilisés seulement plus bas : rapprochés de leur premier utilisateur
  for (const node of [...nodes].reverse()) {
    if (uses(node.id).length) continue;
    const below = users(node.id).map((u) => layer.get(u)).filter((l) => l > layer.get(node.id));
    if (below.length) layer.set(node.id, Math.min(...below) - 1);
  }

  // Graphe de travail : arêtes entre couches voisines, avec points intermédiaires
  const docIndex = new Map(nodes.map((n, i) => [n.id, i]));
  const items = new Map(nodes.map((n) => [n.id, { id: n.id, layer: layer.get(n.id), up: [], down: [], width: NODE_W, order: docIndex.get(n.id) }]));
  const chains = [];
  for (const node of nodes) {
    for (const used of uses(node.id)) {
      const from = layer.get(used);
      const to = layer.get(node.id);
      if (to <= from) { chains.push({ from: used, to: node.id, points: [], back: true }); continue; }
      const points = [];
      let previous = used;
      for (let l = from + 1; l < to; l++) {
        const id = `~${used}>${node.id}@${l}`;
        items.set(id, { id, layer: l, up: [], down: [], width: 10, dummy: true,
          order: (docIndex.get(used) + docIndex.get(node.id)) / 2 });
        items.get(previous).down.push(id);
        items.get(id).up.push(previous);
        points.push(id);
        previous = id;
      }
      items.get(previous).down.push(node.id);
      items.get(node.id).up.push(previous);
      chains.push({ from: used, to: node.id, points });
    }
  }

  const layers = [];
  for (const item of items.values()) (layers[item.layer] ||= []).push(item);
  layers.forEach((row) => row.sort((a, b) => a.order - b.order));
  const position = new Map();
  const index = () => layers.forEach((row) => row?.forEach((item, i) => position.set(item.id, i)));
  index();
  const crossings = () => {
    let total = 0;
    for (const row of layers) {
      if (!row) continue;
      const edges = row.flatMap((item) => item.down.map((d) => [position.get(item.id), position.get(d)]));
      for (let i = 0; i < edges.length; i++) {
        for (let j = i + 1; j < edges.length; j++) {
          if ((edges[i][0] - edges[j][0]) * (edges[i][1] - edges[j][1]) < 0) total++;
        }
      }
    }
    return total;
  };
  let best = layers.map((row) => row && [...row]);
  let fewest = crossings();
  for (let pass = 0; pass < 12; pass++) {
    const down = pass % 2 === 0;
    const sequence = layers.map((_, i) => (down ? i : layers.length - 1 - i));
    for (const i of sequence) {
      const row = layers[i];
      if (!row) continue;
      const key = down ? "up" : "down";
      const score = new Map(row.map((item, j) => {
        const around = item[key].map((n) => position.get(n));
        return [item.id, around.length ? around.reduce((s, x) => s + x, 0) / around.length : j];
      }));
      row.sort((a, b) => score.get(a.id) - score.get(b.id));
      index();
    }
    const count = crossings();
    if (count < fewest) { fewest = count; best = layers.map((row) => row && [...row]); }
  }
  best.forEach((row, i) => { layers[i] = row; });
  index();

  // Abscisses : sous la moyenne des voisins, en gardant l'ordre et l'écart minimal
  const x = new Map();
  const gap = (a, b) => (a.width + b.width) / 2 + (a.dummy || b.dummy ? 8 : GAP_X);
  layers.forEach((row) => {
    let cursor = 0;
    row?.forEach((item, i) => { cursor += i ? gap(row[i - 1], item) : item.width / 2; x.set(item.id, cursor); });
  });
  const place = (row, desired) => {
    const n = row.length;
    const left = new Array(n);
    for (let i = 0; i < n; i++) left[i] = i ? Math.max(desired[i], left[i - 1] + gap(row[i - 1], row[i])) : desired[i];
    const right = new Array(n);
    for (let i = n - 1; i >= 0; i--) right[i] = i < n - 1 ? Math.min(desired[i], right[i + 1] - gap(row[i], row[i + 1])) : desired[i];
    row.forEach((item, i) => x.set(item.id, (left[i] + right[i]) / 2));
    for (let i = 1; i < n; i++) x.set(row[i].id, Math.max(x.get(row[i].id), x.get(row[i - 1].id) + gap(row[i - 1], row[i])));
  };
  for (let pass = 0; pass < 8; pass++) {
    const down = pass % 2 === 0;
    const sequence = layers.map((_, i) => (down ? i : layers.length - 1 - i));
    for (const i of sequence) {
      const row = layers[i];
      if (!row) continue;
      const key = down ? "up" : "down";
      place(row, row.map((item) => {
        const around = [...item[key], ...(pass > 3 ? item[down ? "down" : "up"] : [])].map((n) => x.get(n));
        return around.length ? around.reduce((s, v) => s + v, 0) / around.length : x.get(item.id);
      }));
    }
  }
  const lefts = [...items.values()].map((item) => x.get(item.id) - item.width / 2);
  const rights = [...items.values()].map((item) => x.get(item.id) + item.width / 2);
  const shift = MARGIN - Math.min(...lefts);
  const width = Math.max(...rights) + shift + MARGIN;
  const rowY = (l) => MARGIN + l * (NODE_H + GAP_Y);
  const coords = new Map(nodes.map((n) => [n.id, { x: x.get(n.id) + shift - NODE_W / 2, y: rowY(layer.get(n.id)) }]));
  const waypoints = new Map([...items.values()].filter((i) => i.dummy).map((i) => [i.id, { x: x.get(i.id) + shift, y: rowY(i.layer) }]));
  const height = MARGIN * 2 + layers.length * NODE_H + (layers.length - 1) * GAP_Y;
  return { coords, waypoints, chains, width, height };
}

// Remarques, exemples, notations : affichés seulement si un autre énoncé s'en sert (ou sur demande)
const SIDE_KINDS = new Set(["remark", "example", "notation", "other"]);

// Disposition mémorisée : elle ne dépend pas de l'énoncé choisi, seulement des énoncés et des options
// (une nouvelle structure, après « Indiquer la preuve », est un autre tableau : nouveau calcul).
const layouts = new WeakMap();

function cachedLayout(allNodes, byId, showIsolated, showRemarks) {
  if (!layouts.has(allNodes)) layouts.set(allNodes, new Map());
  const cache = layouts.get(allNodes);
  const key = `${showIsolated}|${showRemarks}`;
  if (!cache.has(key)) {
    const visible = allNodes.filter((n) => showRemarks || !SIDE_KINDS.has(n.kind) || n.usedBy.some((u) => !SIDE_KINDS.has(byId.get(u)?.kind)));
    const kept = new Set(visible.map((n) => n.id));
    const linked = (n) => n.uses.some((u) => kept.has(u)) || n.usedBy.some((u) => kept.has(u));
    const nodes = visible.filter((n) => showIsolated || linked(n));
    cache.set(key, { nodes, ...(nodes.length ? layout(nodes, byId) : {}) });
  }
  return cache.get(key);
}

export function renderGraph(allNodes, { selected = null, showIsolated = false, showRemarks = false, onSelect = () => {} } = {}) {
  const byId = new Map(allNodes.map((n) => [n.id, n]));
  const { nodes, coords, waypoints, chains, width, height } = cachedLayout(allNodes, byId, showIsolated, showRemarks);
  if (!nodes.length) return null;

  const ancestors = selected ? closure(byId, selected, "uses") : new Set();
  const descendants = selected ? closure(byId, selected, "usedBy") : new Set();
  const root = svg("svg", {
    class: `graph${selected && coords.has(selected) ? " focus" : ""}`,
    width, height, viewBox: `0 0 ${width} ${height}`, role: "group", "aria-label": t("tabs.graph"),
  });
  // une pointe par couleur de flèche (normale, chaîne de dépendances, conséquences)
  const marker = (name) => svg("marker", { id: `arrow-${name}`, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" },
    svg("path", { d: "M0 0 10 5 0 10z", class: `arrow-head ${name}` }));
  root.append(svg("defs", {}, marker("plain"), marker("up"), marker("down")));

  const edges = svg("g", {});
  for (const chain of chains) {
    const a = coords.get(chain.from);
    const b = coords.get(chain.to);
    const node = byId.get(chain.to);
    // points de passage : bas de la source, chaque point intermédiaire (traversée verticale de sa couche), haut de la cible
    const points = [[a.x + NODE_W / 2, a.y + NODE_H]];
    for (const id of chain.points) {
      const p = waypoints.get(id);
      points.push([p.x, p.y], [p.x, p.y + NODE_H]);
    }
    points.push([b.x + NODE_W / 2, b.y - 2]);
    let d = `M${points[0][0]} ${points[0][1]}`;
    for (let i = 1; i < points.length; i++) {
      const [x1, y1] = points[i - 1];
      const [x2, y2] = points[i];
      if (x1 === x2 && i % 2 === 0 && chain.points.length) { d += ` L${x2} ${y2}`; continue; }
      const bend = Math.max(12, (y2 - y1) / 2);
      d += ` C${x1} ${y1 + bend} ${x2} ${y2 - bend} ${x2} ${y2}`;
    }
    let cls = "edge";
    if (node.weakUses?.includes(chain.from)) cls += " weak";
    if (selected && (chain.to === selected || ancestors.has(chain.to)) && ancestors.has(chain.from)) cls += " up";
    if (selected && (chain.from === selected || descendants.has(chain.from)) && descendants.has(chain.to)) cls += " down";
    const head = cls.includes(" up") ? "up" : cls.includes(" down") ? "down" : "plain";
    edges.append(svg("path", { class: cls, d, "marker-end": `url(#arrow-${head})` }));
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
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(node.id); }
    });
    root.append(group);
  }
  return root;
}
