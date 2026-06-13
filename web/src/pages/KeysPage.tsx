import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { Badge } from "../lib/ui";

export function KeysPage() {
  const [keys, setKeys] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [val, setVal] = useState("");
  const [msg, setMsg] = useState("");
  async function load() { try { const d = await api("keys"); setKeys(d.keys || (Array.isArray(d) ? d : Object.entries(d).map(([k, v]) => ({ key: k, set: !!v })))); } catch { setKeys([]); } }
  useEffect(() => { load(); }, []);
  async function save() {
    if (!name.trim()) return;
    setMsg("");
    try { await post("keys", { key: name.trim(), value: val }); setName(""); setVal(""); setMsg("✓ saved to ~/.aegis/.env"); await load(); }
    catch (e) { setMsg("✗ " + String(e)); }
  }
  return (
    <>
      <div className="head"><h1>API Keys</h1><span className="crumb">stored in ~/.aegis/.env (0600)</span></div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Set a key</h3>
        <div className="grid c3" style={{ gap: 10, alignItems: "end" }}>
          <label>Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="ANTHROPIC_API_KEY" /></label>
          <label>Value<input type="password" value={val} onChange={(e) => setVal(e.target.value)} placeholder="sk-…" /></label>
          <button className="btn" onClick={save}>Save</button>
        </div>
        {msg && <div className="mut" style={{ marginTop: 8 }}>{msg}</div>}
      </div>
      <div className="card">
        <h3>Configured</h3>
        {!keys.length && <div className="empty">No keys set yet.</div>}
        {keys.map((k, i) => (
          <div className="row" key={i}><span className="mono">{k.key || k.name || String(k)}</span><Badge status={k.set === false ? "empty" : "set"}>{k.set === false ? "empty" : "set"}</Badge></div>
        ))}
      </div>
    </>
  );
}
