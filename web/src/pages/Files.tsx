import { useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/useApi";
import { bytes, compact } from "../lib/format";
import { Card, Empty, Loading, PageHeader } from "../components/ui";
import { Icon } from "../components/icons";

interface Entry { name: string; is_dir: boolean; size: number | null; modified: string }
interface Listing { path: string; parent?: string; entries: Entry[]; error?: string }

export function Files() {
  const [path, setPath] = useState("");
  const { data, loading, error } = useApi<Listing>(`files${path ? `?path=${encodeURIComponent(path)}` : ""}`);
  const [file, setFile] = useState<{ path: string; content?: string; error?: string } | null>(null);

  async function openFile(p: string) {
    setFile({ path: p });
    try { setFile(await api(`files/read?path=${encodeURIComponent(p)}`)); }
    catch (e) { setFile({ path: p, error: String(e) }); }
  }

  return (
    <>
      <PageHeader title="Files" sub={data?.path || "Workspace browser (read-only)"} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="grid gap-[var(--gap)] lg:grid-cols-2">
          <Card title={compact(data.path, 50)} pad={false}
            actions={data.parent ? <button onClick={() => setPath(data.parent!)} className="flex items-center gap-1 text-xs text-dim hover:text-text"><Icon name="chevronRight" size={12} className="rotate-180" /> up</button> : undefined}>
            {data.error && <Empty icon="alert">{data.error}</Empty>}
            <div className="scroll-thin max-h-[72vh] overflow-auto">
              {(data.entries || []).map((e) => (
                <button key={e.name}
                  onClick={() => e.is_dir ? setPath(`${data.path}/${e.name}`) : openFile(`${data.path}/${e.name}`)}
                  className="flex w-full items-center gap-2.5 border-b border-border px-[var(--pad)] py-1.5 text-left last:border-0 hover:bg-surface-2">
                  <Icon name={e.is_dir ? "files" : "logs"} size={14} className={e.is_dir ? "text-primary" : "text-faint"} />
                  <span className="min-w-0 flex-1 truncate text-sm text-text">{e.name}</span>
                  <span className="shrink-0 text-[11px] text-faint">{e.is_dir ? "" : bytes(e.size)}</span>
                </button>
              ))}
            </div>
          </Card>
          <Card title={file ? compact(file.path, 50) : "Preview"} pad={false}>
            {!file && <Empty icon="files">Select a file to preview.</Empty>}
            {file && !file.content && !file.error && <Loading />}
            {file?.error && <Empty icon="alert">{file.error}</Empty>}
            {file?.content && (
              <pre className="scroll-thin max-h-[72vh] overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-xs text-dim">{file.content}</pre>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
