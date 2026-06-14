import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { compact } from "../lib/format";
import { Icon } from "../lib/icons";
import { Badge, Button, Card, Empty, Loading, PageHeader, Stat, useToast } from "../lib/ui";
import { Dialog } from "../lib/components/Dialog";

type Confirm = { action: string; body?: any; title: string; word: string; desc: string };

export function SystemPage() {
  const [info, setInfo] = useState<any>();
  const [stats, setStats] = useState<any>();
  const [ops, setOps] = useState<any>();
  const [busy, setBusy] = useState("");
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [typed, setTyped] = useState("");
  const [console_, setConsole] = useState<{ title: string; text: string } | null>(null);
  const toast = useToast();

  async function load() {
    try {
      const [i, o, s] = await Promise.all([api("system"), api("ops"), api("system/stats").catch(() => null)]);
      setInfo(i); setOps(o); setStats(s);
    } catch (e) { setInfo({ __err: String(e) }); }
  }
  useEffect(() => { load(); }, []);

  async function run(action: string, body: any = {}, ok = "Done") {
    setBusy(action + (body.op || ""));
    try {
      const r: any = await post("ops", { action, ...body });
      if (action === "doctor" || action === "security_audit") {
        setConsole({ title: action === "doctor" ? "Doctor" : "Security audit", text: r.output || r.error || "(no output)" });
      } else if (r && r.ok === false) toast(r.error || r.message || "Failed", "err");
      else if (action === "update_check") toast(r.update_available ? "Update available" : "Up to date", "ok");
      else if (action === "backup") toast("Backup created", "ok");
      else if (action === "curator_run") toast("Curator pass complete", "ok");
      else toast(ok, "ok");
      await load();
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  if (info === undefined) return <><PageHeader title="System" /><Loading /></>;
  if (info.__err) return <><PageHeader title="System" /><Card><Empty>Couldn't load: {info.__err}</Empty></Card></>;

  const cur = ops?.curator || {};
  const svc = ops?.services || {};
  const upd = ops?.update;
  const ico = (n: string) => <span className="ic"><Icon n={n} /></span>;

  return (
    <>
      <PageHeader title="System" sub="install · storage · operations"
        actions={<Button variant="ghost" icon="refresh" onClick={load}>Refresh</Button>} />

      <div className="grid c4">
        <Stat label="Version" value={info.version} />
        <Stat label="Python" value={info.python} sub={info.platform} />
        <Stat label="Disk free" value={`${info.disk_free_gb} GB`} sub={`of ${info.disk_total_gb} GB`} />
        <Stat label="Curator" value={cur.enabled ? "on" : "paused"} sub={cur.last_run_at ? `last ${compact(cur.last_run_at, 16)}` : "never run"} />
      </div>

      {stats && (
        <>
          <div className="section-title">Host</div>
          <div className="grid c4">
            <Stat label="OS" value={stats.arch} sub={stats.os} />
            <Stat label="CPU" value={`${stats.cpu_count} cores`} sub={stats.load_avg ? `load ${stats.load_avg.join(" · ")}` : stats.host} />
            <Stat label="Memory" value={stats.mem_total_gb ? `${stats.mem_used_gb}/${stats.mem_total_gb} GB` : "—"} sub={stats.mem_percent != null ? `${stats.mem_percent}% used` : ""} />
            <Stat label="Uptime" value={stats.uptime || "—"} sub={`disk ${stats.disk_percent}% · ${stats.host}`} />
          </div>
        </>
      )}

      <div className="section-title">Diagnostics</div>
      <div className="ops-grid">
        <div className="ops-card">
          <div className="ops-h">{ico("check")}<div><b>Doctor</b><div className="meta">install &amp; provider health</div></div></div>
          <div className="ops-desc">Run the full health check — providers, auth, tools, workspace, daemon.</div>
          <div className="ops-actions">
            <Button sm icon="check" disabled={busy === "doctor"} onClick={() => run("doctor")}>Run doctor</Button>
          </div>
        </div>
        <div className="ops-card">
          <div className="ops-h">{ico("system")}<div><b>Security audit</b><div className="meta">deps · MCP · plugins · skills</div></div></div>
          <div className="ops-desc">Scan dependencies, MCP commands, plugins, and skills for risks.</div>
          <div className="ops-actions">
            <Button sm variant="ghost" icon="system" disabled={busy === "security_audit"} onClick={() => run("security_audit")}>Run audit</Button>
          </div>
        </div>
      </div>

      <div className="section-title">Operations</div>
      <div className="ops-grid">
        {/* Curator */}
        <div className="ops-card">
          <div className="ops-h">{ico("cron")}<div><b>Memory curator</b><div className="meta">consolidate · prune · archive</div></div></div>
          <div className="ops-desc">Run a full curation pass now, or pause the scheduled run.</div>
          <div className="ops-actions">
            <span className={"ops-state" + (cur.enabled ? "" : " off")}><span className="dot" />{cur.enabled ? "scheduled" : "paused"}</span>
            <Button sm icon="play" disabled={busy === "curator_run"} onClick={() => run("curator_run", {}, "Curator ran")}>Run now</Button>
            {cur.enabled
              ? <Button sm variant="ghost" disabled={busy === "curator_pause"} onClick={() => run("curator_pause", {}, "Curator paused")}>Pause</Button>
              : <Button sm variant="ghost" disabled={busy === "curator_resume"} onClick={() => run("curator_resume", {}, "Curator resumed")}>Resume</Button>}
          </div>
        </div>

        {/* Backup */}
        <div className="ops-card">
          <div className="ops-h">{ico("bolt")}<div><b>Backup</b><div className="meta">{(info.checkpoints || []).length} checkpoints</div></div></div>
          <div className="ops-desc">Snapshot the entire AEGIS home (config, memory, sessions) into a zip.</div>
          <div className="ops-actions">
            <Button sm icon="bolt" disabled={busy === "backup"} onClick={() => run("backup")}>Create backup</Button>
          </div>
        </div>

        {/* Updates */}
        <div className="ops-card">
          <div className="ops-h">{ico("refresh")}<div><b>Updates</b><div className="meta">v{info.version}{upd?.branch ? ` · ${upd.branch}` : ""}</div></div></div>
          <div className="ops-desc">
            {upd
              ? (upd.install === "git"
                  ? (upd.update_available ? `${upd.behind} commit(s) behind — ${upd.hint}` : "Up to date with fetched upstream.")
                  : upd.hint)
              : "Check whether a newer version is available."}
          </div>
          <div className="ops-actions">
            {upd?.update_available && <Badge status="warn">update available</Badge>}
            <Button sm variant="ghost" icon="refresh" disabled={busy === "update_check"} onClick={() => run("update_check")}>Check now</Button>
          </div>
        </div>

        {/* Services (systemd only) */}
        {svc.systemd && (
          <div className="ops-card">
            <div className="ops-h">{ico("system")}<div><b>Services</b><div className="meta">systemd user units</div></div></div>
            <div className="ops-desc">Gateway: <span className="mono">{svc.gateway || "—"}</span> · Cron: <span className="mono">{svc.cron || "—"}</span></div>
            <div className="ops-actions">
              <span className="faint" style={{ fontSize: 11 }}>Gateway</span>
              {["start", "stop", "restart"].map((op) => (
                <Button key={op} sm variant="ghost" disabled={busy === "gateway" + op} onClick={() => run("gateway", { op }, `Gateway ${op}`)}>{op}</Button>
              ))}
            </div>
          </div>
        )}

        {/* Memory reset (danger) */}
        <div className="ops-card danger">
          <div className="ops-h">{ico("memory")}<div><b>Reset memory</b><div className="meta">{ops?.memory?.memory?.entries ?? 0} entries · MEMORY.md</div></div></div>
          <div className="ops-desc">Clear everything the agent remembers. A timestamped backup is written first.</div>
          <div className="ops-actions">
            <Button sm variant="danger" icon="trash"
              onClick={() => { setTyped(""); setConfirm({ action: "memory_reset", title: "Reset agent memory", word: "RESET", desc: "This clears MEMORY.md. A backup is saved alongside it. Type RESET to confirm." }); }}>
              Reset memory
            </Button>
          </div>
        </div>

        {/* User reset (danger) */}
        <div className="ops-card danger">
          <div className="ops-h">{ico("agents")}<div><b>Reset user profile</b><div className="meta">{ops?.memory?.user?.entries ?? 0} entries · USER.md</div></div></div>
          <div className="ops-desc">Clear the learned profile about you. A timestamped backup is written first.</div>
          <div className="ops-actions">
            <Button sm variant="danger" icon="trash"
              onClick={() => { setTyped(""); setConfirm({ action: "user_reset", title: "Reset user profile", word: "RESET", desc: "This clears USER.md. A backup is saved alongside it. Type RESET to confirm." }); }}>
              Reset profile
            </Button>
          </div>
        </div>
      </div>

      {(info.checkpoints || []).length > 0 && (
        <>
          <div className="section-title">Recent checkpoints</div>
          <Card pad={false}>
            <div style={{ padding: "4px 14px 8px" }}>
              {info.checkpoints.map((c: any) => (
                <div className="row" key={c.id}>
                  <span className="mono">{compact(c.label || c.id, 50)}</span>
                  <span className="pill">{compact(c.at, 16)}</span>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}

      <Dialog open={!!confirm} onOpenChange={(v) => !v && setConfirm(null)} title={confirm?.title}
        footer={
          <div className="row-flex" style={{ justifyContent: "flex-end" }}>
            <Button variant="ghost" sm onClick={() => setConfirm(null)}>Cancel</Button>
            <Button variant="danger" sm disabled={typed !== confirm?.word}
              onClick={async () => { const c = confirm!; setConfirm(null); await run(c.action, c.body || {}, "Reset complete"); }}>
              Confirm reset
            </Button>
          </div>
        }>
        <p className="mut" style={{ marginTop: 0, fontSize: 13 }}>{confirm?.desc}</p>
        <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={confirm?.word} />
      </Dialog>

      <Dialog open={!!console_} onOpenChange={(v) => !v && setConsole(null)} title={console_?.title}
        footer={<div className="row-flex" style={{ justifyContent: "flex-end" }}>
          <Button sm variant="ghost" onClick={() => { navigator.clipboard?.writeText(console_?.text || ""); toast("Copied", "ok"); }}>Copy</Button>
          <Button sm onClick={() => setConsole(null)}>Close</Button>
        </div>}>
        <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12, maxHeight: "56vh", overflow: "auto", lineHeight: 1.5 }}>{console_?.text}</pre>
      </Dialog>
    </>
  );
}
