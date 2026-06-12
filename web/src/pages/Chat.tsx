import { useEffect, useRef, useState } from "react";
import { postStream } from "../lib/api";

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
  let g = "⚙";
  if (n.includes("read") || n.includes("file")) g = "📄";
  else if (n.includes("write") || n.includes("edit")) g = "✏️";
  else if (n.includes("bash") || n.includes("shell") || n.includes("exec")) g = "▷";
  else if (n.includes("search") || n.includes("grep") || n.includes("glob")) g = "🔎";
  else if (n.includes("web") || n.includes("fetch") || n.includes("url")) g = "🌐";
  else if (n.includes("memory")) g = "🧠";
  else if (n.includes("kanban") || n.includes("todo")) g = "🗂";
  return <span className="tcard-ico">{g}</span>;
}

function Thinking({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="think">
      <div className="think-h" onClick={() => setOpen((o) => !o)}>
        <span className="think-dot" /> Thinking {open ? "▾" : "▸"}
        {!open && <span className="mut" style={{ marginLeft: 6 }}>{text.slice(-60).replace(/\n/g, " ")}…</span>}
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
            : <span className={"tcard-badge " + t.status}>{t.status === "ok" ? "✓" : "✗"}</span>}
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
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => { logRef.current?.scrollTo(0, logRef.current.scrollHeight); }, [msgs]);

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
    else if (t === "compacting") patchBot((m) => ({ ...m, status: "compacting context…" }));
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
        } else if (ev.type === "error") patchBot((m) => ({ ...m, text: "⚠ " + (ev.reply || "error"), status: "", done: true }));
      });
    } catch (e) {
      patchBot((m) => ({ ...m, text: "⚠ " + String(e), status: "", done: true }));
    } finally {
      setBusy(false);
      patchBot((m) => ({ ...m, status: "", done: true }));
    }
  }

  function newSession() { setSession(""); setMsgs([]); }

  return (
    <>
      <div className="head">
        <h1>Chat</h1>
        <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="crumb">{session || "new session"}</span>
          <button className="btn ghost" onClick={() => setShowCtx((s) => !s)}>{showCtx ? "Hide" : "Context"}</button>
          <button className="btn ghost" onClick={newSession}>New chat</button>
        </span>
      </div>
      <div className="card chatcard">
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
              <div style={{ fontSize: 15, marginBottom: 6 }}>Say hello 👋</div>
              You'll see the agent think and watch every tool it runs, live — same agent as the terminal.
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
          <textarea rows={2} value={input} placeholder="Message AEGIS…  (Enter to send · Shift+Enter for newline)"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <button className="btn" onClick={send} disabled={busy}>{busy ? <span className="spin sm" /> : "Send"}</button>
        </div>
      </div>
    </>
  );
}
