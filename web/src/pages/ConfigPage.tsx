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

export function ConfigPage() {
  const [cfg, setCfg] = useState<any>(undefined);
  const [q, setQ] = useState("");
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");
  async function load() { try { setCfg(await api("config")); } catch (e) { setCfg({ __err: String(e) }); } }
  useEffect(() => { load(); }, []);
  if (cfg === undefined) return <><Head /><div className="empty"><span className="spin" /> loading…</div></>;
  if (cfg.__err) return <><Head /><div className="card mut">Couldn't load: {cfg.__err}</div></>;

  let rows = flatten(cfg).filter(([k]) => !k.includes("seen"));
  if (q) rows = rows.filter(([k]) => k.toLowerCase().includes(q.toLowerCase()));

  async function save(key: string) {
    const raw = edit[key];
    let value: any = raw;
    if (raw === "true") value = true; else if (raw === "false") value = false;
    else if (raw !== "" && !isNaN(Number(raw)) && /^[0-9.]+$/.test(raw)) value = Number(raw);
    try { await post("config", { key, value }); setMsg(`✓ ${key} saved`); setEdit((e) => { const c = { ...e }; delete c[key]; return c; }); await load(); }
    catch (e) { setMsg("✗ " + String(e)); }
  }

  return (
    <>
      <Head count={rows.length} />
      <div className="card">
        <input placeholder="Filter settings…" value={q} onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 12 }} />
        {msg && <div className="mut" style={{ marginBottom: 8 }}>{msg}</div>}
        {rows.map(([key, val]) => {
          const editing = key in edit;
          const display = Array.isArray(val) ? JSON.stringify(val) : String(val);
          return (
            <div className="row" key={key}>
              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: 12.5 }}>{key}</span>
              <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                {editing
                  ? <>
                      <input value={edit[key]} onChange={(e) => setEdit({ ...edit, [key]: e.target.value })} style={{ width: 220 }} autoFocus />
                      <button className="btn" onClick={() => save(key)}>Save</button>
                      <button className="btn ghost" onClick={() => setEdit((e) => { const c = { ...e }; delete c[key]; return c; })}>×</button>
                    </>
                  : <>
                      <span className="mut" style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{display || "—"}</span>
                      {!Array.isArray(val) && typeof val !== "object" && <button className="btn ghost" onClick={() => setEdit({ ...edit, [key]: display })}>edit</button>}
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
  return <div className="head"><h1>Config</h1><span className="crumb">{count != null ? `${count} settings · ` : ""}secrets redacted · edits persist to config.yaml</span></div>;
}
