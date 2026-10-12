// Figures TikZ (SVG de LaTeXML) : cadre blanc aux bords arrondis, couleurs d'origine.
// Les étiquettes (<foreignObject>) sont recalculées et posées en HTML au-dessus du dessin :
// Safari ignore les transformations SVG pour ce contenu et empile toutes les étiquettes dans un coin.
// MathJax ne compose pas les formules aux dimensions de TeX (plus hautes, parfois plus larges) : chaque
// étiquette est posée sur la ligne de base prévue par LaTeX, centrée dans sa boîte, et réduite si elle
// est plus large que la boîte, pour ne pas chevaucher les flèches et les points voisins.

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
    const boxLeft = (Math.min(...corners.map((p) => p.x)) - (box?.x || 0)) * scale;
    const boxTop = (Math.min(...corners.map((p) => p.y)) - (box?.y || 0)) * scale;
    const boxWidth = (Math.max(...corners.map((p) => p.x)) - Math.min(...corners.map((p) => p.x))) * scale;
    const boxHeight = (Math.max(...corners.map((p) => p.y)) - Math.min(...corners.map((p) => p.y))) * scale;
    let fontSize = label.basePx * scale;
    label.style.fontSize = `${fontSize}px`;
    let left = boxLeft;
    let top = boxTop;
    const { metrics } = label;
    if (metrics && boxWidth > 0) {
      // plus large que la boîte de TeX : réduite d'autant (au plus de moitié)
      const width = label.offsetWidth;
      if (width > boxWidth * 1.02) {
        fontSize *= Math.max(0.5, boxWidth / width);
        label.style.fontSize = `${fontSize}px`;
      }
      left = boxLeft + (boxWidth - label.offsetWidth) / 2;
      // ligne de base : à la hauteur prévue par TeX (hauteur / (hauteur + profondeur) de la boîte)
      const baseline = boxTop + boxHeight * (metrics.height / (metrics.height + metrics.depth || 1));
      top = baseline - label.baselineMark.offsetTop;
    }
    // étiquette tournée (« \\subset » vertical d'un diagramme…) : centrée sur sa boîte, tournée du même angle
    const { a, b } = label.matrix;
    if (Math.abs(b) > 1e-6) {
      label.style.rotate = `${Math.atan2(b, a)}rad`;
      left = boxLeft + (boxWidth - label.offsetWidth) / 2;
      top = boxTop + (boxHeight - label.offsetHeight) / 2;
    }
    label.style.left = `${left}px`;
    label.style.top = `${top}px`;
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

