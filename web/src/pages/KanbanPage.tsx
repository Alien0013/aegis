import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { compact } from "../lib/format";
import { Button, Card, Empty, PageHeader, useToast } from "../lib/ui";

const COLUMNS = [
  ["ready", "Ready"],
  ["in_progress", "In progress"],
  ["blocked", "Blocked"],
  ["done", "Done"],
] as const;
type Status = typeof COLUMNS[number][0];

export function KanbanPage() {
  const [board, setBoard] = useState<Record<string, any[]>>({});
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState("");
  const toast = useToast();

  async function load() { try { setBoard(await api("kanban")); } catch (e) { toast(String(e), "err"); } }
  useEffect(() => { load(); }, []);

  async function create() {
    if (!title.trim()) return;
    setBusy("create");
    try { await post("kanban", { action: "create", title: title.trim(), body }); toast("Card created", "ok"); setTitle(""); setBody(""); await load(); }
    catch (e) { toast(String(e), "err"); } finally { setBusy(""); }
  }
  async function decompose() {
    if (!goal.trim()) return;
    setBusy("decompose");
    try { const r = await post("kanban", { action: "decompose", goal: goal.trim() }); toast(`Created ${r.created || 0} card${r.created === 1 ? "" : "s"}`, "ok"); setGoal(""); await load(); }
    catch (e) { toast(String(e), "err"); } finally { setBusy(""); }
  }
  async function runBoard() {
    setBusy("run");
    try { await post("kanban", { action: "run" }); toast("Board runner started", "ok"); await load(); }
    catch (e) { toast(String(e), "err"); } finally { setBusy(""); }
  }
  async function move(id: string, status: Status) { await post("kanban", { action: "move", id, status }); await load(); }

  const total = COLUMNS.reduce((n, [key]) => n + (board[key] || []).length, 0);

  return (
    <>
      <PageHeader title="Kanban" sub={`${total} card${total === 1 ? "" : "s"} across agent lanes`}
        actions={<>
          <Button variant="ghost" icon="refresh" onClick={load}>Refresh</Button>
          <Button icon="play" onClick={runBoard} disabled={busy === "run"}>Run board</Button>
        </>} />
      <div className="grid c2" style={{ marginBottom: 12 }}>
        <Card title="Create a card">
          <div className="stack">
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Card title" />
            <textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} placeholder="Optional details" />
            <Button onClick={create} disabled={busy === "create"} icon="plus">Create card</Button>
          </div>
        </Card>
        <Card title="Decompose a goal">
          <div className="stack">
            <textarea rows={4} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Describe a larger goal and AEGIS will split it into cards" />
            <Button variant="ghost" onClick={decompose} disabled={busy === "decompose"} icon="bolt">Decompose</Button>
          </div>
        </Card>
      </div>
      <div className="kanban-board">
        {COLUMNS.map(([status, label]) => {
          const cards = board[status] || [];
          return (
            <section className="kanban-col" key={status}>
              <h3>{label}<span>{cards.length}</span></h3>
              {!cards.length && <Empty small>—</Empty>}
              {cards.map((card) => (
                <article className="kanban-card" key={card.id}>
                  <b>{card.title || card.id}</b>
                  {card.body && <span className="mut" style={{ fontSize: 12 }}>{compact(card.body, 160)}</span>}
                  <div className="kanban-meta">
                    {card.assignee && <span className="pill">{card.assignee}</span>}
                    {card.priority != null && <span className="pill">P{card.priority}</span>}
                    {card.run_id && <span className="pill">run</span>}
                  </div>
                  <div className="kanban-actions">
                    {COLUMNS.filter(([next]) => next !== status).map(([next, nextLabel]) => (
                      <Button variant="ghost" sm key={next} onClick={() => move(card.id, next)}>{nextLabel}</Button>
                    ))}
                  </div>
                </article>
              ))}
            </section>
          );
        })}
      </div>
    </>
  );
}
