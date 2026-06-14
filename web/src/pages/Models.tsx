import { useEffect, useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Field, Input, Loading, PageHeader, Select, toast } from "../components/ui";

interface ModelsPayload {
  provider?: string;
  model?: string;
  providers?: string[];
  presets?: Record<string, string[]>;
  active?: { context_length?: number; error?: string };
}

export function Models() {
  const { data, loading, error, reload } = useApi<ModelsPayload>("models");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data) { setProvider(data.provider || ""); setModel(data.model || ""); }
  }, [data]);

  const presets = (data?.presets || {})[provider] || [];

  async function setActive() {
    setBusy(true);
    try {
      const r = await post<{ ok?: boolean; error?: string; warning?: string }>("models", { provider, model });
      if (r.ok === false) toast(r.error || "Failed", "err");
      else { toast(r.warning ? `Set (note: ${r.warning})` : "Model set", r.warning ? "info" : "ok"); reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }

  async function probe() {
    setBusy(true);
    try {
      const r = await post<{ ok?: boolean; latency_ms?: number; error?: string }>("providers/probe", { provider });
      toast(r.ok ? `Reachable${r.latency_ms ? ` · ${r.latency_ms}ms` : ""}` : (r.error || "Unreachable"), r.ok ? "ok" : "err");
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }

  return (
    <>
      <PageHeader title="Models" sub="Pick the active provider + model" />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="grid gap-[var(--gap)] lg:grid-cols-2">
          <Card title="Active">
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-mono text-sm text-text">{data.model || "—"}</div>
                  <div className="text-xs text-faint">{data.provider || "no provider"}</div>
                </div>
                <Badge status={data.active?.error ? "error" : "ok"}>{data.active?.error ? "error" : "ready"}</Badge>
              </div>
              {data.active?.context_length ? (
                <div className="text-xs text-dim">context window: {data.active.context_length.toLocaleString()}</div>
              ) : null}
              {data.active?.error && (
                <div className="rounded-[var(--radius)] border border-danger/30 bg-danger/10 p-2 text-xs text-danger">{data.active.error}</div>
              )}
            </div>
          </Card>

          <Card title="Switch model" actions={<Button sm variant="ghost" icon="activity" onClick={probe} disabled={busy}>Probe</Button>}>
            <div className="space-y-3">
              <Field label="Provider">
                <Select value={provider} onChange={(e) => { setProvider(e.target.value); setModel(""); }}>
                  {(data.providers || []).map((p) => <option key={p} value={p}>{p}</option>)}
                </Select>
              </Field>
              <Field label="Model" hint={presets.length ? "Pick a known model or type your own below" : "Type the model id"}>
                {presets.length > 0 && (
                  <Select value={presets.includes(model) ? model : ""} onChange={(e) => setModel(e.target.value)}>
                    <option value="">— custom —</option>
                    {presets.map((m) => <option key={m} value={m}>{m}</option>)}
                  </Select>
                )}
              </Field>
              <Input value={model} placeholder="model id" onChange={(e) => setModel(e.target.value)} />
              <Button variant="primary" icon="check" onClick={setActive} disabled={busy || !provider || !model}>Set active</Button>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
