import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { compact, dateish } from "../lib/format";
import { Icon } from "../lib/icons";
import { Badge, BarChart, Button, Card, Empty, Loading } from "../lib/ui";

const fmt$ = (n: number) => "$" + (Number(n) || 0).toFixed(2);

const QUICK: [string, string, string][] = [
  ["chat", "chat", "Send a live agent turn"],
  ["agents", "agents", "Watch spawned agents live"],
  ["models", "models", "Provider & model routing"],
  ["memory", "memory", "What the agent remembers"],
  ["channels", "channels", "Channels & gateway"],
  ["cron", "cron", "Schedule recurring work"],
];

type Kpi = { label: string; icon: string; value: any; sub?: string; go?: string; spark?: number[]; sparkColor?: string };

export function Overview({ go }: { go: (id: string) => void }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => {
    Promise.all([
      api("status"),
      api("analytics?days=30"),
      api("sessions"),
      api("runs?limit=20"),
      api("agents"),
      api("gateway/status").catch(() => null),
      api("profiles").catch(() => null),
      api("plugins").catch(() => null),
      api("mcp/servers").catch(() => null),
      api("provider-auth").catch(() => null),
    ])
      .then(([status, an, sessions, runs, agents, gateway, profiles, plugins, mcp, auth]) =>
        setD({ status, an, sessions, runs, agents, gateway, profiles, plugins, mcp, auth }))
      .catch((e) => setD({ error: String(e) }));
  }, []);
  if (!d) return <Loading />;
  if (d.error) return <Card><Empty>Couldn't load — {d.error}. Check your dashboard token.</Empty></Card>;

  const online = !d.status.error;
  const running = (d.agents.agents || []).filter((a: any) => /run|active/i.test(a.status || "")).length;
  const sessions = Array.isArray(d.sessions) ? d.sessions : (d.sessions?.sessions || []);
  const series: any[] = d.an.series || [];
  const calls = series.map((s) => Number(s.calls) || 0);
  const cost = series.map((s) => Number(s.cost_usd) || 0);
  const anyData = calls.some((c) => c) || cost.some((c) => c);

  const kpis: Kpi[] = [
    { label: "Sessions", icon: "sessions", value: d.status.sessions ?? sessions.length ?? "—", sub: "stored", go: "sessions" },
    { label: "Agents", icon: "agents", value: (d.agents.agents || []).length, sub: `${running} running now`, go: "agents" },
    { label: "Calls · 30d", icon: "logs", value: (d.an.calls ?? 0).toLocaleString(), sub: "model API calls", go: "traces", spark: calls, sparkColor: "var(--accent)" },
    { label: "Spend · 30d", icon: "models", value: fmt$(d.an.total_cost_usd), sub: "estimated", go: "system", spark: cost, sparkColor: "var(--accent2)" },
  ];

  const health = [
    { go: "models", ok: d.auth?.active?.ready, on: "auth ready", off: "auth missing", title: d.status.provider, note: d.auth?.active?.missing_env_vars?.join(", ") || d.status.model },
    { go: "profiles", ok: !!d.profiles?.active, on: d.profiles?.active || "default", off: "default", title: "Profile", note: `${d.profiles?.available?.length || 0} saved` },
    { go: "channels", ok: d.gateway?.configured, on: "configured", off: "off", title: "Gateway", note: `${d.gateway?.channels?.length || 0} channels` },
    { go: "mcp", ok: d.mcp?.available, on: "connected", off: "none", title: "MCP", note: `${d.mcp?.servers?.length || 0} servers` },
    { go: "plugins", ok: !(d.plugins?.errors?.length), on: "clean", off: "errors", title: "Plugins", note: `${(d.plugins?.plugins || []).length} installed` },
    { go: "config", ok: true, on: "editable", off: "editable", title: "Config", note: `${d.status.exec_mode} permissions` },
  ];

  return (
    <>
      <section className="hero">
        <div className="hero-row">
          <div>
            <h1>AEGIS</h1>
            <div className="sub">
              <Badge status={online ? "ok" : "err"}>{online ? "online" : "offline"}</Badge>
              <span className="mono">{d.status.provider} / {d.status.model}</span>
            </div>
            <div className="hero-chips">
              <span className="hero-chip"><span className={"dot" + (online ? "" : " off")} /> <b>{d.status.exec_mode || "auto"}</b> perms</span>
              <span className="hero-chip">reasoning <b>{d.status.reasoning_effort || "medium"}</b></span>
              <span className="hero-chip"><span className={"dot" + (d.gateway?.channels?.length ? "" : " off")} /> {d.gateway?.channels?.length || 0} channels</span>
              <span className="hero-chip"><span className={"dot" + (d.profiles?.active ? "" : " off")} /> {d.profiles?.active || "default"} profile</span>
            </div>
          </div>
          <div className="row-flex">
            <Button variant="ghost" icon="system" onClick={() => go("terminal")}>Terminal</Button>
            <Button icon="chat" onClick={() => go("chat")}>Open chat</Button>
          </div>
        </div>
      </section>

      <div className="kpi-grid">
        {kpis.map((k, i) => (
          <div className="kpi" key={k.label} style={{ animationDelay: `${i * 60}ms` }} onClick={() => k.go && go(k.go)}>
            <div className="kpi-top"><span>{k.label}</span><span className="ic"><Icon n={k.icon} /></span></div>
            <div className="val">{k.value}</div>
            <div className="sub">{k.sub}</div>
            {k.spark && k.spark.some((v) => v) && (
              <div className="kpi-spark"><BarChart data={k.spark} height={34} color={k.sparkColor} /></div>
            )}
          </div>
        ))}
      </div>

      <div className="section-title">Activity · last 30 days</div>
      <Card pad={!anyData}>
        {!anyData
          ? <Empty small>No activity recorded yet — runs and spend will chart here.</Empty>
          : (
            <div className="grid gap-5 c2">
              <div><div className="mb-1 text-[11px] text-mut">Calls / day · {d.an.calls ?? 0} total</div><BarChart data={calls} height={66} color="var(--accent)" /></div>
              <div><div className="mb-1 text-[11px] text-mut">Spend / day · {fmt$(d.an.total_cost_usd)}</div><BarChart data={cost} height={66} color="var(--accent2)" /></div>
            </div>
          )}
      </Card>

      <div className="section-title">Setup health</div>
      <Card pad={false}>
        <div className="setup-grid">
          {health.map((h) => (
            <div className="setup-item click" key={h.title} onClick={() => go(h.go)}>
              <Badge status={h.ok ? "ready" : "warn"}>{h.ok ? h.on : h.off}</Badge>
              <b>{h.title}</b><span>{h.note}</span>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid c2" style={{ marginTop: 12 }}>
        <Card title="Recent sessions" actions={<Button variant="ghost" sm onClick={() => go("sessions")}>All</Button>} pad={false}>
          {!sessions.length && <Empty small>No sessions yet — say hello in Chat.</Empty>}
          <div className="px-3.5 pb-2 pt-0.5">
            {sessions.slice(0, 8).map((x: any) => (
              <div className="row click" key={x.id} onClick={() => go("sessions")}>
                <span>{compact(x.title || x.id, 60)}</span><span className="pill">{dateish(x.updated_at)}</span>
              </div>
            ))}
          </div>
        </Card>
        <Card title="Quick actions" pad={false}>
          <div className="px-3.5 pb-2 pt-0.5">
            {QUICK.map(([id, , sub]) => (
              <div className="row click" key={id} onClick={() => go(id)}>
                <span className="row-flex" style={{ gap: 9 }}><Icon n={id} /> <span className="font-semibold capitalize">{id}</span></span>
                <span className="text-mut">{sub}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
