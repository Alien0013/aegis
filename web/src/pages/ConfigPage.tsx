import { useEffect, useState } from "react";
import { api } from "../lib/api";
export function ConfigPage() {
  const [cfg, setCfg] = useState<any>(undefined);
  useEffect(() => { api("config").then(setCfg).catch((e) => setCfg({ __err: String(e) })); }, []);
  return (
    <>
      <div className="head"><h1>Config</h1><span className="crumb">secrets redacted</span></div>
      <div className="card">
        {cfg === undefined ? <div className="empty"><span className="spin" /> loading…</div>
          : cfg.__err ? <div className="mut">Couldn't load: {cfg.__err}</div>
          : <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12.5, lineHeight: 1.6 }}>{JSON.stringify(cfg, null, 2)}</pre>}
      </div>
    </>
  );
}
