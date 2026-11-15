// Aperçus tirés du PDF, comme ceux du HTML : un extrait de la page cible, dessiné par pdf.js et recadré sur le texte,
// à partir de la ligne visée.
// - liens du PDF au survol (renvois vers un énoncé ou une équation, citations [12]) : linkPreviews ;
// - renvois des réponses de l'IA quand on lit le PDF : previewCanvas (appelé par le lecteur, reader.previewFor).
// Les bulles sont posées dans la page de l'application, au-dessus du cadre de pdf.js (dont la politique de sécurité
// refuse les styles écrits par script). N'utilise que l'API publique de pdf.js : getAnnotations, getDestination,
// getPageIndex, getTextContent, render — à revérifier à chaque mise à jour de pdf.js (MAINTENANCE, section 7).

import { t } from "./i18n.js";
import { h } from "./util.js";

const SHOW_DELAY = 220;  // ms, comme dans le HTML : un passage de la souris ne l'ouvre pas
const HIDE_DELAY = 250;
const WIDTH = 560;       // largeur de l'extrait (px CSS), celle des aperçus du HTML
const SPAN = 0.3;        // hauteur montrée : environ un tiers de page à partir de la ligne visée
const PAD = 14;          // marge autour du texte (points du PDF)
const REACH = 40;        // la ligne visée est au plus à cette distance sous la cible (points du PDF)
const KEEP = 24;         // extraits gardés (un même renvoi survolé deux fois n'est pas redessiné)

