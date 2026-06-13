import { useEffect, useRef, useState } from "react";
import { api, TOKEN } from "../lib/api";
import { Icon } from "../lib/icons";

type Agent = {
  id: string; kind?: string; type?: string; status?: string;
  task?: string; preview?: string; model?: string; provider?: string;
  active_runs?: number;
};
type Tick = { at: number; type: string; text: string };

const KIND_LABEL: Record<string, string> = { agent: "Primary", subagent: "Subagents", background: "Background" };
const KIND_ORDER = ["agent", "subagent", "background"];

function statusClass(s = "") {
  if (s === "error" || s === "failed") return "error";
  if (s === "running" || s === "active") return "running";
  if (s === "done" || s === "ok" || s === "completed") return "ok";
  return "";
}

// Live activity bus lives at /events (not under /api/), so open it directly.
function openEvents(onMsg: (d: any) => void): () => void {
  const url = TOKEN ? `/events?token=${TOKEN}` : "/events";
  const es = new EventSource(url);
  es.onmessage = (e) => { try { onMsg(JSON.parse(e.data)); } catch { /* ignore */ } };
  return () => es.close();
}

export function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [types, setTypes] = useState<any[]>([]);
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [live, setLive] = useState(false);
  const reloadTimer = useRef<any>(null);

  async function load() {
    try { const d = await api("agents"); setAgents(d.agents || []); setTypes(d.types || []); }
    catch { setAgents([]); }
  }
  useEffect(() => { load(); }, []);

  // Subscribe to the live bus: log relevant events and refetch (debounced) so the
  // roster reflects spawns/finishes within a second of them happening.
  useEffect(() => {
    const close = openEvents((d) => {
      setLive(true);
      const t = String(d.type || "");
      if (/subagent|background|delegat|chat_start|chat_final|tool/.test(t)) {
        const text = d.task || d.text || d.name || d.summary || "";
        setTicks((prev) => [{ at: Date.now(), type: t, text: String(text).slice(0, 80) }, ...prev].slice(0, 18));
        if (/subagent|background|delegat/.test(t)) {
          clearTimeout(reloadTimer.current);
          reloadTimer.current = setTimeout(load, 400);
        }
      }
    });
    return () => { close(); clearTimeout(reloadTimer.current); };
  }, []);

  const grouped = KIND_ORDER.map((k) => [k, agents.filter((a) => (a.kind || "agent") === k)] as const)
    .filter(([, list]) => list.length);
  const running = agents.filter((a) => statusClass(a.status) === "running").length;

  return (
    <>
      <div className="head">
        <div><h1>Agents</h1><span className="crumb">{agents.length} agent{agents.length === 1 ? "" : "s"} · {running} running</span></div>
        <span className={"live-dot" + (live ? " on" : "")}>{live ? "live" : "connecting…"}</span>
      </div>
      <div className="agents-layout">
        <div>
          {grouped.map(([kind, list]) => (
            <div className="card" style={{ marginBottom: 14 }} key={kind}>
              <h3>{KIND_LABEL[kind] || kind} <span className="mut">· {list.length}</span></h3>
              {list.map((a) => (
                <div className="row agent-row" key={a.id}>
                  <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                    <b>{a.type || a.kind || a.id} <span className="mut" style={{ fontWeight: 400 }}>{a.id !== a.type ? a.id : ""}</span></b>
                    {(a.task || a.preview) && <span className="mut" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 460 }}>{a.task || a.preview}</span>}
                    {(a.provider || a.model) && <span className="mut" style={{ fontSize: 12 }}>{[a.provider, a.model].filter(Boolean).join(" / ")}</span>}
                  </span>
                  <span className={"agent-badge " + statusClass(a.status)}>{a.status || "idle"}</span>
                </div>
              ))}
            </div>
          ))}
          {!agents.length && <div className="card empty">No agents yet — start a turn or spawn a subagent.</div>}
          {types.length > 0 && (
            <div className="card">
              <h3>Available agent types</h3>
              {types.map((t) => (
                <div className="row" key={t.name}>
                  <span><b>{t.name}</b> <span className="mut">{t.readonly ? "read-only" : "full tools"}</span></span>
                  <span className="mut" style={{ fontSize: 12, maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{Array.isArray(t.tools) ? t.tools.join(", ") : t.tools}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <aside className="card live-rail">
          <h3><Icon n="logs" /> Live activity</h3>
          {!ticks.length && <div className="empty small">Waiting for activity…</div>}
          {ticks.map((t, i) => (
            <div className="tick" key={i}>
              <span className={"tick-type " + (t.type.includes("done") || t.type.includes("final") ? "ok" : t.type.includes("error") ? "error" : "")}>{t.type}</span>
              {t.text && <span className="tick-text">{t.text}</span>}
            </div>
          ))}
        </aside>
      </div>
    </>
  );
}
