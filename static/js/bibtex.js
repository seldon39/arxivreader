// BibTeX d'articles de la bibliothèque : copié, ou enregistré en fichier .bib. Le serveur le construit hors ligne
// d'après ce qui est enregistré (app/bibtex.py) ; ici seulement la copie et le téléchargement.

import { t, tn } from "./i18n.js";
import { copyText, toast, toastError } from "./util.js";

// Message après une copie ou un export ; `missing` : articles de la sélection qui ne sont pas dans la bibliothèque
function report(key, data) {
  const missing = data.missing ? ` ${tn("bibtex.missing", data.missing)}` : "";
  toast(`${tn(key, data.count || 1)}${missing}`);
}

// `load()` : demande au serveur ({bibtex, count, missing}). Le presse-papiers de WebKit n'accepte une écriture
// qu'à la suite d'un geste : la réponse est donc passée à ClipboardItem sous forme de promesse, sans l'attendre avant
export async function copyBibtex(load) {
  const answer = load();
  answer.catch(() => {});  // l'erreur est traitée plus bas (pas de rejet non traité)
  try {
    if (window.ClipboardItem && navigator.clipboard?.write) {
      const blob = answer.then((data) => new Blob([data.bibtex], { type: "text/plain" }));
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]);
      report("bibtex.copied", await answer);
      return;
    }
  } catch { /* refusé, ou réponse en erreur : repris ci-dessous */ }
  try {
    const data = await answer;
    await copyText(data.bibtex);
    report("bibtex.copied", data);
  } catch (error) {
    toastError(error);
  }
}

// Fichier .bib : le serveur est d'abord interrogé (rien à enregistrer si aucun article n'est connu), puis le fichier
// est téléchargé à son adresse, comme l'export des notes
export async function saveBibtex(load, fileUrl) {
  try {
    const data = await load();
    const link = document.createElement("a");
    link.href = fileUrl;
    link.download = "";
    document.body.append(link);
    link.click();
    link.remove();
    report("bibtex.exported", data);
  } catch (error) {
    toastError(error);
  }
}

export const selectionIds = (items) => items.map((item) => item.paperId || item.arxivId).filter(Boolean);
export const selectionFile = (ids) => `/api/bibtex.bib?ids=${ids.map(encodeURIComponent).join(",")}`;
