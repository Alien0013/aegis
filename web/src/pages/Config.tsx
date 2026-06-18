import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { patch, post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { titleCase } from "../lib/format";
import { cn } from "../lib/cn";
import { Badge, Button, Card, Empty, Loading, PageHeader, toast } from "../components/ui";
import { AutoField, type FieldSchema } from "../components/AutoField";

interface Schema { sections: Record<string, { fields: FieldSchema[] }> }
interface KeyRow { key: string; set: boolean; source?: string; length?: number }
interface EnvPayload { env_path?: string; keys?: KeyRow[] }
interface ConfigExport {
  paths?: { home?: string; config?: string; env?: string };
  env?: EnvPayload;
}

export function Config() {
  const schemaQ = useApi<Schema>("config/schema");
  const valuesQ = useApi<Record<string, unknown>>("config");
  const exportQ = useApi<ConfigExport>("config/export");
  const envQ = useApi<EnvPayload>("env");
  const [active, setActive] = useState("");
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [mode, setMode] = useState<"summary" | "settings" | "yaml">("summary");
  const [busy, setBusy] = useState(false);

  const sections = useMemo(() => Object.entries(schemaQ.data?.sections || {})
    .sort(([a], [b]) => a.localeCompare(b)), [schemaQ.data]);
  const current = active || sections[0]?.[0] || "";
  const fields = (schemaQ.data?.sections[current]?.fields || [])
    .filter((f) => !["dict", "null"].includes(f.type) || f.enum); // skip complex nested

  function valueOf(f: FieldSchema): unknown {
    if (f.path in edits) return edits[f.path];
    // GET /api/config returns a FLAT dotted-key map ({"agent.max_iterations": 123}),
    // so read the key directly — walking it as a nested object always missed and the
    // page fell back to defaults, making saved settings look unsaved.
    const v = valuesQ.data?.[f.path];
    return v === undefined ? f.default : v;
  }
  const dirtyCount = Object.keys(edits).length;

  async function save() {
    setBusy(true);
    try {
      const updates = Object.entries(edits).map(([path, value]) => ({ path, value }));
      const r = await patch<{ ok?: boolean; errors?: Record<string, string> }>("config/fields", { updates });
      if (r.ok === false) toast(`Validation failed: ${Object.values(r.errors || {})[0] || "error"}`, "err");
      else { toast(`Saved ${updates.length} setting${updates.length === 1 ? "" : "s"}`); setEdits({}); valuesQ.reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }

  return (
    <>
      <PageHeader title="Config" sub="~/.aegis/config.yaml — grouped settings"
        actions={<>
          <div className="flex rounded-[var(--radius)] border border-border p-0.5 text-xs">
            {(["summary", "settings", "yaml"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)}
                className={cn("rounded-[calc(var(--radius)-2px)] px-2.5 py-1", mode === m ? "bg-primary text-primary-fg" : "text-dim")}>
                {m === "summary" ? "Summary" : m === "settings" ? "Settings" : "YAML"}</button>
            ))}
          </div>
          {mode === "settings" && (
            <Button variant="primary" icon="check" onClick={save} disabled={busy || !dirtyCount}>
              {dirtyCount ? `Save ${dirtyCount}` : "Saved"}
            </Button>
          )}
        </>} />

      {(schemaQ.error || valuesQ.error || exportQ.error || envQ.error) && (
        <Card><Empty icon="alert">Couldn't load — {schemaQ.error || valuesQ.error || exportQ.error || envQ.error}</Empty></Card>
      )}
      {(schemaQ.loading || valuesQ.loading || exportQ.loading || envQ.loading) && <Loading />}

      {mode === "summary" && valuesQ.data && <ConfigSummary values={valuesQ.data} paths={exportQ.data?.paths} env={envQ.data || exportQ.data?.env} />}

      {mode === "yaml" && schemaQ.data && <YamlEditor />}

      {mode === "settings" && schemaQ.data && (
        <div className="flex gap-[var(--gap)]">
          <aside className="hidden w-48 shrink-0 md:block">
            <div className="scroll-thin sticky top-0 max-h-[80vh] space-y-0.5 overflow-y-auto rounded-[calc(var(--radius)+2px)] border border-border bg-surface p-1.5">
              {sections.map(([name]) => (
                <button key={name} onClick={() => setActive(name)}
                  className={cn("block w-full rounded-[var(--radius)] px-2.5 py-1.5 text-left text-sm",
                    current === name ? "bg-primary/15 font-medium text-primary" : "text-dim hover:bg-surface-2")}>
                  {titleCase(name)}
                </button>
              ))}
            </div>
          </aside>
          <div className="min-w-0 flex-1">
            <Card title={titleCase(current)} sub={`${fields.length} settings`}>
              {!fields.length && <Empty>No editable settings in this section.</Empty>}
              <div className="divide-y divide-border">
                {fields.map((f) => (
                  <AutoField key={f.path} field={f} value={valueOf(f)}
                    onChange={(v) => setEdits((e) => ({ ...e, [f.path]: v }))} />
                ))}
              </div>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}

function ConfigSummary({
  values,
  paths,
  env,
}: {
  values: Record<string, unknown>;
  paths?: ConfigExport["paths"];
  env?: EnvPayload;
}) {
  const keys = env?.keys || [];
  const provider = text(values["model.provider"], "not set");
  const model = text(values["model.default"], "not set");
  const maxTurns = text(values["agent.max_iterations"], "not set");
  const execMode = text(values["tools.exec_mode"], "not set");
  const terminal = text(values["tools.terminal_backend"], "not set");
  const reasoning = text(values["display.reasoning"], "off");
  const effort = text(values["agent.reasoning_effort"], "off");
  const serviceTier = text(values["agent.service_tier"], "normal") || "normal";
  const compression = Number(values["agent.compression.threshold"] ?? 0.5);
  const tailFraction = Number(values["agent.compression.tail_fraction"] ?? 0.25);
  const configuredKeys = keys.filter((key) => key.set);
  const platformKeys = ["TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"];
  const modelKeys = keys.filter((key) => /(OPENAI|ANTHROPIC|GOOGLE|GEMINI|OPENROUTER|GROQ|DEEPSEEK|XAI|MISTRAL)_/.test(key.key));

  return (
    <div className="space-y-[var(--gap)]">
      <div className="grid gap-[var(--gap)] lg:grid-cols-3">
        <SummaryBlock title="Paths" rows={[
          ["Config", paths?.config || "~/.aegis/config.yaml"],
          ["Secrets", env?.env_path || paths?.env || "~/.aegis/.env"],
          ["Home", paths?.home || "~/.aegis"],
        ]} />
        <SummaryBlock title="Model" rows={[
          ["Provider", provider],
          ["Model", model],
          ["Max turns", maxTurns],
          ["Fast mode", serviceTier],
        ]} />
        <SummaryBlock title="Runtime" rows={[
          ["Terminal", terminal],
          ["Exec mode", execMode],
          ["Reasoning", reasoning],
          ["Model effort", effort],
        ]} />
      </div>

      <div className="grid gap-[var(--gap)] lg:grid-cols-[1.1fr_0.9fr]">
        <Card title="API Keys" sub={`${configuredKeys.length}/${keys.length || 0} known keys set`}
          actions={<Link to="/env" className="font-mono text-xs text-primary hover:underline">Env</Link>}>
          <div className="grid gap-2 md:grid-cols-2">
            {modelKeys.slice(0, 10).map((key) => <KeyStatus key={key.key} row={key} />)}
            {!modelKeys.length && <div className="text-sm text-faint">No provider keys discovered.</div>}
          </div>
        </Card>

        <Card title="Messaging Platforms">
          <div className="space-y-2">
            {platformKeys.map((name) => {
              const row = keys.find((key) => key.key === name);
              return <KeyStatus key={name} row={row || { key: name, set: false }} />;
            })}
          </div>
        </Card>
      </div>

      <Card title="Context Compression" sub="Current compaction thresholds">
        <div className="grid gap-3 md:grid-cols-4">
          <Metric label="Enabled" value="yes" tone="success" />
          <Metric label="Threshold" value={percent(compression, "50%")} />
          <Metric label="Target ratio" value={percent(tailFraction, "25%")} />
          <Metric label="Protect last" value={text(values["agent.compression.preserve_last"], "20")} />
        </div>
      </Card>
    </div>
  );
}

function SummaryBlock({ title, rows }: { title: string; rows: Array<[string, string]> }) {
  return (
    <Card title={title}>
      <div className="space-y-2">
        {rows.map(([label, value]) => (
          <div key={label} className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2 text-sm">
            <span className="text-faint">{label}</span>
            <span className="min-w-0 truncate font-mono text-text" title={value}>{value}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function KeyStatus({ row }: { row: KeyRow }) {
  return (
    <div className="flex min-w-0 items-center gap-2 border-b border-border/70 pb-2 last:border-0 last:pb-0">
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-text" title={row.key}>{row.key}</span>
      <Badge tone={row.set ? "success" : "neutral"}>{row.set ? "set" : "missing"}</Badge>
      {row.source && <Badge tone="neutral">{row.source}</Badge>}
    </div>
  );
}

function Metric({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "neutral" | "success" }) {
  return (
    <div className="border border-border bg-surface-2/60 p-3">
      <div className="font-mono text-[10px] uppercase tracking-wide text-faint">{label}</div>
      <div className={cn("mt-1 font-mono text-lg font-semibold", tone === "success" ? "text-success" : "text-text")}>{value}</div>
    </div>
  );
}

function text(value: unknown, fallback = ""): string {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value);
}

function percent(value: number, fallback: string): string {
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : fallback;
}

// Raw YAML escape hatch (advanced).
function YamlEditor() {
  const { data, loading, reload } = useApi<{ path?: string; raw?: string }>("config/yaml");
  const [raw, setRaw] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const text = raw ?? data?.raw ?? "";

  async function save() {
    setBusy(true);
    try {
      const r = await post<{ ok?: boolean; error?: string }>("config/yaml", { raw: text });
      if (r.ok === false) toast(r.error || "Invalid YAML", "err");
      else { toast("YAML saved"); setRaw(null); reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }

  if (loading) return <Loading />;
  return (
    <Card title="config.yaml" sub="Advanced — validated on save, previous version backed up"
      actions={<Button variant="primary" icon="check" onClick={save} disabled={busy || raw === null}>Save</Button>}>
      <textarea value={text} spellCheck={false} onChange={(e) => setRaw(e.target.value)}
        className="scroll-thin h-[60vh] w-full resize-none rounded-[var(--radius)] border border-border bg-surface-2 p-3 font-mono text-xs text-text outline-none focus:border-primary/60" />
    </Card>
  );
}
