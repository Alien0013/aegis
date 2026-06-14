import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Loading, PageHeader, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface Manifest { name: string; enabled?: boolean; description?: string; version?: string }
interface PluginsPayload {
  loaded?: string[]; errors?: { file: string; error: string }[];
  tools?: number; tool_names?: string[]; channels?: string[]; providers?: string[];
  manifests?: Manifest[];
}

export function Plugins() {
  const { data, loading, error, reload } = useApi<PluginsPayload>("plugins");

  async function act(body: Record<string, unknown>) {
    try { const r = await post<{ ok?: boolean; error?: string }>("plugins", body); if (r.error) toast(r.error, "err"); else { toast("Done"); reload(); } }
    catch (e) { toast(String(e), "err"); }
  }

  const manifests = data?.manifests || [];
  const errors = data?.errors || [];

  return (
    <>
      <PageHeader title="Plugins"
        sub={data ? `${manifests.length} package${manifests.length === 1 ? "" : "s"} · ${data.tools || 0} tools` : "Drop-in extensions"}
        actions={<Button variant="ghost" icon="refresh" onClick={() => act({ action: "reload" })}>Reload</Button>} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          {!!errors.length && (
            <Card title="Errors">
              {errors.map((e, i) => (
                <div key={i} className="text-xs text-danger"><span className="font-mono">{e.file}</span>: {e.error}</div>
              ))}
            </Card>
          )}
          <Card title="Packages" pad={false}>
            {!manifests.length && <Empty icon="plugins">No manifest plugins installed.</Empty>}
            {manifests.map((m) => (
              <div key={m.name} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm text-text">{m.name}</span>
                    {m.version && <Badge tone="neutral">v{m.version}</Badge>}
                    <Badge status={m.enabled ? "ok" : undefined} tone={m.enabled ? undefined : "neutral"}>{m.enabled ? "enabled" : "disabled"}</Badge>
                  </div>
                  {m.description && <div className="truncate text-xs text-faint">{m.description}</div>}
                </div>
                <Button sm variant="ghost" onClick={() => act({ action: m.enabled ? "disable" : "enable", name: m.name })}>
                  {m.enabled ? "Disable" : "Enable"}
                </Button>
              </div>
            ))}
          </Card>
          {!!(data.tool_names?.length || data.channels?.length || data.providers?.length) && (
            <Card title="Contributed">
              <div className="flex flex-wrap gap-1.5">
                {(data.tool_names || []).map((t) => <Badge key={t} tone="info"><Icon name="tools" size={11} /> {t}</Badge>)}
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
