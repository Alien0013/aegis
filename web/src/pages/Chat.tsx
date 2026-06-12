import { useEffect, useRef, useState } from "react";
import { api, postStream } from "../lib/api";
import { Icon } from "../lib/icons";
import { compact, dateish } from "../lib/format";

type ToolCard = { id: string; name: string; target: string; status: "running" | "ok" | "error"; preview: string };
type Msg = {
  role: "user" | "bot";
  text: string;
  thinking: string;
  tools: ToolCard[];
  status: string;        // live status line (iteration / compacting)
  done: boolean;
};

const emptyBot = (): Msg => ({ role: "bot", text: "", thinking: "", tools: [], status: "", done: false });

function ToolIcon({ name }: { name: string }) {
  const n = name.toLowerCase();
  let icon = "tools";
  if (n.includes("read") || n.includes("file")) icon = "logs";
  else if (n.includes("write") || n.includes("edit")) icon = "config";
  else if (n.includes("bash") || n.includes("shell") || n.includes("exec")) icon = "system";
  else if (n.includes("search") || n.includes("grep") || n.includes("glob")) icon = "overview";
  else if (n.includes("memory")) icon = "memory";
  else if (n.includes("kanban") || n.includes("todo")) icon = "kanban";
  return <span className="tcard-ico"><Icon n={icon} /></span>;
}

function Thinking({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="think">
      <div className="think-h" onClick={() => setOpen((o) => !o)}>
        <span className="think-dot" /> Thinking {open ? "v" : ">"}
        {!open && <span className="mut" style={{ marginLeft: 6 }}>{text.slice(-60).replace(/\n/g, " ")}...</span>}
      </div>
      {open && <div className="think-body">{text}</div>}
    </div>
  );
}

function ToolCardView({ t }: { t: ToolCard }) {
  return (
    <div className={"tcard " + t.status}>
      <ToolIcon name={t.name} />
      <div className="tcard-main">
        <div className="tcard-top">
          <b>{t.name}</b>
          {t.status === "running" ? <span className="spin sm" />
            : <span className={"tcard-badge " + t.status}>{t.status === "ok" ? "OK" : "ERR"}</span>}
        </div>
        {t.target && <code className="tcard-target">{t.target}</code>}
        {t.preview && <div className="tcard-out">{t.preview}</div>}
      </div>
    </div>
  );
}

