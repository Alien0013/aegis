import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api, TOKEN } from "../lib/api";
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
  css?: string[];
  ui_asset_status?: {
    status?: string;
    entry?: string;
    entry_exists?: boolean;
    css?: string[];
    missing?: string[];
    errors?: string[];
    asset_count?: number;
    checked?: boolean;
  };
  asset_errors?: string[];
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
}

export type PluginRenderer = (element: HTMLElement, context: PluginRenderContext) => void | (() => void);

export interface DashboardPluginRoute {
  path: string;
  plugin?: string;
  label?: string;
  icon?: string;
  position?: string;
  hidden?: boolean;
  render?: PluginRenderer;
}

export interface DashboardPluginRegistration {
  name: string;
  routes?: DashboardPluginRoute[];
  slots?: Record<string, PluginRenderer | PluginRenderer[]>;
}

type HostState = {
  manifests: DashboardPluginManifest[];
  routes: DashboardPluginRoute[];
  slots: Map<string, Array<{ name: string; render: PluginRenderer }>>;
  loading: boolean;
  error: string;
  reload: () => void;
};

type RegisteredPlugin = DashboardPluginRegistration;

const registrations = new Map<string, RegisteredPlugin>();
const listeners = new Set<() => void>();

function normalizePath(path: string | undefined, fallback: string): string {
  const raw = (path || fallback || "").trim();
  if (!raw) return fallback;
  return raw.startsWith("/") ? raw : `/${raw}`;
}

function notify() {
  for (const listener of listeners) listener();
}

function register(plugin: DashboardPluginRegistration) {
  if (!plugin || !plugin.name) return;
  registrations.set(plugin.name, plugin);
  notify();
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
    changed = true;
  }
  return changed;
}

const noopReload = () => {};

function ensureGlobalHost(reload: () => void = noopReload) {
  const existing = window.__AEGIS_PLUGINS__;
  const host = {
    ...(existing || {}),
    register,
    reload,
    api,
    token: TOKEN,
  };
  window.__AEGIS_PLUGINS__ = host;
  window.__HERMES_PLUGINS__ = host;
}

function assetUrl(manifest: DashboardPluginManifest, rel: string | undefined): string {
  const clean = (rel || "").replace(/^\/+/, "");
  return `${manifest.base_path}/${clean}`;
}

function injectManifestAssets(manifest: DashboardPluginManifest) {
  for (const css of manifest.css || []) {
    const id = `aegis-plugin-css-${manifest.name}-${css}`;
    if (document.getElementById(id)) continue;
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.href = assetUrl(manifest, css);
    document.head.appendChild(link);
  }

  const entry = manifest.entry || "dist/index.js";
  const id = `aegis-plugin-js-${manifest.name}`;
  if (document.getElementById(id)) return;
  const script = document.createElement("script");
  script.id = id;
  script.src = assetUrl(manifest, entry);
  script.async = true;
  script.dataset.plugin = manifest.name;
  document.body.appendChild(script);
}

function manifestRoute(manifest: DashboardPluginManifest): DashboardPluginRoute | null {
  const tab = manifest.tab || {};
  if (tab.hidden) return null;
  const path = normalizePath(tab.override || tab.path, `/plugins/${manifest.name}`);
  return {
    path,
    plugin: manifest.name,
    label: tab.label || manifest.label || manifest.title || manifest.name,
    icon: manifest.icon || "plugins",
    position: tab.position || "end",
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
        path: normalizePath(route.path, declared?.path || `/plugins/${manifest.name}`),
        label: route.label || declared?.label,
        icon: route.icon || declared?.icon || manifest.icon || "plugins",
        position: route.position || declared?.position || "end",
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

  return { manifests, routes, slots, loading, error };
}

const PluginContext = createContext<HostState>({
  manifests: [],
  routes: [],
  slots: new Map(),
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
    });
    return typeof cleanup === "function" ? cleanup : undefined;
  }, [manifest, name, render]);
  return <div ref={ref} data-plugin-mount={name} />;
}

export function PluginRoutePage({ route }: { route: DashboardPluginRoute }) {
  const { manifests, loading } = useDashboardPluginHost();
  const manifest = manifests.find(
    (row) => row.name === route.plugin || normalizePath(row.tab?.override || row.tab?.path, `/plugins/${row.name}`) === route.path,
  );
  const name = route.plugin || manifest?.name || route.label || route.path.replace(/^\//, "");
  if (route.render) {
    return <PluginMount name={name} render={route.render} manifests={manifests} />;
  }
  if (loading) return <Loading />;
  const assetErrors = manifest?.asset_errors || manifest?.ui_asset_status?.errors || [];
  if (manifest?.ui_asset_status?.status === "error") {
    return (
      <Card>
        <Empty icon="alert">
          Plugin UI asset error: {assetErrors[0] || route.label || route.path}
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

interface DashboardPluginGlobal {
  register?: (plugin: DashboardPluginRegistration) => void;
  reload?: () => void;
  api?: typeof api;
  token?: string;
  [key: string]: unknown;
}

declare global {
  interface Window {
    __AEGIS_PLUGINS__?: DashboardPluginGlobal;
    __HERMES_PLUGINS__?: DashboardPluginGlobal;
  }
}
