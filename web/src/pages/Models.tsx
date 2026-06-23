import { useEffect, useState } from "react";
import { patch, post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Field, Input, Loading, MetricStrip, PageHeader, Segmented, Select, toast } from "../components/ui";

interface ModelsPayload {
  provider?: string;
  model?: string;
  base_url_override?: string;
  providers?: string[];
  presets?: Record<string, string[]>;
  preset_rows?: Record<string, ModelRow[]>;
  provider_catalog?: ProviderRow[];
  provider_matrix?: ProviderMatrix;
  active?: { context_length?: number; error?: string };
}

interface ProviderRow {
  name?: string;
  base_url?: string;
  origin?: string;
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

interface ProviderMatrix {
  totals: {
    providers: number; ready: number; missing_auth: number; models: number;
    tools: number; vision: number; reasoning: number; streaming: number; known_pricing: number;
  };
  providers: ProviderMatrixRow[];
}

interface ProviderMatrixRow {
  provider: string;
  display_name?: string;
  origin?: string;
  api_mode?: string;
  active?: boolean;
  auth?: { available?: boolean; scheme?: string; methods?: string[]; oauth_status?: string };
  probe?: { status?: string; latency_ms?: number | null; message?: string };
  limits?: { context?: number; max_output?: number };
  capabilities?: Record<string, boolean | undefined>;
  models?: Array<{
    id: string;
    pricing?: { input_per_million?: number; output_per_million?: number; known?: boolean };
  }>;
  model_count?: number;
}

type Window = "7d" | "30d" | "90d";
type ReasoningEffort = "off" | "minimal" | "low" | "medium" | "high" | "xhigh";

const REASONING_VALUES: ReasoningEffort[] = ["off", "minimal", "low", "medium", "high", "xhigh"];

function isFastTier(value: unknown): boolean {
  return ["fast", "priority", "on", "true", "yes"].includes(String(value ?? "").trim().toLowerCase());
}

export function Models() {
  const { data, loading, error, reload } = useApi<ModelsPayload>("models");
  const configQ = useApi<Record<string, unknown>>("config");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [defaultsBusy, setDefaultsBusy] = useState("");
  const [window, setWindow] = useState<Window>("30d");

  useEffect(() => {
    if (data) {
      setProvider(data.provider || "");
      setModel(data.model || "");
      setBaseUrl(data.base_url_override || "");
    }
  }, [data]);

  function providerDefaultBaseUrl(name: string): string {
    const row = (data?.provider_catalog || []).find((item) => item.name === name);
    return row?.origin === "custom" ? (row.base_url || "") : "";
  }

  const presets = (data?.presets || {})[provider] || [];
  const presetRows = (data?.preset_rows || {})[provider] || [];
  const modelRows: ModelRow[] = presetRows.length
    ? presetRows
    : (presets.length ? presets : data?.model ? [data.model] : []).map((id) => ({ id }));
  const mainProvider = data?.provider || "";
  const mainModel = data?.model || "";
  const mainRows = (data?.preset_rows || {})[mainProvider] || [];
  const mainRow = mainRows.find((row) => row.id === mainModel);
  const mainCaps = mainRow?.capabilities || {};
  const providerMatrix = data?.provider_matrix;
  const activeMatrixRows = (providerMatrix?.providers || [])
    .filter((row) => row.active || row.auth?.available || row.origin === "custom")
    .slice(0, 8);
  const reasoningSupported = mainRow ? mainCaps.reasoning_effort !== false : true;
  const fastSupported = mainCaps.fast_mode === true;
  const reasoningDefault = String(configQ.data?.["agent.reasoning_effort"] ?? "medium").trim().toLowerCase();
  const reasoningValue = (REASONING_VALUES.includes(reasoningDefault as ReasoningEffort)
    ? reasoningDefault
    : "medium") as ReasoningEffort;
  const fastOn = isFastTier(configQ.data?.["agent.service_tier"]);

  async function setActive(nextProvider = provider, nextModel = model, nextBaseUrl = baseUrl) {
    setBusy(true);
    try {
      const r = await post<{ ok?: boolean; error?: string; warning?: string }>("models", {
        provider: nextProvider,
        model: nextModel,
        base_url: nextBaseUrl.trim(),
      });
      if (r.ok === false) toast(r.error || "Failed", "err");
      else { toast(r.warning ? `Set note: ${r.warning}` : "Model set", r.warning ? "info" : "ok"); reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }

  async function setDefault(path: "agent.reasoning_effort" | "agent.service_tier", value: string) {
    setDefaultsBusy(path);
    try {
      const r = await patch<{ ok?: boolean; errors?: Record<string, string> }>("config/fields", {
        updates: { [path]: value },
      });
      if (r.ok === false) toast(Object.values(r.errors || {})[0] || "Failed to save defaults", "err");
      else { toast("Model defaults saved", "ok"); configQ.reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setDefaultsBusy(""); }
  }

  async function probe() {
    setBusy(true);
    try {
      const r = await post<{ ok?: boolean; latency_ms?: number; error?: string }>("providers/probe", {
        provider,
        model,
        base_url: baseUrl.trim(),
      });
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
                {(reasoningSupported || fastSupported) && (
                  <div className="border border-border bg-surface-2/35 p-3">
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <div>
                        <div className="font-mono text-xs uppercase tracking-wide text-faint">Defaults</div>
                        <div className="text-xs text-dim">Applies to new sessions, crons, and gateway chats.</div>
                      </div>
                      {configQ.loading && <Badge>loading</Badge>}
                    </div>
                    <div className="flex flex-wrap items-end gap-3">
                      {reasoningSupported && (
                        <Field label="Reasoning effort">
                          <Select
                            value={reasoningValue}
                            disabled={!!defaultsBusy || configQ.loading}
                            onChange={(e) => setDefault("agent.reasoning_effort", e.target.value)}
                          >
                            {REASONING_VALUES.map((level) => (
                              <option key={level} value={level}>{level}</option>
                            ))}
                          </Select>
                        </Field>
                      )}
                      {fastSupported && (
                        <div className="space-y-1">
                          <div className="font-mono text-[10px] font-medium uppercase tracking-wide text-dim">Fast mode</div>
                          <Button
                            icon="zap"
                            variant={fastOn ? "primary" : "outline"}
                            disabled={!!defaultsBusy || configQ.loading}
                            onClick={() => setDefault("agent.service_tier", fastOn ? "normal" : "priority")}
                          >
                            {fastOn ? "Priority" : "Normal"}
                          </Button>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                <div className="grid gap-3 md:grid-cols-2">
                  <Field label="Provider">
                    <Select value={provider} onChange={(e) => {
                      const nextProvider = e.target.value;
                      setProvider(nextProvider);
                      setModel("");
                      setBaseUrl(nextProvider === data.provider ? (data.base_url_override || "") : providerDefaultBaseUrl(nextProvider));
                    }}>
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
                <Field label="Base URL">
                  <Input
                    value={baseUrl}
                    placeholder="http://localhost:11434/v1"
                    onChange={(e) => setBaseUrl(e.target.value)}
                  />
                </Field>
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

          {providerMatrix && (
            <Card
              title="Provider Matrix"
              sub={`${providerMatrix.totals.ready}/${providerMatrix.totals.providers} ready · ${providerMatrix.totals.models} models`}
              pad={false}
            >
              <div className="grid border-b border-border sm:grid-cols-4">
                {[
                  ["Tools", providerMatrix.totals.tools],
                  ["Vision", providerMatrix.totals.vision],
                  ["Reasoning", providerMatrix.totals.reasoning],
                  ["Priced", providerMatrix.totals.known_pricing],
                ].map(([label, value]) => (
                  <div key={String(label)} className="border-border p-3 sm:border-r last:border-r-0">
                    <div className="font-mono text-[10px] uppercase text-faint">{label}</div>
                    <div className="font-mono text-lg font-semibold text-text">{String(value)}</div>
                  </div>
                ))}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-left text-sm">
                  <thead className="border-b border-border bg-surface-2/60 font-mono text-[10px] uppercase text-faint">
                    <tr>
                      <th className="px-3 py-2">Provider</th>
                      <th className="px-3 py-2">Readiness</th>
                      <th className="px-3 py-2">Caps</th>
                      <th className="px-3 py-2">Limits</th>
                      <th className="px-3 py-2">Pricing</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeMatrixRows.map((row) => {
                      const caps = row.capabilities || {};
                      const priced = (row.models || []).filter((model) => model.pricing?.known).length;
                      const samplePrice = (row.models || []).find((model) => model.pricing?.known)?.pricing;
                      return (
                        <tr key={row.provider} className="border-b border-border last:border-b-0">
                          <td className="px-3 py-3">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="font-mono font-semibold text-text">{row.display_name || row.provider}</span>
                              {row.active && <Badge tone="primary">active</Badge>}
                              {row.origin && <Badge tone="neutral">{row.origin}</Badge>}
                            </div>
                            <div className="mt-1 font-mono text-xs text-faint">{row.api_mode || "api"}</div>
                          </td>
                          <td className="px-3 py-3">
                            <div className="flex flex-wrap gap-1.5">
                              <Badge tone={row.auth?.available || row.probe?.status === "ready" ? "success" : "warning"}>
                                {row.probe?.status || "unknown"}
                              </Badge>
                              {row.auth?.scheme && <Badge tone="neutral">{row.auth.scheme}</Badge>}
                            </div>
                          </td>
                          <td className="px-3 py-3">
                            <div className="flex flex-wrap gap-1.5">
                              {caps.tools && <Badge tone="success">tools</Badge>}
                              {caps.streaming && <Badge tone="info">stream</Badge>}
                              {caps.vision && <Badge tone="info">vision</Badge>}
                              {caps.reasoning && <Badge tone="warning">reasoning</Badge>}
                              {caps.structured_output ? <Badge tone="primary">structured</Badge> : <Badge tone="neutral">no structured</Badge>}
                            </div>
                          </td>
                          <td className="px-3 py-3 font-mono text-xs text-dim">
                            <div>{row.limits?.context ? `${row.limits.context.toLocaleString()} ctx` : "ctx unknown"}</div>
                            <div>{row.limits?.max_output ? `${row.limits.max_output.toLocaleString()} out` : "output unknown"}</div>
                            <div>{row.model_count || 0} model{row.model_count === 1 ? "" : "s"}</div>
                          </td>
                          <td className="px-3 py-3 font-mono text-xs text-dim">
                            {samplePrice ? (
                              <>
                                <div>${samplePrice.input_per_million}/M in</div>
                                <div>${samplePrice.output_per_million}/M out</div>
                                <div>{priced}/{row.model_count || 0} priced</div>
                              </>
                            ) : (
                              <div>{priced}/{row.model_count || 0} priced</div>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          <div className="grid gap-[var(--gap)] md:grid-cols-2 xl:grid-cols-3">
            {modelRows.map((row, index) => {
              const caps = row.capabilities || {};
              const capabilityBadges = [
                caps.tool_calls ? { label: "Tools", tone: "success" as const } : null,
                caps.images ? { label: "Vision", tone: "info" as const } : null,
                caps.reasoning_effort ? { label: "Reasoning", tone: "warning" as const } : null,
                caps.reasoning_stream ? { label: "Reason stream", tone: "warning" as const } : null,
                caps.fast_mode ? { label: "Fast", tone: "warning" as const } : null,
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
                  <Button sm onClick={() => setActive(provider || data.provider || "", row.id, baseUrl)} disabled={busy || (row.id === data.model && provider === data.provider)}>
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
