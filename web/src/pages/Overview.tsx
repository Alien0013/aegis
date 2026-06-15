import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { ago, bytes, compact, num, usd } from "../lib/format";
import { Icon } from "../components/icons";
import { Badge, Button, Card, Empty, Loading, PageHeader, Stat } from "../components/ui";

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
  busy_mode?: string;
  toolsets?: string[];
}
interface SessionRow { id: string; title?: string; updated_at?: string; message_count?: number }
interface RunRow {
  id: string;
  kind?: string;
  surface?: string;
  title?: string;
  status?: string;
  started_at?: string;
  updated_at?: string;
  session_id?: string;
  summary?: string;
  preview?: string;
  error?: string;
}
interface AgentRow { id: string; kind?: string; type?: string; status?: string; task?: string; active_runs?: number }
interface Cockpit {
  status?: Status;
  analytics?: { total_cost?: number; total_tokens?: number; balance?: unknown };
  sessions?: SessionRow[];
  runs?: { runs?: RunRow[]; summary?: { total?: number } };
  traces?: { traces?: unknown[]; summary?: { total?: number } };
  agents?: { agents?: AgentRow[]; active_runs?: AgentRow[] };
  tools?: { toolsets?: string[]; disabled?: string[]; tools?: Array<{ enabled?: boolean; available?: boolean }> };
  memory?: { memory_entries?: string[]; user_entries?: string[] };
  projects?: { projects?: Array<{ name?: string; path?: string; branch?: string; current?: boolean; marker?: string; run_count?: number }> };
  review?: { available?: boolean; dirty?: boolean; branch?: string; files?: Array<unknown>; diff_stat?: string; note?: string };
  system?: { platform?: string; python?: string; disk_free_gb?: number; disk_total_gb?: number; aegis_home?: string };
  keys?: Array<{ key?: string; present?: boolean; source?: string }>;
  plugins?: { enabled?: string[]; disabled?: string[] };
  mcp?: Record<string, unknown>;
  profiles?: unknown[];
  logs?: { lines?: string[]; errors?: string[]; path?: string };
}

