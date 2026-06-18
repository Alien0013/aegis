import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  pluginsApi,
  type DashboardPluginHubRow,
  type DashboardPluginsHub,
  type PluginProviderOption,
} from "../lib/api";
import { useApi } from "../lib/useApi";
import {
  Badge,
  Button,
  Card,
  Empty,
  Field,
  Input,
  Loading,
  MetricStrip,
  PageHeader,
  Select,
  Toggle,
  toast,
} from "../components/ui";
import { Icon } from "../components/icons";
import { PluginSlot, useDashboardPluginHost } from "../plugins/host";

const BUILTIN_MEMORY = "__aegis_builtin_memory__";

function optionName(option: PluginProviderOption): string {
  return typeof option === "string" ? option : option.name;
}

function optionDescription(option: PluginProviderOption): string {
  return typeof option === "string" ? "" : option.description || "";
}

function pluginId(row: DashboardPluginHubRow): string {
  return row.key || row.name;
}

function rowStatus(row: DashboardPluginHubRow): string {
  return row.runtime_status || row.status || (row.enabled === false ? "disabled" : "inactive");
}

function statusTone(status: string): "success" | "danger" | "warning" | "info" | "neutral" {
  if (status === "enabled" || status === "loaded" || status === "dashboard") return "success";
  if (status === "disabled") return "danger";
  if (status === "error") return "danger";
  if (status === "inactive") return "warning";
  return "neutral";
}

function loadLabel(row: DashboardPluginHubRow): string {
  if (!row.loaded_at && row.load_duration_ms == null) return "";
  const ms = Number(row.load_duration_ms || 0);
  return ms < 1000 ? `${ms.toFixed(ms < 10 ? 1 : 0)}ms` : `${(ms / 1000).toFixed(2)}s`;
}

function durationLabel(ms: unknown): string {
  const value = Number(ms || 0);
  if (!Number.isFinite(value) || value <= 0) return "";
  return value < 1000 ? `${value.toFixed(value < 10 ? 1 : 0)}ms` : `${(value / 1000).toFixed(2)}s`;
}

function driftCount(row: DashboardPluginHubRow): number {
  return Object.values(row.contribution_drift || {}).reduce((count, item) => (
    count + (item.missing || []).length + (item.extra || []).length
  ), 0);
}

function openRoute(row: DashboardPluginHubRow): string {
  const manifest = row.dashboard_manifest;
  const route = row.dashboard_route || manifest?.route;
  const tab = manifest?.tab;
  if (route?.hidden || tab?.hidden) return "";
  return route?.override || route?.path || tab?.override || tab?.path || "";
}

function mountInfo(row: DashboardPluginHubRow) {
  return row.api_mount || row.dashboard_manifest?.api_mount;
}

function contributions(row: DashboardPluginHubRow): Array<{ key: string; tone: "info" | "success" | "primary" | "neutral"; icon?: string }> {
  return [
    ...(row.tool_names || []).map((key) => ({ key, tone: "info" as const, icon: "tools" })),
    ...(row.provider_names || []).map((key) => ({ key, tone: "success" as const })),
    ...(row.channel_names || []).map((key) => ({ key, tone: "primary" as const })),
    ...(row.hook_names || []).map((key) => ({ key, tone: "neutral" as const })),
    ...(row.middleware_kinds || []).map((key) => ({ key, tone: "neutral" as const })),
  ];
}

