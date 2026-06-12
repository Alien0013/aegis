import { useEffect, useState } from "react";
import { api, post } from "../lib/api";

export function ChannelsPage() {
  const [data, setData] = useState<any>(null);
  async function load() { try { setData(await api("pairing")); } catch (e) { setData({ __err: String(e) }); } }
  useEffect(() => { load(); }, []);
  const pending: any[] = data?.pending || data?.requests || [];
  const authorized: any[] = data?.authorized || data?.users || [];

  async function approve(platform: string, code: string) { await post("pairing", { action: "approve", platform, code }); await load(); }
  async function revoke(platform: string, user_id: string) { await post("pairing", { action: "revoke", platform, user_id }); await load(); }

  return (
    <>
      <div className="head"><h1>Channels & Pairing</h1></div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Connect a channel</h3>
        <div className="mut" style={{ lineHeight: 1.8 }}>
          Set the bot token in <b>API Keys</b> (e.g. <code>TELEGRAM_BOT_TOKEN</code>), then run the gateway:
          <br /><code>aegis gateway --channels telegram,discord,slack</code>
          <br />New users message the bot and appear below to approve.
        </div>
      </div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Pending pairings</h3>
        {!pending.length && <div className="empty">none waiting</div>}
        {pending.map((p, i) => (
          <div className="row" key={i}>
            <span><b>{p.platform}</b> <span className="mut">{p.user || p.user_id || ""}</span> <span className="pill">{p.code}</span></span>
            <button className="btn" onClick={() => approve(p.platform, p.code)}>Approve</button>
          </div>
        ))}
      </div>
      <div className="card">
        <h3>Authorized</h3>
        {!authorized.length && <div className="empty">no authorized users yet</div>}
        {authorized.map((u, i) => (
          <div className="row" key={i}>
            <span><b>{u.platform}</b> <span className="mut">{u.user || u.user_id || u.name || ""}</span></span>
            <button className="btn ghost" onClick={() => revoke(u.platform, u.user_id || u.user || "")}>Revoke</button>
          </div>
        ))}
      </div>
    </>
  );
}