export function Overview() {
  const [data, setData] = useState<Cockpit | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api<Cockpit>("cockpit")
      .then(setData)
      .catch((e) => api<Status>("status").then((s) => setData({ status: s })).catch(() => setErr(String(e))));
  }, []);

  if (err) return <><PageHeader title="Dashboard" /><Card><Empty icon="alert">Couldn't load - {err}</Empty></Card></>;
  if (!data) return <><PageHeader title="Dashboard" /><Loading /></>;

  const s = data.status || {};
  const sessions = data.sessions || [];
  const runs = data.runs?.runs || [];
  const activeRuns = runs.filter((run) => ["running", "queued", "pending"].includes(String(run.status || "")));
  const agents = data.agents?.agents || [];
  const activeAgents = data.agents?.active_runs || agents.filter((agent) => agent.status === "running");
  const enabledTools = (data.tools?.tools || []).filter((tool) => tool.enabled).length;
  const availableTools = (data.tools?.tools || []).filter((tool) => tool.available).length;
  const projects = data.projects?.projects || [];
  const currentProject = projects.find((project) => project.current) || projects[0];
  const errorLines = data.logs?.errors || [];
  const missingKeys = (data.keys || []).filter((key) => !key.present).length;
  const mcpCount = Object.keys(data.mcp || {}).length;

  return (
    <>
      <PageHeader
        title="Dashboard"
        sub={`AEGIS v${s.version || "?"} · ${currentProject?.name || "local control plane"}`}
        actions={
          <div className="flex items-center gap-2">
            <Link to="/chat"><Button icon="chat" variant="primary">Open chat</Button></Link>
            <Link to="/system"><Button icon="activity">System</Button></Link>
          </div>
        }
      />

      <div className="grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-[var(--gap)]">
          <div className="grid grid-cols-2 gap-[var(--gap)] md:grid-cols-4">
            <Stat label="Sessions" value={num(s.sessions ?? sessions.length)} icon="sessions" tone="primary" />
            <Stat label="Active runs" value={num(activeRuns.length || activeAgents.length)} icon="activity" tone={activeRuns.length ? "info" : "success"} />
            <Stat label="Enabled tools" value={`${num(enabledTools || s.tools)}/${num(s.tools)}`} icon="tools" tone="info" />
            <Stat label="30-day cost" value={usd(data.analytics?.total_cost)} icon="analytics" tone="warning" />
          </div>

          <Card className="overflow-hidden" pad={false}>
            <div className="grid min-h-[260px] lg:grid-cols-[minmax(0,1.1fr)_minmax(340px,0.9fr)]">
              <section className="border-b border-border p-[var(--pad)] lg:border-b-0 lg:border-r">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-faint">Runtime</div>
                    <h2 className="mt-2 truncate font-mono text-lg font-semibold text-text">{s.model || "model unavailable"}</h2>
                    <p className="truncate text-sm text-dim">{s.provider || "provider not configured"}</p>
                  </div>
                  <Badge status={s.provider_error ? "error" : "ready"}>{s.provider_error ? "error" : "ready"}</Badge>
                </div>
                {s.provider_error && (
                  <div className="mt-3 rounded-[var(--radius)] border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
                    {compact(s.provider_error, 260)}
                  </div>
                )}
                <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
                  <Mini label="context" value={s.context_length ? num(s.context_length) : "-"} />
                  <Mini label="exec" value={s.exec_mode || "-"} />
                  <Mini label="reasoning" value={s.reasoning_effort || "off"} />
                  <Mini label="busy" value={s.busy_mode || "-"} />
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  {(s.toolsets || []).slice(0, 8).map((toolset) => <Badge key={toolset} tone="neutral">{toolset}</Badge>)}
                  {!s.toolsets?.length && <Badge tone="neutral">no toolsets</Badge>}
                </div>
                <div className="mt-4 grid gap-2 sm:grid-cols-3">
                  <LinkButton to="/models" icon="models" label="Models" />
                  <LinkButton to="/tools" icon="tools" label="Tools" />
                  <LinkButton to="/skills" icon="skills" label="Skills" />
                </div>
              </section>

              <section className="p-[var(--pad)]">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-faint">Active work</div>
                    <div className="mt-1 text-sm text-dim">{runs.length || agents.length} recent run records</div>
                  </div>
                  <Link to="/analytics" className="text-xs text-primary hover:underline">analytics</Link>
                </div>
                <div className="mt-3 space-y-2">
                  {(activeRuns.length ? activeRuns : runs).slice(0, 5).map((run) => (
                    <WorkRow key={run.id} run={run} />
                  ))}
                  {!runs.length && (
                    <div className="rounded-[var(--radius)] border border-border bg-surface-2 p-3 text-sm text-faint">
                      No run history yet. Start a chat or run a schedule to populate this stream.
                    </div>
                  )}
                </div>
              </section>
            </div>
          </Card>

          <div className="grid gap-[var(--gap)] lg:grid-cols-2">
            <Card title="Recent Sessions" sub={`${sessions.length} shown`} actions={<Link to="/sessions" className="text-xs text-primary hover:underline">View all</Link>} pad={false}>
              {!sessions.length && <Empty icon="sessions">No sessions yet.</Empty>}
              {sessions.slice(0, 8).map((sess) => (
                <Link
                  key={sess.id}
                  to={`/sessions?id=${encodeURIComponent(sess.id)}`}
                  className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0 hover:bg-surface-2"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-text">{compact(sess.title || sess.id, 54)}</span>
                    <span className="text-xs text-faint">{sess.message_count || 0} messages</span>
                  </span>
                  <span className="self-center text-xs text-faint">{ago(sess.updated_at)}</span>
                </Link>
              ))}
            </Card>

            <Card title="Project State" sub={currentProject?.path || "workspace"} actions={<Link to="/files" className="text-xs text-primary hover:underline">Files</Link>}>
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-text">{currentProject?.name || "Current workspace"}</div>
                    <div className="truncate text-xs text-faint">{currentProject?.marker || data.review?.note || "directory"}</div>
                  </div>
                  <Badge tone={data.review?.dirty ? "warning" : "success"}>{data.review?.dirty ? "dirty" : "clean"}</Badge>
                </div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <Mini label="branch" value={data.review?.branch || currentProject?.branch || "-"} />
                  <Mini label="changed" value={num(data.review?.files?.length || 0)} />
                  <Mini label="runs" value={num(currentProject?.run_count || 0)} />
                </div>
                {data.review?.diff_stat && (
                  <pre className="scroll-thin max-h-24 overflow-auto rounded-[var(--radius)] border border-border bg-surface-2 p-2 font-mono text-[11px] text-dim">{data.review.diff_stat}</pre>
                )}
              </div>
            </Card>
          </div>

          <div className="grid gap-[var(--gap)] lg:grid-cols-3">
            <RouteTile to="/cron" icon="cron" title="Schedules" text="Autonomous recurring runs with model, toolset, workdir, delivery, and output history." />
            <RouteTile to="/channels" icon="channels" title="Channels" text="Gateway adapters, probes, pairing, delivery queues, and messaging readiness." />
            <RouteTile to="/mcp" icon="mcp" title="MCP" text={`${mcpCount} configured server${mcpCount === 1 ? "" : "s"} plus catalog install and tool visibility.`} />
          </div>
        </div>

        <aside className="min-w-0 space-y-[var(--gap)]">
          <Card title="Operations" sub="health and posture">
            <div className="space-y-2">
              <HealthLine label="Provider" ok={!s.provider_error} detail={s.provider || "-"} />
              <HealthLine label="Tools" ok={availableTools > 0} detail={`${availableTools}/${s.tools || 0} available`} />
              <HealthLine label="Secrets" ok={missingKeys === 0} detail={missingKeys ? `${missingKeys} missing` : "configured"} />
              <HealthLine label="Plugins" ok detail={`${data.plugins?.enabled?.length || 0} enabled`} />
            </div>
          </Card>

          <Card title="Memory" sub="durable context">
            <div className="grid grid-cols-2 gap-2">
              <Mini label="memory facts" value={num(data.memory?.memory_entries?.length || 0)} />
              <Mini label="user facts" value={num(data.memory?.user_entries?.length || 0)} />
            </div>
            <div className="mt-3 space-y-2">
              {(data.memory?.memory_entries || data.memory?.user_entries || []).slice(0, 3).map((entry, index) => (
                <div key={`${entry}-${index}`} className="rounded-[var(--radius)] border border-border bg-surface-2 p-2 text-xs text-dim">
                  {compact(entry, 140)}
                </div>
              ))}
              {!data.memory?.memory_entries?.length && !data.memory?.user_entries?.length && (
                <div className="text-sm text-faint">No durable memory entries yet.</div>
              )}
            </div>
            <Link to="/memory" className="mt-3 block text-xs text-primary hover:underline">Manage memory</Link>
          </Card>

          <Card title="Host" sub={data.system?.platform || "local machine"}>
            <div className="space-y-2 text-sm">
              <InfoLine label="Python" value={data.system?.python || "-"} />
              <InfoLine label="Disk free" value={`${data.system?.disk_free_gb ?? "-"} / ${data.system?.disk_total_gb ?? "-"} GB`} />
              <InfoLine label="Home" value={compact(data.system?.aegis_home, 34)} />
              <InfoLine label="Tokens" value={num(data.analytics?.total_tokens || 0)} />
            </div>
          </Card>

          <Card title="Recent Logs" sub={data.logs?.path || "agent log"} actions={<Link to="/logs" className="text-xs text-primary hover:underline">Open</Link>}>
            {errorLines.length > 0 ? (
              <div className="space-y-2">
                {errorLines.slice(-4).map((line, index) => (
                  <div key={`${line}-${index}`} className="rounded-[var(--radius)] border border-danger/25 bg-danger/10 p-2 font-mono text-[11px] text-danger">
                    {compact(line, 150)}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-faint">No recent errors.</div>
            )}
          </Card>
        </aside>
      </div>
    </>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-[var(--radius)] border border-border bg-surface-2 px-2 py-2">
      <div className="truncate text-sm font-semibold text-text">{value}</div>
      <div className="truncate text-[10px] uppercase tracking-wide text-faint">{label}</div>
    </div>
  );
}

function LinkButton({ to, icon, label }: { to: string; icon: string; label: string }) {
  return (
    <Link to={to} className="flex h-9 items-center justify-center gap-2 rounded-[var(--radius)] border border-border bg-surface-2 text-sm text-dim hover:text-text">
      <Icon name={icon} size={15} />
      {label}
    </Link>
  );
}

function WorkRow({ run }: { run: RunRow }) {
  return (
    <Link
      to={run.session_id ? `/sessions?id=${encodeURIComponent(run.session_id)}` : "/analytics"}
      className="block rounded-[var(--radius)] border border-border bg-surface-2 p-3 hover:border-border-2"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-text">{compact(run.title || run.preview || run.id, 70)}</div>
          <div className="mt-0.5 truncate text-xs text-faint">{run.surface || run.kind || "run"} · {ago(run.updated_at || run.started_at)}</div>
        </div>
        <Badge status={run.status || "recorded"}>{run.status || "recorded"}</Badge>
      </div>
      {(run.summary || run.error) && (
        <div className={run.error ? "mt-2 text-xs text-danger" : "mt-2 text-xs text-dim"}>
          {compact(run.error || run.summary, 130)}
        </div>
      )}
    </Link>
  );
}

function RouteTile({ to, icon, title, text }: { to: string; icon: string; title: string; text: string }) {
  return (
    <Link to={to} className="group rounded-[var(--radius)] border border-border bg-surface p-[var(--pad)] hover:border-border-2 hover:bg-surface-2/50">
      <div className="flex items-center gap-2">
        <span className="grid h-8 w-8 place-items-center rounded-[var(--radius)] border border-border bg-surface-2 text-primary">
          <Icon name={icon} size={16} />
        </span>
        <span className="font-semibold text-text">{title}</span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-faint">{text}</p>
    </Link>
  );
}

function HealthLine({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-2">
      <div className="min-w-0">
        <div className="text-sm font-medium text-text">{label}</div>
        <div className="truncate text-xs text-faint">{detail}</div>
      </div>
      <span className={ok ? "h-2 w-2 rounded-full bg-success" : "h-2 w-2 rounded-full bg-danger"} />
    </div>
  );
}

function InfoLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border pb-2 last:border-0 last:pb-0">
      <span className="text-faint">{label}</span>
      <span className="min-w-0 truncate text-right font-mono text-xs text-text" title={value}>{value}</span>
    </div>
  );
}
