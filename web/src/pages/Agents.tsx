import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, Empty, Loading, PageHeader } from "../components/ui";
import { Icon } from "../components/icons";
import { post } from "../lib/api";
import { desktop, isDesktop } from "../lib/desktop";
import { ago, compact, num } from "../lib/format";
import { useApi } from "../lib/useApi";

interface ActivityRow {
  id: string;
  surface?: string;
  session_id?: string;
  run_id?: string;
  trace_id?: string;
  turn_id?: string;
  title?: string;
  prompt_preview?: string;
  provider?: string;
  model?: string;
  phase?: string;
  status?: string;
  iteration?: number;
  max_iterations?: number;
  active_provider?: string;
  active_tool?: string;
  active_tool_id?: string;
  provider_calls?: number;
  tool_calls?: number;
  tool_errors?: number;
  subagents_active?: number;
  subagents_done?: number;
  subagents?: SubagentRow[];
  compactions?: number;
  last_event?: string;
  last_tool?: string;
  last_text_preview?: string;
  last_error?: string;
  note?: string;
  started_at?: string;
  updated_at?: string;
  ended_at?: string;
  elapsed_ms?: number;
  active_elapsed_ms?: number;
}

interface SubagentRow {
  id: string;
  agent_type?: string;
  task?: string;
  status?: string;
  text_preview?: string;
  reasoning_preview?: string;
  error?: string;
  started_at?: string;
  updated_at?: string;
  ended_at?: string;
  elapsed_ms?: number;
}

interface ActivityPayload {
  active?: ActivityRow[];
  recent?: ActivityRow[];
  active_count?: number;
  recent_count?: number;
}

interface BackgroundJob {
  id: string;
  status?: string;
  prompt?: string;
  result_preview?: string;
  error?: string;
  run_id?: string;
  parent_session_id?: string;
  agent_type?: string;
  retry_of?: string;
  cancel_requested?: boolean;
  started_at?: number;
  finished_at?: number;
}

interface BackgroundPayload {
  jobs?: BackgroundJob[];
  active?: BackgroundJob[];
  completed?: BackgroundJob[];
  failed?: BackgroundJob[];
  stats?: { total?: number; active?: number; completed?: number; failed?: number };
  capacity?: { max?: number; running?: number; available?: number };
}

