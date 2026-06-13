import { useEffect, useState } from "react";
import { api, post } from "../lib/api";

// flatten nested config into dotted keys for inline editing
function flatten(obj: any, prefix = ""): [string, any][] {
  const out: [string, any][] = [];
  for (const [k, v] of Object.entries(obj || {})) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) out.push(...flatten(v, key));
    else out.push([key, v]);
  }
  return out;
}

const QUICK_SELECTS = [
  { key: "tools.exec_mode", label: "Permissions", options: ["auto", "ask", "smart", "allowlist", "deny", "full"] },
  { key: "display.reasoning", label: "Reasoning view", options: ["summary", "live", "off"] },
  { key: "agent.reasoning_effort", label: "Reasoning effort", options: ["off", "minimal", "low", "medium", "high", "xhigh"] },
  { key: "gateway.busy_mode", label: "Busy mode", options: ["queue", "steer", "interrupt"] },
];

const QUICK_TOGGLES = [
  { key: "learn.background", label: "Background learning" },
  { key: "learn.auto_apply", label: "Apply memories" },
  { key: "learn.auto_apply_skills", label: "Apply skills" },
  { key: "memory.enabled", label: "Memory" },
];

// Known enums so a setting renders as a dropdown instead of free text.
const ENUMS: Record<string, string[]> = Object.fromEntries(QUICK_SELECTS.map((s) => [s.key, s.options]));

// Friendly section titles for the top-level config groups.
const GROUP_TITLES: Record<string, string> = {
  agent: "Agent", tools: "Tools & permissions", model: "Model", memory: "Memory",
  learn: "Learning", display: "Display", gateway: "Gateway", web: "Web access",
  server: "Dashboard & server", mcp: "MCP", cron: "Scheduled tasks", goals: "Goals",
  onboarding: "Onboarding", lsp: "Language server",
};
// title-case any group we don't have an explicit label for (custom_providers → Custom providers)
const groupTitle = (g: string) =>
  GROUP_TITLES[g] || g.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export function ConfigPage() {
  const [cfg, setCfg] = useState<any>(undefined);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [msg, setMsg] = useState("");
  async function load() { try { setCfg(await api("config")); } catch (e) { setCfg({ __err: String(e) }); } }
  useEffect(() => { load(); }, []);
  if (cfg === undefined) return <><Head /><div className="empty"><span className="spin" /> loading...</div></>;
  if (cfg.__err) return <><Head /><div className="card mut">Couldn't load: {cfg.__err}</div></>;

  let rows = flatten(cfg).filter(([k]) => !k.includes("seen"));
  if (q) rows = rows.filter(([k]) => k.toLowerCase().includes(q.toLowerCase()));

  // group by top-level segment
  const groups: Record<string, [string, any][]> = {};
  for (const r of rows) { const g = r[0].split(".")[0]; (groups[g] ||= []).push(r); }
  const groupNames = Object.keys(groups).sort();
  const searching = q.trim().length > 0;

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

  async function quickSave(key: string, value: any) {
    try { await post("config", { key, value }); setMsg(`${key} saved`); await load(); }
    catch (e) { setMsg("Error: " + String(e)); }
  }
  async function saveText(key: string, raw: string): Promise<boolean> {
    let value: any;
    try { value = parse(raw); } catch (e) { setMsg("Invalid JSON: " + String(e)); return false; }
    try { await post("config", { key, value }); setMsg(`${key} saved`); setEdit((e) => { const c = { ...e }; delete c[key]; return c; }); await load(); return true; }
    catch (e) { setMsg("Error: " + String(e)); return false; }
  }
  async function addSetting() {
    const key = newKey.trim();
    if (!key) return;
    if (await saveText(key, newValue)) { setNewKey(""); setNewValue(""); }
  }

  // one typed control per setting — no syntax for the user to remember
  function Field({ k, v }: { k: string; v: any }) {
    const leaf = k.split(".").slice(1).join(".") || k;
    if (typeof v === "boolean")
      return <label className="field"><span>{leaf}</span>
        <input type="checkbox" checked={v} onChange={(e) => quickSave(k, e.target.checked)} /></label>;
    if (ENUMS[k])
      return <label className="field"><span>{leaf}</span>
        <select value={String(v ?? "")} onChange={(e) => quickSave(k, e.target.value)}>
          {ENUMS[k].map((o) => <option key={o} value={o}>{o}</option>)}
        </select></label>;
    const isObj = v && typeof v === "object";
    if (isObj) {
      const editing = k in edit;
      return <label className="field wide"><span>{leaf}</span>
        {editing
          ? <span className="field-edit">
              <textarea rows={4} value={edit[k]} autoFocus onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} />
              <button className="btn sm" onClick={() => saveText(k, edit[k])}>Save</button>
              <button className="btn ghost sm" onClick={() => setEdit((e) => { const c = { ...e }; delete c[k]; return c; })}>x</button>
            </span>
          : <button className="field-val mono" onClick={() => setEdit({ ...edit, [k]: toText(v) })}>{toText(v).slice(0, 70) || "[]"}</button>}
      </label>;
    }
    // number / string — save on blur or Enter, only if changed
    const cur = String(v ?? "");
    const pending = k in edit ? edit[k] : cur;
    const commit = () => { if (pending !== cur) quickSave(k, parse(pending)); else setEdit((e) => { const c = { ...e }; delete c[k]; return c; }); };
    return <label className="field"><span>{leaf}</span>
      <input type={typeof v === "number" ? "number" : "text"} value={pending}
        onChange={(e) => setEdit({ ...edit, [k]: e.target.value })}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLInputElement).blur(); } }} />
    </label>;
  }

  return (
    <>
      <Head count={rows.length} />
      <div className="panel" style={{ marginBottom: 14 }}>
        <h3>Quick settings</h3>
        <div className="quick-grid">
          {QUICK_SELECTS.map((item) => (
            <label key={item.key}>{item.label}
              <select value={String(cfg[item.key] ?? "")} onChange={(e) => quickSave(item.key, e.target.value)}>
                {item.options.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </label>
          ))}
        </div>
        <div className="toggle-grid">
          {QUICK_TOGGLES.map((item) => (
            <label className="toggle-row" key={item.key}>
              <span>{item.label}</span>
              <input type="checkbox" checked={Boolean(cfg[item.key])} onChange={(e) => quickSave(item.key, e.target.checked)} />
            </label>
          ))}
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 14 }}>
        <input placeholder="Search all settings..." value={q} onChange={(e) => setQ(e.target.value)} />
        {msg && <div className="mut" style={{ marginTop: 8 }}>{msg}</div>}
      </div>

      {groupNames.map((g) => {
        const isOpen = searching || open[g];
        return (
          <div className="panel group" key={g}>
            <div className="group-h" onClick={() => setOpen((o) => ({ ...o, [g]: !o[g] }))}>
              <b>{groupTitle(g)}</b>
              <span className="mut">{groups[g].length} · {isOpen ? "▾" : "▸"}</span>
            </div>
            {isOpen && <div className="fields">{groups[g].map(([k, v]) => <Field k={k} v={v} key={k} />)}</div>}
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
        <div className="mut" style={{ marginTop: 8 }}>Most settings are above — this is for advanced/new keys. Secrets belong in API Keys.</div>
      </div>
    </>
  );
}

function Head({ count }: { count?: number }) {
  return <div className="head"><h1>Settings</h1><span className="crumb">{count != null ? `${count} settings · ` : ""}changes save instantly to config.yaml · secrets redacted</span></div>;
}
