import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Icon } from "../lib/icons";
import { Button, Card, Empty, Loading, PageHeader } from "../lib/ui";

const fmtSize = (n: any) => {
  const b = Number(n);
  if (!b && b !== 0) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
};

export function FilesPage() {
  const [data, setData] = useState<any>(undefined);
  const [path, setPath] = useState("");
  const [view, setView] = useState<any>(null); // {path, content} | {path, error}

  async function load(p?: string) {
    setData(undefined);
    try { const d = await api(`files${p ? `?path=${encodeURIComponent(p)}` : ""}`); setData(d); setPath(d.path || ""); }
    catch (e) { setData({ error: String(e), entries: [] }); }
  }
  useEffect(() => { load(); }, []);

  async function open(name: string) { await load((path.endsWith("/") ? path : path + "/") + name); setView(null); }
  async function readFile(name: string) {
    const fp = (path.endsWith("/") ? path : path + "/") + name;
    setView({ path: fp, loading: true });
    try { setView(await api(`files/read?path=${encodeURIComponent(fp)}`)); }
    catch (e) { setView({ path: fp, error: String(e) }); }
  }

  const entries: any[] = data?.entries || [];
  return (
    <>
      <PageHeader title="Files" sub="read-only browser · token-gated"
        actions={<Button variant="ghost" icon="refresh" onClick={() => load(path)}>Refresh</Button>} />
      <div className="toolbar">
        <input className="search mono" value={path} onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load(path)} placeholder="/home/you/…" />
        <Button onClick={() => load(path)}>Go</Button>
        {data?.parent && <Button variant="ghost" onClick={() => load(data.parent)}>↑ Up</Button>}
      </div>
      <div className="agents-layout">
        <Card pad={false}>
          {data === undefined ? <Loading />
            : data.error ? <Empty>{data.error}</Empty>
            : !entries.length ? <Empty small>empty directory</Empty>
            : <div className="tablewrap">
                <table className="tbl">
                  <thead><tr><th>Name</th><th className="right">Size</th><th>Modified</th></tr></thead>
                  <tbody>
                    {entries.map((e) => (
                      <tr key={e.name} className="click" onClick={() => e.is_dir ? open(e.name) : readFile(e.name)}>
                        <td className="cellprimary"><span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                          <span style={{ width: 15, height: 15, color: e.is_dir ? "var(--accent)" : "var(--mut)" }}>
                            <Icon n={e.is_dir ? "sessions" : "logs"} /></span>
                          {e.name}{e.is_dir ? "/" : ""}
                        </span></td>
                        <td className="right mono">{e.is_dir ? "" : fmtSize(e.size)}</td>
                        <td className="mono">{String(e.modified || "").replace("T", " ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>}
        </Card>
        {view && (
          <Card title={<span className="mono" style={{ fontSize: 12 }}>{(view.path || "").split("/").pop()}</span>}
            actions={<Button variant="ghost" sm onClick={() => setView(null)}>Close</Button>} pad={false}>
            {view.loading ? <Loading />
              : view.error ? <Empty small>{view.error}</Empty>
              : <pre style={{ margin: 0, padding: 12, maxHeight: "70vh", overflow: "auto", fontFamily: "var(--mono)", fontSize: 11.5, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{view.content}</pre>}
          </Card>
        )}
      </div>
    </>
  );
}
