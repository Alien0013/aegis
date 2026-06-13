import { useEffect, useMemo, useState } from "react";
import { api, patch, post } from "../lib/api";
import { Badge, Button, Card, Empty, PageHeader, Toggle, useToast } from "../lib/ui";

const CHANNELS = [
  { id: "telegram", label: "Telegram", needs: ["TELEGRAM_BOT_TOKEN"], probe: true, setup: "Message the bot, then approve the pairing code." },
  { id: "discord", label: "Discord", needs: ["DISCORD_BOT_TOKEN"], probe: true, setup: "Install discord.py when using the Discord adapter." },
  { id: "slack", label: "Slack", needs: ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"], probe: true, setup: "Use Socket Mode with a bot token and app token." },
  { id: "signal", label: "Signal", needs: ["SIGNAL_CLI_ACCOUNT"], setup: "Requires the signal-cli binary and a registered account." },
  { id: "matrix", label: "Matrix", needs: ["MATRIX_HOMESERVER", "MATRIX_USER", "MATRIX_PASSWORD"], setup: "Requires matrix-nio and a Matrix account." },
  { id: "email", label: "Email", needs: ["EMAIL_IMAP_HOST", "EMAIL_SMTP_HOST", "EMAIL_ADDRESS", "EMAIL_PASSWORD"], setup: "Configure IMAP and SMTP for inbound and outbound mail." },
  { id: "webhook", label: "Webhook", needs: [], setup: "POST bridge events to the local webhook endpoint." },
  { id: "ntfy", label: "ntfy", needs: ["NTFY_TOPIC", "NTFY_TOKEN"], setup: "Use ntfy for push notifications and lightweight replies." },
];

export function ChannelsPage() {
  const [pairing, setPairing] = useState<any>(null);
  const [gateway, setGateway] = useState<any>(null);
  const [catalog, setCatalog] = useState<any>(null);
  const [env, setEnv] = useState<any[]>([]);
  const [probe, setProbe] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState("");
  const toast = useToast();

  async function load() {
    const [p, g, c, e] = await Promise.all([api("pairing"), api("gateway/status"), api("gateway/channels/catalog"), api("env")]);
    setPairing(p);
    setGateway(g);
    setCatalog(c);
    setEnv(e.keys || []);
  }

  useEffect(() => { load().catch((e) => toast(String(e), "err")); }, []);
  const pending: any[] = pairing?.pending || pairing?.requests || [];
  const authorized: any[] = pairing?.authorized || pairing?.users || [];
  const configured = new Set(gateway?.channels || []);
  const setKeys = useMemo(() => new Set(env.filter((k) => k.set !== false).map((k) => k.key)), [env]);

  async function approve(platform: string, code: string) { await post("pairing", { action: "approve", platform, code }); toast("Approved", "ok"); await load(); }
  async function revoke(platform: string, user_id: string) { await post("pairing", { action: "revoke", platform, user_id }); toast("Revoked"); await load(); }

  async function toggleChannel(id: string, enabled: boolean) {
    await patch(`gateway/channels/${encodeURIComponent(id)}`, { enabled });
    toast(`${id} ${enabled ? "enabled" : "disabled"}`, "ok");
    await load();
  }

  async function service(action: string) {
    setBusy(action);
    try {
      const r = await post("gateway/service", { action, channels: [...configured] });
      if (r.ok === false) toast(r.message || r.error || `${action} failed`, "err");
      else toast(r.message || `${action} ok`, "ok");
      await load();
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  async function probeChannel(id: string) {
    setBusy(`probe:${id}`);
    try {
      const r = await post(`gateway/channels/${encodeURIComponent(id)}/probe`, {});
      setProbe((prev) => ({ ...prev, [id]: r }));
      toast(r.ok ? `${id} probe passed` : `${id} probe failed`, r.ok ? "ok" : "err");
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  function addKey(key: string) {
    window.location.hash = `#/keys?key=${encodeURIComponent(key)}`;
  }

  const channels = catalog?.channels?.length ? catalog.channels : CHANNELS;

  return (
    <>
      <PageHeader
        title="Channels & Gateway"
        sub={<><Badge status={gateway?.configured ? "ready" : "idle"}>{gateway?.configured ? "configured" : "no channels"}</Badge> <span className="mono">{gateway?.service || "service unknown"}</span></>}
        actions={<span className="actions">
          <Button variant="ghost" onClick={() => service("status")} disabled={!!busy} icon="refresh">Refresh</Button>
          <Button onClick={() => service("install")} disabled={!!busy} icon="check">Install</Button>
          <Button variant="ghost" onClick={() => service("restart")} disabled={!!busy} icon="refresh">Restart</Button>
          <Button variant="danger" onClick={() => service("stop")} disabled={!!busy}>Stop</Button>
        </span>}
      />
      <div className="stack">
        <Card title="Gateway behavior">
          <div className="grid c3">
            <div><div className="lbl">Busy mode</div><b>{gateway?.busy_mode || "queue"}</b></div>
            <div><div className="lbl">Session mode</div><b>{gateway?.session_mode || "per_channel_peer"}</b></div>
            <div><div className="lbl">Delivery queue</div><b>{gateway?.queue_pending || 0}</b> pending</div>
          </div>
        </Card>

        <Card title="Channel setup" pad={false}>
          <div className="channel-grid">
            {channels.map((c: any) => {
              const enabled = configured.has(c.id);
              const needs = c.env_vars || c.needs || [];
              const missing = c.missing_env_vars || needs.filter((k: string) => !setKeys.has(k));
              const result = probe[c.id];
              return (
                <div className="channel-card" key={c.id}>
                  <div className="channel-top">
                    <div><b>{c.label}</b><div className="mut">{c.setup}</div></div>
                    <Toggle checked={enabled} onChange={(v) => toggleChannel(c.id, v)} />
                  </div>
                  <div className="pill-list">
                    {needs.length ? needs.map((k: string) => (
                      <button className={`pill mono key-pill ${setKeys.has(k) ? "" : "warn"}`} key={k} onClick={() => addKey(k)}>{k}</button>
                    )) : <span className="pill">no secret required</span>}
                  </div>
                  <div className="row compact">
                    <Badge status={enabled ? "enabled" : "disabled"}>{enabled ? "enabled" : "off"}</Badge>
                    <Badge status={missing.length ? "missing" : "set"}>{missing.length ? `${missing.length} missing` : "keys set"}</Badge>
                    <Button sm variant="ghost" onClick={() => probeChannel(c.id)} disabled={!!busy}>{c.probe_available || c.probe ? "Probe" : "Check"}</Button>
                  </div>
                  {!!missing.length && <div className="actions" style={{ marginTop: 8 }}>{missing.map((k: string) => <Button key={k} sm variant="ghost" icon="key" onClick={() => addKey(k)}>Set {k}</Button>)}</div>}
                  {result && <div className={`notice ${result.ok ? "ok" : "warn"}`}>{result.detail || result.error}</div>}
                </div>
              );
            })}
          </div>
        </Card>

        <div className="grid c2">
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
      </div>
    </>
  );
}
