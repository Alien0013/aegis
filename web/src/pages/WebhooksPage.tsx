import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { Button, Card, Empty, Field, PageHeader, useToast } from "../lib/ui";

export function WebhooksPage() {
  const [hooks, setHooks] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function load() {
    try {
      const d = await api("webhooks");
      const raw = d.webhooks || d.hooks || d;
      setHooks(Array.isArray(raw) ? raw
        : Object.entries(raw || {}).map(([k, v]: any) => ({ name: k, prompt: typeof v === "string" ? v : v?.prompt })));
    } catch { setHooks([]); }
  }
  useEffect(() => { load(); }, []);

  async function add() {
    if (!name.trim() || !prompt.trim()) return;
    setBusy(true);
    try { await post("webhooks", { action: "add", name: name.trim(), prompt }); toast(`Webhook ${name} created`, "ok"); setName(""); setPrompt(""); await load(); }
    catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }
  async function remove(n: string) { await post("webhooks", { action: "remove", name: n }); toast("Removed"); await load(); }

  return (
    <>
      <PageHeader title="Webhooks" sub={`${hooks.length} hook${hooks.length === 1 ? "" : "s"}`} />
      <div className="stack">
        <Card title="Create a webhook">
          <div className="grid c3" style={{ alignItems: "end" }}>
            <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="deploy-notify" /></Field>
            <div style={{ gridColumn: "2 / 4", alignSelf: "end" }}><Button onClick={add} disabled={busy} icon="plus">Add</Button></div>
          </div>
          <Field label="Prompt to run when called"><textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="summarize the payload and post it to #ops" /></Field>
          {name.trim() && <div className="mut">POST to <code>/hooks/{name.trim()}</code> to trigger.</div>}
        </Card>
        <Card title="Webhooks" pad={false}>
          {!hooks.length && <Empty small>No webhooks yet.</Empty>}
          <div style={{ padding: hooks.length ? "2px 14px 6px" : 0 }}>
            {hooks.map((h, i) => (
              <div className="row" key={h.name || i}>
                <span style={{ minWidth: 0 }}><b>{h.name}</b> <span className="mut">— {(h.prompt || "").slice(0, 64)}</span></span>
                <Button variant="danger" sm onClick={() => remove(h.name)}>Remove</Button>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
