import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { compact, dateish } from "../lib/format";
import { Badge, BarChart, Button, Card, Empty, Loading, PageHeader, Stat } from "../lib/ui";

const fmt$ = (n: number) => "$" + (Number(n) || 0).toFixed(2);

const QUICK: [string, string][] = [
  ["chat", "Send a live agent turn"],
  ["agents", "Watch spawned agents live"],
  ["models", "Provider & model"],
  ["memory", "What the agent remembers"],
  ["channels", "Channels & gateway"],
  ["cron", "Schedule recurring work"],
];

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
      .then(([status, an, sessions, runs, agents, gateway, profiles, plugins, mcp, auth]) => setD({ status, an, sessions, runs, agents, gateway, profiles, plugins, mcp, auth }))
      .catch((e) => setD({ error: String(e) }));
  }, []);
  if (!d) return <Loading />;
  if (d.error) return <><PageHeader title="Home" /><Card><Empty>Couldn't load — {d.error}. Check your dashboard token.</Empty></Card></>;

  const running = (d.agents.agents || []).filter((a: any) => /run|active/i.test(a.status || "")).length;
  const sessions = Array.isArray(d.sessions) ? d.sessions : (d.sessions?.sessions || []);
  const series: any[] = d.an.series || [];
  const calls = series.map((s) => Number(s.calls) || 0);
  const cost = series.map((s) => Number(s.cost_usd) || 0);
  const anyData = calls.some((c) => c) || cost.some((c) => c);
  return (
    <>
      <PageHeader title="Home"
        sub={<span className="inline-flex items-center gap-2">
          <Badge status={d.status.error ? "offline" : "ok"}>{d.status.error ? "offline" : "online"}</Badge>
          {d.status.provider} / {d.status.model}
        </span>}
        actions={<Button icon="chat" onClick={() => go("chat")}>Open chat</Button>} />

      <div className="grid c4">
        <Stat label="Sessions" value={d.status.sessions ?? sessions.length ?? "—"} onClick={() => go("sessions")} />
        <Stat label="Recent runs" value={(d.runs.runs || []).length} sub="last 20" onClick={() => go("runs")} />
        <Stat label="Agents" value={(d.agents.agents || []).length} sub={`${running} running`} onClick={() => go("agents")} />
        <Stat label="Spend · 30d" value={fmt$(d.an.total_cost_usd)} sub={`${d.an.calls ?? 0} calls`} />
      </div>

      <div className="mt-3">
        <Card title="Activity · 30 days" actions={<span className="text-xs text-mut">{d.an.calls ?? 0} calls · {fmt$(d.an.total_cost_usd)}</span>}>
          {!anyData
            ? <Empty small>No activity recorded yet — runs and spend will chart here.</Empty>
            : (
              <div className="grid gap-5 c2">
                <div><div className="mb-1 text-[11px] text-mut">Calls / day</div><BarChart data={calls} color="var(--accent)" /></div>
                <div><div className="mb-1 text-[11px] text-mut">Spend / day</div><BarChart data={cost} color="var(--accent2)" /></div>
              </div>
            )}
        </Card>
      </div>

      <div className="mt-3">
        <Card title="Setup health" pad={false}>
          <div className="setup-grid">
            <div className="setup-item click" onClick={() => go("models")}>
              <Badge status={d.auth?.active?.ready ? "ready" : "missing"}>{d.auth?.active?.ready ? "auth ready" : "auth missing"}</Badge>
              <b>{d.status.provider}</b><span>{d.auth?.active?.missing_env_vars?.join(", ") || d.status.model}</span>
            </div>
            <div className="setup-item click" onClick={() => go("profiles")}>
              <Badge status={d.profiles?.active ? "active" : "idle"}>{d.profiles?.active || "default"}</Badge>
              <b>Profile</b><span>{d.profiles?.available?.length || 0} saved</span>
            </div>
            <div className="setup-item click" onClick={() => go("channels")}>
              <Badge status={d.gateway?.configured ? "ready" : "idle"}>{d.gateway?.configured ? "configured" : "off"}</Badge>
              <b>Gateway</b><span>{d.gateway?.channels?.length || 0} channels</span>
            </div>
            <div className="setup-item click" onClick={() => go("mcp")}>
              <Badge status={d.mcp?.available ? "ready" : "idle"}>{d.mcp?.available ? "connected" : "none"}</Badge>
              <b>MCP</b><span>{d.mcp?.servers?.length || 0} servers</span>
            </div>
            <div className="setup-item click" onClick={() => go("plugins")}>
              <Badge status={d.plugins?.errors?.length ? "error" : "ready"}>{d.plugins?.errors?.length ? "errors" : "clean"}</Badge>
              <b>Plugins</b><span>{(d.plugins?.plugins || []).length} installed</span>
            </div>
            <div className="setup-item click" onClick={() => go("config")}>
              <Badge status="ready">editable</Badge>
              <b>Config</b><span>{d.status.exec_mode} permissions</span>
            </div>
          </div>
        </Card>
      </div>

      <div className="mt-3 grid c2">
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
            {QUICK.map(([id, sub]) => (
              <div className="row click" key={id} onClick={() => go(id)}>
                <span className="font-semibold capitalize">{id}</span><span className="text-mut">{sub}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
