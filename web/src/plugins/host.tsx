import * as React from "react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AEGIS_BASE_PATH, api, getSessionToken, TOKEN } from "../lib/api";
import { Card, Empty, Loading } from "../components/ui";

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

export interface DashboardPluginManifest {
  name: string;
  plugin?: string;
  key?: string;
  label?: string;
  title?: string;
  description?: string;
  version?: string;
  icon?: string;
  entry?: string;
  integrity?: string;
  css?: string[];
  ui_asset_status?: {
    status?: string;
    entry?: string;
    entry_exists?: boolean;
    css?: string[];
    missing?: string[];
    errors?: string[];
    asset_count?: number;
    fingerprint?: string;
    checked?: boolean;
  };
  asset_errors?: string[];
  asset_fingerprint?: string;
  base_path: string;
  has_api?: boolean;
  api_mount?: DashboardPluginApiMount;
  tab?: {
    path?: string;
    label?: string;
    position?: string;
    override?: string;
    hidden?: boolean;
  };
  slots?: string[];
}

export interface PluginRenderContext {
  manifest?: DashboardPluginManifest;
  assetBase: string;
  apiBase: string;
  token: string;
  sdk: DashboardPluginSdk;
  authedFetch: PluginFetch;
  fetchJSON: PluginFetchJSON;
  createWebSocket: PluginWebSocketFactory;
}

export type PluginRenderer = (element: HTMLElement, context: PluginRenderContext) => void | (() => void);

export interface DashboardPluginRoute {
  path: string;
  plugin?: string;
  label?: string;
  icon?: string;
  position?: string;
  override?: string;
  hidden?: boolean;
  render?: PluginRenderer;
}

export interface DashboardPluginRegistration {
  name: string;
  routes?: DashboardPluginRoute[];
  slots?: Record<string, PluginRenderer | PluginRenderer[]>;
}

export type PluginFetch = (path: string, init?: RequestInit) => Promise<Response>;
export type PluginFetchJSON = <T = unknown>(path: string, init?: RequestInit) => Promise<T>;
export type PluginWebSocketFactory = (path: string, protocols?: string | string[]) => WebSocket;

export interface DashboardPluginSdk {
  sdkVersion: string;
  React: typeof React;
  register: (plugin: DashboardPluginRegistration) => void;
  registerSlot: (pluginName: string, slotName: string, render: PluginRenderer) => void;
  reload: () => void;
  api: typeof api;
  authedFetch: PluginFetch;
  fetch: PluginFetch;
  fetchJSON: PluginFetchJSON;
  createWebSocket: PluginWebSocketFactory;
  webSocket: PluginWebSocketFactory;
  token: string;
}

type HostState = {
  manifests: DashboardPluginManifest[];
  routes: DashboardPluginRoute[];
  slots: Map<string, Array<{ name: string; render: PluginRenderer }>>;
  pluginStatuses: Map<string, PluginClientStatus>;
  loading: boolean;
  error: string;
  reload: () => void;
};

type RegisteredPlugin = DashboardPluginRegistration;
export type PluginClientAssetState = "pending" | "loaded" | "registered" | "error" | "unregistered" | "stale";
export interface PluginClientStatus {
  name: string;
  asset_status: PluginClientAssetState;
  registered: boolean;
  stale?: boolean;
  entry?: string;
  script_src?: string;
  fingerprint?: string;
  errors?: string[];
  css_errors?: string[];
  loaded_at?: string;
  registered_at?: string;
  timeout_at?: string;
}

const registrations = new Map<string, RegisteredPlugin>();
const pluginStatuses = new Map<string, PluginClientStatus>();
const listeners = new Set<() => void>();
const DASHBOARD_PLUGIN_SDK_VERSION = "0.1.0";

function normalizePath(path: string | undefined, fallback: string): string {
  const raw = (path || fallback || "").trim();
  if (!raw) return fallback;
  return raw.startsWith("/") ? raw : `/${raw}`;
}

function notify() {
  for (const listener of listeners) listener();
}

function nowIso(): string {
  return new Date().toISOString();
}

