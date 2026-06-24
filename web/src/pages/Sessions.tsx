import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, del, post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { ago, compact } from "../lib/format";
import { cleanTranscript, type RawMessage } from "../lib/transcript";
import { Badge, Button, Card, Empty, Input, Loading, MetricStrip, PageHeader, SectionTitle, Segmented, toast } from "../components/ui";
import { Icon } from "../components/icons";
import { PluginSlot } from "../plugins/host";

interface SessionRow { id: string; title?: string; updated_at?: string; message_count?: number; archived?: boolean; surface?: string; model?: string }
interface SessionStats { session_count?: number; message_count?: number; empty_sessions?: number; archived_sessions?: number }
interface GatewayStatus { channels?: string[]; active_sessions?: number; last_update?: string; gateway_running?: boolean; gateway_state?: string }
interface TimelineItem {
  id: string;
  kind: string;
  label: string;
  status?: string;
  depth?: number;
  duration_ms?: number;
  provider?: string;
  model?: string;
  tool_name?: string;
  preview?: string;
}
interface SessionDetail {
  messages?: RawMessage[];
  title?: string;
  timeline?: { items?: TimelineItem[]; summary?: { total?: number; errors?: number; tools?: number; provider_calls?: number } };
  lineage?: {
    root_id?: string;
    parent?: LineageNode | null;
    current?: LineageNode;
    children?: LineageNode[];
    descendants?: LineageNode[];
    warnings?: { code?: string; session_id?: string; parent_id?: string; at?: string }[];
    summary?: { ancestor_count?: number; child_count?: number; descendant_count?: number; warning_count?: number };
  };
}
interface LineageNode {
  id: string;
  title?: string;
  parent_id?: string;
  relation?: string;
  depth?: number;
  message_count?: number;
  origin?: { kind?: string; platform?: string; cron_job_id?: string; background_task_id?: string; subagent_id?: string };
}

type Tab = "overview" | "history";

