// Lecture dans le PDF : pdf.js (static/vendor/pdfjs) dans un cadre de la page du lecteur.
// Contrairement au lecteur PDF du système, il dit quelle page on lit et à quelle hauteur : le retour
// au HTML se fait au même endroit, et la position de lecture est gardée même en lisant le PDF.

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
    sidebarViewOnLoad: 0,
    viewerCssTheme: theme === "dark" ? 2 : theme === "light" ? 1 : 0, // thème de l'application
  })) options.set(name, value);
});

// Boutons de pdf.js sans objet ici (ouvrir un fichier, imprimer, enregistrer, lien vers la vue)
const HIDDEN = "#secondaryOpenFile, #printButton, #secondaryPrint, #downloadButton, #secondaryDownload, #viewBookmark, #editorModeButtons";

export class PdfView {
  constructor(pdfUrl, title) {
    this.pdfUrl = pdfUrl;
    this.frame = document.createElement("iframe");
    this.frame.className = "pdf-frame";
    this.frame.title = title;
    this.frame.addEventListener("load", () => {
      const style = this.frame.contentDocument?.createElement("style");
      if (!style) return;
      style.textContent = `${HIDDEN} { display: none !important; }`;
      this.frame.contentDocument.head.append(style);
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

  // pdf.js prêt (document ouvert, pages en place) ; null s'il ne l'est pas au bout de 20 s
  async ready() {
    for (let i = 0; i < 200; i++) {
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

  // endroit lu : page en haut de la fenêtre et hauteur dans la page (coordonnées du PDF)
  location() {
    const app = this.frame.contentWindow?.PDFViewerApplication;
    if (!app?.pdfDocument) return null;
    const location = app.pdfViewer?._location;
    return location ? { page: location.pageNumber, top: location.top } : { page: app.page, top: null };
  }

  // appelé à chaque déplacement dans le PDF
  async onMove(callback) {
    const app = await this.ready();
    app?.eventBus.on("updateviewarea", () => callback(this.location()));
  }

  find() {
    this.frame.contentWindow?.PDFViewerApplication?.findBar?.open();
  }
}
