import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { compact } from "../lib/format";

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
  const [msg, setMsg] = useState("");

  async function load() {
    try { setBoard(await api("kanban")); }
    catch (e) { setMsg(String(e)); }
  }
  useEffect(() => { load(); }, []);

  async function create() {
    if (!title.trim()) return;
    setBusy("create"); setMsg("");
    try {
      await post("kanban", { action: "create", title: title.trim(), body });
      setTitle(""); setBody(""); await load();
    } catch (e) { setMsg(String(e)); }
    finally { setBusy(""); }
  }

  async function decompose() {
    if (!goal.trim()) return;
    setBusy("decompose"); setMsg("");
    try {
      const r = await post("kanban", { action: "decompose", goal: goal.trim() });
      setMsg(`Created ${r.created || 0} card${r.created === 1 ? "" : "s"}`);
      setGoal(""); await load();
    } catch (e) { setMsg(String(e)); }
    finally { setBusy(""); }
  }

  async function runBoard() {
    setBusy("run"); setMsg("");
    try {
      await post("kanban", { action: "run" });
      setMsg("Board runner started");
      await load();
    } catch (e) { setMsg(String(e)); }
    finally { setBusy(""); }
  }

  async function move(id: string, status: Status) {
    await post("kanban", { action: "move", id, status });
    await load();
  }

  const total = COLUMNS.reduce((n, [key]) => n + (board[key] || []).length, 0);

  return (
    <>
      <div className="head">
        <div><h1>Kanban</h1><span className="crumb">{total} card{total === 1 ? "" : "s"} across agent lanes</span></div>
        <span className="actions">
          <button className="btn ghost" onClick={load}>Refresh</button>
          <button className="btn" onClick={runBoard} disabled={busy === "run"}>{busy === "run" ? <span className="spin sm" /> : "Run board"}</button>
        </span>
      </div>
      {msg && <div className="banner" style={{ marginBottom: 14 }}>{msg}</div>}
      <div className="grid c2" style={{ marginBottom: 14 }}>
        <div className="panel">
          <h3>Create a card</h3>
          <div className="stack">
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Card title" />
            <textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} placeholder="Optional details" />
            <button className="btn" onClick={create} disabled={busy === "create"}>Create card</button>
          </div>
        </div>
        <div className="panel">
          <h3>Decompose a goal</h3>
          <div className="stack">
            <textarea rows={4} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Describe a larger goal and AEGIS will split it into cards" />
            <button className="btn ghost" onClick={decompose} disabled={busy === "decompose"}>Decompose</button>
          </div>
        </div>
      </div>
      <div className="kanban-board">
        {COLUMNS.map(([status, label]) => {
          const cards = board[status] || [];
          return (
            <section className="kanban-col" key={status}>
              <header><b>{label}</b><span>{cards.length}</span></header>
              {!cards.length && <div className="empty small">No cards</div>}
              {cards.map((card) => (
                <article className="kanban-card" key={card.id}>
                  <h3>{card.title || card.id}</h3>
                  {card.body && <p>{compact(card.body, 180)}</p>}
                  <div className="kanban-meta">
                    {card.assignee && <span className="pill">{card.assignee}</span>}
                    {card.priority != null && <span className="pill">P{card.priority}</span>}
                    {card.run_id && <span className="pill">run</span>}
                  </div>
                  <div className="kanban-actions">
                    {COLUMNS.filter(([next]) => next !== status).map(([next, nextLabel]) => (
                      <button className="btn ghost" key={next} onClick={() => move(card.id, next)}>{nextLabel}</button>
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
