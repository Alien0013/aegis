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

export function ConfigPage() {
  const [cfg, setCfg] = useState<any>(undefined);
  const [q, setQ] = useState("");
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
    if ((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]"))) {
      return JSON.parse(text);
    }
    return raw;
  }

  const valueOf = (key: string) => cfg[key];

  async function save(key: string, raw = edit[key]): Promise<boolean> {
    let value: any;
    try { value = parse(raw); }
    catch (e) { setMsg("Invalid JSON: " + String(e)); return false; }
    try { await post("config", { key, value }); setMsg(`${key} saved`); setEdit((e) => { const c = { ...e }; delete c[key]; return c; }); await load(); return true; }
    catch (e) { setMsg("Error: " + String(e)); return false; }
  }

  async function quickSave(key: string, value: any) {
    try {
      await post("config", { key, value });
      setMsg(`${key} saved`);
      await load();
    } catch (e) {
      setMsg("Error: " + String(e));
    }
  }

  async function addSetting() {
    const key = newKey.trim();
    if (!key) return;
    if (await save(key, newValue)) {
      setNewKey("");
      setNewValue("");
    }
  }

  return (
    <>
      <Head count={rows.length} />
      <div className="panel" style={{ marginBottom: 14 }}>
        <h3>Quick settings</h3>
        <div className="quick-grid">
          {QUICK_SELECTS.map((item) => (
            <label key={item.key}>{item.label}
              <select
                value={String(valueOf(item.key) ?? "")}
                onChange={(e) => quickSave(item.key, e.target.value)}
              >
                {item.options.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            </label>
          ))}
        </div>
        <div className="toggle-grid">
          {QUICK_TOGGLES.map((item) => (
            <label className="toggle-row" key={item.key}>
              <span>{item.label}</span>
              <input
                type="checkbox"
                checked={Boolean(valueOf(item.key))}
                onChange={(e) => quickSave(item.key, e.target.checked)}
              />
            </label>
          ))}
        </div>
      </div>
      <div className="panel" style={{ marginBottom: 14 }}>
        <h3>Add or replace a setting</h3>
        <div className="grid c3" style={{ gap: 10, alignItems: "end" }}>
          <label>Key<input value={newKey} onChange={(e) => setNewKey(e.target.value)} placeholder="tools.exec_mode" /></label>
          <label style={{ gridColumn: "span 1" }}>Value<input value={newValue} onChange={(e) => setNewValue(e.target.value)} placeholder='true, 3, "text", ["core","mcp"]' /></label>
          <button className="btn" onClick={addSetting}>Save setting</button>
        </div>
        <div className="mut" style={{ marginTop: 8 }}>Objects and arrays can be entered as JSON. Secrets belong in API Keys.</div>
      </div>
      <div className="panel">
        <input placeholder="Filter settings..." value={q} onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 12 }} />
        {msg && <div className="mut" style={{ marginBottom: 8 }}>{msg}</div>}
        {rows.map(([key, val]) => {
          const editing = key in edit;
          const display = toText(val);
          const redacted = display.includes(String.fromCharCode(8226).repeat(6)) || display.includes("******");
          const multiline = display.length > 80 || display.includes("\n") || display.startsWith("{") || display.startsWith("[");
          return (
            <div className="row" key={key}>
              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: 12.5 }}>{key}</span>
              <span style={{ display: "flex", gap: 6, alignItems: "center", maxWidth: "min(680px, 100%)" }}>
                {editing
                  ? <>
                      {multiline
                        ? <textarea value={edit[key]} rows={4} onChange={(e) => setEdit({ ...edit, [key]: e.target.value })} style={{ width: 360 }} autoFocus />
                        : <input value={edit[key]} onChange={(e) => setEdit({ ...edit, [key]: e.target.value })} style={{ width: 260 }} autoFocus />}
                      <button className="btn" onClick={() => save(key)}>Save</button>
                      <button className="btn ghost" onClick={() => setEdit((e) => { const c = { ...e }; delete c[key]; return c; })}>Cancel</button>
                    </>
                  : <>
                      <span className="mut" style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{display || "-"}</span>
                      {redacted
                        ? <span className="pill">API Keys</span>
                        : <button className="btn ghost" onClick={() => setEdit({ ...edit, [key]: display })}>edit</button>}
                    </>}
              </span>
            </div>
          );
        })}
      </div>
    </>
  );
}
function Head({ count }: { count?: number }) {
  return <div className="head"><h1>Config</h1><span className="crumb">{count != null ? `${count} settings - ` : ""}secrets redacted - edits persist to config.yaml</span></div>;
}
