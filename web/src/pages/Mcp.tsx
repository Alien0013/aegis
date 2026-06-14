import { useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Field, Input, Loading, PageHeader, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface Server { name: string; command?: string; args?: string[]; url?: string }

export function Mcp() {
  const { data, loading, error, reload } = useApi<Server[]>("mcp");
  const [form, setForm] = useState({ name: "", command: "", args: "", url: "" });
  const [busy, setBusy] = useState(false);

  async function act(body: Record<string, unknown>) {
    setBusy(true);
    try {
      const r = await post<{ ok?: boolean; error?: string; tools?: unknown[] }>("mcp", body);
      if (r.error) toast(r.error, "err");
      else if (body.action === "probe") toast(`OK${Array.isArray(r.tools) ? ` · ${r.tools.length} tools` : ""}`, "ok");
      else { toast("Done"); reload(); }
    } catch (e) { toast(String(e), "err"); } finally { setBusy(false); }
  }
  function add() {
    if (!form.name.trim() || !(form.command.trim() || form.url.trim())) return;
    act({
      action: "add", name: form.name.trim(),
      command: form.command.trim() || undefined, url: form.url.trim() || undefined,
      args: form.args.trim() ? form.args.trim().split(/\s+/) : undefined,
    });
    setForm({ name: "", command: "", args: "", url: "" });
  }

  return (
    <>
      <PageHeader title="MCP" sub={data ? `${data.length} server${data.length === 1 ? "" : "s"}` : "Model Context Protocol servers"} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <Card title="Add server" sub="stdio (command) or Streamable HTTP (url)">
            <div className="grid gap-2 md:grid-cols-2">
              <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
              <Field label="URL (HTTP servers)"><Input value={form.url} placeholder="https://…" onChange={(e) => setForm({ ...form, url: e.target.value })} /></Field>
              <Field label="Command (stdio)"><Input value={form.command} placeholder="npx" onChange={(e) => setForm({ ...form, command: e.target.value })} /></Field>
              <Field label="Args"><Input value={form.args} placeholder="-y @modelcontextprotocol/server-x" onChange={(e) => setForm({ ...form, args: e.target.value })} /></Field>
            </div>
            <div className="mt-3"><Button variant="primary" icon="plus" onClick={add} disabled={busy}>Add server</Button></div>
          </Card>
          <Card pad={false}>
            {!data.length && <Empty icon="mcp">No MCP servers.</Empty>}
            {data.map((s) => (
              <div key={s.name} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm text-text">{s.name}</span>
                    <Badge tone={s.url ? "info" : "neutral"}>{s.url ? "http" : "stdio"}</Badge>
                  </div>
                  <div className="truncate font-mono text-xs text-faint">{s.url || `${s.command || ""} ${(s.args || []).join(" ")}`.trim()}</div>
                </div>
                <button onClick={() => act({ action: "probe", name: s.name })} className="shrink-0 text-faint hover:text-primary" title="Probe"><Icon name="activity" size={15} /></button>
                <button onClick={() => act({ action: "remove", name: s.name })} className="shrink-0 text-faint hover:text-danger" title="Delete"><Icon name="trash" size={15} /></button>
              </div>
            ))}
          </Card>
        </div>
      )}
    </>
  );
}
