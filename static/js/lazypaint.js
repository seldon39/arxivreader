// Sections loin de l'écran : pas dessinées (content-visibility: hidden). Sans cela, WebKit garde l'image de tout ce qui
// a déjà défilé et ne la libère pas quand elle contient des formules : jusqu'à 3 Go pour un long article, moins d'un
// tiers ainsi. Une section est de nouveau dessinée un écran avant d'arriver dans la fenêtre (en haut comme en bas) :
// rien n'apparaît en retard en défilant, sans tout garder pour autant.
// Une section masquée garde la hauteur exacte mesurée juste avant : la mise en page ne change pas (positions,
// sommaire, recherche, passage au PDF). Avant une nouvelle mise en page (taille du texte, largeur, volets, texte caché
// révélé), tout est réaffiché le temps du calcul (relayout), puis les hauteurs sont reprises.

const SECTIONS = ":is(.ltx_chapter, .ltx_section, .ltx_appendix, .ltx_bibliography, .ltx_subsection)";
const AHEAD = "100%"; // avance : un écran au-dessus et au-dessous de la fenêtre

export class LazyPaint {
  constructor(content) {
    this.sections = [...content.querySelectorAll(SECTIONS)];
    // Marges qui sortaient de la section (celle du haut du titre, celle du bas du dernier paragraphe ou de la
    // dernière preuve) portées par la section elle-même : masquée, elle les perdrait (rien ne sort d'une section
    // masquée), et ce qui suit remonterait. En em : elles suivent la taille du texte.
    // Sections d'un chapitre avant le chapitre : la marge reportée sur une section passe ensuite au chapitre si elle
    // en sortait aussi.
    for (const section of [...this.sections].reverse()) {
      const style = getComputedStyle(section);
      const size = parseFloat(style.fontSize);
      for (const side of ["Top", "Bottom"]) {
        const chain = edgeChain(section, side === "Top");
        const inner = Math.max(0, ...chain.map((element) => parseFloat(getComputedStyle(element)[`margin${side}`])));
        if (!(inner > 0)) continue;
        section.style[`margin${side}`] = `${Math.max(inner, parseFloat(style[`margin${side}`])) / size}em`;
        chain.forEach((element) => element.style.setProperty(`margin-${side.toLowerCase()}`, "0", "important"));
      }
    }
    content.classList.add("lazy-paint");
    this.content = content;
    this.near = new Set();
    this.observer = new IntersectionObserver((entries) => {
      const far = [];
      for (const entry of entries) {
        if (entry.isIntersecting) {
          this.near.add(entry.target);
          entry.target.classList.remove("offscreen");
        } else {
          this.near.delete(entry.target);
          far.push(entry.target);
        }
      }
      if (!this.suspended) hide(far);
    }, { rootMargin: `${AHEAD} 0px` });
    this.sections.forEach((section) => this.observer.observe(section));
  }

  // Tout affiché pendant change() (mise en page complète, positions lues), puis sections lointaines de nouveau masquées
  relayout(change) {
    this.suspended = true;
    this.sections.forEach((section) => section.classList.remove("offscreen"));
    try {
      return change();
    } finally {
      this.suspended = false;
      hide(this.sections.filter((section) => !this.near.has(section)));
    }
  }

  destroy() {
    this.observer.disconnect();
    this.content.classList.remove("lazy-paint");
    this.sections.forEach((section) => section.classList.remove("offscreen"));
  }
}

// Éléments dont la marge du haut (top) ou du bas traverse le bord de la section : premier (ou dernier) enfant dans le
// flux, puis le sien, tant que rien ne sépare les marges (bordure, marge intérieure, texte en ligne). Les boutons
// ajoutés par le lecteur (flèche des preuves, marque-pages, notes : position absolue) sont sautés.
function edgeChain(element, top) {
  const chain = [];
  for (let parent = element; ;) {
    let child = top ? parent.firstElementChild : parent.lastElementChild;
    while (child && outOfFlow(child)) child = top ? child.nextElementSibling : child.previousElementSibling;
    if (!child || !["block", "list-item", "table", "flow-root"].includes(getComputedStyle(child).display)) break;
    if (textBeside(parent, child, top)) break;
    chain.push(child);
    const style = getComputedStyle(child);
    const side = top ? "Top" : "Bottom";
    if (style.display !== "block" || style.overflow !== "visible" || parseFloat(style[`padding${side}`])
      || parseFloat(style[`border${side}Width`]) || child.matches(SECTIONS)) break;
    parent = child;
  }
  return chain;
}

function outOfFlow(element) {
  const style = getComputedStyle(element);
  return style.display === "none" || style.position === "absolute" || style.position === "fixed" || style.float !== "none";
}

// texte (non blanc) avant le premier enfant ou après le dernier : une ligne de texte sépare les marges
function textBeside(parent, child, top) {
  for (let node = top ? child.previousSibling : child.nextSibling; node; node = top ? node.previousSibling : node.nextSibling) {
    if (node.nodeType === Node.TEXT_NODE && node.data.trim()) return true;
  }
  return false;
}

// hauteurs toutes lues, puis toutes posées : une seule mise en page
function hide(sections) {
  const heights = sections.map((section) => section.getBoundingClientRect().height);
  sections.forEach((section, i) => {
    section.style.containIntrinsicHeight = `${heights[i]}px`;
    section.classList.add("offscreen");
  });
}
