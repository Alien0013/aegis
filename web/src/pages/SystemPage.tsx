import { useEffect, useState } from "react";
import { api } from "../lib/api";
export function SystemPage() {
  const [s, setS] = useState<any>(undefined);
  useEffect(() => { api("system").then(setS).catch((e) => setS({ __err: String(e) })); }, []);
  const entries = s && !s.__err ? Object.entries(s) : [];
  return (
    <>
      <div className="head"><h1>System</h1></div>
      <div className="card">
        {s === undefined ? <div className="empty"><span className="spin" /> loading…</div>
          : s.__err ? <div className="mut">Couldn't load: {s.__err}</div>
          : entries.map(([k, v]) => (
            <div className="row" key={k}><span style={{ fontWeight: 600 }}>{k}</span>
              <span className="mut">{typeof v === "object" ? JSON.stringify(v) : String(v)}</span></div>
          ))}
      </div>
    </>
  );
}
