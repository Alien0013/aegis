import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { Badge, Button, Card, Empty, Field, PageHeader, useToast } from "../lib/ui";

export function PluginsPage() {
  const [data, setData] = useState<any>({ manifests: [] });
  const [source, setSource] = useState("");
  const [q, setQ] = useState("");
  const [validation, setValidation] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function load() {
    try { setData(await api("plugins")); }
    catch { setData({ manifests: [] }); }
  }
  useEffect(() => { load(); }, []);

  const plugins: any[] = data.manifests || data.plugins || (Array.isArray(data) ? data : []);
  const filtered = plugins.filter((p) => {
    const query = q.trim().toLowerCase();
    return !query || `${p.name} ${p.description || ""} ${p.path || ""}`.toLowerCase().includes(query);
  });
  const toolsCount = Array.isArray(data.tools) ? data.tools.length : Number(data.tools || 0);
  const channelsCount = Array.isArray(data.channels) ? data.channels.length : 0;
  const providersCount = Array.isArray(data.providers) ? data.providers.length : 0;

  async function act(action: string, extra: any = {}) {
    setBusy(true);
    try {
      const r = await post("plugins", { action, ...extra });
      if (r.ok === false) toast(r.error || "failed", "err");
      else toast(`${action} ok`, "ok");
      await load();
    } finally { setBusy(false); }
  }
  async function install(force = false) {
    if (!source.trim()) return;
    await act("install", { source: source.trim(), force });
    setSource("");
  }
  async function validate() {
    if (!source.trim()) return;
    setBusy(true);
    try {
      const r = await post("plugins/validate", { source: source.trim() });
      setValidation(r);
      toast(r.ok ? "Plugin source looks installable" : r.error || "not installable", r.ok ? "ok" : "err");
    } finally { setBusy(false); }
  }

  return (
    <>
      <PageHeader
        title="Plugins"
        sub={`${plugins.length} installed · ${toolsCount} tools · ${channelsCount} channels · ${providersCount} providers`}
        actions={<Button variant="ghost" onClick={() => act("reload")} disabled={busy} icon="refresh">Reload</Button>}
      />
      <div className="stack">
        <Card title="Install a plugin">
          <div className="grid c3" style={{ alignItems: "end" }}>
            <div style={{ gridColumn: "1 / 3" }}>
              <Field label="Source"><input value={source} onChange={(e) => setSource(e.target.value)} placeholder="local .py file or plugin directory" /></Field>
            </div>
            <span className="actions">
              <Button onClick={validate} disabled={busy} variant="ghost" icon="check">Validate</Button>
              <Button onClick={() => install(false)} disabled={busy} icon="plus">Install</Button>
              <Button onClick={() => install(true)} disabled={busy} variant="ghost" icon="refresh">Update</Button>
            </span>
          </div>
          {validation && <div className={`notice ${validation.ok ? "ok" : "warn"}`}>{validation.ok ? `${validation.kind} source ready` : validation.error}</div>}
        </Card>

        <div className="grid c3">
          <Card title="Loaded files" pad={false}>
            {!data.plugins?.length && <Empty small>No plugin files loaded.</Empty>}
            {(data.plugins || []).map((p: any) => <div className="row" key={p.path || p.name}><span className="mono">{p.entrypoint || p.path}</span><Badge status={p.enabled ? "ok" : "disabled"}>{p.enabled ? "loaded" : "off"}</Badge></div>)}
          </Card>
          <Card title="Registered capabilities">
            <div className="pill-list">
              {(data.tool_names || []).slice(0, 24).map((t: string) => <span className="pill mono" key={t}>{t}</span>)}
              {(data.channels || []).map((c: string) => <span className="pill" key={c}>{c}</span>)}
              {(data.providers || []).map((p: string) => <span className="pill" key={p}>{p}</span>)}
              {!data.tool_names?.length && !data.channels?.length && !data.providers?.length && <span className="mut">No plugin capabilities registered.</span>}
            </div>
          </Card>
          <Card title="Load errors" pad={false}>
            {!data.errors?.length && <Empty small>No load errors.</Empty>}
            {(data.errors || []).map((err: any, i: number) => <div className="row" key={i}><span className="mono">{err.file}</span><Badge status="error">{err.error}</Badge></div>)}
          </Card>
        </div>

        <Card title="Installed" actions={<input className="search compact" placeholder="Search plugins" value={q} onChange={(e) => setQ(e.target.value)} />} pad={false}>
          {!filtered.length && <Empty small>No plugins match.</Empty>}
          <div style={{ padding: filtered.length ? "2px 14px 6px" : 0 }}>
            {filtered.map((p, i) => {
              const on = p.enabled !== false;
              return (
                <div className="row" key={p.name || i}>
                  <span style={{ minWidth: 0 }}>
                    <b>{p.name}</b> {p.version && <span className="pill">v{p.version}</span>} <Badge status={on ? "enabled" : "disabled"}>{on ? "on" : "off"}</Badge>
                    {p.description && <span className="mut"> — {String(p.description).slice(0, 80)}</span>}
                    <div className="mut mono">{p.path || p.entrypoint || ""}</div>
                  </span>
                  <span className="actions">
                    <Button variant="ghost" sm onClick={() => act(on ? "disable" : "enable", { name: p.name })}>{on ? "Disable" : "Enable"}</Button>
                    <Button variant="danger" sm onClick={() => act("remove", { name: p.name })}>Remove</Button>
                  </span>
                </div>
              );
            })}
          </div>
        </Card>
      </div>
    </>
  );
}
