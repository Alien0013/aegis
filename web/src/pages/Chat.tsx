import { useEffect, useRef, useState } from "react";
import { post } from "../lib/api";

type Msg = { role: "user" | "bot"; text: string };

export function Chat() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => { logRef.current?.scrollTo(0, logRef.current.scrollHeight); }, [msgs]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      const r = await post("chat", { message: text, session_id: session });
      if (r.session_id) setSession(r.session_id);
      setMsgs((m) => [...m, { role: "bot", text: r.reply || "(no response)" }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "bot", text: "error: " + String(e) }]);
    } finally { setBusy(false); }
  }

  return (
    <>
      <div className="head"><h1>Chat</h1><span className="crumb">{session || "new session"}</span></div>
      <div className="card">
        <div className="chatlog" ref={logRef}>
          {!msgs.length && <div className="empty">Say hello — this runs the same agent as the terminal.</div>}
          {msgs.map((m, i) => <div className={"msg " + m.role} key={i}>{m.text}</div>)}
          {busy && <div className="msg bot"><span className="spin" /> thinking…</div>}
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
