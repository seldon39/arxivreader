// Lecture dans le PDF : pdf.js (static/vendor/pdfjs) dans un cadre de la page du lecteur.
// Contrairement au lecteur PDF du système, il dit quelle page on lit et à quelle hauteur : le retour
// au HTML se fait au même endroit, et la position de lecture est gardée même en lisant le PDF.

import { pdfWords } from "./pdfsync.js";

const VIEWER = "/static/vendor/pdfjs/web/viewer.html";

// Réglages de pdf.js, posés avant son démarrage : il envoie « webviewerloaded » à la page parente
document.addEventListener("webviewerloaded", (event) => {
  const options = event.detail?.source?.PDFViewerApplicationOptions;
  if (!options) return;
  const theme = document.documentElement.dataset.theme;
  for (const [name, value] of Object.entries({
    disablePreferences: true, // pas de réglages gardés par pdf.js lui-même
    enableScripting: false, // scripts contenus dans les PDF : jamais exécutés
    annotationEditorMode: -1, // pas d'outils d'annotation (rien ne serait enregistré)
    supportsPrinting: false,
    disableHistory: true, // l'adresse du cadre ne change pas en lisant
    externalLinkTarget: 2, // liens vers l'extérieur : nouvelle fenêtre
    defaultZoomValue: "page-width",
    enableSelectionRendering: false, // sélection dessinée par pdfnotes.js (une bande par ligne), pas par pdf.js
    sidebarViewOnLoad: 0,
    viewerCssTheme: theme === "dark" ? 2 : theme === "light" ? 1 : 0, // thème de l'application
  })) options.set(name, value);
});

// Ajouts au lecteur (boutons sans objet masqués, surlignages) : une feuille de style en fichier, seule forme que la
// politique de sécurité de pdf.js accepte (une balise <style> ajoutée par script y serait ignorée)
const EXTRA_CSS = "/static/css/pdfjs-extra.css";

export class PdfView {
  constructor(pdfUrl, title) {
    this.pdfUrl = pdfUrl;
    this.frame = document.createElement("iframe");
    this.frame.className = "pdf-frame";
    this.frame.title = title;
    this.frame.addEventListener("load", () => {
      const doc = this.frame.contentDocument;
      if (!doc) return;
      doc.head.append(Object.assign(doc.createElement("link"), { rel: "stylesheet", href: EXTRA_CSS }));
    });
  }

  // premier affichage : page, et hauteur dans la page si on la connaît, en pleine largeur (un seul
  // déplacement : un défilement fait avant que pdf.js ait fixé le zoom serait décalé ensuite)
  load(page = 1, top = null) {
    const zoom = top == null ? "page-width" : `page-width,0,${Math.round(top)}`;
    this.frame.src = `${VIEWER}?file=${encodeURIComponent(this.pdfUrl)}#page=${page || 1}&zoom=${zoom}`;
  }

  get loaded() {
    return Boolean(this.frame.getAttribute("src"));
  }

  // pdf.js prêt (document ouvert, pages en place) ; null s'il ne l'est pas 30 s après le début du
  // chargement (le PDF peut d'abord être téléchargé depuis arXiv : l'attente compte à partir de load())
  async ready() {
    for (let i = 0; i < 1200 && !this.loaded && this.frame.isConnected !== false; i++) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    for (let i = 0; i < 300; i++) {
      const app = this.frame.contentWindow?.PDFViewerApplication;
      if (app?.pdfDocument && app.pdfViewer?.pagesCount > 0) return app;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return null;
  }

  // page (et hauteur, en coordonnées du PDF, quand on la connaît) ; le zoom ne change pas
  async goTo(page, top = null) {
    if (!page) return;
    if (!this.loaded) { this.load(page, top); return; }
    const app = await this.ready();
    app?.pdfViewer.scrollPageIntoView({ pageNumber: page, destArray: [null, { name: "XYZ" }, null, top, null], allowNegativeOffset: true });
  }

  // endroit lu : page en haut de la fenêtre et hauteur dans la page (coordonnées du PDF), calculés sur le
  // défilement réel (la position que pdf.js tient à jour attend l'image suivante, retardée en arrière-plan)
  location() {
    const app = this.frame.contentWindow?.PDFViewerApplication;
    const viewer = app?.pdfDocument && app.pdfViewer;
    if (!viewer) return null;
    const top = viewer.container.getBoundingClientRect().top;
    for (let i = 0; i < viewer.pagesCount; i++) {
      const view = viewer.getPageView(i);
      const rect = view?.div.getBoundingClientRect();
      if (!rect || rect.bottom <= top + 1) continue;
      const [, y] = view.viewport.convertToPdfPoint(0, Math.max(0, top - rect.top));
      return { page: i + 1, top: y };
    }
    return { page: app.page, top: null };
  }

  // appelé à chaque déplacement dans le PDF
  async onMove(callback) {
    const app = await this.ready();
    app?.eventBus.on("updateviewarea", () => callback(this.location()));
  }

  // texte du PDF, mot à mot avec page et hauteur (calculé une fois)
  text() {
    // un échec n'est pas gardé : la prochaine demande réessaie
    this.words ??= this.ready().then((app) => (app ? pdfWords(app) : null)).catch(() => null)
      .then((words) => { if (!words) this.words = null; return words; });
    return this.words;
  }

  find() {
    this.frame.contentWindow?.PDFViewerApplication?.findBar?.open();
  }
}
