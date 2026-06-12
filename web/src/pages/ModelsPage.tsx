import { useEffect, useState } from "react";
import { api, post } from "../lib/api";

export function ModelsPage() {
  const [data, setData] = useState<any>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [msg, setMsg] = useState("");

  async function load() {
    const d = await api("models");
    setData(d);
    setProvider(d.provider || d.current_provider || "");
    setModel(d.model || d.current_model || d.default || "");
  }
  useEffect(() => { load().catch((e) => setMsg(String(e))); }, []);
  if (!data) return <><div className="head"><h1>Models</h1></div><div className="empty"><span className="spin" /> loading…</div></>;

  const list: any[] = data.models || data.available || (Array.isArray(data) ? data : []);
  const providers: string[] = data.providers || [...new Set(list.map((m: any) => m.provider).filter(Boolean))];

  async function apply() {
    setMsg("");
    try { const r = await post("models", { provider, model }); setMsg(r.error ? "✗ " + r.error : "✓ active model set"); await load(); }
    catch (e) { setMsg("✗ " + String(e)); }
  }

  return (
    <>
      <div className="head"><h1>Models</h1><span className="crumb">{data.provider} · {data.model}</span></div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Active model</h3>
        <div className="grid c3" style={{ gap: 10, alignItems: "end" }}>
          <label>Provider
            {providers.length
              ? <select value={provider} onChange={(e) => setProvider(e.target.value)}>{providers.map((p) => <option key={p}>{p}</option>)}</select>
              : <input value={provider} onChange={(e) => setProvider(e.target.value)} />}
          </label>
          <label>Model<input value={model} onChange={(e) => setModel(e.target.value)} placeholder="claude-sonnet-4-6" /></label>
          <button className="btn" onClick={apply}>Set active</button>
        </div>
        {msg && <div className="mut" style={{ marginTop: 8 }}>{msg}</div>}
      </div>
      <div className="card">
        <h3>Available</h3>
        {!list.length && <div className="empty">No model list from this provider.</div>}
        {list.slice(0, 60).map((m: any, i: number) => (
          <div className="row click" key={i} onClick={() => { setModel(m.id || m.name || m); if (m.provider) setProvider(m.provider); }}>
            <span>{m.id || m.name || String(m)}</span>
            <span className="mut">{m.provider || ""}{m.context ? ` · ${m.context}` : ""}</span>
          </div>
        ))}
      </div>
    </>
  );
}
