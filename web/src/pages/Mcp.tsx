import { useState } from "react";
import { del, post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Field, Input, Loading, PageHeader, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface Server {
  name: string;
  command?: string;
  args?: string[];
  url?: string;
  cwd?: string;
  transport?: string;
  status?: string;
  env_keys?: string[];
  header_keys?: string[];
}
interface CatalogEntry {
  name: string;
  description?: string;
  transport?: string;
  target?: string;
  installed?: boolean;
}
interface McpPayload {
  enabled?: boolean;
  servers?: Server[];
  catalog?: CatalogEntry[];
  malformed?: string[];
}

function splitArgs(raw: string): string[] {
  return raw.split(/\s+/).map((part) => part.trim()).filter(Boolean);
}

export function Mcp() {
  const { data, loading, error, reload } = useApi<McpPayload>("mcp/catalog");
  const [form, setForm] = useState({ name: "", command: "", args: "", url: "", cwd: "" });
  const [busy, setBusy] = useState("");

  async function add() {
    if (!form.name.trim() || !(form.command.trim() || form.url.trim())) return;
    setBusy("add");
    try {
      const payload = {
        name: form.name.trim(),
        command: form.command.trim() || undefined,
        args: form.args.trim() ? splitArgs(form.args) : undefined,
        url: form.url.trim() || undefined,
        cwd: form.cwd.trim() || undefined,
      };
      const r = await post<{ ok?: boolean; error?: string }>("mcp/servers", payload);
      if (r.ok === false) toast(r.error || "Add server failed", "err");
      else { toast("Server added"); setForm({ name: "", command: "", args: "", url: "", cwd: "" }); reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  async function probe(server: Server) {
    setBusy(`probe:${server.name}`);
    try {
      const r = await post<{ ok?: boolean; error?: string; tools?: unknown[] }>(`mcp/servers/${encodeURIComponent(server.name)}/probe`, {});
      if (r.ok === false) toast(r.error || "Probe failed", "err");
      else toast(`OK${Array.isArray(r.tools) ? ` - ${r.tools.length} tools` : ""}`, "ok");
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  async function remove(server: Server) {
    if (!window.confirm(`Delete MCP server "${server.name}"?`)) return;
    setBusy(`delete:${server.name}`);
    try {
      const r = await del<{ ok?: boolean; error?: string }>(`mcp/servers/${encodeURIComponent(server.name)}`);
      if (r.ok === false) toast(r.error || "Delete failed", "err");
      else { toast("Server deleted"); reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  async function install(entry: CatalogEntry) {
    setBusy(`install:${entry.name}`);
    try {
      const r = await post<{ ok?: boolean; error?: string }>(`mcp/catalog/${encodeURIComponent(entry.name)}/install`, {});
      if (r.ok === false) toast(r.error || "Install failed", "err");
      else { toast("Installed"); reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  const servers = data?.servers || [];
  const catalog = (data?.catalog || []).slice(0, 8);

  return (
    <>
      <PageHeader
        title="MCP"
        sub={data ? `${servers.length} server${servers.length === 1 ? "" : "s"}` : "Model Context Protocol servers"}
        actions={<Button icon="refresh" onClick={reload}>Refresh</Button>}
      />
      {error && <Card><Empty icon="alert">Couldn't load - {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <Card title="Add server" sub="stdio command or Streamable HTTP URL">
            <div className="grid gap-2 md:grid-cols-2">
              <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
              <Field label="URL"><Input value={form.url} placeholder="https://example.com/mcp" onChange={(e) => setForm({ ...form, url: e.target.value })} /></Field>
              <Field label="Command"><Input value={form.command} placeholder="npx" onChange={(e) => setForm({ ...form, command: e.target.value })} /></Field>
              <Field label="Args"><Input value={form.args} placeholder="-y @modelcontextprotocol/server-x" onChange={(e) => setForm({ ...form, args: e.target.value })} /></Field>
              <Field label="Working directory"><Input value={form.cwd} placeholder="/workspace/project" onChange={(e) => setForm({ ...form, cwd: e.target.value })} /></Field>
            </div>
            <div className="mt-3"><Button variant="primary" icon="plus" onClick={add} disabled={busy === "add"}>Add server</Button></div>
          </Card>

          <div className="grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_380px]">
            <Card title="Servers" pad={false}>
              {!servers.length && <Empty icon="mcp">No MCP servers.</Empty>}
              {servers.map((server) => (
                <div key={server.name} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm text-text">{server.name}</span>
                      <Badge tone={server.url ? "info" : "neutral"}>{server.transport || (server.url ? "http" : "stdio")}</Badge>
                      {server.status && <Badge status={server.status}>{server.status}</Badge>}
                    </div>
                    <div className="truncate font-mono text-xs text-faint">{server.url || `${server.command || ""} ${(server.args || []).join(" ")}`.trim()}</div>
                    {(server.cwd || !!server.env_keys?.length || !!server.header_keys?.length) && (
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-faint">
                        {server.cwd && <span>cwd {server.cwd}</span>}
                        {!!server.env_keys?.length && <span>env {server.env_keys.join(", ")}</span>}
                        {!!server.header_keys?.length && <span>headers {server.header_keys.join(", ")}</span>}
                      </div>
                    )}
                  </div>
                  <button onClick={() => probe(server)} className="shrink-0 text-faint hover:text-primary" title="Probe"><Icon name="activity" size={15} /></button>
                  <button onClick={() => remove(server)} className="shrink-0 text-faint hover:text-danger" title="Delete"><Icon name="trash" size={15} /></button>
                </div>
              ))}
            </Card>

            <Card title="Catalog" pad={false}>
              {!catalog.length && <Empty icon="mcp">No catalog entries.</Empty>}
              {catalog.map((entry) => (
                <div key={entry.name} className="border-b border-border px-[var(--pad)] py-2.5 last:border-0">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm text-text">{entry.name}</span>
                        <Badge tone={entry.transport === "http" ? "info" : "neutral"}>{entry.transport || "stdio"}</Badge>
                        {entry.installed && <Badge status="active">installed</Badge>}
                      </div>
                      <div className="mt-1 text-xs text-faint">{entry.description || entry.target}</div>
                    </div>
                    <Button sm icon="download" disabled={!!entry.installed || busy === `install:${entry.name}`} onClick={() => install(entry)}>
                      Install
                    </Button>
                  </div>
                </div>
              ))}
            </Card>
          </div>

          {!!data.malformed?.length && (
            <Card title="Malformed entries">
              <div className="font-mono text-xs text-danger">{data.malformed.join(", ")}</div>
            </Card>
          )}
        </div>
      )}
    </>
  );
}
