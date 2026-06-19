import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { del, post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Input, Loading, PageHeader, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface KeyRow { key: string; set: boolean; source?: string; length?: number }
interface EnvPayload { env_path?: string; keys?: KeyRow[] }

export function Keys() {
  const { data, loading, error, reload } = useApi<EnvPayload>("env");
  const [searchParams, setSearchParams] = useSearchParams();
  const [editing, setEditing] = useState("");
  const [value, setValue] = useState("");
  const [newKey, setNewKey] = useState("");
  const rows = data?.keys || [];
  const requestedKey = (searchParams.get("key") || "").trim().toUpperCase();

  useEffect(() => {
    if (!requestedKey) return;
    if (rows.some((row) => row.key === requestedKey)) {
      setEditing(requestedKey);
      setNewKey("");
    } else {
      setNewKey(requestedKey);
      setEditing("__new__");
    }
    setValue("");
  }, [requestedKey, rows]);

  async function setVal(key: string, v: string) {
    if (!v) return false;
    try {
      await post("env", { key, value: v });
      toast(`Set ${key}`);
      setEditing("");
      setValue("");
      reload();
      return true;
    } catch (e) {
      toast(String(e), "err");
      return false;
    }
  }
  async function remove(key: string) {
    try { await del(`env/${encodeURIComponent(key)}`); toast(`Removed ${key}`); reload(); }
    catch (e) { toast(String(e), "err"); }
  }

  return (
    <>
      <PageHeader title="Env" sub={data?.env_path || "Secrets live in the profile .env file; values are hidden"} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <Card title="Add a secret">
            <div className="flex flex-wrap items-end gap-2">
              <Input className="w-64" value={newKey} placeholder="ANTHROPIC_API_KEY" onChange={(e) => setNewKey(e.target.value.toUpperCase())} />
              <Input className="flex-1" type="password" value={editing === "__new__" ? value : ""} placeholder="value (hidden)"
                onFocus={() => setEditing("__new__")} onChange={(e) => setValue(e.target.value)} />
              <Button variant="primary" icon="plus" disabled={!newKey.trim() || !value}
                onClick={async () => {
                  if (await setVal(newKey.trim(), value)) {
                    setNewKey("");
                    setSearchParams({});
                  }
                }}>Save</Button>
            </div>
          </Card>
          <Card title="Known keys" pad={false}>
            {rows.map((k) => (
              <div key={k.key} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2 last:border-0">
                <span className="min-w-0 flex-1 truncate font-mono text-sm text-text">{k.key}</span>
                <Badge status={k.set ? "ok" : undefined} tone={k.set ? undefined : "neutral"}>{k.set ? "set" : "missing"}</Badge>
                {k.source && <Badge tone="neutral">{k.source}</Badge>}
                {k.set && typeof k.length === "number" && <span className="text-xs tabular-nums text-faint">{k.length} chars</span>}
                {editing === k.key ? (
                  <Input autoFocus type="password" className="w-52" value={value} placeholder="new value"
                    onChange={(e) => setValue(e.target.value)}
                    onKeyDown={async (e) => {
                      if (e.key === "Enter" && await setVal(k.key, value)) setSearchParams({});
                    }} />
                ) : (
                  <button onClick={() => { setEditing(k.key); setValue(""); }} className="text-faint hover:text-primary" title="Set value"><Icon name="config" size={15} /></button>
                )}
                {k.set && <button onClick={() => remove(k.key)} className="text-faint hover:text-danger" title="Delete"><Icon name="trash" size={15} /></button>}
              </div>
            ))}
          </Card>
        </div>
      )}
    </>
  );
}
