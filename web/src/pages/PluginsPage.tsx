import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { Badge, Button, Card, Empty, Field, PageHeader, useToast } from "../lib/ui";

export function PluginsPage() {
  const [plugins, setPlugins] = useState<any[]>([]);
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function load() {
    try { const d = await api("plugins"); setPlugins(d.manifests || d.plugins || (Array.isArray(d) ? d : [])); }
    catch { setPlugins([]); }
  }
  useEffect(() => { load(); }, []);

  async function act(action: string, extra: any = {}) {
    setBusy(true);
    try {
      const r = await post("plugins", { action, ...extra });
      if (r.ok === false) toast(r.error || "failed", "err");
      else toast(`${action} ok`, "ok");
      await load();
    } finally { setBusy(false); }
  }
  async function install() { if (!source.trim()) return; await act("install", { source: source.trim() }); setSource(""); }

  return (
    <>
      <PageHeader title="Plugins" sub={`${plugins.length} installed`} />
      <div className="stack">
        <Card title="Install a plugin">
          <div className="grid c3" style={{ alignItems: "end" }}>
            <div style={{ gridColumn: "1 / 3" }}><Field label="Source"><input value={source} onChange={(e) => setSource(e.target.value)} placeholder="github URL, npm name, or local path" /></Field></div>
            <Button onClick={install} disabled={busy} icon="plus">Install</Button>
          </div>
        </Card>
        <Card title="Installed" pad={false}>
          {!plugins.length && <Empty small>No plugins installed.</Empty>}
          <div style={{ padding: plugins.length ? "2px 14px 6px" : 0 }}>
            {plugins.map((p, i) => {
              const on = p.enabled !== false;
              return (
                <div className="row" key={p.name || i}>
                  <span style={{ minWidth: 0 }}>
                    <b>{p.name}</b> {p.version && <span className="pill">v{p.version}</span>} <Badge status={on ? "enabled" : "disabled"}>{on ? "on" : "off"}</Badge>
                    {p.description && <span className="mut"> — {String(p.description).slice(0, 60)}</span>}
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
