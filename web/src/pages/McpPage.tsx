import { useEffect, useState } from "react";
import { api, post } from "../lib/api";

export function McpPage() {
  const [servers, setServers] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const d = await api("mcp");
      const raw = d.servers || d.mcp || d;
      setServers(Array.isArray(raw) ? raw
        : Object.entries(raw || {}).map(([k, v]: any) => ({ name: k, ...(typeof v === "object" ? v : { command: v }) })));
    } catch { setServers([]); }
  }
  useEffect(() => { load(); }, []);

  async function add() {
    if (!name.trim() || !command.trim()) return;
    setBusy(true);
    try {
      const r = await post("mcp", { action: "add", name: name.trim(), command: command.trim() });
      setMsg(r.ok ? `✓ added ${name}` : "✗ " + (r.error || "failed"));
      if (r.ok) { setName(""); setCommand(""); await load(); }
    } finally { setBusy(false); }
  }
  async function remove(n: string) { await post("mcp", { action: "remove", name: n }); await load(); }

  return (
    <>
      <div className="head"><h1>MCP Servers</h1><span className="crumb">{servers.length} server{servers.length === 1 ? "" : "s"}</span></div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Connect a server</h3>
        <div className="grid c3" style={{ gap: 10, alignItems: "end" }}>
          <label>Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="github" /></label>
          <label style={{ gridColumn: "span 1" }}>Command<input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="npx -y @modelcontextprotocol/server-github" /></label>
          <button className="btn" onClick={add} disabled={busy}>Add</button>
        </div>
        {msg && <div className="mut" style={{ marginTop: 8 }}>{msg}</div>}
      </div>
      <div className="card">
        {!servers.length && <div className="empty">No MCP servers connected yet.</div>}
        {servers.map((s, i) => (
          <div className="row" key={s.name || i}>
            <span><b>{s.name}</b> <span className="mut">{[s.command, ...(s.args || [])].filter(Boolean).join(" ").slice(0, 70) || s.url || ""}</span></span>
            <button className="btn ghost" onClick={() => remove(s.name)}>Remove</button>
          </div>
        ))}
      </div>
    </>
  );
}
