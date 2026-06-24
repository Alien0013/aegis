import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Loading, MetricStrip, PageHeader, SectionTitle, Select, toast } from "../components/ui";

interface SessionRow {
  id: string;
  title?: string;
  updated_at?: string;
}

interface PromptPart {
  id?: string;
  tier?: string;
  name?: string;
  source_name?: string;
  source_path?: string;
  hash?: string;
  chars?: number;
  tokens?: number;
  token_estimate?: number;
  cache_stable?: boolean;
  warnings?: string[];
}

interface PromptAuditResponse {
  found?: boolean;
  id?: string;
  title?: string;
  hash?: string;
  chars?: number;
  tokens?: number;
  part_count?: number;
  parts?: PromptPart[];
  warnings?: string[];
  cache?: {
    stable_part_count?: number;
    stable_hash?: string;
    context_part_count?: number;
    volatile_part_count?: number;
  };
  runtime_controls?: Record<string, unknown>;
  context_references?: { count?: number; injected_chars?: number; warnings?: string[] };
  raw_content_included?: boolean;
}

export function PromptAudit() {
  const sessions = useApi<SessionRow[]>("sessions");
  const [selected, setSelected] = useState("");
  const [audit, setAudit] = useState<PromptAuditResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const rows = useMemo(() => [...(sessions.data || [])].sort((a, b) =>
    String(b.updated_at || "").localeCompare(String(a.updated_at || ""))), [sessions.data]);
  const selectedId = selected || rows[0]?.id || "";

  async function load(id = selectedId) {
    if (!id) return;
    setLoading(true);
    try {
      const next = await api<PromptAuditResponse>(`sessions/${encodeURIComponent(id)}/prompt-audit`);
      setAudit(next);
      setSelected(id);
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setLoading(false);
    }
  }

  const current = audit?.id === selectedId ? audit : null;
  const parts = current?.parts || [];
  const byTier = ["stable", "context", "volatile"].map((tier) => ({
    tier,
    parts: parts.filter((part) => (part.tier || "other") === tier),
  }));

  return (
    <>
      <PageHeader
        title="Prompt Audit"
        sub={current?.title || selectedId || "No session selected"}
        actions={<Button icon="refresh" onClick={() => { sessions.reload(); void load(); }}>Refresh</Button>}
      />

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-[280px] flex-1">
            <span className="mb-1 block font-mono text-[10px] font-medium uppercase text-dim">Session</span>
            <Select value={selectedId} onChange={(event) => setSelected(event.target.value)}>
              {rows.map((row) => (
                <option key={row.id} value={row.id}>{row.title || row.id}</option>
              ))}
            </Select>
          </label>
          <Button icon="search" onClick={() => void load(selectedId)} disabled={!selectedId || loading}>Open</Button>
        </div>
      </Card>

      {sessions.loading && <Loading />}
      {!sessions.loading && !rows.length && <Card className="mt-[var(--gap)]"><Empty icon="sessions">No sessions.</Empty></Card>}
      {loading && <Loading label="Loading audit..." />}

      {current && (
        <div className="mt-[var(--gap)] space-y-[var(--gap)]">
          <MetricStrip items={[
            { label: "parts", value: current.part_count || parts.length },
            { label: "chars", value: current.chars || 0 },
            { label: "tokens", value: current.tokens || 0 },
            { label: "stable", value: current.cache?.stable_part_count || 0, tone: "success" },
          ]} />

          <Card title="Cache" sub={current.hash || "no prompt hash"}>
            <div className="grid gap-3 text-sm md:grid-cols-2 xl:grid-cols-4">
              <Info label="system hash" value={current.hash || "-"} />
              <Info label="stable hash" value={current.cache?.stable_hash || "-"} />
              <Info label="context parts" value={String(current.cache?.context_part_count || 0)} />
              <Info label="volatile parts" value={String(current.cache?.volatile_part_count || 0)} />
            </div>
          </Card>

          {!!(current.warnings || []).length && (
            <Card title="Warnings">
              <div className="space-y-2">
                {(current.warnings || []).map((warning) => (
                  <div key={warning} className="border border-warning/35 bg-warning/10 px-3 py-2 font-mono text-xs text-warning">
                    {warning}
                  </div>
                ))}
              </div>
            </Card>
          )}

          {byTier.map(({ tier, parts: tierParts }) => (
            <Card key={tier} pad={false}>
              <SectionTitle title={tier} icon="activity" sub={`${tierParts.length} part${tierParts.length === 1 ? "" : "s"}`} />
              {!tierParts.length && <Empty icon="activity">No {tier} parts.</Empty>}
              <div className="divide-y divide-border">
                {tierParts.map((part) => <PartRow key={part.id || `${part.tier}:${part.name}`} part={part} />)}
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}

function PartRow({ part }: { part: PromptPart }) {
  const warnings = part.warnings || [];
  return (
    <div className="grid gap-3 px-[var(--pad)] py-3 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <div className="truncate font-mono text-sm font-semibold text-text">{part.name || part.id}</div>
          <Badge tone={part.cache_stable ? "success" : "neutral"}>{part.cache_stable ? "cache-stable" : "dynamic"}</Badge>
          {!!warnings.length && <Badge tone="warning">{warnings.length} warning{warnings.length === 1 ? "" : "s"}</Badge>}
        </div>
        <div className="mt-1 truncate font-mono text-[11px] text-faint">{part.id || "-"}</div>
      </div>
      <div className="min-w-0 text-xs text-dim">
        <div className="truncate">{part.source_name || "-"}</div>
        <div className="truncate font-mono text-[11px] text-faint">{part.source_path || "-"}</div>
      </div>
      <div className="grid min-w-[220px] grid-cols-3 gap-2 text-right font-mono text-[11px] text-dim">
        <Info label="hash" value={part.hash || "-"} />
        <Info label="chars" value={String(part.chars || 0)} />
        <Info label="tokens" value={String(part.token_estimate ?? part.tokens ?? 0)} />
      </div>
      {!!warnings.length && (
        <div className="lg:col-span-3">
          {warnings.map((warning) => (
            <div key={warning} className="mt-1 border border-warning/30 bg-warning/10 px-2 py-1 font-mono text-[11px] text-warning">
              {warning}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="font-mono text-[10px] uppercase text-faint">{label}</div>
      <div className="truncate font-mono text-xs text-text">{value}</div>
    </div>
  );
}
