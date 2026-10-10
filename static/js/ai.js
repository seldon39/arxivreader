// Onglet « IA » du lecteur : l'assistant choisi dans les réglages (Claude par défaut) explique un énoncé,
// détaille ou audite une preuve, répond aux questions. Il ne reçoit que le texte de l'article (aucun outil) ;
// ses réponses sont à vérifier.

import { api, streamAI } from "./api.js";
import { authorName, currentModel as modelFor, modelLabel, providerOf, setupNotice } from "./aiproviders.js";
import { COLORS, rangeForQuote } from "./annotations.js";
import { markdownToHtml } from "./markdown.js";
import { typesetTex } from "./math.js";
import { t } from "./i18n.js";
import { saveSettings, settings } from "./settings.js";
import { h, icons, toast, toastError } from "./util.js";

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
    this.statusToken = 0;
    this.running = null;      // { exchange, controller }
    this.live = null;         // rendu en direct de la réponse en cours
    this.pending = { quote: null, parent: null, question: "" };
    this.refs = this.buildRefs();
  }

  setStructure(structure) {
    this.structure = structure;
    this.byId = new Map(structure.nodes.map((n) => [n.id, n]));
    this.refs = this.buildRefs();
  }

  async load() {
    try { this.exchanges = await api.aiExchanges(this.paper.id); } catch { this.exchanges = []; }
  }

  async refreshStatus() {
    const expected = currentModel();
    if (this.checking && this.statusFor === expected) return; // vérification déjà en cours
    const token = ++this.statusToken; // seule la dernière vérification demandée compte
    this.statusFor = expected;
    this.checking = true;
    let status;
    try { status = await api.aiStatus(); } catch { status = { available: false, loggedIn: false, provider: providerOf(expected), model: expected }; }
    if (token !== this.statusToken) return;
    const models = status.models || [];
    if (status.provider === "local" && models.length && !models.includes(settings.aiLocalModel)) {
      await saveSettings({ aiLocalModel: models[0] }); // modèle local pas encore choisi (ou disparu) : le premier
      status.model = `local:${models[0]}`;
      this.statusFor = currentModel();
    }
    this.status = status;
    this.checking = false;
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
    // pas de \b : il ne connaît que les lettres ASCII (« étape 3 » ne serait jamais repéré)
    const statement = new RegExp(`(?<![\\p{L}\\p{N}])(${words})(\\s|&nbsp;| )+(${NUMBER})(?![\\p{L}\\p{N}])`, "giu");
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
    const hasText = node.proofs.length || node.justifiedBy || node.proofPassage;
    const action = (task, label) => h("button", { class: `button small-button ai-action${compact ? " compact" : ""}`, type: "button",
      onclick: () => this.ask({ task, target: nodeId }) }, h("span", { class: "ai-icon", html: icons.sparkle }), label);
    return h("div", { class: "ai-actions" },
      action("explain", t("ai.task.explain")),
      hasText || node.manual === "immediate" ? action("detail", t("ai.task.detail")) : null,
      hasText ? action("audit", t("ai.task.audit")) : null,
      action("annotate", t("ai.task.annotate")));
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
      model: this.status?.model || currentModel(), created_at: new Date().toISOString().slice(0, 19).replace("T", " ") };
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
    if (local.error && ["login", "missing", "offline"].includes(local.errorCode)) this.status = { ...(this.status || {}), loggedIn: false };
    this.running = null;
    this.onChange();
  }

  stop() {
    this.running?.controller.abort();
  }

  async remove(exchange) {
    try {
      if (typeof exchange.id === "number") await api.deleteAIExchange(exchange.id);
    } catch (error) {
      toastError(error);
      return;
    }
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
    const header = `**${modelLabel(exchange.model)} · ${this.title(exchange)}** — *${exchange.created_at.slice(0, 10)}, ${t("ai.unverified")}*`;
    const asked = exchange.question ? `\n\n> ${exchange.question.replace(/\n/g, " ")}` : "";
    try {
      await this.annotator.appendNote(id, `${header}${asked}\n\n${parseProposals(exchange.answer).text.trim()}`);
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
    if ((!this.status && !this.checking) || this.statusFor !== currentModel()) this.refreshStatus(); // assistant changé dans les réglages
    const status = this.status;
    if (status && !status.loggedIn) body.append(setupNotice(status, () => { this.status = null; this.onChange(); }));

    body.append(this.askBox(focus));

    const list = [...this.exchanges].reverse(); // la plus récente en haut, juste sous la question
    this.live = null;
    body.append(h("div", { class: "ai-list" }, list.length ? list.map((e) => this.card(e))
      : h("p", { class: "hint" }, t("ai.empty"))));
    body.append(h("p", { class: "hint ai-privacy" }, t(`ai.privacy.${providerOf(currentModel())}`)));
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
    const model = this.modelPicker();
    const box = h("div", { class: "panel-section ai-ask" },
      ...chips, area,
      h("div", { class: "ai-ask-row" }, model || h("span", { class: "muted small" }, modelLabel(currentModel())), h("span", { class: "spacer" }), sendButton),
      h("p", { class: "hint ai-hint" }, t("ai.hint")),
      h("div", { class: "ai-ask-row" }, h("span", { class: "muted small" }, t("ai.wholePaper")), paperAction("summary"), paperAction("review")));
    requestAnimationFrame(() => { grow(); if (focus) area.focus(); });
    typesetTex(chips).catch(() => {});
    return box;
  }

  // Choix du modèle : Opus ou Sonnet pour Claude, modèles installés pour un modèle local (sinon rien)
  modelPicker() {
    const provider = providerOf(currentModel());
    const options = provider === "claude" ? [["opus", "Opus"], ["sonnet", "Sonnet"]]
      : provider === "local" ? (this.status?.models || []).map((name) => [name, name]) : [];
    if (!options.length) return null;
    const current = provider === "local" ? settings.aiLocalModel : settings.aiModel || "opus";
    const select = h("select", { class: "ai-model", "aria-label": t("ai.model"), title: t("ai.model") },
      options.map(([value, label]) => h("option", { value, selected: current === value }, label)));
    select.addEventListener("change", async () => {
      await saveSettings(provider === "local" ? { aiLocalModel: select.value } : { aiModel: select.value });
      if (this.status) this.status.model = currentModel();
      this.onChange();
    });
    return select;
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
        foot.push(h("div", { class: "notice small" }, t(ERROR_KEYS[exchange.errorCode] || "ai.error.other",
          { message: exchange.error || "" })));
      }
      const actions = h("div", { class: "ai-card-actions" },
        exchange.answer && h("button", { class: "button small-button", type: "button", onclick: () => this.saveToNotes(exchange) },
          h("span", { class: "ai-icon", html: icons.note }), t("ai.saveToNotes")),
        exchange.answer && h("button", { class: "button small-button", type: "button",
          onclick: () => { this.pending.parent = exchange; this.onChange({ focus: true }); } }, t("ai.followUp")),
        exchange.answer && typeof exchange.id === "number" && (providerOf(currentModel()) === "claude"
          ? h("button", { class: "button small-button", type: "button", title: t("ai.continueHint"), onclick: () => this.continueInClaude(exchange) },
            h("span", { class: "ai-icon", html: icons.external }), t("ai.continue"))
          : h("button", { class: "button small-button", type: "button", title: t("ai.exportCopyHint"), onclick: () => this.copyExchange(exchange) },
            t("ai.exportCopy"))),
        exchange.answer && h("button", { class: "button small-button", type: "button",
          onclick: () => navigator.clipboard.writeText(parseProposals(exchange.answer).text.trim()).then(() => toast(t("ai.copied")), toastError) }, t("ai.copy")));
      const proposals = parseProposals(exchange.answer || "").items;
      if (proposals.length && exchange.status === "done") foot.push(this.proposalBox(exchange, proposals));
      foot.push(actions, h("p", { class: "ai-disclaimer" }, t("ai.disclaimer", { model: modelLabel(exchange.model) })));
    }
    return h("article", { class: `ai-card status-${exchange.status}` }, head, ...context, answer, ...foot);
  }

  // Nouvelle conversation dans l'application Claude, pré-remplie (non envoyée) avec l'article et l'échange.
  // Le lien est limité à ~14 000 caractères : le texte complet est aussi copié dans le presse-papiers.
  async continueInClaude(exchange) {
    const pending = api.exportAI(exchange.id).then((result) => result.text);
    const copied = await copyLater(pending);
    let text;
    try { text = await pending; } catch (error) { toastError(error); return; }
    const LIMIT = 13000;
    const long = text.length > LIMIT;
    const prefill = long ? `${text.slice(0, LIMIT)}\n\n[…] ${copied ? t("ai.continueCut") : ""}`.trim() : text;
    location.href = `claude://claude.ai/new?q=${encodeURIComponent(prefill)}`;
    toast(t(long ? (copied ? "ai.continueLong" : "ai.continueLongNoCopy") : copied ? "ai.continueDone" : "ai.continueOpened"), { duration: 8000 });
  }

  // Autres assistants : le même texte, copié, à coller dans une nouvelle conversation
  async copyExchange(exchange) {
    const pending = api.exportAI(exchange.id).then((result) => result.text);
    if (await copyLater(pending)) toast(t("ai.exportCopied"));
    else pending.then(() => toast(t("ai.copyFailed"), { error: true }), toastError);
  }

  // ---------- Annotations proposées par Claude ----------

  // Aperçu des propositions : chacune est retrouvée dans l'article (sinon signalée), cochée par défaut ;
  // « Appliquer » crée les passages surlignés et les notes, marqués « Claude », et s'annule d'un clic.
  // Énoncé et preuves visés par la réponse : les extraits y sont cherchés d'abord
  scopesOf(exchange) {
    const node = this.byId.get(exchange.target);
    return node ? [node.id, ...node.proofs].map((id) => this.find(id)) : [];
  }

  proposalBox(exchange, proposals) {
    const content = this.annotator.content;
    const normalize = (label) => String(label || "").toLowerCase().replace(/[.\s]+$/, "").replace(/\s+/g, " ").trim();
    const byLabel = new Map(this.structure.nodes.map((n) => [normalize(n.label), n]));
    // résultat de la recherche gardé avec la réponse (l'onglet se redessine souvent)
    exchange.found ||= proposals.map((proposal) => (proposal.quote ? Boolean(rangeForQuote(content, proposal.quote, this.scopesOf(exchange))) : null));
    const rows = proposals.map((proposal, i) => {
      if (proposal.quote) return { proposal, found: exchange.found[i], kind: "highlight" };
      const node = byLabel.get(normalize(proposal.statement));
      const proofId = node?.proofs[0];
      const onProof = Boolean(proposal.on === "proof" && proofId && this.find(proofId)?.matches(".ltx_proof"));
      const id = node && (onProof ? proofId : node.id);
      return { proposal, found: Boolean(id && this.find(id)), kind: "note", id, node, onProof };
    });
    const applied = Boolean(exchange.applied);
    const boxes = rows.map((row) => h("input", { type: "checkbox", checked: row.found && !applied, disabled: !row.found || applied }));
    const count = () => boxes.filter((b) => b.checked && !b.disabled).length;
    const apply = h("button", { class: "button small-button primary", type: "button" });
    const label = () => {
      apply.textContent = exchange.applied ? t("ai.proposals.applied") : t("ai.proposals.apply", { n: count() });
      apply.disabled = Boolean(exchange.applied) || !count();
    };
    boxes.forEach((box) => box.addEventListener("change", label));
    label();
    apply.addEventListener("click", async () => {
      apply.disabled = true;
      const chosen = rows.filter((_, i) => boxes[i].checked && !boxes[i].disabled);
      const undo = await this.applyProposals(chosen, this.scopesOf(exchange), providerOf(exchange.model));
      if (!undo) { label(); return; }
      exchange.applied = 1;
      api.setAIApplied(exchange.id, true).catch(toastError);
      this.onChange();
      let undone = false; // le bouton Annuler ne sert qu'une fois
      toast(t("ai.proposals.done", { n: undo.count }), { action: { label: t("common.undo"), onClick: async () => {
        if (undone) return;
        undone = true;
        const failures = await undo.run();
        if (failures) { toast(t("ai.proposals.undoPartial"), { error: true }); return; } // gardé « appliqué » : rien en double
        exchange.applied = 0;
        api.setAIApplied(exchange.id, false).catch(toastError);
        this.onChange();
      } } });
    });
    const list = h("div", { class: "ai-proposals-list" }, rows.map((row, i) => {
      const { proposal } = row;
      const what = row.kind === "highlight"
        ? h("blockquote", { class: `note-quote hl-border-${COLORS.includes(proposal.color) ? proposal.color : "yellow"}` }, proposal.quote)
        : h("div", { class: "ai-proposal-target" }, h("span", { class: "ai-icon", html: icons.note }),
          t(row.onProof ? "ai.proposals.onProof" : "ai.proposals.onStatement", { label: row.node?.label || proposal.statement || "?" }));
      const note = proposal.note ? h("div", { class: "markdown ai-proposal-note" }) : null;
      if (note) note.innerHTML = markdownToHtml(proposal.note, { refs: this.refs });
      return h("label", { class: `ai-proposal${row.found ? "" : " missing"}` }, boxes[i],
        h("span", { class: "ai-proposal-body" }, what, note, !row.found && h("span", { class: "badge warn" }, t("ai.proposals.notFound"))));
    }));
    list.addEventListener("click", (event) => {
      const ref = event.target.closest("a.ai-ref");
      if (!ref) return;
      event.preventDefault(); // la case ne se coche pas et on reste dans le lecteur
      this.navigate(ref.dataset.ref);
    });
    typesetTex([list]).catch(() => {});
    return h("div", { class: "ai-proposals" },
      h("div", { class: "ai-proposals-head" }, h("span", { class: "ai-icon", html: icons.sparkle }), t("ai.proposals.title", { n: proposals.length })),
      list, h("div", { class: "ai-card-actions" }, apply));
  }

  // Crée les annotations choisies ; renvoie de quoi tout annuler (ou null si rien n'a pu être fait)
  async applyProposals(rows, scopes = [], author = "claude") {
    const created = [];
    const previousNotes = new Map();
    for (const row of rows) {
      const { proposal } = row;
      try {
        if (row.kind === "highlight") {
          const range = rangeForQuote(this.annotator.content, proposal.quote, scopes); // retrouvé à nouveau : le texte a pu changer
          if (!range) continue;
          const color = COLORS.includes(proposal.color) ? proposal.color : "yellow";
          created.push((await this.annotator.highlightRange(range, { color, note: proposal.note || null, author })).id);
        } else if (proposal.note) {
          if (!previousNotes.has(row.id)) previousNotes.set(row.id, this.annotator.block(row.id)?.note || "");
          await this.annotator.appendNote(row.id, `**${authorName(author)}** — *${t("ai.unverified")}*\n\n${proposal.note}`);
        }
      } catch (error) {
        toastError(error);
      }
    }
    const count = created.length + previousNotes.size;
    if (!count) return null;
    return {
      count,
      // renvoie le nombre d'échecs (0 : tout est annulé)
      run: async () => {
        let failures = 0;
        const attempt = (promise) => promise.catch((error) => { failures++; toastError(error); });
        for (const id of created) await attempt(this.annotator.removeHighlight(id));
        for (const [id, note] of previousNotes) {
          const block = this.annotator.block(id);
          // note créée par l'application seule (ni note ni état avant) : supprimée plutôt que vidée
          if (!note && block && !block.state) await attempt(this.annotator.deleteBlock(block.id));
          else await attempt(this.annotator.saveBlock(id, { note }));
        }
        return failures;
      },
    };
  }

  destroy() {
    this.stop();
  }
}

