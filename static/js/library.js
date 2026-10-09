// Page « Bibliothèque » : import par identifiant arXiv et liste des articles.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { formatDate, h, icons, toast, withMath } from "./util.js";

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
      toast(t("error.generic", { message: error.message }), { error: true, duration: 6000 });
    } finally {
      submit.disabled = false;
      status.hidden = true;
    }
  });
  filter.addEventListener("input", () => { filterText = filter.value; refreshList(); });

  let papers = [];
  async function refreshList(reload = true) {
    if (reload) {
      try { papers = await api.papers(); } catch (error) { toast(error.message, { error: true }); }
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
  input.focus();
  await refreshList();
}

function card(paper, refreshList) {
  const open = () => { location.hash = `#/paper/${encodeURIComponent(paper.id)}`; };
  const action = (icon, title, handler) => h("button", {
    class: "icon-button", type: "button", title, "aria-label": title, html: icon,
    onclick: (event) => { event.stopPropagation(); handler(); },
  });
  const authors = paper.authors.length > 6 ? [...paper.authors.slice(0, 6), "…"] : paper.authors;
  return h("article", {
    class: "card", tabindex: 0, role: "link",
    onclick: open, onkeydown: (event) => { if (event.key === "Enter") open(); },
  },
    h("h3", { class: "card-title" }, withMath(paper.title)),
    h("div", { class: "card-authors" }, authors.join(", ")),
    h("div", { class: "card-meta" },
      paper.primary_category && h("span", { class: "chip category" }, paper.primary_category),
      h("span", { class: "chip" }, `arXiv:${paper.arxiv_id}v${paper.version}`),
      h("span", { class: "chip" }, formatDate(paper.published)),
      paper.source === "none" && h("span", { class: "chip" }, t("library.pdfOnly")),
    ),
    h("div", { class: "card-actions" },
      action(icons.external, "arXiv", () => window.open(`https://arxiv.org/abs/${paper.arxiv_id}`, "_blank", "noopener")),
      action(icons.refresh, t("library.refresh"), async () => {
        try { await api.refresh(paper.id); toast(t("library.updated")); refreshList(); }
        catch (error) { toast(error.message, { error: true }); }
      }),
      action(icons.trash, t("library.delete"), async () => {
        if (!confirm(t("library.confirmDelete", { title: paper.title }))) return;
        await api.remove(paper.id);
        toast(t("library.deleted"));
        refreshList();
      }),
    ),
  );
}
