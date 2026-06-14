import { useState } from "react";
import { Link } from "react-router-dom";
import { api, del } from "../lib/api";
import { useApi } from "../lib/useApi";
import { ago, compact } from "../lib/format";
import { cleanTranscript, type RawMessage } from "../lib/transcript";
import { Badge, Button, Card, Empty, Input, Loading, PageHeader, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface SessionRow { id: string; title?: string; updated_at?: string; message_count?: number }

export function Sessions() {
  const { data, loading, error, reload } = useApi<SessionRow[]>("sessions");
  const [q, setQ] = useState("");
  const [openId, setOpenId] = useState("");
  const [detail, setDetail] = useState<{ messages?: RawMessage[] } | null>(null);

  const rows = (data || []).filter((s) =>
    !q || (s.title || s.id).toLowerCase().includes(q.toLowerCase()));

  async function open(id: string) {
    setOpenId(id); setDetail(null);
    try { setDetail(await api(`session?id=${encodeURIComponent(id)}`)); }
    catch (e) { toast(String(e), "err"); }
  }
  async function remove(id: string) {
    try { await del(`sessions/${encodeURIComponent(id)}`); toast("Deleted"); if (openId === id) setOpenId(""); reload(); }
    catch (e) { toast(String(e), "err"); }
  }

  const turns = detail ? cleanTranscript(detail.messages || []) : [];

  return (
    <>
      <PageHeader title="Sessions" sub={data ? `${data.length} total` : "Conversation history"}
        actions={<Input value={q} placeholder="Filter…" onChange={(e) => setQ(e.target.value)} className="w-52" />} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <Card pad={false}>
          {!rows.length && <Empty icon="sessions">No sessions.</Empty>}
          {rows.map((s) => (
            <div key={s.id} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2 last:border-0 hover:bg-surface-2">
              <button onClick={() => open(s.id)} className="min-w-0 flex-1 text-left">
                <div className="truncate text-sm text-text">{compact(s.title || s.id, 60)}</div>
                <div className="text-xs text-faint">{ago(s.updated_at)}{s.message_count ? ` · ${s.message_count} msgs` : ""}</div>
              </button>
              <Link to={`/chat?id=${encodeURIComponent(s.id)}`} className="shrink-0 text-faint hover:text-primary" title="Open in chat">
                <Icon name="chat" size={15} />
              </Link>
              <button onClick={() => remove(s.id)} className="shrink-0 text-faint hover:text-danger" title="Delete">
                <Icon name="trash" size={15} />
              </button>
            </div>
          ))}
        </Card>
      )}

      {/* detail drawer */}
      {openId && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={() => setOpenId("")}>
          <div className="flex h-full w-full max-w-2xl flex-col border-l border-border bg-surface" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-text">Session transcript</div>
                <div className="truncate font-mono text-xs text-faint">{openId}</div>
              </div>
              <div className="flex items-center gap-2">
                <Link to={`/chat?id=${encodeURIComponent(openId)}`}><Button sm icon="chat">Open</Button></Link>
                <button onClick={() => setOpenId("")} className="text-faint hover:text-text"><Icon name="x" size={18} /></button>
              </div>
            </div>
            <div className="scroll-thin flex-1 space-y-3 overflow-y-auto p-4">
              {!detail && <Loading />}
              {detail && !turns.length && <Empty icon="chat">No conversation (only tool/system turns).</Empty>}
              {turns.map((t, i) => (
                <div key={i} className={t.role === "user" ? "flex justify-end" : ""}>
                  <div className={t.role === "user"
                    ? "max-w-[85%] whitespace-pre-wrap break-words rounded-[calc(var(--radius)+2px)] bg-primary/15 px-3 py-2 text-sm text-text"
                    : "max-w-[90%] whitespace-pre-wrap break-words text-sm text-text"}>
                    {t.role === "bot" && <Badge tone="neutral">assistant</Badge>}
                    <div className={t.role === "bot" ? "mt-1" : ""}>{t.text}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