export function Agents() {
  const activity = useApi<ActivityPayload>("activity");
  const background = useApi<BackgroundPayload>("background/jobs");
  const [jobBusy, setJobBusy] = useState("");
  const active = activity.data?.active || [];
  const recent = activity.data?.recent || [];
  const backgroundJobs = background.data?.jobs || [];
  const activeSubagents = active.reduce((total, row) => total + Number(row.subagents_active || 0), 0);
  const toolCalls = [...active, ...recent].reduce((total, row) => total + Number(row.tool_calls || 0), 0);
  const modelCalls = [...active, ...recent].reduce((total, row) => total + Number(row.provider_calls || 0), 0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      activity.reload();
      background.reload();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activity.reload, background.reload]);

  async function backgroundAction(job: BackgroundJob, action: "cancel" | "retry") {
    setJobBusy(`${action}:${job.id}`);
    try {
      await post(`background/jobs/${encodeURIComponent(job.id)}/${action}`, {});
      background.reload();
      activity.reload();
    } finally {
      setJobBusy("");
    }
  }

  const separateWindow = () => {
    if (isDesktop && desktop?.openAgentsWindow) {
      desktop.openAgentsWindow();
      return;
    }
    const url = `${window.location.origin}${window.location.pathname}${window.location.search}#/agents`;
    window.open(url, "aegis-agents", "popup,width=1180,height=820");
  };

  const groupedRecent = useMemo(() => recent.slice(0, 16), [recent]);

  return (
    <>
      <PageHeader
        title="Live Agents"
        sub="Running turns, subagents, active tools, and recent completed work."
        actions={
          <div className="flex gap-2">
            <Button icon="refresh" onClick={activity.reload}>Refresh</Button>
            <Button icon="external" variant="primary" onClick={separateWindow}>
              {isDesktop ? "Open window" : "Pop out"}
            </Button>
          </div>
        }
      />

      {activity.error && <Card><Empty icon="alert">Could not load live activity - {activity.error}</Empty></Card>}
      {activity.loading && !activity.data && <Loading />}

      <div className="grid gap-[var(--gap)] sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="active runs" value={num(active.length)} tone={active.length ? "info" : "success"} />
        <Metric label="subagents" value={num(activeSubagents)} tone={activeSubagents ? "warning" : "neutral"} />
        <Metric label="model calls" value={num(modelCalls)} tone="primary" />
        <Metric label="tool calls" value={num(toolCalls)} tone="info" />
      </div>

      <div className="mt-[var(--gap)] grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_390px]">
        <section className="min-w-0 space-y-[var(--gap)]">
          <Card
            title="Working Now"
            sub={active.length ? `${active.length} active execution${active.length === 1 ? "" : "s"}` : "No running turns"}
            pad={false}
          >
            {!active.length && (
              <Empty icon="activity">No live agent work right now. Start a chat, schedule, or subagent task and it will appear here.</Empty>
            )}
            <div className="grid gap-px bg-border">
              {active.map((row) => <ActivityCard key={row.id} row={row} active />)}
            </div>
          </Card>

          <Card title="Recent Activity" sub={`${groupedRecent.length} retained`} pad={false}>
            {!groupedRecent.length && <Empty icon="analytics">Completed activity will appear after a run finishes.</Empty>}
            <div className="grid gap-px bg-border">
              {groupedRecent.map((row) => <ActivityCard key={`${row.id}-${row.ended_at || row.updated_at}`} row={row} />)}
            </div>
          </Card>
        </section>

        <aside className="min-w-0 space-y-[var(--gap)]">
          <Card title="Window" sub={isDesktop ? "native desktop" : "browser pop-out"}>
            <Button icon="external" variant="primary" className="w-full justify-center" onClick={separateWindow}>
              {isDesktop ? "Open live agents window" : "Open pop-out"}
            </Button>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <Mini label="active" value={num(active.length)} />
              <Mini label="recent" value={num(recent.length)} />
            </div>
          </Card>
          <BackgroundJobsCard
            payload={background.data ?? undefined}
            jobs={backgroundJobs}
            loading={background.loading}
            error={background.error}
            busy={jobBusy}
            onAction={backgroundAction}
          />
          <Card title="Shortcuts">
            <div className="grid gap-2">
              <LinkButton to="/app" icon="chat" label="Desktop chat" />
              <LinkButton to="/chat" icon="terminal" label="Terminal chat" />
              <LinkButton to="/command-center" icon="command" label="Command Center" />
              <LinkButton to="/logs" icon="logs" label="Logs" />
            </div>
          </Card>
          <Card title="Last Signal">
            {active[0] || recent[0] ? (
              <ActivityFacts row={(active[0] || recent[0]) as ActivityRow} />
            ) : (
              <div className="text-sm text-faint">No activity snapshot yet.</div>
            )}
          </Card>
        </aside>
      </div>
    </>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone: "primary" | "info" | "warning" | "success" | "neutral" }) {
  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="font-mono text-2xl font-semibold text-text">{value}</div>
          <div className="mt-1 text-[10px] uppercase tracking-wide text-faint">{label}</div>
        </div>
        <Badge tone={tone}>{label.split(" ")[0]}</Badge>
      </div>
    </Card>
  );
}

