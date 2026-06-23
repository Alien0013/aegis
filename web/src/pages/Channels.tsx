import type { ReactNode } from "react";
import { useState } from "react";
import { useApi } from "../lib/useApi";
import { patch, post, put } from "../lib/api";
import { Badge, Button, Card, Empty, Field, Input, Loading, PageHeader, toast } from "../components/ui";
import { compact, dateish, titleCase } from "../lib/format";
import { Icon } from "../components/icons";

interface EnvField {
  key: string;
  required?: boolean;
  set?: boolean;
  description?: string;
}
interface ChannelRow {
  id?: string; channel?: string; name?: string; label?: string;
  configured?: boolean; ready?: boolean; enabled?: boolean; active?: boolean;
  env?: string[]; env_vars?: string[]; missing_env_vars?: string[];
  setup?: string; profile?: Record<string, unknown>;
}
interface PlatformRow extends Omit<ChannelRow, "env_vars"> {
  env_vars?: EnvField[];
  required_env_vars?: string[];
  optional_env_vars?: string[];
  metadata?: { optional_env?: string[]; required_env?: string[] } & Record<string, unknown>;
}
type CatalogPayload = { channels?: ChannelRow[]; catalog?: ChannelRow[]; enabled?: string[] } & Record<string, unknown>;
type PlatformPayload = { platforms?: PlatformRow[] };
interface OutboxMessage {
  id: number;
  platform?: string;
  chat_id?: string;
  thread_id?: string;
  text?: string;
  attempts?: number;
  status?: string;
  next_at?: number | string;
  created_at?: number | string;
  text_truncated?: boolean;
}
interface OutboxPayload {
  stats?: Record<string, number> & { statuses?: Record<string, number> };
  messages?: OutboxMessage[];
  dead_letters?: OutboxMessage[];
}

function channelId(row: { id?: string; channel?: string; name?: string; label?: string }): string {
  return row.id || row.channel || row.name || (row.label || "").toLowerCase().replace(/\s+/g, "-");
}
function envFieldKey(field: string | EnvField): string {
  return typeof field === "string" ? field : field.key;
}
function envFieldSet(field: string | EnvField): boolean | undefined {
  return typeof field === "string" ? undefined : field.set;
}