// Moteur d'un document ouvert dans pdf.js (un par chargement du cadre) : texte des pages et extraits, gardés
function engine(view, app) {
  if (app.arxivPreview) return app.arxivPreview;
  const pdf = app.pdfDocument;
  const texts = new Map();  // page → { min, max, lines } (promesse)
  const crops = new Map();  // clé → extrait dessiné (promesse)

  // texte de la page : bornes en largeur (sans les marges blanches) et lignes (base, haut, bas, texte), en
  // coordonnées du PDF ; une ligne = les morceaux de même base (indices et exposants forment leurs propres lignes)
  const pageText = (page) => {
    if (!texts.has(page)) {
      texts.set(page, pdf.getPage(page).then(async (p) => {
        const items = (await p.getTextContent()).items.filter((item) => item.str?.trim() && item.transform);
        if (!items.length) return null;
        let min = Infinity, max = -Infinity;
        const byBase = new Map();
        for (const item of items) {
          const [x, y] = [item.transform[4], item.transform[5]];
          const size = item.height || Math.hypot(item.transform[2], item.transform[3]) || 10;
          // texte tourné (tampon arXiv dans la marge) : hors des bornes, qui suivent le corps du texte
          if (Math.abs(item.transform[1]) <= Math.abs(item.transform[0])) {
            min = Math.min(min, x);
            max = Math.max(max, x + (item.width || 0));
          }
          const key = Math.round(y);
          const line = byBase.get(key) || byBase.set(key, { base: y, top: -Infinity, bottom: Infinity, parts: [] }).get(key);
          line.top = Math.max(line.top, y + size * 0.8);
          line.bottom = Math.min(line.bottom, y - size * 0.25);
          line.parts.push([x, item.str]);
        }
        const lines = [...byBase.values()].map((line) => ({
          base: line.base, top: line.top, bottom: line.bottom,
          text: line.parts.sort((a, b) => a[0] - b[0]).map((part) => part[1]).join(" "),
        }));
        return { min, max, lines };
      }).catch(() => null));
    }
    return texts.get(page);
  };

  // haut de l'extrait. La cible d'un lien (hyperref) est un peu au-dessus de la ligne visée, d'un écart qui varie
  // (environ 8 points pour un énoncé, plus d'une ligne dans une bibliographie) : on prend la ligne qui contient le
  // texte du lien (« 31 » de [31], « 1.1 », « 47 » de (47)), sinon la première ligne nettement sous la cible.
  const startOf = (text, top, hint) => {
    const near = text.lines.filter((line) => line.base <= top + 2 && line.base >= top - REACH).sort((a, b) => b.base - a.base);
    if (!near.length) return top;
    const token = tokenOf(hint);
    const pattern = token && new RegExp(`(^|[^\\w.])${token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?!\\w|\\.\\d)`);
    const line = (pattern && near.find((l) => pattern.test(l.text))) || near.find((l) => l.base <= top - 5) || near[0];
    // la ligne avec ses indices et exposants ; puis au milieu de l'interligne, sans rien de la ligne précédente
    const band = text.lines.filter((l) => l.bottom < line.top && l.top > line.bottom);
    const head = Math.max(line.top, ...band.map((l) => l.top));
    const previous = text.lines.filter((l) => l.base > line.base && !band.includes(l)).map((l) => l.bottom);
    const gap = previous.length ? Math.min(...previous) : Infinity;
    return gap > head ? Math.min(head + 3, (head + gap) / 2) : head + 0.5;
  };

  // bas de l'extrait : après la dernière ligne entière, environ un tiers de page plus bas ; une ligne à cheval (grand
  // symbole, formule centrée) est gardée entière
  const endOf = (text, from, span) => {
    const limit = from - span;
    const inside = text.lines.filter((line) => line.top <= from && line.bottom >= limit);
    if (!inside.length) return null;
    let to = Math.min(...inside.map((line) => line.bottom)) - 3;
    for (let i = 0; i < 6; i++) {
      const across = text.lines.find((line) => line.top > to + 0.5 && line.bottom < to - 0.5 && line.top <= from);
      if (!across) break;
      to = across.bottom - 3;
    }
    return to;
  };

  const draw = async ({ page, top }, hint, cssWidth) => {
    const p = await pdf.getPage(page);
    const base = p.getViewport({ scale: 1 });
    const text = await pageText(page);
    let x0 = 0, x1 = base.width;
    if (text) {
      const [a] = base.convertToViewportPoint(text.min - PAD, 0);
      const [b] = base.convertToViewportPoint(text.max + PAD, 0);
      x0 = Math.max(0, Math.min(a, b));
      x1 = Math.min(base.width, Math.max(a, b));
      if (x1 - x0 < base.width / 3) { x0 = 0; x1 = base.width; }  // page presque vide : toute la largeur
    }
    // page tournée (/Rotate : tableau en paysage…) : les lignes du PDF ne sont plus horizontales à l'écran, la page
    // entière est montrée (réduite à la largeur de la bulle)
    if (base.rotation % 360) {
      const css = cssWidth / base.width, scale = css * (devicePixelRatio || 1);
      const source = view.frame.contentDocument.createElement("canvas");
      source.width = Math.round(base.width * scale);
      source.height = Math.round(base.height * scale);
      await p.render({ canvasContext: source.getContext("2d"), viewport: p.getViewport({ scale }), background: "#fff" }).promise;
      return { source, width: cssWidth };
    }
    const from = top != null && text ? startOf(text, top, hint) : top;
    const to = from != null && text ? endOf(text, from, base.height * SPAN) : null;
    const y0 = from == null ? 0 : Math.max(0, base.convertToViewportPoint(0, from)[1]);
    const y1 = to == null ? y0 + base.height * SPAN : base.convertToViewportPoint(0, to)[1];
    const height = Math.min(Math.max(y1 - y0, Math.min(80, base.height)), base.height - y0);
    const css = cssWidth / (x1 - x0);  // points du PDF → px CSS
    const scale = css * (devicePixelRatio || 1);
    // dessin dans un canevas du cadre de pdf.js (son moteur y dessine), recopié ensuite dans la page de l'application
    const source = view.frame.contentDocument.createElement("canvas");
    source.width = Math.round((x1 - x0) * scale);
    source.height = Math.max(1, Math.round(height * scale));
    await p.render({
      canvasContext: source.getContext("2d"),
      viewport: p.getViewport({ scale }),
      transform: [1, 0, 0, 1, -x0 * scale, -y0 * scale],
      background: "#fff",
    }).promise;
    return { source, width: (x1 - x0) * css };
  };

  // extrait prêt à poser : un canevas de la page de l'application (largeur CSS fixée)
  const canvas = async (target, hint) => {
    const cssWidth = Math.min(WIDTH, innerWidth - 24);
    const key = `${target.page}:${target.top}:${tokenOf(hint)}:${cssWidth}:${devicePixelRatio}`;
    if (!crops.has(key)) {
      if (crops.size >= KEEP) crops.delete(crops.keys().next().value);
      crops.set(key, draw(target, hint, cssWidth).catch((error) => { crops.delete(key); throw error; }));
    }
    const drawn = await crops.get(key);
    const element = h("canvas", { width: drawn.source.width, height: drawn.source.height });
    element.style.width = `${drawn.width}px`;
    element.getContext("2d").drawImage(drawn.source, 0, 0);
    return element;
  };

  // cible d'une destination de pdf.js : page (à partir de 1) et hauteur en coordonnées du PDF (null : haut de la page)
  const resolve = async (dest) => {
    const explicit = typeof dest === "string" ? await pdf.getDestination(dest) : dest;
    if (!Array.isArray(explicit)) return null;
    const [ref, kind] = explicit;
    const index = ref && typeof ref === "object" ? await pdf.getPageIndex(ref) : Number.isInteger(ref) ? ref : null;
    if (index == null || index < 0 || index >= pdf.numPages) return null;
    const name = kind?.name;
    const top = name === "XYZ" ? explicit[3] : name === "FitH" || name === "FitBH" ? explicit[2] : name === "FitR" ? explicit[5] : null;
    return { page: index + 1, top: typeof top === "number" ? top : null };
  };

  app.arxivPreview = { canvas, resolve };
  return app.arxivPreview;
}

// numéro porté par le lien : « [31] » → 31, « Theorem 1.1 » → 1.1, « (47) » → 47 ; null s'il n'y en a pas
function tokenOf(hint) {
  const words = String(hint || "").replace(/[[\]()]/g, " ").trim().split(/\s+/).filter((word) => /\d/.test(word));
  return words.at(-1)?.replace(/[.,;:]+$/, "") || null;
}

