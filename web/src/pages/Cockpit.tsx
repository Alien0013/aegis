import { useEffect, useMemo, useRef, useState } from "react";
import { api, post, postStream } from "../lib/api";
import { compact, dateish } from "../lib/format";
import { Icon } from "../lib/icons";

type EventRow = {
  id: string;
  kind: string;
  title: string;
  text: string;
  status?: string;
  target?: string;
  time: string;
};

const fmt = new Intl.NumberFormat();
const money = (n: any) => "$" + (Number(n || 0)).toFixed(2);
const tokens = (n: any) => Number(n || 0) ? fmt.format(Number(n || 0)) : "-";
const uid = () => Math.random().toString(36).slice(2);

function entries(raw: string): string[] {
  return (raw || "").split("§").map((x) => x.trim()).filter(Boolean);
}

function kv(label: string, value: any) {
  return <div className="kv"><span>{label}</span><b>{compact(value, 26)}</b></div>;
}

function ToolEvent({ ev, showThinking }: { ev: EventRow; showThinking: boolean }) {
  if (ev.kind === "thinking" && !showThinking) return null;
  const icon = ev.kind === "tool" ? "tools" : ev.kind === "thinking" ? "models" : ev.kind === "error" ? "logs" : "chat";
  return (
    <div className={`timeline-item ${ev.kind} ${ev.status || ""}`}>
      <span className="timeline-icon"><Icon n={icon} /></span>
      <div className="timeline-main">
        <div className="timeline-top">
          <b>{ev.title}</b>
          <span>{ev.status || ev.time}</span>
        </div>
        {ev.target && <code className="timeline-target">{ev.target}</code>}
        {ev.text && <p>{ev.text}</p>}
      </div>
    </div>
  );
}

