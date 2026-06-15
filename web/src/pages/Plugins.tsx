import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Loading, PageHeader, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface Manifest { name: string; enabled?: boolean; description?: string; version?: string; path?: string; entrypoint?: string }
interface PluginError { file?: string; path?: string; error: string }
interface PluginsPayload {
  loaded?: string[];
  errors?: PluginError[];
  tools?: number | string[];
  tool_names?: string[];
  channels?: string[];
  providers?: string[];
  manifests?: Manifest[];
  plugins?: Manifest[];
}

export function Plugins() {
  const { data, loading, error, reload } = useApi<PluginsPayload>("plugins");

  async function reloadPlugins() {
    try {
      await post("plugins/reload", {});
      toast("Reloaded");
      reload();
    } catch {
      try {
        const r = await post<{ ok?: boolean; error?: string }>("plugins", { action: "reload" });
        if (r.error) toast(r.error, "err");
        else { toast("Reloaded"); reload(); }
      } catch (e) { toast(String(e), "err"); }
    }
  }

  async function setEnabled(name: string, enabled: boolean) {
    const action = enabled ? "enable" : "disable";
    try {
      await post(`plugins/${encodeURIComponent(name)}/${action}`, {});
      toast(enabled ? "Enabled" : "Disabled");
      reload();
    } catch {
      try {
        const r = await post<{ ok?: boolean; error?: string }>("plugins", { action, name });
        if (r.error || r.ok === false) toast(r.error || "Plugin update failed", "err");
        else { toast(enabled ? "Enabled" : "Disabled"); reload(); }
      } catch (e) { toast(String(e), "err"); }
    }
  }

  const manifests = data?.plugins || data?.manifests || [];
  const errors = data?.errors || [];
  const toolNames = Array.isArray(data?.tools) ? data.tools : (data?.tool_names || []);
  const toolCount = Array.isArray(data?.tools) ? data.tools.length : (data?.tools || toolNames.length || 0);
  const loaded = data?.loaded || [];

  return (
    <>
      <PageHeader title="Plugins"
        sub={data ? `${manifests.length} package${manifests.length === 1 ? "" : "s"} · ${toolCount} tools` : "Drop-in extensions"}
        actions={<Button variant="ghost" icon="refresh" onClick={reloadPlugins}>Reload</Button>} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          {!!errors.length && (
            <Card title="Errors">
              {errors.map((e, i) => (
                <div key={i} className="text-xs text-danger"><span className="font-mono">{e.path || e.file || "plugin"}</span>: {e.error}</div>
              ))}
            </Card>
          )}
          <div className="grid gap-[var(--gap)] md:grid-cols-2 xl:grid-cols-3">
            {!manifests.length && !loaded.length && <Card><Empty icon="plugins">No plugins installed.</Empty></Card>}
            {manifests.map((m) => (
              <Card key={m.name} pad={false}>
                <div className="border-b border-border p-[var(--pad)]">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate font-mono text-base font-semibold text-text">{m.name}</div>
                      {m.description && <div className="line-clamp-2 text-xs text-faint">{m.description}</div>}
                    </div>
                    <Badge status={m.enabled ? "ok" : undefined} tone={m.enabled ? undefined : "neutral"}>{m.enabled ? "enabled" : "disabled"}</Badge>
                  </div>
                </div>
                <div className="space-y-3 p-[var(--pad)]">
                  <div className="flex flex-wrap gap-1.5">
                    {m.version && <Badge tone="neutral">v{m.version}</Badge>}
                    {m.entrypoint && <Badge tone="neutral">{m.entrypoint.split("/").pop()}</Badge>}
                    <Badge tone="info">extension</Badge>
                  </div>
                  {m.path && <div className="truncate font-mono text-[11px] text-faint">{m.path}</div>}
                  <Button sm variant={m.enabled ? "danger" : "primary"} onClick={() => setEnabled(m.name, !m.enabled)}>
                    {m.enabled ? "Disable" : "Enable"}
                  </Button>
                </div>
              </Card>
            ))}
          </div>
          {!!loaded.length && (
            <Card title="Loaded Files">
              <div className="flex flex-wrap gap-1.5">
                {loaded.map((file) => <Badge key={file} tone="neutral">{file}</Badge>)}
              </div>
            </Card>
          )}
          {!!(toolNames.length || data.channels?.length || data.providers?.length) && (
            <Card title="Contributed">
              <div className="flex flex-wrap gap-1.5">
                {toolNames.map((t) => <Badge key={t} tone="info"><Icon name="tools" size={11} /> {t}</Badge>)}
                {(data.channels || []).map((c) => <Badge key={c} tone="primary">{c}</Badge>)}
                {(data.providers || []).map((p) => <Badge key={p} tone="success">{p}</Badge>)}
              </div>
            </Card>
          )}
        </div>
      )}
    </>
  );
}
