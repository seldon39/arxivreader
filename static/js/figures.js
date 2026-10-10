// Figures TikZ (SVG de LaTeXML) : cadre blanc aux bords arrondis, couleurs d'origine.
// Les étiquettes (<foreignObject>) sont recalculées et posées en HTML au-dessus du dessin :
// Safari ignore les transformations SVG pour ce contenu et empile toutes les étiquettes dans un coin.

const PADDING = 14;

// Matrice cumulée d'un élément jusqu'au <svg> (attributs transform), en unités du viewBox.
function matrixTo(svg, element) {
  let matrix = new DOMMatrix();
  for (let node = element; node && node !== svg; node = node.parentNode) {
    const own = node.transform?.baseVal?.consolidate?.();
    if (own) matrix = new DOMMatrix([own.matrix.a, own.matrix.b, own.matrix.c, own.matrix.d, own.matrix.e, own.matrix.f]).multiply(matrix);
  }
  return matrix;
}

function viewBoxScale(svg) {
  const box = svg.viewBox?.baseVal;
  const width = svg.getBoundingClientRect().width;
  return box && box.width ? width / box.width : 1;
}

function layoutFigure(card) {
  const svg = card.querySelector(":scope > svg");
  const labels = card.querySelectorAll(":scope > .figure-label");
  const scale = viewBoxScale(svg);
  const box = svg.viewBox?.baseVal;
  let minX = 0;
  let minY = 0;
  let maxX = svg.getBoundingClientRect().width;
  let maxY = svg.getBoundingClientRect().height;
  for (const label of labels) {
    const { x, y, w, h } = label.geometry;
    const corners = [[x, y], [x + w, y], [x, y + h], [x + w, y + h]].map(([px, py]) => label.matrix.transformPoint(new DOMPoint(px, py)));
    const left = (Math.min(...corners.map((p) => p.x)) - (box?.x || 0)) * scale;
    const top = (Math.min(...corners.map((p) => p.y)) - (box?.y || 0)) * scale;
    label.style.left = `${left}px`;
    label.style.top = `${top}px`;
    label.style.fontSize = `${label.basePx * scale}px`;
    minX = Math.min(minX, left);
    minY = Math.min(minY, top);
    maxX = Math.max(maxX, left + label.offsetWidth);
    maxY = Math.max(maxY, top + label.offsetHeight);
  }
  // Le cadre englobe les étiquettes qui dépassent du dessin
  const extra = { left: -minX, top: -minY, right: maxX - svg.getBoundingClientRect().width, bottom: maxY - svg.getBoundingClientRect().height };
  card.style.padding = ["top", "right", "bottom", "left"].map((side) => `${PADDING + Math.max(0, Math.ceil(extra[side]))}px`).join(" ");
  card.style.setProperty("--shift-x", `${PADDING + Math.max(0, Math.ceil(extra.left))}px`);
  card.style.setProperty("--shift-y", `${PADDING + Math.max(0, Math.ceil(extra.top))}px`);
}

export function prepareFigures(root) {
  const cards = [];
  for (const svg of root.querySelectorAll("svg.ltx_picture")) {
    const card = document.createElement("span");
    card.className = "figure-card";
    // Dessin centré dans la source (\centering, diagramme seul sur sa ligne) : bloc centré, sans retrait
    // de paragraphe, même quand LaTeXML l'a placé dans une boîte en ligne (ltx_inline-block)
    if (svg.classList.contains("ltx_centering")) {
      card.classList.add("centered");
      const box = svg.parentElement?.closest(".ltx_inline-block");
      const alone = box && [...box.childNodes].every((node) => node.contains?.(svg) || (node.nodeType === 3 && !node.data.trim()));
      if (alone) box.classList.add("figure-block");
    }
    svg.replaceWith(card);
    card.append(svg);
    for (const fo of svg.querySelectorAll("foreignObject")) {
      const label = document.createElement("span");
      label.className = "figure-label";
      label.matrix = matrixTo(svg, fo);
      label.geometry = {
        x: fo.x?.baseVal?.value || 0,
        y: fo.y?.baseVal?.value || 0,
        w: fo.width?.baseVal?.value || 0,
        h: fo.height?.baseVal?.value || 0,
      };
      const size = (fo.getAttribute("style") || "").match(/font-size:\s*([\d.]+)pt/);
      label.basePx = size ? parseFloat(size[1]) * (4 / 3) : 13.33;
      label.append(...fo.childNodes);
      fo.remove();
      card.append(label);
    }
    cards.push(card);
  }
  return cards;
}

export function layoutFigures(cards) {
  // Deux passes : l'agrandissement du cadre peut réduire l'échelle du dessin dans une colonne étroite
  for (let pass = 0; pass < 2; pass++) cards.forEach((card) => { if (card.isConnected) layoutFigure(card); });
}
