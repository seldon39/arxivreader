// Appels au serveur local.

import { t } from "./i18n.js";

// Version de l'article affiché par le lecteur de cette page, jointe à ses écritures : un autre onglet a pu le mettre à
// jour depuis, et le serveur les refuse alors (main._same_version)
const reading = new Map();
export const setReadingVersion = (id, version) => reading.set(id, version);
const versioned = (id) => (reading.get(id) ? { "X-ArXivReader-Version": String(reading.get(id)) } : {});

function failure(status, data) {
  const stale = data?.detail === "stale_version";
  const error = new Error(stale ? t("reader.stale") : (data && (data.error || data.detail)) || `HTTP ${status}`);
  error.status = status;
  if (stale) error.code = "stale_version";
  else if (data && typeof data.code === "string") error.code = data.code; // cause précise (traduite par la page)
  return error;
}

async function request(method, url, body, headers = {}) {
  const options = { method, headers: { ...headers } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const type = response.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) throw failure(response.status, data);
  return data;
}

// Compilation d'un fichier TeX (une minute ou plus pour un livre) : suivie jusqu'à la fin
async function texJob(started) {
  const { job } = await started;
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    const state = await request("GET", `/api/tex/job/${encodeURIComponent(job)}`);
    if (state.running) continue;
    if (state.error) throw new Error(state.error);
    return state.result;
  }
}

