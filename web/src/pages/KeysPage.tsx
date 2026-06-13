import { useEffect, useMemo, useState } from "react";
import { api, apiDelete, post } from "../lib/api";
import { Badge, Button, Card, Empty, Field, PageHeader, useToast } from "../lib/ui";

type EnvKey = { key: string; set?: boolean; source?: string; preview?: string; length?: number };

const PROVIDER_KEYS = [
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "GOOGLE_API_KEY",
  "OPENROUTER_API_KEY",
  "QWEN_API_KEY",
  "DASHSCOPE_API_KEY",
  "XAI_API_KEY",
  "MINIMAX_API_KEY",
];

const CHANNEL_KEYS = [
  "TELEGRAM_BOT_TOKEN",
  "DISCORD_BOT_TOKEN",
  "SLACK_BOT_TOKEN",
  "SLACK_APP_TOKEN",
  "MATRIX_PASSWORD",
  "EMAIL_PASSWORD",
  "NTFY_TOKEN",
];

function groupFor(key: string): string {
  if (CHANNEL_KEYS.includes(key)) return "Channels";
  if (PROVIDER_KEYS.includes(key) || /API_KEY$/.test(key)) return "Providers";
  if (/(TOKEN|PASSWORD|SECRET|KEY)$/.test(key)) return "Other secrets";
  return "Environment";
}

export function KeysPage() {
  const [keys, setKeys] = useState<EnvKey[]>([]);
  const [envPath, setEnvPath] = useState("~/.aegis/.env");
  const [name, setName] = useState("");
  const [val, setVal] = useState("");
  const [q, setQ] = useState("");
  const [show, setShow] = useState(false);
  const toast = useToast();

  async function load() {
    const d = await api("env");
    setEnvPath(d.env_path || "~/.aegis/.env");
    setKeys(d.keys || []);
  }

  useEffect(() => { load().catch((e) => toast(String(e), "err")); }, []);
  useEffect(() => {
    const query = window.location.hash.split("?", 2)[1] || "";
    const key = new URLSearchParams(query).get("key");
    if (key) setName(key.toUpperCase());
  }, []);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    const rows = query ? keys.filter((k) => k.key.toLowerCase().includes(query)) : keys;
    return rows.sort((a, b) => groupFor(a.key).localeCompare(groupFor(b.key)) || a.key.localeCompare(b.key));
  }, [keys, q]);
  const groups = [...new Set(filtered.map((k) => groupFor(k.key)))];

  async function save() {
    const key = name.trim().toUpperCase();
    if (!key) return;
    try {
      await post("env", { key, value: val });
      setName("");
      setVal("");
      setShow(false);
      toast("Saved to local .env", "ok");
      await load();
    } catch (e) { toast(String(e), "err"); }
  }

  async function reveal(key: string) {
    try {
      const r = await api(`env/${encodeURIComponent(key)}/reveal`);
      setName(r.key || key);
      setVal(r.value || "");
      setShow(true);
      toast(`${key} loaded for editing`);
    } catch (e) { toast(String(e), "err"); }
  }

  async function remove(key: string) {
    try {
      await apiDelete(`env/${encodeURIComponent(key)}`);
      toast(`${key} removed`, "ok");
      await load();
    } catch (e) { toast(String(e), "err"); }
  }

  return (
    <>
      <PageHeader
        title="API Keys"
        sub={<>stored in <span className="mono">{envPath}</span> · comma-separated provider keys form a credential pool</>}
      />
      <div className="stack">
        <Card title="Presets">
          <div className="key-preset-grid">
            {[...PROVIDER_KEYS, ...CHANNEL_KEYS].map((k) => {
              const row = keys.find((x) => x.key === k);
              return (
                <button className="key-preset" key={k} onClick={() => setName(k)}>
                  <span className="mono">{k}</span>
                  <Badge status={row?.set === false || !row ? "missing" : "set"}>{row?.set === false || !row ? "missing" : "set"}</Badge>
                </button>
              );
            })}
          </div>
        </Card>
        <Card title="Set a key">
          <div className="grid c3" style={{ alignItems: "end" }}>
            <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value.toUpperCase())} placeholder="OPENAI_API_KEY" /></Field>
            <Field label="Value"><input type={show ? "text" : "password"} value={val} onChange={(e) => setVal(e.target.value)} placeholder="hidden value" /></Field>
            <span className="actions">
              <Button onClick={() => setShow(!show)} variant="ghost" icon="key">{show ? "Hide" : "Show"}</Button>
              <Button onClick={save} icon="check">Save</Button>
            </span>
          </div>
        </Card>
        <Card title="Configured" actions={<input className="search compact" placeholder="Search keys" value={q} onChange={(e) => setQ(e.target.value)} />} pad={false}>
          {!filtered.length && <Empty small>No keys match.</Empty>}
          {groups.map((group) => (
            <div key={group} className="key-group">
              <div className="list-subhead">{group}</div>
              {filtered.filter((k) => groupFor(k.key) === group).map((k) => (
                <div className="row" key={k.key}>
                  <span style={{ minWidth: 0 }}>
                    <span className="mono">{k.key}</span>
                    <span className="mut"> · {k.source || "missing"}{k.length ? ` · ${k.length} chars` : ""}</span>
                  </span>
                  <span className="actions">
                    <Badge status={k.set === false ? "empty" : "set"}>{k.set === false ? "empty" : "set"}</Badge>
                    <Button variant="ghost" sm onClick={() => reveal(k.key)} disabled={k.set === false}>Reveal</Button>
                    <Button variant="danger" sm onClick={() => remove(k.key)} disabled={k.set === false}>Delete</Button>
                  </span>
                </div>
              ))}
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
