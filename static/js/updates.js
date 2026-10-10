// Mises à jour : une version plus récente est publiée sur GitHub ? Pastille dans la barre du haut,
// détails (télécharger, nouveautés, ignorer cette version) dans les réglages.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { h, toastError } from "./util.js";

let current = null;

export async function refreshUpdates(force = false) {
  try {
    current = force ? await api.checkUpdates() : await api.updates();
  } catch (error) {
    if (force) toastError(error);
    return current;
  }
  showPill();
  return current;
}

function showPill() {
  document.querySelector(".update-pill")?.remove();
  if (!current?.available || current.dismissed) return;
  const pill = h("button", { class: "update-pill", type: "button", title: t("updates.pillTitle", { version: current.latest.version }) },
    t("updates.pill"));
  pill.addEventListener("click", () => dispatchEvent(new CustomEvent("arxivreader:settings", { detail: "updates" })));
  document.querySelector(".topbar-actions")?.prepend(pill);
}

// Section « Mises à jour » des réglages
export function updatesSection() {
  const box = h("div", { class: "updates-settings", id: "settings-updates" });
  const draw = (state, checking = false) => {
    const latest = state?.latest;
    const parts = [h("p", { class: "hint" }, t("updates.current", { version: state?.current || "…" }))];
    if (state?.available) {
      parts.push(h("div", { class: "notice update-notice" },
        h("strong", {}, t("updates.available", { version: latest.version })),
        h("p", {}, t("updates.howTo")),
        h("div", { class: "ai-setup-actions" },
          h("a", { class: "button small-button primary", href: latest.download || latest.url, target: "_blank", rel: "noopener noreferrer" },
            t("updates.download")),
          h("a", { class: "button small-button", href: latest.url, target: "_blank", rel: "noopener noreferrer" }, t("updates.notes")),
          !state.dismissed && h("button", { class: "button small-button", type: "button", onclick: () =>
            api.dismissUpdate(latest.version).then((next) => { current = next; showPill(); draw(next); }, toastError) }, t("updates.ignore")))));
    } else if (state?.checked && !state.error) {
      parts.push(h("p", { class: "hint ai-ready" }, `✓ ${t("updates.upToDate")}`));
    }
    if (state?.error) parts.push(h("p", { class: "hint" }, t("updates.error", { message: state.error })));
    parts.push(h("div", {}, h("button", { class: "button small-button", type: "button", disabled: checking,
      onclick: () => { draw(current, true); refreshUpdates(true).then((next) => draw(next)); } },
    checking ? t("updates.checking") : t("updates.checkNow"))));
    box.replaceChildren(...parts);
  };
  draw(current);
  if (!current) refreshUpdates().then((next) => draw(next));
  return box;
}
