// Thin API client for the AEGIS dashboard backend (aegis/dashboard.py).
// Token is read from ?token= once and persisted; every request carries it.
const url = new URL(window.location.href);
const fromQuery = url.searchParams.get("token");
if (fromQuery) localStorage.setItem("aegis_token", fromQuery);
export const TOKEN = localStorage.getItem("aegis_token") || "";

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (TOKEN) h["X-Aegis-Token"] = TOKEN;
  return h;
}

export async function api<T = any>(path: string): Promise<T> {
  const r = await fetch(`/api/${path}`, { headers: headers() });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export async function post<T = any>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`/api/${path}`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export function sse(path: string, onMessage: (data: any) => void): () => void {
  const q = TOKEN ? `${path}${path.includes("?") ? "&" : "?"}token=${TOKEN}` : path;
  const es = new EventSource(`/api/${q}`);
  es.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch { /* ignore */ } };
  return () => es.close();
}