// Dessins que LaTeXML n'a pas su convertir (PSTricks… : une vraie taille, des étiquettes peut-être, mais rien de
// dessiné : ni trait ni image). À appeler sur le HTML d'origine, avant prepareFigures (qui sort étiquettes et
// images du dessin). Le dessin vide de taille nulle que LaTeXML met juste avant est compté avec lui.
const DRAWING = "path, line, rect, circle, ellipse, polygon, polyline, image, use, img";
export function emptyPictures(root) {
  // span.ltx_picture : environnement picture ou PSTricks ; les dessins TikZ (svg.ltx_picture), faits parfois de
  // seuls nœuds (texte sans trait), ne sont pas concernés
  return [...root.querySelectorAll(".ltx_picture:not(svg)")].filter((picture) => {
    const svg = picture.querySelector("svg");
    if (!svg || picture.querySelector(DRAWING)) return false;
    return Number(svg.getAttribute("width")) > 20 && Number(svg.getAttribute("height")) > 20;
  }).map((picture) => {
    const before = picture.previousElementSibling;
    const beforeSvg = before?.matches("svg") ? before : before?.querySelector("svg");
    const twin = before?.matches(".ltx_picture") && beforeSvg && !before.querySelector(DRAWING) && !Number(beforeSvg.getAttribute("width"));
    return twin ? [before, picture] : [picture];
  });
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
      // seul dans sa boîte (aucun texte à côté), et pas une minipage ni une boîte de largeur fixée (côte à côte)
      const textOutside = box && box.textContent.replace(svg.textContent, "").trim();
      const sideBySide = box && (box.closest(".ltx_minipage") || /width/.test(box.getAttribute("style") || ""));
      if (box && !textOutside && !sideBySide) box.classList.add("figure-block");
    }
    // dans une cellule d'équation (diagramme numéroté) : la cellule prend la largeur de son
    // contenu, une largeur en % du dessin y vaudrait 0 ; le dessin garde sa largeur, bornée par la colonne
    if (svg.closest(".ltx_eqn_cell")) card.classList.add("in-cell");
    svg.replaceWith(card);
    card.append(svg);
    for (const fo of svg.querySelectorAll("foreignObject")) {
      const label = document.createElement("span");
      label.className = "figure-label";
      label.matrix = matrixTo(svg, fo);
      // Cellule d'un diagramme (tikz-cd, matrice TikZ) : LaTeXML compte deux fois la hauteur du texte
      // (ligne de la matrice déjà posée sur la ligne de base, plus la translation propre à l'étiquette) ;
      // les nœuds remontent alors vers les flèches qui en partent. On retire cette translation.
      const own = fo.transform?.baseVal?.consolidate?.()?.matrix;
      if (fo.closest(".ltx_tikzmatrix_col") && own && own.a === 1 && own.b === 0 && own.c === 0 && own.d === -1 && own.e === 0) {
        label.matrix = label.matrix.multiply(new DOMMatrix().translate(0, own.f));
      }
      label.geometry = {
        x: fo.x?.baseVal?.value || 0,
        y: fo.y?.baseVal?.value || 0,
        w: fo.width?.baseVal?.value || 0,
        h: fo.height?.baseVal?.value || 0,
      };
      const style = fo.getAttribute("style") || "";
      const size = style.match(/font-size:\s*([\d.]+)pt/);
      label.basePx = size ? parseFloat(size[1]) * (4 / 3) : 13.33;
      // dimensions de l'étiquette selon TeX (en em) ; seulement pour une étiquette ni tournée ni inclinée
      const em = (name) => parseFloat(style.match(new RegExp(`--ltx-fo-${name}:\\s*([\\d.]+)em`))?.[1]);
      const upright = Math.abs(label.matrix.b) < 1e-6 && Math.abs(label.matrix.c) < 1e-6;
      if (upright && em("height") >= 0 && em("depth") >= 0) label.metrics = { height: em("height"), depth: em("depth") };
      label.append(...fo.childNodes);
      // repère de hauteur nulle posé sur la ligne de base du texte
      label.baselineMark = document.createElement("span");
      label.baselineMark.className = "figure-baseline";
      label.append(label.baselineMark);
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

// ---------- Figure en grand ----------
// Fenêtre superposée (<dialog> modal) : l'article reste visible, grisé, derrière ; × , Échap ou un clic
// sur le fond la ferment. Un dessin TikZ est recopié puis remis en page à la nouvelle taille
// (ses étiquettes HTML sont recalculées) ; une image est simplement agrandie.
export function openFigure(target, { closeLabel = "Close", onLink = null } = {}) {
  const opener = document.activeElement;
  const figure = target.closest(".ltx_figure");
  const caption = figure?.querySelector(".ltx_caption");
  const frame = document.createElement("div");
  frame.className = "figure-viewer-frame";
  let card = null;
  if (target.matches(".figure-card")) {
    card = target.cloneNode(true);
    // propriétés de mise en page des étiquettes (non recopiées par cloneNode)
    const originals = target.querySelectorAll(":scope > .figure-label");
    card.querySelectorAll(":scope > .figure-label").forEach((label, i) => {
      Object.assign(label, { matrix: originals[i].matrix, geometry: originals[i].geometry, basePx: originals[i].basePx,
        metrics: originals[i].metrics, baselineMark: label.querySelector(":scope > .figure-baseline") });
    });
    card.classList.remove("centered");
    frame.append(card);
  } else {
    const image = target.cloneNode(true);
    image.removeAttribute("width");
    image.removeAttribute("height");
    image.className = "figure-viewer-image";
    frame.append(image);
  }
  const close = document.createElement("button");
  close.type = "button";
  close.className = "icon-button figure-viewer-close";
  close.title = closeLabel;
  close.setAttribute("aria-label", closeLabel);
  close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.4 5 12 10.6 17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4L12 13.4 6.4 19 5 17.6 10.6 12 5 6.4z"/></svg>';
  const dialog = document.createElement("dialog");
  dialog.className = "figure-viewer";
  dialog.setAttribute("aria-label", caption?.textContent.trim().slice(0, 120) || closeLabel);
  dialog.append(close, frame);
  if (caption) {
    const text = caption.cloneNode(true);
    text.classList.add("figure-viewer-caption");
    dialog.append(text);
  }
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close(); // clic sur le fond
    else if (event.target.closest?.('a[href^="#"]')) { // renvoi dans la légende ou une étiquette : on y va
      dialog.close();
      onLink?.(event);
    }
  });
  dialog.addEventListener("keydown", (event) => { if (event.key === "Escape") event.stopPropagation(); }); // ferme la figure seule
  dialog.addEventListener("close", () => {
    removeEventListener("resize", fit);
    dialog.remove();
    opener?.focus?.({ preventScroll: true });
  });
  document.body.append(dialog);
  dialog.showModal();
  close.focus();

  // dessin agrandi pour tenir dans la fenêtre (marges, légende et bouton compris)
  function fit() {
    if (!card) return;
    const svg = card.querySelector(":scope > svg");
    const box = svg.viewBox?.baseVal;
    if (!box?.width) return;
    // place disponible (marges du cadre et étiquettes qui débordent du dessin comprises)
    const room = { w: Math.min(innerWidth * 0.96, 1480) - 130, h: innerHeight * 0.92 - 150 - (caption ? 60 : 0) };
    const width = Math.max(120, Math.min(room.w, (room.h * box.width) / box.height));
    svg.style.width = `${Math.round(width)}px`;
    svg.style.maxWidth = "none";
    layoutFigures([card]);
    // étiquettes débordant du dessin : le cadre s'élargit ; s'il dépasse la fenêtre, le dessin rétrécit d'autant
    const over = card.getBoundingClientRect().width - frame.clientWidth;
    if (over > 0) {
      svg.style.width = `${Math.max(100, Math.round(width - over - 2))}px`;
      layoutFigures([card]);
    }
  }
  fit();
  addEventListener("resize", fit);
}
