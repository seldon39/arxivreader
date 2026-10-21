// Appels au serveur local.

async function request(method, url, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const type = response.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = (data && (data.error || data.detail)) || `HTTP ${response.status}`;
    const error = new Error(message);
    if (data && typeof data.code === "string") error.code = data.code; // cause précise (traduite par la page)
    throw error;
  }
  return data;
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
  pdfArxivMatch: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/arxiv-match`),
  updateMeta: (id, title, authors) => request("PUT", `/api/papers/${encodeURIComponent(id)}/meta`, { title, authors }),
  zoteroStatus: () => request("GET", "/api/zotero"),
  zoteroSync: () => request("POST", "/api/zotero/sync", {}),
  papersRelevance: () => request("GET", "/api/papers/relevance"),
  folders: () => request("GET", "/api/folders"),
  createFolder: (name, papers = []) => request("POST", "/api/folders", { name, papers }),
  renameFolder: (id, name) => request("PUT", `/api/folders/${id}`, { name }),
  deleteFolder: (id) => request("DELETE", `/api/folders/${id}`),
  setInFolder: (id, paper, member) => request(member ? "PUT" : "DELETE", `/api/folders/${id}/papers/${encodeURIComponent(paper)}`, member ? {} : undefined),
  // `known` : ce que la page sait de l'article (titre, auteurs…) : inscrit tout de suite, importé en arrière-plan
  importPaper: (query, library = true, known = {}) => request("POST", "/api/papers", { query, library, ...known }),
  // ajout immédiat à la bibliothèque (import en arrière-plan), d'après ce que la page sait déjà de l'article
  quickAdd: (query, known = {}) => request("POST", "/api/papers/quick", { query, ...known }),
  setInLibrary: (id, value) => request("PUT", `/api/papers/${encodeURIComponent(id)}/library`, { in_library: value }),
  paper: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}`),
  content: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/content`),
  pages: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/pages`),
  crosslinks: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/crosslinks`),
  statement: (id, node) => request("GET", `/api/papers/${encodeURIComponent(id)}/statements/${encodeURIComponent(node)}`),
  savePosition: (id, anchor) => request("PUT", `/api/papers/${encodeURIComponent(id)}/position`, { anchor }),
  refresh: (id) => request("POST", `/api/papers/${encodeURIComponent(id)}/refresh`, {}),
  importing: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/importing`),
  search: (query) => request("POST", "/api/search", query),
  // `kept` : seulement un graphe déjà gardé (aucune construction lancée)
  related: (query, refresh = false, kept = false) => request("POST", "/api/related", { query, refresh, kept }),
  relatedStatus: (id) => request("GET", `/api/related/status?id=${encodeURIComponent(id)}`),
  relatedCancel: (id) => request("POST", "/api/related/cancel", { id }),
  feed: (view, day) => request("GET", `/api/feed?view=${encodeURIComponent(view)}${day ? `&day=${day}` : ""}`),
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
  notesDevice: () => request("GET", "/api/notes/device"),
  figures: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/figures`),
  annotations: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/annotations`),
  addAnnotation: (id, body) => request("POST", `/api/papers/${encodeURIComponent(id)}/annotations`, body),
  updateAnnotation: (annotationId, body) => request("PUT", `/api/annotations/${annotationId}`, body),
  deleteAnnotation: (annotationId) => request("DELETE", `/api/annotations/${annotationId}`),
  setProof: (id, nodeId, body) => request("PUT", `/api/papers/${encodeURIComponent(id)}/proofs/${encodeURIComponent(nodeId)}`, body),
  clearProof: (id, nodeId) => request("DELETE", `/api/papers/${encodeURIComponent(id)}/proofs/${encodeURIComponent(nodeId)}`),
  aiStatus: () => request("GET", "/api/ai/status"),
  updates: () => request("GET", "/api/updates"),
  checkUpdates: () => request("POST", "/api/updates/check", {}),
  dismissUpdate: (version) => request("POST", "/api/updates/dismiss", { version }),
  aiSetup: (provider, step) => request("POST", "/api/ai/setup", { provider, step }),
  aiExchanges: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/ai`),
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
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `HTTP ${response.status}`);
  }
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
