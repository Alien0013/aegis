import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { Badge, Button, Card, Empty, Field, PageHeader, useToast } from "../lib/ui";

export function CronPage() {
  const [jobs, setJobs] = useState<any[]>([]);
  const [schedule, setSchedule] = useState("@daily");
  const [prompt, setPrompt] = useState("");
  const [channel, setChannel] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function load() {
    try { const d = await api("cron"); setJobs(d.jobs || d.cron || (Array.isArray(d) ? d : [])); }
    catch { setJobs([]); }
  }
  useEffect(() => { load(); }, []);

  async function add() {
    if (!schedule.trim() || !prompt.trim()) return;
    setBusy(true);
    try { await post("cron", { action: "add", schedule, prompt, channel }); setPrompt(""); toast("Job scheduled", "ok"); await load(); }
    catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }
  async function remove(id: string) { await post("cron", { action: "remove", id }); toast("Job deleted"); await load(); }
  async function toggle(id: string, enabled: boolean) { await post("cron", { action: "toggle", id, enabled: !enabled }); await load(); }
  async function runNow(id: string) { await post("cron", { action: "run", id }); toast("Run triggered", "ok"); }

  return (
    <>
      <PageHeader title="Cron" sub={`${jobs.length} job${jobs.length === 1 ? "" : "s"}`} />
      <div className="stack">
        <Card title="Schedule a task">
          <div className="grid c3" style={{ alignItems: "end" }}>
            <Field label="Schedule"><input value={schedule} onChange={(e) => setSchedule(e.target.value)} placeholder="@daily · every 2h · 0 9 * * 1" /></Field>
            <Field label="Deliver to (optional)"><input value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="telegram:12345" /></Field>
            <Button onClick={add} disabled={busy} icon="plus">Add job</Button>
          </div>
          <Field label="Prompt"><textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="summarize today's commits and DM me" /></Field>
        </Card>
        <Card title="Scheduled jobs" pad={false}>
          {!jobs.length && <Empty small>No scheduled jobs yet.</Empty>}
          <div style={{ padding: jobs.length ? "2px 14px 6px" : 0 }}>
            {jobs.map((j) => (
              <div className="row" key={j.id}>
                <span style={{ minWidth: 0 }}>
                  <b className="mono">{j.schedule}</b> <span className="mut">— {(j.prompt || "").slice(0, 64)}</span>
                  {j.channel && <span className="pill" style={{ marginLeft: 8 }}>{j.channel}</span>}
                  {j.enabled === false && <Badge status="disabled" >paused</Badge>}
                </span>
                <span className="actions">
                  <Button variant="ghost" sm onClick={() => runNow(j.id)}>Run</Button>
                  <Button variant="ghost" sm onClick={() => toggle(j.id, j.enabled !== false)}>{j.enabled === false ? "Enable" : "Disable"}</Button>
                  <Button variant="danger" sm onClick={() => remove(j.id)}>Delete</Button>
                </span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