export function Plugins() {
  const { data, loading, error, reload, setData } = useApi<DashboardPluginsHub>("dashboard/plugins/hub");
  const pluginHost = useDashboardPluginHost();
  const [installId, setInstallId] = useState("");
  const [installForce, setInstallForce] = useState(false);
  const [installEnable, setInstallEnable] = useState(true);
  const [busy, setBusy] = useState("");
  const [memoryProvider, setMemoryProvider] = useState(BUILTIN_MEMORY);
  const [contextEngine, setContextEngine] = useState("default");

  useEffect(() => {
    const providers = data?.providers;
    if (!providers) return;
    setMemoryProvider(providers.memory_provider || BUILTIN_MEMORY);
    setContextEngine(providers.context_engine || "default");
  }, [data?.providers]);

  const rows = data?.plugins || [];
  const errors = data?.errors || [];
  const enabledCount = rows.filter((row) => ["enabled", "loaded"].includes(rowStatus(row))).length;
  const dashboardCount = rows.filter((row) => row.has_dashboard_manifest).length;

  const memoryOptions = useMemo(
    () => (data?.providers?.memory_options || []).filter((option) => optionName(option)),
    [data?.providers?.memory_options],
  );
  const contextOptions = useMemo(
    () => (data?.providers?.context_options || []).filter((option) => optionName(option)),
    [data?.providers?.context_options],
  );

  async function applyHub(action: () => Promise<DashboardPluginsHub>, message: string) {
    setBusy(message);
    try {
      const result = await action();
      if (result.plugins) setData(result);
      else reload();
      pluginHost.reload();
      toast(message);
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy("");
    }
  }

  function refresh() {
    void applyHub(() => pluginsApi.rescan().then(() => pluginsApi.hub()), "Rescanned");
  }

  function installPlugin() {
    const identifier = installId.trim();
    if (!identifier) {
      toast("Plugin source is required", "err");
      return;
    }
    void applyHub(
      () => pluginsApi.install({ identifier, force: installForce, enable: installEnable }),
      "Installed",
    ).then(() => setInstallId(""));
  }

  function saveProviders() {
    void applyHub(
      () => pluginsApi.saveProviders({
        memory_provider: memoryProvider === BUILTIN_MEMORY ? "" : memoryProvider,
        context_engine: contextEngine || "default",
      }),
      "Saved providers",
    );
  }

  return (
    <>
      <PageHeader
        title="Plugins"
        sub={data ? `${rows.length} packages · ${enabledCount} enabled · ${dashboardCount} dashboard panels` : "Drop-in extensions"}
        actions={<Button variant="ghost" icon="refresh" disabled={!!busy} onClick={refresh}>Rescan</Button>}
      />

      <PluginSlot name="plugins:top" className="mb-[var(--gap)]" />

      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}

      {data && (
        <div className="space-y-[var(--gap)]">
          <MetricStrip
            items={[
              { label: "Packages", value: rows.length },
              { label: "Enabled", value: enabledCount, tone: "success" },
              { label: "Dashboard", value: dashboardCount, tone: "info" },
              { label: "Errors", value: errors.length, tone: errors.length ? "danger" : "neutral" },
            ]}
          />

          {!!errors.length && (
            <Card title="Load Errors">
              <div className="space-y-1">
                {errors.map((item, index) => (
                  <div key={`${item.path || item.file || "plugin"}-${index}`} className="text-xs text-danger">
                    <span className="font-mono">{item.path || item.file || "plugin"}</span>: {item.error}
                  </div>
                ))}
              </div>
            </Card>
          )}

          {data.providers && (
            <Card
              title="Runtime Providers"
              sub="Provider choices used by plugin-backed memory and context engines."
              actions={<Button sm variant="primary" disabled={!!busy} onClick={saveProviders}>Save</Button>}
            >
              <div className="grid gap-[var(--gap)] md:grid-cols-2">
                <Field label="Memory Provider">
                  <Select value={memoryProvider} onChange={(event) => setMemoryProvider(event.target.value)}>
                    <option value={BUILTIN_MEMORY}>(builtin)</option>
                    {memoryOptions.map((option) => (
                      <option key={optionName(option)} value={optionName(option)}>
                        {optionDescription(option) ? `${optionName(option)} — ${optionDescription(option)}` : optionName(option)}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Context Engine">
                  <Select value={contextEngine} onChange={(event) => setContextEngine(event.target.value)}>
                    {!contextOptions.some((option) => optionName(option) === "default") && <option value="default">default</option>}
                    {contextOptions.map((option) => (
                      <option key={optionName(option)} value={optionName(option)}>
                        {optionDescription(option) ? `${optionName(option)} — ${optionDescription(option)}` : optionName(option)}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>
            </Card>
          )}

          <Card
            title="Install"
            sub="Local .py files and plugin directories are installed into the AEGIS plugin home."
            actions={<Button sm variant="primary" disabled={!!busy} icon="download" onClick={installPlugin}>Install</Button>}
          >
            <div className="grid gap-[var(--gap)] lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
              <Field label="Source">
                <Input
                  spellCheck={false}
                  className="font-mono"
                  value={installId}
                  onChange={(event) => setInstallId(event.target.value)}
                  placeholder="/path/to/plugin.py or /path/to/plugin-dir"
                />
              </Field>
              <div className="flex flex-wrap gap-5 pb-1">
                <label className="flex items-center gap-2 text-xs text-dim">
                  <Toggle on={installForce} onChange={setInstallForce} disabled={!!busy} />
                  Force
                </label>
                <label className="flex items-center gap-2 text-xs text-dim">
                  <Toggle on={installEnable} onChange={setInstallEnable} disabled={!!busy} />
                  Enable
                </label>
              </div>
            </div>
          </Card>

          <div className="grid gap-[var(--gap)] xl:grid-cols-2">
            {!rows.length && <Card><Empty icon="plugins">No plugins installed.</Empty></Card>}
            {rows.map((row) => (
              <PluginRow
                key={`${pluginId(row)}-${row.dashboard_manifest?.name || ""}`}
                row={row}
                busy={busy}
                run={applyHub}
              />
            ))}
          </div>

          {!!data.orphan_dashboard_plugins?.length && (
            <Card title="Dashboard Only">
              <div className="flex flex-wrap gap-2">
                {data.orphan_dashboard_plugins.map((manifest) => {
                  const path = manifest.route?.path || manifest.tab?.override || manifest.tab?.path || "";
                  return (
                    <Badge key={manifest.name} tone="info">
                      {path && !manifest.route?.hidden && !manifest.tab?.hidden ? (
                        <Link to={path} className="inline-flex items-center gap-1">
                          {manifest.label || manifest.name} <Icon name="external" size={11} />
                        </Link>
                      ) : manifest.label || manifest.name}
                    </Badge>
                  );
                })}
              </div>
            </Card>
          )}

          {!!data.loaded?.length && (
            <Card title="Loaded Files">
              <div className="flex flex-wrap gap-1.5">
                {data.loaded.map((file) => <Badge key={file} tone="neutral">{file}</Badge>)}
              </div>
            </Card>
          )}
        </div>
      )}

      <PluginSlot name="plugins:bottom" className="mt-[var(--gap)]" />
    </>
  );
}

function PluginRow({
  row,
  busy,
  run,
}: {
  row: DashboardPluginHubRow;
  busy: string;
  run: (action: () => Promise<DashboardPluginsHub>, message: string) => Promise<void>;
}) {
  const id = pluginId(row);
  const status = rowStatus(row);
  const route = openRoute(row);
  const manifest = row.dashboard_manifest;
  const mount = mountInfo(row);
  const canToggle = status !== "dashboard";
  const contrib = contributions(row);
  const title = row.key && row.key !== row.name ? row.key : row.name;
  const load = loadLabel(row);
  const drift = driftCount(row);
  const apiRequests = Number(mount?.request_count || 0);
  const apiErrors = Number(mount?.error_count || 0);
  const apiMountErrors = Number(mount?.mount_error_count || 0);
  const apiMountDuration = durationLabel(mount?.mount_duration_ms);
  const lastApiErrorPath = mount?.last_error_path || "";
  const lastApiErrorType = mount?.last_error_type || "";
  const lastApiError = mount?.last_error || "";

  function toggleRuntime() {
    void run(
      () => status === "enabled" || status === "loaded" ? pluginsApi.disable(id) : pluginsApi.enable(id),
      status === "enabled" || status === "loaded" ? "Disabled" : "Enabled",
    );
  }

  function updatePlugin() {
    void run(() => pluginsApi.update(id), "Updated");
  }

  function removePlugin() {
    if (!window.confirm(`Remove ${title}?`)) return;
    void run(() => pluginsApi.remove(id), "Removed");
  }

  function toggleVisibility() {
    const target = manifest?.name || id;
    void run(() => pluginsApi.setVisibility(target, !row.user_hidden), row.user_hidden ? "Shown" : "Hidden");
  }

  return (
    <Card pad={false} className={busy ? "opacity-70" : ""}>
      <div className="border-b border-border p-[var(--pad)]">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate font-mono text-base font-semibold text-text">{title}</div>
            {row.key && row.key !== row.name && <div className="truncate text-[11px] text-faint">{row.name}</div>}
            {row.description && <div className="mt-1 line-clamp-2 text-xs text-faint">{row.description}</div>}
          </div>
          <Badge tone={statusTone(status)} status={status}>{status}</Badge>
        </div>
      </div>

      <div className="space-y-3 p-[var(--pad)]">
        <div className="flex flex-wrap gap-1.5">
          {row.version && <Badge tone="neutral">v{row.version}</Badge>}
          {row.kind && <Badge tone="info">{row.kind}</Badge>}
          {row.source && <Badge tone="neutral">{row.source}</Badge>}
          {row.category && <Badge tone="neutral">{row.category}</Badge>}
          {load && <Badge tone={row.load_status === "error" ? "danger" : "neutral"}>load {load}</Badge>}
          {!!drift && <Badge tone="warning">drift {drift}</Badge>}
          {row.user_hidden && <Badge tone="warning">hidden</Badge>}
          {row.auth_required && <Badge tone="danger">auth required</Badge>}
          {manifest?.slots?.map((slot) => <Badge key={slot} tone="neutral">{slot}</Badge>)}
        </div>

        {!!contrib.length && (
          <div className="flex flex-wrap gap-1.5">
            {contrib.map((item) => (
              <Badge key={`${item.tone}-${item.key}`} tone={item.tone}>
                {item.icon && <Icon name={item.icon} size={11} />} {item.key}
              </Badge>
            ))}
          </div>
        )}

        {mount && (mount.api || mount.status !== "skipped") && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-faint">
            <Badge tone={mount.mounted ? "success" : mount.status === "error" ? "danger" : "warning"}>
              api {mount.status || "unknown"}
            </Badge>
            {apiMountDuration && <Badge tone="neutral">mount {apiMountDuration}</Badge>}
            {apiRequests > 0 && <Badge tone="info">req {apiRequests}</Badge>}
            {apiErrors > 0 && <Badge tone="danger">err {apiErrors}</Badge>}
            {apiMountErrors > 0 && <Badge tone="danger">mount err {apiMountErrors}</Badge>}
            {!!mount.routes?.length && <span className="truncate font-mono">{mount.routes.join(", ")}</span>}
            {mount.error && <span className="text-danger">{mount.error}</span>}
            {(lastApiErrorPath || lastApiError) && (
              <span className="min-w-0 truncate text-danger">
                {lastApiErrorType && <span className="font-mono">{lastApiErrorType}</span>}
                {lastApiErrorPath && <span className="font-mono"> {lastApiErrorPath}</span>}
                {lastApiError && <span> {lastApiError}</span>}
              </span>
            )}
          </div>
        )}

        {row.auth_required && row.auth_command && (
          <div className="rounded-[var(--radius)] border border-danger/30 bg-danger/10 px-3 py-2 font-mono text-xs text-danger">
            {row.auth_command}
          </div>
        )}

        {row.load_error && (
          <div className="rounded-[var(--radius)] border border-danger/30 bg-danger/10 px-3 py-2 font-mono text-xs text-danger">
            {row.load_error}
          </div>
        )}

        {row.path && <div className="truncate font-mono text-[11px] text-faint">{row.path}</div>}

        <div className="flex flex-wrap gap-2">
          {canToggle && (
            <Button sm variant={status === "enabled" || status === "loaded" ? "danger" : "primary"} disabled={!!busy} onClick={toggleRuntime}>
              {status === "enabled" || status === "loaded" ? "Disable" : "Enable"}
            </Button>
          )}
          {route && (
            <Button sm variant="ghost" icon="external" onClick={() => { window.location.hash = route; }}>
              Open
            </Button>
          )}
          {row.has_dashboard_manifest && (
            <Button sm variant="ghost" disabled={!!busy} onClick={toggleVisibility}>
              {row.user_hidden ? "Show" : "Hide"}
            </Button>
          )}
          {row.can_update_git && (
            <Button sm variant="ghost" disabled={!!busy} icon="refresh" onClick={updatePlugin}>Update</Button>
          )}
          {row.can_remove && (
            <Button sm variant="danger" disabled={!!busy} icon="trash" onClick={removePlugin} aria-label={`Remove ${title}`} />
          )}
        </div>

        {!row.has_dashboard_manifest && !manifest && (
          <div className="text-xs italic text-faint">No dashboard panel.</div>
        )}
      </div>
    </Card>
  );
}
