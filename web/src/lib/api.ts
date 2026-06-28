// Typed client for the AEGIS dashboard backend (aegis/dashboard_fastapi.py).
// Auth state is injected by the dashboard HTML. The client intentionally does
// not persist long-lived tokens in localStorage or copy them into regular links;
// REST uses headers/cookies and WebSockets use short-lived /api/auth/ws-ticket
// credentials.

declare global {
  interface Window {
    __AEGIS_SESSION_TOKEN__?: string;
    __AEGIS_AUTH_REQUIRED__?: boolean;
    __AEGIS_BASE_PATH__?: string;
  }
}

function readBasePath(): string {
  const raw = window.__AEGIS_BASE_PATH__ || "";
  if (!raw) return "";
  const withLead = raw.startsWith("/") ? raw : `/${raw}`;
  return withLead.replace(/\/+$/, "");
}

export const AEGIS_BASE_PATH = readBasePath();
export const AUTH_REQUIRED = Boolean(window.__AEGIS_AUTH_REQUIRED__);

export function getSessionToken(): string {
  return window.__AEGIS_SESSION_TOKEN__ || "";
}

// Back-compat for plugin contexts that read sdk.token. Prefer authedFetch(),
// buildWsUrl(), and downloadUrl(); TOKEN is never persisted or appended to UI links.
export const TOKEN = getSessionToken();

function apiPath(path: string): string {
  const clean = String(path || "").replace(/^\/+/, "").replace(/^api\//, "");
  return `${AEGIS_BASE_PATH}/api/${clean}`;
}

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
  return fetch(apiPath(path), { headers: headers(), credentials: "include" }).then((r) => parse<T>(r, path));
}

export function post<T = unknown>(path: string, body?: unknown): Promise<T> {
  return fetch(apiPath(path), {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body ?? {}),
    credentials: "include",
  }).then((r) => parse<T>(r, path));
}

export function put<T = unknown>(path: string, body?: unknown): Promise<T> {
  return fetch(apiPath(path), {
    method: "PUT",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body ?? {}),
    credentials: "include",
  }).then((r) => parse<T>(r, path));
}

export function patch<T = unknown>(path: string, body?: unknown): Promise<T> {
  return fetch(apiPath(path), {
    method: "PATCH",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body ?? {}),
    credentials: "include",
  }).then((r) => parse<T>(r, path));
}

export function del<T = unknown>(path: string): Promise<T> {
  return fetch(apiPath(path), { method: "DELETE", headers: headers(), credentials: "include" })
    .then((r) => parse<T>(r, path));
}

export interface AuthMe {
  authenticated?: boolean;
  auth_required?: boolean;
  providers?: string[];
  user?: string;
}

export interface AuthWsTicket {
  ok?: boolean;
  ticket: string;
  ttl_seconds?: number;
  expires_at?: string;
}

export function authMe(): Promise<AuthMe> {
  return api<AuthMe>("auth/me");
}

export async function authWsTicket(): Promise<AuthWsTicket> {
  return post<AuthWsTicket>("auth/ws-ticket", {});
}

export async function buildWsAuthParam(): Promise<[string, string] | null> {
  try {
    const ticket = await authWsTicket();
    if (ticket.ticket) return ["ticket", ticket.ticket];
  } catch {
    // Cookie-authenticated WebSockets may still succeed. Do not fall back to a
    // long-lived token in the URL.
  }
  return null;
}

export async function buildWsUrl(path: string, params: Record<string, string> = {}): Promise<string> {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const rawPath = path.startsWith("/") ? path : `/api/${path.replace(/^api\//, "")}`;
  const qs = new URLSearchParams(params);
  const auth = await buildWsAuthParam();
  if (auth) qs.set(auth[0], auth[1]);
  return `${proto}//${window.location.host}${AEGIS_BASE_PATH}${rawPath}?${qs.toString()}`;
}

