import { useEffect, useState } from "react";
import { api, post } from "../lib/api";

export function PluginsPage() {
  const [plugins, setPlugins] = useState<any[]>([]);
  const [source, setSource] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try { const d = await api("plugins"); setPlugins(d.manifests || d.plugins || (Array.isArray(d) ? d : [])); }
    catch { setPlugins([]); }
  }
  useEffect(() => { load(); }, []);

  async function act(action: string, extra: any = {}) {
    setBusy(true);
    try {
      const r = await post("plugins", { action, ...extra });
      setMsg(r.ok === false ? "✗ " + (r.error || "failed") : `✓ ${action} ok`);
      await load();
    } finally { setBusy(false); }
  }
  async function install() {
    if (!source.trim()) return;
    await act("install", { source: source.trim() });
    setSource("");
  }

  return (
    <>
      <div className="head"><h1>Plugins</h1><span className="crumb">{plugins.length} installed</span></div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Install a plugin</h3>
        <div className="grid c3" style={{ gap: 10, alignItems: "end" }}>
          <label style={{ gridColumn: "span 2" }}>Source<input value={source} onChange={(e) => setSource(e.target.value)} placeholder="github URL, npm name, or local path" /></label>
          <button className="btn" onClick={install} disabled={busy}>Install</button>
        </div>
        {msg && <div className="mut" style={{ marginTop: 8 }}>{msg}</div>}
      </div>
      <div className="card">
        {!plugins.length && <div className="empty">No plugins installed.</div>}
        {plugins.map((p, i) => {
          const on = p.enabled !== false;
          return (
            <div className="row" key={p.name || i}>
              <span>
                <b>{p.name}</b> {p.version && <span className="pill" style={{ marginLeft: 6 }}>v{p.version}</span>}
                {p.description && <span className="mut"> — {String(p.description).slice(0, 60)}</span>}
              </span>
              <span style={{ display: "flex", gap: 8 }}>
                <button className="btn ghost" onClick={() => act(on ? "disable" : "enable", { name: p.name })}>{on ? "Disable" : "Enable"}</button>
                <button className="btn ghost" onClick={() => act("remove", { name: p.name })}>Remove</button>
              </span>
            </div>
          );
        })}
      </div>
    </>
  );
}