// Réponse de Claude : texte, et propositions d'annotations (bloc ```annotations``` en JSON, retiré du texte).
// Pendant l'écriture, le bloc commencé est simplement masqué.
export function parseProposals(answer) {
  const match = answer.match(/```annotations[^\n]*\n([\s\S]*?)(```|$)/);
  if (!match) return { text: answer, items: [] };
  const text = answer.slice(0, match.index).trimEnd() + answer.slice(match.index + match[0].length);
  if (match[2] !== "```") return { text, items: [] };
  try {
    const items = JSON.parse(match[1]);
    return { text, items: Array.isArray(items) ? items.filter((item) => item && (item.quote || (item.statement && item.note))) : [] };
  } catch {
    return { text, items: [] };
  }
}

const currentModel = () => modelFor(settings);

// Copie d'un texte qui arrive du serveur : Safari n'autorise l'écriture dans le presse-papiers que pendant le
// clic, d'où un ClipboardItem dont le contenu est promis (l'attendre d'abord ferait échouer la copie)
async function copyLater(textPromise) {
  try {
    if (window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": textPromise.then((text) => new Blob([text], { type: "text/plain" })) })]);
    } else {
      await navigator.clipboard.writeText(await textPromise);
    }
    return true;
  } catch {
    return false;
  }
}
const ERROR_KEYS = { login: "ai.error.login", missing: "ai.error.login", limit: "ai.error.limit", offline: "ai.error.offline" };

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
    const blocks = splitBlocks(parseProposals(text).text);
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

