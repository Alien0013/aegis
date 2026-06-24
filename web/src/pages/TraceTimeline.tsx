import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Loading, MetricStrip, PageHeader, SectionTitle, Select, toast } from "../components/ui";

interface TraceRow {
  id?: string;
  trace_id?: string;
  title?: string;
  status?: string;
  source?: string;
  updated_at?: string;
  started_at?: string;
}

interface TimelineItem {
  id: string;
  kind: string;
  label: string;
  status?: string;
  depth?: number;
  duration_ms?: number;
  offset_ms?: number;
  provider?: string;
  model?: string;
  tool_name?: string;
  preview?: string;
}

interface TimelineResponse {
  found?: boolean;
  id?: string;
  source?: string;
  trace_id?: string;
  items?: TimelineItem[];
  summary?: {
    total?: number;
    errors?: number;
    tools?: number;
    provider_calls?: number;
    duration_ms?: number;
  };
  trace?: { title?: string; status?: string };
}

interface TraceListResponse {
  traces?: TraceRow[];
  summary?: { total?: number; source?: string };
}

export function TraceTimeline() {
  const traces = useApi<TraceListResponse>("traces?limit=80");
  const rows = useMemo(() => traces.data?.traces || [], [traces.data]);
  const [selected, setSelected] = useState("");
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const selectedId = selected || rows[0]?.id || rows[0]?.trace_id || "";

  async function load(id = selectedId) {
    if (!id) return;
    setLoading(true);
    try {
      const next = await api<TimelineResponse>(`traces/timeline?id=${encodeURIComponent(id)}`);
      setTimeline(next);
      setSelected(id);
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setLoading(false);
    }
  }

  const current = timeline?.id === selectedId || timeline?.trace_id === selectedId ? timeline : null;
  const items = current?.items || [];

  return (
    <>
      <PageHeader
        title="Trace Timeline"
        sub={current?.trace?.title || selectedId || "No trace selected"}
        actions={<Button icon="refresh" onClick={() => { traces.reload(); void load(); }}>Refresh</Button>}
      />

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-[280px] flex-1">
            <span className="mb-1 block font-mono text-[10px] font-medium uppercase text-dim">Trace</span>
            <Select value={selectedId} onChange={(event) => setSelected(event.target.value)}>
              {rows.map((row) => {
                const id = row.id || row.trace_id || "";
                return <option key={id} value={id}>{row.title || id}</option>;
              })}
            </Select>
          </label>
          <Button icon="search" onClick={() => void load(selectedId)} disabled={!selectedId || loading}>Open</Button>
        </div>
      </Card>

      {traces.loading && <Loading />}
      {!traces.loading && !rows.length && <Card className="mt-[var(--gap)]"><Empty icon="activity">No traces.</Empty></Card>}
      {loading && <Loading label="Loading timeline..." />}

      {current && (
        <div className="mt-[var(--gap)] space-y-[var(--gap)]">
          <MetricStrip items={[
            { label: "events", value: current.summary?.total || items.length },
            { label: "provider", value: current.summary?.provider_calls || 0, tone: "info" },
            { label: "tools", value: current.summary?.tools || 0, tone: "primary" },
            { label: "errors", value: current.summary?.errors || 0, tone: current.summary?.errors ? "danger" : "success" },
          ]} />

          <Card pad={false}>
            <SectionTitle
              icon="activity"
              title={current.trace_id || current.id}
              sub={`${current.source || "trace"} / ${current.trace?.status || "recorded"}`}
            />
            {!items.length && <Empty icon="activity">No timeline events.</Empty>}
            <div className="divide-y divide-border">
              {items.map((item) => <TimelineRow key={item.id} item={item} />)}
            </div>
          </Card>
        </div>
      )}
    </>
  );
}

function TimelineRow({ item }: { item: TimelineItem }) {
  return (
    <div className="grid gap-3 px-[var(--pad)] py-3 lg:grid-cols-[80px_minmax(0,1fr)_240px]">
      <div className="font-mono text-[11px] text-faint">+{item.offset_ms || 0}ms</div>
      <div className="min-w-0" style={{ paddingLeft: `${Math.min(Number(item.depth || 0), 8) * 14}px` }}>
        <div className="flex flex-wrap items-center gap-2">
          <div className="truncate font-mono text-sm font-semibold text-text">{item.label}</div>
          <Badge status={item.status || "ok"} />
          <Badge tone="neutral">{item.kind}</Badge>
        </div>
        {!!item.preview && <div className="mt-1 truncate text-xs text-dim">{item.preview}</div>}
      </div>
      <div className="grid grid-cols-3 gap-2 text-right font-mono text-[11px] text-dim">
        <Info label="duration" value={`${item.duration_ms || 0}ms`} />
        <Info label="provider" value={item.provider || "-"} />
        <Info label="tool" value={item.tool_name || "-"} />
      </div>
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
