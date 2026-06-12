import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { compact, countLabel, dateish } from "../lib/format";

const fmt$ = (n: number) => "$" + (Number(n) || 0).toFixed(2);

export function Overview({ go }: { go: (id: string) => void }) {
  const [d, setD] = useState<any>(null);
  const [welcome, setWelcome] = useState(!localStorage.getItem("aegis_welcomed"));
  useEffect(() => {
    Promise.all([api("status"), api("analytics?days=30"), api("sessions"), api("runs?limit=20"), api("agents")])
      .then(([status, an, sessions, runs, agents]) => setD({ status, an, sessions, runs, agents }))
      .catch((e) => setD({ error: String(e) }));
  }, []);
  if (!d) return <div className="empty"><span className="spin" /> loading...</div>;
  if (d.error) return <div className="panel"><h3>Couldn't load</h3><div className="mut">{d.error}. Check your dashboard token.</div></div>;
  const tiles = [
    ["Sessions", d.status.sessions ?? "-", ""],
    ["Runs", (d.runs.runs || []).length, "recent"],
    ["Agents", (d.agents.agents || []).length, "configured + active"],
    ["Spend / 30d", fmt$(d.an.total_cost_usd), (d.an.calls ?? 0) + " calls"],
  ];
  return (
    <>
      <div className="head">
        <div>
          <h1>Overview</h1>
          <span className="crumb">{d.status.provider} / {d.status.model}</span>
        </div>
        <button className="btn" onClick={() => go("chat")}>Open chat</button>
      </div>
      {welcome && (
        <div className="banner" style={{ marginBottom: 14 }}>
          <div className="welcome-row">
            <div>
              <h3>Welcome to AEGIS</h3>
              <div className="mut" style={{ lineHeight: 1.9 }}>
                Three ways to use it: <b>Chat</b> here, <code>aegis</code> in a terminal, or <code>aegis tui</code> full-screen.<br />
                <b>Models</b> - pick provider / <b>Channels</b> - Telegram, Discord, Slack / <b>Skills</b> and <b>Memory</b> - what it knows / <b>Cron</b> - schedules.
              </div>
            </div>
            <button className="btn ghost" onClick={() => { localStorage.setItem("aegis_welcomed", "1"); setWelcome(false); }}>Got it</button>
          </div>
        </div>
      )}
      <div className="grid c4">
        {tiles.map(([l, v, s]) => (
          <div className="panel stat" key={l as string}><div className="lbl">{l}</div><div className="val">{v}</div><div className="sub">{s}</div></div>
        ))}
      </div>
      <div className="grid c2" style={{ marginTop: 14 }}>
        <div className="panel">
          <h3>Recent sessions</h3>
          {(d.sessions || []).slice(0, 8).map((x: any) => (
            <div className="row click" key={x.id} onClick={() => go("sessions")}>
              <span>{compact(x.title || x.id, 68)}</span><span className="pill">{dateish(x.updated_at)}</span>
            </div>
          ))}
          {(!d.sessions || !d.sessions.length) && <div className="empty">no sessions yet - say hello in Chat</div>}
        </div>
        <div className="panel">
          <h3>Operational map</h3>
          {[
            ["chat", "Send a live agent turn"],
            ["kanban", "Plan and run agent cards"],
            ["runs", countLabel((d.runs.runs || []).length, "recent run")],
            ["traces", "Inspect tool and model spans"],
            ["models", `${d.status.provider}/${d.status.model}`],
            ["channels", "Pair messaging users"],
            ["cron", "Schedule recurring work"],
          ].map(([id, sub]) => (
            <div className="row click" key={id} onClick={() => go(id)}><span style={{ textTransform: "capitalize" }}>{id}</span><span className="mut">{sub}</span></div>
          ))}
        </div>
      </div>
    </>
  );
}
