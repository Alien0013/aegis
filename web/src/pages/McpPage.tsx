import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { Button, Card, Empty, Field, PageHeader, useToast } from "../lib/ui";

export function McpPage() {
  const [servers, setServers] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

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
      if (r.ok) { toast(`Connected ${name}`, "ok"); setName(""); setCommand(""); await load(); }
      else toast(r.error || "failed", "err");
    } finally { setBusy(false); }
  }
  async function remove(n: string) { await post("mcp", { action: "remove", name: n }); toast("Removed"); await load(); }

  return (
    <>
      <PageHeader title="MCP Servers" sub={`${servers.length} server${servers.length === 1 ? "" : "s"}`} />
      <div className="stack">
        <Card title="Connect a server">
          <div className="grid c3" style={{ alignItems: "end" }}>
            <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="github" /></Field>
            <Field label="Command"><input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="npx -y @modelcontextprotocol/server-github" /></Field>
            <Button onClick={add} disabled={busy} icon="plus">Add</Button>
          </div>
        </Card>
        <Card title="Connected" pad={false}>
          {!servers.length && <Empty small>No MCP servers connected yet.</Empty>}
          <div style={{ padding: servers.length ? "2px 14px 6px" : 0 }}>
            {servers.map((s, i) => (
              <div className="row" key={s.name || i}>
                <span style={{ minWidth: 0 }}><b>{s.name}</b> <span className="mut mono">{[s.command, ...(s.args || [])].filter(Boolean).join(" ").slice(0, 70) || s.url || ""}</span></span>
                <Button variant="danger" sm onClick={() => remove(s.name)}>Remove</Button>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
