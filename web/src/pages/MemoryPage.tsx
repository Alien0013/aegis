import { useEffect, useState } from "react";
import { api, post } from "../lib/api";

export function MemoryPage() {
  const [data, setData] = useState<any>(null);
  const [target, setTarget] = useState<"user" | "memory">("user");
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");
  async function load() { try { setData(await api("memory")); } catch (e) { setData({ __err: String(e) }); } }
  useEffect(() => { load(); }, []);
  const entries = (raw: string) => (raw || "").split("§").map((s) => s.trim()).filter(Boolean);

  async function add() {
    if (!text.trim()) return; setMsg("");
    try { const r = await post("memory", { action: "add", target, content: text }); setMsg(r.result || "saved"); setText(""); await load(); }
    catch (e) { setMsg("✗ " + String(e)); }
  }
  async function remove(t: string, match: string) {
    try { await post("memory", { action: "remove", target: t, match }); await load(); } catch { /* ignore */ }
  }

  return (
    <>
      <div className="head"><h1>Memory</h1><span className="crumb">USER.md = profile · MEMORY.md = agent notes</span></div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Add a fact</h3>
        <div className="composer">
          <select value={target} onChange={(e) => setTarget(e.target.value as any)} style={{ width: 130 }}>
            <option value="user">user profile</option><option value="memory">agent notes</option>
          </select>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Prefers concise answers" onKeyDown={(e) => e.key === "Enter" && add()} />
          <button className="btn" onClick={add}>Add</button>
        </div>
        {msg && <div className="mut" style={{ marginTop: 8 }}>{msg}</div>}
      </div>
      {data && !data.__err && (["user", "memory"] as const).map((t) => (
        <div className="card" key={t} style={{ marginBottom: 14 }}>
          <h3>{t === "user" ? "About the user (USER.md)" : "Agent notes (MEMORY.md)"}</h3>
          {!entries(data[t]).length && <div className="empty">empty</div>}
          {entries(data[t]).map((e, i) => (
            <div className="row" key={i}>
              <span style={{ whiteSpace: "pre-wrap" }}>{e}</span>
              <button className="btn ghost" onClick={() => remove(t, e.slice(0, 40))}>delete</button>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