export function Chat() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState("");
  const [showCtx, setShowCtx] = useState(false);
  const [cwd, setCwd] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [sessions, setSessions] = useState<any[]>([]);
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => { logRef.current?.scrollTo(0, logRef.current.scrollHeight); }, [msgs]);
  useEffect(() => { api("sessions").then((s) => setSessions(Array.isArray(s) ? s : [])).catch(() => setSessions([])); }, []);

  function patchBot(fn: (m: Msg) => Msg) {
    setMsgs((all) => {
      const c = [...all];
      for (let i = c.length - 1; i >= 0; i--) if (c[i].role === "bot") { c[i] = fn(c[i]); break; }
      return c;
    });
  }

  function applyEvent(e: any) {
    const t = e.type;
    if (t === "assistant_delta") patchBot((m) => ({ ...m, text: m.text + (e.text || "") }));
    else if (t === "assistant_message") patchBot((m) => (m.text ? m : { ...m, text: e.text || "" }));
    else if (t === "reasoning_delta") patchBot((m) => ({ ...m, thinking: m.thinking + (e.text || "") }));
    else if (t === "tool_start" && e.name)
      patchBot((m) => ({ ...m, status: "", tools: [...m.tools, { id: e.id || String(m.tools.length), name: e.name, target: e.target || "", status: "running", preview: "" }] }));
    else if (t === "tool_result")
      patchBot((m) => ({
        ...m,
        tools: m.tools.map((tc) => (tc.id === e.id || (!e.id && tc.status === "running"))
          ? { ...tc, status: e.status === "error" ? "error" : "ok", preview: e.target || "" } : tc),
      }));
    else if (t === "iteration") patchBot((m) => ({ ...m, status: `step ${e.n}/${e.max}` }));
    else if (t === "compacting") patchBot((m) => ({ ...m, status: "compacting context..." }));
    else if (t === "compacted") patchBot((m) => ({ ...m, status: "" }));
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", text, thinking: "", tools: [], status: "", done: true }, emptyBot()]);
    setBusy(true);
    try {
      await postStream("chat/stream", {
        message: text, session_id: session,
        cwd: cwd.trim() || undefined, provider: provider.trim() || undefined, model: model.trim() || undefined,
      }, (ev) => {
        if (ev.type === "start" && ev.session_id) setSession(ev.session_id);
        else if (ev.type === "event") applyEvent(ev.event || {});
        else if (ev.type === "final") {
          patchBot((m) => ({ ...m, text: m.text || ev.reply || "(no response)", status: "", done: true }));
          if (ev.session_id) setSession(ev.session_id);
        } else if (ev.type === "error") patchBot((m) => ({ ...m, text: "Error: " + (ev.reply || "error"), status: "", done: true }));
      });
    } catch (e) {
      patchBot((m) => ({ ...m, text: "Error: " + String(e), status: "", done: true }));
    } finally {
      setBusy(false);
      patchBot((m) => ({ ...m, status: "", done: true }));
    }
  }

  function newSession() { setSession(""); setMsgs([]); }
  async function resume(id: string) {
    setSession(id);
    setMsgs([]);
    try {
      const d = await api(`session?id=${encodeURIComponent(id)}`);
      const loaded: Msg[] = (d.messages || []).filter((m: any) => m.content).map((m: any) => ({
        role: m.role === "user" ? "user" : "bot",
        text: m.content,
        thinking: "",
        tools: [],
        status: "",
        done: true,
      }));
      setMsgs(loaded);
    } catch {
      setMsgs([]);
    }
  }

  return (
    <>
      <div className="head">
        <div>
          <h1>Chat</h1>
          <span className="crumb">{session ? `session ${compact(session, 34)}` : "new dashboard session"}</span>
        </div>
        <span className="actions">
          <span className="crumb">{session || "new session"}</span>
          <button className="btn ghost" onClick={() => setShowCtx((s) => !s)}>{showCtx ? "Hide" : "Context"}</button>
          <button className="btn ghost" onClick={newSession}>New chat</button>
        </span>
      </div>
      <div className="chat-layout">
        <aside className="session-rail">
          <h3>Recent sessions</h3>
          {!sessions.length && <div className="empty small">No sessions yet.</div>}
          {sessions.slice(0, 12).map((s) => (
            <button className={"session-chip" + (session === s.id ? " active" : "")} key={s.id} onClick={() => resume(s.id)}>
              <b>{compact(s.title || s.id, 44)}</b>
              <span>{dateish(s.updated_at)}</span>
            </button>
          ))}
        </aside>
        <section className="panel chatcard">
        {showCtx && (
          <div className="grid c3" style={{ gap: 8, marginBottom: 10 }}>
            <input value={cwd} onChange={(e) => setCwd(e.target.value)} placeholder="working dir (cwd)" />
            <input value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="provider (optional)" />
            <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="model (optional)" />
          </div>
        )}
        <div className="chatlog" ref={logRef}>
          {!msgs.length && (
            <div className="empty">
              <div style={{ fontSize: 15, marginBottom: 6 }}>Start a dashboard turn</div>
              You can watch reasoning summaries, tool calls, and the final answer in one stream.
            </div>
          )}
          {msgs.map((m, i) =>
            m.role === "user"
              ? <div className="msg user" key={i}>{m.text}</div>
              : (
                <div className="botwrap" key={i}>
                  <Thinking text={m.thinking} />
                  {m.tools.map((t) => <ToolCardView t={t} key={t.id} />)}
                  {(m.text || (busy && i === msgs.length - 1)) && (
                    <div className="msg bot">
                      {m.text || <span className="typing"><i /><i /><i /></span>}
                    </div>
                  )}
                  {!m.done && m.status && <div className="statusline"><span className="spin sm" /> {m.status}</div>}
                </div>
              ),
          )}
        </div>
        <div className="composer">
          <textarea rows={2} value={input} placeholder="Message AEGIS...  (Enter to send / Shift+Enter for newline)"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <button className="btn" onClick={send} disabled={busy}>{busy ? <span className="spin sm" /> : "Send"}</button>
        </div>
        </section>
      </div>
    </>
  );
}
