import { useEffect, useMemo, useState } from "react";
import { api, post } from "../lib/api";
import { Badge, Button, Card, Empty, Field, Loading, PageHeader, useToast } from "../lib/ui";

const authStatus = (row: any) => {
  const auth = row?.auth || {};
  if (row?.error) return "error";
  if (auth.ready === true || auth.status === "ready" || auth.status === "none") return "ready";
  if (auth.ready === false || auth.status === "missing") return "missing";
  return auth.status || "unknown";
};

export function ModelsPage() {
  const [data, setData] = useState<any>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [q, setQ] = useState("");
  const [probe, setProbe] = useState<any>(null);
  const [authData, setAuthData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function load() {
    const [d, auth] = await Promise.all([api("models"), api("provider-auth").catch(() => null)]);
    setData(d);
    setAuthData(auth);
    setProvider(d.provider || d.current_provider || "");
    setModel(d.model || d.current_model || d.default || "");
  }

  useEffect(() => { load().catch((e) => toast(String(e), "err")); }, []);
  const catalog: any[] = data?.provider_catalog || [];
  const providers: string[] = data?.providers || catalog.map((p: any) => p.name).filter(Boolean);
  const active = data?.active || {};
  const activeCatalog = catalog.find((p: any) => p.name === provider) || {};
  const selectedAuth = (authData?.providers || []).find((p: any) => p.name === provider) || authData?.active || {};
  const presets = data?.presets || {};
  const presetRows = Object.entries(presets).flatMap(([p, models]) =>
    (Array.isArray(models) ? models : []).map((id) => ({ id, provider: p })));
  const apiRows: any[] = data?.models || data?.available || [];
  const list: any[] = apiRows.length ? apiRows : presetRows;
  const modelsForProvider = provider ? list.filter((m: any) => !m.provider || m.provider === provider) : list;
  const filteredCatalog = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return catalog;
    return catalog.filter((p: any) => `${p.name} ${p.display_name || ""} ${p.default_model || ""} ${p.capability_summary || ""}`.toLowerCase().includes(query));
  }, [catalog, q]);

  if (!data) return <><PageHeader title="Models" /><Loading /></>;

  async function apply() {
    try {
      const r = await post("models", { provider, model });
      if (r.error || r.ok === false) toast(r.error || "model rejected", "err");
      else { toast(`Active model set${r.warning ? " (" + r.warning + ")" : ""}`, "ok"); await load(); }
    } catch (e) { toast(String(e), "err"); }
  }

  async function testProvider() {
    setBusy(true);
    setProbe(null);
    try {
      const r = await post("providers/probe", { provider, model });
      setProbe(r);
      toast(r.ok ? "Provider responded" : "Provider probe failed", r.ok ? "ok" : "err");
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }

  function addKey(key: string) {
    window.location.hash = `#/keys?key=${encodeURIComponent(key)}`;
  }

  return (
    <>
      <PageHeader
        title="Models"
        sub={<><Badge status={authStatus(active)}>active</Badge> <span className="mono">{data.provider} · {data.model}</span></>}
        actions={<Button onClick={testProvider} disabled={busy} icon="bolt">{busy ? "Testing..." : "Test now"}</Button>}
      />
      <div className="stack">
        <div className="grid c3">
          <Card title="Active route">
            <div className="statline"><b>{active.name || data.provider}</b><span>{active.model || data.model}</span></div>
            <div className="mut">{active.capability_summary || "capabilities unknown"}</div>
            <div className="badge-row">
              <Badge status={authStatus(active)}>{authStatus(active)}</Badge>
              {active.context_length && <Badge>{Number(active.context_length).toLocaleString()} ctx</Badge>}
              {active.api_mode && <Badge>{active.api_mode}</Badge>}
            </div>
            {active.error && <div className="notice err">{active.error}</div>}
            {active.warning && <div className="notice warn">{active.warning}</div>}
          </Card>
          <Card title="Auth">
            <div className="mut">{activeCatalog.auth_scheme || selectedAuth.auth_scheme || active.auth?.scheme || "provider auth"}</div>
            <div className="pill-list">
              {(selectedAuth.env_vars || activeCatalog.env_vars || active.auth?.env_vars || []).map((v: string) => (
                <button className={`pill mono key-pill ${selectedAuth.missing_env_vars?.includes(v) ? "warn" : ""}`} key={v} onClick={() => addKey(v)}>{v}</button>
              ))}
              {(activeCatalog.oauth || selectedAuth.oauth) && <span className="pill">OAuth {selectedAuth.oauth_status || activeCatalog.oauth_status}</span>}
              {!selectedAuth.env_vars?.length && !activeCatalog.env_vars?.length && !activeCatalog.oauth && <span className="pill">no key required</span>}
            </div>
            {!!selectedAuth.missing_env_vars?.length && (
              <div className="notice warn">
                Missing {selectedAuth.missing_env_vars.join(", ")}
                <div className="actions" style={{ marginTop: 7 }}>
                  {selectedAuth.missing_env_vars.map((v: string) => <Button key={v} sm variant="ghost" icon="key" onClick={() => addKey(v)}>Set {v}</Button>)}
                </div>
              </div>
            )}
            {(selectedAuth.oauth_notes || activeCatalog.oauth_notes) && <div className="mut">{selectedAuth.oauth_notes || activeCatalog.oauth_notes}</div>}
          </Card>
          <Card title="Probe result">
            {!probe && <div className="mut">Run a live one-token probe for the selected provider/model.</div>}
            {probe && <>
              <Badge status={probe.ok ? "ok" : "error"}>{probe.ok ? "passed" : "failed"}</Badge>
              <div className="mono smalltext">{probe.detail}</div>
            </>}
          </Card>
        </div>

        <Card title="Set active model">
          <div className="grid c3" style={{ alignItems: "end" }}>
            <Field label="Provider">
              {providers.length
                ? <select value={provider} onChange={(e) => {
                    const next = e.target.value;
                    setProvider(next);
                    const first = list.find((m: any) => m.provider === next);
                    const cat = catalog.find((p: any) => p.name === next);
                    setModel(first?.id || first?.name || cat?.default_model || "");
                  }}>{providers.map((p) => <option key={p}>{p}</option>)}</select>
                : <input value={provider} onChange={(e) => setProvider(e.target.value)} />}
            </Field>
            <Field label="Model"><input value={model} onChange={(e) => setModel(e.target.value)} placeholder={activeCatalog.default_model || "model id"} /></Field>
            <Button onClick={apply} icon="check">Set active</Button>
          </div>
        </Card>

        <div className="grid c2">
          <Card title="Available models" pad={false}>
            {!modelsForProvider.length && <Empty small>No model list from this provider.</Empty>}
            <div style={{ padding: modelsForProvider.length ? "2px 14px 6px" : 0, maxHeight: "46vh", overflow: "auto" }}>
              {modelsForProvider.slice(0, 160).map((m: any, i: number) => {
                const id = m.id || m.name || String(m);
                return (
                  <div className="row click" key={i} onClick={() => { setModel(id); if (m.provider) setProvider(m.provider); }}>
                    <span className="mono">{id} {id === data.model && <Badge status="ok">active</Badge>}</span>
                    <span className="mut">{m.provider || ""}{m.context ? ` · ${m.context}` : ""}</span>
                  </div>
                );
              })}
            </div>
          </Card>
          <Card title="Provider catalog" actions={<input className="search compact" placeholder="Search providers" value={q} onChange={(e) => setQ(e.target.value)} />} pad={false}>
            {!filteredCatalog.length && <Empty small>No providers match.</Empty>}
            <div style={{ padding: filteredCatalog.length ? "2px 14px 6px" : 0, maxHeight: "46vh", overflow: "auto" }}>
              {filteredCatalog.map((p: any) => (
                <div className="row click" key={p.name} onClick={() => { setProvider(p.name); setModel(p.default_model || ""); }}>
                  <span style={{ minWidth: 0 }}>
                    <b>{p.display_name || p.name}</b> <span className="mono mut">{p.default_model}</span>
                    <br /><span className="mut">{p.capability_summary || p.api_mode}</span>
                  </span>
                  <span className="actions">
                    <Badge status={authStatus(p)}>{authStatus(p)}</Badge>
                    <Badge>{p.origin}</Badge>
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="grid c2">
          <Card title="Fallbacks" pad={false}>
            {!data.fallbacks?.length && <Empty small>No fallback providers configured.</Empty>}
            {(data.fallbacks || []).map((f: any, i: number) => (
              <div className="row" key={i}><span><b>{f.name}</b> <span className="mono mut">{f.model}</span></span><Badge status={f.error ? "error" : "ready"}>{f.error ? "error" : "ready"}</Badge></div>
            ))}
          </Card>
          <Card title="Routing" pad={false}>
            {!data.routing?.length && <Empty small>No prompt routing rules.</Empty>}
            {(data.routing || []).map((r: any) => (
              <div className="row" key={r.index}><span><b>{r.match}</b> <span className="mono mut">{r.provider}/{r.model}</span></span><Badge status={r.warning ? "warn" : "ok"}>{r.warning || "ok"}</Badge></div>
            ))}
          </Card>
        </div>
      </div>
    </>
  );
}
