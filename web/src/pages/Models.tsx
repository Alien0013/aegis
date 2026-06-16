import { useEffect, useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Field, Input, Loading, MetricStrip, PageHeader, Segmented, Select, toast } from "../components/ui";

interface ModelsPayload {
  provider?: string;
  model?: string;
  providers?: string[];
  presets?: Record<string, string[]>;
  preset_rows?: Record<string, ModelRow[]>;
  active?: { context_length?: number; error?: string };
}

interface ModelRow {
  id: string;
  label?: string;
  source?: string;
  api_mode?: string;
  capabilities?: Record<string, boolean | undefined>;
  capability_summary?: string;
  context_length?: number;
}

type Window = "7d" | "30d" | "90d";

export function Models() {
  const { data, loading, error, reload } = useApi<ModelsPayload>("models");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [window, setWindow] = useState<Window>("30d");

  useEffect(() => {
    if (data) { setProvider(data.provider || ""); setModel(data.model || ""); }
  }, [data]);

  const presets = (data?.presets || {})[provider] || [];
  const presetRows = (data?.preset_rows || {})[provider] || [];
  const modelRows: ModelRow[] = presetRows.length
    ? presetRows
    : (presets.length ? presets : data?.model ? [data.model] : []).map((id) => ({ id }));

  async function setActive(nextProvider = provider, nextModel = model) {
    setBusy(true);
    try {
      const r = await post<{ ok?: boolean; error?: string; warning?: string }>("models", { provider: nextProvider, model: nextModel });
      if (r.ok === false) toast(r.error || "Failed", "err");
      else { toast(r.warning ? `Set note: ${r.warning}` : "Model set", r.warning ? "info" : "ok"); reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }

  async function probe() {
    setBusy(true);
    try {
      const r = await post<{ ok?: boolean; latency_ms?: number; error?: string }>("providers/probe", { provider });
      toast(r.ok ? `Reachable${r.latency_ms ? ` / ${r.latency_ms}ms` : ""}` : (r.error || "Unreachable"), r.ok ? "ok" : "err");
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }

  return (
    <>
      <PageHeader
        title="Models"
        sub="Provider routing and active model"
        actions={<Segmented<Window> value={window} onChange={setWindow} items={[
          { value: "7d", label: "7D" },
          { value: "30d", label: "30D" },
          { value: "90d", label: "90D" },
        ]} />}
      />
      {error && <Card><Empty icon="alert">Couldn't load - {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <div className="grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_minmax(340px,0.9fr)]">
            <Card pad={false}>
              <div className="border-b border-border px-[var(--pad)] py-3">
                <div className="font-mono text-base font-semibold text-text">Model Settings</div>
                <div className="text-xs text-faint">Applies to new sessions</div>
              </div>
              <div className="space-y-3 p-[var(--pad)]">
                <div className="flex items-center justify-between gap-3 border border-border bg-surface-2/45 p-3">
                  <div className="min-w-0">
                    <div className="font-mono text-xs uppercase tracking-wide text-faint">Main model</div>
                    <div className="truncate font-mono text-sm text-text">{data.provider || "provider"} / {data.model || "model"}</div>
                  </div>
                  <Badge status={data.active?.error ? "error" : "ready"}>{data.active?.error ? "error" : "ready"}</Badge>
                </div>

                <div className="grid gap-3 md:grid-cols-2">
                  <Field label="Provider">
                    <Select value={provider} onChange={(e) => { setProvider(e.target.value); setModel(""); }}>
                      {(data.providers || []).map((p) => <option key={p} value={p}>{p}</option>)}
                    </Select>
                  </Field>
                  <Field label="Known model">
                    <Select value={presets.includes(model) ? model : ""} onChange={(e) => setModel(e.target.value)}>
                      <option value="">custom</option>
                      {presets.map((m) => <option key={m} value={m}>{m}</option>)}
                    </Select>
                  </Field>
                </div>
                <Field label="Model id"><Input value={model} placeholder="model id" onChange={(e) => setModel(e.target.value)} /></Field>
                {data.active?.error && (
                  <div className="border border-danger/35 bg-danger/10 p-2 text-xs text-danger">{data.active.error}</div>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" icon="check" onClick={() => setActive()} disabled={busy || !provider || !model}>Change</Button>
                  <Button icon="activity" onClick={probe} disabled={busy}>Probe</Button>
                </div>
              </div>
            </Card>

            <MetricStrip items={[
              { label: "models", value: modelRows.length || 1 },
              { label: "total sessions", value: data.active?.context_length ? "ctx" : "-" },
              { label: "context", value: data.active?.context_length ? data.active.context_length.toLocaleString() : "-" },
              { label: "window", value: window.toUpperCase() },
            ]} />
          </div>

          <div className="grid gap-[var(--gap)] md:grid-cols-2 xl:grid-cols-3">
            {modelRows.map((row, index) => {
              const caps = row.capabilities || {};
              const capabilityBadges = [
                caps.tool_calls ? { label: "Tools", tone: "success" as const } : null,
                caps.images ? { label: "Vision", tone: "info" as const } : null,
                caps.reasoning_effort ? { label: "Reasoning", tone: "warning" as const } : null,
                caps.reasoning_stream ? { label: "Reason stream", tone: "warning" as const } : null,
                caps.response_state ? { label: "State", tone: "primary" as const } : null,
                caps.dynamic_tools ? { label: "Dynamic tools", tone: "info" as const } : null,
              ].filter(Boolean) as { label: string; tone: "success" | "info" | "warning" | "primary" }[];
              return (
              <Card key={`${provider}:${row.id}`} pad={false}>
                <div className="border-b border-border p-[var(--pad)]">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-mono text-xs text-faint">#{index + 1}</div>
                      <div className="truncate font-mono text-base font-semibold text-text">{row.id}</div>
                      <div className="truncate font-mono text-xs text-faint">{provider || data.provider || "provider"}</div>
                      {(row.context_length || row.api_mode || row.source) && (
                        <div className="mt-1 truncate font-mono text-[11px] text-faint">
                          {row.context_length ? `${row.context_length.toLocaleString()} ctx` : ""}
                          {row.context_length && (row.api_mode || row.source) ? " / " : ""}
                          {row.api_mode || row.source || ""}
                        </div>
                      )}
                    </div>
                    {row.id === data.model && provider === data.provider && <Badge tone="primary">main</Badge>}
                  </div>
                </div>
                <div className="space-y-3 p-[var(--pad)]">
                  <div className="flex flex-wrap gap-1.5">
                    {capabilityBadges.length
                      ? capabilityBadges.map((badge) => <Badge key={badge.label} tone={badge.tone}>{badge.label}</Badge>)
                      : <Badge>basic</Badge>}
                  </div>
                  <Button sm onClick={() => setActive(provider || data.provider || "", row.id)} disabled={busy || (row.id === data.model && provider === data.provider)}>
                    Use as
                  </Button>
                </div>
              </Card>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}
