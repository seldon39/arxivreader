// Page « Bibliothèque » : import par identifiant arXiv et liste des articles.

import { api } from "./api.js";
import { t, tn } from "./i18n.js";
import { formatDate, h, icons, timeAgo, toast, toastError, withMath } from "./util.js";

let filterText = "";

export async function renderLibrary(view) {
  const input = h("input", {
    class: "text-input", type: "text", name: "query", autocomplete: "off", spellcheck: "false",
    placeholder: t("library.placeholder"), "aria-label": t("library.placeholder"),
  });
  const submit = h("button", { class: "button primary", type: "submit" }, t("library.import"));
  const status = h("div", { class: "busy", hidden: true }, h("span", { class: "spinner" }), t("library.importing"));
  const form = h("form", { class: "import-box" }, input, submit);
  const list = h("div", {});
  const filter = h("input", {
    class: "text-input", type: "search", value: filterText, placeholder: t("library.filter"), "aria-label": t("library.filter"),
  });
  const heading = h("h2", {});

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = input.value.trim();
    if (!query) return;
    submit.disabled = true;
    status.hidden = false;
    try {
      const paper = await api.importPaper(query);
      input.value = "";
      toast(t("library.imported", { title: paper.title }));
      await refreshList();
    } catch (error) {
      toastError(error);
    } finally {
      submit.disabled = false;
      status.hidden = true;
    }
  });
  filter.addEventListener("input", () => { filterText = filter.value; refreshList(false); }); // filtre local, sans requête

  let papers = [];
  async function refreshList(reload = true) {
    if (reload) {
      try {
        papers = await api.papers();
      } catch (error) {
        const retry = h("button", { class: "button small-button", type: "button", onclick: () => refreshList() }, t("common.retry"));
        list.replaceChildren(h("p", { class: "notice" }, t("error.generic", { message: error.message }), retry));
        return;
      }
    }
    heading.textContent = t("library.papers", { n: papers.length });
    const needle = filterText.trim().toLowerCase();
    const shown = papers.filter((p) => !needle || [p.title, p.arxiv_id, p.authors.join(" "), p.categories.join(" ")]
      .join(" ").toLowerCase().includes(needle));
    if (!papers.length) {
      const example = h("code", {}, "2608.24719");
      const text = t("library.empty", { example: "\u0000" }).split("\u0000");
      list.replaceChildren(h("div", { class: "empty" }, text[0], example, text[1]));
    } else if (!shown.length) {
      list.replaceChildren(h("div", { class: "empty" }, t("library.noMatch")));
    } else {
      list.replaceChildren(h("div", { class: "cards" }, shown.map((p) => card(p, refreshList))));
    }
  }

  view.replaceChildren(h("section", { class: "library" },
    h("h1", {}, t("library.title")),
    h("p", { class: "lead" }, t("library.lead")),
    form, status,
    h("div", { class: "library-tools" }, heading, filter),
    list,
  ));
  if (matchMedia("(hover: hover)").matches) input.focus(); // pas sur téléphone : le clavier s'ouvrirait à chaque visite
  list.replaceChildren(h("div", { class: "busy" }, h("span", { class: "spinner" }), t("common.loading")));
  await refreshList();
}

// « Ouvert il y a 2 jours · 3 notes · 5 énoncés compris · 2 réponses de Claude »
function readingLine(paper) {
  const reading = paper.reading || {};
  const parts = [
    paper.opened_at ? t("library.opened", { when: timeAgo(paper.opened_at) }) : t("library.notOpened"),
    reading.highlights ? tn("library.highlights", reading.highlights) : "",
    reading.notes ? tn("library.notes", reading.notes) : "",
    reading.done ? tn("library.done", reading.done) : "",
    reading.answers ? tn("library.answers", reading.answers) : "",
  ].filter(Boolean);
  return h("div", { class: "card-reading muted small" }, parts.join(" · "));
}

function card(paper, refreshList) {
  const href = `#/paper/${encodeURIComponent(paper.id)}`;
  const action = (icon, title, handler) => h("button", {
    class: "icon-button", type: "button", title, "aria-label": title, html: icon,
    onclick: (event) => handler(event.currentTarget),
  });
  const authors = paper.authors.length > 6 ? [...paper.authors.slice(0, 6), "…"] : paper.authors;
  // Le titre est un vrai lien (⌘-clic, copier le lien…) qui couvre toute la carte ; les boutons restent au-dessus
  return h("article", { class: "card" },
    h("h3", { class: "card-title" }, h("a", { class: "card-link", href }, withMath(paper.title))),
    h("div", { class: "card-authors" }, authors.join(", ")),
    readingLine(paper),
    h("div", { class: "card-meta" },
      paper.primary_category && h("span", { class: "chip category" }, paper.primary_category),
      h("span", { class: "chip" }, `arXiv:${paper.arxiv_id}v${paper.version}`),
      h("span", { class: "chip" }, formatDate(paper.published)),
      paper.source === "none" && h("span", { class: "chip" }, t("library.pdfOnly")),
    ),
    h("div", { class: "card-actions" },
      h("a", { class: "icon-button", href: `https://arxiv.org/abs/${paper.arxiv_id}`, target: "_blank", rel: "noopener noreferrer",
        title: "arXiv", "aria-label": "arXiv", html: icons.external }),
      action(icons.refresh, t("library.refresh"), async (button) => {
        button.disabled = true;
        button.classList.add("spinning");
        try { await api.refresh(paper.id); toast(t("library.updated")); refreshList(); }
        catch (error) { toastError(error); }
        finally { button.disabled = false; button.classList.remove("spinning"); }
      }),
      // Retirer de la bibliothèque (annulable) : l'article reste en cache, ses notes et réponses sont gardées
      action(icons.trash, t("library.delete"), async () => {
        try {
          await api.setInLibrary(paper.id, false);
          refreshList();
          toast(t("library.deleted"), { action: { label: t("common.undo"),
            onClick: () => api.setInLibrary(paper.id, true).then(() => refreshList(), toastError) } });
        } catch (error) {
          toastError(error);
        }
      }),
    ),
  );
}
