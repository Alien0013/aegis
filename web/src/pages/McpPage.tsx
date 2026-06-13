import { useEffect, useMemo, useState } from "react";
import { api, post } from "../lib/api";
import { Badge, Button, Card, Empty, Field, PageHeader, useToast } from "../lib/ui";

export function McpPage() {
  const [payload, setPayload] = useState<any>({});
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function load() {
    try { setPayload(await api("mcp/catalog")); }
    catch { setPayload({ servers: [], catalog: [] }); }
  }
  useEffect(() => { load(); }, []);

  const servers: any[] = payload.servers || [];
  const catalog: any[] = payload.catalog || [];
  const filteredCatalog = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return catalog;
    return catalog.filter((c) => `${c.name} ${c.description || ""} ${c.target || ""}`.toLowerCase().includes(query));
  }, [catalog, q]);

  async function act(body: any, ok: string) {
    setBusy(true);
    try {
      const r = await post("mcp", body);
      if (r.ok === false) toast(r.error || "failed", "err");
      else toast(ok, "ok");
      await load();
    } finally { setBusy(false); }
  }

  async function add() {
    if (!name.trim() || !command.trim()) return;
    await act({ action: "add", name: name.trim(), command: command.trim() }, `Connected ${name}`);
    setName("");
    setCommand("");
  }

  return (
    <>
      <PageHeader
        title="MCP Servers"
        sub={<><Badge status={payload.enabled === false ? "disabled" : "enabled"}>{payload.enabled === false ? "disabled" : "enabled"}</Badge> {servers.length} connected · {catalog.length} catalog entries</>}
      />
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
                <span style={{ minWidth: 0 }}>
                  <b>{s.name}</b> <Badge status={s.status}>{s.status || "configured"}</Badge>
                  <span className="mut mono"> {[s.command, ...(s.args || [])].filter(Boolean).join(" ").slice(0, 90) || s.url || ""}</span>
                  {!!s.env_keys?.length && <div className="pill-list">{s.env_keys.map((k: string) => <span className="pill mono" key={k}>{k}</span>)}</div>}
                  {!!s.tools?.length && <div className="mut">{s.tools.length} tools · {(s.resources || []).length} resources · {(s.prompts || []).length} prompts</div>}
                  {s.error && <div className="notice warn">{s.error}</div>}
                </span>
                <Button variant="danger" sm onClick={() => act({ action: "remove", name: s.name }, "Removed")}>Remove</Button>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Catalog" actions={<input className="search compact" placeholder="Search MCP" value={q} onChange={(e) => setQ(e.target.value)} />} pad={false}>
          {!filteredCatalog.length && <Empty small>No catalog entries match.</Empty>}
          <div className="market-grid">
            {filteredCatalog.map((entry) => (
              <div className="market-card" key={entry.name}>
                <div className="channel-top">
                  <div><b>{entry.name}</b><div className="mut">{entry.description || entry.target}</div></div>
                  <Badge status={entry.installed ? "enabled" : "idle"}>{entry.installed ? "installed" : entry.transport}</Badge>
                </div>
                <div className="mono smalltext">{entry.target}</div>
                <Button sm icon="plus" disabled={busy || entry.installed} onClick={() => act({ action: "install", name: entry.name }, `Installed ${entry.name}`)}>
                  {entry.installed ? "Installed" : "Install"}
                </Button>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
