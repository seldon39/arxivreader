// Onglet « IA » du lecteur : Claude explique un énoncé, détaille ou audite une preuve, répond aux questions.
// Claude ne reçoit que le texte de l'article (aucun outil, aucun accès au réseau) ; ses réponses sont à vérifier.

import { api, streamAI } from "./api.js";
import { markdownToHtml } from "./markdown.js";
import { typesetTex } from "./math.js";
import { t } from "./i18n.js";
import { saveSettings, settings } from "./settings.js";
import { h, icons, toast } from "./util.js";

// Mots qui précèdent un numéro d'énoncé dans les réponses (anglais et français)
const KIND_WORDS = {
  theorem: "theorems?|théorèmes?|thm\\.?", lemma: "lemmas?|lemmes?|lemmata", proposition: "propositions?|prop\\.",
  corollary: "corollar(?:y|ies)|corollaires?|cor\\.", definition: "definitions?|définitions?", remark: "remarks?|remarques?",
  example: "examples?|exemples?", conjecture: "conjectures?", claim: "claims?|assertions?|steps?|étapes?",
  notation: "notations?|assumptions?|hypothès(?:e|es)|conventions?", problem: "problems?|problèmes?|questions?",
};
const KIND_OF_WORD = Object.entries(KIND_WORDS).map(([kind, words]) => [new RegExp(`^(?:${words})$`, "i"), kind]);
const NUMBER = "\\d+(?:\\.\\d+)*[a-z]?";

export class Assistant {
  constructor({ paper, structure, find, navigate, annotator, onChange, equationIds }) {
    this.paper = paper;
    this.equationIds = equationIds;
    this.structure = structure;
    this.byId = new Map(structure.nodes.map((n) => [n.id, n]));
    this.find = find;
    this.navigate = navigate;
    this.annotator = annotator;
    this.onChange = onChange;
    this.exchanges = [];
    this.status = null;
    this.running = null;      // { exchange, controller }
    this.live = null;         // rendu en direct de la réponse en cours
    this.pending = { quote: null, parent: null, question: "" };
    this.refs = this.buildRefs();
  }

  async load() {
    try { this.exchanges = await api.aiExchanges(this.paper.id); } catch { this.exchanges = []; }
  }

  async refreshStatus() {
    try { this.status = await api.aiStatus(); } catch { this.status = { available: false, loggedIn: false }; }
    this.onChange();
  }

  // ---------- Renvois cliquables dans les réponses ----------

  buildRefs() {
    const byKind = new Map();
    const byNumber = new Map();
    for (const node of this.structure.nodes) {
      const number = node.label.match(new RegExp(`(${NUMBER})\\s*$`))?.[1];
      if (!number) continue;
      byKind.set(`${node.kind}:${number}`, node.id);
      byNumber.set(number, byNumber.has(number) ? null : node.id); // ambigu si deux énoncés ont le même numéro
    }
    // numéros d'équations lus dans l'article affiché (la structure ne garde que ceux cités par un énoncé)
    let equations = null;
    const equationId = (label) => {
      equations ||= new Map([...Object.entries(this.structure.equations || {}).map(([id, eq]) => [eq.label, id]), ...(this.equationIds?.() || [])]);
      return equations.get(label);
    };
    const words = Object.values(KIND_WORDS).join("|");
    const statement = new RegExp(`\\b(${words})(\\s|&nbsp;| )+(${NUMBER})\\b`, "giu");
    const equation = new RegExp(`\\((${NUMBER})\\)`, "g");
    const link = (id, text) => `<a href="#${encodeURIComponent(id)}" class="ai-ref" data-ref="${id.replace(/"/g, "&quot;")}">${text}</a>`;
    return (text) => text
      .replace(statement, (match, word, _space, number) => {
        const kind = KIND_OF_WORD.find(([re]) => re.test(word))?.[1];
        const id = byKind.get(`${kind}:${number}`) || byNumber.get(number);
        return id ? link(id, match) : match;
      })
      .replace(equation, (match, number) => {
        const id = equationId(`(${number})`);
        return id ? link(id, match) : match;
      });
  }

  // ---------- Demandes ----------

