import { useEffect, useMemo, useState } from "react";
import { api, post } from "../lib/api";

type SchemaField = {
  path: string;
  type?: string;
  default?: any;
  label?: string;
  description?: string;
  enum?: string[];
  group?: string;
  restart?: string;
};

function flatten(obj: any, prefix = ""): [string, any][] {
  const out: [string, any][] = [];
  for (const [k, v] of Object.entries(obj || {})) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) out.push(...flatten(v, key));
    else out.push([key, v]);
  }
  return out;
}

function getByPath(obj: any, path: string): any {
  if (obj && Object.prototype.hasOwnProperty.call(obj, path)) return obj[path];
  return path.split(".").reduce((node, part) => (
    node && typeof node === "object" ? node[part] : undefined
  ), obj);
}

const QUICK_KEYS = [
  "tools.exec_mode",
  "display.reasoning",
  "agent.reasoning_effort",
  "gateway.busy_mode",
];

const QUICK_TOGGLES = [
  "learn.background",
  "learn.auto_apply",
  "learn.auto_apply_skills",
  "memory.enabled",
];

const FALLBACK_ENUMS: Record<string, string[]> = {
  "tools.exec_mode": ["auto", "ask", "smart", "allowlist", "deny", "full"],
  "display.reasoning": ["summary", "live", "off"],
  "agent.reasoning_effort": ["off", "minimal", "low", "medium", "high", "xhigh"],
  "gateway.busy_mode": ["queue", "steer", "interrupt"],
};

const GROUP_TITLES: Record<string, string> = {
  agent: "Agent",
  tools: "Tools & permissions",
  model: "Model",
  memory: "Memory",
  learn: "Learning",
  display: "Display",
  gateway: "Gateway",
  web: "Web access",
  server: "Dashboard & server",
  mcp: "MCP",
  cron: "Scheduled tasks",
  goals: "Goals",
  onboarding: "Onboarding",
  lsp: "Language server",
};

const groupTitle = (field: SchemaField) => (
  field.group || GROUP_TITLES[field.path.split(".")[0]] ||
  field.path.split(".")[0].replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())
);

const labelFor = (field: SchemaField) => (
  field.label || field.path.split(".").slice(1).join(".") || field.path
);

