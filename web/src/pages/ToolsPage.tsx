import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { Badge, Button, Card, Empty, Loading, PageHeader, Stat, Toolbar } from "../lib/ui";

type Tool = {
  name: string;
  description: string;
  toolset: string;
  groups: string[];
  enabled: boolean;
  available: boolean;
  unavailable_reason?: string;
};
// Tools grouped by toolset (the unit you enable/disable), each row showing whether it's
// active in the current toolset selection, merely available, or gated on missing setup.
// /api/tools returns the flat tool array.
export function ToolsPage() {
  const [data, setData] = useState<Tool[] | undefined>(undefined);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");

  async function load() {
    setErr("");
    setData(undefined);
    try {
      setData(await api<Tool[]>("tools"));
    } catch (e) {
      setErr(String(e));
    }
  }
  useEffect(() => {
    load();
  }, []);

  const groups = useMemo(() => {
    const tools = data || [];
    const ql = q.trim().toLowerCase();
    const match = ql
      ? tools.filter((t) =>
          `${t.name} ${t.description} ${t.toolset} ${(t.groups || []).join(" ")}`.toLowerCase().includes(ql))
      : tools;
    const by: Record<string, Tool[]> = {};
    for (const t of match) (by[t.toolset || "core"] ||= []).push(t);
    return Object.entries(by).sort((a, b) => a[0].localeCompare(b[0]));
  }, [data, q]);

  const header = (
    <PageHeader
      title="Tools"
      sub={data ? `${data.length} tools across ${new Set(data.map((t) => t.toolset)).size} toolsets` : undefined}
      actions={<Button variant="ghost" icon="refresh" onClick={load}>Refresh</Button>}
    />
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
        <Stat label="Active now" value={enabled} sub="in selected toolsets" />
        <Stat label="Available" value={available} sub={available < tools.length ? `${tools.length - available} need setup` : "all ready"} />
        <Stat label="Toolsets" value={new Set(tools.map((t) => t.toolset)).size} />
      </div>
      <Toolbar q={q} setQ={setQ} placeholder="Filter tools by name, toolset, or capability…" />
      {!groups.length && <div className="card"><Empty>No tools match “{q}”.</Empty></div>}
      <div className="grid gap-3">
        {groups.map(([toolset, items]) => {
          const on = items.filter((t) => t.enabled).length;
          return (
            <Card
              key={toolset}
              title={
                <span className="flex items-center gap-2">
                  <span className="mono">{toolset}</span>
                  <span className="pill">{items.length}</span>
                  <span className="text-[11px] text-faint">{on}/{items.length} active</span>
                </span>
              }
              pad={false}
            >
              <div>
                {items.map((t) => (
                  <div key={t.name} className="flex items-start gap-3 border-t border-line px-4 py-2.5 first:border-t-0">
                    <div className="min-w-0 flex-1">
                      <div className="mono text-[13px] font-medium">{t.name}</div>
                      <div className="text-[12px] text-faint" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {t.description}
                      </div>
                      {!t.available && t.unavailable_reason && (
                        <div className="mt-0.5 text-[11px]" style={{ color: "var(--warn, #d9a23a)" }}>{t.unavailable_reason}</div>
                      )}
                    </div>
                    <span className="shrink-0">
                      {t.enabled ? (
                        <Badge status="enabled">active</Badge>
                      ) : t.available ? (
                        <Badge status="idle">off</Badge>
                      ) : (
                        <Badge status="warn">needs setup</Badge>
                      )}
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