export function Cockpit({ go }: { go: (id: string) => void }) {
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [prompt, setPrompt] = useState("");
  const [session, setSession] = useState("");
  const [cwd, setCwd] = useState("");
  const [events, setEvents] = useState<EventRow[]>([]);
  const [showThinking, setShowThinking] = useState(true);
  const [toolQuery, setToolQuery] = useState("");
  const [sessionQuery, setSessionQuery] = useState("");
  const [memoryText, setMemoryText] = useState("");
  const [memoryTarget, setMemoryTarget] = useState<"user" | "memory">("user");
  const [cardTitle, setCardTitle] = useState("");
  const [cardBody, setCardBody] = useState("");
  const [allowPrefix, setAllowPrefix] = useState("");
  const [previewUrl, setPreviewUrl] = useState("http://127.0.0.1:9120/");
  const timelineRef = useRef<HTMLDivElement>(null);

  async function load() {
    setMsg("");
    try {
      const next = await api("cockpit");
      setData(next);
      if (!session) setSession(next.latest_session?.id || "");
      if (!cwd) setCwd(next.review?.root || next.review?.cwd || "");
    } catch (e) {
      setMsg("Could not load cockpit: " + String(e));
    }
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => { timelineRef.current?.scrollTo(0, timelineRef.current.scrollHeight); }, [events]);

  async function saveConfig(key: string, value: any) {
    setBusy(key);
    try {
      await post("config", { key, value });
      await load();
    } finally {
      setBusy("");
    }
  }

  async function saveModel(provider: string, model: string) {
    setBusy("model");
    try {
      const r = await post("models", { provider, model });
      setMsg(r.error || r.warning || "Model updated");
      await load();
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy("");
    }
  }

  function patchEvent(id: string, fn: (ev: EventRow) => EventRow) {
    setEvents((rows) => rows.map((ev) => ev.id === id ? fn(ev) : ev));
  }

  function pushEvent(ev: Partial<EventRow>) {
    const row: EventRow = {
      id: ev.id || uid(),
      kind: ev.kind || "note",
      title: ev.title || "Activity",
      text: ev.text || "",
      status: ev.status,
      target: ev.target,
      time: new Date().toLocaleTimeString(),
    };
    setEvents((rows) => [...rows.slice(-80), row]);
    return row.id;
  }

  function appendLive(kind: string, title: string, text: string) {
    setEvents((rows) => {
      const last = rows[rows.length - 1];
      if (last && last.kind === kind && last.status === "running") {
        return [...rows.slice(0, -1), { ...last, text: last.text + text }];
      }
      const row: EventRow = {
        id: uid(),
        kind,
        title,
        text,
        status: "running",
        time: new Date().toLocaleTimeString(),
      };
      return [...rows.slice(-80), row];
    });
  }

  function settleLive() {
    setEvents((rows) => rows.map((ev) => (
      ev.status === "running" && ["answer", "thinking", "step"].includes(ev.kind)
        ? { ...ev, status: "ok" }
        : ev
    )));
  }

  function applyStreamEvent(raw: any) {
    const e = raw || {};
    if (e.type === "iteration") {
      pushEvent({ kind: "step", title: "Agent step", text: `${e.n}/${e.max}`, status: "running" });
    } else if (e.type === "reasoning_delta") {
      appendLive("thinking", "Thinking", e.text || "");
    } else if (e.type === "tool_start") {
      pushEvent({ id: e.id || uid(), kind: "tool", title: e.name || "Tool", target: e.target || e.summary || "", status: "running" });
    } else if (e.type === "tool_result") {
      if (e.id) patchEvent(e.id, (x) => ({ ...x, text: e.target || e.summary || x.text, status: e.status || "ok" }));
      else pushEvent({ kind: "tool", title: e.name || "Tool result", text: e.target || e.summary || "", status: e.status || "ok" });
    } else if (e.type === "assistant_delta") {
      appendLive("answer", "Assistant", e.text || "");
    } else if (e.type === "assistant_message") {
      pushEvent({ kind: "answer", title: "Assistant", text: e.text || "", status: "ok" });
    } else if (e.type === "compacting") {
      pushEvent({ kind: "step", title: "Context", text: "Compacting context", status: "running" });
    } else if (e.type === "error") {
      pushEvent({ kind: "error", title: "Error", text: e.message || e.summary || "", status: "error" });
    }
  }

  async function send() {
    const text = prompt.trim();
    if (!text || busy === "send") return;
    setPrompt("");
    pushEvent({ kind: "user", title: "You", text, status: "ok" });
    setBusy("send");
    try {
      await postStream("chat/stream", {
        message: text,
        session_id: session,
        cwd: cwd.trim() || undefined,
      }, (frame) => {
        if (frame.type === "start" && frame.session_id) setSession(frame.session_id);
        else if (frame.type === "event") applyStreamEvent(frame.event);
        else if (frame.type === "final") {
          settleLive();
          pushEvent({ kind: "answer", title: "Final", text: frame.reply || "", status: "ok" });
          if (frame.session_id) setSession(frame.session_id);
        } else if (frame.type === "error") {
          pushEvent({ kind: "error", title: "Error", text: frame.reply || "stream failed", status: "error" });
        }
      });
      await load();
    } catch (e) {
      pushEvent({ kind: "error", title: "Error", text: String(e), status: "error" });
    } finally {
      setBusy("");
    }
  }

  async function createCard() {
    if (!cardTitle.trim()) return;
    await post("kanban", { action: "create", title: cardTitle.trim(), body: cardBody });
    setCardTitle(""); setCardBody("");
    await load();
  }

  async function addMemory() {
    if (!memoryText.trim()) return;
    const r = await post("memory", { action: "add", target: memoryTarget, content: memoryText });
    setMsg(r.result || "Memory saved");
    setMemoryText("");
    await load();
  }

  async function removeMemory(target: "user" | "memory", value: string) {
    await post("memory", { action: "remove", target, match: value.slice(0, 80) });
    await load();
  }

  const status = data?.status || {};
  const tools: any[] = data?.tools?.tools || [];
  const agents: any[] = data?.agents?.agents || [];
  const activeAgents = agents.filter((a) => a.id !== "primary");
  const kanban = data?.kanban || {};
  const cards: any[] = Object.values(kanban).flat() as any[];
  const latestSession = data?.latest_session || {};
  const usage = latestSession?.meta?.usage || {};
  const promptInfo = latestSession?.prompt || {};
  const sessions: any[] = data?.sessions || [];
  const filteredSessions = sessions.filter((s) => `${s.title} ${s.id}`.toLowerCase().includes(sessionQuery.toLowerCase()));
  const toolsets = [...new Set(tools.map((t) => String(t.toolset || "core")))].sort();
  const groups = [...new Set(tools.flatMap((t) => t.groups || []))].sort();
  const visibleTools = tools.filter((t) => `${t.name} ${t.toolset} ${(t.groups || []).join(" ")}`.toLowerCase().includes(toolQuery.toLowerCase()));
  const toolsBySet = useMemo(() => {
    const out: Record<string, any[]> = {};
    for (const tool of visibleTools) (out[tool.toolset || "core"] ||= []).push(tool);
    return out;
  }, [visibleTools]);
  const modelPresets: any[] = data?.status ? [] : [];
  void modelPresets;
  const currentToolsets: string[] = data?.tools?.toolsets || [];
  const denyGroups: string[] = data?.tools?.deny_groups || [];
  const allowlist: string[] = data?.tools?.allowlist || [];
  const review = data?.review || {};
  const logs = data?.logs || {};
  const memory = data?.memory || {};
  const projects: any[] = data?.projects?.projects || [];
  const worktrees: any[] = data?.worktrees?.worktrees || [];

  if (!data) {
    return <>
      <div className="head"><h1>Cockpit</h1><span className="crumb">loading agent workspace</span></div>
      {msg ? <div className="banner err">{msg}</div> : <div className="empty"><span className="spin" /> loading...</div>}
    </>;
  }

  return (
    <>
      <div className="head">
        <div>
          <h1>Cockpit</h1>
          <span className="crumb">{status.provider}/{status.model} · {review.branch || "workspace"} · {session ? compact(session, 28) : "new session"}</span>
        </div>
        <span className="actions">
          <button className="btn ghost" onClick={() => go("config")}>Settings</button>
          <button className="btn ghost" onClick={load}>Refresh</button>
          <button className="btn" onClick={() => go("chat")}>Open full chat</button>
        </span>
      </div>
      {msg && <div className={"banner" + (msg.toLowerCase().includes("error") ? " err" : "")} style={{ marginBottom: 14 }}>{msg}</div>}

      <section className="cockpit-hero">
        <div className="runtime-card">
          <div className="runtime-main">
            <span className={"statusdot" + (status.provider_error ? " err" : "")} />
            <div>
              <b>{status.model || "model"}</b>
              <span>{status.provider_error || `${status.provider || "provider"} · ${status.exec_mode || "auto"} permissions`}</span>
            </div>
          </div>
          <div className="runtime-grid">
            {kv("Context", tokens(status.context_length))}
            {kv("Prompt", tokens(promptInfo.tokens))}
            {kv("Input", tokens(usage.input_tokens))}
            {kv("Output", tokens(usage.output_tokens))}
            {kv("Reasoning", `${status.reasoning_display || "-"} / ${status.reasoning_effort || "-"}`)}
            {kv("CWD", cwd || review.cwd)}
          </div>
        </div>
        <div className="quick-controls">
          <label>Provider<input value={status.provider || ""} onChange={(e) => setData({ ...data, status: { ...status, provider: e.target.value } })} /></label>
          <label>Model<input value={status.model || ""} onChange={(e) => setData({ ...data, status: { ...status, model: e.target.value } })} /></label>
          <button className="btn" disabled={busy === "model"} onClick={() => saveModel(status.provider, status.model)}>Apply model</button>
          <label>Permissions
            <select value={status.exec_mode || "auto"} onChange={(e) => saveConfig("tools.exec_mode", e.target.value)}>
              {["auto", "ask", "smart", "allowlist", "deny", "full"].map((x) => <option key={x}>{x}</option>)}
            </select>
          </label>
          <label>Thinking
            <select value={status.reasoning_display || "summary"} onChange={(e) => saveConfig("display.reasoning", e.target.value)}>
              {["summary", "live", "off"].map((x) => <option key={x}>{x}</option>)}
            </select>
          </label>
          <label>Effort
            <select value={status.reasoning_effort || "off"} onChange={(e) => saveConfig("agent.reasoning_effort", e.target.value)}>
              {["off", "minimal", "low", "medium", "high", "xhigh"].map((x) => <option key={x}>{x}</option>)}
            </select>
          </label>
        </div>
      </section>

      <div className="cockpit-layout">
        <aside className="cockpit-left">
          <section className="panel">
            <div className="section-head"><h3>Sessions</h3><button className="linkbtn" onClick={() => go("sessions")}>All</button></div>
            <input value={sessionQuery} onChange={(e) => setSessionQuery(e.target.value)} placeholder="Search threads" />
            <div className="mini-list">
              {filteredSessions.slice(0, 10).map((s) => (
                <button className={"session-chip" + (session === s.id ? " active" : "")} key={s.id} onClick={() => setSession(s.id)}>
                  <b>{compact(s.title || s.id, 44)}</b><span>{dateish(s.updated_at)}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="section-head"><h3>Subagents</h3><button className="linkbtn" onClick={() => go("agents")}>Open</button></div>
            {!activeAgents.length && <div className="empty small">No background workers right now.</div>}
            {activeAgents.slice(0, 8).map((a) => (
              <div className="agent-row" key={a.id}>
                <span className={"agent-dot " + (a.status || "configured")} />
                <div><b>{compact(a.id, 30)}</b><span>{compact(a.task || a.type || a.kind, 60)}</span></div>
                <span className="pill">{a.status || "ready"}</span>
              </div>
            ))}
          </section>

          <section className="panel">
            <div className="section-head"><h3>Projects</h3><button className="linkbtn" onClick={() => go("projects")}>Manage</button></div>
            {projects.slice(0, 5).map((p) => (
              <div className="minirow" key={p.id}><b>{compact(p.name, 28)}</b><span>{p.branch || p.kind}</span></div>
            ))}
            {worktrees.slice(0, 4).map((w) => (
              <div className="minirow" key={w.path}><b>{compact(w.branch || w.path, 28)}</b><span>{w.dirty ? "dirty" : "clean"}</span></div>
            ))}
          </section>
        </aside>

        <main className="cockpit-center">
          <section className="panel thread-panel">
            <div className="section-head">
              <h3>Live Agent Timeline</h3>
              <span className="actions">
                <label className="toggle-row tiny"><span>Thinking</span><input type="checkbox" checked={showThinking} onChange={(e) => setShowThinking(e.target.checked)} /></label>
                <button className="btn ghost" onClick={() => setEvents([])}>Clear</button>
              </span>
            </div>
            <div className="timeline" ref={timelineRef}>
              {!events.length && (
                <div className="empty">
                  <div style={{ fontSize: 15, marginBottom: 6 }}>Start from here</div>
                  Thinking, tools, terminal-like events, errors, retries, and final answers appear in one timeline.
                </div>
              )}
              {events.map((ev) => <ToolEvent key={ev.id} ev={ev} showThinking={showThinking} />)}
            </div>
            <div className="cockpit-composer">
              <input value={cwd} onChange={(e) => setCwd(e.target.value)} placeholder="cwd / project path" />
              <textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)}
                placeholder="Ask AEGIS to work. Enter sends, Shift+Enter inserts a newline."
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
              <button className="btn" disabled={busy === "send"} onClick={send}>{busy === "send" ? <span className="spin sm" /> : "Run"}</button>
            </div>
          </section>

          <div className="grid c2">
            <section className="panel">
              <div className="section-head"><h3>Kanban In Flow</h3><button className="linkbtn" onClick={() => go("kanban")}>Board</button></div>
              <div className="kanban-mini">
                {(["ready", "in_progress", "blocked", "done"] as const).map((key) => (
                  <div key={key}><b>{(kanban[key] || []).length}</b><span>{key.replace("_", " ")}</span></div>
                ))}
              </div>
              <input value={cardTitle} onChange={(e) => setCardTitle(e.target.value)} placeholder="New card title" />
              <textarea rows={2} value={cardBody} onChange={(e) => setCardBody(e.target.value)} placeholder="Details / acceptance criteria" />
              <div className="actions">
                <button className="btn" onClick={createCard}>Create card</button>
                <button className="btn ghost" onClick={async () => { await post("kanban", { action: "run" }); await load(); }}>Run board</button>
              </div>
              <div className="mini-list">
                {cards.slice(0, 6).map((c) => <div className="minirow" key={c.id}><b>{compact(c.title || c.id, 42)}</b><span>{c.assignee || c.priority ? `P${c.priority || "-"}` : "card"}</span></div>)}
              </div>
            </section>

            <section className="panel">
              <div className="section-head"><h3>Review / Diff</h3><button className="linkbtn" onClick={() => go("worktrees")}>Worktrees</button></div>
              <div className="minirow"><b>{review.branch || "branch"}</b><span>{review.dirty ? "changes pending" : "clean"}</span></div>
              <div className="diff-files">
                {(review.files || []).slice(0, 8).map((f: any) => <span className="pill" key={`${f.source}:${f.path}`}>{f.status} {compact(f.path, 30)}</span>)}
              </div>
              <pre className="diff-preview">{review.diff_stat || review.staged_diff_stat || "No git diff in this workspace."}</pre>
            </section>
          </div>

          <section className="panel">
            <div className="section-head"><h3>Integrated Preview</h3><span className="crumb">local unauthenticated pages</span></div>
            <div className="preview-bar">
              <input value={previewUrl} onChange={(e) => setPreviewUrl(e.target.value)} placeholder="http://127.0.0.1:3000/" />
              <button className="btn ghost" onClick={() => window.open(previewUrl, "_blank")}>Open</button>
            </div>
            <iframe className="preview-frame" src={previewUrl} title="Preview" />
          </section>
        </main>

        <aside className="cockpit-right">
          <section className="panel">
            <div className="section-head"><h3>Permission Center</h3><button className="linkbtn" onClick={() => go("config")}>Advanced</button></div>
            <div className="chip-grid">
              {groups.slice(0, 12).map((group) => {
                const denied = denyGroups.includes(group);
                return <button className={"chipbtn" + (denied ? " denied" : "")} key={group}
                  onClick={() => saveConfig("tools.deny_groups", denied ? denyGroups.filter((g) => g !== group) : [...denyGroups, group])}>
                  {denied ? "Deny " : "Allow "}{group}
                </button>;
              })}
            </div>
            <div className="allow-row">
              <input value={allowPrefix} onChange={(e) => setAllowPrefix(e.target.value)} placeholder="Allow command prefix, e.g. git status" />
              <button className="btn ghost" onClick={() => { if (allowPrefix.trim()) { void saveConfig("tools.allowlist", [...allowlist, allowPrefix.trim()]); setAllowPrefix(""); } }}>Add</button>
            </div>
            <div className="mini-list">{allowlist.slice(0, 6).map((a) => <button className="pill pillbtn" key={a} onClick={() => saveConfig("tools.allowlist", allowlist.filter((x) => x !== a))}>{a} ×</button>)}</div>
          </section>

          <section className="panel">
            <div className="section-head"><h3>Tool Manager</h3><button className="linkbtn" onClick={() => go("tools")}>All</button></div>
            <input value={toolQuery} onChange={(e) => setToolQuery(e.target.value)} placeholder="Find tools" />
            <div className="toolset-row">
              {toolsets.map((set) => {
                const on = currentToolsets.includes(set);
                return <button className={"chipbtn" + (on ? " active" : "")} key={set}
                  onClick={() => saveConfig("tools.toolsets", on ? currentToolsets.filter((x) => x !== set) : [...currentToolsets, set])}>
                  {set}
                </button>;
              })}
            </div>
            <div className="tool-scroll">
              {(Object.entries(toolsBySet) as [string, any[]][]).map(([set, rows]) => (
                <details key={set} open={rows.some((t) => t.enabled)}>
                  <summary>{set} <span>{rows.length}</span></summary>
                  {rows.slice(0, 12).map((t) => (
                    <details className="tool-row" key={t.name}>
                      <summary><b>{t.name}</b><span className={t.enabled ? "oktxt" : "mut"}>{t.enabled ? "enabled" : "off"}</span></summary>
                      <p>{t.description}</p>
                      <pre className="json mini">{JSON.stringify(t.schema?.parameters || t.schema || {}, null, 2)}</pre>
                    </details>
                  ))}
                </details>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="section-head"><h3>Memory</h3><button className="linkbtn" onClick={() => go("memory")}>Open</button></div>
            <div className="composer compact">
              <select value={memoryTarget} onChange={(e) => setMemoryTarget(e.target.value as any)}>
                <option value="user">USER</option><option value="memory">MEMORY</option>
              </select>
              <input value={memoryText} onChange={(e) => setMemoryText(e.target.value)} placeholder="Add durable fact" />
              <button className="btn ghost" onClick={addMemory}>Add</button>
            </div>
            <div className="memory-stack">
              {entries(memory.user).slice(0, 3).map((m) => <button className="memory-pill" key={m} onClick={() => removeMemory("user", m)}>{compact(m, 82)}</button>)}
              {entries(memory.memory).slice(0, 3).map((m) => <button className="memory-pill agent-note" key={m} onClick={() => removeMemory("memory", m)}>{compact(m, 82)}</button>)}
              {!entries(memory.user).length && !entries(memory.memory).length && <div className="empty small">No memory facts yet.</div>}
            </div>
          </section>

          <section className="panel">
            <div className="section-head"><h3>Setup & Recovery</h3><button className="linkbtn" onClick={() => go("system")}>System</button></div>
            {[
              ["Provider", status.provider_error ? "needs attention" : "ready"],
              ["API keys", `${(data.keys || []).filter((k: any) => k.set).length} set`],
              ["Memory", status.learn?.background ? "learning on" : "learning off"],
              ["MCP", `${Object.keys(data.mcp || {}).length} servers`],
              ["Plugins", `${(data.plugins?.enabled || []).length} enabled`],
            ].map(([a, b]) => <div className="minirow" key={a}><b>{a}</b><span>{b}</span></div>)}
            <button className="btn ghost" onClick={async () => { const r = await post("system", { action: "backup" }); setMsg(r.path ? `Backup created: ${r.path}` : r.error || "Backup finished"); }}>Create backup</button>
            {(logs.errors || []).slice(0, 3).map((line: string, i: number) => <pre className="log-chip" key={i}>{compact(line, 140)}</pre>)}
          </section>
        </aside>
      </div>
    </>
  );
}