export function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const raw = String(path || "");
  const url = /^(https?:)?\/\//i.test(raw)
    ? raw
    : raw.startsWith("/api/")
      ? `${AEGIS_BASE_PATH}${raw}`
      : apiPath(raw);
  const h = new Headers(init.headers || {});
  const token = getSessionToken();
  if (token && !h.has("X-Aegis-Token")) h.set("X-Aegis-Token", token);
  return fetch(url, { ...init, headers: h, credentials: init.credentials ?? "include" });
}

export function downloadUrl(path: string, params: Record<string, string> = {}): string {
  const raw = path.startsWith("/") ? path : `/api/${path.replace(/^api\//, "")}`;
  const qs = new URLSearchParams(params);
  const query = qs.toString();
  return `${AEGIS_BASE_PATH}${raw}${query ? `?${query}` : ""}`;
}

export interface DashboardPluginApiMount {
  status?: string;
  mounted?: boolean;
  api?: string;
  routes?: string[];
  error?: string;
  request_count?: number;
  success_count?: number;
  error_count?: number;
  last_request_at?: string;
  last_request_path?: string;
  last_request_method?: string;
  last_success_at?: string;
  last_error_at?: string;
  last_error_path?: string;
  last_error_method?: string;
  last_error_type?: string;
  last_error?: string;
  mount_count?: number;
  mount_error_count?: number;
  mounted_at?: string;
  mount_error_at?: string;
  mount_duration_ms?: number;
  fingerprint?: string;
}

export interface DashboardPluginUiAssetStatus {
  status?: string;
  entry?: string;
  entry_exists?: boolean;
  css?: string[];
  missing?: string[];
  errors?: string[];
  asset_count?: number;
  fingerprint?: string;
  checked?: boolean;
}

export interface DashboardPluginManifest {
  name: string;
  plugin?: string;
  key?: string;
  label?: string;
  title?: string;
  description?: string;
  version?: string;
  icon?: string;
  kind?: string;
  category?: string;
  source?: string;
  entry?: string;
  integrity?: string;
  css?: string[];
  asset_fingerprint?: string;
  base_path?: string;
  has_api?: boolean;
  ui_asset_status?: DashboardPluginUiAssetStatus;
  asset_errors?: string[];
  api_mounted?: boolean;
  api_routes?: string[];
  api_mount?: DashboardPluginApiMount;
  route?: {
    path?: string;
    label?: string;
    plugin?: string;
    position?: string;
    override?: string;
    hidden?: boolean;
  };
  tab?: {
    path?: string;
    label?: string;
    position?: string;
    override?: string;
    hidden?: boolean;
  };
  slots?: string[];
  user_hidden?: boolean;
}

export type PluginProviderOption = string | { name: string; description?: string };

export interface PluginContributionMap {
  tools?: string[];
  channels?: string[];
  providers?: string[];
  hooks?: string[];
  middleware?: string[];
}

export type PluginContributionDrift = Record<string, { missing?: string[]; extra?: string[] }>;

export interface DashboardPluginHubRow {
  name: string;
  key?: string;
  version?: string;
  description?: string;
  kind?: string;
  category?: string;
  source?: string;
  status?: string;
  runtime_status?: string;
  load_status?: string;
  load_duration_ms?: number;
  loaded_at?: string;
  load_error?: string;
  enabled?: boolean;
  loaded?: boolean;
  path?: string;
  has_dashboard_manifest?: boolean;
  dashboard_manifest?: DashboardPluginManifest | null;
  dashboard_route?: DashboardPluginManifest["route"] | null;
  api_mount?: DashboardPluginManifest["api_mount"] | null;
  ui_asset_status?: DashboardPluginUiAssetStatus | null;
  asset_errors?: string[];
  can_remove?: boolean;
  can_update_git?: boolean;
  auth_required?: boolean;
  auth_command?: string;
  user_hidden?: boolean;
  tool_names?: string[];
  channel_names?: string[];
  provider_names?: string[];
  hook_names?: string[];
  middleware_kinds?: string[];
  declared_contributions?: PluginContributionMap;
  runtime_contributions?: PluginContributionMap;
  contribution_drift?: PluginContributionDrift;
  manifest_errors?: string[];
}

