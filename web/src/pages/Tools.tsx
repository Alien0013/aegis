import { useMemo, useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Field, Input, Loading, PageHeader, Select, Toggle, toast } from "../components/ui";

interface ToolRow {
  name: string; description: string; groups: string[]; toolset: string;
  enabled: boolean; off: boolean; available: boolean; unavailable_reason: string;
}

interface ToolValidation {
  ok: boolean; total: number; valid: number; invalid: number; warnings: number;
  issues: Array<{ tool: string; path: string; message: string; severity: string }>;
}

interface PermissionDryRun {
  ok: boolean; error?: string; tool?: string; available?: boolean; unavailable_reason?: string;
  visibility?: { enabled: boolean; toolset_active: boolean; off: boolean; available: boolean; toolset: string };
  authorize_without_approver?: { allowed: boolean; reason: string };
  explanation?: {
    decision: "allow" | "deny" | "prompt"; allowed: boolean; requires_prompt: boolean;
    mode: string; groups: string[]; deny_groups: string[]; allowlist_match: boolean;
    hardline_blocked: boolean; prompt: string; reasons: string[];
    security_scan: { flagged: boolean; reason: string };
  };
}

// GET /api/tools returns the tools array directly.
export function Tools() {
  const { data, loading, error, reload } = useApi<ToolRow[]>("tools");
  const validation = useApi<ToolValidation>("tools/validation");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");
  const [dryTool, setDryTool] = useState("");
  const [dryArgs, setDryArgs] = useState("{\n  \"command\": \"ls\"\n}");
  const [dryBusy, setDryBusy] = useState(false);
  const [dryRun, setDryRun] = useState<PermissionDryRun | null>(null);

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

  async function runPermissionDryRun() {
    const toolName = dryTool || rows[0]?.name || "";
    if (!toolName) {
      toast("Choose a tool first", "err");
      return;
    }
    setDryBusy(true);
    try {
      const result = await post<PermissionDryRun>("tools/permission-dry-run", { tool: toolName, args: dryArgs });
      setDryRun(result);
      if (!result.ok) toast(result.error || "Permission dry-run failed", "err");
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setDryBusy(false);
    }
  }

  const total = data?.length || 0;
  const enabledCount = (data || []).filter((t) => t.enabled).length;
  const selectedTool = dryTool || rows[0]?.name || "";

  return (
    <>
      <PageHeader title="Tools" sub={data ? `${enabledCount}/${total} enabled` : "Tool registry"}
        actions={<Input value={q} placeholder="Filter tools…" onChange={(e) => setQ(e.target.value)} className="w-52" />} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <div className="grid gap-[var(--gap)] xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
            <Card
              title="Schema Health"
              sub={validation.data ? `${validation.data.valid}/${validation.data.total} valid` : "Registry validation"}
            >
              {validation.loading && <Loading label="Checking schemas…" />}
              {validation.error && <Empty icon="alert">Couldn't validate — {validation.error}</Empty>}
              {validation.data && (
                <div className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    <Badge tone={validation.data.ok ? "success" : "danger"}>{validation.data.ok ? "valid" : "invalid"}</Badge>
                    <Badge tone="neutral">{validation.data.total} tools</Badge>
                    {!!validation.data.warnings && <Badge tone="warning">{validation.data.warnings} warnings</Badge>}
                    {!!validation.data.invalid && <Badge tone="danger">{validation.data.invalid} invalid</Badge>}
                  </div>
                  {validation.data.issues.length ? (
                    <div className="space-y-1 text-xs">
                      {validation.data.issues.slice(0, 5).map((issue, idx) => (
                        <div key={`${issue.tool}-${issue.path}-${idx}`} className="rounded-[var(--radius)] border border-border bg-surface-2 px-2 py-1">
                          <span className="font-mono text-text">{issue.tool}</span>
                          <span className="mx-1 text-faint">{issue.path}</span>
                          <Badge tone={issue.severity === "warning" ? "warning" : "danger"}>{issue.severity}</Badge>
                          <div className="mt-1 text-dim">{issue.message}</div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-sm text-dim">All registered tool schemas pass the dashboard validator.</div>
                  )}
                </div>
              )}
            </Card>
            <Card
              title="Permission Dry Run"
              sub="Simulate the policy path without executing a tool."
              actions={<Button sm icon="shield" disabled={dryBusy} onClick={runPermissionDryRun}>Check</Button>}
            >
              <div className="grid gap-3 lg:grid-cols-[220px_minmax(0,1fr)]">
                <Field label="Tool">
                  <Select value={selectedTool} onChange={(e) => setDryTool(e.target.value)}>
                    {rows.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
                  </Select>
                </Field>
                <Field label="JSON args">
                  <textarea
                    value={dryArgs}
                    onChange={(e) => setDryArgs(e.target.value)}
                    spellCheck={false}
                    rows={5}
                    className="w-full rounded-[var(--radius)] border border-border bg-surface-2/80 px-3 py-2 font-mono text-xs text-text outline-none focus:border-primary/60 focus:ring-2 focus:ring-primary/20"
                  />
                </Field>
              </div>
              {dryRun?.ok && dryRun.explanation && (
                <div className="mt-3 space-y-2 rounded-[var(--radius)] border border-border bg-surface-2/70 p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={dryRun.explanation.decision === "allow" ? "success" : dryRun.explanation.decision === "prompt" ? "warning" : "danger"}>
                      {dryRun.explanation.decision}
                    </Badge>
                    <Badge tone="neutral">mode {dryRun.explanation.mode}</Badge>
                    {dryRun.explanation.groups.map((g) => <Badge key={g} tone="neutral">{g}</Badge>)}
                    {dryRun.explanation.security_scan.flagged && <Badge tone="warning">security scan</Badge>}
                    {dryRun.available === false && <Badge tone="warning">unavailable</Badge>}
                    {dryRun.visibility?.enabled ? <Badge tone="success">model-visible</Badge> : <Badge tone="neutral">hidden</Badge>}
                    {dryRun.visibility?.off && <Badge tone="warning">disabled</Badge>}
                    {dryRun.visibility && !dryRun.visibility.toolset_active && <Badge tone="warning">toolset off</Badge>}
                  </div>
                  <div className="space-y-1 text-xs text-dim">
                    {dryRun.explanation.reasons.map((reason) => <div key={reason}>{reason}</div>)}
                    {dryRun.authorize_without_approver && (
                      <div>headless execution: {dryRun.authorize_without_approver.allowed ? "allowed" : "blocked"} — {dryRun.authorize_without_approver.reason}</div>
                    )}
                    {dryRun.unavailable_reason && <div>{dryRun.unavailable_reason}</div>}
                  </div>
                </div>
              )}
              {dryRun && !dryRun.ok && <div className="mt-3 text-sm text-danger">{dryRun.error}</div>}
            </Card>
          </div>
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
