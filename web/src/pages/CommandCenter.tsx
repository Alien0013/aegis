import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { ago, compact, num, usd } from "../lib/format";
import { cn } from "../lib/cn";
import { Badge, Button, Card, Empty, Loading, PageHeader, Stat } from "../components/ui";
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
interface AgentRow {
  id: string;
  kind?: string;
  type?: string;
  status?: string;
  task?: string;
  active_runs?: number;
}
interface ToolRow {
  name?: string;
  tool?: string;
  enabled?: boolean;
  available?: boolean;
  unavailable_reason?: string;
  description?: string;
}
interface Cockpit {
  status?: Status;
  analytics?: { total_cost?: number; total_tokens?: number; balance?: unknown };
  sessions?: SessionRow[];
  runs?: { runs?: RunRow[]; summary?: { total?: number } };
  traces?: { traces?: unknown[]; summary?: { total?: number; errors?: number } };
  agents?: { agents?: AgentRow[]; active_runs?: AgentRow[] };
  tools?: { toolsets?: string[]; disabled?: string[]; tools?: ToolRow[] };
  memory?: { memory_entries?: string[]; user_entries?: string[] };
  projects?: { projects?: Array<{ name?: string; path?: string; branch?: string; current?: boolean; marker?: string; run_count?: number }> };
  review?: { available?: boolean; dirty?: boolean; branch?: string; files?: unknown[]; diff_stat?: string; note?: string };
  system?: { platform?: string; python?: string; disk_free_gb?: number; disk_total_gb?: number; aegis_home?: string };
  keys?: Array<{ key?: string; present?: boolean; source?: string }>;
  plugins?: { enabled?: string[]; disabled?: string[] };
  mcp?: Record<string, unknown>;
  profiles?: unknown[];
  logs?: { lines?: string[]; errors?: string[]; path?: string };
}

const ACTIVE_STATUSES = new Set(["active", "running", "queued", "pending", "in_progress", "working"]);
const BAD_STATUSES = new Set(["error", "failed", "blocked", "denied"]);

