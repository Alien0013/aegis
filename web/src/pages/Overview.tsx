import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { compact, dateish } from "../lib/format";
import { Badge, Button, Card, Empty, Loading, PageHeader, Stat } from "../lib/ui";

const fmt$ = (n: number) => "$" + (Number(n) || 0).toFixed(2);

const QUICK: [string, string, string][] = [
  ["chat", "chat", "Send a live agent turn"],
  ["agents", "agents", "Watch spawned agents live"],
  ["models", "models", "Provider & model"],
  ["memory", "memory", "What the agent remembers"],
  ["channels", "channels", "Pair Telegram / Discord / Slack"],
  ["cron", "cron", "Schedule recurring work"],
];

export function Overview({ go }: { go: (id: string) => void }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => {
    Promise.all([api("status"), api("analytics?days=30"), api("sessions"), api("runs?limit=20"), api("agents")])
      .then(([status, an, sessions, runs, agents]) => setD({ status, an, sessions, runs, agents }))
      .catch((e) => setD({ error: String(e) }));
  }, []);
  if (!d) return <Loading />;
  if (d.error) return <><PageHeader title="Home" /><Card><Empty>Couldn't load — {d.error}. Check your dashboard token.</Empty></Card></>;

  const running = (d.agents.agents || []).filter((a: any) => /run|active/i.test(a.status || "")).length;
  const sessions = Array.isArray(d.sessions) ? d.sessions : (d.sessions?.sessions || []);
  return (
    <>
      <PageHeader title="Home"
        sub={<span className="row-flex" style={{ gap: 8 }}><Badge status={d.status.error ? "offline" : "ok"}>{d.status.error ? "offline" : "online"}</Badge> {d.status.provider} / {d.status.model}</span>}
        actions={<Button icon="chat" onClick={() => go("chat")}>Open chat</Button>} />

      <div className="grid c4">
        <Stat label="Sessions" value={d.status.sessions ?? sessions.length ?? "—"} onClick={() => go("sessions")} />
        <Stat label="Recent runs" value={(d.runs.runs || []).length} sub="last 20" onClick={() => go("runs")} />
        <Stat label="Agents" value={(d.agents.agents || []).length} sub={`${running} running`} onClick={() => go("agents")} />
        <Stat label="Spend · 30d" value={fmt$(d.an.total_cost_usd)} sub={`${d.an.calls ?? 0} calls`} />
      </div>

      <div className="grid c2" style={{ marginTop: 12 }}>
        <Card title="Recent sessions" actions={<Button variant="ghost" sm onClick={() => go("sessions")}>All</Button>} pad={false}>
          {!sessions.length && <Empty small>No sessions yet — say hello in Chat.</Empty>}
          <div style={{ padding: "2px 14px 8px" }}>
            {sessions.slice(0, 8).map((x: any) => (
              <div className="row click" key={x.id} onClick={() => go("sessions")}>
                <span>{compact(x.title || x.id, 60)}</span><span className="pill">{dateish(x.updated_at)}</span>
              </div>
            ))}
          </div>
        </Card>
        <Card title="Quick actions" pad={false}>
          <div style={{ padding: "2px 14px 8px" }}>
            {QUICK.map(([id, , sub]) => (
              <div className="row click" key={id} onClick={() => go(id)}>
                <span style={{ textTransform: "capitalize", fontWeight: 600 }}>{id}</span><span className="mut">{sub}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