function BackgroundJobsCard({
  payload,
  jobs,
  loading,
  error,
  busy,
  onAction,
}: {
  payload?: BackgroundPayload;
  jobs: BackgroundJob[];
  loading?: boolean;
  error?: string;
  busy: string;
  onAction: (job: BackgroundJob, action: "cancel" | "retry") => void;
}) {
  const stats = payload?.stats || {};
  const capacity = payload?.capacity || {};
  const visible = jobs.slice(0, 6);
  return (
    <Card title="Background Jobs" sub={`${num(stats.active || 0)} active / ${num(stats.failed || 0)} failed`} pad={false}>
      <div className="border-b border-border px-[var(--pad)] py-3">
        <div className="grid grid-cols-3 gap-2">
          <Mini label="running" value={num(capacity.running || 0)} />
          <Mini label="free" value={num(capacity.available || 0)} />
          <Mini label="max" value={num(capacity.max || 0)} />
        </div>
      </div>
      {error && <Empty icon="alert">Could not load background jobs - {error}</Empty>}
      {loading && !payload && <Loading />}
      {!error && !visible.length && !loading && (
        <Empty icon="agents">No retained background jobs.</Empty>
      )}
      <div className="grid gap-px bg-border">
        {visible.map((job) => {
          const status = String(job.status || "");
          const running = status === "running" || status === "cancelling";
          const retryable = !running;
          const bad = status === "error" || status === "cancelled";
          return (
            <div key={job.id} className="bg-surface p-3 text-xs">
              <div className="flex items-center gap-2">
                <Icon name="agents" size={13} className={bad ? "text-danger" : running ? "text-info" : "text-success"} />
                <span className="min-w-0 flex-1 truncate font-mono text-text">{job.agent_type || "worker"}</span>
                <Badge status={status || "queued"}>{status || "queued"}</Badge>
              </div>
              <div className="mt-1 truncate text-dim">{compact(job.prompt || job.id, 120)}</div>
              {(job.error || job.result_preview) && (
                <div className={bad ? "mt-1 text-danger" : "mt-1 text-faint"}>
                  {compact(job.error || job.result_preview || "", 160)}
                </div>
              )}
              <div className="mt-3 flex gap-2">
                {running && (
                  <Button sm icon="x" variant="danger" disabled={busy === `cancel:${job.id}`} onClick={() => onAction(job, "cancel")}>
                    Cancel
                  </Button>
                )}
                {retryable && (
                  <Button sm icon="refresh" disabled={busy === `retry:${job.id}`} onClick={() => onAction(job, "retry")}>
                    Retry
                  </Button>
                )}
                {job.run_id && <Link to="/analytics" className="px-2 py-1 text-faint hover:text-primary">trace</Link>}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function ActivityCard({ row, active = false }: { row: ActivityRow; active?: boolean }) {
  const bad = Boolean(row.last_error) || ["error", "failed", "cancelled"].includes(String(row.status || "").toLowerCase());
  const title = row.title || row.prompt_preview || row.active_tool || row.last_tool || row.id;
  const provider = [row.provider, row.model].filter(Boolean).join(" / ");
  const tool = row.active_tool || row.last_tool || "";
  const subagents = row.subagents || [];
  const longRun = active && (Number(row.elapsed_ms || 0) > 30000 || Number(row.active_elapsed_ms || 0) > 30000);
  return (
    <article className="bg-surface p-[var(--pad)]">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${bad ? "bg-danger" : active ? "bg-info" : "bg-success"}`} />
            <span className="truncate font-semibold text-text">{compact(title, 110)}</span>
            <Badge status={row.status || (active ? "running" : "done")}>{row.status || (active ? "running" : "done")}</Badge>
            {longRun && <Badge tone="warning">long run</Badge>}
            {row.phase && <Badge tone="neutral">{row.phase}</Badge>}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-faint">
            <span>{row.surface || "agent"}</span>
            {provider && <span>{provider}</span>}
            {row.iteration ? <span>step {row.iteration}/{row.max_iterations || "?"}</span> : null}
            <span>elapsed {formatMs(row.elapsed_ms || 0)}</span>
            {active && row.active_elapsed_ms ? <span>active {formatMs(row.active_elapsed_ms)}</span> : null}
          </div>
          {(tool || row.active_provider || row.last_text_preview || row.last_error) && (
            <div className="mt-3 rounded-[var(--radius)] border border-border bg-surface-2/70 px-3 py-2 text-xs">
              {tool && <div className="font-mono text-text">tool: {tool}</div>}
              {row.active_provider && <div className="font-mono text-text">model: {row.active_provider}</div>}
              {row.last_error && <div className="mt-1 text-danger">{compact(row.last_error, 220)}</div>}
              {!row.last_error && row.last_text_preview && <div className="mt-1 text-dim">{compact(row.last_text_preview, 220)}</div>}
            </div>
          )}
          {subagents.length > 0 && (
            <div className="mt-3 grid gap-2">
              {subagents.slice(0, 6).map((child) => <SubagentCard key={child.id} child={child} />)}
              {subagents.length > 6 && (
                <div className="text-[11px] text-faint">+{subagents.length - 6} more subagents</div>
              )}
            </div>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2 lg:w-56">
          <Mini label="model" value={num(row.provider_calls || 0)} />
          <Mini label="tools" value={num(row.tool_calls || 0)} danger={Boolean(row.tool_errors)} />
          <Mini label="sub active" value={num(row.subagents_active || 0)} />
          <Mini label="sub done" value={num(row.subagents_done || 0)} />
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-faint">
        {row.session_id && <Link to={`/sessions?id=${encodeURIComponent(row.session_id)}`} className="hover:text-primary">session {short(row.session_id)}</Link>}
        {row.run_id && <span>run {short(row.run_id)}</span>}
        {row.trace_id && <Link to="/analytics" className="hover:text-primary">trace {short(row.trace_id)}</Link>}
        <span>{ago(row.updated_at || row.started_at)}</span>
      </div>
    </article>
  );
}

function SubagentCard({ child }: { child: SubagentRow }) {
  const status = child.status || "running";
  const bad = ["error", "failed", "cancelled"].includes(status.toLowerCase());
  const preview = child.error || child.text_preview || child.reasoning_preview || "";
  return (
    <div className={`rounded-[var(--radius)] border px-3 py-2 text-xs ${bad ? "border-danger/35 bg-danger/10" : "border-border bg-surface-2/55"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Icon name="agents" size={13} className={bad ? "text-danger" : "text-primary"} />
        <span className="min-w-0 truncate font-mono text-text">{child.agent_type || "worker"}</span>
        <Badge status={status}>{status}</Badge>
        <span className="text-faint">{formatMs(child.elapsed_ms || 0)}</span>
      </div>
      {child.task && <div className="mt-1 truncate text-dim">{compact(child.task, 150)}</div>}
      {preview && <div className={bad ? "mt-1 text-danger" : "mt-1 text-faint"}>{compact(preview, 220)}</div>}
    </div>
  );
}

function ActivityFacts({ row }: { row: ActivityRow }) {
  return (
    <div className="space-y-2 text-sm">
      <Fact label="Status" value={row.status || row.phase || "running"} />
      <Fact label="Surface" value={row.surface || "agent"} />
      <Fact label="Model" value={[row.provider, row.model].filter(Boolean).join(" / ") || "-"} />
      <Fact label="Tool" value={row.active_tool || row.last_tool || "-"} />
      <Fact label="Session" value={short(row.session_id)} />
      <Fact label="Run" value={short(row.run_id)} />
    </div>
  );
}

function Mini({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className={`rounded-[var(--radius)] border px-2 py-1.5 ${danger ? "border-danger/35 bg-danger/10" : "border-border bg-surface-2/60"}`}>
      <div className="font-mono text-sm font-semibold text-text">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-faint">{label}</div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border pb-2 last:border-0 last:pb-0">
      <span className="text-faint">{label}</span>
      <span className="min-w-0 truncate text-right font-mono text-xs text-text">{value || "-"}</span>
    </div>
  );
}

function LinkButton({ to, icon, label }: { to: string; icon: string; label: string }) {
  return (
    <Link to={to} className="flex items-center gap-2 rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-2 text-sm text-dim hover:text-text">
      <Icon name={icon} size={14} />
      <span>{label}</span>
    </Link>
  );
}

function formatMs(ms: number): string {
  if (!ms) return "0s";
  const seconds = Math.max(1, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function short(value?: string): string {
  const text = String(value || "");
  if (!text) return "-";
  return text.length > 16 ? `${text.slice(0, 10)}...${text.slice(-4)}` : text;
}
