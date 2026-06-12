import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { compact } from "../lib/format";
export function SystemPage() {
  const [s, setS] = useState<any>(undefined);
  const [msg, setMsg] = useState("");
  async function load() { try { setS(await api("system")); } catch (e) { setS({ __err: String(e) }); } }
  useEffect(() => { load(); }, []);
  async function backup() {
    setMsg("");
    try {
      const r = await post("system", { action: "backup" });
      setMsg(r.ok ? `Backup created: ${r.path}` : r.error || "Backup failed");
      await load();
    } catch (e) {
      setMsg(String(e));
    }
  }
  const entries = s && !s.__err ? Object.entries(s) : [];
  return (
    <>
      <div className="head">
        <div><h1>System</h1><span className="crumb">install, storage, checkpoints</span></div>
        <span className="actions">
          <button className="btn ghost" onClick={load}>Refresh</button>
          <button className="btn" onClick={backup}>Backup</button>
        </span>
      </div>
      {msg && <div className="banner" style={{ marginBottom: 14 }}>{msg}</div>}
      <div className="panel">
        {s === undefined ? <div className="empty"><span className="spin" /> loading...</div>
          : s.__err ? <div className="mut">Couldn't load: {s.__err}</div>
          : entries.map(([k, v]) => (
            <div className="row" key={k}><span style={{ fontWeight: 600 }}>{k}</span>
              <span className="mut">{compact(v, 160)}</span></div>
          ))}
      </div>
    </>
  );
}
