import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { Badge, Button, Card, Empty, Field, Loading, PageHeader, useToast } from "../lib/ui";

export function ModelsPage() {
  const [data, setData] = useState<any>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const toast = useToast();

  async function load() {
    const d = await api("models");
    setData(d);
    setProvider(d.provider || d.current_provider || "");
    setModel(d.model || d.current_model || d.default || "");
  }
  useEffect(() => { load().catch((e) => toast(String(e), "err")); }, []);
  if (!data) return <><PageHeader title="Models" /><Loading /></>;

  const presetRows = Object.entries(data.presets || {}).flatMap(([p, models]) =>
    (Array.isArray(models) ? models : []).map((id) => ({ id, provider: p })));
  const apiRows: any[] = data.models || data.available || [];
  const list: any[] = apiRows.length ? apiRows : (presetRows.length ? presetRows : (Array.isArray(data) ? data : []));
  const providers: string[] = data.providers || [...new Set([
    ...list.map((m: any) => m.provider).filter(Boolean),
    ...(data.provider_catalog || []).map((p: any) => p.name).filter(Boolean),
  ])];
  const modelsForProvider = provider ? list.filter((m: any) => !m.provider || m.provider === provider) : list;
  const activeId = data.model;

  async function apply() {
    try {
      const r = await post("models", { provider, model });
      if (r.error) toast(r.error, "err");
      else { toast(`Active model set${r.warning ? " (" + r.warning + ")" : ""}`, "ok"); await load(); }
    } catch (e) { toast(String(e), "err"); }
  }

  return (
    <>
      <PageHeader title="Models" sub={<><Badge status="ok">active</Badge> <span className="mono">{data.provider} · {data.model}</span></>} />
      <div className="stack">
        <Card title="Active model">
          <div className="grid c3" style={{ alignItems: "end" }}>
            <Field label="Provider">
              {providers.length
                ? <select value={provider} onChange={(e) => {
                    const next = e.target.value; setProvider(next);
                    const first = list.find((m: any) => m.provider === next);
                    if (first) setModel(first.id || first.name || String(first));
                  }}>{providers.map((p) => <option key={p}>{p}</option>)}</select>
                : <input value={provider} onChange={(e) => setProvider(e.target.value)} />}
            </Field>
            <Field label="Model"><input value={model} onChange={(e) => setModel(e.target.value)} placeholder="claude-sonnet-4-6" /></Field>
            <Button onClick={apply} icon="check">Set active</Button>
          </div>
        </Card>
        <Card title="Available" pad={false}>
          {!modelsForProvider.length && <Empty small>No model list from this provider.</Empty>}
          <div style={{ padding: modelsForProvider.length ? "2px 14px 6px" : 0, maxHeight: "56vh", overflow: "auto" }}>
            {modelsForProvider.slice(0, 120).map((m: any, i: number) => {
              const id = m.id || m.name || String(m);
              return (
                <div className="row click" key={i} onClick={() => { setModel(id); if (m.provider) setProvider(m.provider); }}>
                  <span className="mono">{id} {id === activeId && <Badge status="ok">active</Badge>}</span>
                  <span className="mut">{m.provider || ""}{m.context ? ` · ${m.context}` : ""}</span>
                </div>
              );
            })}
          </div>
        </Card>
      </div>
    </>
  );
}
