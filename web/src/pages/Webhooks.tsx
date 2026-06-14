import { useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { compact } from "../lib/format";
import { Button, Card, Empty, Input, Loading, PageHeader, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface Hook { name: string; prompt: string }

export function Webhooks() {
  const { data, loading, error, reload } = useApi<Hook[]>("webhooks");
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");

  async function act(body: Record<string, unknown>) {
    try { const r = await post<{ ok?: boolean; error?: string }>("webhooks", body); if (r.error) toast(r.error, "err"); else { toast("Done"); reload(); } }
    catch (e) { toast(String(e), "err"); }
  }

  return (
    <>
      <PageHeader title="Webhooks" sub={data ? `${data.length} endpoint${data.length === 1 ? "" : "s"}` : "Trigger the agent over HTTP"} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <Card title="New webhook">
            <div className="flex flex-wrap items-end gap-2">
              <Input className="w-48" value={name} placeholder="name" onChange={(e) => setName(e.target.value)} />
              <Input className="flex-1" value={prompt} placeholder="prompt to run on trigger" onChange={(e) => setPrompt(e.target.value)} />
              <Button variant="primary" icon="plus" disabled={!name.trim() || !prompt.trim()}
                onClick={() => { act({ action: "add", name: name.trim(), prompt: prompt.trim() }); setName(""); setPrompt(""); }}>Add</Button>
            </div>
          </Card>
          <Card pad={false}>
            {!data.length && <Empty icon="webhooks">No webhooks.</Empty>}
            {data.map((h) => (
              <div key={h.name} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0">
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-sm text-text">{h.name}</div>
                  <div className="truncate text-xs text-faint">{compact(h.prompt, 90)}</div>
                </div>
                <button onClick={() => act({ action: "remove", name: h.name })} className="shrink-0 text-faint hover:text-danger" title="Delete"><Icon name="trash" size={15} /></button>
              </div>
            ))}
          </Card>
        </div>
      )}
    </>
  );
}
