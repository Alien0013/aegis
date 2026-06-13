import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { Button, Card, Empty, PageHeader, useToast } from "../lib/ui";

export function MemoryPage() {
  const [data, setData] = useState<any>(null);
  const [target, setTarget] = useState<"user" | "memory">("user");
  const [text, setText] = useState("");
  const toast = useToast();
  async function load() { try { setData(await api("memory")); } catch (e) { setData({ __err: String(e) }); } }
  useEffect(() => { load(); }, []);
  const entries = (raw: string) => (raw || "").split("§").map((s) => s.trim()).filter(Boolean);

  async function add() {
    if (!text.trim()) return;
    try { const r = await post("memory", { action: "add", target, content: text }); toast(r.result || "Saved", "ok"); setText(""); await load(); }
    catch (e) { toast(String(e), "err"); }
  }
  async function remove(t: string, match: string) {
    try { await post("memory", { action: "remove", target: t, match }); toast("Removed"); await load(); } catch (e) { toast(String(e), "err"); }
  }

  return (
    <>
      <PageHeader title="Memory" sub="USER.md = profile · MEMORY.md = agent notes" />
      <div className="stack">
        <Card title="Add a fact">
          <div className="row-flex">
            <select value={target} onChange={(e) => setTarget(e.target.value as any)} style={{ width: 150 }}>
              <option value="user">user profile</option><option value="memory">agent notes</option>
            </select>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Prefers concise answers" onKeyDown={(e) => e.key === "Enter" && add()} />
            <Button onClick={add} icon="plus">Add</Button>
          </div>
        </Card>
        {data && !data.__err && (["user", "memory"] as const).map((t) => (
          <Card key={t} title={t === "user" ? "About the user (USER.md)" : "Agent notes (MEMORY.md)"} pad={false}>
            {!entries(data[t]).length && <Empty small>empty</Empty>}
            <div style={{ padding: entries(data[t]).length ? "2px 14px 6px" : 0 }}>
              {entries(data[t]).map((e, i) => (
                <div className="row" key={i}>
                  <span style={{ whiteSpace: "pre-wrap", minWidth: 0 }}>{e}</span>
                  <Button variant="danger" sm onClick={() => remove(t, e.slice(0, 40))}>Delete</Button>
                </div>
              ))}
            </div>
          </Card>
        ))}
        {data?.__err && <Card><Empty>Couldn't load — {data.__err}</Empty></Card>}
      </div>
    </>
  );
}
