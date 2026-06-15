import { useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Field, Input, Loading, PageHeader, Spinner, Toggle, toast } from "../components/ui";
import { Icon } from "../components/icons";
import { cn } from "../lib/cn";
import { ago, compact } from "../lib/format";

interface KCard {
  id: string; title: string; body: string; assignee: string; priority: number;
  tenant: string; parents: string[]; status: string; run_id?: string;
  session_id?: string; updated_at?: string; workspace_kind?: string; skills?: string;
}
interface Board {
  order?: string[]; assignees?: string[]; tenants?: string[];
  [status: string]: KCard[] | string[] | unknown;
}
interface Draft {
  status: string;
  title: string;
  body: string;
  assignee: string;
  tenant: string;
  priority: string;
  skills: string;
  workspace: string;
  parent: string;
}

const COL: Record<string, { label: string; desc: string; dot: string }> = {
  triage: { label: "Triage", desc: "Raw ideas - a specifier will flesh out the spec", dot: "bg-warning" },
  todo: { label: "Todo", desc: "Waiting on dependencies or unassigned", dot: "bg-dim" },
  scheduled: { label: "Scheduled", desc: "Waiting on a time delay or scheduled follow-up", dot: "bg-info" },
  ready: { label: "Ready", desc: "Dependencies satisfied; assign a profile to dispatch", dot: "bg-primary" },
  in_progress: { label: "In Progress", desc: "Claimed by a worker and in-flight", dot: "bg-success" },
  blocked: { label: "Blocked", desc: "Worker asked for human input", dot: "bg-danger" },
  review: { label: "Review", desc: "Awaiting review sign-off", dot: "bg-info" },
  done: { label: "Done", desc: "Completed", dot: "bg-success" },
  archived: { label: "Archived", desc: "Closed or hidden tasks", dot: "bg-faint" },
};
const DEFAULT_ORDER = ["triage", "todo", "scheduled", "ready", "in_progress", "blocked", "review", "done"];
const EMPTY_DRAFT: Draft = {
  status: "triage", title: "", body: "", assignee: "", tenant: "", priority: "0",
  skills: "", workspace: "scratch", parent: "",
};

