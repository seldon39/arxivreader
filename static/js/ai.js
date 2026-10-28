// Onglet « IA » du lecteur : l'assistant choisi dans les réglages (Claude par défaut) explique un énoncé,
// détaille ou audite une preuve, répond aux questions. Il ne reçoit que le texte de l'article (aucun outil) ;
// ses réponses sont à vérifier.

import { api, streamAI } from "./api.js";
import { authorName, currentModel as modelFor, modelControls, modelLabel, providerOf, setupNotice } from "./aiproviders.js";
import { COLORS, rangeForQuote } from "./annotations.js";
import { markdownToHtml } from "./markdown.js";
import { typesetTex } from "./math.js";
import { t, tn } from "./i18n.js";
import { DEFAULT_AI_FONT_SIZE, saveSettings, settings } from "./settings.js";
import { h, icons, plainTitle, toast, toastError } from "./util.js";

// « Envoyer à… » : nouvelle conversation dans l'application (Claude) ou le site (ChatGPT, Gemini) de
// l'assistant choisi, pré-remplie avec les références de l'article ; Gemini ne sait pas recevoir de texte par
// son adresse : tout est à coller. Modèle local : pas d'application.
const SHARE = {
  claude: (message) => `claude://claude.ai/new?q=${encodeURIComponent(message)}`,
  codex: (message) => `https://chatgpt.com/?q=${encodeURIComponent(message)}`,
  gemini: () => "https://gemini.google.com/app",
};

// Mots qui précèdent un numéro d'énoncé dans les réponses (anglais et français)
const KIND_WORDS = {
  theorem: "theorems?|théorèmes?|thm\\.?", lemma: "lemmas?|lemmes?|lemmata", proposition: "propositions?|prop\\.",
  corollary: "corollar(?:y|ies)|corollaires?|cor\\.", definition: "definitions?|définitions?", remark: "remarks?|remarques?",
  example: "examples?|exemples?", conjecture: "conjectures?", claim: "claims?|assertions?|steps?|étapes?",
  notation: "notations?|assumptions?|hypothès(?:e|es)|conventions?", problem: "problems?|problèmes?|questions?",
};
const KIND_OF_WORD = Object.entries(KIND_WORDS).map(([kind, words]) => [new RegExp(`^(?:${words})$`, "i"), kind]);
const NUMBER = "\\d+(?:\\.\\d+)*[a-z]?";


