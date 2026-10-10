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
    throw new Error(message);
  }
  return data;
}

export const api = {
  papers: () => request("GET", "/api/papers"),
  importPaper: (query, library = true) => request("POST", "/api/papers", { query, library }),
  setInLibrary: (id, value) => request("PUT", `/api/papers/${encodeURIComponent(id)}/library`, { in_library: value }),
  paper: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}`),
  content: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/content`),
  savePosition: (id, anchor) => request("PUT", `/api/papers/${encodeURIComponent(id)}/position`, { anchor }),
  refresh: (id) => request("POST", `/api/papers/${encodeURIComponent(id)}/refresh`, {}),
  remove: (id) => request("DELETE", `/api/papers/${encodeURIComponent(id)}`),
  feed: (view, day) => request("GET", `/api/feed?view=${encodeURIComponent(view)}${day ? `&day=${day}` : ""}`),
  refreshFeed: (view) => request("POST", "/api/feed/refresh", { view }),
  feedStatus: (key, status) => request("PUT", "/api/feed/status", { key, status }),
  lookup: (text) => request("POST", "/api/lookup", { text }),
  profile: () => request("GET", "/api/profile"),
  addProfileItem: (item) => request("POST", "/api/profile/items", item),
  removeProfileItem: (id) => request("DELETE", `/api/profile/items/${id}`),
  resolveProfile: (kind, query) => request("POST", "/api/profile/resolve", { kind, query }),
  prefillProfile: (owner) => request("POST", "/api/profile/prefill", { owner }),
  settings: () => request("GET", "/api/settings"),
  saveSettings: (values) => request("PUT", "/api/settings", values),
};