export function CommandCenter() {
  const [data, setData] = useState<Cockpit | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [selectedRunId, setSelectedRunId] = useState("");
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
  const sessions = useMemo(() => [...(data?.sessions || [])].sort(byUpdated).slice(0, 9), [data?.sessions]);
  const runs = useMemo(() => [...(data?.runs?.runs || [])].sort(byRunTime), [data?.runs?.runs]);
  const activeRuns = runs.filter((run) => ACTIVE_STATUSES.has(statusKey(run.status)));
  const problemRuns = runs.filter((run) => run.error || BAD_STATUSES.has(statusKey(run.status)));
  const agents = data?.agents?.agents || [];
  const activeAgents = data?.agents?.active_runs || agents.filter((agent) => ACTIVE_STATUSES.has(statusKey(agent.status)));
  const tools = data?.tools?.tools || [];
  const enabledTools = tools.filter((tool) => tool.enabled).length;
  const availableTools = tools.filter((tool) => tool.available).length;
  const disabledToolsets = data?.tools?.disabled || [];
  const missingKeys = (data?.keys || []).filter((key) => !key.present).length;
  const projects = data?.projects?.projects || [];
  const currentProject = projects.find((project) => project.current) || projects[0];
  const selectedRun = runs.find((run) => run.id === selectedRunId) || activeRuns[0] || runs[0];
  const pluginStatuses = Array.from(pluginHost.pluginStatuses.values());
  const loadedPlugins = pluginStatuses.filter((plugin) => plugin.registered || plugin.asset_status === "loaded").length;
  const pluginProblems = pluginStatuses.filter((plugin) => plugin.asset_status === "error" || plugin.errors?.length || plugin.css_errors?.length).length;
  const enabledPlugins = data?.plugins?.enabled?.length ?? pluginHost.manifests.length;
  const gateway = status.gateway_state || (status.gateway_running ? "running" : "offline");
  const ready = !status.provider_error && !error;
  const healthItems = [
    { label: "Provider", ok: !status.provider_error, detail: status.provider || "not configured" },
    { label: "Gateway", ok: gateway === "running", detail: gateway },
    { label: "Tools", ok: availableTools > 0 || !!status.tools, detail: `${availableTools || status.tools || 0}/${status.tools || tools.length || 0} available` },
    { label: "Secrets", ok: missingKeys === 0, detail: missingKeys ? `${missingKeys} missing` : "configured" },
    { label: "Plugins", ok: pluginProblems === 0, detail: `${enabledPlugins} enabled${pluginProblems ? `, ${pluginProblems} needs attention` : ""}` },
  ];

  if (loading && !data) return <><PageHeader title="Command Center" /><Loading /></>;

  return (
    <>
      <PageHeader
        title="Command Center"
        sub={`${currentProject?.name || "Local workspace"} / ${status.model || "model unavailable"}${updatedAt ? ` / refreshed ${ago(updatedAt.toISOString())}` : ""}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Link to="/app"><Button icon="plus" variant="primary">New chat</Button></Link>
            <Link to="/chat"><Button icon="terminal">Terminal</Button></Link>
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

      <div className="grid grid-cols-2 gap-[var(--gap)] md:grid-cols-4 xl:grid-cols-6">
        <Stat label="Active work" value={num(activeRuns.length || activeAgents.length)} icon="activity" tone={activeRuns.length || activeAgents.length ? "info" : "success"} />
        <Stat label="Sessions" value={num(status.sessions ?? sessions.length)} icon="sessions" tone="primary" />
        <Stat label="Tools" value={`${num(enabledTools || status.tools)}/${num(status.tools || tools.length)}`} icon="tools" tone="info" />
        <Stat label="Plugins" value={num(enabledPlugins)} icon="plugins" tone={pluginProblems ? "warning" : "success"} />
        <Stat label="Cost" value={usd(data?.analytics?.total_cost)} icon="analytics" tone="warning" />
        <Stat label="Trace errors" value={num(data?.traces?.summary?.errors || problemRuns.length)} icon="alert" tone={problemRuns.length ? "danger" : "neutral"} />
      </div>

      <div className="mt-[var(--gap)] grid gap-[var(--gap)] 2xl:grid-cols-[280px_minmax(0,1fr)_380px]">
        <aside className="min-w-0 space-y-[var(--gap)]">
          <Card title="Launch Pad" sub="fast paths" pad={false}>
            <div className="grid grid-cols-2 gap-px bg-border p-px">
              <Shortcut to="/app" icon="chat" label="Chat app" detail="desktop shell" />
              <Shortcut to="/chat" icon="terminal" label="Terminal" detail="streaming CLI" />
              <Shortcut to="/sessions" icon="sessions" label="Sessions" detail={`${sessions.length} recent`} />
              <Shortcut to="/plugins" icon="plugins" label="Plugins" detail={`${enabledPlugins} enabled`} />
              <Shortcut to="/models" icon="models" label="Models" detail={status.provider || "provider"} />
              <Shortcut to="/logs" icon="logs" label="Logs" detail={(data?.logs?.errors || []).length ? "errors found" : "quiet"} />
            </div>
          </Card>

          <Card title="Runtime" sub={ready ? "ready" : "attention"}>
            <div className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate font-mono text-sm font-semibold text-text">{status.model || "model unavailable"}</div>
                  <div className="truncate text-xs text-faint">{status.provider || "provider not configured"}</div>
                </div>
                <Badge status={ready ? "ready" : "error"}>{ready ? "ready" : "error"}</Badge>
              </div>
              {status.provider_error && (
                <div className="rounded-[var(--radius)] border border-danger/35 bg-danger/10 p-2 text-xs text-danger">
                  {compact(status.provider_error, 180)}
                </div>
              )}
              <div className="grid grid-cols-2 gap-2">
                <Mini label="context" value={status.context_length ? num(status.context_length) : "-"} />
                <Mini label="exec" value={status.exec_mode || "-"} />
                <Mini label="reasoning" value={status.reasoning_effort || "off"} />
                <Mini label="busy" value={status.busy_mode || "-"} />
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(status.toolsets || data?.tools?.toolsets || []).slice(0, 8).map((toolset) => <Badge key={toolset} tone="neutral">{toolset}</Badge>)}
                {disabledToolsets.slice(0, 3).map((toolset) => <Badge key={toolset} tone="warning">{toolset} off</Badge>)}
              </div>
            </div>
          </Card>

          <Card title="Recent Sessions" sub="resume work" pad={false}>
            {!sessions.length && <Empty icon="sessions">No sessions yet.</Empty>}
            {sessions.slice(0, 6).map((session) => (
              <Link
                key={session.id}
                to={`/app?id=${encodeURIComponent(session.id)}`}
                className="flex items-center justify-between gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0 hover:bg-surface-2/60"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-text">{compact(session.title || session.id, 42)}</span>
                  <span className="block truncate text-xs text-faint">{session.message_count || 0} msg / {session.model || session.surface || "chat"}</span>
                </span>
                <span className="shrink-0 text-xs text-faint">{ago(session.updated_at)}</span>
              </Link>
            ))}
          </Card>
        </aside>

        <main className="min-w-0 space-y-[var(--gap)]">
          <Card title="Active Work" sub={`${activeRuns.length || activeAgents.length} live / ${runs.length} recent`} actions={<Link to="/analytics" className="text-xs text-primary hover:underline">Analytics</Link>} pad={false}>
            <div className="grid min-h-[320px] lg:grid-cols-[minmax(0,1fr)_260px]">
              <div className="border-b border-border lg:border-b-0 lg:border-r">
                {(activeRuns.length ? activeRuns : runs).slice(0, 8).map((run, index) => (
                  <RunTimelineRow
                    key={run.id}
                    run={run}
                    selected={selectedRun?.id === run.id}
                    first={index === 0}
                    onSelect={() => setSelectedRunId(run.id)}
                  />
                ))}
                {!runs.length && (
                  <div className="p-[var(--pad)]">
                    <Empty icon="activity">No run history yet. Start chat or a schedule to populate this lane.</Empty>
                  </div>
                )}
              </div>
              <div className="p-[var(--pad)]">
                <div className="font-mono text-[10px] uppercase tracking-wide text-faint">Long Task Monitor</div>
                <div className="mt-3 space-y-3">
                  {(activeRuns.length ? activeRuns : runs.slice(0, 4)).map((run) => (
                    <TaskMeter key={run.id} run={run} />
                  ))}
                  {!runs.length && <div className="text-sm text-faint">No queued or running work.</div>}
                </div>
              </div>
            </div>
          </Card>

          <div className="grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.85fr)]">
            <Card title="Project Posture" sub={currentProject?.path || "workspace"}>
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-text">{currentProject?.name || "Current workspace"}</div>
                    <div className="truncate text-xs text-faint">{currentProject?.marker || data?.review?.note || "local checkout"}</div>
                  </div>
                  <Badge tone={data?.review?.dirty ? "warning" : "success"}>{data?.review?.dirty ? "dirty" : "clean"}</Badge>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <Mini label="branch" value={data?.review?.branch || currentProject?.branch || "-"} />
                  <Mini label="changed" value={num(data?.review?.files?.length || 0)} />
                  <Mini label="runs" value={num(currentProject?.run_count || 0)} />
                </div>
                {data?.review?.diff_stat && (
                  <pre className="scroll-thin max-h-24 overflow-auto rounded-[var(--radius)] border border-border bg-surface-2 p-2 font-mono text-[11px] text-dim">{data.review.diff_stat}</pre>
                )}
              </div>
            </Card>

            <Card title="Tools And Plugins" sub={`${availableTools || status.tools || 0} tools / ${enabledPlugins} plugins`} pad={false}>
              <div className="divide-y divide-border">
                <StatusLine icon="tools" label="Available tools" value={`${availableTools}/${tools.length || status.tools || 0}`} ok={availableTools > 0 || !!status.tools} />
                <StatusLine icon="plugins" label="Dashboard plugins" value={`${loadedPlugins}/${pluginHost.manifests.length || enabledPlugins}`} ok={!pluginProblems} />
                <StatusLine icon="mcp" label="MCP servers" value={num(Object.keys(data?.mcp || {}).length)} ok={Object.keys(data?.mcp || {}).length > 0} />
                <StatusLine icon="keys" label="Keys" value={missingKeys ? `${missingKeys} missing` : "ready"} ok={missingKeys === 0} />
              </div>
            </Card>
          </div>
        </main>

        <aside className="min-w-0 space-y-[var(--gap)]">
          <PreviewPanel run={selectedRun} />

          <Card title="Health Rail" sub="runtime checks">
            <div className="space-y-2">
              {healthItems.map((item) => (
                <HealthLine key={item.label} label={item.label} ok={item.ok} detail={item.detail} />
              ))}
            </div>
          </Card>

          <Card title="Plugin Loadout" sub={pluginHost.error || `${pluginHost.manifests.length} dashboard manifests`} pad={false}>
            {pluginHost.loading && <Loading label="Loading plugins..." />}
            {!pluginHost.loading && !pluginHost.manifests.length && <Empty icon="plugins">No dashboard plugins registered.</Empty>}
            {pluginHost.manifests.slice(0, 6).map((manifest) => {
              const client = pluginHost.pluginStatuses.get(manifest.name);
              const bad = client?.asset_status === "error" || manifest.asset_errors?.length;
              return (
                <div key={manifest.name} className="flex items-center justify-between gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-text">{manifest.label || manifest.title || manifest.name}</div>
                    <div className="truncate text-xs text-faint">{client?.asset_status || manifest.ui_asset_status?.status || manifest.version || "manifest"}</div>
                  </div>
                  <Badge tone={bad ? "danger" : client?.registered ? "success" : "neutral"}>{bad ? "error" : client?.registered ? "ready" : "seen"}</Badge>
                </div>
              );
            })}
          </Card>

          <Card title="Preview Timeline" sub={selectedRun ? compact(selectedRun.id, 28) : "latest signals"} pad={false}>
            <div className="divide-y divide-border">
              {(selectedRun ? [selectedRun, ...runs.filter((run) => run.id !== selectedRun.id)] : runs).slice(0, 5).map((run) => (
                <button
                  key={run.id}
                  onClick={() => setSelectedRunId(run.id)}
                  className="flex w-full items-center gap-3 px-[var(--pad)] py-2.5 text-left hover:bg-surface-2/60"
                >
                  <span className={cn("h-2 w-2 shrink-0 rounded-full", dotClass(run.status, !!run.error))} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-text">{compact(run.title || run.preview || run.id, 44)}</span>
                    <span className="block truncate text-xs text-faint">{run.surface || run.kind || "run"} / {elapsed(run)}</span>
                  </span>
                  <Icon name="chevronRight" size={14} className="shrink-0 text-faint" />
                </button>
              ))}
              {!runs.length && <div className="p-[var(--pad)] text-sm text-faint">No timeline events yet.</div>}
            </div>
          </Card>

          <Card title="Log Watch" sub={data?.logs?.path || "recent errors"}>
            {(data?.logs?.errors || []).length ? (
              <div className="space-y-2">
                {(data?.logs?.errors || []).slice(-4).map((line, index) => (
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

      <PluginSlot name="dashboard:bottom" className="mt-[var(--gap)]" />
    </>
  );
}

function Shortcut({ to, icon, label, detail }: { to: string; icon: string; label: string; detail: string }) {
  return (
    <Link to={to} className="min-w-0 bg-surface px-3 py-3 text-dim transition hover:bg-surface-2 hover:text-text">
      <Icon name={icon} size={16} className="mb-2 text-primary" />
      <div className="truncate text-sm font-semibold text-text">{label}</div>
      <div className="truncate text-[11px] text-faint">{detail}</div>
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

function StatusLine({ icon, label, value, ok }: { icon: string; label: string; value: string; ok: boolean }) {
  return (
    <Link to={icon === "plugins" ? "/plugins" : icon === "tools" ? "/tools" : icon === "keys" ? "/env" : "/mcp"} className="flex items-center justify-between gap-3 px-[var(--pad)] py-3 hover:bg-surface-2/60">
      <span className="flex min-w-0 items-center gap-2">
        <Icon name={icon} size={15} className={ok ? "text-success" : "text-warning"} />
        <span className="truncate text-sm text-text">{label}</span>
      </span>
      <span className="shrink-0 font-mono text-xs text-faint">{value}</span>
    </Link>
  );
}

function RunTimelineRow({
  run,
  selected,
  first,
  onSelect,
}: {
  run: RunRow;
  selected: boolean;
  first: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className={cn(
        "grid w-full grid-cols-[26px_minmax(0,1fr)_auto] gap-3 px-[var(--pad)] py-3 text-left transition",
        selected ? "bg-primary/10" : "hover:bg-surface-2/60",
      )}
    >
      <span className="relative mt-0.5 flex justify-center">
        {!first && <span className="absolute -top-3 h-3 w-px bg-border" />}
        <span className={cn("relative z-10 h-2.5 w-2.5 rounded-full", dotClass(run.status, !!run.error))} />
        <span className="absolute top-3 h-[calc(100%+12px)] w-px bg-border" />
      </span>
      <span className="min-w-0">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-semibold text-text">{compact(run.title || run.preview || run.id, 78)}</span>
          <Badge status={run.status || "recorded"}>{run.status || "recorded"}</Badge>
        </span>
        <span className="mt-1 block truncate text-xs text-faint">{run.surface || run.kind || "run"} / {ago(run.updated_at || run.started_at)} / {elapsed(run)}</span>
        {(run.summary || run.error) && (
          <span className={cn("mt-1 block truncate text-xs", run.error ? "text-danger" : "text-dim")}>
            {compact(run.error || run.summary, 140)}
          </span>
        )}
      </span>
      <Icon name="chevronRight" size={15} className="mt-1 text-faint" />
    </button>
  );
}

function TaskMeter({ run }: { run: RunRow }) {
  const active = ACTIVE_STATUSES.has(statusKey(run.status));
  const pct = active ? Math.min(92, 28 + Math.floor(elapsedMinutes(run) * 6)) : 100;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2 text-xs">
        <span className="min-w-0 truncate text-dim">{compact(run.title || run.preview || run.id, 34)}</span>
        <span className="shrink-0 font-mono text-faint">{elapsed(run)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-[var(--radius)] bg-border">
        <div className={cn("h-full", run.error ? "bg-danger" : active ? "bg-info" : "bg-success")} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function PreviewPanel({ run }: { run?: RunRow }) {
  if (!run) {
    return (
      <Card title="Now Preview" sub="selected work">
        <div className="text-sm text-faint">Select a run to inspect its session, summary, and timeline position.</div>
      </Card>
    );
  }
  return (
    <Card
      title="Now Preview"
      sub={run.status || run.kind || "run"}
      actions={run.session_id ? <Link to={`/sessions?id=${encodeURIComponent(run.session_id)}`} className="text-xs text-primary hover:underline">Transcript</Link> : undefined}
    >
      <div className="space-y-3">
        <div>
          <div className="truncate text-sm font-semibold text-text">{compact(run.title || run.preview || run.id, 80)}</div>
          <div className="mt-1 truncate font-mono text-[11px] text-faint">{run.id}</div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Mini label="surface" value={run.surface || run.kind || "-"} />
          <Mini label="elapsed" value={elapsed(run)} />
        </div>
        {(run.summary || run.preview || run.error) && (
          <div className={cn("rounded-[var(--radius)] border p-3 text-xs leading-relaxed", run.error ? "border-danger/30 bg-danger/10 text-danger" : "border-border bg-surface-2 text-dim")}>
            {compact(run.error || run.summary || run.preview, 260)}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          {run.session_id && <Link to={`/app?id=${encodeURIComponent(run.session_id)}`}><Button sm icon="chat">Resume</Button></Link>}
          {run.session_id && <Link to={`/chat?id=${encodeURIComponent(run.session_id)}`}><Button sm icon="terminal">Terminal</Button></Link>}
          <Link to="/analytics"><Button sm icon="analytics">Trace</Button></Link>
        </div>
      </div>
    </Card>
  );
}

function HealthLine({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border pb-2 last:border-0 last:pb-0">
      <div className="min-w-0">
        <div className="text-sm font-medium text-text">{label}</div>
        <div className="truncate text-xs text-faint">{detail}</div>
      </div>
      <span className={cn("h-2 w-2 rounded-full", ok ? "bg-success" : "bg-danger")} />
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

function dotClass(status: unknown, error = false): string {
  const key = statusKey(status);
  if (error || BAD_STATUSES.has(key)) return "bg-danger";
  if (ACTIVE_STATUSES.has(key)) return "bg-info";
  if (key === "done" || key === "ok" || key === "ready" || key === "completed") return "bg-success";
  return "bg-faint";
}