// Requête en cours : ce que fait l'assistant et depuis combien de temps (une réponse peut prendre
// plusieurs minutes : le compteur montre que rien n'est bloqué), avec un mot d'explication passé 20 s
function runningLine(exchange) {
  const label = h("span", {});
  const clock = h("span", { class: "ai-elapsed", "aria-hidden": "true" });
  const hint = h("p", { class: "muted small ai-wait-hint", role: "status", hidden: true }, t("ai.longWait"));
  const line = h("div", { class: "ai-running" }, h("div", { class: "busy small" }, h("span", { class: "spinner" }), label, clock), hint);
  const started = exchange.startedAt || Date.parse(`${String(exchange.created_at || "").replace(" ", "T")}Z`) || Date.now();
  const tick = () => {
    if (exchange.status !== "running" || (timer && !line.isConnected)) { clearInterval(timer); return; }
    const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
    label.textContent = t(exchange.answer ? "ai.writing" : "ai.thinking");
    clock.textContent = ` ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    hint.hidden = Boolean(exchange.answer) || seconds < 20;
  };
  let timer = null;
  tick();
  timer = setInterval(tick, 1000);
  return line;
}

export class Assistant {
  // `inPdf()` : article lu dans son PDF (la consigne mentionne alors les zones) ; `contextPages(center)` : pages du
  // PDF autour de `center` (page affichée si null), en images, pour un article sans texte (scan)
  constructor({ paper, structure, find, navigate, annotator, onChange, equationIds, inPdf = () => false, contextPages = null,
    currentPage = null }) {
    this.paper = paper;
    this.inPdf = inPdf;
    this.currentPage = currentPage;  // page du PDF qu'on lit
    this.contextPages = contextPages;
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
    this.pending = { quote: null, parent: null, question: "", zone: null };
    this.docPending = {};     // question en cours de saisie dans Résumé, Relecture
    this.view = "questions";  // sous-onglet : questions, summary, review
    this.positions = {};      // position de lecture de chaque sous-onglet ({ top, atBottom })
    this.seen = {};           // échanges vus en bas de chaque sous-onglet (pastille du bouton ↓)
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
    // réponse antérieure au choix qui vient d'être enregistré (requête partie avant) : redemandée
    for (let attempt = 0; attempt < 4; attempt++) {
      try { status = await api.aiStatus(); } catch { status = { available: false, loggedIn: false, provider: providerOf(expected), model: expected }; }
      if (token !== this.statusToken || !status.model || status.model === currentModel()) break;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
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
    // sections : numéro en tête du titre (« 2.1. Titre », « 2.1 Titre », « Appendix A … », « A.1 … »)
    const sections = new Map();
    for (const entry of this.structure.toc || []) {
      // lettre seule : annexe (« Appendix A … », « Appendix B: … ») ou « A.1 … », pas « A word on notation »
      const match = String(entry.title || "").match(/^(?:(?:Appendix|Annexe)\s+([A-Z])(?=[\s.:]|$)|((?:\d+|[A-Z](?=\.\d))(?:\.\d+)*)\.?(?=[\s:]|$))/);
      const number = match?.[1] || match?.[2];
      if (number && !sections.has(number)) sections.set(number, entry.id);
    }
    // références : étiquette entre crochets (« 12 », « BMMP14 ») ou auteur-année (« Amini et al. (2019) »)
    const cited = new Map();
    const authorYear = [];
    const escape = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    for (const item of this.structure.bibliography || []) {
      const label = String(item.label || "").trim().replace(/^\[(.*)\]$/, "$1");
      const year = label.match(/^(.*\p{L}.*?)[\s,]*\(?((?:1[89]|20)\d\d[a-z]?)\)?$/u);
      if (year) authorYear.push([escape(year[1].trim()), year[2], item.id]);
      if (label && !cited.has(escape(label))) cited.set(escape(label), item.id);
    }
    const quote = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const authorYearRe = authorYear.length ? new RegExp(authorYear.map(([who, year]) =>
      `(?<![\\p{L}\\p{N}])(${quote(who)})(\\s*\\(\\s*${year}\\s*\\)|,?\\s+${year})(?![\\p{L}\\p{N}])`).join("|"), "gu") : null;
    const words = Object.values(KIND_WORDS).join("|");
    // énoncé ou section d'un AUTRE article (« Theorem 3.1 of [12] », « [12, Theorem 3.1] ») : pas relié à celui-ci
    const elsewhere = "(?![^\\S\\n]*,?[^\\S\\n]+(?:of|in|from|de|du|dans|chez)[^\\S\\n]+(?:\\[|the cited|l['’]article cité))";
    const notCited = "(?<!\\[[^\\[\\]\\n]{0,80})";
    // pas de \b : il ne connaît que les lettres ASCII (« étape 3 » ne serait jamais repéré)
    const statement = new RegExp(`${notCited}(?<![\\p{L}\\p{N}])(${words})(\\s|&nbsp;| )+(${NUMBER})(?![\\p{L}\\p{N}])${elsewhere}`, "giu");
    // mot-clé sans casse, numéro avec (« la section a été… » n'est pas l'annexe A) ; espace obligatoire sauf après §
    const anyCase = (word) => word.replace(/\p{L}/gu, (c) => `[${c.toLowerCase()}${c.toUpperCase()}]`);
    const sectionWords = ["sections?", "sect\\.", "sec\\.", "appendix", "appendices", "annexes?", "chapters?", "chapitres?"].map(anyCase).join("|");
    const section = new RegExp(`${notCited}(?<![\\p{L}\\p{N}])(?:(?:${sectionWords})(?:\\s|&nbsp;| )+|§(?:\\s|&nbsp;| )*)((?:[A-Z]|\\d+)(?:\\.\\d+)*)(?![\\p{L}\\p{N}]|\\.\\d)${elsewhere}`, "gu");
    const equation = new RegExp(`\\((${NUMBER})\\)`, "g");
    const bracket = /\[([^\[\]\n]{1,80})\]/g;
    const link = (id, text) => `<a href="#${encodeURIComponent(id)}" class="ai-ref" data-ref="${id.replace(/"/g, "&quot;")}">${text}</a>`;
    // chaque étape seulement hors des liens déjà posés
    const outside = (html, change) => {
      let depth = 0;
      return html.split(/(<[^>]+>)/).map((part) => {
        if (part.startsWith("<")) {
          if (/^<a\b/.test(part)) depth++;
          else if (/^<\/a>/.test(part)) depth--;
          return part;
        }
        return depth ? part : change(part);
      }).join("");
    };
    const steps = [
      (text) => text.replace(statement, (match, word, _space, number) => {
        const kind = KIND_OF_WORD.find(([re]) => re.test(word))?.[1];
        const id = byKind.get(`${kind}:${number}`) || byNumber.get(number);
        return id ? link(id, match) : match;
      }),
      (text) => text.replace(section, (match, number) => {
        const id = sections.get(number);
        return id ? link(id, match) : match;
      }),
      (text) => text.replace(equation, (match, number) => {
        const id = equationId(`(${number})`);
        return id ? link(id, match) : match;
      }),
      // [12], [3, 5], [3–5], [BMMP14], [12, Theorem 3.1] : chaque étiquette connue devient un lien ; la première doit
      // en être une (« [0, 1] », intervalle écrit hors formule, reste tel quel)
      // séparateurs : virgule, point-virgule (pas celui d'une entité « &amp; »), tiret de plage ; « [EGA-IV] »,
      // « [Smith &amp; Jones 2019] » : l'étiquette entière d'abord
      (text) => text.replace(bracket, (match, inside) => {
        const whole = cited.get(inside.trim());
        if (whole) return `[${inside.replace(inside.trim(), () => link(whole, inside.trim()))}]`;
        const pieces = /(?:&(?:amp|lt|gt|quot);|[^,;–])+/g;
        const labelOf = (piece) => cited.get(piece.trim()) || (/^\s*\S+-\S+\s*$/.test(piece) && piece.trim().split("-").every((p) => cited.has(p)) ? "range" : null);
        if (!labelOf(inside.match(pieces)?.[0] || "")) return match;
        return `[${inside.replace(pieces, (piece) => {
          const label = piece.trim();
          const id = cited.get(label);
          if (id) return piece.replace(label, () => link(id, label));
          if (labelOf(piece) === "range") return piece.replace(label, () => label.split("-").map((p) => link(cited.get(p), p)).join("-"));
          return piece;
        })}]`;
      }),
      (text) => authorYearRe ? text.replace(authorYearRe, (match, ...groups) => {
        const index = groups.findIndex((g, i) => i % 2 === 0 && g !== undefined && i < authorYear.length * 2);
        const id = index >= 0 ? authorYear[index / 2][2] : null;
        return id ? link(id, match) : match;
      }) : text,
    ];
    return (text) => steps.reduce((html, step) => outside(html, step), text);
  }

  // ---------- Demandes ----------

  // Actions sur un énoncé : expliquer ; et sur sa preuve, s'il en a une : détailler, auditer
  actionsFor(nodeId, { compact = false } = {}) {
    const node = this.byId.get(nodeId);
    if (!node) return null;
    const hasText = node.proofs.length || node.justifiedBy || node.proofPassage;
    const action = (task, label) => h("button", { class: `button small-button ai-action${compact ? " compact" : ""}`, type: "button",
      onclick: () => this.ask({ task, target: nodeId }) }, label);
    return h("div", { class: "ai-actions" },
      action("explain", t("ai.task.explain")),
      hasText || node.manual === "immediate" ? action("detail", t("ai.task.detail")) : null,
      hasText ? action("audit", t("ai.task.audit")) : null,
      action("annotate", t("ai.task.annotate")));
  }

  // Passage sélectionné dans l'article : il accompagne la prochaine question
  setPassage(quote) {
    this.pending.quote = quote;
    this.pending.zone = null;
    this.view = "questions";
    this.onChange({ focus: true });
  }

  // zone du PDF ({image: data URL PNG, page, quote: texte de la couche de texte}) : la question part avec l'image
  setZone(zone) {
    this.pending.zone = zone;
    this.pending.quote = null;
    this.view = "questions";
    this.onChange({ focus: true });
  }

  // l'assistant choisi lit-il les images ? (zones du PDF)
  readsImages() { return Boolean(this.status?.images && this.status.loggedIn !== false); }

  // une demande peut-elle partir ? (sinon l'utilisateur est prévenu)
  canAsk() {
    if (this.running) {
      toast(t("ai.busy"));
      return false;
    }
    if (this.status && !this.status.loggedIn) {
      this.onChange({ show: true });
      toast(t("ai.loginFirst"), { error: true });
      return false;
    }
    return true;
  }

  async ask({ task, target = null, quote = null, question = null, parent = null, zone = null }) {
    if (!this.canAsk()) return;
    const controller = new AbortController();
    if (zone) quote = zone.quote || null;
    const local = { id: `local-${Date.now()}`, startedAt: Date.now(), task, target, quote, question, parent_id: parent?.id ?? null, answer: "", status: "running",
      imageUrl: zone?.image || null,
      model: this.status?.model || currentModel(), created_at: new Date().toISOString().slice(0, 19).replace("T", " ") };
    this.exchanges.push(local);
    this.running = { exchange: local, controller };
    this.view = this.viewOf(local);
    this.positions[this.view] = { top: 0, atBottom: true };
    this.onChange({ show: true });
    let frame = 0;
    const redraw = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; this.live?.update(local.answer, false); this.followAnswer(); });
    };
    try {
      // page de la zone, sinon celle qu'on lit dans le PDF : un livre trop long part réduit autour d'elle
      const image = zone ? { image: zone.image, page: zone.page } : this.inPdf() && this.currentPage?.() ? { page: this.currentPage() } : {};
      // PDF sans texte (scan) : l'assistant voit les pages autour de la zone (sinon de la page affichée)
      const pages = this.paper.noText && task !== "transcribe" && this.readsImages() && this.contextPages
        ? await this.contextPages(zone?.page ?? null, controller.signal).catch(() => []) : [];
      await streamAI(this.paper.id, { task, target, quote, question, parent_id: parent?.id ?? null, ...image, pages }, (event) => {
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

  // Supprime un échange (question et réponse) ; un résumé ou une relecture part avec les questions posées sur lui
  async remove(exchange) {
    const doomed = new Set([exchange]);
    if (["summary", "review"].includes(exchange.task)) {
      for (let grew = true; grew;) {
        grew = false;
        for (const e of this.exchanges) {
          if (!doomed.has(e) && [...doomed].some((d) => d.id === e.parent_id)) { doomed.add(e); grew = true; }
        }
      }
      if (!confirm(doomed.size > 1 ? tn("ai.deleteWithFollowUps", doomed.size - 1) : t(`ai.confirmDelete.${exchange.task}`))) return;
    }
    if (this.running && doomed.has(this.running.exchange)) this.stop();
    // les suites d'abord, le document en dernier : un échec en cours de route ne laisse pas de suite orpheline
    for (const e of [...doomed].reverse()) {
      try {
        if (typeof e.id === "number") await api.deleteAIExchange(e.id);
      } catch (error) {
        toastError(error);
        break;
      }
      this.exchanges = this.exchanges.filter((x) => x !== e);
      if (this.pending.parent === e) this.pending.parent = null;
    }
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
  // Trois fils, en sous-onglets : Questions (questions sur l'article, suites par « Répondre »), Résumé et Relecture
  // (le document, puis les questions posées sur lui). Chacun : fil chronologique, saisie en bas, boutons ↑ ↓.

  // fil d'un échange : celui de son premier ancêtre (résumé, relecture, sinon questions)
  viewOf(exchange) {
    const byId = new Map(this.exchanges.map((e) => [e.id, e]));
    let root = exchange;
    for (let depth = 0; root?.parent_id && byId.has(root.parent_id) && depth < 50; depth++) root = byId.get(root.parent_id);
    return root?.task === "summary" ? "summary" : root?.task === "review" ? "review" : "questions";
  }

  renderPanel(body, { focus = false } = {}) {
    if ((!this.status && !this.checking) || this.statusFor !== currentModel()) this.refreshStatus(); // assistant changé dans les réglages
    body.classList.add("ai-body");
    const view = this.view;
    const status = this.status;
    this.live = null;
    const exchanges = this.exchanges.filter((e) => this.viewOf(e) === view);
    const notice = status && !status.loggedIn ? setupNotice(status, () => { this.status = null; this.onChange(); }) : null;

    const content = h("div", { class: "ai-thread-content" });
    const thread = h("div", { class: "ai-thread", tabindex: 0, "aria-label": t(`ai.view.${view}`) }, content);
    if (view === "questions") {
      if (exchanges.length) exchanges.forEach((e) => this.renderExchange(content, e));
      else content.append(h("p", { class: "hint ai-empty" }, t("ai.emptyQuestions") + (this.inPdf() && this.readsImages() ? ` ${t("ai.hintZone")}` : "")));
    } else {
      const docs = exchanges.filter((e) => e.task === (view === "summary" ? "summary" : "review"));
      if (exchanges.length) exchanges.forEach((e) => (docs.includes(e) ? this.renderDocument(content, e, e === docs[docs.length - 1])
        : this.renderExchange(content, e, { reply: false })));
      else content.append(h("div", { class: "ai-empty ai-empty-doc" }, h("div", { class: "ai-empty-title" }, t(`ai.title.${view}`)),
        h("p", {}, t(`ai.intro.${view}`))));
    }

    // ↑ en haut à droite, ↓ en bas à droite (dans la marge du fil : le texte n'est jamais couvert) ; seulement s'ils servent
    const up = h("button", { class: "ai-jump up", type: "button", title: t("ai.toTop"), "aria-label": t("ai.toTop"), html: ARROW_UP, hidden: true,
      onclick: () => thread.scrollTo({ top: 0, behavior: "smooth" }) });
    const dot = h("span", { class: "ai-jump-dot", hidden: true });
    const last = exchanges.length ? exchanges[exchanges.length - 1].id : null;
    if (!(view in this.seen)) this.seen[view] = last;
    const down = h("button", { class: "ai-jump down", type: "button", title: t("ai.toBottom"), "aria-label": t("ai.toBottom"), html: ARROW_DOWN, hidden: true,
      onclick: () => thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" }) }, dot);
    const update = () => {
      if (!thread.isConnected) return;
      const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 24;
      this.positions[view] = { top: thread.scrollTop, atBottom };
      if (atBottom) this.seen[view] = last;
      up.hidden = thread.scrollTop < 24;
      down.hidden = atBottom;
      dot.hidden = atBottom || this.seen[view] === last;
      // barre de défilement classique (Windows) : les boutons restent à sa gauche
      frame.style.setProperty("--ai-scrollbar", `${thread.offsetWidth - thread.clientWidth}px`);
    };
    thread.addEventListener("scroll", update, { passive: true });
    // formules mises en forme après coup, images : le fil grandit ; s'il était en bas, il y reste
    this.threadObserver?.disconnect(); // fil du rendu précédent
    this.threadObserver = new ResizeObserver(() => {
      if (!thread.isConnected) return;
      if (this.positions[view]?.atBottom) thread.scrollTop = thread.scrollHeight;
      update();
    });
    this.threadObserver.observe(content);
    this.thread = thread;
    this.updateJumps = update;

    const frame = h("div", { class: "ai-frame" }, thread, up, down);
    body.append(this.viewBar(), ...(notice ? [notice] : []), frame, this.bottomBar(view, exchanges, focus));
    // position de lecture : celle laissée ; la première fois, le document depuis le haut, sinon la dernière réponse
    requestAnimationFrame(() => {
      const kept = this.positions[view];
      const first = view !== "questions" && exchanges.length === 1;
      thread.scrollTop = kept ? (kept.atBottom ? thread.scrollHeight : kept.top) : first ? 0 : thread.scrollHeight;
      update();
    });
  }

  // le fil suit la réponse qui s'écrit, si l'on est en bas
  followAnswer() {
    if (!this.thread?.isConnected) return;
    if (this.positions[this.view]?.atBottom !== false) this.thread.scrollTop = this.thread.scrollHeight;
    this.updateJumps?.();
  }

  // dernier résumé ou relecture enregistré et terminé (parent des questions posées dessus)
  lastDocument(view, exchanges) {
    const task = view === "summary" ? "summary" : "review";
    return [...exchanges].reverse().find((e) => e.task === task && typeof e.id === "number" && e.status === "done") || null;
  }

  showView(view) {
    if (view === this.view) return;
    this.view = view;
    this.onChange({ focus: false });
  }

  // Barre du haut : sous-onglets (une réponse en cours ailleurs y est signalée) et taille du texte des réponses
  viewBar() {
    const views = ["questions", "summary", "review"];
    const busy = this.running ? this.viewOf(this.running.exchange) : null;
    const seg = h("div", { class: "ai-views", role: "group", "aria-label": t("ai.views") }, views.map((view) =>
      h("button", { type: "button", class: `ai-view${busy === view ? " busy" : ""}`, "aria-pressed": String(this.view === view),
        title: busy === view ? t("ai.busyHere") : undefined, onclick: () => this.showView(view) }, t(`ai.view.${view}`))));
    return h("div", { class: "ai-topbar" }, seg, h("span", { class: "spacer" }), this.textSizeBar(), this.shareLink({ compact: true }));
  }

  // Barre du bas : la saisie (Questions ; Résumé et Relecture une fois faits), sinon le bouton qui les lance
  bottomBar(view, exchanges, focus) {
    const running = Boolean(this.running);
    const footer = h("p", { class: "ai-footnote", title: t(`ai.privacy.${providerOf(currentModel())}`) }, t("ai.footnote"));
    if (view !== "questions" && !this.lastDocument(view, exchanges) && !exchanges.some((e) => e.status === "running")) {
      const task = view === "summary" ? "summary" : "review";
      const launch = running
        ? h("button", { class: "button ai-launch", type: "button", onclick: () => this.stop() }, t("ai.stop"))
        : h("button", { class: "button primary ai-launch", type: "button", onclick: () => this.ask({ task }) }, t(`ai.launch.${task}`));
      return h("div", { class: "ai-bottom" }, launch, h("div", { class: "ai-foot" }, this.modelPicker() || h("span", { class: "muted small" }, modelLabel(currentModel()))), footer);
    }
    return h("div", { class: "ai-bottom" }, this.askBox(view, exchanges, focus), footer);
  }

  askBox(view, exchanges, focus) {
    const pending = view === "questions" ? this.pending : (this.docPending[view] ||= { question: "" });
    const placeholder = view === "questions" ? "ai.placeholder" : `ai.placeholder.${view}`;
    const area = h("textarea", { class: "ai-question", rows: 1, placeholder: t(placeholder), "aria-label": t(placeholder) });
    area.value = pending.question;
    const grow = () => {
      if (!area.offsetWidth) return; // panneau fermé : hauteur calculée à son ouverture (sinon toujours la plus grande)
      area.style.height = "auto";
      area.style.height = `${Math.min(220, area.scrollHeight + 2)}px`;
    };
    let width = 0;
    new ResizeObserver(() => { if (area.offsetWidth !== width) { width = area.offsetWidth; grow(); } }).observe(area);
    area.addEventListener("input", () => { pending.question = area.value; grow(); });
    // le brouillon n'est vidé qu'au départ effectif de la demande (sinon il reste à sa place)
    const send = () => {
      const question = area.value.trim();
      if (view !== "questions") {
        // question sur le résumé ou la relecture : suite du dernier document enregistré (il accompagne la question)
        const doc = this.lastDocument(view, exchanges);
        if (!question || !doc) { area.focus(); return; }
        if (!this.canAsk()) return;
        pending.question = "";
        this.ask({ task: "ask", question, parent: doc });
        return;
      }
      if (!question && !pending.quote && !pending.zone) { area.focus(); return; }
      if (!this.canAsk()) return;
      const { quote, zone, parent } = pending;
      Object.assign(pending, { quote: null, parent: null, question: "", zone: null });
      if (zone) this.ask({ task: "zone", zone, question: question || null, parent });
      else if (quote && !question) this.ask({ task: "passage", quote, parent });
      else this.ask({ task: quote ? "passage" : "ask", quote, question, parent });
    };
    // Entrée envoie ; Maj + Entrée va à la ligne (et rien pendant la composition d'un caractère : accents, IME)
    area.addEventListener("keydown", (event) => {
      // keyCode 229 : Entrée qui valide un caractère composé (WebKit ne met pas toujours isComposing)
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); send(); }
    });
    const chips = [];
    const chip = (label, onRemove, removeKey, ...content) => h("div", { class: "ai-chip" }, label,
      h("button", { class: "icon-button small", type: "button", title: t(removeKey), "aria-label": t(removeKey), html: icons.close, onclick: onRemove }), ...content);
    if (view === "questions") {
      if (pending.parent) {
        chips.push(chip(h("div", { class: "ai-reply-to" }, h("div", { class: "ai-reply-who" }, `↩ ${t("ai.replyTo", { who: modelLabel(pending.parent.model), time: timeOf(pending.parent) })}`),
          h("div", { class: "ai-reply-text" }, excerpt(pending.parent.answer))),
          () => { pending.parent = null; this.onChange({ focus: true }); }, "ai.cancelFollowUp"));
        chips[chips.length - 1].classList.add("reply");
      }
      if (pending.quote) {
        chips.push(chip(h("span", { class: "muted small" }, t("ai.passage")), () => { pending.quote = null; this.onChange({ focus: true }); }, "ai.removePassage",
          h("blockquote", { class: "note-quote ai-quote" }, pending.quote)));
      }
      if (pending.zone) {
        chips.push(chip(h("span", { class: "muted small" }, t("ai.zone", { page: pending.zone.page })), () => { pending.zone = null; this.onChange({ focus: true }); },
          "ai.removePassage", h("blockquote", { class: "note-quote ai-quote zone-quote" }, h("img", { src: pending.zone.image, alt: "" }))));
      }
    }
    const running = Boolean(this.running);
    const ready = Boolean(pending.question.trim() || pending.quote || pending.zone);
    const sendButton = running
      ? h("button", { class: "ai-send stop", type: "button", title: t("ai.stop"), "aria-label": t("ai.stop"), html: STOP, onclick: () => this.stop() })
      : h("button", { class: "ai-send", type: "button", title: t("ai.send"), "aria-label": t("ai.send"), html: ARROW_UP, onclick: send });
    if (!running) area.addEventListener("input", () => sendButton.classList.toggle("ready", Boolean(area.value.trim() || pending.quote || pending.zone)));
    sendButton.classList.toggle("ready", ready);
    const box = h("div", { class: "ai-ask" }, ...chips,
      h("div", { class: "ai-field" }, area, sendButton),
      h("div", { class: "ai-foot" }, this.modelPicker() || h("span", { class: "muted small" }, modelLabel(currentModel())),
        h("span", { class: "spacer" }), h("span", { class: "muted small ai-enter" }, t("ai.enterToSend"))));
    requestAnimationFrame(() => { grow(); if (focus) area.focus(); });
    typesetTex(chips);
    return box;
  }

  // Taille du texte des réponses (A− / taille / A+ ; clic sur la taille : taille par défaut)
  textSizeBar() {
    const size = () => settings.aiFontSize || DEFAULT_AI_FONT_SIZE;
    const value = h("button", { class: "zoom-reset", type: "button", title: t("ai.textSizeReset") }, `${size()} px`);
    const set = (next) => {
      next = Math.max(11, Math.min(26, next));
      if (next === size()) return;
      saveSettings({ aiFontSize: next });
      value.textContent = `${next} px`;
      value.setAttribute("aria-disabled", String(next === DEFAULT_AI_FONT_SIZE));
    };
    value.setAttribute("aria-disabled", String(size() === DEFAULT_AI_FONT_SIZE));
    value.addEventListener("click", () => set(DEFAULT_AI_FONT_SIZE));
    const button = (icon, key, step) => h("button", { class: "icon-button small", type: "button", title: t(key), "aria-label": t(key), html: icon,
      onclick: () => set(size() + step) });
    return h("div", { class: "ai-text-size", role: "group", "aria-label": t("ai.textSize"), title: t("ai.textSize") },
      button(icons.minus, "ai.textSmaller", -1), value, button(icons.plus, "ai.textLarger", 1));
  }

  // Choix du modèle et du niveau de réflexion (liste du compte connecté), sous la saisie
  modelPicker() {
    const [model, effort] = modelControls(this.status, () => {
      if (this.status) this.status.model = currentModel();
      this.onChange();
    });
    return model ? h("span", { class: "ai-model-picker" }, model, effort) : null;
  }

  // ---------- Échanges ----------

  // Question à droite (bulle), réponse à gauche ; `reply` : bouton « Répondre » (fil des questions)
  renderExchange(thread, exchange, { reply = true } = {}) {
    const node = this.byId.get(exchange.target);
    const bubble = h("div", { class: "ai-bubble" });
    if (exchange.task !== "ask") {
      bubble.append(h("div", { class: "ai-bubble-tag" }, node
        ? [t(`ai.title.${exchange.task}`, { label: "" }).trim(), " ", h("a", { href: `#${node.id}`, onclick: (e) => { e.preventDefault(); this.navigate(node.id); } }, node.label)]
        : this.title(exchange)));
    }
    const parent = reply && exchange.parent_id && this.exchanges.find((e) => e.id === exchange.parent_id);
    if (parent) {
      bubble.append(h("div", { class: "ai-reply-to" }, h("div", { class: "ai-reply-who" }, `${modelLabel(parent.model)} · ${timeOf(parent)}`),
        h("div", { class: "ai-reply-text" }, excerpt(parent.answer))));
    }
    const zone = ["zone", "transcribe"].includes(exchange.task);
    if (zone && (exchange.imageUrl || exchange.image)) {  // zone du PDF : son image (le texte de la couche, souvent faux, n'est pas montré)
      bubble.append(h("blockquote", { class: "note-quote ai-quote zone-quote" }, h("img", { src: exchange.imageUrl || `/api/ai/${exchange.id}/image`, alt: "" })));
    } else if (exchange.quote && !zone) bubble.append(h("blockquote", { class: "note-quote ai-quote" }, exchange.quote));
    if (exchange.question) bubble.append(h("p", { class: "ai-asked" }, exchange.question));
    typesetTex([bubble]);
    thread.append(bubble, this.renderAnswer(exchange, { reply }));
  }

  // Résumé ou relecture : le document, pleine largeur ; « Refaire » sur le plus récent
  renderDocument(thread, exchange, latest) {
    const redo = latest && exchange.status !== "running" && !this.running
      ? h("button", { class: "button small-button", type: "button", title: t(`ai.redo.${exchange.task}`), onclick: () => this.ask({ task: exchange.task }) },
        h("span", { class: "ai-icon", html: icons.refresh }), t("ai.redo")) : null;
    const answer = this.renderAnswer(exchange, { reply: false, document: true });
    answer.querySelector(".ai-who")?.append(h("span", { class: "spacer" }), redo || "");
    thread.append(answer);
  }

  renderAnswer(exchange, { reply = true, document = false } = {}) {
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
    const who = h("div", { class: "ai-who" }, h("span", { class: "ai-who-dot" }), h("span", {}, `${modelLabel(exchange.model)} · ${timeOf(exchange)}`));
    const parts = [who, answer];
    if (exchange.status === "running") {
      parts.push(runningLine(exchange));
    } else {
      if (exchange.status === "stopped") parts.push(h("span", { class: "badge warn" }, t("ai.stopped")));
      if (exchange.status === "error" || exchange.error) {
        parts.push(h("div", { class: "notice small" }, t(ERROR_KEYS[exchange.errorCode] || "ai.error.other", { message: exchange.error || "" })));
      }
      const proposals = parseProposals(exchange.answer || "").items;
      if (proposals.length && exchange.status === "done") parts.push(this.proposalBox(exchange, proposals));
      parts.push(this.answerActions(exchange, { reply, document }));
    }
    return h("div", { class: `ai-reply status-${exchange.status}${document ? " document" : ""}` }, ...parts);
  }

  // Répondre · Notes · Copier · Continuer ↗ · ⋯ (copier l'échange, supprimer)
  answerActions(exchange, { reply, document }) {
    const zone = ["zone", "transcribe"].includes(exchange.task);
    const latex = zone && exchange.answer && latexOf(exchange.answer);
    const saved = typeof exchange.id === "number";
    const button = (label, onclick, title) => h("button", { class: "ai-act", type: "button", title: title || undefined, onclick }, label);
    const replying = this.pending.parent === exchange;
    const claude = providerOf(currentModel()) === "claude";
    const item = (label, action, extra = {}) => h("button", { type: "button", role: "menuitem", ...extra, onclick: () => { close(); action(); } }, label);
    const more = h("div", { class: "ai-more-menu", hidden: true, role: "menu" },
      exchange.answer && saved && item(t("ai.exportCopy"), () => this.copyExchange(exchange), { title: t("ai.exportCopyHint") }),
      document && exchange.answer && saved && claude && item(t("ai.continue"), () => this.continueInClaude(exchange)),
      item(t(document ? `ai.delete.${exchange.task}` : "ai.delete"), () => this.remove(exchange), { class: "danger" }));
    const toggle = h("button", { class: "ai-act ai-more", type: "button", title: t("ai.more"), "aria-label": t("ai.more"), "aria-haspopup": "menu",
      "aria-expanded": "false" }, "⋯");
    const wrap = h("span", { class: "ai-more-wrap" }, toggle, more);
    const outside = (e) => { if (!wrap.contains(e.target)) close(); };
    const escape = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); toggle.focus(); } };
    function close() {
      more.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      removeEventListener("click", outside, true);
      removeEventListener("keydown", escape, true);
    }
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!more.hidden) { close(); return; }
      more.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      addEventListener("click", outside, true);
      addEventListener("keydown", escape, true);
      more.querySelector("button")?.focus();
      // ouvert du côté où il tient dans le fil (sinon coupé par le bord du panneau) : à droite du bouton si la
      // place manque à gauche, en dessous si elle manque au-dessus
      more.classList.remove("to-right", "below");
      const room = toggle.closest(".ai-thread")?.getBoundingClientRect();
      if (room) {
        let box = more.getBoundingClientRect();
        if (box.left < room.left) more.classList.add("to-right");
        box = more.getBoundingClientRect();
        if (box.top < room.top) more.classList.add("below");
      }
    });
    const replyButton = reply && exchange.answer && saved
      ? button(`↩ ${t("ai.reply")}`, () => { this.pending.parent = replying ? null : exchange; this.onChange({ focus: true }); }, t("ai.replyHint")) : null;
    replyButton?.classList.toggle("on", replying);
    return h("div", { class: "ai-acts" },
      replyButton,
      exchange.answer && button(t("ai.notes"), () => this.saveToNotes(exchange), t("ai.saveToNotes")),
      latex && button(t("ai.copyLatex"), () => navigator.clipboard.writeText(latex).then(() => toast(t("ai.latexCopied")), toastError)),
      exchange.answer && button(t("ai.copyShort"), () => navigator.clipboard.writeText(parseProposals(exchange.answer).text.trim())
        .then(() => toast(t("ai.copied")), toastError), t("ai.copy")),
      !document && exchange.answer && saved && claude && button(`${t("ai.continueShort")} ↗`, () => this.continueInClaude(exchange), t("ai.continueHint")),
      wrap);
  }

  // Tout l'article dans l'application ou le site de l'assistant : un vrai lien (cliqué, il s'ouvre à côté),
  // et en même temps son texte complet copié dans le presse-papiers, au cas où l'assistant ne pourrait pas
  // ouvrir le lien d'arXiv (Gemini : à coller en entier)
  shareLink({ compact = false } = {}) {
    const provider = providerOf(currentModel());
    if (!SHARE[provider]) return null;
    const paper = this.paper;
    const authors = paper.authors.length > 8 ? `${paper.authors.slice(0, 8).join(", ")} et al.` : paper.authors.join(", ");
    const message = t("ai.shareMessage", { title: plainTitle(paper.title).slice(0, 400), authors: authors.slice(0, 600),
      url: paper.arxiv_id ? `https://arxiv.org/abs/${paper.arxiv_id}v${paper.version}` : "" });
    return h("a", { class: compact ? "icon-button small ai-share" : "button small-button ai-share", href: SHARE[provider](message), target: "_blank",
      rel: "noopener", title: t(`ai.shareHint.${provider}`), "aria-label": compact ? t(`ai.share.${provider}`) : undefined,
      onclick: () => {
        copyLater(api.paperText(paper.id).then((result) => result.text))
          .then((copied) => toast(t(copied ? `ai.shareDone.${provider}` : "ai.shareNoCopy"), { duration: 9000 }));
      } },
      h("span", { class: "ai-icon", html: icons.external }), compact ? null : t(`ai.share.${provider}`));
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
    typesetTex([list]);
    return h("div", { class: "ai-proposals" },
      h("div", { class: "ai-proposals-head" }, t("ai.proposals.title", { n: proposals.length })),
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

const svg = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
const ARROW_UP = svg("M11 20V7.8l-5.6 5.6L4 12l8-8 8 8-1.4 1.4L13 7.8V20z");
const ARROW_DOWN = svg("M13 4v12.2l5.6-5.6L20 12l-8 8-8-8 1.4-1.4 5.6 5.6V4z");
const STOP = svg("M7 7h10v10H7z");

// heure de l'échange (enregistrée en UTC), avec le jour s'il ne date pas d'aujourd'hui
function timeOf(exchange) {
  const raw = String(exchange.created_at || "").replace(" ", "T");
  const date = new Date(Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(raw) ? raw : `${raw}Z`));
  if (Number.isNaN(date.getTime())) return "";
  const time = date.toLocaleTimeString(document.documentElement.lang || undefined, { hour: "2-digit", minute: "2-digit" });
  if (date.toDateString() === new Date().toDateString()) return time;
  return `${date.toLocaleDateString(document.documentElement.lang || undefined, { day: "numeric", month: "short" })} ${time}`;
}

// début d'une réponse, sur une ligne (rappel de la réponse à laquelle on répond) ; formules gardées entières
function excerpt(answer, length = 160) {
  let text = parseProposals(answer || "").text.replace(/\$\$/g, "$").replace(/\\[()[\]]/g, "$");
  // Markdown retiré hors des formules seulement (« $x_1$ » garde son indice)
  text = text.split(/(\$[^$]*\$)/).map((part, i) => (i % 2 ? part : part.replace(/(^|\n)\s*>\s?/g, " ").replace(/(^|\s)#+\s/g, " ").replace(/[*`]+/g, ""))).join("")
    .replace(/\s+/g, " ").trim();
  if (text.length > length) {
    text = text.slice(0, length);
    if ((text.match(/\$/g) || []).length % 2) text = text.slice(0, text.lastIndexOf("$"));
    text = `${text.trimEnd()}…`;
  }
  return text;
}

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
    if (added.length) typesetTex(added);
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

// transcription LaTeX d'une réponse sur une zone : le premier bloc $$…$$ (ou \[…\], ou bloc de code), sinon tout le
// texte s'il est court (texte courant transcrit avec ses $…$)
function latexOf(answer) {
  const display = answer.match(/\$\$([\s\S]+?)\$\$/) || answer.match(/\\\[([\s\S]+?)\\\]/) || answer.match(/```(?:latex|tex)?\n([\s\S]+?)```/);
  if (display) return display[1].trim();
  return answer.length < 2000 ? answer.trim() : "";
}