export interface ExtensionStatus {
  ok?: boolean;
  mcp?: {
    enabled?: boolean;
    server_count?: number;
    active_server_count?: number;
    stdio_count?: number;
    http_count?: number;
    malformed?: string[];
    catalog_count?: number;
    filtered_server_count?: number;
    selected_tool_count?: number;
  };
  plugins?: {
    safe_mode?: boolean;
    manifest_count?: number;
    loaded_count?: number;
    error_count?: number;
    runtime_error_count?: number;
    dashboard_plugin_count?: number;
    dashboard_api_route_count?: number;
    dashboard_api_error_count?: number;
    middleware_kinds?: string[];
    hook_names?: string[];
    enabled?: string[];
    disabled?: string[];
    allowlist?: string[];
    manifest_errors?: Array<{ name: string; errors: string[] }>;
  };
  acp?: {
    enabled?: boolean;
    protocol_version?: number;
    surface_runner?: string;
    session_store?: string;
    shared_trace_state?: boolean;
    session_management?: string[];
  };
}

export interface DashboardPluginsHub {
  ok?: boolean;
  plugins: DashboardPluginHubRow[];
  plugin_status?: DashboardPluginHubRow[];
  manifests?: unknown[];
  orphan_dashboard_plugins?: DashboardPluginManifest[];
  providers?: {
    memory_provider?: string;
    memory_options?: PluginProviderOption[];
    context_engine?: string;
    context_options?: PluginProviderOption[];
  };
  loaded?: string[];
  errors?: Array<{ file?: string; path?: string; error: string }>;
  enabled?: string[];
  disabled?: string[];
  allowlist?: string[];
  safe_mode?: boolean;
  extension_status?: ExtensionStatus;
}

function pluginPath(name: string): string {
  return String(name || "").split("/").map(encodeURIComponent).join("/");
}

export const pluginsApi = {
  hub: () => api<DashboardPluginsHub>("dashboard/plugins/hub"),
  rescan: () => api<{ ok?: boolean; count?: number }>("dashboard/plugins/rescan"),
  install: (body: { identifier: string; force?: boolean; enable?: boolean }) =>
    post<DashboardPluginsHub & { ok?: boolean; name?: string; plugin_name?: string; error?: string }>(
      "dashboard/agent-plugins/install",
      body,
    ),
  enable: (name: string) =>
    post<DashboardPluginsHub & { ok?: boolean; name?: string; error?: string }>(
      `dashboard/agent-plugins/${pluginPath(name)}/enable`,
      {},
    ),
  disable: (name: string) =>
    post<DashboardPluginsHub & { ok?: boolean; name?: string; error?: string }>(
      `dashboard/agent-plugins/${pluginPath(name)}/disable`,
      {},
    ),
  update: (name: string) =>
    post<DashboardPluginsHub & { ok?: boolean; name?: string; output?: string; unchanged?: boolean; error?: string }>(
      `dashboard/agent-plugins/${pluginPath(name)}/update`,
      {},
    ),
  remove: (name: string) =>
    del<DashboardPluginsHub & { ok?: boolean; name?: string; error?: string }>(
      `dashboard/agent-plugins/${pluginPath(name)}`,
    ),
  saveProviders: (body: { memory_provider?: string; context_engine?: string }) =>
    put<DashboardPluginsHub & { ok?: boolean; error?: string }>("dashboard/plugin-providers", body),
  setVisibility: (name: string, hidden: boolean) =>
    post<DashboardPluginsHub & { ok?: boolean; name?: string; hidden?: boolean; error?: string }>(
      `dashboard/plugins/${pluginPath(name)}/visibility`,
      { hidden },
    ),
};

/** Subscribe to a server-sent-events endpoint. Returns an unsubscribe fn. */
export function sse(path: string, onMessage: (data: unknown) => void): () => void {
  const es = new EventSource(apiPath(path), { withCredentials: true });
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
  options: { signal?: AbortSignal } = {},
): Promise<void> {
  const r = await fetch(apiPath(path), {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    signal: options.signal,
    credentials: "include",
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
