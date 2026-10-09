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
  importPaper: (query) => request("POST", "/api/papers", { query }),
  paper: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}`),
  content: (id) => request("GET", `/api/papers/${encodeURIComponent(id)}/content`),
  savePosition: (id, anchor) => request("PUT", `/api/papers/${encodeURIComponent(id)}/position`, { anchor }),
  refresh: (id) => request("POST", `/api/papers/${encodeURIComponent(id)}/refresh`, {}),
  remove: (id) => request("DELETE", `/api/papers/${encodeURIComponent(id)}`),
  settings: () => request("GET", "/api/settings"),
  saveSettings: (values) => request("PUT", "/api/settings", values),
};
