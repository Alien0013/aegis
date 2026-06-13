import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { Button, Card, Empty, PageHeader, useToast } from "../lib/ui";

export function ChannelsPage() {
  const [data, setData] = useState<any>(null);
  const toast = useToast();
  async function load() { try { setData(await api("pairing")); } catch (e) { setData({ __err: String(e) }); } }
  useEffect(() => { load(); }, []);
  const pending: any[] = data?.pending || data?.requests || [];
  const authorized: any[] = data?.authorized || data?.users || [];

  async function approve(platform: string, code: string) { await post("pairing", { action: "approve", platform, code }); toast("Approved", "ok"); await load(); }
  async function revoke(platform: string, user_id: string) { await post("pairing", { action: "revoke", platform, user_id }); toast("Revoked"); await load(); }

  return (
    <>
      <PageHeader title="Channels & Pairing" sub="Telegram · Discord · Slack" />
      <div className="stack">
        <Card title="Connect a channel">
          <div className="mut" style={{ lineHeight: 1.9 }}>
            Set the bot token in <b>API Keys</b> (e.g. <code>TELEGRAM_BOT_TOKEN</code>), then run the gateway:
            <br /><code>aegis gateway --channels telegram,discord,slack</code>
            <br />New users message the bot and appear below to approve.
          </div>
        </Card>
        <Card title="Pending pairings" pad={false}>
          {!pending.length && <Empty small>None waiting.</Empty>}
          <div style={{ padding: pending.length ? "2px 14px 6px" : 0 }}>
            {pending.map((p, i) => (
              <div className="row" key={i}>
                <span><b>{p.platform}</b> <span className="mut">{p.user || p.user_id || ""}</span> <span className="pill mono">{p.code}</span></span>
                <Button sm icon="check" onClick={() => approve(p.platform, p.code)}>Approve</Button>
              </div>
            ))}
          </div>
        </Card>
        <Card title="Authorized" pad={false}>
          {!authorized.length && <Empty small>No authorized users yet.</Empty>}
          <div style={{ padding: authorized.length ? "2px 14px 6px" : 0 }}>
            {authorized.map((u, i) => (
              <div className="row" key={i}>
                <span><b>{u.platform}</b> <span className="mut">{u.user || u.user_id || u.name || ""}</span></span>
                <Button variant="danger" sm onClick={() => revoke(u.platform, u.user_id || u.user || "")}>Revoke</Button>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
