import { useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Input, Loading, PageHeader, Select, toast } from "../components/ui";
import { Icon } from "../components/icons";

type Target = "user" | "memory";
const split = (raw: string) => (raw || "").split("§").map((s) => s.trim()).filter(Boolean);

export function Memory() {
  const { data, loading, error, reload } = useApi<{ memory?: string; user?: string }>("memory");
  const [target, setTarget] = useState<Target>("user");
  const [text, setText] = useState("");

  async function add() {
    if (!text.trim()) return;
    try { await post("memory", { action: "add", target, content: text }); toast("Saved"); setText(""); reload(); }
    catch (e) { toast(String(e), "err"); }
  }
  async function remove(t: Target, match: string) {
    try { await post("memory", { action: "remove", target: t, match }); toast("Removed"); reload(); }
    catch (e) { toast(String(e), "err"); }
  }

  return (
    <>
      <PageHeader title="Memory" sub="What AEGIS remembers — USER.md (about you) · MEMORY.md (agent notes)" />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <Card title="Add a fact">
            <div className="flex flex-wrap items-end gap-2">
              <Select value={target} onChange={(e) => setTarget(e.target.value as Target)} className="w-40">
                <option value="user">User profile</option>
                <option value="memory">Agent notes</option>
              </Select>
              <Input className="flex-1" value={text} placeholder="e.g. Prefers concise answers"
                onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
              <Button variant="primary" icon="plus" onClick={add}>Add</Button>
            </div>
          </Card>

          {([["user", "About you (USER.md)"], ["memory", "Agent notes (MEMORY.md)"]] as const).map(([t, title]) => {
            const entries = split(t === "user" ? data.user || "" : data.memory || "");
            return (
              <Card key={t} title={title} sub={`${entries.length} ${entries.length === 1 ? "entry" : "entries"}`} pad={false}>
                {!entries.length && <Empty icon="memory">Nothing yet.</Empty>}
                {entries.map((e, i) => (
                  <div key={i} className="flex items-start justify-between gap-3 border-b border-border px-[var(--pad)] py-2 last:border-0">
                    <span className="min-w-0 whitespace-pre-wrap break-words text-sm text-text">{e}</span>
                    <button onClick={() => remove(t, e.slice(0, 60))} className="shrink-0 text-faint hover:text-danger" title="Delete">
                      <Icon name="trash" size={14} />
                    </button>
                  </div>
                ))}
              </Card>
            );
          })}
          <Badge tone="info">Tip: the agent also writes here automatically as it learns.</Badge>
        </div>
      )}
    </>
  );
}
