import { useEffect, useMemo, useState } from "react";
import { api, patch as apiPatch, post } from "../lib/api";
import { Icon } from "../lib/icons";
import { Badge, Button, Toggle, Toolbar, useToast } from "../lib/ui";

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

const QUICK_KEYS = ["tools.exec_mode", "display.reasoning", "agent.reasoning_effort", "gateway.busy_mode"];
const QUICK_TOGGLES = ["learn.background", "learn.auto_apply", "learn.auto_apply_skills", "memory.enabled"];

const FALLBACK_ENUMS: Record<string, string[]> = {
  "tools.exec_mode": ["auto", "ask", "smart", "allowlist", "deny", "full"],
  "display.reasoning": ["summary", "live", "off"],
  "agent.reasoning_effort": ["off", "minimal", "low", "medium", "high", "xhigh"],
  "gateway.busy_mode": ["queue", "steer", "interrupt"],
};

const GROUP_TITLES: Record<string, string> = {
  agent: "Agent", tools: "Tools", model: "Model", memory: "Memory", learn: "Learning",
  display: "Display", gateway: "Gateway", web: "Web", server: "Server", mcp: "MCP",
  cron: "Cron", goals: "Goals", onboarding: "Onboarding", lsp: "LSP", compression: "Compression",
  curator: "Curator", delegation: "Delegation", skills: "Skills", voice: "Voice", security: "Security",
};

// Section key (top-level path segment) → sidebar icon.
const SEC_ICON: Record<string, string> = {
  agent: "agents", model: "models", tools: "tools", memory: "memory", gateway: "channels",
  display: "overview", learn: "skills", skills: "skills", mcp: "tools", cron: "cron",
  security: "config", web: "search", server: "system", lsp: "config", goals: "kanban",
  onboarding: "config", compression: "logs", curator: "cron", delegation: "agents", voice: "chat",
};

const groupTitle = (field: SchemaField) => (
  field.group || GROUP_TITLES[field.path.split(".")[0]] ||
  field.path.split(".")[0].replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())
);
const labelFor = (field: SchemaField) => (
  field.label || field.path.split(".").slice(1).join(".") || field.path
);
const sectionKey = (fields: SchemaField[]) => (fields[0]?.path || "").split(".")[0];

