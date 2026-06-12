import { useEffect, useState } from "react";
import { api, post } from "../lib/api";

export function CronPage() {
  const [jobs, setJobs] = useState<any[]>([]);
  const [schedule, setSchedule] = useState("@daily");
  const [prompt, setPrompt] = useState("");
  const [channel, setChannel] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try { const d = await api("cron"); setJobs(d.jobs || d.cron || (Array.isArray(d) ? d : [])); }
    catch { setJobs([]); }
  }
  useEffect(() => { load(); }, []);

  async function add() {
    if (!schedule.trim() || !prompt.trim()) return;
    setBusy(true);
    try { await post("cron", { action: "add", schedule, prompt, channel }); setPrompt(""); await load(); }
    finally { setBusy(false); }
  }
  async function remove(id: string) { await post("cron", { action: "remove", id }); await load(); }
  async function toggle(id: string, enabled: boolean) { await post("cron", { action: "toggle", id, enabled: !enabled }); await load(); }
  async function runNow(id: string) { await post("cron", { action: "run", id }); }

  return (
    <>
      <div className="head"><h1>Cron</h1><span className="crumb">{jobs.length} job{jobs.length === 1 ? "" : "s"}</span></div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Schedule a task</h3>
        <div className="grid c3" style={{ gap: 10, alignItems: "end" }}>
          <label>Schedule<input value={schedule} onChange={(e) => setSchedule(e.target.value)} placeholder="@daily, every 2h, 0 9 * * 1" /></label>
          <label style={{ gridColumn: "span 1" }}>Deliver to<input value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="telegram:12345 (optional)" /></label>
          <button className="btn" onClick={add} disabled={busy}>Add job</button>
        </div>
        <label style={{ display: "block", marginTop: 10 }}>Prompt
          <textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="summarize today's commits and DM me" /></label>
      </div>
      <div className="card">
        {!jobs.length && <div className="empty">No scheduled jobs yet.</div>}
        {jobs.map((j) => (
          <div className="row" key={j.id}>
            <span><b>{j.schedule}</b> <span className="mut">— {(j.prompt || "").slice(0, 60)}</span>{j.channel && <span className="pill" style={{ marginLeft: 8 }}>{j.channel}</span>}</span>
            <span style={{ display: "flex", gap: 8 }}>
              <button className="btn ghost" onClick={() => runNow(j.id)}>Run</button>
              <button className="btn ghost" onClick={() => toggle(j.id, j.enabled !== false)}>{j.enabled === false ? "Enable" : "Disable"}</button>
              <button className="btn ghost" onClick={() => remove(j.id)}>Delete</button>
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
