import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Icon } from "../lib/icons";
import { compact, dateish, titleOf } from "../lib/format";
import { Badge, Button, Empty, Loading, PageHeader, Toolbar } from "../lib/ui";

type Col = [string, string];
type Props = {
  endpoint: string; title: string; cols: Col[];
  arrayKey?: string; raw?: boolean; empty?: string;
  detailEndpoint?: string; detailParam?: string; idKey?: string;
};

// Pull the row array out of whatever shape the endpoint returns.
function rowsOf(data: any, arrayKey?: string): any[] {
  if (Array.isArray(data)) return data;
  if (arrayKey && Array.isArray(data?.[arrayKey])) return data[arrayKey];
  if (arrayKey && data?.[arrayKey] && typeof data[arrayKey] === "object") return flattenMap(data[arrayKey], arrayKey);
  for (const k of ["items", "rows", "entries", "list", arrayKey || ""]) {
    if (k && Array.isArray(data?.[k])) return data[k];
  }
  if (data && typeof data === "object") {
    const vals = Object.values(data).filter((v) => Array.isArray(v));
    if (vals.length) return Object.entries(data).flatMap(([k, v]) =>
      Array.isArray(v) ? v.map((row) => toRow(row, { status: k })) : []);
    const maps = Object.values(data).filter((v) => v && typeof v === "object");
    if (maps.length) return flattenMap(data, "group");
    const strs = Object.entries(data).filter(([, v]) => typeof v === "string" && (v as string).trim());
    if (strs.length) return strs.map(([k, v]) => ({ text: `${k}: ${v}` }));
  }
  return [];
}

function toRow(value: any, extra: Record<string, any>): any {
  return value && typeof value === "object" && !Array.isArray(value)
    ? { ...extra, ...value } : { ...extra, value, text: String(value) };
}

function flattenMap(obj: Record<string, any>, arrayKey: string): any[] {
  const rows: any[] = [];
  const outerName = arrayKey === "pending" || arrayKey === "approved" ? "platform" : "group";
  for (const [outer, value] of Object.entries(obj || {})) {
    if (Array.isArray(value)) rows.push(...value.map((item) => toRow(item, { [outerName]: outer })));
    else if (value && typeof value === "object")
      for (const [inner, item] of Object.entries(value)) rows.push(toRow(item, { [outerName]: outer, code: inner }));
    else rows.push({ [outerName]: outer, value, text: String(value) });
  }
  return rows;
}

const valueFor = (row: any, key: string) => key.split(".").reduce((acc, part) => acc?.[part], row);
const isStatusCol = (key: string, label: string) => /status|state/i.test(key) || /^status$/i.test(label);

function cell(value: any): string {
  const s = String(value ?? "");
  if (/^\d{4}-\d{2}-\d{2}T/.test(s)) return dateish(value);
  return compact(value);
}

