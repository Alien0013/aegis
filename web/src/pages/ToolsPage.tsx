import { useEffect, useMemo, useState } from "react";
import { api, post } from "../lib/api";
import { Badge, Button, Card, Empty, Loading, PageHeader, Stat, Toggle, Toolbar, useToast } from "../lib/ui";

type Tool = {
  name: string;
  description: string;
  toolset: string;
  groups: string[];
  enabled: boolean;
  toolset_active: boolean;
  off: boolean;
  available: boolean;
  unavailable_reason?: string;
};

// Tools grouped by toolset. Each tool has a live on/off switch (writes tools.disabled), and each
// toolset has a master switch (writes tools.toolsets). Changes save instantly — no terminal needed.
// /api/tools returns the flat tool array.
export function ToolsPage() {
  const [data, setData] = useState<Tool[] | undefined>(undefined);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string>("");
  const toast = useToast();

  async function load() {
    setErr("");
    try { setData(await api<Tool[]>("tools")); }
    catch (e) { setErr(String(e)); }
  }
  useEffect(() => { load(); }, []);

  async function toggleTool(t: Tool, on: boolean) {
    setBusy(t.name);
    try {
      await post("tools", { name: t.name, enabled: on });
      toast(`${t.name} ${on ? "enabled" : "disabled"}`, "ok");
      await load();
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }
  async function toggleToolset(toolset: string, on: boolean) {
    setBusy("ts:" + toolset);
    try {
      await post("tools", { toolset, enabled: on });
      toast(`toolset ${toolset} ${on ? "enabled" : "disabled"}`, "ok");
      await load();
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  const groups = useMemo(() => {
    const tools = data || [];
    const ql = q.trim().toLowerCase();
    const match = ql
      ? tools.filter((t) => `${t.name} ${t.description} ${t.toolset} ${(t.groups || []).join(" ")}`.toLowerCase().includes(ql))
      : tools;
    const by: Record<string, Tool[]> = {};
    for (const t of match) (by[t.toolset || "core"] ||= []).push(t);
    return Object.entries(by).sort((a, b) => a[0].localeCompare(b[0]));
  }, [data, q]);

  const header = (
    <PageHeader title="Tools"
      sub={data ? `${data.length} tools across ${new Set(data.map((t) => t.toolset)).size} toolsets · toggle any on or off` : undefined}
      actions={<Button variant="ghost" icon="refresh" onClick={load}>Refresh</Button>} />
  );
  if (data === undefined) return <>{header}<Loading /></>;
  if (err) return <>{header}<div className="card"><Empty>Couldn't load: {err}</Empty></div></>;

  const tools = data || [];
  const enabled = tools.filter((t) => t.enabled).length;
  const available = tools.filter((t) => t.available).length;

  return (
    <>
      {header}
      <div className="grid c4">
        <Stat label="Tools" value={tools.length} />
        <Stat label="Enabled" value={enabled} sub="model-visible now" />
        <Stat label="Available" value={available} sub={available < tools.length ? `${tools.length - available} need setup` : "all ready"} />
        <Stat label="Toolsets" value={new Set(tools.map((t) => t.toolset)).size} />
      </div>
      <Toolbar q={q} setQ={setQ} placeholder="Filter tools by name, toolset, or capability…" />
      {!groups.length && <div className="card"><Empty>No tools match “{q}”.</Empty></div>}
      <div className="grid gap-3">
        {groups.map(([toolset, items]) => {
          const on = items.filter((t) => t.enabled).length;
          const tsActive = items.some((t) => t.toolset_active);
          return (
            <Card key={toolset} pad={false}
              title={
                <span className="spread" style={{ width: "100%" }}>
                  <span className="row-flex" style={{ gap: 8 }}>
                    <span className="mono">{toolset}</span>
                    <span className="pill">{items.length}</span>
                    <span className="text-[11px] text-faint">{on}/{items.length} on</span>
                  </span>
                  <span className="row-flex" style={{ gap: 8 }}>
                    <span className="text-[11px] text-faint">toolset</span>
                    <Toggle checked={tsActive} onChange={(v) => toggleToolset(toolset, v)} />
                  </span>
                </span>
              }>
              <div>
                {items.map((t) => (
                  <div key={t.name} className="flex items-center gap-3 border-t border-line px-4 py-2.5 first:border-t-0">
                    <div className="min-w-0 flex-1">
                      <div className="row-flex" style={{ gap: 8 }}>
                        <span className="mono text-[13px] font-medium">{t.name}</span>
                        {t.off && <Badge status="idle">off</Badge>}
                        {!t.available && <Badge status="warn">needs setup</Badge>}
                      </div>
                      <div className="text-[12px] text-faint" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {!t.available && t.unavailable_reason ? t.unavailable_reason : t.description}
                      </div>
                    </div>
                    <span className="shrink-0" style={{ opacity: busy === t.name ? 0.5 : 1 }}>
                      <Toggle checked={t.enabled} onChange={(v) => toggleTool(t, v)} />
                    </span>
                  </div>
                ))}
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
