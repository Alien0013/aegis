// Typed client for the AEGIS dashboard backend (aegis/dashboard_fastapi.py).
// The session token is injected into index.html as window.__AEGIS_SESSION_TOKEN__,
// or passed as ?token=… on first load; we persist it and send it on every call.

const url = new URL(window.location.href);
const fromQuery = url.searchParams.get("token");
if (fromQuery) localStorage.setItem("aegis_token", fromQuery);
const fromBootstrap = (window as unknown as { __AEGIS_SESSION_TOKEN__?: string })
  .__AEGIS_SESSION_TOKEN__ || "";
if (fromBootstrap) localStorage.setItem("aegis_token", fromBootstrap);

export const TOKEN = localStorage.getItem("aegis_token") || "";

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (TOKEN) h["X-Aegis-Token"] = TOKEN;
  return h;
}

export class ApiError extends Error {
  constructor(public path: string, public status: number, message?: string) {
    super(message || `${path}: ${status}`);
    this.name = "ApiError";
  }
}

async function parse<T>(r: Response, path: string): Promise<T> {
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.text()).slice(0, 300); } catch { /* ignore */ }
    throw new ApiError(path, r.status, detail || `${path}: ${r.status}`);
  }
  const ctype = r.headers.get("content-type") || "";
  return (ctype.includes("application/json") ? await r.json() : await r.text()) as T;
}

export function api<T = unknown>(path: string): Promise<T> {
  return fetch(`/api/${path}`, { headers: headers() }).then((r) => parse<T>(r, path));
}

export function post<T = unknown>(path: string, body?: unknown): Promise<T> {
  return fetch(`/api/${path}`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body ?? {}),
  }).then((r) => parse<T>(r, path));
}

export function put<T = unknown>(path: string, body?: unknown): Promise<T> {
  return fetch(`/api/${path}`, {
    method: "PUT",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body ?? {}),
  }).then((r) => parse<T>(r, path));
}

export function patch<T = unknown>(path: string, body?: unknown): Promise<T> {
  return fetch(`/api/${path}`, {
    method: "PATCH",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body ?? {}),
  }).then((r) => parse<T>(r, path));
}

export function del<T = unknown>(path: string): Promise<T> {
  return fetch(`/api/${path}`, { method: "DELETE", headers: headers() })
    .then((r) => parse<T>(r, path));
}

/** Subscribe to a server-sent-events endpoint. Returns an unsubscribe fn. */
export function sse(path: string, onMessage: (data: unknown) => void): () => void {
  const sep = path.includes("?") ? "&" : "?";
  const q = TOKEN ? `${path}${sep}token=${encodeURIComponent(TOKEN)}` : path;
  const es = new EventSource(`/api/${q}`);
  es.onmessage = (e) => {
    try { onMessage(JSON.parse(e.data)); } catch { /* ignore non-JSON frames */ }
  };
  return () => es.close();
}

/** POST an endpoint that streams `data: {json}\n\n` frames (e.g. chat/stream). */
export async function postStream(
  path: string,
  body: unknown,
  onEvent: (data: Record<string, unknown>) => void,
): Promise<void> {
  const r = await fetch(`/api/${path}`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new ApiError(path, r.status);
  if (!r.body) throw new ApiError(path, 0, "no stream body");
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (line) {
        try { onEvent(JSON.parse(line.slice(5).trim())); } catch { /* ignore */ }
      }
    }
  }
}
