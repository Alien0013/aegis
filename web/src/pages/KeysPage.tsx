import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { Badge, Button, Card, Empty, Field, PageHeader, useToast } from "../lib/ui";

export function KeysPage() {
  const [keys, setKeys] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [val, setVal] = useState("");
  const toast = useToast();
  async function load() { try { const d = await api("keys"); setKeys(d.keys || (Array.isArray(d) ? d : Object.entries(d).map(([k, v]) => ({ key: k, set: !!v })))); } catch { setKeys([]); } }
  useEffect(() => { load(); }, []);
  async function save() {
    if (!name.trim()) return;
    try { await post("keys", { key: name.trim(), value: val }); setName(""); setVal(""); toast("Saved to ~/.aegis/.env", "ok"); await load(); }
    catch (e) { toast(String(e), "err"); }
  }
  return (
    <>
      <PageHeader title="API Keys" sub="stored in ~/.aegis/.env (chmod 0600)" />
      <div className="stack">
        <Card title="Set a key">
          <div className="grid c3" style={{ alignItems: "end" }}>
            <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="ANTHROPIC_API_KEY" /></Field>
            <Field label="Value"><input type="password" value={val} onChange={(e) => setVal(e.target.value)} placeholder="sk-…" /></Field>
            <Button onClick={save} icon="key">Save</Button>
          </div>
        </Card>
        <Card title="Configured" pad={false}>
          {!keys.length && <Empty small>No keys set yet.</Empty>}
          <div style={{ padding: keys.length ? "2px 14px 6px" : 0 }}>
            {keys.map((k, i) => (
              <div className="row" key={i}>
                <span className="mono">{k.key || k.name || String(k)}</span>
                <Badge status={k.set === false ? "empty" : "set"}>{k.set === false ? "empty" : "set"}</Badge>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
