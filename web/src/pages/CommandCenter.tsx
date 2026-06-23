import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { ago, compact, num, usd } from "../lib/format";
import { cn } from "../lib/cn";
import { Badge, Button, Card, Empty, Loading, MetricStrip, PageHeader, Segmented } from "../components/ui";
import { Icon } from "../components/icons";
import { PluginSlot, useDashboardPluginHost } from "../plugins/host";

interface Status {
  version?: string;
  provider?: string;
  model?: string;
  context_length?: number;
  provider_error?: string;
  sessions?: number;
  active_sessions?: number;
  skills?: number;
  tools?: number;
  exec_mode?: string;
  reasoning_effort?: string;
  busy_mode?: string;
  gateway_running?: boolean;
  gateway_state?: string;
  toolsets?: string[];
  activity?: { active?: ActivityRow[]; recent?: ActivityRow[]; active_count?: number; recent_count?: number };
}

interface SessionRow {
  id: string;
  title?: string;
  updated_at?: string;
  message_count?: number;
  surface?: string;
  model?: string;
}

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

interface ToolRow {
  name?: string;
  tool?: string;
  enabled?: boolean;
  available?: boolean;
  unavailable_reason?: string;
}

interface ActivityRow {
  id: string;
  surface?: string;
  session_id?: string;
  run_id?: string;
  title?: string;
  prompt_preview?: string;
  provider?: string;
  model?: string;
  phase?: string;
  status?: string;
  iteration?: number;
  max_iterations?: number;
  active_tool?: string;
  tool_calls?: number;
  provider_calls?: number;
  subagents_active?: number;
  subagents_done?: number;
  elapsed_ms?: number;
  active_elapsed_ms?: number;
  last_text_preview?: string;
  last_error?: string;
  updated_at?: string;
}

interface Cockpit {
  status?: Status;
  activity?: { active?: ActivityRow[]; recent?: ActivityRow[]; active_count?: number; recent_count?: number };
  analytics?: { total_cost?: number; total_tokens?: number; balance?: unknown };
  sessions?: SessionRow[];
  runs?: { runs?: RunRow[]; summary?: { total?: number } };
  traces?: { traces?: unknown[]; summary?: { total?: number; errors?: number } };
  tools?: { toolsets?: string[]; disabled?: string[]; tools?: ToolRow[] };
  memory?: { memory_entries?: string[]; user_entries?: string[] };
  projects?: { projects?: Array<{ name?: string; path?: string; branch?: string; current?: boolean; marker?: string; run_count?: number }> };
  review?: { available?: boolean; dirty?: boolean; branch?: string; files?: unknown[]; diff_stat?: string; note?: string };
  system?: { platform?: string; python?: string; disk_free_gb?: number; disk_total_gb?: number; aegis_home?: string };
  keys?: Array<{ key?: string; present?: boolean; source?: string }>;
  plugins?: { enabled?: string[]; disabled?: string[] };
  mcp?: Record<string, unknown>;
  logs?: { lines?: string[]; errors?: string[]; path?: string };
}

type Mode = "sessions" | "system" | "usage";

const ACTIVE_STATUSES = new Set(["active", "running", "queued", "pending", "in_progress", "working"]);
const BAD_STATUSES = new Set(["error", "failed", "blocked", "denied"]);

