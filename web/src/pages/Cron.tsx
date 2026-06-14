import { useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { ago, compact } from "../lib/format";
import { Badge, Button, Card, Empty, Input, Loading, PageHeader, Toggle, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface Job {
  id: string; name?: string; schedule: string; prompt: string; enabled: boolean;
  next_run?: string; last_run?: string; last_status?: string; one_shot?: boolean;
}

export function Cron() {
  const { data, loading, error, reload } = useApi<Job[]>("cron");
  const [schedule, setSchedule] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);

  async function act(body: Record<string, unknown>) {
    setBusy(true);
    try {
      const r = await post<{ ok?: boolean; error?: string; id?: string }>("cron", body);
      if (r.error) toast(r.error, "err"); else { toast("Done"); reload(); }
    } catch (e) { toast(String(e), "err"); } finally { setBusy(false); }
  }
  async function add() {
    if (!schedule.trim() || !prompt.trim()) return;
    await act({ action: "add", schedule: schedule.trim(), prompt: prompt.trim() });
    setSchedule(""); setPrompt("");
  }

  return (
    <>
      <PageHeader title="Schedules" sub={data ? `${data.length} cron job${data.length === 1 ? "" : "s"}` : "Recurring agent tasks"} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <Card title="New schedule">
            <div className="flex flex-wrap items-end gap-2">
              <Input className="w-48" value={schedule} placeholder="@daily · 30m · 0 9 * * *" onChange={(e) => setSchedule(e.target.value)} />
              <Input className="flex-1" value={prompt} placeholder="what to run…" onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && add()} />
              <Button variant="primary" icon="plus" onClick={add} disabled={busy}>Add</Button>
            </div>
          </Card>
          <Card pad={false}>
            {!data.length && <Empty icon="cron">No schedules yet.</Empty>}
            {data.map((j) => (
              <div key={j.id} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0">
                <Toggle on={j.enabled} disabled={busy} onChange={() => act({ action: "toggle", id: j.id, enabled: !j.enabled })} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm text-primary">{j.schedule}</span>
                    {j.one_shot && <Badge tone="info">one-shot</Badge>}
                    {j.last_status && <Badge status={j.last_status}>{j.last_status}</Badge>}
                  </div>
                  <div className="truncate text-xs text-dim">{compact(j.prompt, 80)}</div>
                  <div className="text-[11px] text-faint">{j.next_run ? `next ${ago(j.next_run)}` : ""}{j.last_run ? ` · last ${ago(j.last_run)}` : ""}</div>
                </div>
                <button onClick={() => act({ action: "run", id: j.id })} className="shrink-0 text-faint hover:text-primary" title="Run now"><Icon name="zap" size={15} /></button>
                <button onClick={() => act({ action: "remove", id: j.id })} className="shrink-0 text-faint hover:text-danger" title="Delete"><Icon name="trash" size={15} /></button>
              </div>
            ))}
          </Card>
        </div>
      )}
    </>
  );
}
