import { useEffect, useState } from "react";
import { api, post } from "../lib/api";

export function WebhooksPage() {
  const [hooks, setHooks] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const d = await api("webhooks");
      const raw = d.webhooks || d.hooks || d;
      setHooks(Array.isArray(raw) ? raw
        : Object.entries(raw || {}).map(([k, v]: any) => ({ name: k, prompt: typeof v === "string" ? v : v?.prompt })));
    } catch { setHooks([]); }
  }
  useEffect(() => { load(); }, []);

  async function add() {
    if (!name.trim() || !prompt.trim()) return;
    setBusy(true);
    try { await post("webhooks", { action: "add", name: name.trim(), prompt }); setName(""); setPrompt(""); await load(); }
    finally { setBusy(false); }
  }
  async function remove(n: string) { await post("webhooks", { action: "remove", name: n }); await load(); }

  return (
    <>
      <div className="head"><h1>Webhooks</h1><span className="crumb">{hooks.length} hook{hooks.length === 1 ? "" : "s"}</span></div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Create a webhook</h3>
        <div className="grid c3" style={{ gap: 10, alignItems: "end" }}>
          <label>Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="deploy-notify" /></label>
          <button className="btn" style={{ gridColumn: "3" }} onClick={add} disabled={busy}>Add</button>
        </div>
        <label style={{ display: "block", marginTop: 10 }}>Prompt to run when called
          <textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="summarize the payload and post it to #ops" /></label>
        {name.trim() && <div className="mut" style={{ marginTop: 8 }}>POST to <code>/hooks/{name.trim()}</code> to trigger.</div>}
      </div>
      <div className="card">
        {!hooks.length && <div className="empty">No webhooks yet.</div>}
        {hooks.map((h, i) => (
          <div className="row" key={h.name || i}>
            <span><b>{h.name}</b> <span className="mut">— {(h.prompt || "").slice(0, 60)}</span></span>
            <button className="btn ghost" onClick={() => remove(h.name)}>Remove</button>
          </div>
        ))}
      </div>
    </>
  );
}