export function CommandCenter() {
  const [mode, setMode] = useState<Mode>("sessions");
  const [data, setData] = useState<Cockpit | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const pluginHost = useDashboardPluginHost();

  const load = () => {
    setLoading(true);
    api<Cockpit>("cockpit")
      .then((next) => {
        setData(next);
        setError("");
        setUpdatedAt(new Date());
      })
      .catch((e) => api<Status>("status")
        .then((status) => {
          setData({ status });
          setError("");
          setUpdatedAt(new Date());
        })
        .catch(() => setError(String(e))))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const status = data?.status || {};
  const sessions = useMemo(() => [...(data?.sessions || [])].sort(byUpdated), [data?.sessions]);
  const runs = useMemo(() => [...(data?.runs?.runs || [])].sort(byRunTime), [data?.runs?.runs]);
  const activeRuns = runs.filter((run) => ACTIVE_STATUSES.has(statusKey(run.status)));
  const activeActivity = data?.activity?.active || status.activity?.active || [];
  const recentActivity = data?.activity?.recent || status.activity?.recent || [];
  const problemRuns = runs.filter((run) => run.error || BAD_STATUSES.has(statusKey(run.status)));
  const tools = data?.tools?.tools || [];
  const availableTools = tools.filter((tool) => tool.available).length;
  const enabledTools = tools.filter((tool) => tool.enabled).length;
  const missingKeys = (data?.keys || []).filter((key) => !key.present).length;
  const gateway = status.gateway_state || (status.gateway_running ? "running" : "offline");
  const projects = data?.projects?.projects || [];
  const currentProject = projects.find((project) => project.current) || projects[0];
  const pluginStatuses = Array.from(pluginHost.pluginStatuses.values());
  const pluginProblems = pluginStatuses.filter((plugin) => plugin.asset_status === "error" || plugin.errors?.length || plugin.css_errors?.length).length;
  const pluginCount = data?.plugins?.enabled?.length ?? pluginHost.manifests.length;
  const ready = !status.provider_error && !error;

  if (loading && !data) return <><PageHeader title="Command Center" /><Loading /></>;

  return (
    <>
      <PageHeader
        title="Command Center"
        sub={`${currentProject?.name || "Local workspace"} / ${status.model || "model unavailable"}${updatedAt ? ` / refreshed ${ago(updatedAt.toISOString())}` : ""}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Link to="/app"><Button icon="plus" variant="primary">New chat</Button></Link>
            <Link to="/sessions"><Button icon="sessions">Sessions</Button></Link>
            <Button icon="refresh" onClick={load} disabled={loading}>Refresh</Button>
          </div>
        }
      />

      <PluginSlot name="dashboard:top" className="mb-[var(--gap)]" />

      {error && (
        <Card className="mb-[var(--gap)]">
          <Empty icon="alert">Could not load command center: {error}</Empty>
        </Card>
      )}

      <div className="mb-[var(--gap)] flex flex-wrap items-center justify-between gap-3">
        <Segmented<Mode>
          value={mode}
          onChange={setMode}
          items={[
            { value: "sessions", label: "Sessions", icon: "sessions", count: sessions.length },
            { value: "system", label: "System", icon: "system", count: ready ? "ok" : "!" },
            { value: "usage", label: "Usage", icon: "analytics", count: data?.runs?.summary?.total || runs.length },
          ]}
        />
        <div className="flex flex-wrap gap-1.5">
          <Badge tone={ready ? "success" : "danger"}>{ready ? "ready" : "attention"}</Badge>
          <Badge tone={gateway === "running" ? "success" : "neutral"}>{gateway}</Badge>
          <Badge tone={activeActivity.length || activeRuns.length ? "info" : "neutral"}>{activeActivity.length || activeRuns.length} active</Badge>
        </div>
      </div>

      <MetricStrip items={[
        { label: "sessions", value: num(status.sessions ?? sessions.length), tone: "primary" },
        { label: "active work", value: num(activeActivity.length || activeRuns.length), tone: activeActivity.length || activeRuns.length ? "info" : "success" },
        { label: "tools", value: `${num(availableTools || enabledTools || status.tools)}/${num(status.tools || tools.length)}`, tone: "info" },
        { label: "trace errors", value: num(data?.traces?.summary?.errors || problemRuns.length), tone: problemRuns.length ? "danger" : "neutral" },
      ]} />

      <div className="mt-[var(--gap)]">
        {mode === "sessions" && (
          <div className="grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_360px]">
            <Card title="Recent Sessions" sub="Resume or inspect the latest work" pad={false}>
              {!sessions.length && <Empty icon="sessions">No sessions yet.</Empty>}
              {sessions.slice(0, 12).map((session) => <SessionLine key={session.id} session={session} />)}
            </Card>
            <div className="space-y-[var(--gap)]">
              <Card title="Active Work" sub={`${activeActivity.length || activeRuns.length || runs.length} shown`} pad={false}>
                {activeActivity.slice(0, 8).map((item) => <ActivityLine key={item.id} item={item} />)}
                {!activeActivity.length && (activeRuns.length ? activeRuns : runs).slice(0, 8).map((run) => <RunLine key={run.id} run={run} />)}
                {!activeActivity.length && !runs.length && <Empty icon="activity">No running or recorded work yet.</Empty>}
              </Card>
              <Card title="Fast Paths">
                <div className="grid grid-cols-2 gap-2">
                  <QuickLink to="/app" icon="chat" label="Chat app" />
                  <QuickLink to="/chat" icon="terminal" label="Terminal" />
                  <QuickLink to="/models" icon="models" label="Models" />
                  <QuickLink to="/logs" icon="logs" label="Logs" />
                </div>
              </Card>
            </div>
          </div>
        )}

        {mode === "system" && (
          <div className="grid gap-[var(--gap)] lg:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_420px]">
            <Card title="Runtime" sub={status.provider || "provider not configured"}>
              <div className="space-y-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate font-mono text-lg font-semibold text-text">{status.model || "model unavailable"}</div>
                    <div className="truncate text-xs text-faint">AEGIS {status.version || "unknown"} / {data?.system?.platform || "local"}</div>
                  </div>
                  <Badge status={ready ? "ready" : "error"}>{ready ? "ready" : "error"}</Badge>
                </div>
                {status.provider_error && (
                  <div className="rounded-[var(--radius)] border border-danger/35 bg-danger/10 p-3 text-xs text-danger">
                    {compact(status.provider_error, 260)}
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                  <Mini label="context" value={status.context_length ? num(status.context_length) : "-"} />
                  <Mini label="exec" value={status.exec_mode || "-"} />
                  <Mini label="reasoning" value={status.reasoning_effort || "off"} />
                  <Mini label="busy" value={status.busy_mode || "-"} />
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(status.toolsets || data?.tools?.toolsets || []).slice(0, 10).map((toolset) => <Badge key={toolset} tone="neutral">{toolset}</Badge>)}
                  {(data?.tools?.disabled || []).slice(0, 5).map((toolset) => <Badge key={toolset} tone="warning">{toolset} off</Badge>)}
                </div>
              </div>
            </Card>

            <Card title="Health" sub="What needs attention" pad={false}>
              <HealthLine icon="models" label="Provider" value={status.provider || "not configured"} ok={!status.provider_error} to="/models" />
              <HealthLine icon="channels" label="Gateway" value={gateway} ok={gateway === "running"} to="/channels" />
              <HealthLine icon="tools" label="Tools" value={`${availableTools}/${tools.length || status.tools || 0} available`} ok={availableTools > 0 || !!status.tools} to="/tools" />
              <HealthLine icon="keys" label="Secrets" value={missingKeys ? `${missingKeys} missing` : "configured"} ok={missingKeys === 0} to="/env" />
              <HealthLine icon="plugins" label="Plugins" value={pluginProblems ? `${pluginProblems} issue${pluginProblems === 1 ? "" : "s"}` : `${pluginCount} enabled`} ok={pluginProblems === 0} to="/plugins" />
            </Card>

            <Card title="Workspace" sub={currentProject?.path || "local checkout"}>
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-text">{currentProject?.name || "Current workspace"}</div>
                    <div className="truncate text-xs text-faint">{currentProject?.marker || data?.review?.note || "directory"}</div>
                  </div>
                  <Badge tone={data?.review?.dirty ? "warning" : "success"}>{data?.review?.dirty ? "dirty" : "clean"}</Badge>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <Mini label="branch" value={data?.review?.branch || currentProject?.branch || "-"} />
                  <Mini label="changed" value={num(data?.review?.files?.length || 0)} />
                  <Mini label="runs" value={num(currentProject?.run_count || 0)} />
                </div>
                {data?.review?.diff_stat && (
                  <pre className="scroll-thin max-h-28 overflow-auto rounded-[var(--radius)] border border-border bg-surface-2 p-2 font-mono text-[11px] text-dim">{data.review.diff_stat}</pre>
                )}
              </div>
            </Card>

            <Card title="System Paths" sub={data?.system?.aegis_home || "AEGIS home"}>
              <div className="grid grid-cols-2 gap-2">
                <Mini label="python" value={compact(data?.system?.python || "-", 30)} />
                <Mini label="disk free" value={data?.system?.disk_free_gb != null ? `${data.system.disk_free_gb.toFixed(1)} GB` : "-"} />
                <Mini label="disk total" value={data?.system?.disk_total_gb != null ? `${data.system.disk_total_gb.toFixed(1)} GB` : "-"} />
                <Mini label="mcp" value={num(Object.keys(data?.mcp || {}).length)} />
              </div>
            </Card>
          </div>
        )}

        {mode === "usage" && (
          <div className="grid gap-[var(--gap)] xl:grid-cols-[380px_minmax(0,1fr)]">
            <Card title="Usage Summary" sub="Cost, traces, and durable context">
              <div className="grid grid-cols-2 gap-2">
                <Mini label="cost" value={usd(data?.analytics?.total_cost)} />
                <Mini label="tokens" value={num(data?.analytics?.total_tokens)} />
                <Mini label="traces" value={num(data?.traces?.summary?.total || data?.traces?.traces?.length || 0)} />
                <Mini label="errors" value={num(data?.traces?.summary?.errors || problemRuns.length)} />
                <Mini label="memory" value={num(data?.memory?.memory_entries?.length || 0)} />
                <Mini label="user facts" value={num(data?.memory?.user_entries?.length || 0)} />
              </div>
            </Card>
            <Card title="Run History" sub={`${runs.length} recent runs`} pad={false}>
              {recentActivity.slice(0, 5).map((item) => <ActivityLine key={item.id} item={item} />)}
              {!runs.length && !recentActivity.length && <Empty icon="analytics">No run history yet.</Empty>}
              {runs.slice(0, 14).map((run) => <RunLine key={run.id} run={run} detail />)}
            </Card>
            <Card title="Log Watch" sub={data?.logs?.path || "recent errors"} pad={false}>
              {(data?.logs?.errors || []).length ? (
                <div className="divide-y divide-border">
                  {(data?.logs?.errors || []).slice(-8).map((line, index) => (
                    <div key={`${line}-${index}`} className="px-[var(--pad)] py-2 font-mono text-[11px] text-danger">
                      {compact(line, 180)}
                    </div>
                  ))}
                </div>
              ) : (
                <Empty icon="logs">No recent errors.</Empty>
              )}
            </Card>
            <Card title="Plugin Loadout" sub={`${pluginHost.manifests.length} dashboard manifests`} pad={false}>
              {pluginHost.loading && <Loading label="Loading plugins..." />}
              {!pluginHost.loading && !pluginHost.manifests.length && <Empty icon="plugins">No dashboard plugins registered.</Empty>}
              {pluginHost.manifests.slice(0, 10).map((manifest) => {
                const client = pluginHost.pluginStatuses.get(manifest.name);
                const bad = client?.asset_status === "error" || manifest.asset_errors?.length;
                return (
                  <HealthLine
                    key={manifest.name}
                    icon="plugins"
                    label={manifest.label || manifest.title || manifest.name}
                    value={client?.asset_status || manifest.ui_asset_status?.status || manifest.version || "manifest"}
                    ok={!bad}
                    to="/plugins"
                  />
                );
              })}
            </Card>
          </div>
        )}
      </div>

      <PluginSlot name="dashboard:bottom" className="mt-[var(--gap)]" />
    </>
  );
}

function SessionLine({ session }: { session: SessionRow }) {
  return (
    <Link
      to={`/sessions?id=${encodeURIComponent(session.id)}`}
      className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 border-b border-border px-[var(--pad)] py-3 last:border-0 hover:bg-surface-2/60"
    >
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold text-text">{compact(session.title || session.id, 72)}</span>
        <span className="mt-0.5 block truncate text-xs text-faint">
          {session.message_count || 0} messages / {session.model || session.surface || "chat"}
        </span>
      </span>
      <span className="self-center text-xs text-faint">{ago(session.updated_at)}</span>
    </Link>
  );
}

function RunLine({ run, detail = false }: { run: RunRow; detail?: boolean }) {
  const active = ACTIVE_STATUSES.has(statusKey(run.status));
  const bad = !!run.error || BAD_STATUSES.has(statusKey(run.status));
  const to = run.session_id ? `/sessions?id=${encodeURIComponent(run.session_id)}` : "/analytics";
  return (
    <Link
      to={to}
      className="grid grid-cols-[auto_minmax(0,1fr)_auto] gap-3 border-b border-border px-[var(--pad)] py-3 last:border-0 hover:bg-surface-2/60"
    >
      <span className={cn("mt-1 h-2.5 w-2.5 rounded-full", bad ? "bg-danger" : active ? "bg-info" : "bg-success")} />
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold text-text">{compact(run.title || run.preview || run.id, 76)}</span>
        <span className="mt-0.5 block truncate text-xs text-faint">{run.surface || run.kind || "run"} / {elapsed(run)} / {ago(run.updated_at || run.started_at)}</span>
        {detail && (run.summary || run.error) && (
          <span className={cn("mt-1 block truncate text-xs", run.error ? "text-danger" : "text-dim")}>{compact(run.error || run.summary, 150)}</span>
        )}
      </span>
      <Badge status={run.status || "done"}>{run.status || "done"}</Badge>
    </Link>
  );
}

function ActivityLine({ item }: { item: ActivityRow }) {
  const active = ACTIVE_STATUSES.has(statusKey(item.status)) || statusKey(item.status) === "running";
  const bad = !!item.last_error || BAD_STATUSES.has(statusKey(item.status));
  const to = item.session_id ? `/sessions?id=${encodeURIComponent(item.session_id)}` : "/analytics";
  const primary = item.active_tool
    ? `tool ${item.active_tool}`
    : item.provider || item.model
      ? [item.provider, item.model].filter(Boolean).join("/")
      : item.phase || item.surface || "activity";
  const detail = [
    item.surface || "agent",
    item.phase || item.status || "running",
    item.iteration ? `step ${item.iteration}/${item.max_iterations || "?"}` : "",
    formatMs(item.active_elapsed_ms || item.elapsed_ms || 0),
  ].filter(Boolean).join(" / ");
  return (
    <Link
      to={to}
      className="grid grid-cols-[auto_minmax(0,1fr)_auto] gap-3 border-b border-border px-[var(--pad)] py-3 last:border-0 hover:bg-surface-2/60"
    >
      <span className={cn("mt-1 h-2.5 w-2.5 rounded-full", bad ? "bg-danger" : active ? "bg-info" : "bg-success")} />
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold text-text">{compact(item.title || item.prompt_preview || primary, 76)}</span>
        <span className="mt-0.5 block truncate text-xs text-faint">{detail}</span>
        {(item.last_text_preview || item.last_error) && (
          <span className={cn("mt-1 block truncate text-xs", item.last_error ? "text-danger" : "text-dim")}>
            {compact(item.last_error || item.last_text_preview, 150)}
          </span>
        )}
      </span>
      <Badge status={item.status || (active ? "running" : "done")}>{item.status || (active ? "running" : "done")}</Badge>
    </Link>
  );
}

function QuickLink({ to, icon, label }: { to: string; icon: string; label: string }) {
  return (
    <Link to={to} className="flex items-center gap-2 rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-2 text-sm text-dim hover:text-text">
      <Icon name={icon} size={15} className="text-primary" />
      <span className="truncate font-mono text-xs font-semibold">{label}</span>
    </Link>
  );
}

function HealthLine({ icon, label, value, ok, to }: { icon: string; label: string; value: string; ok: boolean; to: string }) {
  return (
    <Link to={to} className="flex items-center justify-between gap-3 border-b border-border px-[var(--pad)] py-3 last:border-0 hover:bg-surface-2/60">
      <span className="flex min-w-0 items-center gap-2">
        <Icon name={icon} size={15} className={ok ? "text-success" : "text-warning"} />
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium text-text">{label}</span>
          <span className="block truncate text-xs text-faint">{value}</span>
        </span>
      </span>
      <span className={cn("h-2 w-2 shrink-0 rounded-full", ok ? "bg-success" : "bg-warning")} />
    </Link>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-[var(--radius)] border border-border bg-surface-2 px-2 py-2">
      <div className="truncate text-sm font-semibold text-text">{value}</div>
      <div className="truncate font-mono text-[10px] uppercase tracking-wide text-faint">{label}</div>
    </div>
  );
}

function statusKey(value: unknown): string {
  return String(value || "").toLowerCase();
}

function byUpdated(a: SessionRow, b: SessionRow): number {
  return timeValue(b.updated_at) - timeValue(a.updated_at);
}

function byRunTime(a: RunRow, b: RunRow): number {
  return timeValue(b.updated_at || b.started_at) - timeValue(a.updated_at || a.started_at);
}

function timeValue(value: unknown): number {
  if (!value) return 0;
  const t = typeof value === "number" ? value * (value < 1e12 ? 1000 : 1) : Date.parse(String(value));
  return Number.isNaN(t) ? 0 : t;
}

function elapsedMinutes(run: RunRow): number {
  const start = timeValue(run.started_at || run.updated_at);
  if (!start) return 0;
  const stop = ACTIVE_STATUSES.has(statusKey(run.status)) ? Date.now() : timeValue(run.updated_at) || Date.now();
  return Math.max(0, (stop - start) / 60000);
}

function elapsed(run: RunRow): string {
  const minutes = elapsedMinutes(run);
  if (minutes < 1) return "under 1m";
  if (minutes < 60) return `${Math.floor(minutes)}m`;
  const hours = minutes / 60;
  if (hours < 24) return `${hours.toFixed(hours < 10 ? 1 : 0)}h`;
  return `${Math.floor(hours / 24)}d`;
}

function formatMs(ms: number): string {
  if (!ms) return "";
  const seconds = Math.max(1, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = minutes / 60;
  return `${hours.toFixed(hours < 10 ? 1 : 0)}h`;
}
