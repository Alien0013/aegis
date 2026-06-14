import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { ago, compact, num } from "../lib/format";
import { Badge, Card, Empty, Loading, PageHeader, Stat } from "../components/ui";

interface Status {
  version?: string;
  provider?: string;
  model?: string;
  context_length?: number;
  provider_error?: string;
  sessions?: number;
  skills?: number;
  tools?: number;
  exec_mode?: string;
  reasoning_effort?: string;
}
interface Cockpit {
  status?: Status;
  analytics?: { total_cost?: number; total_tokens?: number };
  sessions?: Array<{ id: string; title?: string; updated_at?: string }>;
}

export function Overview() {
  const [data, setData] = useState<Cockpit | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api<Cockpit>("cockpit")
      .then(setData)
      .catch((e) => api<Status>("status").then((s) => setData({ status: s })).catch(() => setErr(String(e))));
  }, []);

  if (err) return <><PageHeader title="Overview" /><Card><Empty icon="alert">Couldn't load — {err}</Empty></Card></>;
  if (!data) return <><PageHeader title="Overview" /><Loading /></>;

  const s = data.status || {};
  const sessions = data.sessions || [];
  const cost = data.analytics?.total_cost;

  return (
    <>
      <PageHeader
        title="Overview"
        sub={`AEGIS v${s.version || "?"} · control panel`}
      />

      <div className="grid grid-cols-2 gap-[var(--gap)] md:grid-cols-4">
        <Stat label="Sessions" value={num(s.sessions)} icon="sessions" tone="primary" />
        <Stat label="Tools" value={num(s.tools)} icon="tools" tone="info" />
        <Stat label="Skills" value={num(s.skills)} icon="skills" tone="success" />
        <Stat label="30-day cost" value={cost != null ? `$${Number(cost).toFixed(2)}` : "-"} icon="analytics" tone="warning" />
      </div>

      <div className="mt-[var(--gap)] grid gap-[var(--gap)] lg:grid-cols-2">
        <Card title="Active model" sub="model · provider · permissions">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="min-w-0">
                <div className="truncate font-mono text-sm text-text">{s.model || "—"}</div>
                <div className="text-xs text-faint">{s.provider || "no provider"}</div>
              </div>
              <Badge status={s.provider_error ? "error" : "ok"}>
                {s.provider_error ? "error" : "ready"}
              </Badge>
            </div>
            {s.provider_error && (
              <div className="rounded-[var(--radius)] border border-danger/30 bg-danger/10 p-2 text-xs text-danger">
                {compact(s.provider_error, 160)}
              </div>
            )}
            <div className="grid grid-cols-3 gap-2 text-center">
              <Mini label="context" value={s.context_length ? num(s.context_length) : "—"} />
              <Mini label="perms" value={s.exec_mode || "—"} />
              <Mini label="reasoning" value={s.reasoning_effort || "off"} />
            </div>
            <Link to="/models" className="block text-xs text-primary hover:underline">Manage models →</Link>
          </div>
        </Card>

        <Card title="Recent sessions" sub={`${sessions.length} shown`}
          actions={<Link to="/sessions" className="text-xs text-primary hover:underline">All</Link>}
          pad={false}>
          {!sessions.length && <Empty icon="sessions">No sessions yet.</Empty>}
          {sessions.slice(0, 8).map((sess) => (
            <Link
              key={sess.id}
              to={`/sessions?id=${encodeURIComponent(sess.id)}`}
              className="flex items-center justify-between gap-3 border-b border-border px-[var(--pad)] py-2 last:border-0 hover:bg-surface-2"
            >
              <span className="min-w-0 truncate text-sm text-text">{compact(sess.title || sess.id, 48)}</span>
              <span className="shrink-0 text-xs text-faint">{ago(sess.updated_at)}</span>
            </Link>
          ))}
        </Card>
      </div>
    </>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[var(--radius)] border border-border bg-surface-2 py-1.5">
      <div className="text-sm font-medium text-text">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-faint">{label}</div>
    </div>
  );
}
