// Kanban — the agent orchestration board. Eight status columns (triage → done),
// drag a card to move it, drop on the trash to archive, "+" to add a card to a
// column, and "Nudge dispatcher" to kick the autonomous worker. Backed by
// /api/kanban (GET board, POST create/move/archive/run).

import { useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { PageHeader, Spinner, toast } from "../components/ui";
import { Icon } from "../components/icons";
import { cn } from "../lib/cn";
import { ago, compact } from "../lib/format";

interface KCard {
  id: string; title: string; body: string; assignee: string; priority: number;
  tenant: string; parents: string[]; status: string; run_id?: string;
  session_id?: string; updated_at?: string;
}
interface Board {
  order?: string[]; assignees?: string[]; tenants?: string[];
  [status: string]: KCard[] | string[] | unknown;
}

const COL: Record<string, { label: string; desc: string; dot: string }> = {
  triage: { label: "Triage", desc: "Raw ideas — a specifier will flesh out the spec", dot: "bg-warning" },
  todo: { label: "Todo", desc: "Waiting on dependencies or unassigned", dot: "bg-faint" },
  scheduled: { label: "Scheduled", desc: "Waiting on a time delay or scheduled follow-up", dot: "bg-info" },
  ready: { label: "Ready", desc: "Dependencies satisfied; assign a profile to dispatch", dot: "bg-warning" },
  in_progress: { label: "In Progress", desc: "Claimed by a worker — in-flight", dot: "bg-success" },
  blocked: { label: "Blocked", desc: "Worker asked for human input", dot: "bg-danger" },
  review: { label: "Review", desc: "Awaiting review sign-off", dot: "bg-primary" },
  done: { label: "Done", desc: "Completed", dot: "bg-info" },
  archived: { label: "Archived", desc: "Archived", dot: "bg-faint" },
};
const DEFAULT_ORDER = ["triage", "todo", "scheduled", "ready", "in_progress", "blocked", "review", "done"];

export function Kanban() {
  const [showArchived, setShowArchived] = useState(false);
  const { data, loading, error, reload } = useApi<Board>(showArchived ? "kanban?archived=1" : "kanban");
  const [q, setQ] = useState("");
  const [tenant, setTenant] = useState("");
  const [assignee, setAssignee] = useState("");
  const [lanes, setLanes] = useState(false);
  const [drag, setDrag] = useState("");
  const [overCol, setOverCol] = useState("");
  const [overTrash, setOverTrash] = useState(false);
  const [adding, setAdding] = useState("");
  const [title, setTitle] = useState("");
  const [running, setRunning] = useState(false);

  const order = data?.order || DEFAULT_ORDER;
  const assignees = data?.assignees || [];
  const tenants = data?.tenants || [];
  const cardsOf = (s: string): KCard[] => (Array.isArray(data?.[s]) ? (data![s] as KCard[]) : []);
  const total = order.reduce((n, s) => n + cardsOf(s).length, 0);

  const match = (c: KCard) =>
    (!q || c.title.toLowerCase().includes(q.toLowerCase()) || (c.body || "").toLowerCase().includes(q.toLowerCase())) &&
    (!tenant || c.tenant === tenant) &&
    (!assignee || c.assignee === assignee);

  async function move(id: string, status: string) {
    setDrag(""); setOverCol("");
    try { await post("kanban", { action: "move", id, status }); reload(); }
    catch (e) { toast(String(e), "err"); }
  }
  async function archive(id: string) {
    setDrag(""); setOverTrash(false);
    try { await post("kanban", { action: "archive", id }); reload(); }
    catch (e) { toast(String(e), "err"); }
  }
  async function create(status: string) {
    const t = title.trim();
    if (!t) { setAdding(""); return; }
    try {
      await post("kanban", { action: "create", title: t, status, assignee: assignee || "", tenant: tenant || "" });
      setTitle(""); setAdding(""); reload();
    } catch (e) { toast(String(e), "err"); }
  }
  async function nudge() {
    setRunning(true);
    try { await post("kanban", { action: "run" }); toast("Dispatcher nudged — workers will claim ready cards"); }
    catch (e) { toast(String(e), "err"); }
    finally { setTimeout(() => { setRunning(false); reload(); }, 900); }
  }
  const clearFilters = () => { setQ(""); setTenant(""); setAssignee(""); };
  const filtersOn = !!(q || tenant || assignee);

  return (
    <>
      <PageHeader
        title="Kanban"
        sub={`Agent orchestration board · ${total} task${total === 1 ? "" : "s"}`}
        actions={
          <div className="flex items-center gap-2">
            <button onClick={nudge} disabled={running}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-primary px-3 py-1.5 text-sm font-medium text-primary-fg transition hover:opacity-90 disabled:opacity-50">
              {running ? <Spinner size={14} /> : <Icon name="play" size={14} />} Nudge dispatcher
            </button>
            <button onClick={reload} title="Refresh"
              className="inline-flex items-center gap-1.5 rounded-[var(--radius)] border border-border px-2.5 py-1.5 text-sm text-dim hover:text-text">
              <Icon name="refresh" size={14} />
            </button>
          </div>
        }
      />

      {/* filters */}
      <div className="mb-[var(--gap)] flex flex-wrap items-center gap-2 rounded-[calc(var(--radius)+2px)] border border-border bg-surface px-[var(--pad)] py-2.5">
        <div className="flex items-center gap-2 rounded-[var(--radius)] border border-border bg-surface-2 px-2.5">
          <Icon name="search" size={13} className="text-faint" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter cards…"
            className="w-40 bg-transparent py-1.5 text-sm text-text outline-none placeholder:text-faint" />
        </div>
        <select value={tenant} onChange={(e) => setTenant(e.target.value)}
          className="rounded-[var(--radius)] border border-border bg-surface-2 px-2 py-1.5 text-sm text-dim outline-none">
          <option value="">All tenants</option>
          {tenants.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={assignee} onChange={(e) => setAssignee(e.target.value)}
          className="rounded-[var(--radius)] border border-border bg-surface-2 px-2 py-1.5 text-sm text-dim outline-none">
          <option value="">All profiles</option>
          {assignees.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-xs text-dim">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Show archived
        </label>
        <label className="flex items-center gap-1.5 text-xs text-dim">
          <input type="checkbox" checked={lanes} onChange={(e) => setLanes(e.target.checked)} /> Lanes by profile
        </label>
        {filtersOn && (
          <button onClick={clearFilters} className="ml-auto text-xs text-faint hover:text-text">Clear filters</button>
        )}
      </div>

      {error && <div className="rounded-[var(--radius)] border border-danger/40 bg-danger/10 p-3 text-sm text-danger">Couldn't load board — {error}</div>}
      {loading && !data && <div className="flex justify-center py-12"><Spinner size={20} /></div>}

      {data && (
        <div className="flex gap-3 overflow-x-auto pb-3">
          {order.map((s) => {
            const meta = COL[s] || { label: s, desc: "", dot: "bg-faint" };
            const cards = cardsOf(s).filter(match);
            return (
              <div
                key={s}
                onDragOver={(e) => { e.preventDefault(); setOverCol(s); }}
                onDragLeave={() => setOverCol((c) => (c === s ? "" : c))}
                onDrop={() => drag && move(drag, s)}
                className={cn(
                  "flex w-72 shrink-0 flex-col rounded-[calc(var(--radius)+2px)] border bg-surface/60",
                  overCol === s ? "border-primary/60 ring-1 ring-primary/30" : "border-border",
                )}
              >
                <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                  <span className={cn("h-2 w-2 shrink-0 rounded-full", meta.dot)} />
                  <span className="text-sm font-medium text-text">{meta.label}</span>
                  <span className="rounded-full bg-surface-2 px-1.5 text-[11px] tabular-nums text-faint">{cards.length}</span>
                  <button onClick={() => { setAdding(adding === s ? "" : s); setTitle(""); }}
                    className="ml-auto text-faint hover:text-primary" title={`Add card to ${meta.label}`}>
                    <Icon name="plus" size={15} />
                  </button>
                </div>
                <div className="px-3 pt-1 text-[11px] leading-snug text-faint">{meta.desc}</div>

                <div className="scroll-thin flex max-h-[62vh] min-h-[120px] flex-1 flex-col gap-2 overflow-y-auto p-2">
                  {adding === s && (
                    <div className="rounded-[var(--radius)] border border-primary/40 bg-surface-2 p-2">
                      <textarea autoFocus value={title} onChange={(e) => setTitle(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); create(s); } if (e.key === "Escape") setAdding(""); }}
                        rows={2} placeholder="Card title…  (Enter to add)"
                        className="w-full resize-none rounded-[var(--radius)] border border-border bg-surface px-2 py-1.5 text-sm text-text outline-none placeholder:text-faint" />
                      <div className="mt-1.5 flex justify-end gap-1.5">
                        <button onClick={() => setAdding("")} className="text-xs text-faint hover:text-text">Cancel</button>
                        <button onClick={() => create(s)} className="rounded-[var(--radius)] bg-primary px-2 py-0.5 text-xs font-medium text-primary-fg hover:opacity-90">Add</button>
                      </div>
                    </div>
                  )}
                  {cards.length === 0 && adding !== s && (
                    <div className="py-6 text-center text-xs text-faint">— no tasks —</div>
                  )}
                  {(lanes ? laneSort(cards) : cards).map((c, i, arr) => (
                    <div key={c.id}>
                      {lanes && (i === 0 || arr[i - 1].assignee !== c.assignee) && (
                        <div className="px-0.5 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wide text-faint">
                          {c.assignee || "unassigned"}
                        </div>
                      )}
                      <CardTile card={c} dragging={drag === c.id}
                        onDragStart={() => setDrag(c.id)} onDragEnd={() => setDrag("")} />
                    </div>
                  ))}
                </div>
              </div>
            );
          })}

          {/* trash / archive drop zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setOverTrash(true); }}
            onDragLeave={() => setOverTrash(false)}
            onDrop={() => drag && archive(drag)}
            className={cn(
              "flex w-36 shrink-0 flex-col items-center justify-center gap-1.5 rounded-[calc(var(--radius)+2px)] border border-dashed text-xs",
              overTrash ? "border-danger bg-danger/10 text-danger" : "border-border text-faint",
            )}
          >
            <Icon name="trash" size={18} /> Drop to archive
          </div>
        </div>
      )}
    </>
  );
}

function laneSort(cards: KCard[]): KCard[] {
  return [...cards].sort((a, b) => (a.assignee || "~").localeCompare(b.assignee || "~"));
}

function CardTile({ card, dragging, onDragStart, onDragEnd }: {
  card: KCard; dragging: boolean; onDragStart: () => void; onDragEnd: () => void;
}) {
  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={cn(
        "cursor-grab rounded-[var(--radius)] border border-border bg-surface p-2.5 text-left transition active:cursor-grabbing",
        dragging ? "opacity-40" : "hover:border-border-2",
      )}
    >
      <div className="text-sm text-text">{compact(card.title, 90)}</div>
      {card.body && <div className="mt-1 line-clamp-2 text-[11px] text-faint">{compact(card.body, 120)}</div>}
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]">
        {card.assignee && <span className="rounded-full bg-primary/15 px-1.5 py-px text-primary">{card.assignee}</span>}
        {card.tenant && <span className="rounded-full bg-surface-2 px-1.5 py-px text-dim">{card.tenant}</span>}
        {card.priority > 0 && <span className="rounded-full bg-warning/15 px-1.5 py-px text-warning">P{card.priority}</span>}
        {card.parents?.length > 0 && <span className="text-faint">⛓ {card.parents.length}</span>}
        {card.updated_at && <span className="ml-auto text-faint">{ago(card.updated_at)}</span>}
      </div>
    </div>
  );
}
