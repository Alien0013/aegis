import { useEffect, useMemo, useState } from "react";
import { api, patch as apiPatch, post } from "../lib/api";
import { Badge, Button, Toolbar, useToast } from "../lib/ui";

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
const DEFAULT_OPEN_GROUPS = new Set(["Agent", "Model", "Tools & permissions", "Gateway", "Memory", "Display"]);

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
  const [typeFilter, setTypeFilter] = useState("");
  const [changedOnly, setChangedOnly] = useState(false);
  const [restartOnly, setRestartOnly] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [msg, setMsg] = useState("");
  const [active, setActive] = useState("");
  const [yaml, setYaml] = useState(false);
  const [raw, setRaw] = useState("");
  const toast = useToast();

  async function openYaml() {
    try { const r: any = await api("config/yaml"); setRaw(r.raw || ""); setYaml(true); }
    catch (e) { toast(String(e), "err"); }
  }
  async function saveYaml() {
    try {
      const r: any = await post("config/yaml", { raw });
      if (r.ok) { toast(r.backup ? "Saved (backed up)" : "Saved", "ok"); setYaml(false); await load(); }
      else toast(r.error || "Save failed", "err");
    } catch (e) { toast(String(e), "err"); }
  }
  async function backupConfig() {
    try { const r: any = await post("config/backup", {}); toast(r.backup ? "Config backed up" : "Nothing to back up", "ok"); }
    catch (e) { toast(String(e), "err"); }
  }
  async function resetSection(key: string, label: string) {
    if (!key || !confirm(`Reset all "${label}" settings to defaults? A backup is saved first.`)) return;
    try {
      const r: any = await post("config/reset", { section: key });
      if (r.ok) { toast(`${label} reset to defaults`, "ok"); await load(); }
      else toast(r.error || "Reset failed", "err");
    } catch (e) { toast(String(e), "err"); }
  }

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
  const visible = filtered.filter((f) => {
    if (typeFilter && (f.enum ? "enum" : f.type) !== typeFilter) return false;
    if (restartOnly && !f.restart) return false;
    if (changedOnly && JSON.stringify(getByPath(cfg, f.path)) === JSON.stringify(f.default)) return false;
    return true;
  });
  const groups: Record<string, SchemaField[]> = {};
  for (const f of visible) (groups[groupTitle(f)] ||= []).push(f);
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
    try {
      if (meta.has(key)) await apiPatch("config/fields", { updates: [{ path: key, value }] });
      else await post("config", { key, value });
      const leaf = key.split(".").slice(1).join(".") || key;
      setMsg(`${key} saved`);
      toast(`${leaf} → ${typeof value === "boolean" ? (value ? "on" : "off") : JSON.stringify(value)}`, "ok");
      await load();
    }
    catch (e) { setMsg("Error: " + String(e)); toast("Couldn't save " + key, "err"); }
  }

  async function saveText(key: string, raw: string): Promise<boolean> {
    let value: any;
    try { value = parse(raw); } catch (e) { setMsg("Invalid JSON: " + String(e)); return false; }
    try {
      await post("config", { key, value });
      setMsg(`${key} saved`);
      toast(`${key} saved`, "ok");
      setEdit((prev) => { const next = { ...prev }; delete next[key]; return next; });
      await load();
      return true;
    } catch (e) {
      setMsg("Error: " + String(e));
      toast("Couldn't save " + key, "err");
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
    const changed = JSON.stringify(value) !== JSON.stringify(field.default);
    const type = field.enum ? "enum" : field.type || typeof value;
    const metaBadges = (
      <span className="field-badges">
        <Badge status={changed ? "warn" : "idle"}>{changed ? "changed" : type}</Badge>
        {field.restart && <Badge status="warn">{field.restart}</Badge>}
      </span>
    );

    if (typeof value === "boolean") {
      return (
        <label className="field schema-field">
          <span className="field-info"><b>{leaf}</b>{metaBadges}<small>{description}{restart}</small></span>
          <input type="checkbox" checked={value} onChange={(e) => saveValue(field.path, e.target.checked)} />
        </label>
      );
    }
    if (enums) {
      const options = enums.includes(String(value ?? "")) ? enums : [String(value ?? ""), ...enums];
      return (
        <label className="field schema-field">
          <span className="field-info"><b>{leaf}</b>{metaBadges}<small>{description}{restart}</small></span>
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
          <span className="field-info"><b>{leaf}</b>{metaBadges}<small>{description}{restart}</small></span>
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
        <span className="field-info"><b>{leaf}</b>{metaBadges}<small>{description}{restart}</small></span>
        <span className="field-edit compact">
          <input
            type={typeof value === "number" ? "number" : "text"}
            value={pending}
            onChange={(e) => setEdit({ ...edit, [field.path]: e.target.value })}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } }}
          />
          {pending !== cur && <>
            <Button sm onClick={commit} icon="check">Save</Button>
            <Button sm variant="ghost" onClick={() => setEdit((prev) => { const next = { ...prev }; delete next[field.path]; return next; })}>Cancel</Button>
          </>}
        </span>
      </label>
    );
  }

  return (
    <>
      <Head count={filtered.length} />
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <Button sm variant={yaml ? "primary" : "ghost"} icon="config" onClick={() => (yaml ? setYaml(false) : openYaml())}>
          {yaml ? "Form view" : "Edit YAML"}
        </Button>
        <Button sm variant="ghost" icon="bolt" onClick={backupConfig}>Backup config</Button>
        {yaml && <Button sm icon="check" onClick={saveYaml}>Save YAML</Button>}
      </div>
      {yaml ? (
        <div className="panel" style={{ padding: 12 }}>
          <textarea value={raw} onChange={(e) => setRaw(e.target.value)} spellCheck={false}
            style={{ width: "100%", minHeight: "62vh", fontFamily: "var(--mono)", fontSize: 12, lineHeight: 1.5 }} />
          <div className="mut" style={{ marginTop: 8 }}>Edits the whole config.yaml. Validated and backed up before saving.</div>
        </div>
      ) : (
      <>
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

      <div className="panel" style={{ marginBottom: 14, padding: 12 }}>
        <Toolbar q={q} setQ={setQ} placeholder="Search settings by name, group, or description...">
          <select className="compact-select" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            <option value="">All types</option>
            <option value="bool">Booleans</option>
            <option value="enum">Enums</option>
            <option value="str">Text</option>
            <option value="int">Integers</option>
            <option value="float">Numbers</option>
            <option value="list">Lists</option>
            <option value="dict">Objects</option>
          </select>
          <label className="inline-check"><input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} /> Changed</label>
          <label className="inline-check"><input type="checkbox" checked={restartOnly} onChange={(e) => setRestartOnly(e.target.checked)} /> Restart</label>
        </Toolbar>
        {msg && <div className="mut" style={{ marginTop: 8 }}>{msg}</div>}
      </div>

      {searching
        ? groupNames.map((g) => (
            <div className="panel group" key={g}>
              <div className="group-h" style={{ cursor: "default" }}><b>{g}</b><span className="mut">{groups[g].length}</span></div>
              <div className="fields">{groups[g].map((field) => <FieldRow field={field} key={field.path} />)}</div>
            </div>
          ))
        : (
          <div className="config-layout">
            <aside className="config-rail">
              <div className="list-subhead" style={{ marginTop: 0 }}>Sections</div>
              {groupNames.map((g) => (
                <button key={g} className={"config-sec" + ((active || groupNames[0]) === g ? " active" : "")} onClick={() => setActive(g)}>
                  <span>{g}</span><span className="pill">{groups[g].length}</span>
                </button>
              ))}
            </aside>
            <div className="config-main">
              {(() => {
                const sec = groupNames.includes(active) ? active : groupNames[0];
                if (!sec) return <div className="card"><div className="empty small">No settings.</div></div>;
                return (
                  <div className="panel group">
                    <div className="group-h" style={{ cursor: "default" }}>
                      <b>{sec}</b>
                      <span className="row-flex" style={{ gap: 10 }}>
                        <span className="mut">{groups[sec].length} fields · saves instantly</span>
                        <Button sm variant="ghost" onClick={() => resetSection((groups[sec][0]?.path || "").split(".")[0], sec)}>Reset section</Button>
                      </span>
                    </div>
                    <div className="fields">{groups[sec].map((field) => <FieldRow field={field} key={field.path} />)}</div>
                  </div>
                );
              })()}
              <div className="panel" style={{ marginTop: 12 }}>
                <h3>Add a custom setting</h3>
                <div className="grid c3" style={{ gap: 10, alignItems: "end" }}>
                  <label>Key<input value={newKey} onChange={(e) => setNewKey(e.target.value)} placeholder="tools.exec_mode" /></label>
                  <label>Value<input value={newValue} onChange={(e) => setNewValue(e.target.value)} placeholder='true, 3, "text", ["core"]' /></label>
                  <button className="btn" onClick={addSetting}>Save</button>
                </div>
                <div className="mut" style={{ marginTop: 8 }}>Secrets belong in API Keys.</div>
              </div>
            </div>
          </div>
        )}
      </>
      )}
    </>
  );
}

function Head({ count }: { count?: number }) {
  return <div className="head"><h1>Settings</h1><span className="crumb">{count != null ? `${count} settings · ` : ""}schema-backed config.yaml editor</span></div>;
}