export function ConfigPage() {
  const [cfg, setCfg] = useState<any>(undefined);
  const [schema, setSchema] = useState<SchemaField[]>([]);
  const [q, setQ] = useState("");
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [typeFilter, setTypeFilter] = useState("");
  const [changedOnly, setChangedOnly] = useState(false);
  const [restartOnly, setRestartOnly] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [active, setActive] = useState("");
  const [yaml, setYaml] = useState(false);
  const [raw, setRaw] = useState("");
  const [path, setPath] = useState("");
  const toast = useToast();

  async function load() {
    const [nextCfg, nextSchema] = await Promise.all([api("config"), api("config/schema")]);
    setCfg(nextCfg);
    setSchema(nextSchema.fields || []);
    api("config/yaml").then((r: any) => setPath(r.path || "")).catch(() => {});
  }
  useEffect(() => { load().catch((e) => setCfg({ __err: String(e) })); }, []);
  const meta = useMemo(() => new Map(schema.map((f) => [f.path, f])), [schema]);

  async function openYaml() {
    try { const r: any = await api("config/yaml"); setRaw(r.raw || ""); setPath(r.path || path); setYaml(true); }
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

  function toText(value: any): string {
    return value && typeof value === "object" ? JSON.stringify(value, null, 2) : String(value ?? "");
  }
  function parse(rawv: string): any {
    const text = rawv.trim();
    if (!text) return "";
    if (text === "true") return true;
    if (text === "false") return false;
    if (text === "null") return null;
    if (/^-?\d+(\.\d+)?$/.test(text)) return Number(text);
    if ((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]"))) return JSON.parse(text);
    return rawv;
  }
  async function saveValue(key: string, value: any) {
    try {
      if (meta.has(key)) await apiPatch("config/fields", { updates: [{ path: key, value }] });
      else await post("config", { key, value });
      const leaf = key.split(".").slice(1).join(".") || key;
      toast(`${leaf} → ${typeof value === "boolean" ? (value ? "on" : "off") : JSON.stringify(value)}`, "ok");
      await load();
    } catch (e) { toast("Couldn't save " + key, "err"); }
  }
  async function saveText(key: string, rawv: string): Promise<boolean> {
    let value: any;
    try { value = parse(rawv); } catch (e) { toast("Invalid JSON: " + String(e), "err"); return false; }
    try {
      await post("config", { key, value });
      toast(`${key} saved`, "ok");
      setEdit((prev) => { const next = { ...prev }; delete next[key]; return next; });
      await load();
      return true;
    } catch (e) { toast("Couldn't save " + key, "err"); return false; }
  }
  async function addSetting() {
    const key = newKey.trim();
    if (!key) return;
    if (await saveText(key, newValue)) { setNewKey(""); setNewValue(""); }
  }

  if (cfg === undefined) return <><Head /><div className="empty"><span className="spin" /> loading...</div></>;
  if (cfg.__err) return <><Head /><div className="card mut">Couldn't load: {cfg.__err}</div></>;

  const allRows = flatten(cfg).filter(([k]) => !k.includes("seen"));
  const fields: SchemaField[] = allRows.map(([fpath, value]) => ({
    path: fpath,
    default: meta.get(fpath)?.default,
    type: meta.get(fpath)?.type || (value === null ? "null" : typeof value),
    ...(meta.get(fpath) || {}),
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

  function FieldRow({ field }: { field: SchemaField }) {
    const value = getByPath(cfg, field.path);
    const enums = field.enum || FALLBACK_ENUMS[field.path];
    const leaf = labelFor(field);
    const description = field.description || `Default: ${toText(field.default) || "(empty)"}`;
    const changed = JSON.stringify(value) !== JSON.stringify(field.default);
    const head = (
      <div className="cfg-field-top">
        <span className="cfg-field-lbl">{leaf}</span>
        {changed && <span className="cfg-dot" title="changed from default" />}
        {field.restart && <Badge status="warn">{field.restart}</Badge>}
      </div>
    );

    if (typeof value === "boolean") {
      return (
        <div className="cfg-field rowy">
          <div className="cfg-left">{head}<div className="cfg-field-desc">{description}</div></div>
          <div className="cfg-field-ctl"><Toggle checked={value} onChange={(v) => saveValue(field.path, v)} /></div>
        </div>
      );
    }
    if (enums) {
      const options = enums.includes(String(value ?? "")) ? enums : [String(value ?? ""), ...enums];
      return (
        <div className="cfg-field">
          {head}<div className="cfg-field-desc">{description}</div>
          <div className="cfg-field-ctl">
            <select value={String(value ?? "")} onChange={(e) => saveValue(field.path, e.target.value)}>
              {options.map((o) => <option key={o} value={o}>{o || "(empty)"}</option>)}
            </select>
          </div>
        </div>
      );
    }
    if (value && typeof value === "object") {
      const editing = field.path in edit;
      return (
        <div className="cfg-field">
          {head}<div className="cfg-field-desc">{description}</div>
          <div className="cfg-field-ctl">
            {editing ? (<>
              <textarea rows={4} value={edit[field.path]} autoFocus onChange={(e) => setEdit({ ...edit, [field.path]: e.target.value })} />
              <Button sm icon="check" onClick={() => saveText(field.path, edit[field.path])}>Save</Button>
              <Button sm variant="ghost" onClick={() => setEdit((p) => { const n = { ...p }; delete n[field.path]; return n; })}>Cancel</Button>
            </>) : (
              <button className="field-val mono" onClick={() => setEdit({ ...edit, [field.path]: toText(value) })}>{toText(value).slice(0, 120) || "[]"}</button>
            )}
          </div>
        </div>
      );
    }
    const cur = String(value ?? "");
    const pending = field.path in edit ? edit[field.path] : cur;
    const commit = () => {
      if (pending !== cur) saveValue(field.path, parse(pending));
      else setEdit((p) => { const n = { ...p }; delete n[field.path]; return n; });
    };
    return (
      <div className="cfg-field">
        {head}<div className="cfg-field-desc">{description}</div>
        <div className="cfg-field-ctl">
          <input type={typeof value === "number" ? "number" : "text"} value={pending}
            onChange={(e) => setEdit({ ...edit, [field.path]: e.target.value })}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } }}
            onBlur={commit} />
          {pending !== cur && <Button sm icon="check" onClick={commit}>Save</Button>}
        </div>
      </div>
    );
  }

  function Section({ name }: { name: string }) {
    const key = sectionKey(groups[name]);
    return (
      <div className="panel">
        <div className="cfg-sec-head">
          <b>{name}</b>
          <span className="row-flex" style={{ gap: 10 }}>
            <span className="mut" style={{ fontSize: 11 }}>{groups[name].length} fields · saves instantly</span>
            <Button sm variant="ghost" onClick={() => resetSection(key, name)}>Reset</Button>
          </span>
        </div>
        <div className="cfg-fields">{groups[name].map((f) => <FieldRow field={f} key={f.path} />)}</div>
      </div>
    );
  }

  return (
    <>
      <Head count={filtered.length} />
      <div className="cfg-bar">
        <span className="cfg-path">{path || "~/.aegis/config.yaml"}</span>
        <span className="cfg-bar-actions">
          <Button sm variant="ghost" icon="bolt" onClick={backupConfig}>Backup</Button>
          <Button sm variant={yaml ? "primary" : "ghost"} icon="config" onClick={() => (yaml ? setYaml(false) : openYaml())}>{yaml ? "Form" : "YAML"}</Button>
          {yaml && <Button sm icon="check" onClick={saveYaml}>Save YAML</Button>}
        </span>
      </div>

      {yaml ? (
        <div className="panel" style={{ padding: 12 }}>
          <textarea value={raw} onChange={(e) => setRaw(e.target.value)} spellCheck={false}
            style={{ width: "100%", minHeight: "66vh", fontFamily: "var(--mono)", fontSize: 12, lineHeight: 1.5 }} />
          <div className="mut" style={{ marginTop: 8 }}>Edits the whole config.yaml. Validated and backed up before saving.</div>
        </div>
      ) : (
      <>
        <div className="panel" style={{ marginBottom: 12 }}>
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

        <div style={{ marginBottom: 12 }}>
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
        </div>

        {searching
          ? groupNames.map((g) => <Section name={g} key={g} />)
          : (
            <div className="config-layout">
              <aside className="config-rail">
                {groupNames.map((g) => (
                  <button key={g} className={"config-sec" + ((active && groupNames.includes(active) ? active : groupNames[0]) === g ? " active" : "")} onClick={() => setActive(g)}>
                    <Icon n={SEC_ICON[sectionKey(groups[g])] || "config"} />
                    <span className="nm">{g}</span>
                    <span className="ct">{groups[g].length}</span>
                  </button>
                ))}
              </aside>
              <div className="config-main">
                {groupNames.length ? <Section name={groupNames.includes(active) ? active : groupNames[0]} /> : <div className="panel"><div className="empty small">No settings.</div></div>}
                <div className="panel">
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
