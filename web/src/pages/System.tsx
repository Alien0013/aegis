import { useApi } from "../lib/useApi";
import { ago } from "../lib/format";
import { Card, Empty, Loading, PageHeader, Stat } from "../components/ui";

interface SysInfo {
  version?: string; python?: string; platform?: string; aegis_home?: string;
  disk_free_gb?: number; disk_total_gb?: number;
  checkpoints?: { id: string; label?: string; at?: string }[];
}

export function System() {
  const { data, loading, error } = useApi<SysInfo>("system");
  const stats = useApi<Record<string, unknown>>("system/stats");

  return (
    <>
      <PageHeader title="System" sub="Host + install facts" />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <div className="grid grid-cols-2 gap-[var(--gap)] md:grid-cols-4">
            <Stat label="Version" value={data.version || "?"} icon="shield" tone="primary" />
            <Stat label="Python" value={data.python || "?"} icon="system" />
            <Stat label="Disk free" value={data.disk_free_gb != null ? `${data.disk_free_gb} GB` : "?"} icon="database" tone="info" />
            <Stat label="Checkpoints" value={(data.checkpoints || []).length} icon="logs" tone="success" />
          </div>
          <Card title="Install">
            <dl className="grid gap-y-2 text-sm sm:grid-cols-2">
              <Row k="Platform" v={data.platform} />
              <Row k="AEGIS home" v={data.aegis_home} mono />
              <Row k="Disk total" v={data.disk_total_gb != null ? `${data.disk_total_gb} GB` : ""} />
              {Object.entries(stats.data || {}).slice(0, 6).map(([k, v]) =>
                typeof v === "object" ? null : <Row key={k} k={k.replace(/_/g, " ")} v={String(v)} />)}
            </dl>
          </Card>
          {!!(data.checkpoints || []).length && (
            <Card title="Recent checkpoints" pad={false}>
              {(data.checkpoints || []).map((c) => (
                <div key={c.id} className="flex items-center justify-between gap-3 border-b border-border px-[var(--pad)] py-2 last:border-0">
                  <span className="min-w-0 truncate text-sm text-text">{c.label || c.id}</span>
                  <span className="shrink-0 text-xs text-faint">{ago(c.at)}</span>
                </div>
              ))}
            </Card>
          )}
        </div>
      )}
    </>
  );
}

function Row({ k, v, mono }: { k: string; v?: string; mono?: boolean }) {
  if (!v) return null;
  return (
    <div className="flex justify-between gap-3 sm:block">
      <dt className="text-xs uppercase tracking-wide text-faint">{k}</dt>
      <dd className={mono ? "font-mono text-sm text-text" : "text-sm text-text"}>{v}</dd>
    </div>
  );
}