function setPluginStatus(name: string, patch: Partial<PluginClientStatus>) {
  if (!name) return;
  const previous = pluginStatuses.get(name) || {
    name,
    asset_status: "pending" as PluginClientAssetState,
    registered: false,
    errors: [],
    css_errors: [],
  };
  pluginStatuses.set(name, {
    ...previous,
    ...patch,
    name,
    errors: patch.errors ?? previous.errors ?? [],
    css_errors: patch.css_errors ?? previous.css_errors ?? [],
  });
  notify();
}

function register(plugin: DashboardPluginRegistration) {
  if (!plugin || !plugin.name) return;
  const previousRegistration = registrations.get(plugin.name);
  registrations.set(plugin.name, {
    ...plugin,
    slots: {
      ...(previousRegistration?.slots || {}),
      ...(plugin.slots || {}),
    },
  });
  const previous = pluginStatuses.get(plugin.name);
  setPluginStatus(plugin.name, {
    asset_status: "registered",
    registered: true,
    stale: false,
    registered_at: nowIso(),
    errors: previous?.css_errors || [],
  });
}

function registerSlot(pluginName: string, slotName: string, render: PluginRenderer) {
  const name = String(pluginName || "").trim();
  const slot = String(slotName || "").trim();
  if (!name || !slot || typeof render !== "function") return;
  const previous = registrations.get(name) || { name };
  const slots = { ...(previous.slots || {}) };
  const current = slots[slot];
  slots[slot] = Array.isArray(current) ? [...current, render] : current ? [current, render] : [render];
  registrations.set(name, { ...previous, name, slots });
  const previousStatus = pluginStatuses.get(name);
  setPluginStatus(name, {
    asset_status: "registered",
    registered: true,
    stale: false,
    registered_at: previousStatus?.registered_at || nowIso(),
    errors: previousStatus?.errors || [],
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function pruneRegistrations(manifests: DashboardPluginManifest[]): boolean {
  const active = new Set(manifests.map((manifest) => manifest.name));
  let changed = false;
  for (const name of registrations.keys()) {
    if (active.has(name)) continue;
    registrations.delete(name);
    setPluginStatus(name, {
      asset_status: "stale",
      registered: false,
      stale: true,
      errors: [`plugin registration no longer has a dashboard manifest: ${name}`],
    });
    changed = true;
  }
  return changed;
}

const noopReload = () => {};

function pluginUrl(path: string): string {
  const raw = String(path || "");
  if (/^(https?|wss?):\/\//i.test(raw)) return raw;
  return raw.startsWith("/") ? raw : `/api/${raw.replace(/^api\//, "")}`;
}

function isSameHost(url: string): boolean {
  try {
    return new URL(url, window.location.href).host === window.location.host;
  } catch {
    return true;
  }
}

function pluginFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const url = pluginUrl(path);
  const headers = new Headers(init.headers || {});
  const token = getSessionToken();
  if (token && isSameHost(url) && !headers.has("X-Aegis-Token")) headers.set("X-Aegis-Token", token);
  return window.fetch(url, { ...init, headers, credentials: init.credentials ?? "include" });
}

async function pluginFetchJSON<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await pluginFetch(path, init);
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  if (!response.ok) {
    throw new Error(text.slice(0, 300) || `${path}: ${response.status}`);
  }
  if (!text || response.status === 204) return undefined as T;
  return (contentType.includes("application/json") ? JSON.parse(text) : text) as T;
}

function mintWsTicketSync(): string {
  try {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${AEGIS_BASE_PATH}/api/auth/ws-ticket`, false);
    xhr.withCredentials = true;
    const token = getSessionToken();
    if (token) xhr.setRequestHeader("X-Aegis-Token", token);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.send("{}");
    if (xhr.status >= 200 && xhr.status < 300) {
      const body = JSON.parse(xhr.responseText || "{}");
      return String(body.ticket || "");
    }
  } catch {
    // Fall through: cookie-authenticated sockets may still be accepted.
  }
  return "";
}

function pluginWebSocket(path: string, protocols?: string | string[]): WebSocket {
  const url = new URL(pluginUrl(path), window.location.href);
  if (url.protocol === "http:") url.protocol = "ws:";
  else if (url.protocol === "https:") url.protocol = "wss:";
  if (url.host === window.location.host && !url.searchParams.has("ticket")) {
    const ticket = mintWsTicketSync();
    if (ticket) url.searchParams.set("ticket", ticket);
  }
  return new WebSocket(url.toString(), protocols);
}

const dashboardPluginSdk: DashboardPluginSdk = {
  sdkVersion: DASHBOARD_PLUGIN_SDK_VERSION,
  React,
  register,
  registerSlot,
  reload: noopReload,
  api,
  authedFetch: pluginFetch,
  fetch: pluginFetch,
  fetchJSON: pluginFetchJSON,
  createWebSocket: pluginWebSocket,
  webSocket: pluginWebSocket,
  token: TOKEN,
};

function getDashboardPluginSdk(reload?: () => void): DashboardPluginSdk {
  if (reload) dashboardPluginSdk.reload = reload;
  return dashboardPluginSdk;
}

function ensureGlobalHost(reload: () => void = noopReload) {
  const existing = window.__AEGIS_PLUGINS__;
  const sdk = getDashboardPluginSdk(reload);
  const host = {
    ...(existing || {}),
    ...sdk,
    sdk,
  };
  window.__AEGIS_PLUGINS__ = host;
  window.__AEGIS_PLUGIN_SDK__ = sdk;
}

function assetUrl(manifest: DashboardPluginManifest, rel: string | undefined): string {
  const clean = (rel || "").replace(/^\/+/, "");
  const raw = `${manifest.base_path}/${clean}`;
  const fingerprint = manifest.asset_fingerprint || manifest.ui_asset_status?.fingerprint || "";
  if (!fingerprint) return raw;
  return `${raw}${raw.includes("?") ? "&" : "?"}v=${encodeURIComponent(fingerprint)}`;
}

function assetFingerprint(manifest: DashboardPluginManifest): string {
  return manifest.asset_fingerprint || manifest.ui_asset_status?.fingerprint || "";
}

function injectManifestAssets(manifest: DashboardPluginManifest) {
  const fingerprint = assetFingerprint(manifest);
  for (const css of manifest.css || []) {
    const id = `aegis-plugin-css-${manifest.name}-${css}`;
    const href = assetUrl(manifest, css);
    const existing = document.getElementById(id) as HTMLLinkElement | null;
    const sameCssFingerprint = fingerprint ? existing?.dataset.fingerprint === fingerprint : true;
    if (existing && sameCssFingerprint && existing.href.endsWith(href)) continue;
    existing?.remove();
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.href = href;
    link.dataset.fingerprint = fingerprint;
    link.onerror = () => {
      const previous = pluginStatuses.get(manifest.name);
      const cssErrors = [...(previous?.css_errors || []), `stylesheet failed to load: ${css}`];
      setPluginStatus(manifest.name, {
        asset_status: "error",
        css_errors: cssErrors,
        errors: [...(previous?.errors || []), `stylesheet failed to load: ${css}`],
      });
    };
    document.head.appendChild(link);
  }

  const entry = manifest.entry || "dist/index.js";
  const id = `aegis-plugin-js-${manifest.name}`;
  const scriptSrc = assetUrl(manifest, entry);
  const existing = document.getElementById(id) as HTMLScriptElement | null;
  const sameScriptFingerprint = fingerprint ? existing?.dataset.fingerprint === fingerprint : true;
  if (existing && sameScriptFingerprint && existing.src.endsWith(scriptSrc)) return;
  if (existing) {
    existing.remove();
    registrations.delete(manifest.name);
  }
  setPluginStatus(manifest.name, {
    asset_status: registrations.has(manifest.name) ? "registered" : "pending",
    registered: registrations.has(manifest.name),
    stale: false,
    entry,
    script_src: scriptSrc,
    fingerprint,
    errors: [],
  });
  const script = document.createElement("script");
  script.id = id;
  script.src = scriptSrc;
  script.async = true;
  script.dataset.plugin = manifest.name;
  script.dataset.fingerprint = fingerprint;
  if (manifest.integrity) {
    script.integrity = manifest.integrity;
    script.crossOrigin = "anonymous";
  }
  script.onload = () => {
    if (registrations.has(manifest.name)) {
      const previous = pluginStatuses.get(manifest.name);
      setPluginStatus(manifest.name, {
        asset_status: "registered",
        registered: true,
        loaded_at: nowIso(),
        errors: previous?.css_errors || [],
      });
      return;
    }
    setPluginStatus(manifest.name, {
      asset_status: "loaded",
      registered: false,
      loaded_at: nowIso(),
    });
    window.setTimeout(() => {
      if (registrations.has(manifest.name)) return;
      setPluginStatus(manifest.name, {
        asset_status: "unregistered",
        registered: false,
        timeout_at: nowIso(),
        errors: [`plugin script loaded but did not register: ${manifest.name}`],
      });
    }, 2500);
  };
  script.onerror = () => setPluginStatus(manifest.name, {
    asset_status: "error",
    registered: false,
    errors: [`script failed to load: ${entry}`],
  });
  document.body.appendChild(script);
}

function manifestRoute(manifest: DashboardPluginManifest): DashboardPluginRoute | null {
  const tab = manifest.tab || {};
  if (tab.hidden) return null;
  const override = tab.override ? normalizePath(tab.override, "") : undefined;
  const path = normalizePath(override || tab.path, `/plugins/${manifest.name}`);
  return {
    path,
    plugin: manifest.name,
    label: tab.label || manifest.label || manifest.title || manifest.name,
    icon: manifest.icon || "plugins",
    position: tab.position || "end",
    override,
  };
}

function buildState(manifests: DashboardPluginManifest[], loading: boolean, error: string): Omit<HostState, "reload"> {
  const byName = new Map(manifests.map((manifest) => [manifest.name, manifest]));
  const routes: DashboardPluginRoute[] = [];
  const slots = new Map<string, Array<{ name: string; render: PluginRenderer }>>();

  for (const manifest of manifests) {
    const registered = registrations.get(manifest.name);
    const declared = manifestRoute(manifest);
    for (const route of registered?.routes || []) {
      if (route.hidden) continue;
      routes.push({
        ...route,
        plugin: manifest.name,
        path: normalizePath(route.override || route.path, declared?.path || `/plugins/${manifest.name}`),
        label: route.label || declared?.label,
        icon: route.icon || declared?.icon || manifest.icon || "plugins",
        position: route.position || declared?.position || "end",
        override: route.override ? normalizePath(route.override, "") : declared?.override,
      });
    }
    if (declared && !routes.some((route) => route.path === declared.path)) {
      routes.push(declared);
    }
    for (const [slotName, renderers] of Object.entries(registered?.slots || {})) {
      const list = Array.isArray(renderers) ? renderers : [renderers];
      const bucket = slots.get(slotName) || [];
      for (const render of list) bucket.push({ name: manifest.name, render });
      slots.set(slotName, bucket);
    }
  }

  if (loading || error) {
    for (const [name, registered] of registrations) {
      if (byName.has(name)) continue;
      for (const route of registered.routes || []) {
        if (route.hidden) continue;
        routes.push({
          ...route,
          plugin: name,
          path: normalizePath(route.path, `/plugins/${name}`),
          icon: route.icon || "plugins",
          position: route.position || "end",
        });
      }
      for (const [slotName, renderers] of Object.entries(registered.slots || {})) {
        const list = Array.isArray(renderers) ? renderers : [renderers];
        const bucket = slots.get(slotName) || [];
        for (const render of list) bucket.push({ name, render });
        slots.set(slotName, bucket);
      }
    }
  }

  return { manifests, routes, slots, pluginStatuses: new Map(pluginStatuses), loading, error };
}

const PluginContext = createContext<HostState>({
  manifests: [],
  routes: [],
  slots: new Map(),
  pluginStatuses: new Map(),
  loading: true,
  error: "",
  reload: noopReload,
});

export function DashboardPluginProvider({ children }: { children: ReactNode }) {
  const [manifests, setManifests] = useState<DashboardPluginManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [, setVersion] = useState(0);
  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  useEffect(() => {
    ensureGlobalHost(reload);
    return subscribe(() => setVersion((version) => version + 1));
  }, [reload]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<DashboardPluginManifest[]>("dashboard/plugins")
      .then((rows) => {
        if (cancelled) return;
        const nextManifests = rows || [];
        const pruned = pruneRegistrations(nextManifests);
        setManifests(nextManifests);
        setError("");
        for (const manifest of nextManifests) injectManifestAssets(manifest);
        if (pruned) notify();
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [reloadKey]);

  const value = useMemo(() => ({ ...buildState(manifests, loading, error), reload }), [manifests, loading, error, reload]);
  return <PluginContext.Provider value={value}>{children}</PluginContext.Provider>;
}

export function useDashboardPluginHost() {
  return useContext(PluginContext);
}

export function PluginSlot({ name, className = "" }: { name: string; className?: string }) {
  const { slots, manifests } = useDashboardPluginHost();
  const renders = slots.get(name) || [];
  if (!renders.length) return null;
  return (
    <div className={className} data-plugin-slot={name}>
      {renders.map((entry, index) => (
        <PluginMount key={`${entry.name}-${name}-${index}`} name={entry.name} render={entry.render} manifests={manifests} />
      ))}
    </div>
  );
}

function PluginMount({
  name,
  render,
  manifests,
}: {
  name: string;
  render: PluginRenderer;
  manifests: DashboardPluginManifest[];
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const manifest = manifests.find((row) => row.name === name);
  useEffect(() => {
    if (!ref.current) return;
    ref.current.innerHTML = "";
    const cleanup = render(ref.current, {
      manifest,
      assetBase: manifest?.base_path || `/dashboard-plugins/${name}`,
      apiBase: `/api/plugins/${name}`,
      token: TOKEN,
      sdk: getDashboardPluginSdk(),
      authedFetch: pluginFetch,
      fetchJSON: pluginFetchJSON,
      createWebSocket: pluginWebSocket,
    });
    return typeof cleanup === "function" ? cleanup : undefined;
  }, [manifest, name, render]);
  return <div ref={ref} data-plugin-mount={name} />;
}

export function PluginRoutePage({ route }: { route: DashboardPluginRoute }) {
  const { manifests, pluginStatuses, loading } = useDashboardPluginHost();
  const manifest = manifests.find(
    (row) => row.name === route.plugin || normalizePath(row.tab?.override || row.tab?.path, `/plugins/${row.name}`) === route.path,
  );
  const name = route.plugin || manifest?.name || route.label || route.path.replace(/^\//, "");
  if (route.render) {
    return <PluginMount name={name} render={route.render} manifests={manifests} />;
  }
  if (loading) return <Loading />;
  const assetErrors = manifest?.asset_errors || manifest?.ui_asset_status?.errors || [];
  const clientStatus = pluginStatuses.get(name);
  if (manifest?.ui_asset_status?.status === "error") {
    return (
      <Card>
        <Empty icon="alert">
          Plugin UI asset error: {assetErrors[0] || route.label || route.path}
        </Empty>
      </Card>
    );
  }
  if (clientStatus?.asset_status === "error" || clientStatus?.asset_status === "unregistered") {
    return (
      <Card>
        <Empty icon="alert">
          Plugin script {clientStatus.asset_status}: {(clientStatus.errors || [])[0] || route.label || route.path}
        </Empty>
      </Card>
    );
  }
  return (
    <Card>
      <Empty icon="plugins">Plugin route waiting for its script: {route.label || route.path}</Empty>
    </Card>
  );
}

interface DashboardPluginGlobal extends Partial<DashboardPluginSdk> {
  sdk?: DashboardPluginSdk;
  reload?: () => void;
  [key: string]: unknown;
}

declare global {
  interface Window {
    __AEGIS_PLUGINS__?: DashboardPluginGlobal;
    __AEGIS_PLUGIN_SDK__?: DashboardPluginSdk;
  }
}