export function Kanban() {
  const [showArchived, setShowArchived] = useState(false);
  const { data, loading, error, reload } = useApi<Board>(showArchived ? "kanban?archived=1" : "kanban");
  const [q, setQ] = useState("");
  const [tenant, setTenant] = useState("");
  const [assignee, setAssignee] = useState("");
  const [lanes, setLanes] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [drag, setDrag] = useState("");
  const [overCol, setOverCol] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [running, setRunning] = useState(false);

  const orderBase = data?.order || DEFAULT_ORDER;
  const order = showArchived && !orderBase.includes("archived") ? [...orderBase, "archived"] : orderBase;
  const assignees = data?.assignees || [];
  const tenants = data?.tenants || [];
  const cardsOf = (s: string): KCard[] => (Array.isArray(data?.[s]) ? (data![s] as KCard[]) : []);
  const allCards = order.flatMap(cardsOf);
  const total = orderBase.reduce((n, s) => n + cardsOf(s).length, 0);

  const match = (c: KCard) =>
    (!q || c.title.toLowerCase().includes(q.toLowerCase()) || (c.body || "").toLowerCase().includes(q.toLowerCase()) || c.id.toLowerCase().includes(q.toLowerCase())) &&
    (!tenant || c.tenant === tenant) &&
    (!assignee || c.assignee === assignee);

  async function move(id: string, status: string) {
    setDrag(""); setOverCol("");
    try { await post("kanban", { action: "move", id, status }); reload(); }
    catch (e) { toast(String(e), "err"); }
  }
  async function archive(id: string) {
    setDrag("");
    try { await post("kanban", { action: "archive", id }); reload(); }
    catch (e) { toast(String(e), "err"); }
  }
  async function create() {
    if (!draft?.title.trim()) return;
    try {
      await post("kanban", {
        action: "create",
        title: draft.title.trim(),
        body: draft.body.trim(),
        status: draft.status,
        assignee: draft.assignee.trim(),
        tenant: draft.tenant.trim(),
        priority: Number(draft.priority || 0),
        skills: draft.skills.trim(),
        workspace: draft.workspace.trim(),
        parent: draft.parent.trim(),
      });
      setDraft(null);
      reload();
    } catch (e) { toast(String(e), "err"); }
  }
  async function nudge() {
    setRunning(true);
    try { await post("kanban", { action: "run" }); toast("Dispatcher nudged"); }
    catch (e) { toast(String(e), "err"); }
    finally { setTimeout(() => { setRunning(false); reload(); }, 900); }
  }
  const clearFilters = () => { setQ(""); setTenant(""); setAssignee(""); };
  const openDraft = (status: string) => setDraft({ ...EMPTY_DRAFT, status, assignee, tenant });

  return (
    <>
      <PageHeader title="Kanban" sub={`${total} task${total === 1 ? "" : "s"} on Default board`} />

      <Card className="mb-[var(--gap)]" pad={false}>
        <div className="flex flex-wrap items-end justify-between gap-3 p-[var(--pad)]">
          <Field label="Board">
            <select className="min-h-9 min-w-56 border border-border bg-surface-2 px-3 font-mono text-sm text-text outline-none">
              <option>Default / {total}</option>
            </select>
          </Field>
          <div className="flex items-center gap-2">
            <button className="grid h-8 w-8 place-items-center border border-border text-faint hover:text-primary" title="Help">
              <Icon name="alert" size={14} />
            </button>
            <Button variant="primary" icon="plus" onClick={() => openDraft("triage")}>New task</Button>
          </div>
        </div>
      </Card>

      <div className="mb-[var(--gap)] flex flex-wrap items-center gap-2">
        <button
          onClick={nudge}
          disabled={running}
          className="inline-flex min-h-8 items-center gap-2 border border-primary bg-primary px-3 font-mono text-xs font-semibold text-primary-fg disabled:opacity-50"
        >
          {running ? <Spinner size={14} /> : <Icon name="play" size={14} />}
          Nudge dispatcher
        </button>
        <Button icon="refresh" onClick={reload}>Refresh</Button>
        <Button onClick={clearFilters}>Clear filters</Button>
        <button
          onClick={() => setSettingsOpen((v) => !v)}
          className="inline-flex min-h-8 items-center gap-1.5 border border-border px-3 font-mono text-xs text-dim hover:bg-surface-2 hover:text-text"
        >
          <Icon name={settingsOpen ? "chevronDown" : "chevronRight"} size={13} />
          Orchestration settings
        </button>
      </div>

      {settingsOpen && (
        <Card className="mb-[var(--gap)]" title="Orchestration Settings" sub="Dispatcher defaults and claim behavior">
          <div className="grid gap-3 md:grid-cols-4">
            <Field label="Mode">
              <select className="w-full border border-border bg-surface-2 px-3 py-1.5 font-mono text-sm text-text outline-none">
                <option>Auto</option>
                <option>Paused</option>
                <option>Manual</option>
              </select>
            </Field>
            <Field label="Specifier"><Input placeholder="default" /></Field>
            <Field label="Worker"><Input placeholder="default" /></Field>
            <Field label="Profile notes"><Input placeholder="What is this profile good at?" /></Field>
          </div>
        </Card>
      )}

      <Card className="mb-[var(--gap)]">
        <div className="grid gap-3 lg:grid-cols-[220px_150px_150px_auto_auto_auto] lg:items-end">
          <Field label="Search"><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter cards..." /></Field>
          <Field label="Tenant">
            <select value={tenant} onChange={(e) => setTenant(e.target.value)}
              className="w-full border border-border bg-surface-2 px-3 py-1.5 font-mono text-sm text-text outline-none">
              <option value="">All tenants</option>
              {tenants.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Assignee">
            <select value={assignee} onChange={(e) => setAssignee(e.target.value)}
              className="w-full border border-border bg-surface-2 px-3 py-1.5 font-mono text-sm text-text outline-none">
              <option value="">All profiles</option>
              {assignees.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </Field>
          <label className="flex items-center gap-2 pb-2 font-mono text-xs text-dim"><Toggle on={showArchived} onChange={setShowArchived} /> Show archived</label>
          <label className="flex items-center gap-2 pb-2 font-mono text-xs text-dim"><Toggle on={lanes} onChange={setLanes} /> Lanes by profile</label>
          <div className="pb-2 font-mono text-xs text-faint">{allCards.filter(match).length} visible</div>
        </div>
      </Card>

      {error && <Card><Empty icon="alert">Couldn't load board - {error}</Empty></Card>}
      {loading && !data && <Loading label="Loading Kanban board..." />}

      {data && (
        <div className="scroll-thin flex gap-3 overflow-x-auto pb-3">
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
                  "flex min-h-[240px] w-[280px] shrink-0 flex-col border bg-surface/55",
                  overCol === s ? "border-primary ring-1 ring-primary/35" : "border-border",
                )}
              >
                <div className="border-b border-border px-3 py-3">
                  <div className="flex items-center gap-2">
                    <button className="h-3.5 w-3.5 border border-border" title={`Select all tasks in ${meta.label}`} />
                    <span className={cn("h-2 w-2 rounded-full", meta.dot)} />
                    <span className="font-mono text-sm font-semibold text-text">{meta.label}</span>
                    <span className="ml-auto font-mono text-xs text-faint">{cards.length}</span>
                    <button onClick={() => openDraft(s)} className="grid h-6 w-6 place-items-center border border-border text-faint hover:text-primary" title={`Add task to ${meta.label}`}>
                      <Icon name="plus" size={13} />
                    </button>
                  </div>
                  <div className="mt-2 min-h-8 text-xs leading-snug text-faint">{meta.desc}</div>
                </div>
                <div className="scroll-thin flex max-h-[62vh] min-h-[140px] flex-1 flex-col gap-2 overflow-y-auto p-2">
                  {cards.length === 0 && (
                    <div className="grid min-h-16 place-items-center border border-dashed border-border text-xs text-faint">- no tasks -</div>
                  )}
                  {(lanes ? laneSort(cards) : cards).map((card, i, arr) => (
                    <div key={card.id}>
                      {lanes && (i === 0 || arr[i - 1].assignee !== card.assignee) && (
                        <div className="px-0.5 pb-1 pt-1 font-mono text-[10px] uppercase tracking-wide text-faint">
                          {card.assignee || "unassigned"}
                        </div>
                      )}
                      <CardTile card={card} dragging={drag === card.id}
                        onDragStart={() => setDrag(card.id)} onDragEnd={() => setDrag("")}
                        onArchive={() => archive(card.id)} />
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {draft && (
        <TaskDialog
          draft={draft}
          setDraft={setDraft}
          cards={allCards}
          onClose={() => setDraft(null)}
          onCreate={create}
        />
      )}
    </>
  );
}

function TaskDialog({ draft, setDraft, cards, onClose, onCreate }: {
  draft: Draft;
  setDraft: (draft: Draft | null) => void;
  cards: KCard[];
  onClose: () => void;
  onCreate: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center bg-black/55 pt-[6vh] backdrop-blur-sm" onMouseDown={onClose}>
      <div className="w-full max-w-2xl border border-border bg-bg shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <div className="font-mono text-base font-semibold text-text">New task</div>
            <div className="text-xs text-faint">{COL[draft.status]?.label || draft.status}</div>
          </div>
          <button onClick={onClose} className="text-faint hover:text-text"><Icon name="x" size={18} /></button>
        </div>
        <div className="grid gap-3 p-4 md:grid-cols-2">
          <Field label="Title">
            <Input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder={draft.status === "triage" ? "Rough idea - AI will spec it..." : "New task title..."} />
          </Field>
          <Field label="Assignee">
            <Input value={draft.assignee} onChange={(e) => setDraft({ ...draft, assignee: e.target.value })} placeholder="Profile or worker" />
          </Field>
          <Field label="Tenant"><Input value={draft.tenant} onChange={(e) => setDraft({ ...draft, tenant: e.target.value })} placeholder="default" /></Field>
          <Field label="Priority"><Input value={draft.priority} inputMode="numeric" onChange={(e) => setDraft({ ...draft, priority: e.target.value })} placeholder="0" /></Field>
          <Field label="Skills"><Input value={draft.skills} onChange={(e) => setDraft({ ...draft, skills: e.target.value })} placeholder="kanban-worker, code-review" /></Field>
          <Field label="Workspace">
            <select value={draft.workspace} onChange={(e) => setDraft({ ...draft, workspace: e.target.value })}
              className="w-full border border-border bg-surface-2 px-3 py-1.5 font-mono text-sm text-text outline-none">
              <option value="scratch">scratch</option>
              <option value="dir">dir</option>
              <option value="worktree">worktree</option>
            </select>
          </Field>
          <Field label="Parent">
            <select value={draft.parent} onChange={(e) => setDraft({ ...draft, parent: e.target.value })}
              className="w-full border border-border bg-surface-2 px-3 py-1.5 font-mono text-sm text-text outline-none">
              <option value="">- no parent -</option>
              {cards.slice(0, 80).map((card) => <option key={card.id} value={card.id}>{compact(card.title, 60)}</option>)}
            </select>
          </Field>
          <label className="flex items-end gap-2 pb-1 font-mono text-xs text-dim">
            <Toggle on={draft.status === "triage"} onChange={(v) => setDraft({ ...draft, status: v ? "triage" : "todo" })} />
            Spec first
          </label>
          <div className="md:col-span-2">
            <Field label="Body">
              <textarea
                value={draft.body}
                onChange={(e) => setDraft({ ...draft, body: e.target.value })}
                rows={6}
                placeholder="What should the worker know?"
                className="w-full resize-y border border-border bg-surface-2 px-3 py-2 text-sm text-text outline-none focus:border-primary/60"
              />
            </Field>
          </div>
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon="check" onClick={onCreate} disabled={!draft.title.trim()}>Create</Button>
        </div>
      </div>
    </div>
  );
}

function laneSort(cards: KCard[]): KCard[] {
  return [...cards].sort((a, b) => (a.assignee || "~").localeCompare(b.assignee || "~"));
}

function CardTile({ card, dragging, onDragStart, onDragEnd, onArchive }: {
  card: KCard; dragging: boolean; onDragStart: () => void; onDragEnd: () => void; onArchive: () => void;
}) {
  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={cn(
        "cursor-grab border border-border bg-bg/55 p-2.5 text-left transition active:cursor-grabbing",
        dragging ? "opacity-40" : "hover:border-border-2 hover:bg-surface-2/40",
      )}
    >
      <div className="flex items-start gap-2">
        <button className="mt-0.5 h-3.5 w-3.5 shrink-0 border border-border" title={`Select task ${card.id}`} />
        <div className="min-w-0 flex-1">
          <div className="font-mono text-sm text-text">{compact(card.title, 90)}</div>
          {card.body && <div className="mt-1 line-clamp-2 text-[11px] text-faint">{compact(card.body, 120)}</div>}
        </div>
        <button onClick={onArchive} className="text-faint hover:text-danger" title="Archive"><Icon name="trash" size={13} /></button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 font-mono text-[10px]">
        <span className="text-faint">{card.id}</span>
        {card.assignee && <span className="border border-primary/35 px-1 text-primary">{card.assignee}</span>}
        {card.tenant && <span className="border border-border px-1 text-dim">{card.tenant}</span>}
        {card.priority > 0 && <span className="border border-warning/35 px-1 text-warning">P{card.priority}</span>}
        {card.parents?.length > 0 && <span className="text-faint">dep {card.parents.length}</span>}
        {card.updated_at && <span className="ml-auto text-faint">{ago(card.updated_at)}</span>}
      </div>
    </div>
  );
}