  // Actions sur un énoncé : expliquer ; et sur sa preuve, s'il en a une : détailler, auditer
  actionsFor(nodeId, { compact = false } = {}) {
    const node = this.byId.get(nodeId);
    if (!node) return null;
    const action = (task, label) => h("button", { class: `button small-button ai-action${compact ? " compact" : ""}`, type: "button",
      onclick: () => this.ask({ task, target: nodeId }) }, h("span", { class: "ai-icon", html: icons.sparkle }), label);
    return h("div", { class: "ai-actions" },
      action("explain", t("ai.task.explain")),
      node.proofs.length || node.justifiedBy ? action("detail", t("ai.task.detail")) : null,
      node.proofs.length || node.justifiedBy ? action("audit", t("ai.task.audit")) : null);
  }

  // Passage sélectionné dans l'article : il accompagne la prochaine question
  setPassage(quote) {
    this.pending.quote = quote;
    this.onChange({ focus: true });
  }

  async ask({ task, target = null, quote = null, question = null, parent = null }) {
    if (this.running) {
      toast(t("ai.busy"));
      return;
    }
    if (this.status && !this.status.loggedIn) {
      this.onChange({ show: true });
      toast(t("ai.loginFirst"), { error: true });
      return;
    }
    const controller = new AbortController();
    const local = { id: `local-${Date.now()}`, task, target, quote, question, parent_id: parent?.id ?? null, answer: "", status: "running",
      model: this.status?.model || settings.aiModel || "opus", created_at: new Date().toISOString().slice(0, 19).replace("T", " ") };
    this.exchanges.push(local);
    this.running = { exchange: local, controller };
    this.pending = { quote: null, parent: null, question: "" };
    this.onChange({ show: true });
    let frame = 0;
    const redraw = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; this.live?.update(local.answer, false); });
    };
    try {
      await streamAI(this.paper.id, { task, target, quote, question, parent_id: parent?.id ?? null }, (event) => {
        if (event.exchange) Object.assign(local, event.exchange, { answer: local.answer });
        if (event.delta) { local.answer += event.delta; redraw(); }
        if (event.error) { local.error = event.error; local.errorCode = event.code; }
        if (event.done) Object.assign(local, event.exchange, { answer: event.exchange.answer || local.answer });
      }, controller.signal);
    } catch (error) {
      if (error.name === "AbortError") local.status = "stopped";
      else { local.status = "error"; local.error = error.message; }
    }
    if (local.status === "running") local.status = local.error ? "error" : "done";
    if (local.error && local.errorCode === "login") this.status = { ...(this.status || {}), loggedIn: false };
    this.running = null;
    this.onChange();
  }

  stop() {
    this.running?.controller.abort();
  }

  async remove(exchange) {
    if (typeof exchange.id === "number") await api.deleteAIExchange(exchange.id);
    this.exchanges = this.exchanges.filter((e) => e !== exchange);
    this.onChange();
  }

  // Réponse ajoutée à la note de l'énoncé, de la preuve, ou à la fiche de lecture
  async saveToNotes(exchange) {
    const node = this.byId.get(exchange.target);
    let id = null;
    if (node) {
      const proofId = node.proofs[0];
      // note de la preuve si c'est un bloc « Proof. » ; sinon (section à part, justification) celle de l'énoncé
      id = ["detail", "audit"].includes(exchange.task) && proofId && this.find(proofId)?.matches(".ltx_proof") ? proofId : node.id;
    }
    const header = `**Claude · ${this.title(exchange)}** — *${exchange.created_at.slice(0, 10)}, ${t("ai.unverified")}*`;
    const asked = exchange.question ? `\n\n> ${exchange.question.replace(/\n/g, " ")}` : "";
    try {
      await this.annotator.appendNote(id, `${header}${asked}\n\n${exchange.answer.trim()}`);
      toast(id ? t("ai.savedBlock", { label: node.label }) : t("ai.savedGeneral"));
    } catch (error) {
      toast(t("error.generic", { message: error.message }), { error: true });
    }
  }

  title(exchange) {
    const node = this.byId.get(exchange.target);
    const label = node?.label || "";
    return t(`ai.title.${exchange.task}`, { label });
  }

  // ---------- Onglet ----------

  renderPanel(body, { focus = false } = {}) {
    if (!this.status) this.refreshStatus();
    const status = this.status;
    if (status && !status.loggedIn) body.append(this.loginNotice(status));

    body.append(this.askBox(focus));

    const list = [...this.exchanges].reverse(); // la plus récente en haut, juste sous la question
    this.live = null;
    body.append(h("div", { class: "ai-list" }, list.length ? list.map((e) => this.card(e))
      : h("p", { class: "hint" }, t("ai.empty"))));
    body.append(h("p", { class: "hint ai-privacy" }, t("ai.privacy")));
  }

  loginNotice(status) {
    return h("div", { class: "notice ai-login" },
      h("strong", {}, t(status.available ? "ai.login.title" : "ai.missing.title")),
      h("p", {}, t(status.available ? "ai.login.text" : "ai.missing.text")),
      h("code", { class: "ai-command" }, "./arxivreader login"),
      h("button", { class: "button small-button", type: "button", onclick: () => { this.status = null; this.onChange(); } }, t("ai.login.check")));
  }

  askBox(focus) {
    const pending = this.pending;
    const area = h("textarea", { class: "note-input ai-question", rows: 2, placeholder: t(pending.parent ? "ai.followUpPlaceholder" : "ai.placeholder"),
      "aria-label": t("ai.placeholder") });
    area.value = pending.question;
    const grow = () => { area.style.height = "auto"; area.style.height = `${Math.min(260, area.scrollHeight + 2)}px`; };
    area.addEventListener("input", () => { pending.question = area.value; grow(); });
    const send = () => {
      const question = area.value.trim();
      if (!question && !pending.quote) { area.focus(); return; }
      if (pending.quote && !question) this.ask({ task: "passage", quote: pending.quote, parent: pending.parent });
      else this.ask({ task: pending.quote ? "passage" : "ask", quote: pending.quote, question, parent: pending.parent });
    };
    area.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); send(); }
    });
    const chips = [];
    if (pending.quote) {
      const quote = h("blockquote", { class: "note-quote ai-quote" }, pending.quote);
      chips.push(h("div", { class: "ai-chip" }, h("span", { class: "muted small" }, t("ai.passage")),
        h("button", { class: "icon-button small", type: "button", title: t("ai.removePassage"), "aria-label": t("ai.removePassage"), html: icons.close,
          onclick: () => { pending.quote = null; this.onChange({ focus: true }); } }), quote));
    }
    if (pending.parent) {
      chips.push(h("div", { class: "ai-chip" }, h("span", { class: "muted small" }, t("ai.followUpOf", { title: this.title(pending.parent) })),
        h("button", { class: "icon-button small", type: "button", title: t("ai.cancelFollowUp"), "aria-label": t("ai.cancelFollowUp"), html: icons.close,
          onclick: () => { pending.parent = null; this.onChange({ focus: true }); } })));
    }
    const running = Boolean(this.running);
    const sendButton = running
      ? h("button", { class: "button", type: "button", onclick: () => this.stop() }, t("ai.stop"))
      : h("button", { class: "button primary", type: "button", onclick: send }, pending.quote && !pending.question.trim() ? t("ai.explainPassage") : t("ai.send"));
    const paperAction = (task) => h("button", { class: "button small-button", type: "button", disabled: running,
      onclick: () => this.ask({ task }) }, t(`ai.task.${task}`));
    const model = h("select", { class: "ai-model", "aria-label": t("ai.model"), title: t("ai.model") },
      [["opus", "Opus"], ["sonnet", "Sonnet"]].map(([value, label]) => h("option", { value, selected: (settings.aiModel || "opus") === value }, label)));
    model.addEventListener("change", () => {
      saveSettings({ aiModel: model.value });
      if (this.status) this.status.model = model.value;
    });
    const box = h("div", { class: "panel-section ai-ask" },
      ...chips, area,
      h("div", { class: "ai-ask-row" }, model, h("span", { class: "spacer" }), sendButton),
      h("p", { class: "hint ai-hint" }, t("ai.hint")),
      h("div", { class: "ai-ask-row" }, h("span", { class: "muted small" }, t("ai.wholePaper")), paperAction("summary"), paperAction("review")));
    requestAnimationFrame(() => { grow(); if (focus) area.focus(); });
    typesetTex(chips).catch(() => {});
    return box;
  }

  card(exchange) {
    const node = this.byId.get(exchange.target);
    const head = h("div", { class: "ai-card-head" },
      h("span", { class: "ai-icon", html: icons.sparkle }),
      h("span", { class: "ai-card-title" }, node
        ? [t(`ai.title.${exchange.task}`, { label: "" }).trim(), " ", h("a", { href: `#${node.id}`, onclick: (e) => { e.preventDefault(); this.navigate(node.id); } }, node.label)]
        : this.title(exchange)),
      h("span", { class: "spacer" }),
      h("span", { class: "muted small ai-date" }, exchange.created_at?.slice(5, 16).replace(" ", " · ")),
      exchange.status !== "running" && h("button", { class: "icon-button small", type: "button", title: t("ai.delete"), "aria-label": t("ai.delete"), html: icons.trash,
        onclick: () => this.remove(exchange) }));
    const parent = exchange.parent_id && this.exchanges.find((e) => e.id === exchange.parent_id);
    const context = [];
    if (parent) context.push(h("p", { class: "muted small ai-context" }, t("ai.followUpOf", { title: this.title(parent) })));
    if (exchange.quote) context.push(h("blockquote", { class: "note-quote ai-quote" }, exchange.quote));
    if (exchange.question) context.push(h("p", { class: "ai-asked" }, exchange.question));
    if (context.length) typesetTex(context).catch(() => {});

    const answer = h("div", { class: "markdown ai-answer" });
    const live = new LiveAnswer(answer, this.refs);
    live.update(exchange.answer || "", exchange.status !== "running");
    if (exchange.status === "running") this.live = live;
    answer.addEventListener("click", (event) => {
      const ref = event.target.closest("a.ai-ref");
      if (!ref) return;
      event.preventDefault();
      this.navigate(ref.dataset.ref);
    });

    const foot = [];
    if (exchange.status === "running") {
      foot.push(h("div", { class: "busy small" }, h("span", { class: "spinner" }), t(exchange.answer ? "ai.writing" : "ai.thinking")));
    } else {
      if (exchange.status === "stopped") foot.push(h("span", { class: "badge warn" }, t("ai.stopped")));
      if (exchange.status === "error" || exchange.error) {
        foot.push(h("div", { class: "notice small" }, t(exchange.errorCode === "login" ? "ai.error.login" : exchange.errorCode === "limit" ? "ai.error.limit" : "ai.error.other",
          { message: exchange.error || "" })));
      }
      const actions = h("div", { class: "ai-card-actions" },
        exchange.answer && h("button", { class: "button small-button", type: "button", onclick: () => this.saveToNotes(exchange) },
          h("span", { class: "ai-icon", html: icons.note }), t("ai.saveToNotes")),
        exchange.answer && h("button", { class: "button small-button", type: "button",
          onclick: () => { this.pending.parent = exchange; this.onChange({ focus: true }); } }, t("ai.followUp")),
        exchange.answer && h("button", { class: "button small-button", type: "button",
          onclick: () => navigator.clipboard.writeText(exchange.answer).then(() => toast(t("ai.copied"))) }, t("ai.copy")));
      foot.push(actions, h("p", { class: "ai-disclaimer" }, t("ai.disclaimer", { model: modelName(exchange.model) })));
    }
    return h("article", { class: `ai-card status-${exchange.status}` }, head, ...context, answer, ...foot);
  }

  destroy() {
    this.stop();
  }
}

