import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { compact } from "../lib/format";
import { Button, Card, Empty, Loading, PageHeader, useToast } from "../lib/ui";

export function SystemPage() {
  const [s, setS] = useState<any>(undefined);
  const toast = useToast();
  async function load() { try { setS(await api("system")); } catch (e) { setS({ __err: String(e) }); } }
  useEffect(() => { load(); }, []);
  async function backup() {
    try {
      const r = await post("system", { action: "backup" });
      if (r.ok) { toast(`Backup created: ${r.path}`, "ok"); await load(); }
      else toast(r.error || "Backup failed", "err");
    } catch (e) { toast(String(e), "err"); }
  }
  const entries = s && !s.__err ? Object.entries(s) : [];
  return (
    <>
      <PageHeader title="System" sub="install · storage · checkpoints"
        actions={<>
          <Button variant="ghost" icon="refresh" onClick={load}>Refresh</Button>
          <Button icon="bolt" onClick={backup}>Backup</Button>
        </>} />
      <Card pad={false}>
        {s === undefined ? <Loading />
          : s.__err ? <Empty>Couldn't load: {s.__err}</Empty>
          : <div style={{ padding: "2px 14px 6px" }}>
              {entries.map(([k, v]) => (
                <div className="row" key={k}><span style={{ fontWeight: 600 }}>{k}</span><span className="mut mono">{compact(v, 160)}</span></div>
              ))}
            </div>}
      </Card>
    </>
  );
}
