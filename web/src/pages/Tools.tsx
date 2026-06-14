import { useMemo, useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Card, Empty, Input, Loading, PageHeader, Toggle, toast } from "../components/ui";

interface ToolRow {
  name: string; description: string; groups: string[]; toolset: string;
  enabled: boolean; off: boolean; available: boolean; unavailable_reason: string;
}

// GET /api/tools returns the tools array directly.
export function Tools() {
  const { data, loading, error, reload } = useApi<ToolRow[]>("tools");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");

  const rows = useMemo(() => (data || []).filter((t) =>
    !q || t.name.toLowerCase().includes(q.toLowerCase()) || (t.description || "").toLowerCase().includes(q.toLowerCase())),
    [data, q]);

  const groups = useMemo(() => {
    const by: Record<string, ToolRow[]> = {};
    for (const t of rows) (by[t.toolset || "other"] ||= []).push(t);
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [rows]);

  async function toggle(t: ToolRow) {
    setBusy(t.name);
    try { await post("tools", { name: t.name, enabled: !t.enabled }); reload(); }
    catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  const total = data?.length || 0;
  const enabledCount = (data || []).filter((t) => t.enabled).length;

  return (
    <>
      <PageHeader title="Tools" sub={data ? `${enabledCount}/${total} enabled` : "Tool registry"}
        actions={<Input value={q} placeholder="Filter tools…" onChange={(e) => setQ(e.target.value)} className="w-52" />} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          {!groups.length && <Card><Empty icon="tools">No tools match.</Empty></Card>}
          {groups.map(([toolset, list]) => (
            <Card key={toolset} title={toolset} sub={`${list.filter((r) => r.enabled).length}/${list.length} on`} pad={false}>
              {list.map((t) => (
                <div key={t.name} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2 last:border-0">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm text-text">{t.name}</span>
                      {(t.groups || []).map((g) => <Badge key={g} tone="neutral">{g}</Badge>)}
                      {!t.available && <Badge tone="warning">unavailable</Badge>}
                    </div>
                    <div className="truncate text-xs text-faint">{t.unavailable_reason || t.description}</div>
                  </div>
                  <Toggle on={t.enabled} disabled={busy === t.name || !t.available} onChange={() => toggle(t)} />
                </div>
              ))}
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