const modelName = (model) => ({ opus: "Opus", sonnet: "Sonnet" }[model] || model || "Claude");

// Rendu progressif : les paragraphes terminés sont mis en forme (MathJax) une seule fois,
// seul le dernier, encore en cours d'écriture, est redessiné.
class LiveAnswer {
  constructor(element, refs) {
    this.element = element;
    this.refs = refs;
    this.done = 0;
    this.tail = h("div", { class: "ai-tail" });
    element.append(this.tail);
  }

  update(text, final) {
    const blocks = splitBlocks(text);
    const complete = final ? blocks.length : Math.max(0, blocks.length - 1);
    const added = [];
    for (; this.done < complete; this.done++) {
      const block = h("div", { class: "ai-block" });
      block.innerHTML = markdownToHtml(blocks[this.done], { refs: this.refs });
      this.tail.before(block);
      added.push(block);
    }
    this.tail.innerHTML = final ? "" : markdownToHtml(blocks[complete] || "", { refs: this.refs });
    if (added.length) typesetTex(added).catch(() => {});
  }
}

// Coupe le Markdown aux lignes vides, sans couper un bloc de code ni une formule $$…$$
function splitBlocks(text) {
  const blocks = [];
  let current = [];
  let fence = false;
  let display = false;
  for (const line of text.split("\n")) {
    if (/^```/.test(line)) fence = !fence;
    if (!fence) display = (line.match(/\$\$/g) || []).length % 2 ? !display : display;
    if (!line.trim() && !fence && !display) {
      if (current.length) blocks.push(current.join("\n"));
      current = [];
    } else {
      current.push(line);
    }
  }
  if (current.length) blocks.push(current.join("\n"));
  return blocks;
}

