import { useEffect, useRef, useState } from "react";
import { postStream } from "../lib/api";

type Msg = { role: "user" | "bot"; text: string; tools?: string[] };

export function Chat() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => { logRef.current?.scrollTo(0, logRef.current.scrollHeight); }, [msgs]);

  function patchBot(fn: (m: Msg) => Msg) {
    setMsgs((all) => { const c = [...all]; for (let i = c.length - 1; i >= 0; i--) { if (c[i].role === "bot") { c[i] = fn(c[i]); break; } } return c; });
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", text }, { role: "bot", text: "", tools: [] }]);
    setBusy(true);
    try {
      await postStream("chat/stream", { message: text, session_id: session }, (ev) => {
        if (ev.type === "start" && ev.session_id) setSession(ev.session_id);
        else if (ev.type === "event") {
          const e = ev.event || {};
          const name = e.name || e.type;
          if (e.type === "assistant_delta" && e.text) patchBot((m) => ({ ...m, text: m.text + e.text }));
          else if (e.type === "tool_start" && name) patchBot((m) => ({ ...m, tools: [...(m.tools || []), name] }));
        } else if (ev.type === "final") {
          patchBot((m) => ({ ...m, text: m.text || ev.reply || "(no response)" }));
          if (ev.session_id) setSession(ev.session_id);
        } else if (ev.type === "error") {
          patchBot((m) => ({ ...m, text: "error: " + (ev.reply || "") }));
        }
      });
    } catch (e) {
      patchBot((m) => ({ ...m, text: "error: " + String(e) }));
    } finally { setBusy(false); }
  }

  return (
    <>
      <div className="head"><h1>Chat</h1><span className="crumb">{session || "new session"}</span></div>
      <div className="card">
        <div className="chatlog" ref={logRef}>
          {!msgs.length && <div className="empty">Say hello — streams live, same agent as the terminal.</div>}
          {msgs.map((m, i) => (
            <div className={"msg " + m.role} key={i}>
              {m.tools && m.tools.length > 0 && (
                <div className="mut" style={{ fontSize: 12, marginBottom: 6 }}>⚙ {m.tools.join(" · ")}</div>
              )}
              {m.text || (busy && i === msgs.length - 1 ? <span className="spin" /> : "")}
            </div>
          ))}
        </div>
        <div className="composer">
          <textarea rows={2} value={input} placeholder="Message AEGIS…  (Enter to send, Shift+Enter for newline)"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <button className="btn" onClick={send} disabled={busy}>Send</button>
        </div>
      </div>
    </>
  );
}
