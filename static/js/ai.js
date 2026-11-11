// Onglet « IA » du lecteur : l'assistant choisi sous le champ (Claude par défaut) explique un énoncé,
// détaille ou audite une preuve, répond aux questions. Il ne reçoit que le texte de l'article (aucun outil) ;
// ses réponses sont à vérifier.

import { api, streamAI } from "./api.js";
import { authorName, currentModel as modelFor, modelControls, modelLabel, providerOf, setupNotice } from "./aiproviders.js";
import { COLORS, rangeForQuote } from "./annotations.js";
import { markdownToHtml } from "./markdown.js";
import { typesetTex } from "./math.js";
import { hasText, t, tn } from "./i18n.js";
import { DEFAULT_AI_FONT_SIZE, saveSettings, settings } from "./settings.js";
import { escapeHtml, escapeRegExp, h, icons, plainTitle, toast, toastError } from "./util.js";

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
  theorem: "theorems?|théorèmes?|thms?\\.?", lemma: "lemmas?|lemmes?|lemmata", proposition: "propositions?|props?\\.",
  corollary: "corollar(?:y|ies)|corollaires?|cors?\\.", definition: "definitions?|définitions?", remark: "remarks?|remarques?",
  example: "examples?|exemples?", conjecture: "conjectures?", claim: "claims?|assertions?|steps?|étapes?",
  notation: "notations?|assumptions?|hypothès(?:e|es)|conventions?", problem: "problems?|problèmes?|questions?",
};
const KIND_OF_WORD = Object.entries(KIND_WORDS).map(([kind, words]) => [new RegExp(`^(?:${words})$`, "i"), kind]);
const NUMBER = "\\d+(?:\\.\\d+)*[a-z]?";
const NUMBER_END = "(?![\\p{L}\\p{N}]|\\.\\d)";  // « 2.3 » ne se relit pas « 2 » suivi de « .3 »
const GAP = "(?:\\s|&nbsp;)";
// Énumération après un mot au pluriel : « 3.6, 1.2, 1.3 », « 3.5–3.8 », « 2.1 to 2.4 ». Un « and » la ferme : dans
// « Theorems 1 and 2, 3 divides n », le 3 n'en est plus.
const LIST_OPEN = new RegExp(`${GAP}*,${GAP}*|-{1,2}|[–—]|${GAP}+(?:–|to|à)${GAP}+`, "iuy");
const LIST_CLOSE = new RegExp(`(?:${GAP}*,)?${GAP}+(?:and|or|et|ou)${GAP}+|(?:${GAP}*,)?${GAP}*&amp;${GAP}*`, "iuy");
const LIST_ITEM_END = `${NUMBER_END}(?!-\\p{L})`;  // « 3-manifolds » n'est pas un numéro
// énoncé ou section d'un AUTRE article (« Theorem 3.1 of [12] ») : à tester après la liste entière
const ELSEWHERE = /[^\S\n]*,?[^\S\n]+(?:of|in|from|de|du|dans|chez)[^\S\n]+(?:\[|the cited|l['’]article cité)/uy;
const matchAt = (re, text, at) => { re.lastIndex = at; return re.exec(text); };
// numéros qui suivent le premier, chacun avec sa position ; la liste s'arrête au premier qui ne va pas avec lui
function listAfter(text, from, first, item, sameList) {
  const items = [];
  for (let at = from, closed = false; !closed;) {
    const close = matchAt(LIST_CLOSE, text, at);
    const joint = close || matchAt(LIST_OPEN, text, at);
    const found = joint && matchAt(item, text, at + joint[0].length);
    if (!found || !sameList(first, found[0])) break;
    at += joint[0].length + found[0].length;
    items.push({ number: found[0], start: at - found[0].length, end: at });
    closed = Boolean(close);
  }
  return items;
}
const isPlural = (word) => /(?:s|§§|lemmata)\.?$/i.test(word);  // une énumération suit un mot au pluriel


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
    label.textContent = exchange.answer ? t("ai.writing") : exchange.phase ? t(`ai.phase.${exchange.phase}`, crossNames(exchange) || {}) : t("ai.thinking");
    clock.textContent = ` ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    hint.hidden = Boolean(exchange.answer) || seconds < 20;
  };
  let timer = null;
  tick();
  timer = setInterval(tick, 1000);
  return line;
}

// Relecture croisée : second assistant (celui demandé, sinon d'après les étapes enregistrées) et noms des deux,
// pour les étiquettes de la synthèse ([[a]], [[b]]) ; null pour tout autre échange
const secondOf = (exchange) => exchange.second || exchange.steps?.find((step) => step.kind !== "review")?.model || null;
function crossNames(exchange) {
  const second = secondOf(exchange);
  return second ? { a: modelLabel(exchange.model), b: modelLabel(second) } : null;
}
function whoOf(exchange) {
  const names = crossNames(exchange);
  return names ? `${names.a} + ${names.b}` : modelLabel(exchange.model);
}
// modèle d'un assistant d'après ses réglages (comme ai.model_of)
const modelOf = (provider) => modelFor({ ...settings, aiProvider: provider });

// Livre : relecture, résumé de tous les chapitres en parallèle (tâche du serveur, bookjob) ; cible de la synthèse
// question qui parle de notes, ou relance : seule une telle demande peut rendre au lecteur une note (author = "reader")
// en-tête d'une note écrite par un assistant (« **Claude** — *non vérifié* », « **Claude Opus · Résumé** — … »)
const ASSISTANT_HEADER = /^\s*\*\*[^*\n]{1,80}\*\* — /;
const ABOUT_NOTES = /\b(?:notes?|annotations?|surlign|highlights?|remarques?|relectures?|reviews?|comments?|commentaires?|refai|recommenc|r[eé]essa|retente|redo|retry|try\s+again|do\s+it\s+again|once\s+more|encore\s+une\s+fois|essa[iy]e\s+encore|de\s+nouveau)/i;
const BOOK = "@book";
const SECTIONS = "@sections";  // article, menu Portée : toutes les sections en parallèle, puis synthèse
const BOOK_TASKS = ["summary", "review"];
const jobRunning = (job) => Boolean(job) && ["chapters", "synthesis"].includes(job.phase);
// ce qui, en changeant, demande de recharger les échanges et de refaire le panneau (pas l'écoulement du temps)
const jobSignature = (jobs) => JSON.stringify(BOOK_TASKS.map((task) => jobs[task]
  && [jobs[task].phase, jobs[task].chapters.map((c) => `${c.state}:${c.exchange}`)]));
const duration = (seconds) => (seconds < 60 ? `${Math.round(seconds)} s` : `${Math.round(seconds / 60)} min`);  // durée totale
const minutes = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.round(seconds) % 60).padStart(2, "0")}`;

// Marqueurs de la synthèse, remplacés par `tag(sorte, mot)` (« Les deux », « Gemini »…)
const MARKER = /\[\[\s*(both|a|b|dispute|withdrawn|confirmed|uncertain)\s*\]\]/gi;
function withTags(text, names, tag) {
  return names ? text.replace(MARKER, (_, kind) => tag(kind.toLowerCase(), names[kind.toLowerCase()] || t(`ai.tag.${kind.toLowerCase()}`))) : text;
}

// Autres assistants prêts (relecture croisée), vérifiés au plus toutes les deux minutes (sauf réglages de l'IA modifiés) : la vérification lance
// leurs programmes (Claude, Codex) ou interroge Gemini ; une seule à la fois
const PARTNERS_TTL = 120_000;
let partnersCache = null;  // { provider, at, list (promesse) }
// compte changé ou déconnecté dans les réglages : états des assistants revérifiés au prochain affichage de l'onglet
let accounts = 0;
addEventListener("arxivreader:ai-accounts", () => { partnersCache = null; accounts++; });
function readyPartners(provider) {
  if (partnersCache?.provider !== provider || Date.now() - partnersCache.at > PARTNERS_TTL) {
    partnersCache = { provider, at: Date.now(), list: api.aiPartners().catch(() => []) };
  }
  return partnersCache.list;
}

export class Assistant {
  // `inPdf()` : article lu dans son PDF (la consigne mentionne alors les zones) ; `contextPages(center)` : pages du
  // PDF autour de `center` (page affichée si null), en images, pour un article sans texte (scan)
  // `pdfQuote(quote, near)` : passage proposé cherché dans le PDF par ses mots, { target, text } ou null (le PDF est
  // chargé au besoin) ; `views` : vues où placer les propositions — ["pdf"] pour un article lu dans son seul PDF,
  // ["html", "pdf"] pour un article qui a les deux (chaque proposition dit la sienne : `surface`, sinon le HTML)
  constructor({ paper, structure, find, navigate, annotator, onChange, equationIds, inPdf = () => false, contextPages = null,
    currentPage = null, readingChapter = () => null, pdfQuote = null, views = ["html"] }) {
    this.paper = paper;
    this.pdfQuote = pdfQuote;
    this.views = pdfQuote ? views : views.filter((view) => view !== "pdf");
    this.inPdf = inPdf;
    this.currentPage = currentPage;  // page du PDF qu'on lit
    this.readingChapter = readingChapter;  // livre : chapitre de l'endroit lu (résumé, relecture de ce chapitre par défaut)
    this.chapterOf = {};      // livre : chapitre choisi dans Résumé, Relecture (null : tout le livre)
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
    this.partners = null;     // autres assistants prêts (relecture croisée)
    this.relaunch = false;    // Relecture : barre de lancement rouverte par « Refaire » (simple ou croisée)
    this.jobs = {};           // livre : état des tâches « tous les chapitres » du serveur ({ summary, review })
    this.fresh = {};          // livre : refaire aussi les chapitres déjà faits (case de la barre de lancement)
    this.refs = this.buildRefs();
  }

  setStructure(structure) {
    this.structure = structure;
    // choix de chapitre gardés s'ils existent encore (une preuve indiquée refait la structure, pas les chapitres)
    this.sectionList = null;
    const ids = new Set([...this.chapters.map((c) => c.id), SECTIONS]);
    for (const view of Object.keys(this.chapterOf || {})) if (this.chapterOf[view] && !ids.has(this.chapterOf[view])) delete this.chapterOf[view];
    this.chapterOf ||= {};
    this.byId = new Map(structure.nodes.map((n) => [n.id, n]));
    this.refs = this.buildRefs();
  }

  async load() {
    try { this.exchanges = await api.aiExchanges(this.paper.id); } catch { this.exchanges = []; }
    if (this.chapters.length >= 2) {
      try { this.jobs = await api.aiBook(this.paper.id); } catch { this.jobs = {}; }
      if (BOOK_TASKS.some((task) => jobRunning(this.jobs[task]))) this.pollJobs();
    }
  }

  // Échanges relus (une tâche du livre en a ajouté) ; la demande en cours de cette page garde son objet, mis à jour
  // au fil de sa réponse. Les échanges retenus par une saisie (réponse à, question modifiée) passent aux nouveaux
  // objets : supprimer ou répondre vise toujours un échange de la liste
  async reloadExchanges() {
    let list;
    try { list = await api.aiExchanges(this.paper.id); } catch { return; }
    const live = this.running?.exchange;
    if (live && this.exchanges.includes(live)) {
      const order = (e) => (typeof e.id === "number" ? e.id : Infinity);
      list = [...list.filter((e) => e.id !== live.id), live].sort((a, b) => order(a) - order(b));
    }
    this.exchanges = list;
    const byId = new Map(list.map((e) => [e.id, e]));
    for (const pending of [this.pending, ...Object.values(this.docPending)]) {
      for (const key of ["parent", "replaces"]) {
        if (pending[key]) pending[key] = byId.get(pending[key].id) || null;  // supprimé ailleurs : la saisie le lâche
      }
    }
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

  // Autres assistants prêts : choix de l'assistant sous la saisie, relecture croisée
  async loadPartners() {
    const provider = providerOf(currentModel());
    if (this.partnersFor === provider) return;
    this.partnersFor = provider;
    const found = (await readyPartners(provider)).filter((p) => p.provider !== provider);
    if (this.partnersFor !== provider) return;  // assistant changé entre-temps
    const local = found.find((p) => p.provider === "local" && p.models?.length && !p.models.includes(settings.aiLocalModel));
    if (local) {  // modèle local pas encore choisi (ou disparu) : le premier, avant d'afficher les noms (relecture croisée)
      await saveSettings({ aiLocalModel: local.models[0] });
      local.model = `local:${local.models[0]}`;
      if (this.partnersFor !== provider) return;
    }
    const had = this.partners?.length;
    this.partners = found;
    if (found.length || had) this.onChange();  // aussi quand le dernier autre assistant vient de partir
  }

  // second assistant de la relecture croisée, si elle est choisie et possible (le dernier choisi, sinon le premier prêt)
  crossPartner() {
    if (settings.aiReviewMode !== "cross" || !this.partners?.length) return null;
    return this.partners.find((p) => p.provider === settings.aiSecond) || this.partners[0];
  }

  // modèle du vérificateur : le choix enregistré, sinon Sonnet, plus léger, s'il est proposé (comme ai.verifier) ; autres assistants : le même
  verifierModel() {
    const current = currentModel();
    if (providerOf(current) !== "claude") return current;
    const names = (this.status?.choices || []).map((c) => c.value);
    return names.includes(settings.aiVerifyModel) ? settings.aiVerifyModel : names.includes("sonnet") ? "sonnet" : current;
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
    for (const item of this.structure.bibliography || []) {
      const label = String(item.label || "").trim().replace(/^\[(.*)\]$/, "$1");
      const year = label.match(/^(.*\p{L}.*?)[\s,]*\(?((?:1[89]|20)\d\d[a-z]?)\)?$/u);
      if (year) authorYear.push([escapeHtml(year[1].trim()), year[2], item.id]);
      if (label && !cited.has(escapeHtml(label))) cited.set(escapeHtml(label), item.id);
    }
    const authorYearRe = authorYear.length ? new RegExp(authorYear.map(([who, year]) =>
      `(?<![\\p{L}\\p{N}])(${escapeRegExp(who)})(\\s*\\(\\s*${year}\\s*\\)|,?\\s+${year})(?![\\p{L}\\p{N}])`).join("|"), "gu") : null;
    const words = Object.values(KIND_WORDS).join("|");
    const notCited = "(?<!\\[[^\\[\\]\\n]{0,80})";
    // pas de \b : il ne connaît que les lettres ASCII (« étape 3 » ne serait jamais repéré)
    const statement = new RegExp(`${notCited}(?<![\\p{L}\\p{N}])(${words})${GAP}+(${NUMBER})${NUMBER_END}`, "giu");
    const statementItem = new RegExp(`${NUMBER}${LIST_ITEM_END}`, "iuy");
    // mot-clé sans casse, numéro avec (« la section a été… » n'est pas l'annexe A) ; espace obligatoire sauf après §
    const anyCase = (word) => word.replace(/\p{L}/gu, (c) => `[${c.toLowerCase()}${c.toUpperCase()}]`);
    const sectionWords = ["sections?", "sects?\\.", "secs?\\.", "appendix", "appendices", "annexes?", "chapters?", "chapitres?"].map(anyCase).join("|");
    const sectionNumber = "(?:[A-Z]|\\d+)(?:\\.\\d+)*";
    const section = new RegExp(`${notCited}(?<![\\p{L}\\p{N}])(?:(${sectionWords})${GAP}+|(§§?)${GAP}*)(${sectionNumber})${NUMBER_END}`, "gu");
    const sectionItem = new RegExp(`${sectionNumber}${LIST_ITEM_END}`, "uy");
    const sameDepth = (a, b) => a.split(".").length === b.split(".").length;  // « Theorems 1.2, 7 cases » : le 7 n'en est pas
    // chiffres avec chiffres ; lettres dans l'ordre (« Appendices B, A proof… » : ce A est un mot)
    const sameSections = (a, b) => sameDepth(a, b) && (/^\d/.test(a) ? /^\d/.test(b) : b > a);
    const equation = new RegExp(`\\((${NUMBER})\\)`, "g");
    const bracket = /\[([^\[\]\n]{1,80})\]/g;
    const link = (id, text) => `<a href="#${encodeURIComponent(id)}" class="ai-ref" data-ref="${id.replace(/"/g, "&quot;")}">${text}</a>`;
    // « Theorems 3.6, 1.2 » : le mot et le premier numéro forment le premier lien, puis un lien par numéro connu
    const linkRefs = (text, head, read, item, sameList) => {
      let html = "", done = 0, match;
      const piece = (id, label) => (id ? link(id, label) : label);
      head.lastIndex = 0;
      while ((match = head.exec(text))) {
        const { word, number, idOf } = read(match);
        const headEnd = head.lastIndex;
        const rest = isPlural(word) ? listAfter(text, headEnd, number, item, sameList) : [];
        const end = rest.length ? rest[rest.length - 1].end : headEnd;
        head.lastIndex = end;
        if (matchAt(ELSEWHERE, text, end)) continue;
        html += text.slice(done, match.index) + piece(idOf(number), match[0]);
        let at = headEnd;
        for (const entry of rest) {
          html += text.slice(at, entry.start) + piece(idOf(entry.number), entry.number);
          at = entry.end;
        }
        done = end;
      }
      return html + text.slice(done);
    };
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
      (text) => linkRefs(text, statement, ([, word, number]) => {
        const kind = KIND_OF_WORD.find(([re]) => re.test(word))?.[1];
        return { word, number, idOf: (n) => byKind.get(`${kind}:${n}`) || byNumber.get(n) };
      }, statementItem, sameDepth),
      (text) => linkRefs(text, section, ([, word, mark, number]) => ({ word: word || mark, number, idOf: (n) => sections.get(n) }), sectionItem, sameSections),
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

  // Actions sur un énoncé : expliquer, vérifier par le calcul ; et sur sa preuve, s'il en a une : détailler, auditer
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
      action("compute", t("ai.task.compute")),
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

  // `second` : relecture croisée avec cet assistant (claude, codex, gemini, local)
  // `check` : relecture enregistrée à vérifier (le même assistant tente de réfuter ses points graves, modèle du
  // vérificateur d'après les réglages) ; la relecture vérifiée est un nouveau document, rangé à la même place
  async ask({ task, target = null, quote = null, question = null, parent = null, zone = null, second = null, chapter = null, check = null }) {
    if (!this.canAsk()) return;
    const controller = new AbortController();
    if (zone) quote = zone.quote || null;
    if (check) target = check.target;
    const local = { id: `local-${Date.now()}`, startedAt: Date.now(), task, target: chapter || target, quote, question, parent_id: parent?.id ?? null, answer: "", status: "running",
      imageUrl: zone?.image || null, second: check ? this.verifierModel() : second && modelOf(second), steps: second || check ? [] : null, phase: null,
      model: this.status?.model || currentModel(), created_at: new Date().toISOString().slice(0, 19).replace("T", " ") };
    this.exchanges.push(local);
    let finish;
    let ended = false;  // l'événement final {done} est arrivé : sans lui, le flux s'est interrompu
    this.running = { exchange: local, controller, finished: new Promise((resolve) => { finish = resolve; }) };
    this.view = this.viewOf(local);
    if (this.byChapter(this.view)) this.chapterOf[this.view] = this.chapterOfExchange(local);
    this.positions[this.place(this.view)] = { top: 0, atBottom: true };
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
      const body = check ? { task, check: check.id, ...image, pages }
        : { task, target, quote, question, parent_id: parent?.id ?? null, ...image, pages, ...(second ? { second } : {}), ...(chapter ? { chapter } : {}) };
      await streamAI(this.paper.id, body, (event) => {
        if (event.exchange) Object.assign(local, event.exchange, { answer: local.answer, steps: local.steps });
        if (event.delta) { local.answer += event.delta; redraw(); }
        if (event.phase) local.phase = event.phase;  // ligne d'attente : mise à jour chaque seconde
        if (event.step) { local.steps.push(event.step); this.onChange(); }
        if (event.coverage) local.coverage = event.coverage;
        if (event.cut) local.cut = true;
        if (event.error) { local.error = event.error; local.errorCode = event.code; }
        if (event.done) { ended = true; Object.assign(local, event.exchange, { answer: event.exchange.answer || local.answer }); }
      }, controller.signal);
      if (!ended && !local.error) local.status = "stopped";  // connexion coupée en cours de réponse : pas une réponse terminée
    } catch (error) {
      if (error.name === "AbortError") local.status = "stopped";
      else { local.status = "error"; local.error = error.message; }
    }
    if (local.status === "running") local.status = local.error ? "error" : "done";
    if (local.error && ["login", "missing", "offline"].includes(local.errorCode)) this.status = { ...(this.status || {}), loggedIn: false };
    this.running = null;
    finish();
    this.onChange();
  }

  stop() {
    this.running?.controller.abort();
  }

  // Question qu'on peut modifier (« Edit ») : la dernière envoyée dans son fil, posée depuis le champ (question,
  // passage, zone du PDF) ; envoyée de nouveau, elle remplace l'échange, réponse comprise
  editable(exchange) {
    const view = this.viewOf(exchange);
    const place = this.place(view, this.chapterOfExchange(exchange));
    const last = this.exchanges.filter((e) => this.viewOf(e) === view && this.place(view, this.chapterOfExchange(e)) === place
      && !["summary", "review"].includes(e.task)).at(-1);
    return exchange === last && ["ask", "passage", "zone"].includes(exchange.task) && (exchange.task !== "zone" || exchange.imageUrl || exchange.image);
  }

  // « Edit » : réponse en cours arrêtée (et enregistrée), question remise dans le champ avec son contexte (passage,
  // zone du PDF, échange auquel elle répond) ; l'envoi la remplace (l'ancien échange est supprimé, discard)
  async edit(exchange) {
    if (this.running?.exchange === exchange) {
      this.stop();
      await this.running?.finished;
    }
    const view = this.viewOf(exchange);
    const pending = this.draft(view, this.chapterOfExchange(exchange));
    Object.assign(pending, { question: exchange.question || "", replaces: exchange });
    if (view === "questions") {
      const zone = exchange.task === "zone"
        ? { image: exchange.imageUrl || await dataUrl(`/api/ai/${exchange.id}/image`), page: this.currentPage?.() || 1, quote: exchange.quote } : null;
      Object.assign(pending, { quote: exchange.task === "passage" ? exchange.quote : null, zone,
        parent: exchange.parent_id ? this.exchanges.find((e) => e.id === exchange.parent_id) || null : null });
    }
    this.view = view;
    this.onChange({ focus: true });
  }

  // Échange remplacé par sa question modifiée : supprimé, et retiré du fil, avant le nouvel envoi
  async discard(exchange) {
    try {
      if (typeof exchange.id === "number") await api.deleteAIExchange(exchange.id);
    } catch (error) {
      toastError(error);
      return false;
    }
    this.exchanges = this.exchanges.filter((e) => e.id !== exchange.id);
    return true;
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
      this.exchanges = this.exchanges.filter((x) => x.id !== e.id);
      if (this.pending.parent?.id === e.id) this.pending.parent = null;
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
    const header = `**${whoOf(exchange)} · ${this.title(exchange)}** — *${exchange.created_at.slice(0, 10)}, ${t("ai.unverified")}*`;
    const asked = exchange.question ? `\n\n> ${exchange.question.replace(/\n/g, " ")}` : "";
    try {
      await this.annotator.appendNote(id, `${header}${asked}\n\n${this.plainText(exchange)}`);
      toast(id ? t("ai.savedBlock", { label: node.label }) : t("ai.savedGeneral"));
    } catch (error) {
      toastError(error);
    }
  }

  // texte d'une réponse pour les notes et le presse-papiers : sans les propositions, étiquettes écrites en clair
  plainText(exchange) {
    return withTags(parseProposals(exchange.answer).text.trim(), crossNames(exchange), (_, word) => `**${word}**`);
  }

  title(exchange) {
    if (exchange.target === BOOK) return t(`ai.title.${exchange.task}${this.isArticle ? "Sections" : "Book"}`);
    const chapter = ["summary", "review"].includes(exchange.task) && this.chapterOfExchange(exchange);
    if (chapter) return t(`ai.title.${exchange.task}Chapter`, { label: this.chapterTitle(chapter) });
    const node = this.byId.get(exchange.target);
    const label = node?.label || "";
    if (exchange.task === "compute" && !node) return t("ai.title.computePassage");  // passage ou zone : pas d'énoncé nommé
    return t(`ai.title.${exchange.task}`, { label });
  }

  // ---------- Onglet ----------
  // Trois fils, en sous-onglets : Questions (questions sur l'article, suites par « Répondre »), Résumé et Relecture
  // (le document, puis les questions posées sur lui). Chacun : fil chronologique, saisie en bas, boutons ↑ ↓.

  // fil d'un échange : celui de son premier ancêtre (résumé, relecture, sinon questions)
  viewOf(exchange) {
    const root = this.rootOf(exchange);
    return root?.task === "summary" ? "summary" : root?.task === "review" ? "review" : "questions";
  }

  rootOf(exchange) {
    const byId = new Map(this.exchanges.map((e) => [e.id, e]));
    let root = exchange;
    for (let depth = 0; root?.parent_id && byId.has(root.parent_id) && depth < 50; depth++) root = byId.get(root.parent_id);
    return root;
  }

  // ---------- Livre : résumé, relecture d'un chapitre ----------

  // Chapitres d'un livre, sinon sections d'un article : ce qu'on résume ou relit séparément
  get chapters() {
    if (this.structure.chapters?.length) return this.structure.chapters;
    return (this.sectionList ||= (this.structure.sections || []).map((s) => ({ ...s, title: plainTitle(s.title) })));
  }

  get isArticle() { return !this.structure.chapters?.length; }

  // Textes de la tâche parallèle : ceux des sections sur un article, s'ils diffèrent de ceux d'un livre
  scopedKey(key, suffix = "") {
    const section = key.replace("ai.book.", "ai.sections.");
    return this.isArticle && hasText(section + suffix) ? section : key;
  }

  tb(key, params) { return t(this.scopedKey(key), params); }

  tnb(key, n) { return tn(this.scopedKey(key, ".other"), n); }

  chapterTitle(id) { return this.chapters.find((c) => c.id === id)?.title || ""; }

  // chapitre d'un résumé ou d'une relecture, et des questions posées dessus ; null : tout le livre
  chapterOfExchange(exchange) {
    const target = this.rootOf(exchange)?.target;
    if (this.isArticle && target === BOOK) return this.chapters.length >= 2 ? SECTIONS : null;  // plus de sections : refaite en entier
    return this.chapters.some((c) => c.id === target) ? target : null;
  }

  // Résumé, Relecture d'un livre : un chapitre à la fois ; position de lecture gardée par chapitre
  byChapter(view) { return view !== "questions" && this.chapters.length >= 2; }

  place(view, chapter = this.chapterOf[view]) { return this.byChapter(view) ? `${view}:${chapter || ""}` : view; }

  // question en cours de saisie (et « Edit » en cours) : une par fil, donc par chapitre dans un livre
  // question écrite d'avance dans l'onglet Questions, ouvert ; envoyée seulement par le lecteur
  // question écrite d'avance (non envoyée) : brouillon repris de zéro — passage, zone, réponse à un échange ou
  // modification en cours l'enverraient par un autre circuit, sans les blocs de l'adaptation (ai.py, build_prompt)
  prefill(question) {
    this.view = "questions";
    Object.assign(this.pending, { quote: null, zone: null, parent: null, replaces: null, question });
    this.onChange({ show: true, focus: true });
  }

  draft(view, chapter = this.chapterOf[view]) {
    return view === "questions" ? this.pending : (this.docPending[this.place(view, chapter)] ||= { question: "" });
  }

  // Énoncés d'autres chapitres que le chapitre utilise, et ceux qui en dépendent, directement ou par une chaîne :
  // comme ai.chapter_links (ce que l'assistant reçoit), montrés avant l'envoi. Article : ce qu'une preuve utilise compte
  // dans la section où elle se trouve (troisième liste : énoncés d'autres sections prouvés dans celle-ci)
  chapterLinks(chapter) {
    const nodes = this.structure.nodes;
    const here = nodes.filter((n) => n.chapter === chapter).map((n) => n.id);
    const worked = this.isArticle ? nodes.filter((n) => (n.provedIn?.length ? n.provedIn : [n.chapter]).includes(chapter)).map((n) => n.id) : here;
    const own = new Set([...here, ...worked]);
    const used = new Set(worked.flatMap((id) => this.byId.get(id)?.uses || []).filter((id) => this.byId.has(id) && !own.has(id)));
    const reached = new Set();
    const todo = [...own];
    while (todo.length) {
      for (const user of this.byId.get(todo.pop())?.usedBy || []) {
        if (this.byId.has(user) && !own.has(user) && !reached.has(user)) { reached.add(user); todo.push(user); }
      }
    }
    const inOrder = (set) => this.structure.nodes.filter((n) => set.has(n.id));
    return [inOrder(used), inOrder(reached), this.isArticle ? nodes.filter((n) => own.has(n.id) && n.chapter !== chapter) : []];
  }

  // Avant l'envoi : les énoncés joints, cliquables (aperçu au survol, comme les renvois des réponses)
  chapterLinksBox(chapter) {
    const [used, dependents, proved] = this.chapterLinks(chapter);
    const uses = (key) => t(this.isArticle ? `ai.sections.${key}` : `ai.uses.${key}`);
    const short = (title) => (title.length > 28 ? `${title.slice(0, 27)}…` : title);
    const chips = (nodes) => (nodes.length ? h("div", { class: "ai-uses" }, nodes.map((n) => h("a", { href: `#${n.id}`, class: "ai-ref", dataset: { ref: n.id },
      onclick: (event) => { event.preventDefault(); this.navigate(n.id); } }, n.label, h("span", { class: "muted" }, ` · ${short(this.chapterTitle(n.chapter))}`))))
      : h("div", { class: "ai-uses muted small" }, t("ai.uses.none")));
    return [...(proved.length ? [h("div", { class: "ai-uses-title" }, t("ai.sections.proved")), chips(proved)] : []),
      h("div", { class: "ai-uses-title" }, uses("before")), chips(used), h("div", { class: "ai-uses-title" }, uses("after")), chips(dependents)];
  }

  // Sous les sous-onglets : tous les chapitres en parallèle, puis synthèse (ou résumé d'ensemble), sinon un chapitre
  // (✓ : déjà fait ; … : tâche du livre en cours). Article : d'abord l'article entier, comme s'il n'y avait pas de menu
  chapterBar(view) {
    const task = view === "summary" ? "summary" : "review";
    const done = new Set(this.exchanges.filter((e) => e.task === task && e.status === "done" && typeof e.id === "number").map((e) => this.chapterOfExchange(e) || ""));
    const mark = (id) => (done.has(id) ? "  ✓" : "");
    const select = h("select", { class: "ai-model", "aria-label": t("ai.chapter"), title: t("ai.chapter"),
      onchange: () => { this.chapterOf[view] = select.value || null; this.focusChapter = true; this.onChange(); } },
    ...(this.isArticle ? [h("option", { value: "", selected: !this.chapterOf[view] }, t(`ai.sections.full.${task}`) + mark(""))] : []),
    h("option", { value: this.isArticle ? SECTIONS : "", selected: this.isArticle ? this.chapterOf[view] === SECTIONS : !this.chapterOf[view] },
      this.tb(`ai.book.all.${task}`) + (jobRunning(this.jobs[task]) ? "  …" : mark(this.isArticle ? SECTIONS : ""))),
    h("optgroup", { label: t(this.isArticle ? "ai.sections.list" : "ai.chapters") },
      this.chapters.map((c) => h("option", { value: c.id, selected: c.id === this.chapterOf[view] }, c.title + mark(c.id)))));
    return h("div", { class: "ai-chapterbar" }, h("span", {}, t("ai.chapter")), select);
  }

  // ---------- Livre : tous les chapitres en parallèle, puis synthèse (tâche de fond du serveur, bookjob) ----------

  // résumé, relecture de tout le livre (de toutes les sections d'un article) : l'entrée « en parallèle » du menu
  bookPlace(view) { return this.byChapter(view) && (this.isArticle ? this.chapterOf[view] === SECTIONS : !this.chapterOf[view]); }

  // Chapitre -> son document terminé qu'une nouvelle tâche reprend tel quel (relecture : avec sa fiche), comme
  // bookjob.reusable
  reusable(task) {
    const found = new Map();
    for (const e of this.exchanges) {
      if (e.task === task && !e.parent_id && e.status === "done" && typeof e.id === "number" && e.answer && !e.cut
        && this.chapters.some((c) => c.id === e.target) && (task !== "review" || e.card)) found.set(e.target, e);
    }
    return found;
  }

  async startBook(task, second = null) {
    if (this.status && !this.status.loggedIn) {
      this.onChange({ show: true });
      toast(t("ai.loginFirst"), { error: true });
      return;
    }
    try {
      this.jobsStamp = (this.jobsStamp || 0) + 1;  // un relevé parti avant le lancement ne l'efface pas (pollJobs)
      this.jobs[task] = await api.startAIBook(this.paper.id, { task, fresh: Boolean(this.fresh[task]), ...(second ? { second } : {}) });
    } catch (error) {
      toastError(error);
      return;
    }
    this.relaunch = false;
    this.fresh[task] = false;
    this.positions[this.place(this.view)] = { top: 0, atBottom: true };
    this.pollJobs();
    this.onChange();
  }

  async stopBook(task) {
    this.jobsStamp = (this.jobsStamp || 0) + 1;
    try { this.jobs[task] = await api.stopAIBook(this.paper.id, task); } catch (error) { toastError(error); }
    await this.reloadExchanges();
    this.onChange();
  }

  // Suivi des tâches en cours, toutes les 2 s tant qu'il y en a : un chapitre fini (ou une étape) recharge les échanges
  // et refait le panneau (✓ du menu, documents) ; sinon la progression est mise à jour sur place (temps, synthèse)
  pollJobs() {
    if (this.pollTimer || this.destroyed) return;
    const tick = async () => {
      let next = null;
      const stamp = this.jobsStamp;
      try {
        next = await api.aiBook(this.paper.id);
        this.pollFailures = 0;
      } catch (error) {
        // article supprimé ailleurs : plus rien à suivre ; sinon (réseau, serveur) nouvel essai, de plus en plus espacé
        this.pollFailures = (this.pollFailures || 0) + 1;
        if (error.status === 404 && !this.destroyed) {
          this.jobs = {};
          this.pollTimer = null;
          this.onChange();  // panneau refait sans la tâche (bouton Arrêter, progression)
          return;
        }
      }
      if (this.destroyed) return;
      if (next && stamp !== this.jobsStamp) next = null;  // lancement ou arrêt pendant le relevé : relevé suivant
      if (next) {
        const ended = BOOK_TASKS.filter((task) => jobRunning(this.jobs[task]) && !jobRunning(next[task]));
        const changed = jobSignature(next) !== jobSignature(this.jobs);
        this.jobs = next;
        if (changed) {
          await this.reloadExchanges();
          if (this.destroyed) return;
          for (const task of ended) {
            const job = next[task];
            if (job?.phase === "error") toast(t(ERROR_KEYS[job.code] || "ai.error.other", { message: job.error || "" }), { error: true });
            else if (job?.phase === "done") {
              toast(this.tb(`ai.book.finished.${task}`, { time: duration(job.elapsed || 0) }));
              delete this.positions[`${task}:${this.isArticle ? SECTIONS : ""}`];  // synthèse seule : lue depuis son début
            }
          }
          this.onChange();
        } else this.bookLive?.(next);
      }
      const wait = this.pollFailures >= 3 ? 10000 : 2000;
      this.pollTimer = BOOK_TASKS.some((task) => jobRunning(this.jobs[task])) ? setTimeout(tick, wait) : null;
    };
    this.pollTimer = setTimeout(tick, 2000);
  }

  // Ligne d'un chapitre (ou de la synthèse) : état, titre (un chapitre fait s'ouvre d'un clic), note
  bookItem(view, chapter, state) {
    const name = chapter ? h("button", { type: "button", class: "ai-book-name", onclick: () => { this.chapterOf[view] = chapter.id; this.onChange(); } }, chapter.title)
      : h("span", { class: "ai-book-name" }, this.tb(`ai.book.synthesis.${view}`));
    const item = h("li", { class: chapter ? "" : "synthesis" }, h("span", { class: "ai-book-mark", "aria-hidden": "true" }), name, h("span", { class: "ai-book-note" }));
    const set = ([kind, mark, note]) => {
      item.classList.remove("done", "run", "wait");
      item.classList.add(kind);
      item.firstChild.textContent = mark;
      item.lastChild.textContent = note;
      if (chapter) name.disabled = kind !== "done";
    };
    set(state);
    item.set = set;
    return item;
  }

  // Avant le lancement : ce qui sera fait, chapitre par chapitre (✓ : déjà fait, repris tel quel)
  bookIntro(view) {
    const task = view === "summary" ? "summary" : "review";
    const cross = view === "review" && this.crossPartner();
    const reused = this.fresh[task] ? new Map() : this.reusable(task);
    const intro = cross ? this.tb("ai.book.intro.cross", { a: modelLabel(currentModel()), b: modelLabel(modelOf(cross.provider)) }) : this.tb(`ai.book.intro.${task}`);
    return h("div", { class: "ai-empty ai-empty-doc ai-book" }, h("div", { class: "ai-empty-title" }, this.tb(`ai.book.title.${task}`)), h("p", {}, intro),
      h("ul", { class: "ai-book-list" }, this.chapters.map((c) => this.bookItem(view, c, reused.has(c.id)
        ? ["done", "✓", this.tb(`ai.book.state.reused.${task}`)] : ["wait", "·", this.tb(`ai.book.state.todo.${task}`)])),
      this.bookItem(view, null, ["wait", "Σ", this.tb("ai.book.state.atEnd")])),
      h("p", { class: "hint ai-book-hint" }, this.tb(`ai.book.hint.${task}`)));
  }

  // Tâche en cours : barre, état de chaque chapitre, temps écoulé depuis le lancement (une estimation du temps restant
  // se trompait souvent : les chapitres ont des durées très différentes), synthèse qui s'écrit
  bookProgress(view, task) {
    const bar = h("span", {});
    const status = h("p", { class: "hint ai-book-status", role: "status" });
    const items = new Map(this.chapters.map((c) => [c.id, this.bookItem(view, c, ["wait", "·", ""])]));
    const synthesis = this.bookItem(view, null, ["wait", "Σ", ""]);
    const text = h("div", { class: "markdown ai-answer ai-book-text" });
    const live = new LiveAnswer(text, this.refs);
    const update = (jobs) => {
      const job = jobs[task];
      if (!jobRunning(job)) return;
      const finished = job.chapters.filter((c) => ["done", "reused"].includes(c.state));
      for (const c of job.chapters) {
        items.get(c.id)?.set(c.state === "reused" ? ["done", "✓", this.tb(`ai.book.state.reused.${task}`)]
          : c.state === "done" ? ["done", "✓", this.tb("ai.book.state.done", { time: minutes(c.seconds || 0) })]
            : c.state === "running" ? ["run", "◌", this.tb("ai.book.state.running", { time: minutes(c.elapsed || 0) })]
              : ["wait", "·", this.tb("ai.book.state.waiting")]);
      }
      synthesis.set(job.phase === "synthesis" ? ["run", "Σ", this.tb("ai.book.state.writing")] : ["wait", "Σ", this.tb("ai.book.state.after")]);
      bar.style.width = `${Math.round(100 * (finished.length + (job.phase === "synthesis" ? 0.5 : 0)) / (job.chapters.length + 1))}%`;
      const running = job.chapters.filter((c) => c.state === "running").length;
      status.textContent = (job.phase === "synthesis" ? this.tb(`ai.book.status.synthesis.${task}`, { n: finished.length })
        : this.tb("ai.book.status.chapters", { done: finished.length, total: job.chapters.length, running }))
        + ` · ${this.tb("ai.book.status.elapsed", { time: minutes(job.elapsed || 0) })}`;
      if (job.synthesis?.answer) live.update(job.synthesis.answer, false);
    };
    update(this.jobs);
    this.bookLive = (jobs) => { if (status.isConnected) update(jobs); };
    return h("div", { class: "ai-empty ai-empty-doc ai-book" }, h("div", { class: "ai-empty-title" }, this.tb(`ai.book.running.${task}`)),
      h("div", { class: "ai-book-bar" }, bar), status, h("ul", { class: "ai-book-list" }, [...items.values()], synthesis),
      h("p", { class: "hint ai-book-hint" }, this.tb(`ai.book.continue.${task}`)), text);
  }

  // Synthèse du livre : un lien vers le document de chaque chapitre
  bookChips(view) {
    const task = view === "summary" ? "summary" : "review";
    const done = new Set(this.exchanges.filter((e) => e.task === task && !e.parent_id && e.status === "done").map((e) => e.target));
    const chapters = this.chapters.filter((c) => done.has(c.id));
    return chapters.length ? h("div", { class: "ai-uses ai-book-chips" }, h("span", { class: "muted small" }, this.tb(`ai.book.chips.${task}`)),
      chapters.map((c) => h("button", { type: "button", class: "ai-ref", onclick: () => { this.chapterOf[view] = c.id; this.onChange(); } }, c.title))) : null;
  }

  renderPanel(body, { focus = false } = {}) {
    if (this.accounts !== accounts) { // compte changé dans les réglages
      this.accounts = accounts;
      this.status = null;
      this.partnersFor = null;
    }
    if ((!this.status && !this.checking) || this.statusFor !== currentModel()) this.refreshStatus(); // assistant changé
    body.classList.add("ai-body");
    const view = this.view;
    const status = this.status;
    this.live = null;
    const book = this.byChapter(view);
    if (book && !(view in this.chapterOf)) this.chapterOf[view] = this.readingChapter() || null;
    const chapter = book ? this.chapterOf[view] : null;
    const place = this.place(view);
    this.shown = place;
    const task = view === "summary" ? "summary" : "review";
    const whole = this.bookPlace(view);  // livre : tous les chapitres, puis synthèse
    const job = whole && this.jobs[task];
    // synthèse en cours : montrée par la progression de la tâche, pas comme un document vide
    const exchanges = this.exchanges.filter((e) => this.viewOf(e) === view && (!book || this.chapterOfExchange(e) === chapter)
      && !(jobRunning(job) && e.id === job.synthesis?.exchange));
    this.loadPartners();
    const notice = status && !status.loggedIn ? setupNotice(status, () => { this.status = null; this.onChange(); }) : null;

    const content = h("div", { class: "ai-thread-content" });
    const thread = h("div", { class: "ai-thread", tabindex: 0, "aria-label": t(`ai.view.${view}`) }, content);
    if (view === "questions") {
      if (exchanges.length) exchanges.forEach((e) => this.renderExchange(content, e));
      else content.append(h("p", { class: "hint ai-empty" }, t("ai.emptyQuestions") + (this.inPdf() && this.readsImages() ? ` ${t("ai.hintZone")}` : "")));
    } else {
      const docs = exchanges.filter((e) => e.task === task);
      if (exchanges.length) exchanges.forEach((e) => (docs.includes(e) ? this.renderDocument(content, e, e === docs[docs.length - 1])
        : this.renderExchange(content, e, { reply: false })));
      else if (whole && !jobRunning(job)) content.append(this.bookIntro(view));
      else if (!whole) {
        const cross = view === "review" && this.crossPartner();
        const kind = cross ? "cross" : null;
        const label = chapter && this.chapterTitle(chapter);
        const section = chapter && this.isArticle;
        const title = chapter ? t(kind ? `ai.title.${kind}Chapter` : `ai.title.${view}Chapter`, { label }) : t(kind ? `ai.title.${kind}` : `ai.title.${view}`);
        const names = cross ? { a: modelLabel(currentModel()), b: modelLabel(modelOf(cross.provider)) } : null;
        const intro = kind ? t(section ? "ai.sections.introCross" : `ai.intro.${kind}`, names)
          : t(section ? "ai.sections.intro" : chapter ? "ai.intro.chapter" : `ai.intro.${view}`);
        content.append(h("div", { class: "ai-empty ai-empty-doc" }, h("div", { class: "ai-empty-title" }, title), h("p", {}, intro),
          chapter ? this.chapterLinksBox(chapter) : null));
      }
      this.bookLive = null;
      if (jobRunning(job)) content.append(this.bookProgress(view, task));
      else if (job?.phase === "error") {
        content.append(h("div", { class: "notice small ai-book-error" }, t(ERROR_KEYS[job.code] || "ai.error.other", { message: job.error || "" })));
      }
    }

    // ↑ en haut à droite, ↓ en bas à droite (dans la marge du fil : le texte n'est jamais couvert) ; seulement s'ils servent
    const up = h("button", { class: "ai-jump up", type: "button", title: t("ai.toTop"), "aria-label": t("ai.toTop"), html: ARROW_UP, hidden: true,
      onclick: () => thread.scrollTo({ top: 0, behavior: "smooth" }) });
    const dot = h("span", { class: "ai-jump-dot", hidden: true });
    const last = exchanges.length ? exchanges[exchanges.length - 1].id : null;
    if (!(place in this.seen)) this.seen[place] = last;
    const down = h("button", { class: "ai-jump down", type: "button", title: t("ai.toBottom"), "aria-label": t("ai.toBottom"), html: ARROW_DOWN, hidden: true,
      onclick: () => thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" }) }, dot);
    let scrollbar = null;
    const update = () => {
      if (!thread.isConnected) return;
      const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 24;
      this.positions[place] = { top: thread.scrollTop, atBottom };
      if (atBottom) this.seen[place] = last;
      up.hidden = thread.scrollTop < 24;
      down.hidden = atBottom;
      dot.hidden = atBottom || this.seen[place] === last;
      // barre de défilement classique (Windows) : les boutons restent à sa gauche ; posée seulement si elle change
      // (à chaque défilement, elle referait le style de tout le fil)
      const width = thread.offsetWidth - thread.clientWidth;
      if (width !== scrollbar) frame.style.setProperty("--ai-scrollbar", `${(scrollbar = width)}px`);
    };
    thread.addEventListener("scroll", update, { passive: true });
    // formules mises en forme après coup, images : le fil grandit ; s'il était en bas, il y reste
    this.threadObserver?.disconnect(); // fil du rendu précédent
    this.threadObserver = new ResizeObserver(() => {
      if (!thread.isConnected) return;
      if (this.positions[place]?.atBottom) thread.scrollTop = thread.scrollHeight;
      update();
    });
    this.threadObserver.observe(content);
    this.thread = thread;
    this.updateJumps = update;

    const frame = h("div", { class: "ai-frame" }, thread, up, down);
    body.append(this.viewBar(), book ? this.chapterBar(view) : "", ...(notice ? [notice] : []), frame, this.bottomBar(view, exchanges, focus));
    if (this.focusChapter) { // menu du chapitre changé au clavier : il garde le focus (panneau refait, attaché ensuite)
      this.focusChapter = false;
      requestAnimationFrame(() => body.querySelector(".ai-chapterbar select")?.focus({ preventScroll: true }));
    }
    // position de lecture : celle laissée ; la première fois, le document depuis le haut, sinon la dernière réponse
    requestAnimationFrame(() => {
      const kept = this.positions[place];
      const first = view !== "questions" && exchanges.length <= 1;  // document seul, ou présentation avant le lancement
      thread.scrollTop = kept ? (kept.atBottom ? thread.scrollHeight : kept.top) : first ? 0 : thread.scrollHeight;
      update();
    });
  }

  // le fil suit la réponse qui s'écrit, si l'on est en bas
  followAnswer() {
    if (!this.thread?.isConnected) return;
    if (this.positions[this.shown]?.atBottom !== false) this.thread.scrollTop = this.thread.scrollHeight;
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
    // réponse en cours de cette page, tâche du livre en cours : signalées sur leur sous-onglet
    const busy = new Set([this.running && this.viewOf(this.running.exchange), ...BOOK_TASKS.filter((task) => jobRunning(this.jobs[task]))]);
    const seg = h("div", { class: "ai-views", role: "group", "aria-label": t("ai.views") }, views.map((view) =>
      h("button", { type: "button", class: `ai-view${busy.has(view) ? " busy" : ""}`, "aria-pressed": String(this.view === view),
        title: busy.has(view) ? t("ai.busyHere") : undefined, onclick: () => this.showView(view) }, t(`ai.view.${view}`))));
    return h("div", { class: "ai-topbar" }, seg, h("span", { class: "spacer" }), this.textSizeBar(), this.shareLink({ compact: true }));
  }

  // Barre du bas : la saisie (Questions ; Résumé et Relecture une fois faits), sinon le bouton qui les lance
  bottomBar(view, exchanges, focus) {
    const running = Boolean(this.running);
    const footer = h("p", { class: "ai-footnote", title: t(`ai.privacy.${providerOf(currentModel())}`) }, t("ai.footnote"));
    const task = view === "summary" ? "summary" : "review";
    const whole = this.bookPlace(view);
    if (whole && jobRunning(this.jobs[task])) {  // tâche du livre en cours : on peut l'arrêter (chapitres finis gardés)
      return h("div", { class: "ai-bottom" }, h("button", { class: "button ai-launch", type: "button", onclick: () => this.stopBook(task) },
        this.tb("ai.book.stop")), footer);
    }
    // chapitre relu (ou résumé) par la tâche du livre en ce moment : rien à demander avant la fin (seulement un échange
    // de cette tâche : une demande en cours ailleurs, sur un article qui n'est pas un livre, n'est pas un chapitre)
    const job = jobRunning(this.jobs[task]) ? this.jobs[task] : null;
    const jobExchanges = new Set([...(job?.chapters || []).map((c) => c.exchange), job?.synthesis?.exchange]
      .filter((id) => id != null));
    if (view !== "questions" && exchanges.some((e) => e.status === "running" && jobExchanges.has(e.id))) {
      return h("div", { class: "ai-bottom" }, h("p", { class: "muted small ai-book-busy" }, this.tb(`ai.book.chapterBusy.${task}`)), footer);
    }
    const relaunch = view === "review" && this.relaunch && !running;
    if (view !== "questions" && (!this.lastDocument(view, exchanges) || relaunch) && !exchanges.some((e) => e.status === "running")) {
      const chapter = this.byChapter(view) ? this.chapterOf[view] : null;
      const cross = view === "review" && this.crossPartner();
      const kind = cross ? "cross" : null;
      // livre, tous les chapitres : nombre de chapitres à faire (ceux déjà faits sont repris, sauf si l'on coche)
      const reused = whole ? this.reusable(task).size : 0;
      const todo = whole && this.chapters.length - (this.fresh[task] ? 0 : reused);
      const label = whole ? (todo ? this.tnb(`ai.book.launch.${kind || task}`, todo) : this.tb(`ai.book.launch.${task}Only`))
        : t(kind ? `ai.launch.${kind}` : chapter ? (this.isArticle ? `ai.sections.launch.${task}` : `ai.launch.${task}Chapter`) : `ai.launch.${task}`);
      const launch = running && !whole
        ? h("button", { class: "button ai-launch", type: "button", onclick: () => this.stop() }, t("ai.stop"))
        : h("button", { class: "button primary ai-launch", type: "button", onclick: () => {
          this.relaunch = false;
          if (whole) this.startBook(task, cross?.provider);
          else this.ask({ task, second: cross?.provider, chapter });
        } }, label);
      const fresh = whole && reused > 0 && h("label", { class: "ai-book-fresh small" }, h("input", { type: "checkbox", checked: Boolean(this.fresh[task]),
        onchange: (event) => { this.fresh[task] = event.target.checked; this.onChange(); } }), ` ${this.tnb(`ai.book.fresh.${task}`, reused)}`);
      // « Refaire » une relecture : retour au champ des questions sans rien lancer
      const cancel = relaunch && h("button", { class: "button", type: "button", onclick: () => { this.relaunch = false; this.onChange(); } }, t("ai.cancelRedo"));
      const picker = this.modelPicker(cross?.provider) || h("span", { class: "muted small" }, modelLabel(currentModel()));
      // coût : nombre de demandes de la taille de l'article (ou du chapitre d'un livre), sur une ligne sous le bouton
      const calls = kind === "cross" ? 4 : 1;
      const requests = whole ? (todo ? todo * calls + 1 : 1) : calls;
      const unit = this.isArticle || !(whole || chapter) ? "paper" : "chapter";
      const cost = !running && requests > 1 ? h("p", { class: "muted small ai-cost" }, t(`ai.cost.${unit}`, { n: requests })) : null;
      return h("div", { class: "ai-bottom" }, view === "review" ? this.reviewMode(kind) : null,
        cancel ? h("div", { class: "ai-launch-row" }, launch, cancel) : launch, cost, fresh,
        cross ? this.reviewers(picker, cross) : h("div", { class: "ai-foot" }, picker), footer);
    }
    return h("div", { class: "ai-bottom" }, this.askBox(view, exchanges, focus), footer);
  }

  // Relecture simple ou croisée, si un autre assistant est prêt (sinon rien à choisir) ; le choix est retenu
  reviewMode(kind) {
    if (!this.partners?.length) return null;
    const modes = ["single", "cross"];
    const button = (mode) => h("button", { type: "button", class: "ai-view", "aria-pressed": mode === (kind || "single"),
      onclick: () => saveSettings({ aiReviewMode: mode }).then(() => this.onChange()) }, t(`ai.mode.${mode}`));
    return h("div", { class: "ai-mode" }, h("div", { class: "ai-views", role: "group", "aria-label": t("ai.mode") }, modes.map(button)));
  }

  // Vérification d'une relecture : le vérificateur, du même assistant ; son modèle et son niveau de réflexion se
  // choisissent à part (Claude ; les autres assistants se vérifient eux-mêmes, avec le même modèle)
  verifierControls() {
    const model = this.verifierModel();
    const choices = this.status?.provider === "claude" && this.status.loggedIn ? this.status.choices || [] : [];
    let controls = [h("span", { class: "muted small" }, modelLabel(model))];
    if (choices.length) {
      const levels = choices.find((c) => c.value === model)?.levels || [];
      const choose = h("select", { class: "ai-model", name: "aiVerifyModel", "aria-label": t("ai.verifier.model"), title: t("ai.verifier.model"),
        onchange: (event) => saveSettings({ aiVerifyModel: event.target.value, aiVerifyEffort: "" }).then(() => this.onChange()) },
      choices.map((c) => h("option", { value: c.value, selected: c.value === model }, c.label)));
      const effort = levels.length ? h("select", { class: "ai-model ai-effort", name: "aiVerifyEffort", "aria-label": t("ai.effort"), title: t("ai.verifier.effortHint"),
        onchange: (event) => saveSettings({ aiVerifyEffort: event.target.value }).then(() => this.onChange()) },
      ["", ...levels].map((level) => h("option", { value: level, selected: level === (levels.includes(settings.aiVerifyEffort) ? settings.aiVerifyEffort : "") },
        level ? t(`ai.effort.${level}`) : t("ai.verifier.effortSame")))) : null;
      controls = [choose, effort];
    }
    return controls;
  }

  // Sous la dernière relecture terminée : « Vérifier les points graves » (deux demandes de plus), sauf si elle l'est
  // déjà, ou si c'est la synthèse d'un livre (vérifier plutôt la relecture de chaque chapitre)
  verifyBox(exchange) {
    if (exchange.task !== "review" || exchange.status !== "done" || typeof exchange.id !== "number" || !exchange.answer
      || exchange.parent_id || exchange.steps?.some((step) => step.kind === "verify") || (exchange.target === BOOK && !this.isArticle)) return null;
    if (this.running || (this.status && !this.status.loggedIn)) return null;
    const chapter = this.chapterOfExchange(exchange);
    const unit = this.isArticle || !chapter || chapter === SECTIONS ? "paper" : "chapter";
    const go = h("button", { class: "button ai-verify-go", type: "button", title: t("ai.verify.hint"),
      onclick: () => this.ask({ task: "review", check: exchange }) }, t("ai.verify.button"));
    return h("div", { class: "ai-verify" }, h("div", { class: "ai-verify-row" }, go,
      h("span", { class: "muted small" }, t("ai.verify.by")), ...this.verifierControls()),
    h("p", { class: "muted small ai-cost" }, t("ai.verify.cost", { what: t(`ai.verify.unit.${unit}`) })));
  }

  // Relecture croisée : le premier relecteur (l'assistant choisi, qui écrit la synthèse), puis le second, chacun
  // avec son modèle et son niveau de réflexion (ceux de l'assistant)
  reviewers(picker, partner) {
    const choose = h("select", { class: "ai-model", "aria-label": t("ai.secondAssistant"), title: t("ai.secondAssistant"),
      onchange: (event) => saveSettings({ aiSecond: event.target.value }).then(() => this.onChange()) },
    this.partners.map((p) => h("option", { value: p.provider, selected: p === partner }, authorName(p.provider))));
    const [model, effort] = modelControls(partner, () => this.onChange());
    return h("div", { class: "ai-reviewers" },
      h("span", { class: "ai-reviewer" }, t("ai.reviewer1")), h("div", {}, picker),
      h("span", { class: "ai-reviewer" }, t("ai.reviewer2")), h("div", {}, choose, model, effort));
  }

  askBox(view, exchanges, focus) {
    const pending = this.draft(view);
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
        const { replaces } = pending;
        Object.assign(pending, { question: "", replaces: null });
        afterDiscard(replaces, () => this.ask({ task: "ask", question, parent: doc }));
        return;
      }
      if (!question && !pending.quote && !pending.zone) { area.focus(); return; }
      if (!this.canAsk()) return;
      const { quote, zone, parent, replaces } = pending;
      Object.assign(pending, { quote: null, parent: null, question: "", zone: null, replaces: null });
      afterDiscard(replaces, () => {
        if (zone) this.ask({ task: "zone", zone, question: question || null, parent });
        else if (quote && !question) this.ask({ task: "passage", quote, parent });
        else this.ask({ task: quote ? "passage" : "ask", quote, question, parent });
      });
    };
    // question modifiée : l'ancien échange supprimé d'abord (rien n'est envoyé si la suppression échoue)
    const afterDiscard = (replaces, go) => (replaces ? this.discard(replaces).then((ok) => ok && go()) : go());
    // « Vérifier par le calcul » sur le passage ou la zone choisis (la question écrite, s'il y en a une, précise quoi tester)
    const compute = () => {
      if (!this.canAsk()) return;
      const question = area.value.trim();
      const { quote, zone, parent, replaces } = pending;
      Object.assign(pending, { quote: null, parent: null, question: "", zone: null, replaces: null });
      afterDiscard(replaces, () => this.ask({ task: "compute", quote, zone, question: question || null, parent }));
    };
    // Entrée envoie ; Maj + Entrée va à la ligne (et rien pendant la composition d'un caractère : accents, IME)
    area.addEventListener("keydown", (event) => {
      // keyCode 229 : Entrée qui valide un caractère composé (WebKit ne met pas toujours isComposing)
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); send(); }
    });
    const chips = [];
    const chip = (label, onRemove, removeKey, ...content) => h("div", { class: "ai-chip" }, label,
      h("button", { class: "icon-button small", type: "button", title: t(removeKey), "aria-label": t(removeKey), html: icons.close, onclick: onRemove }), ...content);
    if (pending.replaces) {
      chips.push(chip(h("div", { class: "ai-reply-to" }, h("div", { class: "ai-reply-who" }, `✎ ${t("ai.editing")}`)), () => {
        Object.assign(pending, { replaces: null, question: "", quote: null, zone: null, parent: null });
        this.onChange({ focus: true });
      }, "ai.cancelEdit"));
      chips[chips.length - 1].classList.add("reply");
    }
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
    const checkable = view === "questions" && !running && (pending.quote || pending.zone);
    const box = h("div", { class: "ai-ask" }, ...chips,
      checkable && h("div", { class: "ai-actions ai-pending-actions" },
        h("button", { class: "button small-button ai-action compact", type: "button", title: t("ai.task.computeHint"), onclick: compute }, t("ai.task.compute"))),
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

  // Choix de l'assistant (ceux qui sont prêts ; un seul : affiché quand même, on voit qui répond), de son modèle et
  // du niveau de réflexion, sous la saisie ; communs aux sous-onglets (les réglages n'y touchent pas). `exclude` : assistant du second
  // relecteur (relecture croisée)
  modelPicker(exclude = null) {
    const [model, effort] = modelControls(this.status, () => {
      if (this.status) this.status.model = currentModel();
      this.onChange();
    });
    const current = providerOf(currentModel());
    const options = [...new Set([current, ...(this.partners || []).map((p) => p.provider)])].filter((p) => p !== exclude);
    const assistant = options.length > 0 && h("select", { class: "ai-model", "aria-label": t("ai.assistant"), title: t("ai.assistant"),
      onchange: (event) => this.switchAssistant(event.target.value) },
    options.map((p) => h("option", { value: p, selected: p === current }, authorName(p))));
    return model || assistant ? h("span", { class: "ai-model-picker" }, assistant, model, effort) : null;
  }

  // Autre assistant choisi : son état est déjà connu (assistants prêts), et
  // l'ancien rejoint la liste des autres, sans nouvelle vérification. État posé avant le réglage : l'enregistrer
  // redessine le panneau aussitôt (reader, onSettingsChange)
  switchAssistant(provider) {
    const previous = this.status;
    const next = this.partners?.find((p) => p.provider === provider);
    this.partners = [...(this.partners || []).filter((p) => p !== next), ...(previous?.loggedIn ? [previous] : [])];
    this.partnersFor = provider;
    partnersCache = { provider, at: Date.now(), list: Promise.resolve(this.partners) };
    if (next) {
      this.status = next;
      this.statusFor = modelOf(provider);
    }
    saveSettings({ aiProvider: provider });
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
    const zone = ["zone", "transcribe"].includes(exchange.task) || exchange.task === "compute" && Boolean(exchange.imageUrl || exchange.image);
    if (zone && (exchange.imageUrl || exchange.image)) {  // zone du PDF : son image (le texte de la couche, souvent faux, n'est pas montré)
      bubble.append(h("blockquote", { class: "note-quote ai-quote zone-quote" }, h("img", { src: exchange.imageUrl || `/api/ai/${exchange.id}/image`, alt: "" })));
    } else if (exchange.quote && !zone) bubble.append(h("blockquote", { class: "note-quote ai-quote" }, exchange.quote));
    if (exchange.question) bubble.append(h("p", { class: "ai-asked" }, exchange.question));
    typesetTex([bubble]);
    // « Edit » sous la bulle, au survol (arrête la réponse en cours, remet la question dans le champ)
    const edit = this.editable(exchange) && h("button", { class: "ai-edit", type: "button", title: t("ai.editHint"), onclick: () => this.edit(exchange) },
      `✎ ${t("ai.edit")}`);
    thread.append(edit ? h("div", { class: "ai-q" }, bubble, edit) : bubble, this.renderAnswer(exchange, { reply }));
  }

  // Résumé ou relecture : le document, pleine largeur ; « Refaire » sur le plus récent, en bas à droite (à la fin de la lecture)
  // Relecture, un autre assistant prêt : « Refaire » rouvre la barre de lancement (simple ou croisée, relecteurs) ;
  // sinon refaite telle quelle (relecture croisée : même second assistant), ses étapes repliées au-dessus de la synthèse
  renderDocument(thread, exchange, latest) {
    const second = secondOf(exchange);
    const choose = exchange.task === "review";  // relecture simple, vérifiée ou croisée : on choisit avant de refaire
    const view = this.viewOf(exchange);
    const whole = this.bookPlace(view);  // synthèse du livre : refaite par la tâche (chapitres faits repris)
    const again = () => {
      if (!choose && whole) { this.startBook(exchange.task); return; }
      if (!choose) { this.ask({ task: exchange.task, second: second && providerOf(second), chapter: this.chapterOfExchange(exchange) }); return; }
      this.relaunch = !this.relaunch;
      this.onChange();
    };
    const redo = latest && exchange.status !== "running" && !(whole ? jobRunning(this.jobs[exchange.task]) : this.running)
      ? h("button", { class: "button small-button", type: "button", title: t(`ai.redo.${exchange.task}`), "aria-pressed": choose ? this.relaunch : undefined,
        onclick: again }, h("span", { class: "ai-icon", html: icons.refresh }), t("ai.redo")) : null;
    const answer = this.renderAnswer(exchange, { reply: false, document: true });
    const chapter = this.chapterOfExchange(exchange);  // livre : le chapitre du document, dans son en-tête
    if (chapter && chapter !== SECTIONS) answer.querySelector(".ai-who span:not(.ai-who-dot)")?.append(` · ${this.chapterTitle(chapter)}`);
    if (exchange.target === BOOK) {  // synthèse du livre : d'après les documents des chapitres, un lien vers chacun
      answer.querySelector(".ai-who span:not(.ai-who-dot)")?.append(` · ${this.tb(`ai.book.of.${exchange.task}`)}`);
      const chips = exchange.status === "done" && this.bookChips(view);
      if (chips) answer.querySelector(".markdown")?.after(chips);
    }
    if (exchange.steps?.length) answer.querySelector(".ai-who")?.after(this.stepsFold(exchange));
    if (redo) answer.querySelector(".ai-acts")?.append(redo);
    const verify = latest && this.verifyBox(exchange);
    if (verify) answer.append(verify);
    thread.append(answer);
  }

  // Étapes d'une relecture croisée (relectures, comparaison), chacune dépliable ; rendues à la première ouverture
  stepsFold(exchange) {
    const fold = h("details", { class: "ai-steps", open: exchange.stepsOpen },
      h("summary", {}, tn(exchange.steps.some((step) => step.kind === "verify") ? "ai.stepsVerified" : "ai.steps", exchange.steps.length, { a: modelLabel(exchange.model) })),
      exchange.steps.map((step, i) => {
        const body = h("div", { class: "markdown ai-answer" });
        const item = h("details", { class: "ai-step" }, h("summary", {}, `${i + 1}. ${t(`ai.step.${step.kind}`, { name: modelLabel(step.model) })}`), body);
        item.addEventListener("toggle", () => new LiveAnswer(body, this.refs).update(step.answer || "", true), { once: true });
        return item;
      }));
    fold.addEventListener("toggle", () => { exchange.stepsOpen = fold.open; });
    fold.addEventListener("click", (event) => {
      const ref = event.target.closest("a.ai-ref");
      if (!ref) return;
      event.preventDefault();
      this.navigate(ref.dataset.ref);
    });
    return fold;
  }

  renderAnswer(exchange, { reply = true, document = false } = {}) {
    const answer = h("div", { class: "markdown ai-answer" });
    const live = new LiveAnswer(answer, this.refs, crossNames(exchange));
    live.update(exchange.answer || "", exchange.status !== "running");
    if (exchange.status === "running" && exchange === this.running?.exchange) this.live = live;  // pas un chapitre de la tâche du livre
    answer.addEventListener("click", (event) => {
      const ref = event.target.closest("a.ai-ref");
      if (!ref) return;
      event.preventDefault();
      this.navigate(ref.dataset.ref);
    });
    const who = h("div", { class: "ai-who" }, h("span", { class: "ai-who-dot" }), h("span", {}, `${whoOf(exchange)} · ${timeOf(exchange)}`));
    const parts = [who, answer];
    if (exchange.status === "running") {
      parts.push(runningLine(exchange));
    } else {
      if (exchange.status === "stopped") parts.push(h("span", { class: "badge warn" }, t("ai.stopped")));
      if (exchange.cut) parts.push(h("span", { class: "badge warn" }, t("ai.cut")));
      const covered = exchange.coverage;
      if (covered?.partial && covered.total) {
        const percent = Math.min(99, Math.round(100 * covered.sent / covered.total));
        const pages = (covered.pages || []).join(", ");
        parts.push(h("span", { class: "badge warn" }, t(pages ? "ai.coveragePages" : "ai.coverage", { percent, pages })));
      }
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
      exchange.answer && button(t("ai.copyShort"), () => navigator.clipboard.writeText(this.plainText(exchange))
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
  // Le lien est limité à ~14 000 caractères : le texte complet est aussi copié dans le presse-papiers. Plus long, il
  // n'est pas mis dans le lien : conversation vide, à coller d'un ⌘V (Claude en fait une pièce jointe), plutôt qu'un
  // début de texte à remplacer, qu'on enverrait tronqué par mégarde. Seul le début, si la copie a échoué.
  async continueInClaude(exchange) {
    const pending = api.exportAI(exchange.id).then((result) => result.text);
    const copied = await copyLater(pending);
    let text;
    try { text = await pending; } catch (error) { toastError(error); return; }
    const LIMIT = 13000;
    const long = text.length > LIMIT;
    // sans moitié de caractère à la coupe (formules en lettres mathématiques) : encodeURIComponent la refuserait
    const prefill = long ? (copied ? "" : `${text.slice(0, LIMIT).replace(/[\uD800-\uDBFF]$/, "")}\n\n[…]`) : text;
    location.href = `claude://claude.ai/new${prefill ? `?q=${encodeURIComponent(prefill)}` : ""}`;
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
    // article lu dans son PDF : passages cherchés dans le PDF (en attente : undefined, case grisée), puis nouveau rendu
    // passages à placer dans le PDF : cherchés dans son texte (en attente : undefined, case grisée), puis nouveau rendu
    exchange.found ||= proposals.map((proposal) => (!proposal.quote ? null
      : this.surfaceOf(proposal) === "pdf" ? undefined : Boolean(rangeForQuote(content, proposal.quote, this.scopesOf(exchange)))));
    if (!exchange.locating && exchange.found.includes(undefined)) {
      const before = exchange.found;
      exchange.locating = Promise.all(proposals.map((proposal, i) => (before[i] === undefined
        ? this.pdfQuote(proposal.quote, this.nearOf(proposal)).catch(() => null) : null)))
        .then((spots) => { exchange.found = before.map((found, i) => (found === undefined ? Boolean(spots[i]) : found)); this.onChange(); });
    }
    const rows = proposals.map((proposal, i) => {
      if (proposal.delete != null) {  // suppression : l'annotation visée, si elle est toujours là
        const item = this.deletedItem(proposal);
        return { proposal, found: Boolean(item), kind: "delete", item };
      }
      if (proposal.quote) {
        const view = this.surfaceOf(proposal);
        const kind = proposal.pin ? "pin" : proposal.zone && view === "pdf" ? "zone" : "highlight";
        return { proposal, found: exchange.found[i], kind, view };
      }
      const node = byLabel.get(normalize(proposal.statement));
      const proofId = node?.proofs[0];
      const onProof = Boolean(proposal.on === "proof" && proofId && this.find(proofId)?.matches(".ltx_proof"));
      const id = node && (onProof ? proofId : node.id);
      return { proposal, found: Boolean(id && this.find(id)), kind: "note", id, node, onProof };
    });
    const applied = Boolean(exchange.applied);
    // cases décochées par le lecteur : gardées quand le panneau est redessiné (fin de la recherche dans le PDF)
    exchange.unchecked ||= new Set();
    const boxes = rows.map((row, i) => h("input", { type: "checkbox", checked: row.found && !applied && !exchange.unchecked.has(i), disabled: !row.found || applied }));
    const count = () => boxes.filter((b) => b.checked && !b.disabled).length;
    const apply = h("button", { class: "button small-button primary", type: "button" });
    // passages encore cherchés dans le PDF : pas d'application partielle (l'échange serait marqué appliqué sans eux)
    const locating = () => exchange.found.includes(undefined);
    const label = () => {
      apply.textContent = exchange.applied ? t("ai.proposals.applied") : locating() ? t("ai.proposals.locating") : t("ai.proposals.apply", { n: count() });
      apply.disabled = Boolean(exchange.applied) || locating() || !count();
    };
    boxes.forEach((box, i) => box.addEventListener("change", () => {
      if (box.checked) exchange.unchecked.delete(i); else exchange.unchecked.add(i);
      label();
    }));
    label();
    apply.addEventListener("click", async () => {
      apply.disabled = true;
      const chosen = rows.filter((_, i) => boxes[i].checked && !boxes[i].disabled);
      // suppressions choisies : marquées « supprimée » ensuite (les autres lignes introuvables le restent)
      exchange.deletedRows = new Set(rows.flatMap((row, i) => (row.kind === "delete" && boxes[i].checked && !boxes[i].disabled ? [i] : [])));
      const undo = await this.applyProposals(chosen, this.scopesOf(exchange), providerOf(exchange.model), ABOUT_NOTES.test(exchange.question || ""));
      if (undo.nothing) {  // rien de fait : passages introuvables, ou notes déjà supprimées (erreur du serveur : déjà dite)
        if (!undo.failed) toast(undo.gone && !undo.skipped ? t("ai.proposals.alreadyGone", { n: undo.gone }) : t("ai.proposals.noneApplied"), { error: true });
        label();
        return;
      }
      exchange.applied = 1;
      api.setAIApplied(exchange.id, true).catch(toastError);
      this.onChange();
      let undone = false; // le bouton Annuler ne sert qu'une fois
      const done = [undo.count && t("ai.proposals.done", { n: undo.count }), undo.deleted && t("ai.proposals.deletedDone", { n: undo.deleted }),
        undo.skipped && t("ai.proposals.skipped", { n: undo.skipped }), undo.gone && t("ai.proposals.alreadyGone", { n: undo.gone })].filter(Boolean).join(" ");
      toast(done, { action: { label: t("common.undo"), onClick: async () => {
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
      // article à deux vues : celle où la proposition sera placée ; zone et note épinglée nommées
      const where = row.view && this.views.length > 1 ? t(row.view === "pdf" ? "ai.proposals.inPdf" : "ai.proposals.inHtml") : "";
      const what = row.kind === "delete" ? this.deletionPreview(row)
        : row.kind === "note"
        ? h("div", { class: "ai-proposal-target" }, h("span", { class: "ai-icon", html: icons.note }),
          t(row.onProof ? "ai.proposals.onProof" : "ai.proposals.onStatement", { label: row.node?.label || proposal.statement || "?" }))
        : [row.kind !== "highlight" || where ? h("div", { class: "ai-proposal-target" }, row.kind === "pin" && h("span", { class: "ai-icon", html: icons.note }),
            [row.kind === "zone" ? t("ai.proposals.zone") : row.kind === "pin" ? t("ai.proposals.pin") : "", where].filter(Boolean).join(" · ")) : null,
          h("blockquote", { class: row.kind === "pin" ? "note-quote" : `note-quote hl-border-${COLORS.includes(proposal.color) ? proposal.color : "yellow"}` }, proposal.quote)];
      const note = proposal.note ? h("div", { class: "markdown ai-proposal-note" }) : null;
      if (note) note.innerHTML = markdownToHtml(proposal.note, { refs: this.refs });
      return h("label", { class: `ai-proposal${row.found ? "" : " missing"}` }, boxes[i],
        h("span", { class: "ai-proposal-body" }, what, note, row.found === false && h("span", { class: "badge warn" },
          t(row.kind !== "delete" ? "ai.proposals.notFound" : applied && exchange.deletedRows?.has(i) ? "ai.proposals.deleted" : "ai.proposals.deleteGone"))));
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

  // aperçu d'une suppression proposée : « Supprimer · <nature> · <vue, page> », puis le passage ou la note visés
  deletionPreview(row) {
    const item = row.item;
    const pdf = item?.target?.pdf;
    const nature = !item ? "" : item.kind === "bookmark" ? t("ai.proposals.delBookmark", { name: item.quote || "" })
      : item.kind === "block" ? t("ai.proposals.delBlock", { label: this.blockLabel(item.target?.id) })
      : item.kind === "note" ? t("notes.freeNote") : pdf?.rect ? t("ai.proposals.zone") : t("ai.proposals.delHighlight");
    const where = !item ? "" : pdf ? t("notes.pdfPage", { page: pdf.page }) : this.views.length > 1 ? t("ai.proposals.inHtml") : "";
    const quote = item && item.kind === "highlight" && !pdf?.rect && item.quote;
    const thumb = item && pdf?.rect && item.image && h("img", { class: "ai-proposal-zone", src: `/api/annotations/${item.id}/image`, alt: t("notes.zone") });
    const text = item && item.kind !== "bookmark" && !quote && item.note ? h("div", { class: "markdown ai-proposal-note" }) : null;
    if (text) text.innerHTML = markdownToHtml(item.note.length > 400 ? `${item.note.slice(0, 400)}…` : item.note);
    return [h("div", { class: "ai-proposal-target ai-proposal-delete" }, h("span", { class: "ai-icon", html: icons.trash }),
      [t("ai.proposals.delete"), nature, where].filter(Boolean).join(" · ")),
      quote && h("blockquote", { class: `note-quote hl-border-${COLORS.includes(item.color) ? item.color : "yellow"}` }, quote),
      thumb, text];
  }

  // énoncé, ou preuve d'un énoncé (« Preuve — Lemme 2.1 », comme dans l'onglet Notes), d'une note d'énoncé
  blockLabel(id) {
    const node = this.byId.get(id);
    if (node) return node.label;
    const element = id ? this.find(id) : null;
    const proofOf = element?.matches?.(".ltx_proof") && this.byId.get(element.dataset.proofOf);
    return proofOf ? `${t("reader.proof")} — ${proofOf.label}` : id || "?";
  }

  // place dans le PDF du passage qu'une proposition remplace : le nouveau est cherché d'abord près de lui
  replacedPlace(proposal) {
    return this.replacedItem(proposal)?.target?.pdf || null;
  }

  // où chercher d'abord un passage dans le PDF : place de celui qu'il remplace, sinon la page que l'assistant indique
  nearOf(proposal) {
    const page = Number(proposal.page);
    return this.replacedPlace(proposal) || (Number.isInteger(page) && page > 0 ? { page, start: 0 } : null);
  }

  // vue où placer une proposition : celle qu'elle nomme (« surface », adaptation des notes) si l'article l'a ; sinon le
  // HTML, ou le PDF pour un article lu dans son seul PDF ; une zone va dans le PDF s'il y en a un
  surfaceOf(proposal) {
    if (this.views.length < 2) return this.views[0] || "html";
    const named = String(proposal.surface || "").toLowerCase();
    return named === "pdf" || (proposal.zone && named !== "html") ? "pdf" : "html";
  }

  // annotation que remplace une proposition ; remise par « Annuler », elle a un nouveau numéro (même uid) : suivi
  replacedItem(proposal) {
    return proposal.replaces == null ? null : this.itemOf(proposal.replaces);
  }

  // annotation de l'article lu, par son numéro ; une annotation remise (« Annuler ») est suivie sous son nouveau numéro
  itemOf(number) {
    let id = String(number);
    const restored = this.annotator.restoredIds;
    for (let hops = 0; restored?.has(id) && hops < 20; hops++) id = restored.get(id);
    return this.annotator.items.find((a) => String(a.id) === id) || null;
  }

  // annotation qu'une proposition « delete » demande de supprimer (DELETE_PROMPT dans ai.py) ; la fiche de lecture
  // n'en est pas une
  deletedItem(proposal) {
    const item = this.itemOf(proposal.delete);
    return item && DELETABLE.includes(item.kind) ? item : null;
  }

  // Crée les annotations choisies ; renvoie de quoi tout annuler (ou null si rien n'a pu être fait).
  // `replaces` (fichier TeX modifié, TEX_ADAPT_PROMPT dans ai.py) : l'annotation adaptée prend la place de l'ancienne,
  // supprimée (remise par « Annuler ») ; une note d'énoncé remplacée sur le même énoncé est réécrite, pas complétée
  async applyProposals(rows, scopes = [], author = "claude", aboutNotes = false) {
    const created = [];
    const previousNotes = new Map();
    const removed = [];  // anciennes annotations remplacées
    const statesCopied = [];  // énoncés qui ont reçu l'état de lecture d'une note remplacée
    const written = new Set();  // énoncés (ou preuves) dont la note vient d'être écrite : leur ancienne note reste
    const extended = [];  // notes libres du HTML complétées par une note épinglée : [id, texte d'avant]
    let skipped = 0;  // propositions choisies qui n'ont pas pu être placées (texte changé, page sans texte)
    let deleted = 0;  // annotations supprimées à la demande du lecteur (remises par « Annuler », avec `removed`)
    let gone = 0;  // suppressions choisies dont l'annotation n'est déjà plus là
    let failed = 0;  // suppressions refusées par le serveur (déjà signalées par toastError)
    // suppressions d'abord : une note d'énoncé supprimée puis réécrite par une autre proposition garde la nouvelle
    rows = [...rows.filter((row) => row.kind === "delete"), ...rows.filter((row) => row.kind !== "delete")];
    for (const row of rows) {
      const { proposal } = row;
      if (row.kind === "delete") {
        const item = this.deletedItem(proposal);
        if (!item || removed.some((old) => old.id === item.id)) { gone++; continue; }  // déjà supprimée (ou listée deux fois)
        try {
          const kept = await this.annotator.remove(item.id);
          if (kept) { removed.push(kept); deleted++; } else gone++;
        } catch (error) { failed++; toastError(error); }
        continue;
      }
      const own = aboutNotes && proposal.author === "reader"; // adaptation : note du lecteur reprise telle quelle (ADAPT_PROMPT dans ai.py)
      // remplacée seulement par une annotation de même nature (passage par passage, note d'énoncé par note
      // d'énoncé) : sinon son état de lecture se perdrait ; jamais une zone du PDF (son image serait perdue)
      const candidate = this.replacedItem(proposal);
      const sameKind = { highlight: "highlight", note: "block" }[row.kind];
      const old = candidate && !removed.includes(candidate) && candidate.kind === sameKind && !candidate.target?.pdf?.rect ? candidate : null;
      try {
        let placed = false;
        if (row.kind !== "note") {  // passage, zone ou note épinglée
          const color = COLORS.includes(proposal.color) ? proposal.color : "yellow";
          const values = { color, note: proposal.note || null, author: own ? null : author };
          // note épinglée : une note libre, qui ne garde pas l'auteur (comme une note d'énoncé, son en-tête le dit) ;
          // note d'un assistant recopiée d'une vue à l'autre : déjà son en-tête
          const pinned = own || ASSISTANT_HEADER.test(proposal.note || "") ? proposal.note
            : `**${authorName(author)}** — *${t("ai.unverified")}*\n\n${proposal.note}`;
          if (row.view === "pdf") {  // dans le PDF, à l'endroit où ses mots se trouvent
            const spot = await this.pdfQuote(proposal.quote, old?.target?.pdf || this.nearOf(proposal));
            if (!spot || !this.annotator.pdf) { skipped++; continue; }
            const made = row.kind === "zone" ? await this.annotator.pdf.addZone({ target: spot.target, quote: spot.text, ...values })
              : row.kind === "pin" ? await this.annotator.pdf.addPin({ target: spot.target, note: pinned })
              : await this.annotator.pdf.addHighlight({ target: spot.target, quote: spot.text, ...values });
            if (!made) { skipped++; continue; }
            created.push(made.id);
          } else {
            const range = rangeForQuote(this.annotator.content, proposal.quote, scopes); // retrouvé à nouveau : le texte a pu changer
            if (!range) { skipped++; continue; }
            if (row.kind === "pin") {
              const pin = await this.annotator.addPin(range, pinned);
              if (!pin) { skipped++; continue; }
              if (pin.block) {  // dans un énoncé ou une preuve : sa note (comme une note posée à la main)
                if (!previousNotes.has(pin.block)) previousNotes.set(pin.block, this.annotator.block(pin.block)?.note || "");
                await this.annotator.appendNote(pin.block, pinned);
                written.add(pin.block);
              } else if (pin.previous === null) created.push(pin.annotation.id);
              else if (!extended.some(([id]) => id === pin.annotation.id)) extended.push([pin.annotation.id, pin.previous]);
            } else created.push((await this.annotator.highlightRange(range, values)).id);
          }
          placed = true;
        } else if (proposal.note) {
          if (!previousNotes.has(row.id)) previousNotes.set(row.id, this.annotator.block(row.id)?.note || "");
          const text = own ? proposal.note : `**${authorName(author)}** — *${t("ai.unverified")}*\n\n${proposal.note}`;
          if (old?.kind === "block" && old.target?.id === row.id) await this.annotator.saveBlock(row.id, { note: text });
          else await this.annotator.appendNote(row.id, text);
          written.add(row.id);
          // état de lecture de l'ancienne note (énoncé qui a perdu son \label) : repris s'il n'y en a pas ; note passée
          // sur une preuve (qui ne montre pas d'état) : état mis sur son énoncé
          const stateId = row.onProof ? row.node?.id : row.id;
          if (old?.kind === "block" && old.state && stateId && old.target?.id !== stateId && !this.annotator.block(stateId)?.state) {
            await this.annotator.saveBlock(stateId, { state: old.state });
            statesCopied.push([stateId, old.state]);
          }
          placed = true;
        }
        if (placed && old && !(old.kind === "block" && written.has(old.target?.id))) {
          if (old.kind === "highlight") await this.annotator.removeHighlight(old.id);
          else await this.annotator.deleteBlock(old.id);
          removed.push(old);
        }
      } catch (error) {
        toastError(error);
      }
    }
    const count = created.length + previousNotes.size + extended.length;
    if (!count && !deleted) return { nothing: true, gone, failed, skipped };
    return {
      count,
      deleted,
      gone,
      skipped,
      // renvoie le nombre d'échecs (0 : tout est annulé)
      run: async () => {
        let failures = 0;
        const attempt = (promise) => promise.catch((error) => { failures++; toastError(error); });
        for (const id of created) await attempt(this.annotator.removeHighlight(id));
        for (const [id, note] of extended.reverse()) await attempt(this.annotator.setFreeNote(id, note));
        for (const [id, state] of statesCopied) await attempt(this.annotator.setState(id, state));  // même état : retiré
        for (const [id, note] of previousNotes) {
          const block = this.annotator.block(id);
          // note créée par l'application seule (ni note ni état avant) : supprimée plutôt que vidée
          if (!note && block && !block.state) await attempt(this.annotator.deleteBlock(block.id));
          else await attempt(this.annotator.saveBlock(id, { note }));
        }
        // anciennes notes remises en dernier : une note d'énoncé remplacée retrouve son énoncé même si une autre
        // proposition y avait écrit
        for (const annotation of removed) await attempt(this.annotator.restore(annotation));  // numéro suivi par restore
        return failures;
      },
    };
  }

  destroy() {
    this.destroyed = true;
    clearTimeout(this.pollTimer);
    this.stop();
  }
}

// Réponse de Claude : texte, et propositions d'annotations (bloc ```annotations``` en JSON, retiré du texte).
// Pendant l'écriture, le bloc commencé est simplement masqué.
function parseProposals(answer) {
  const match = answer.match(/```annotations[^\n]*\n([\s\S]*?)(```|$)/);
  if (!match) return { text: answer, items: [] };
  const text = answer.slice(0, match.index).trimEnd() + answer.slice(match.index + match[0].length);
  if (match[2] !== "```") return { text, items: [] };
  try {
    const items = JSON.parse(match[1]);
    // note épinglée : seulement avec sa note ; suppression : un numéro d'annotation
    return { text, items: Array.isArray(items) ? items.filter((item) => item && ((item.quote && (!item.pin || item.note)) || (item.statement && item.note)
      || (item.delete != null && /^\d+$/.test(String(item.delete))))) : [] };
  } catch {
    return { text, items: [] };
  }
}

const currentModel = () => modelFor(settings);
const DELETABLE = ["highlight", "note", "block", "bookmark"];  // ce que l'IA peut proposer de supprimer

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
// Image enregistrée (zone du PDF d'un échange) en data URL : la question modifiée repart avec elle
async function dataUrl(url) {
  const blob = await (await fetch(url)).blob();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

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
// `names` : relecture croisée, marqueurs de la synthèse montrés en étiquettes (remplacés avant le Markdown, où
// « [a] » pourrait passer pour une citation, et posés après)
class LiveAnswer {
  constructor(element, refs, names = null) {
    this.element = element;
    this.refs = refs;
    this.names = names;
    this.done = 0;
    this.tail = h("div", { class: "ai-tail" });
    element.append(this.tail);
  }

  update(text, final) {
    const blocks = splitBlocks(parseProposals(text).text);
    const complete = final ? blocks.length : Math.max(0, blocks.length - 1);
    const added = [];
    for (; this.done < complete; this.done++) {
      // paragraphe en retrait après une ligne vide : suite de l'élément de liste au-dessus (un point d'une relecture),
      // aligné sur son texte (chaque bloc est rendu à part : il ne peut entrer dans la liste du bloc précédent)
      const block = h("div", { class: CONTINUED.test(blocks[this.done]) ? "ai-block ai-continued" : "ai-block" });
      block.innerHTML = this.html(blocks[this.done]);
      this.tail.before(block);
      added.push(block);
    }
    this.tail.innerHTML = final ? "" : this.html(blocks[complete] || "");
    if (added.length) { for (const block of added) addCopyButtons(block); typesetTex(added); }
  }

  html(text) {
    if (!this.names) return markdownToHtml(text, { refs: this.refs });
    const marked = markdownToHtml(withTags(text, this.names, (kind) => `\u0001${kind}\u0001`), { refs: this.refs });
    return marked.replace(/\u0001(\w+)\u0001/g, (_, kind) => `<span class="ai-tag ${kind}">${escapeHtml(this.names[kind] || t(`ai.tag.${kind}`))}</span>`);
  }
}

// Bouton « Copier » au-dessus de chaque bloc de code d'une réponse terminée (script à coller dans Sage) ; le code n'est
// jamais exécuté par l'application. Le presse-papiers s'écrit pendant le clic (Safari).
function addCopyButtons(root) {
  for (const pre of root.querySelectorAll("pre")) {
    if (pre.previousElementSibling?.classList.contains("ai-code-bar")) continue;
    const copy = () => navigator.clipboard.writeText(pre.querySelector("code")?.textContent ?? pre.textContent)
      .then(() => toast(t("ai.codeCopied")), () => toast(t("ai.copyFailed")));
    pre.before(h("div", { class: "ai-code-bar" }, h("button", { class: "ai-act", type: "button", title: t("ai.copyCodeHint"), onclick: copy }, t("ai.copyCode"))));
  }
}

// Coupe le Markdown aux lignes vides, sans couper un bloc de code ni une formule $$…$$
const CONTINUED = /^[ \t]{2,}\S/;

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