function Drawer({ row, detail, loading, error, onClose }:
  { row: any; detail: any; loading: boolean; error: string; onClose: () => void }) {
  const payload = detail && !detail.__err ? detail : row;
  const messages = payload?.messages || payload?.session?.messages || [];
  const metrics = payload?.metrics || payload?.summary || {};
  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-h">
          <div><b>{titleOf(row)}</b><div className="crumb mono">{row.id || row.trace_id || row.run_id || row.name || ""}</div></div>
          <button className="iconbtn" aria-label="Close" onClick={onClose}><Icon n="close" /></button>
        </div>
        <div className="drawer-b">
          {loading && <Loading />}
          {error && <div className="badge err" style={{ height: "auto", padding: 8 }}>{error}</div>}
          {!loading && !error && <>
            {!!Object.keys(metrics).length && (
              <div className="kvgrid" style={{ marginBottom: 14 }}>
                {Object.entries(metrics).slice(0, 10).map(([k, v]) => (
                  <div className="kv" key={k}><span>{k.replaceAll("_", " ")}</span><b>{compact(v, 40)}</b></div>
                ))}
              </div>
            )}
            {!!messages.length && (
              <div style={{ marginBottom: 14 }}>
                <h3 style={{ fontSize: 12, color: "var(--faint)", textTransform: "uppercase", letterSpacing: ".05em" }}>Messages</h3>
                {messages.slice(-14).map((m: any, i: number) => (
                  <div key={i} style={{ display: "flex", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                    <span className="pill">{m.role || "msg"}</span>
                    <span style={{ minWidth: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{compact(m.content || m.text || "", 400)}</span>
                  </div>
                ))}
              </div>
            )}
            <h3 style={{ fontSize: 12, color: "var(--faint)", textTransform: "uppercase", letterSpacing: ".05em" }}>Raw</h3>
            <pre>{JSON.stringify(payload, null, 2)}</pre>
          </>}
        </div>
      </aside>
    </>
  );
}

export function ListPage({ endpoint, title, cols, arrayKey, raw, empty, detailEndpoint, detailParam = "id", idKey = "id" }: Props) {
  const [data, setData] = useState<any>(undefined);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [dLoading, setDLoading] = useState(false);
  const [dError, setDError] = useState("");

  async function load() {
    setErr(""); setData(undefined);
    try { setData(await api(endpoint)); } catch (e) { setErr(String(e)); setData(null); }
  }
  useEffect(() => { load(); setSelected(null); }, [endpoint]);

  async function open(row: any) {
    setSelected(row); setDetail(null); setDError("");
    const id = valueFor(row, idKey) || row.id || row.trace_id || row.run_id || row.name;
    if (!detailEndpoint || !id) return;
    setDLoading(true);
    try { setDetail(await api(`${detailEndpoint}?${detailParam}=${encodeURIComponent(String(id))}`)); }
    catch (e) { setDError(String(e)); } finally { setDLoading(false); }
  }

  const header = (
    <PageHeader title={title}
      sub={data && !err ? `${rowsOf(data, arrayKey).length} item${rowsOf(data, arrayKey).length === 1 ? "" : "s"}` : undefined}
      actions={<Button variant="ghost" icon="refresh" onClick={load}>Refresh</Button>} />
  );
  if (data === undefined) return <>{header}<Loading /></>;
  if (err) return <>{header}<div className="card"><Empty>Couldn't load: {err}</Empty></div></>;

  let rows = rowsOf(data, arrayKey);
  if (q) rows = rows.filter((r) => JSON.stringify(r).toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      {header}
      <Toolbar q={q} setQ={setQ} placeholder={`Filter ${title.toLowerCase()}…`} />
      <div className="card pad0">
        {!rows.length && <Empty>{empty || "Nothing here yet."}</Empty>}
        {raw
          ? <div style={{ padding: 12, maxHeight: "70vh", overflow: "auto" }}>
              {rows.map((r, i) => <div className="mono" key={i} style={{ fontSize: 11.5, color: "var(--mut)", padding: "2px 0", whiteSpace: "pre-wrap" }}>{r.text ?? r.line ?? JSON.stringify(r)}</div>)}
            </div>
          : !!rows.length && (
            <div className="tablewrap">
              <table className="tbl">
                <thead><tr>{cols.map(([key, label]) => <th key={key}>{label}</th>)}</tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className={detailEndpoint || true ? "click" : ""} onClick={() => open(r)}>
                      {cols.map(([key, label], j) => {
                        const v = valueFor(r, key);
                        return <td key={key} className={j === 0 ? "cellprimary" : ""}>
                          {isStatusCol(key, label) && v ? <Badge status={String(v)} /> : (j === 0 ? cell(v) : <span className="mono">{cell(v)}</span>)}
                        </td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
      {selected && <Drawer row={selected} detail={detail} loading={dLoading} error={dError} onClose={() => setSelected(null)} />}
    </>
  );
}