export function ConfigPage() {
  const [cfg, setCfg] = useState<any>(undefined);
  const [schema, setSchema] = useState<SchemaField[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [msg, setMsg] = useState("");

  async function load() {
    const [nextCfg, nextSchema] = await Promise.all([api("config"), api("config/schema")]);
    setCfg(nextCfg);
    setSchema(nextSchema.fields || []);
  }

  useEffect(() => { load().catch((e) => setCfg({ __err: String(e) })); }, []);
  const meta = useMemo(() => new Map(schema.map((f) => [f.path, f])), [schema]);

  if (cfg === undefined) return <><Head /><div className="empty"><span className="spin" /> loading...</div></>;
  if (cfg.__err) return <><Head /><div className="card mut">Couldn't load: {cfg.__err}</div></>;

  const allRows = flatten(cfg).filter(([k]) => !k.includes("seen"));
  const fields: SchemaField[] = allRows.map(([path, value]) => ({
    path,
    default: meta.get(path)?.default,
    type: meta.get(path)?.type || (value === null ? "null" : typeof value),
    ...(meta.get(path) || {}),
  }));
  const query = q.trim().toLowerCase();
  const filtered = query
    ? fields.filter((f) => `${f.path} ${labelFor(f)} ${f.description || ""} ${groupTitle(f)}`.toLowerCase().includes(query))
    : fields;
  const groups: Record<string, SchemaField[]> = {};
  for (const f of filtered) (groups[groupTitle(f)] ||= []).push(f);
  const groupNames = Object.keys(groups).sort();
  const searching = query.length > 0;

  function toText(value: any): string {
    return value && typeof value === "object" ? JSON.stringify(value, null, 2) : String(value ?? "");
  }

  function parse(raw: string): any {
    const text = raw.trim();
    if (!text) return "";
    if (text === "true") return true;
    if (text === "false") return false;
    if (text === "null") return null;
    if (/^-?\d+(\.\d+)?$/.test(text)) return Number(text);
    if ((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]"))) return JSON.parse(text);
    return raw;
  }

  async function saveValue(key: string, value: any) {
    try { await post("config", { key, value }); setMsg(`${key} saved`); await load(); }
    catch (e) { setMsg("Error: " + String(e)); }
  }

  async function saveText(key: string, raw: string): Promise<boolean> {
    let value: any;
    try { value = parse(raw); } catch (e) { setMsg("Invalid JSON: " + String(e)); return false; }
    try {
      await post("config", { key, value });
      setMsg(`${key} saved`);
      setEdit((prev) => { const next = { ...prev }; delete next[key]; return next; });
      await load();
      return true;
    } catch (e) {
      setMsg("Error: " + String(e));
      return false;
    }
  }

  async function addSetting() {
    const key = newKey.trim();
    if (!key) return;
    if (await saveText(key, newValue)) { setNewKey(""); setNewValue(""); }
  }

  function FieldRow({ field }: { field: SchemaField }) {
    const value = getByPath(cfg, field.path);
    const enums = field.enum || FALLBACK_ENUMS[field.path];
    const leaf = labelFor(field);
    const description = field.description || `Default: ${toText(field.default) || "(empty)"}`;
    const restart = field.restart ? ` · ${field.restart}` : "";

    if (typeof value === "boolean") {
      return (
        <label className="field schema-field">
          <span className="field-info"><b>{leaf}</b><small>{description}{restart}</small></span>
          <input type="checkbox" checked={value} onChange={(e) => saveValue(field.path, e.target.checked)} />
        </label>
      );
    }
    if (enums) {
      const options = enums.includes(String(value ?? "")) ? enums : [String(value ?? ""), ...enums];
      return (
        <label className="field schema-field">
          <span className="field-info"><b>{leaf}</b><small>{description}{restart}</small></span>
          <select value={String(value ?? "")} onChange={(e) => saveValue(field.path, e.target.value)}>
            {options.map((o) => <option key={o} value={o}>{o || "(empty)"}</option>)}
          </select>
        </label>
      );
    }
    if (value && typeof value === "object") {
      const editing = field.path in edit;
      return (
        <label className="field wide schema-field">
          <span className="field-info"><b>{leaf}</b><small>{description}{restart}</small></span>
          {editing
            ? <span className="field-edit">
                <textarea rows={4} value={edit[field.path]} autoFocus onChange={(e) => setEdit({ ...edit, [field.path]: e.target.value })} />
                <button className="btn sm" onClick={() => saveText(field.path, edit[field.path])}>Save</button>
                <button className="btn ghost sm" onClick={() => setEdit((prev) => { const next = { ...prev }; delete next[field.path]; return next; })}>Cancel</button>
              </span>
            : <button className="field-val mono" onClick={() => setEdit({ ...edit, [field.path]: toText(value) })}>{toText(value).slice(0, 90) || "[]"}</button>}
        </label>
      );
    }
    const cur = String(value ?? "");
    const pending = field.path in edit ? edit[field.path] : cur;
    const commit = () => {
      if (pending !== cur) saveValue(field.path, parse(pending));
      else setEdit((prev) => { const next = { ...prev }; delete next[field.path]; return next; });
    };
    return (
      <label className="field schema-field">
        <span className="field-info"><b>{leaf}</b><small>{description}{restart}</small></span>
        <input
          type={typeof value === "number" ? "number" : "text"}
          value={pending}
          onChange={(e) => setEdit({ ...edit, [field.path]: e.target.value })}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLInputElement).blur(); } }}
        />
      </label>
    );
  }

  return (
    <>
      <Head count={filtered.length} />
      <div className="panel" style={{ marginBottom: 14 }}>
        <h3>Quick settings</h3>
        <div className="quick-grid">
          {QUICK_KEYS.map((key) => {
            const field = meta.get(key) || { path: key, enum: FALLBACK_ENUMS[key] };
            const value = getByPath(cfg, key);
            const options = field.enum || FALLBACK_ENUMS[key] || [];
            return (
              <label key={key}>{field.label || labelFor(field)}
                <select value={String(value ?? "")} onChange={(e) => saveValue(key, e.target.value)}>
                  {options.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </label>
            );
          })}
        </div>
        <div className="toggle-grid">
          {QUICK_TOGGLES.map((key) => {
            const field = meta.get(key) || { path: key };
            return (
              <label className="toggle-row" key={key}>
                <span>{field.label || labelFor(field)}</span>
                <input type="checkbox" checked={Boolean(getByPath(cfg, key))} onChange={(e) => saveValue(key, e.target.checked)} />
              </label>
            );
          })}
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 14 }}>
        <input placeholder="Search settings by name, group, or description..." value={q} onChange={(e) => setQ(e.target.value)} />
        {msg && <div className="mut" style={{ marginTop: 8 }}>{msg}</div>}
      </div>

      {groupNames.map((g) => {
        const isOpen = searching || open[g];
        return (
          <div className="panel group" key={g}>
            <button className="group-h" onClick={() => setOpen((prev) => ({ ...prev, [g]: !prev[g] }))}>
              <b>{g}</b>
              <span className="mut">{groups[g].length} · {isOpen ? "open" : "closed"}</span>
            </button>
            {isOpen && <div className="fields">{groups[g].map((field) => <FieldRow field={field} key={field.path} />)}</div>}
          </div>
        );
      })}

      <div className="panel" style={{ marginTop: 14 }}>
        <h3>Add a custom setting</h3>
        <div className="grid c3" style={{ gap: 10, alignItems: "end" }}>
          <label>Key<input value={newKey} onChange={(e) => setNewKey(e.target.value)} placeholder="tools.exec_mode" /></label>
          <label>Value<input value={newValue} onChange={(e) => setNewValue(e.target.value)} placeholder='true, 3, "text", ["core"]' /></label>
          <button className="btn" onClick={addSetting}>Save</button>
        </div>
        <div className="mut" style={{ marginTop: 8 }}>Secrets belong in API Keys.</div>
      </div>
    </>
  );
}

function Head({ count }: { count?: number }) {
  return <div className="head"><h1>Settings</h1><span className="crumb">{count != null ? `${count} settings · ` : ""}schema-backed config.yaml editor</span></div>;
}
