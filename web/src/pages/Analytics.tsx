import { useState } from "react";
import { useApi } from "../lib/useApi";
import { num, usd } from "../lib/format";
import { Card, Empty, Loading, PageHeader, Select, Stat } from "../components/ui";

interface ModelRow { calls: number; input: number; output: number; cache_read: number; cost_usd: number }
interface Analytics {
  days?: number; calls?: number; total_cost_usd?: number;
  by_model?: Record<string, ModelRow>;
  series?: { date?: string; cost?: number; cost_usd?: number; tokens?: number }[];
  balance?: unknown;
}

export function Analytics() {
  const [days, setDays] = useState("30");
  const { data, loading, error } = useApi<Analytics>(`analytics?days=${days}`);

  const byModel = Object.entries(data?.by_model || {}).sort(([, a], [, b]) => b.cost_usd - a.cost_usd);
  const totalTokens = byModel.reduce((s, [, m]) => s + m.input + m.output, 0);
  const series = data?.series || [];
  const maxCost = Math.max(1e-9, ...series.map((d) => Number(d.cost ?? d.cost_usd ?? 0)));

  return (
    <>
      <PageHeader title="Analytics" sub="Token usage + cost (cache-aware)"
        actions={<Select value={days} onChange={(e) => setDays(e.target.value)} className="w-28">
          {["7", "30", "90"].map((d) => <option key={d} value={d}>{d} days</option>)}
        </Select>} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <div className="grid grid-cols-2 gap-[var(--gap)] md:grid-cols-3">
            <Stat label={`Cost (${data.days || days}d)`} value={usd(data.total_cost_usd)} icon="analytics" tone="warning" />
            <Stat label="API calls" value={num(data.calls)} icon="activity" tone="info" />
            <Stat label="Tokens" value={num(totalTokens)} icon="database" tone="primary" />
          </div>

          {series.length > 0 && (
            <Card title="Daily cost">
              <div className="flex h-32 items-end gap-1">
                {series.map((d, i) => {
                  const c = Number(d.cost ?? d.cost_usd ?? 0);
                  return (
                    <div key={i} className="group relative flex-1" title={`${d.date || ""}: ${usd(c)}`}>
                      <div className="w-full rounded-t-sm bg-primary/70 transition-colors group-hover:bg-primary"
                        style={{ height: `${Math.max(2, (c / maxCost) * 120)}px` }} />
                    </div>
                  );
                })}
              </div>
            </Card>
          )}

          <Card title="By model" pad={false}>
            {!byModel.length && <Empty icon="models">No usage recorded.</Empty>}
            {byModel.map(([model, m]) => (
              <div key={model} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2 last:border-0">
                <span className="min-w-0 flex-1 truncate font-mono text-sm text-text">{model}</span>
                <span className="shrink-0 text-xs text-faint">{num(m.calls)} calls</span>
                <span className="shrink-0 text-xs text-dim">{num(m.input + m.output)} tok</span>
                <span className="w-16 shrink-0 text-right text-sm font-medium text-warning">{usd(m.cost_usd)}</span>
              </div>
            ))}
          </Card>
        </div>
      )}
    </>
  );
}
