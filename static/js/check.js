// Rapport de la dernière vérification des services (page #/check) : ouvert d'un clic sur sa notification, depuis le
// menu Aide de l'app Mac ou les Réglages ; le texte entier (une notification n'en montre que le début), à copier.

import { api } from "./api.js";
import { copyFromMenu } from "./commands.js";
import { t } from "./i18n.js";
import { h, toast, toastError } from "./util.js";

export async function renderCheck(view, isCurrent = () => true) {
  const { text } = await api.checkReport();
  if (!isCurrent()) return;  // page déjà quittée pendant la lecture du rapport
  const page = h("section", { class: "library check-page" }, h("h2", {}, t("check.title")), h("p", { class: "hint" }, t("check.intro")));
  if (!text) {
    page.append(h("p", {}, t("check.none")));
  } else {
    page.append(h("div", { class: "check-actions" },
      h("button", { class: "button small-button primary", type: "button",
        onclick: () => copyFromMenu(text).then(() => toast(t("check.copied")), toastError) }, t("check.copy"))),
    h("pre", { class: "check-report" }, text));
  }
  view.replaceChildren(page);
}
