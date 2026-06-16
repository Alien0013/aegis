import { useRef, useState } from "react";
import { api, post, TOKEN } from "../lib/api";
import { useApi } from "../lib/useApi";
import { bytes, compact } from "../lib/format";
import { Button, Card, Empty, Field, Input, Loading, PageHeader, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface Entry { name: string; is_dir: boolean; size: number | null; modified: string }
interface Listing { path: string; parent?: string; entries: Entry[]; error?: string }
interface Preview { ok?: boolean; path: string; content?: string; error?: string }

function joinPath(base: string, name: string): string {
  return base.endsWith("/") ? `${base}${name}` : `${base}/${name}`;
}

export function Files() {
  const [path, setPath] = useState("");
  const [mkdirName, setMkdirName] = useState("");
  const [file, setFile] = useState<Preview | null>(null);
  const [busy, setBusy] = useState("");
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const query = `fs/list${path ? `?path=${encodeURIComponent(path)}` : ""}`;
  const { data, loading, error, reload } = useApi<Listing>(query);

  async function openFile(target: string) {
    setFile({ path: target });
    try { setFile(await api(`fs/read-text?path=${encodeURIComponent(target)}`)); }
    catch (e) { setFile({ path: target, error: String(e) }); }
  }

  function downloadFile(target: string) {
    const params = new URLSearchParams({ path: target });
    if (TOKEN) params.set("token", TOKEN);
    window.location.href = `/api/files/download?${params.toString()}`;
  }

  async function jumpDefaultCwd() {
    try {
      const r = await api<{ path?: string }>("fs/default-cwd");
      if (r.path) setPath(r.path);
    } catch (e) { toast(String(e), "err"); }
  }

  async function jumpGitRoot() {
    try {
      const r = await api<{ ok?: boolean; root?: string; error?: string }>(`fs/git-root${data?.path ? `?path=${encodeURIComponent(data.path)}` : ""}`);
      if (r.root) setPath(r.root);
      else toast(r.error || "No git root found", "err");
    } catch (e) { toast(String(e), "err"); }
  }

  async function makeDir() {
    if (!data?.path || !mkdirName.trim()) return;
    setBusy("mkdir");
    try {
      const r = await post<{ ok?: boolean; error?: string }>("files/mkdir", {
        path: data.path,
        name: mkdirName.trim(),
        exist_ok: true,
      });
      if (r.ok) { toast("Folder created"); setMkdirName(""); reload(); }
      else toast(r.error || "Create folder failed", "err");
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  async function upload(selected: FileList | null) {
    const item = selected?.[0];
    if (!item || !data?.path) return;
    setBusy("upload");
    try {
      const form = new FormData();
      form.append("path", data.path);
      form.append("file", item);
      const headers: Record<string, string> = {};
      if (TOKEN) headers["X-Aegis-Token"] = TOKEN;
      const response = await fetch("/api/files/upload", { method: "POST", headers, body: form });
      const body = await response.json();
      if (!response.ok || body.ok === false) toast(body.error || `Upload failed (${response.status})`, "err");
      else { toast("Uploaded"); reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally {
      setBusy("");
      if (uploadRef.current) uploadRef.current.value = "";
    }
  }

  async function remove(target: string, isDir: boolean) {
    if (!window.confirm(`Delete ${target}?`)) return;
    setBusy(target);
    try {
      const r = await post<{ ok?: boolean; error?: string }>("files/delete", { path: target, recursive: isDir });
      if (r.ok) {
        toast("Deleted");
        if (file?.path === target) setFile(null);
        reload();
      } else toast(r.error || "Delete failed", "err");
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  return (
    <>
      <PageHeader
        title="Files"
        sub={data?.path || "Managed workspace files"}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button icon="terminal" onClick={jumpDefaultCwd}>CWD</Button>
            <Button icon="files" onClick={jumpGitRoot}>Git root</Button>
            <input ref={uploadRef} type="file" className="hidden" onChange={(e) => upload(e.target.files)} />
            <Button icon="upload" disabled={!data?.path || busy === "upload"} onClick={() => uploadRef.current?.click()}>Upload</Button>
          </div>
        }
      />
      {error && <Card><Empty icon="alert">Couldn't load - {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="grid gap-[var(--gap)] lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.8fr)]">
          <div className="space-y-[var(--gap)]">
            <Card title={compact(data.path, 70)}
              actions={data.parent ? <button onClick={() => setPath(data.parent!)} className="flex items-center gap-1 text-xs text-dim hover:text-text"><Icon name="chevronRight" size={12} className="rotate-180" /> up</button> : undefined}>
              <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto]">
                <Field label="New folder">
                  <Input value={mkdirName} placeholder="notes" onChange={(e) => setMkdirName(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && makeDir()} />
                </Field>
                <div className="flex items-end">
                  <Button variant="primary" icon="plus" disabled={!mkdirName.trim() || busy === "mkdir"} onClick={makeDir}>Create</Button>
                </div>
              </div>
            </Card>

            <Card title="Directory" pad={false}>
              {data.error && <Empty icon="alert">{data.error}</Empty>}
              {!data.error && !(data.entries || []).length && <Empty icon="files">No files here.</Empty>}
              <div className="scroll-thin max-h-[64vh] overflow-auto">
                {(data.entries || []).map((entry) => {
                  const target = joinPath(data.path, entry.name);
                  return (
                    <div key={entry.name} className="flex items-center gap-2.5 border-b border-border px-[var(--pad)] py-1.5 last:border-0 hover:bg-surface-2">
                      <button
                        onClick={() => entry.is_dir ? setPath(target) : openFile(target)}
                        className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
                      >
                        <Icon name={entry.is_dir ? "files" : "logs"} size={14} className={entry.is_dir ? "text-primary" : "text-faint"} />
                        <span className="min-w-0 flex-1 truncate text-sm text-text">{entry.name}</span>
                        <span className="shrink-0 text-[11px] text-faint">{entry.is_dir ? "" : bytes(entry.size)}</span>
                      </button>
                      {!entry.is_dir && (
                        <button
                          onClick={() => downloadFile(target)}
                          className="shrink-0 text-faint hover:text-primary"
                          title="Download"
                        >
                          <Icon name="download" size={15} />
                        </button>
                      )}
                      <button
                        disabled={busy === target}
                        onClick={() => remove(target, entry.is_dir)}
                        className="shrink-0 text-faint hover:text-danger disabled:opacity-40"
                        title="Delete"
                      >
                        <Icon name="trash" size={15} />
                      </button>
                    </div>
                  );
                })}
              </div>
            </Card>
          </div>

          <Card title={file ? compact(file.path, 58) : "Preview"} pad={false}>
            {!file && <Empty icon="files">Select a file to preview.</Empty>}
            {file && !file.content && !file.error && <Loading />}
            {file?.error && <Empty icon="alert">{file.error}</Empty>}
            {file?.content && (
              <pre className="scroll-thin max-h-[74vh] overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-xs text-dim">{file.content}</pre>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
