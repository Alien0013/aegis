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
  for (const k of ["items", "rows", "entries", "list", arrayKey || ""]) {
    if (k && Array.isArray(data?.[k])) return data[k];
  }
  // memory endpoint: {memory: "text", user: "text"} → split into entries
  if (data && typeof data === "object") {
    const vals = Object.values(data).filter((v) => Array.isArray(v));
    if (vals.length) return vals[0] as any[];
    const strs = Object.entries(data).filter(([, v]) => typeof v === "string" && (v as string).trim());
    if (strs.length) return strs.map(([k, v]) => ({ text: `${k}: ${v}` }));
  }
  return [];
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