// PDF de l'ordinateur : le fichier tel quel (le seul envoi qui ne soit pas du JSON), son nom dans un en-tête
// `check` : un PDF d'arXiv n'est pas importé, la réponse donne son identifiant ({paper: null, arxiv: {arxivId}})
async function uploadPdf(file, { check = false } = {}) {
  const response = await fetch(`/api/papers/pdf${check ? "?check=1" : ""}`, { method: "POST", body: file,
    headers: { "Content-Type": "application/pdf", "X-Filename": encodeURIComponent(file.name || "document.pdf") } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

export const api = {
  papers: () => request("GET", "/api/papers"),
  uploadPdf,
  // PDF choisi dans la fenêtre de l'application : lu par le serveur à son chemin
  importPdfAt: (path, { check = false } = {}) => request("POST", `/api/papers/pdf?path=${encodeURIComponent(path)}${check ? "&check=1" : ""}`, {}),
  // fichier TeX : compilé en arrière-plan ; ces appels attendent la fin et renvoient {paper, report}
  importTex: (path, pdf = null) => texJob(request("POST", "/api/papers/tex", { path, pdf })),
  recompile: (id) => texJob(request("POST", `/api/papers/${encodeURIComponent(id)}/recompile`, {})),
  chooseBib: (id, name, path) => texJob(request("POST", `/api/papers/${encodeURIComponent(id)}/bib`, { name, path })),
  texReport: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/texfile`),
  texTools: () => request("GET", "/api/tex/tools"),
  // version HTML expérimentale (LaTeXML) : état, conversion (attendue jusqu'à la fin), conversion déjà lancée
  htmlStatus: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/html`),
  makeHtml: (id) => texJob(request("POST", `/api/papers/${encodeURIComponent(id)}/html`, {})),
  htmlJob: (id) => texJob(Promise.resolve({ job: `${id}.html` })),
  pdfArxivMatch: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/arxiv-match`),
  updateMeta: (id, title, authors) => request("PUT", `/api/papers/${encodeURIComponent(id)}/meta`, { title, authors }),
  zoteroStatus: () => request("GET", "/api/zotero"),
  zoteroSync: () => request("POST", "/api/zotero/sync", {}),
  papersRelevance: () => request("GET", "/api/papers/relevance"),
  librarySearch: (query) => request("GET", `/api/library/search?q=${encodeURIComponent(query)}`),
  folders: () => request("GET", "/api/folders"),
  createFolder: (name, papers = []) => request("POST", "/api/folders", { name, papers }),
  renameFolder: (id, name) => request("PUT", `/api/folders/${id}`, { name }),
  deleteFolder: (id) => request("DELETE", `/api/folders/${id}`),
  setInFolder: (id, paper, member) => request(member ? "PUT" : "DELETE", `/api/folders/${id}/papers/${encodeURIComponent(paper)}`, member ? {} : undefined),
  // `known` : ce que la page sait de l'article (titre, auteurs…) : inscrit tout de suite, importé en arrière-plan
  importPaper: (query, library = true, known = {}) => request("POST", "/api/papers", { query, library, ...known }),
  referenceArxiv: (entry) => request("POST", "/api/references/arxiv", { text: entry.text || "", title: entry.title || "", doi: entry.doi || "" }),
  // ajout immédiat à la bibliothèque (import en arrière-plan), d'après ce que la page sait déjà de l'article
  quickAdd: (query, known = {}) => request("POST", "/api/papers/quick", { query, ...known }),
  setInLibrary: (id, value) => request("PUT", `/api/papers/${encodeURIComponent(id)}/library`, { in_library: value }),
  paper: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}`),
  content: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/content`),
  pages: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/pages`),
  crosslinks: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/crosslinks`),
  statement: (id, node) => request("GET", `/api/papers/${encodeURIComponent(id)}/statements/${encodeURIComponent(node)}`),
  savePosition: (id, anchor) => request("PUT", `/api/papers/${encodeURIComponent(id)}/position`, { anchor }, versioned(id)),
  refresh: (id) => request("POST", `/api/papers/${encodeURIComponent(id)}/refresh`, {}),
  importing: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/importing`),
  versions: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/versions`),
  openVersion: (id, version) => request("POST", `/api/papers/${encodeURIComponent(id)}/versions/${version}`, {}),
  search: (query) => request("POST", "/api/search", query),
  // `kept` : seulement un graphe déjà gardé (aucune construction lancée)
  related: (query, refresh = false, kept = false) => request("POST", "/api/related", { query, refresh, kept }),
  relatedStatus: (id) => request("GET", `/api/related/status?id=${encodeURIComponent(id)}`),
  relatedCancel: (id) => request("POST", "/api/related/cancel", { id }),
  feed: (view, day) => request("GET", `/api/feed?view=${encodeURIComponent(view)}${day ? `&day=${day}` : ""}`),
  feedNewVersions: (force) => request("GET", `/api/feed/new-versions${force ? "?force=true" : ""}`),
  refreshFeed: (view) => request("POST", "/api/feed/refresh", { view }),
  linkArxiv: (key, query) => request("POST", "/api/feed/arxiv", { key, query }),
  alerts: () => request("GET", "/api/alerts"),
  alertsSeen: (keys) => request("POST", "/api/alerts/seen", { keys }),
  feedStatus: (key, status) => request("PUT", "/api/feed/status", { key, status }),
  lookup: (text) => request("POST", "/api/lookup", { text }),
  profile: () => request("GET", "/api/profile"),
  addProfileItem: (item) => request("POST", "/api/profile/items", item),
  removeProfileItem: (id) => request("DELETE", `/api/profile/items/${id}`),
  resolveProfile: (kind, query) => request("POST", "/api/profile/resolve", { kind, query }),
  prefillProfile: (owner) => request("POST", "/api/profile/prefill", { owner }),
  onboardingFind: (name, identifier) => request("POST", "/api/onboarding/find", { name, identifier }),
  onboardingSuggest: (owner, papers) => request("POST", "/api/onboarding/suggest", { owner, papers }),
  onboardingApply: (body) => request("POST", "/api/onboarding/apply", body),
  notesCount: () => request("GET", "/api/notes"),
  importNotes: (files, accept = []) => request("POST", "/api/notes/import", { files, accept }),
  authorRule: (id, body) => request("PUT", `/api/profile/items/${id}/rule`, body),
  authorCategories: (id, paper) => request("GET", `/api/profile/items/${id}/categories${paper ? `?paper=${encodeURIComponent(paper)}` : ""}`),
  notesDevice: () => request("GET", "/api/notes/device"),
  setNotesFolder: (path) => request("PUT", "/api/notes/folder", { path }),
  exportNotesToFolder: (lang) => request("POST", `/api/notes/export-folder?lang=${encodeURIComponent(lang)}`, {}),
  figures: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/figures`),
  annotations: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/annotations`),
  addAnnotation: (id, body) => request("POST", `/api/papers/${encodeURIComponent(id)}/annotations`, body, versioned(id)),
  updateAnnotation: (annotationId, body) => request("PUT", `/api/annotations/${annotationId}`, body),
  deleteAnnotation: (annotationId) => request("DELETE", `/api/annotations/${annotationId}`),
  setProof: (id, nodeId, body) => request("PUT", `/api/papers/${encodeURIComponent(id)}/proofs/${encodeURIComponent(nodeId)}`, body, versioned(id)),
  clearProof: (id, nodeId) => request("DELETE", `/api/papers/${encodeURIComponent(id)}/proofs/${encodeURIComponent(nodeId)}`, undefined, versioned(id)),
  aiStatus: (provider) => request("GET", `/api/ai/status${provider ? `?provider=${encodeURIComponent(provider)}` : ""}`),
  aiPartners: () => request("GET", "/api/ai/partners"),
  aiProfile: () => request("GET", "/api/ai/profile"),
  updates: () => request("GET", "/api/updates"),
  checkUpdates: () => request("POST", "/api/updates/check", {}),
  dismissUpdate: (version) => request("POST", "/api/updates/dismiss", { version }),
  aiSetup: (provider, step) => request("POST", "/api/ai/setup", { provider, step }),
  aiLogout: (provider) => request("POST", "/api/ai/logout", { provider }),
  aiExchanges: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/ai`),
  // livre : relecture ou résumé de tous les chapitres en parallèle, puis synthèse (tâche de fond du serveur)
  aiBook: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/ai/book`),
  startAIBook: (id, body) => request("POST", `/api/papers/${encodeURIComponent(id)}/ai/book`, body, versioned(id)),
  stopAIBook: (id, task) => request("POST", `/api/papers/${encodeURIComponent(id)}/ai/book/stop`, { task }),
  exportAI: (exchangeId) => request("GET", `/api/ai/${exchangeId}/export`),
  paperText: (paperId) => request("GET", `/api/papers/${encodeURIComponent(paperId)}/text`),
  setAIApplied: (exchangeId, applied) => request("PUT", `/api/ai/${exchangeId}/applied`, { applied }),
  deleteAIExchange: (exchangeId) => request("DELETE", `/api/ai/${exchangeId}`),
  settings: () => request("GET", "/api/settings"),
  saveSettings: (values) => request("PUT", "/api/settings", values),
};

// Réponse de Claude en direct : une ligne JSON par événement ({exchange}, {delta}, {error}, {done}).
export async function streamAI(paperId, body, onEvent, signal) {
  const response = await fetch(`/api/papers/${encodeURIComponent(paperId)}/ai`, {
    method: "POST", headers: { "Content-Type": "application/json", ...versioned(paperId) }, body: JSON.stringify(body), signal,
  });
  if (!response.ok) throw failure(response.status, await response.json().catch(() => ({})));
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line));
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}