export function Sessions() {
  const { data, loading, error, reload } = useApi<SessionRow[]>("sessions");
  const stats = useApi<SessionStats>("sessions/stats");
  const gateway = useApi<GatewayStatus>("gateway/status");
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>("overview");
  const [q, setQ] = useState("");
  const [openId, setOpenId] = useState("");
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const detailSeq = useRef(0);

  useEffect(() => {
    const id = searchParams.get("id");
    if (id) {
      setTab("history");
      void open(id);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data || []).filter((s) =>
      !needle || (s.title || s.id).toLowerCase().includes(needle) || s.id.toLowerCase().includes(needle));
  }, [data, q]);

  const recent = [...(data || [])].sort((a, b) =>
    String(b.updated_at || "").localeCompare(String(a.updated_at || ""))).slice(0, 8);
  const activeStore = Math.max(0, (stats.data?.session_count || data?.length || 0) - (stats.data?.empty_sessions || 0));
  const archived = stats.data?.archived_sessions || (data || []).filter((s) => s.archived).length;
  const platformNames = gateway.data?.channels?.length ? gateway.data.channels : ["local"];

  async function open(id: string) {
    const seq = ++detailSeq.current;
    setOpenId(id);
    setDetail(null);
    try {
      const next = await api<SessionDetail>(`session?id=${encodeURIComponent(id)}`);
      if (seq === detailSeq.current) setDetail(next);
    }
    catch (e) { if (seq === detailSeq.current) toast(String(e), "err"); }
  }
  async function remove(id: string) {
    if (!window.confirm(`Delete session "${id}"?`)) return;
    try {
      await del(`sessions/${encodeURIComponent(id)}`);
      toast("Deleted");
      if (openId === id) setOpenId("");
      reload();
      stats.reload();
    } catch (e) { toast(String(e), "err"); }
  }
  async function pruneEmpty() {
    if (!window.confirm("Delete empty sessions?")) return;
    try {
      const r = await post<{ count?: number }>("sessions/prune-empty", { dry_run: false });
      toast(`Deleted ${r.count || 0} empty session${r.count === 1 ? "" : "s"}`);
      reload(); stats.reload();
    } catch (e) { toast(String(e), "err"); }
  }
  async function pruneOld() {
    if (!window.confirm("Delete sessions older than 30 days?")) return;
    try {
      const r = await post<{ count?: number }>("sessions/prune", { older_than_days: 30 });
      toast(`Deleted ${r.count || 0} old session${r.count === 1 ? "" : "s"}`);
      reload(); stats.reload();
    } catch (e) { toast(String(e), "err"); }
  }

  const turns = detail ? cleanTranscript(detail.messages || []) : [];
  const timeline = (detail?.timeline?.items || []).slice(0, 12);
  const lineage = detail?.lineage;
  const lineageChildren = (lineage?.children || []).slice(0, 5);

  return (
    <>
      <PageHeader
        title="Sessions"
        sub={stats.data ? `${stats.data.session_count || 0} sessions / ${stats.data.message_count || 0} messages` : "Conversation history"}
        actions={<Button icon="refresh" onClick={() => { reload(); stats.reload(); gateway.reload(); }}>Refresh</Button>}
      />
      <PluginSlot name="sessions:top" className="mt-[var(--gap)]" />

      <MetricStrip items={[
        { label: "total", value: stats.data?.session_count || data?.length || 0 },
        { label: "active in store", value: activeStore, tone: "success" },
        { label: "archived", value: archived },
        { label: "messages", value: stats.data?.message_count || 0 },
      ]} />

      <div className="mt-[var(--gap)]">
        <Segmented<Tab>
          value={tab}
          onChange={setTab}
          items={[
            { value: "overview", label: "Overview" },
            { value: "history", label: "History", count: rows.length },
          ]}
        />
      </div>

      {error && <Card className="mt-[var(--gap)]"><Empty icon="alert">Couldn't load - {error}</Empty></Card>}
      {loading && <Loading />}

      {data && tab === "overview" && (
        <div className="mt-[var(--gap)] grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="min-w-0 space-y-[var(--gap)]">
            <Card pad={false}>
              <SectionTitle icon="channels" title="Connected Platforms" sub={`${platformNames.length} route${platformNames.length === 1 ? "" : "s"}`} />
              <div className="divide-y divide-border p-[var(--pad)]">
                {platformNames.map((name) => (
                  <div key={name} className="flex items-center justify-between gap-3 border border-border bg-surface-2/50 px-3 py-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <Icon name="channels" size={15} className="text-success" />
                      <div className="min-w-0">
                        <div className="truncate font-mono text-sm text-text">{name}</div>
                        <div className="text-[11px] text-faint">Last update: {ago(gateway.data?.last_update)}</div>
                      </div>
                    </div>
                    <Badge tone={gateway.error ? "warning" : "success"}>{gateway.error ? "unknown" : "connected"}</Badge>
                  </div>
                ))}
              </div>
            </Card>

            <Card pad={false}>
              <SectionTitle icon="cron" title="Recent Sessions" sub={`${recent.length} latest conversations`} />
              {!recent.length && <Empty icon="sessions">No sessions.</Empty>}
              {recent.map((s) => (
                <SessionLine key={s.id} session={s} onOpen={open} onRemove={remove} />
              ))}
            </Card>
          </div>

          <aside className="space-y-[var(--gap)]">
            <Card title="Maintenance" sub={`${stats.data?.empty_sessions || 0} empty sessions`}>
              <div className="grid gap-2">
                <Button icon="trash" onClick={pruneEmpty}>Delete empty</Button>
                <Button icon="trash" onClick={pruneOld}>Delete older than 30d</Button>
              </div>
            </Card>
            <Card title="Gateway Store" sub={gateway.data?.gateway_state || (gateway.data?.gateway_running ? "running" : "offline")}>
              <div className="space-y-2 text-sm">
                <Info label="Active sessions" value={String(gateway.data?.active_sessions ?? 0)} />
                <Info label="Channels" value={platformNames.join(", ")} />
                <Info label="Visible rows" value={String(data.length)} />
              </div>
            </Card>
          </aside>
        </div>
      )}

      {data && tab === "history" && (
        <div className="mt-[var(--gap)] space-y-[var(--gap)]">
          <Card>
            <div className="flex flex-wrap items-center gap-2">
              <Input value={q} placeholder="Filter sessions..." onChange={(e) => setQ(e.target.value)} className="max-w-sm" />
              <Button icon="trash" onClick={pruneEmpty}>Delete empty</Button>
              <Button icon="trash" onClick={pruneOld}>Delete 30d</Button>
            </div>
          </Card>
          <Card pad={false}>
            {!rows.length && <Empty icon="sessions">No sessions match.</Empty>}
            {rows.map((s) => <SessionLine key={s.id} session={s} onOpen={open} onRemove={remove} />)}
          </Card>
        </div>
      )}

      {openId && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm" onClick={() => setOpenId("")}>
          <div className="flex h-full w-full max-w-3xl flex-col border-l border-border bg-bg" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div className="min-w-0">
                <div className="truncate font-mono text-sm font-semibold text-text">Session transcript</div>
                <div className="truncate font-mono text-xs text-faint">{openId}</div>
              </div>
              <div className="flex items-center gap-2">
                <Link to={`/chat?id=${encodeURIComponent(openId)}`}><Button sm icon="chat">Open</Button></Link>
                <button onClick={() => setOpenId("")} className="text-faint hover:text-text"><Icon name="x" size={18} /></button>
              </div>
            </div>
            <div className="scroll-thin flex-1 space-y-3 overflow-y-auto p-4">
              {!detail && <Loading />}
              {lineage?.current && (
                <div className="border border-border bg-surface-2/55 p-3">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <div className="font-mono text-xs font-semibold uppercase text-faint">Lineage</div>
                    <div className="flex flex-wrap gap-1.5">
                      <Badge tone="neutral">{lineage.summary?.ancestor_count || 0} ancestors</Badge>
                      <Badge tone="info">{lineage.summary?.descendant_count || 0} descendants</Badge>
                      {!!lineage.summary?.warning_count && <Badge tone="warning">{lineage.summary.warning_count} warnings</Badge>}
                    </div>
                  </div>
                  <div className="grid gap-2 md:grid-cols-3">
                    <LineageMini label="Root" node={lineage.root_id === lineage.current.id ? lineage.current : undefined} id={lineage.root_id} />
                    <LineageMini label="Parent" node={lineage.parent || undefined} />
                    <LineageMini label="Current" node={lineage.current} />
                  </div>
                  {!!lineageChildren.length && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {lineageChildren.map((child) => (
                        <button
                          key={child.id}
                          onClick={() => open(child.id)}
                          className="border border-border bg-bg px-2 py-1 text-left font-mono text-[11px] text-faint hover:text-primary"
                          title={child.title || child.id}
                        >
                          {compact(child.title || child.id, 30)}
                        </button>
                      ))}
                    </div>
                  )}
                  {!!lineage.warnings?.length && (
                    <div className="mt-2 space-y-1">
                      {lineage.warnings.slice(0, 3).map((warning, i) => (
                        <div key={`${warning.code}-${i}`} className="font-mono text-[11px] text-warning">
                          {warning.code}: {warning.session_id || warning.parent_id || warning.at || ""}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {!!timeline.length && (
                <div className="border border-border bg-surface-2/55 p-3">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <div className="font-mono text-xs font-semibold uppercase text-faint">Trace timeline</div>
                    <div className="flex flex-wrap gap-1.5">
                      <Badge tone="neutral">{detail?.timeline?.summary?.total || timeline.length} events</Badge>
                      {!!detail?.timeline?.summary?.errors && <Badge tone="danger">{detail.timeline.summary.errors} errors</Badge>}
                      {!!detail?.timeline?.summary?.tools && <Badge tone="info">{detail.timeline.summary.tools} tools</Badge>}
                    </div>
                  </div>
                  <div className="space-y-2">
                    {timeline.map((item) => (
                      <div
                        key={item.id}
                        className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-2 border-l border-border pl-2 text-xs"
                        style={{ marginLeft: `${Math.min(Number(item.depth || 0), 4) * 12}px` }}
                      >
                        <Badge tone={item.status === "error" ? "danger" : item.kind === "tool" ? "info" : item.kind === "message" ? "neutral" : "primary"}>
                          {item.kind}
                        </Badge>
                        <div className="min-w-0">
                          <div className="truncate font-mono text-text">{item.label}</div>
                          {(item.preview || item.provider || item.model) && (
                            <div className="truncate text-faint">
                              {item.preview || [item.provider, item.model].filter(Boolean).join(" / ")}
                            </div>
                          )}
                        </div>
                        <div className="font-mono text-faint">{item.duration_ms || 0}ms</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {detail && !turns.length && <Empty icon="chat">No conversation turns.</Empty>}
              {turns.map((t, i) => (
                <div key={i} className={t.role === "user" ? "flex justify-end" : ""}>
                  <div className={t.role === "user"
                    ? "max-w-[85%] whitespace-pre-wrap break-words border border-primary/35 bg-primary/12 px-3 py-2 text-sm text-text"
                    : "max-w-[90%] whitespace-pre-wrap break-words text-sm text-text"}>
                    {t.role === "bot" && <Badge tone="neutral">assistant</Badge>}
                    <div className={t.role === "bot" ? "mt-1" : ""}>{t.text}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      <PluginSlot name="sessions:bottom" className="mt-[var(--gap)]" />
    </>
  );
}

function LineageMini({ label, node, id }: { label: string; node?: LineageNode; id?: string }) {
  const displayId = node?.id || id || "";
  const title = node?.title || displayId || "-";
  const kind = node?.origin?.kind || node?.relation || "";
  return (
    <div className="min-w-0 border border-border bg-bg/60 px-2 py-2">
      <div className="text-[10px] uppercase text-faint">{label}</div>
      <div className="truncate font-mono text-xs text-text">{compact(title, 34)}</div>
      <div className="mt-0.5 flex min-w-0 items-center gap-1.5">
        {kind && <Badge tone="neutral">{kind}</Badge>}
        <span className="truncate font-mono text-[10px] text-faint">{displayId || "none"}</span>
      </div>
    </div>
  );
}

function SessionLine({ session, onOpen, onRemove }: {
  session: SessionRow;
  onOpen: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 border-b border-border px-[var(--pad)] py-3 last:border-0 hover:bg-surface-2/45">
      <button onClick={() => onOpen(session.id)} className="min-w-0 text-left">
        <div className="truncate font-mono text-sm text-text">{compact(session.title || session.id, 70)}</div>
        <div className="mt-0.5 truncate text-xs text-dim">
          {session.model ? `${session.model} / ` : ""}{session.message_count || 0} msgs / {ago(session.updated_at)}
        </div>
        {session.id && <div className="mt-1 truncate font-mono text-[10px] text-faint">{session.surface || "local"} / {session.id}</div>}
      </button>
      <div className="flex items-center gap-2">
        <Link to={`/chat?id=${encodeURIComponent(session.id)}`} className="grid h-8 w-8 place-items-center border border-border text-faint hover:text-primary" title="Resume in chat">
          <Icon name="chat" size={14} />
        </Link>
        <button onClick={() => onRemove(session.id)} className="grid h-8 w-8 place-items-center border border-border text-faint hover:text-danger" title="Delete">
          <Icon name="trash" size={14} />
        </button>
      </div>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 border-b border-border pb-1 last:border-0">
      <span className="text-faint">{label}</span>
      <span className="truncate font-mono text-text">{value || "-"}</span>
    </div>
  );
}
