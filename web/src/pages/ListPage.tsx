import { useEffect, useState } from "react";
import { api } from "../lib/api";

type Col = [string, string];
type Props = {
  endpoint: string; title: string; cols: Col[];
  arrayKey?: string; raw?: boolean; empty?: string;
};

// Pull the row array out of whatever shape the endpoint returns.
function rowsOf(data: any, arrayKey?: string): any[] {
  if (Array.isArray(data)) return data;
  if (arrayKey && Array.isArray(data?.[arrayKey])) return data[arrayKey];
  if (arrayKey && data?.[arrayKey] && typeof data[arrayKey] === "object") {
    return flattenMap(data[arrayKey], arrayKey);
  }
  for (const k of ["items", "rows", "entries", "list", arrayKey || ""]) {
    if (k && Array.isArray(data?.[k])) return data[k];
  }
  // Column/map endpoints: {ready: [...], done: [...]} or {pending: {telegram: {...}}}
  if (data && typeof data === "object") {
    const vals = Object.values(data).filter((v) => Array.isArray(v));
    if (vals.length) return Object.entries(data).flatMap(([k, v]) =>
      Array.isArray(v) ? v.map((row) => toRow(row, { status: k })) : [],
    );
    const maps = Object.values(data).filter((v) => v && typeof v === "object");
    if (maps.length) return flattenMap(data, "group");
    // memory endpoint: {memory: "text", user: "text"} → split into entries
    const strs = Object.entries(data).filter(([, v]) => typeof v === "string" && (v as string).trim());
    if (strs.length) return strs.map(([k, v]) => ({ text: `${k}: ${v}` }));
  }
  return [];
}

function toRow(value: any, extra: Record<string, any>): any {
  return value && typeof value === "object" && !Array.isArray(value)
    ? { ...extra, ...value }
    : { ...extra, value: value, text: String(value) };
}

function flattenMap(obj: Record<string, any>, arrayKey: string): any[] {
  const rows: any[] = [];
  const outerName = arrayKey === "pending" || arrayKey === "approved" ? "platform" : "group";
  for (const [outer, value] of Object.entries(obj || {})) {
    if (Array.isArray(value)) {
      rows.push(...value.map((item) => toRow(item, { [outerName]: outer })));
    } else if (value && typeof value === "object") {
      for (const [inner, item] of Object.entries(value)) {
        rows.push(toRow(item, { [outerName]: outer, code: inner }));
      }
    } else {
      rows.push({ [outerName]: outer, value, text: String(value) });
    }
  }
  return rows;
}

export function ListPage({ endpoint, title, cols, arrayKey, raw, empty }: Props) {
  const [data, setData] = useState<any>(undefined);
  const [q, setQ] = useState("");
  useEffect(() => { setData(undefined); api(endpoint).then(setData).catch((e) => setData({ __err: String(e) })); }, [endpoint]);
  if (data === undefined) return <><Head title={title} /><div className="empty"><span className="spin" /> loading…</div></>;
  if (data?.__err) return <><Head title={title} /><div className="card mut">Couldn't load: {data.__err}</div></>;
  let rows = rowsOf(data, arrayKey);
  if (q) rows = rows.filter((r) => JSON.stringify(r).toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <Head title={title} count={rows.length} />
      <div className="card">
        <input placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 12 }} />
        {!rows.length && <div className="empty">{empty || "Nothing here yet."}</div>}
        {raw
          ? rows.map((r, i) => <div className="row" key={i}><span style={{ whiteSpace: "pre-wrap" }}>{r.text ?? r.line ?? JSON.stringify(r)}</span></div>)
          : rows.map((r, i) => (
            <div className="row" key={i}>
              {cols.map(([key, label], j) => (
                <span key={key} style={j === 0 ? { fontWeight: 600 } : { color: "var(--mut)" }}>
                  {j === 0 ? "" : label + ": "}{String(r[key] ?? "—").slice(0, 90)}
                </span>
              ))}
            </div>
          ))}
      </div>
    </>
  );
}

function Head({ title, count }: { title: string; count?: number }) {
  return <div className="head"><h1>{title}</h1>{count != null && <span className="crumb">{count} item{count === 1 ? "" : "s"}</span>}</div>;
}