export function Channels() {
  const { data, loading, error, reload } = useApi<CatalogPayload>("gateway/channels/catalog");
  const platforms = useApi<PlatformPayload>("messaging/platforms");
  const status = useApi<Record<string, unknown>>("gateway/status");
  const outbox = useApi<OutboxPayload>("gateway/outbox?limit=12");
  const [configuring, setConfiguring] = useState<{ row: ChannelRow; platform?: PlatformRow } | null>(null);
  const [outboxBusy, setOutboxBusy] = useState("");

  const rows: ChannelRow[] = (Array.isArray(data?.channels) ? data!.channels
    : Array.isArray(data?.catalog) ? data!.catalog : []) as ChannelRow[];
  const platformRows = new Map((platforms.data?.platforms || []).map((row) => [channelId(row), row]));
  const activeChannels = ((status.data?.channels as string[]) || data?.enabled || []) as string[];

  async function probe(ch: string) {
    try {
      const r = await post<{ ok?: boolean; error?: string }>(`gateway/channels/${encodeURIComponent(ch)}/probe`, {});
      toast(r.ok ? "Reachable" : (r.error || "Unreachable"), r.ok ? "ok" : "err");
    } catch (e) { toast(String(e), "err"); }
  }
  async function setActive(channels: string[]) {
    try { await post("gateway/channels", { channels }); toast("Updated"); status.reload(); reload(); }
    catch (e) { toast(String(e), "err"); }
  }
  async function outboxAction(message: OutboxMessage, action: "retry" | "discard") {
    setOutboxBusy(`${action}:${message.id}`);
    try {
      const r = await post<{ ok?: boolean; error?: string }>(`gateway/outbox/${message.id}/${action}`, {});
      if (r.ok === false) toast(r.error || `${titleCase(action)} failed`, "err");
      else {
        toast(action === "retry" ? "Queued for retry" : "Message discarded");
        outbox.reload();
        status.reload();
      }
    } catch (e) { toast(String(e), "err"); }
    finally { setOutboxBusy(""); }
  }

  return (
    <>
      <PageHeader title="Channels" sub={activeChannels.length ? `active: ${activeChannels.join(", ")}` : "Messaging platforms served by the gateway"} />
      {error && <Card><Empty icon="alert">Couldn't load - {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <div className="grid gap-[var(--gap)] md:grid-cols-2 xl:grid-cols-3">
            {!rows.length && <Card><Empty icon="channels">No channel catalog available.</Empty></Card>}
            {rows.map((c) => {
              const id = channelId(c);
              const platform = platformRows.get(id);
              const on = activeChannels.includes(id);
              const configured = platform?.configured ?? c.configured ?? c.ready ?? false;
              const optionalCount = platform?.optional_env_vars?.length || platform?.metadata?.optional_env?.length || 0;
              return (
                <Card key={id} pad={false}>
                  <div className="border-b border-border p-[var(--pad)]">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-mono text-base font-semibold text-text">{c.label || titleCase(id)}</div>
                        <div className="truncate font-mono text-xs text-faint">{id}</div>
                      </div>
                      <Badge tone={on ? "success" : configured ? "info" : "neutral"}>{on ? "active" : configured ? "configured" : "needs token"}</Badge>
                    </div>
                  </div>
                  <div className="space-y-3 p-[var(--pad)]">
                    <div className="flex items-center gap-2 text-xs text-dim">
                      <Icon name="channels" size={14} className={on ? "text-success" : "text-faint"} />
                      {on ? "Delivery enabled for inbound and outbound messages." : "Enable this adapter when credentials are ready."}
                    </div>
                    {optionalCount > 0 && (
                      <div className="flex items-center gap-2 text-xs text-dim">
                        <Icon name="shield" size={14} className="text-faint" />
                        {optionalCount} hardening control{optionalCount === 1 ? "" : "s"}
                      </div>
                    )}
                    <div className="grid grid-cols-3 gap-2">
                      <Button sm onClick={() => probe(id)}>Test</Button>
                      <Button sm onClick={() => setConfiguring({ row: c, platform })}>Configure</Button>
                      <Button sm variant={on ? "danger" : "primary"}
                        onClick={() => setActive(on ? activeChannels.filter((x) => x !== id) : [...activeChannels, id])}>
                        {on ? "Disable" : "Enable"}
                      </Button>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
          <OutboxPanel
            payload={outbox.data}
            loading={outbox.loading}
            error={outbox.error}
            busy={outboxBusy}
            onReload={outbox.reload}
            onAction={outboxAction}
          />
        </div>
      )}
      {configuring && (
        <ChannelConfig
          row={configuring.row}
          platform={configuring.platform}
          onClose={() => setConfiguring(null)}
          onSaved={() => { setConfiguring(null); status.reload(); platforms.reload(); reload(); }}
        />
      )}
    </>
  );
}

function OutboxPanel({
  payload,
  loading,
  error,
  busy,
  onReload,
  onAction,
}: {
  payload: OutboxPayload | null;
  loading: boolean;
  error: string;
  busy: string;
  onReload: () => void;
  onAction: (message: OutboxMessage, action: "retry" | "discard") => void;
}) {
  const stats = payload?.stats || {};
  const rows = payload?.dead_letters?.length ? payload.dead_letters : payload?.messages || [];
  return (
    <Card pad={false}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-[var(--pad)]">
        <div>
          <div className="font-mono text-base font-semibold text-text">Gateway Outbox</div>
          <div className="text-xs text-faint">pending {stats.pending || 0} / failed {stats.failed || 0} / discarded {stats.discarded || 0}</div>
        </div>
        <Button sm icon="refresh" onClick={onReload}>Refresh</Button>
      </div>
      {error && <Empty icon="alert">Couldn't load outbox - {error}</Empty>}
      {loading && <Loading />}
      {!loading && !error && !rows.length && <Empty icon="check">No failed deliveries.</Empty>}
      {!loading && !error && !!rows.length && (
        <div className="divide-y divide-border">
          {rows.map((message) => (
            <div key={message.id} className="grid gap-3 p-[var(--pad)] lg:grid-cols-[minmax(0,1fr)_auto]">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge status={message.status || "pending"}>{message.status || "pending"}</Badge>
                  <span className="font-mono text-xs text-primary">{message.platform || "gateway"}:{message.chat_id || "unknown"}</span>
                  <span className="font-mono text-xs text-faint">#{message.id}</span>
                  <span className="text-xs text-faint">{dateish(message.created_at)}</span>
                </div>
                <div className="mt-2 line-clamp-2 text-xs text-dim">{compact(message.text || "", 260)}</div>
                <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
                  <Mini>attempts {message.attempts || 0}</Mini>
                  {message.thread_id && <Mini>thread {compact(message.thread_id, 28)}</Mini>}
                  {message.next_at && <Mini>next {dateish(message.next_at)}</Mini>}
                  {message.text_truncated && <Mini>truncated</Mini>}
                </div>
              </div>
              <div className="flex items-start gap-2">
                <Button sm icon="refresh" disabled={busy === `retry:${message.id}`} onClick={() => onAction(message, "retry")}>Retry</Button>
                <Button sm variant="danger" icon="trash" disabled={busy === `discard:${message.id}`} onClick={() => onAction(message, "discard")}>Discard</Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function Mini({ children }: { children: ReactNode }) {
  return <span className="border border-border bg-surface-2 px-1.5 py-px font-mono text-faint">{children}</span>;
}

function ChannelConfig({
  row,
  platform,
  onClose,
  onSaved,
}: {
  row: ChannelRow;
  platform?: PlatformRow;
  onClose: () => void;
  onSaved: () => void;
}) {
  const id = channelId(row);
  const profile = row.profile || {};
  const envVars: (string | EnvField)[] = platform?.env_vars || row.env_vars || row.env || [];
  const missing = new Set(platform?.missing_env_vars || row.missing_env_vars || []);
  const [envForm, setEnvForm] = useState<Record<string, string>>({});
  const [clearEnv, setClearEnv] = useState<Record<string, boolean>>({});
  const [form, setForm] = useState({
    personality: String(profile.personality || profile.profile || ""),
    provider: String(profile.provider || ""),
    model: String(profile.model || ""),
    reasoning_effort: String(profile.reasoning_effort || ""),
    service_tier: String(profile.service_tier || ""),
    busy_mode: String(profile.busy_mode || ""),
  });

  async function save() {
    try {
      const env = Object.fromEntries(Object.entries(envForm).filter(([, value]) => value.trim()));
      const clear_env = Object.entries(clearEnv).filter(([, value]) => value).map(([key]) => key);
      if (Object.keys(env).length || clear_env.length) {
        const r = await put<{ ok?: boolean; error?: string }>(`messaging/platforms/${encodeURIComponent(id)}`, {
          env,
          clear_env,
        });
        if (r.ok === false) { toast(r.error || "Environment save failed", "err"); return; }
      }
      const r = await patch<{ ok?: boolean; error?: string }>(`gateway/channels/${encodeURIComponent(id)}`, {
        enabled: true,
        ...form,
      });
      if (r.ok === false) toast(r.error || "Channel save failed", "err");
      else { toast("Channel saved"); onSaved(); }
    } catch (e) { toast(String(e), "err"); }
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center bg-black/55 pt-[8vh] backdrop-blur-sm" onMouseDown={onClose}>
      <div className="max-h-[84vh] w-full max-w-xl overflow-y-auto border border-border bg-bg shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <div className="font-mono text-base font-semibold text-text">Configure {titleCase(id)}</div>
            <div className="text-xs text-faint">Adapter credentials and routing</div>
          </div>
          <button onClick={onClose} className="text-faint hover:text-text"><Icon name="x" size={18} /></button>
        </div>
        <div className="space-y-3 p-4">
          <div className="border border-border bg-surface-2/55 p-3 text-xs text-dim">
            <div>{row.setup || "Credentials are read from environment variables."}</div>
            {!!envVars.length && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {envVars.map((field) => {
                  const key = envFieldKey(field);
                  const isSet = envFieldSet(field);
                  return (
                    <Badge key={key} tone={missing.has(key) ? "warning" : isSet === false ? "neutral" : "success"}>
                      {key}
                    </Badge>
                  );
                })}
              </div>
            )}
          </div>
          {!!envVars.length && (
            <div className="grid gap-3 sm:grid-cols-2">
              {envVars.map((field) => {
                const key = envFieldKey(field);
                const required = typeof field !== "string" && !!field.required;
                const isSet = envFieldSet(field);
                return (
                  <div key={key} className="space-y-1.5">
                    <Field label={key} hint={required ? "required" : "optional"}>
                      <Input
                        value={envForm[key] || ""}
                        placeholder={isSet ? "set" : "unset"}
                        onChange={(e) => setEnvForm({ ...envForm, [key]: e.target.value })}
                      />
                    </Field>
                    <label className="flex items-center gap-2 font-mono text-[11px] text-dim">
                      <input
                        type="checkbox"
                        checked={!!clearEnv[key]}
                        onChange={(e) => setClearEnv({ ...clearEnv, [key]: e.target.checked })}
                      />
                      Clear
                    </label>
                  </div>
                );
              })}
            </div>
          )}
          <Field label="Profile"><Input value={form.personality} placeholder="default personality" onChange={(e) => setForm({ ...form, personality: e.target.value })} /></Field>
          <Field label="Provider"><Input value={form.provider} placeholder="provider override" onChange={(e) => setForm({ ...form, provider: e.target.value })} /></Field>
          <Field label="Model"><Input value={form.model} placeholder="model override" onChange={(e) => setForm({ ...form, model: e.target.value })} /></Field>
          <Field label="Reasoning effort"><Input value={form.reasoning_effort} placeholder="low, medium, high" onChange={(e) => setForm({ ...form, reasoning_effort: e.target.value })} /></Field>
          <Field label="Fast mode"><Input value={form.service_tier} placeholder="priority or normal" onChange={(e) => setForm({ ...form, service_tier: e.target.value })} /></Field>
          <Field label="Busy mode"><Input value={form.busy_mode} placeholder="queue, reject, interrupt" onChange={(e) => setForm({ ...form, busy_mode: e.target.value })} /></Field>
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon="check" onClick={save}>Save & Enable</Button>
        </div>
      </div>
    </div>
  );
}
