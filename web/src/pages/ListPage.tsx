import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Icon } from "../lib/icons";
import { compact, countLabel, dateish, titleOf } from "../lib/format";

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
    // memory endpoint: {memory: "text", user: "text"} -> split into entries
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

function valueFor(row: any, key: string) {
  return key.split(".").reduce((acc, part) => acc?.[part], row);
}

function prettyJson(value: any): string {
  return JSON.stringify(value, null, 2);
}

function DetailDrawer({
  row,
  detail,
  loading,
  error,
  source,
  onClose,
}: {
  row: any;
  detail: any;
  loading: boolean;
  error: string;
  source: string;
  onClose: () => void;
}) {
  const payload = detail && !detail.__err ? detail : row;
  const messages = payload?.messages || payload?.session?.messages || [];
  const runs = payload?.runs || [];
  const traces = payload?.traces || [];
  const metrics = payload?.metrics || payload?.summary || {};
  return (
    <div className="drawer" role="dialog" aria-modal="true">
      <div className="drawer-backdrop" onClick={onClose} />
      <section className="drawer-panel">
        <div className="drawer-head">
          <div>
            <h2>{titleOf(row)}</h2>
            <div className="crumb">{row.id || row.trace_id || row.run_id || row.name || source}</div>
          </div>
          <button className="iconbtn" aria-label="Close detail" onClick={onClose}><Icon n="close" /></button>
        </div>
        {loading && <div className="empty"><span className="spin" /> loading detail...</div>}
        {error && <div className="banner err">{error}</div>}
        {!loading && !error && (
          <div className="drawer-body">
            {!!Object.keys(metrics || {}).length && (
              <div className="kvgrid">
                {Object.entries(metrics).slice(0, 8).map(([k, v]) => (
                  <div className="kv" key={k}>
                    <span>{k.replaceAll("_", " ")}</span>
                    <b>{compact(v, 48)}</b>
                  </div>
                ))}
              </div>
            )}
            {!!messages.length && (
              <section className="detail-section">
                <h3>Messages</h3>
                <div className="detail-list">
                  {messages.slice(-12).map((m: any, i: number) => (
                    <div className="message-row" key={i}>
                      <span className="pill">{m.role || "message"}</span>
                      <p>{m.content || m.text || ""}</p>
                    </div>
                  ))}
                </div>
              </section>
            )}
            {!!runs.length && (
              <section className="detail-section">
                <h3>Runs</h3>
                {runs.slice(0, 8).map((r: any) => (
                  <div className="minirow" key={r.id}>{compact(r.title || r.id, 80)}<span>{r.status || r.surface || ""}</span></div>
                ))}
              </section>
            )}
            {!!traces.length && (
              <section className="detail-section">
                <h3>Traces</h3>
                {traces.slice(0, 8).map((t: any) => (
                  <div className="minirow" key={t.id || t.trace_id}>{compact(t.id || t.trace_id, 72)}<span>{t.status || t.source || ""}</span></div>
                ))}
              </section>
            )}
            <section className="detail-section">
              <h3>Raw</h3>
              <pre className="json">{prettyJson(payload)}</pre>
            </section>
          </div>
        )}
      </section>
    </div>
  );
}

export function ListPage({
  endpoint,
  title,
  cols,
  arrayKey,
  raw,
  empty,
  detailEndpoint,
  detailParam = "id",
  idKey = "id",
}: Props) {
  const [data, setData] = useState<any>(undefined);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  async function load() {
    setErr("");
    setData(undefined);
    try { setData(await api(endpoint)); }
    catch (e) { setErr(String(e)); setData(null); }
  }

  useEffect(() => { load(); setSelected(null); }, [endpoint]);

  async function open(row: any) {
    setSelected(row);
    setDetail(null);
    setDetailError("");
    const id = valueFor(row, idKey) || row.id || row.trace_id || row.run_id || row.name;
    if (!detailEndpoint || !id) return;
    setDetailLoading(true);
    try {
      setDetail(await api(`${detailEndpoint}?${detailParam}=${encodeURIComponent(String(id))}`));
    } catch (e) {
      setDetailError(String(e));
    } finally {
      setDetailLoading(false);
    }
  }

  if (data === undefined) return <><Head title={title} /><div className="empty"><span className="spin" /> loading...</div></>;
  if (err) return <><Head title={title} /><div className="banner err">Couldn't load: {err}</div></>;
  let rows = rowsOf(data, arrayKey);
  if (q) rows = rows.filter((r) => JSON.stringify(r).toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <Head title={title} count={rows.length} onRefresh={load} />
      <div className="panel">
        <div className="toolbar">
          <input placeholder={`Filter ${title.toLowerCase()}...`} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {!rows.length && <div className="empty">{empty || "Nothing here yet."}</div>}
        {raw
          ? rows.map((r, i) => <div className="row logrow" key={i}><span>{r.text ?? r.line ?? JSON.stringify(r)}</span></div>)
          : rows.map((r, i) => (
            <button className="row datarow" key={i} onClick={() => open(r)}>
              {cols.map(([key, label], j) => (
                <span key={key} className={j === 0 ? "primary" : ""}>
                  {j === 0 ? "" : label + ": "}{formatCell(valueFor(r, key))}
                </span>
              ))}
            </button>
          ))}
      </div>
      {selected && (
        <DetailDrawer
          row={selected}
          detail={detail}
          loading={detailLoading}
          error={detailError}
          source={endpoint}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}

function formatCell(value: any): string {
  if (String(value || "").match(/^\d{4}-\d{2}-\d{2}T/)) return dateish(value);
  return compact(value);
}

function Head({ title, count, onRefresh }: { title: string; count?: number; onRefresh?: () => void }) {
  return (
    <div className="head">
      <div>
        <h1>{title}</h1>
        {count != null && <span className="crumb">{countLabel(count, "item")}</span>}
      </div>
      {onRefresh && <button className="btn ghost" onClick={onRefresh}>Refresh</button>}
    </div>
  );
}
