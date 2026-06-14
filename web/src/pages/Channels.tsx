import { useApi } from "../lib/useApi";
import { post } from "../lib/api";
import { Badge, Button, Card, Empty, Loading, PageHeader, toast } from "../components/ui";
import { titleCase } from "../lib/format";

interface ChannelRow {
  channel?: string; name?: string; label?: string;
  configured?: boolean; ready?: boolean; enabled?: boolean; active?: boolean;
}
// _gateway_channel_payload shape varies; read defensively.
type CatalogPayload = { channels?: ChannelRow[]; catalog?: ChannelRow[] } & Record<string, unknown>;

export function Channels() {
  const { data, loading, error, reload } = useApi<CatalogPayload>("gateway/channels/catalog");
  const status = useApi<Record<string, unknown>>("gateway/status");

  const rows: ChannelRow[] = (Array.isArray(data?.channels) ? data!.channels
    : Array.isArray(data?.catalog) ? data!.catalog : []) as ChannelRow[];
  const activeChannels = (status.data?.channels as string[]) || [];

  async function probe(ch: string) {
    try {
      const r = await post<{ ok?: boolean; error?: string }>(`gateway/channels/${encodeURIComponent(ch)}/probe`, {});
      toast(r.ok ? "Reachable" : (r.error || "Unreachable"), r.ok ? "ok" : "err");
    } catch (e) { toast(String(e), "err"); }
  }
  async function setActive(channels: string[]) {
    try { await post("gateway/channels", { channels }); toast("Updated"); status.reload(); reload(); }
    catch (e) { toast(String(e), "err"); }
  }

  return (
    <>
      <PageHeader title="Channels" sub="Messaging platforms served by the gateway" />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <Card title="Catalog" sub={activeChannels.length ? `active: ${activeChannels.join(", ")}` : "none active"} pad={false}>
          {!rows.length && <Empty icon="channels">No channel catalog available.</Empty>}
          {rows.map((c) => {
            const id = c.channel || c.name || "";
            const on = activeChannels.includes(id);
            const configured = c.configured ?? c.ready ?? false;
            return (
              <div key={id} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-text">{c.label || titleCase(id)}</span>
                    <Badge status={configured ? "ok" : undefined} tone={configured ? undefined : "neutral"}>
                      {configured ? "configured" : "needs token"}
                    </Badge>
                    {on && <Badge status="active">active</Badge>}
                  </div>
                </div>
                <Button sm variant="ghost" onClick={() => probe(id)}>Probe</Button>
                <Button sm variant={on ? "danger" : "outline"}
                  onClick={() => setActive(on ? activeChannels.filter((x) => x !== id) : [...activeChannels, id])}>
                  {on ? "Disable" : "Enable"}
                </Button>
              </div>
            );
          })}
        </Card>
      )}
    </>
  );
}