// Extrait du PDF pour un endroit connu (énoncé cité par l'IA) : `target` { page, top }, `hint` son libellé
export function previewCanvas(view, app, target, hint) {
  return engine(view, app).canvas(target, hint);
}

// Aperçu au survol des liens du PDF
export function linkPreviews(view, app) {
  const viewer = app.pdfViewer;
  const container = viewer?.container;
  if (!container || container.arxivPreviews) return;
  container.arxivPreviews = true;
  const { canvas, resolve } = engine(view, app);
  const annotations = new Map();  // page → annotations (promesse)
  let box = null, current = null, showTimer = 0, hideTimer = 0, token = 0;

  const hide = () => {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    token++;
    box?.remove();
    box = current = null;
    if (openHide === hide) openHide = null;
  };
  const scheduleHide = () => {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, HIDE_DELAY);
  };
  const pageAnnotations = (page) => {
    if (!annotations.has(page)) annotations.set(page, app.pdfDocument.getPage(page).then((p) => p.getAnnotations()).catch(() => []));
    return annotations.get(page);
  };

  const show = async (link) => {
    const mine = ++token;
    const section = link.closest("section[data-annotation-id]");
    const pageNumber = Number(link.closest(".page[data-page-number]")?.dataset.pageNumber);
    if (!section || !pageNumber) return;
    const data = (await pageAnnotations(pageNumber)).find((a) => a.id === section.dataset.annotationId);
    if (mine !== token || !data) return;
    let body, footer = null;
    if (data.url) {
      if (!/^(https?|mailto):/i.test(data.url)) return;
      body = h("div", { class: "pdf-preview-url" }, data.url);
    } else if (data.dest) {
      const target = await resolve(data.dest).catch(() => null);
      if (mine !== token || !target) return;
      body = await canvas(target, data.overlaidText || link.textContent).catch(() => null);
      if (mine !== token || !body) return;
      const label = viewer.getPageView(target.page - 1)?.pageLabel || String(target.page);
      footer = h("div", { class: "preview-footer" },
        h("span", {}, t("pdf.previewPage", { n: label })),
        h("button", { type: "button", onclick: () => { hide(); app.pdfLinkService?.goToDestination(data.dest); } }, t("reader.goTo")));
    } else return;
    if (!view.frame.isConnected || !view.frame.offsetParent || !link.isConnected) return;
    hide();
    token = mine;
    box = h("div", { class: `preview pdf-preview${data.url ? " url" : ""}`, role: "tooltip" }, body, footer);
    box.addEventListener("mouseenter", () => clearTimeout(hideTimer));
    box.addEventListener("mouseleave", scheduleHide);
    document.body.append(box);
    openHide = hide;
    // place : sous le lien (sinon au-dessus), coordonnées du cadre ramenées à la page de l'application
    const frame = view.frame.getBoundingClientRect();
    const r = link.getBoundingClientRect();
    const rect = { left: frame.left + r.left, top: frame.top + r.top, bottom: frame.top + r.bottom };
    const width = box.offsetWidth, height = box.offsetHeight;
    const below = rect.bottom + 8 + height < innerHeight;
    box.style.left = `${Math.min(Math.max(12, rect.left - 20), innerWidth - width - 12)}px`;
    box.style.top = `${Math.min(Math.max(8, below ? rect.bottom + 8 : rect.top - height - 8), Math.max(8, innerHeight - height - 8))}px`;
    current = link;
  };

  const LINK = ".annotationLayer .linkAnnotation a";
  container.addEventListener("mouseover", (event) => {
    const link = event.target.closest?.(LINK);
    if (!link || link.contains(event.relatedTarget)) return;
    // retour sur le lien de la bulle ouverte : elle reste ; autre lien : elle se ferme comme prévu (même si le
    // nouveau lien n'a pas d'aperçu), la nouvelle la remplace une fois dessinée
    if (box && current === link) { clearTimeout(hideTimer); return; }
    clearTimeout(showTimer);
    showTimer = setTimeout(() => show(link), SHOW_DELAY);
  });
  container.addEventListener("mouseout", (event) => {
    const link = event.target.closest?.(LINK);
    if (!link || link.contains(event.relatedTarget)) return;
    clearTimeout(showTimer);
    token++;  // aperçu en cours de dessin : abandonné
    if (box) scheduleHide();
  });
  // l'extrait ne suit pas le défilement ni le zoom : il se ferme. Échap dans le PDF : aussi les aperçus du lecteur
  // (renvois de l'IA), dont l'écoute est dans la page de l'application (view.onEscape, posé par le lecteur)
  container.addEventListener("scroll", hide, { passive: true });
  container.addEventListener("mousedown", hide);
  app.eventBus?.on("scalechanging", hide);
  view.frame.contentDocument?.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    hide();
    view.onEscape?.();
  });
  view.hideLinkPreview?.();  // cadre rechargé : la bulle de l'ancien document ne reste pas
  view.hideLinkPreview = hide;
}

// Échap dans la page de l'application : la bulle ouverte, quelle que soit la vue (une seule écoute pour toutes)
let openHide = null;
document.addEventListener("keydown", (event) => { if (event.key === "Escape") openHide?.(); });
