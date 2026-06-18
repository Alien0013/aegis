import { useEffect, useState } from "react";
import { useApi } from "../lib/useApi";
import { ago } from "../lib/format";
import { Button, Card, Empty, Input, Loading, PageHeader, Stat, toast } from "../components/ui";
import { desktop, isDesktop } from "../lib/desktop";
import type { DesktopConnection, DesktopUpdaterStatus } from "../lib/desktop";

interface SysInfo {
  version?: string; python?: string; platform?: string; aegis_home?: string;
  disk_free_gb?: number; disk_total_gb?: number;
  checkpoints?: { id: string; label?: string; at?: string }[];
}

export function System() {
  const { data, loading, error } = useApi<SysInfo>("system");
  const stats = useApi<Record<string, unknown>>("system/stats");
  const [desktopConnection, setDesktopConnection] = useState<DesktopConnection | null>(null);
  const [projectDir, setProjectDir] = useState("");
  const [savingProjectDir, setSavingProjectDir] = useState(false);
  const [checkingUpdates, setCheckingUpdates] = useState(false);

  function applyDesktopSnapshot(connection?: DesktopConnection | null, updater?: DesktopUpdaterStatus | null) {
    setDesktopConnection((current) => {
      const base = connection || current || {};
      const desktopData = {
        ...(current?.desktop || {}),
        ...(connection?.desktop || {}),
      };
      if (updater) desktopData.updater = updater;
      return {
        ...(current || {}),
        ...(base || {}),
        desktop: desktopData,
      };
    });
  }

  useEffect(() => {
    let cancelled = false;
    if (!isDesktop || !desktop?.getConnection) return;
    desktop.getConnection()
      .then((connection) => {
        if (!cancelled) {
          applyDesktopSnapshot(connection);
          setProjectDir(connection.settings?.defaultProjectDir || "");
        }
      })
      .catch(() => { if (!cancelled) setDesktopConnection(null); });
    return () => { cancelled = true; };
  }, []);

  const updater = desktopConnection?.desktop?.updater;

  useEffect(() => {
    const active = updater?.checking || ["checking", "available", "downloading"].includes(updater?.stage || "");
    if (!isDesktop || !desktop?.getUpdateStatus || !active) return;
    const getUpdateStatus = desktop.getUpdateStatus;
    let cancelled = false;
    const poll = () => {
      getUpdateStatus()
        .then((status) => { if (!cancelled) applyDesktopSnapshot(null, status); })
        .catch(() => undefined);
    };
    const timer = window.setInterval(poll, 1000);
    poll();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [updater?.checking, updater?.stage]);

  async function checkDesktopUpdates() {
    if (!desktop?.checkForUpdates) return;
    setCheckingUpdates(true);
    try {
      const updater = await desktop.checkForUpdates();
      const connection = await desktop.getConnection?.();
      applyDesktopSnapshot(connection, updater);
    } finally {
      setCheckingUpdates(false);
    }
  }

  async function refreshDesktopConnection() {
    const connection = await desktop?.getConnection?.();
    applyDesktopSnapshot(connection);
    setProjectDir(connection?.settings?.defaultProjectDir || "");
    return connection;
  }

  async function saveProjectDir(next = projectDir) {
    if (!desktop?.setDefaultProjectDir) return;
    setSavingProjectDir(true);
    try {
      const res = await desktop.setDefaultProjectDir(next);
      const connection = await refreshDesktopConnection();
      setProjectDir(res.settings?.defaultProjectDir || connection?.settings?.defaultProjectDir || "");
      toast(next.trim() ? "Desktop project directory saved" : "Desktop project directory cleared");
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setSavingProjectDir(false);
    }
  }

  async function chooseProjectDir() {
    if (!desktop?.chooseProjectDir) return;
    setSavingProjectDir(true);
    try {
      const res = await desktop.chooseProjectDir();
      const connection = await refreshDesktopConnection();
      if (!res.cancelled) {
        setProjectDir(res.settings?.defaultProjectDir || connection?.settings?.defaultProjectDir || "");
        toast("Desktop project directory saved");
      }
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setSavingProjectDir(false);
    }
  }

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
          {isDesktop && (
            <Card
              title="Desktop backend"
              actions={
                desktop?.checkForUpdates ? (
                  <Button sm icon="refresh" disabled={checkingUpdates || updater?.checking} onClick={checkDesktopUpdates}>
                    Check for updates
                  </Button>
                ) : undefined
              }
            >
              <dl className="grid gap-y-2 text-sm sm:grid-cols-2">
                <Row k="Mode" v={desktopConnection?.mode || "local"} />
                <Row k="Backend" v={desktopConnection?.backend?.running ? "running" : "offline"} />
                <Row k="Updater" v={updater?.stage} />
                <Row k="Update note" v={updater?.message || updater?.error} />
                <Row k="Update version" v={updater?.version} />
                <Row k="Last checked" v={updater?.lastCheckedAt ? ago(updater.lastCheckedAt) : ""} />
                <Row k="PID" v={desktopConnection?.backend?.pid ? String(desktopConnection.backend.pid) : ""} />
                <Row k="Port" v={desktopConnection?.backend?.port ? String(desktopConnection.backend.port) : ""} />
                <Row k="Uptime" v={formatDuration(desktopConnection?.backend?.uptimeMs || 0)} />
                <Row k="Restarts" v={`${desktopConnection?.backend?.crashRestarts ?? 0}/${desktopConnection?.backend?.maxCrashRestarts ?? 0}`} />
                <Row k="Base URL" v={desktopConnection?.baseUrl} mono />
                <Row k="Command" v={desktopConnection?.backend?.command} mono />
                <Row k="AEGIS home" v={desktopConnection?.backend?.env?.AEGIS_HOME} mono />
                <Row k="Terminal cwd" v={desktopConnection?.backend?.env?.TERMINAL_CWD} mono />
                <Row k="Cwd source" v={desktopConnection?.backend?.cwdSource} />
                <Row k="Logs" v={desktopConnection?.backend?.logPath} mono />
                <Row k="Settings" v={desktopConnection?.settings?.settingsPath} mono />
              </dl>
              <div className="mt-4 border-t border-border pt-4">
                <div className="mb-2">
                  <div className="font-mono text-xs font-semibold uppercase tracking-wide text-dim">Default project directory</div>
                  <p className="mt-1 text-xs text-faint">
                    Used for desktop backend launches when no TERMINAL_CWD override is present. Restart backend to apply changes.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Input
                    className="min-w-72 flex-1"
                    value={projectDir}
                    placeholder={desktopConnection?.backend?.env?.TERMINAL_CWD || "/workspace/project"}
                    onChange={(event) => setProjectDir(event.target.value)}
                    onKeyDown={(event) => event.key === "Enter" && saveProjectDir()}
                  />
                  {desktop?.chooseProjectDir && <Button icon="files" disabled={savingProjectDir} onClick={chooseProjectDir}>Choose</Button>}
                  <Button variant="primary" icon="check" disabled={savingProjectDir} onClick={() => saveProjectDir()}>Save</Button>
                  <Button variant="ghost" icon="trash" disabled={savingProjectDir || !projectDir} onClick={() => saveProjectDir("")}>Clear</Button>
                  <Button icon="refresh" onClick={() => desktop?.restartBackend()}>Restart</Button>
                </div>
                {desktopConnection?.settings?.explicitLaunchCwd && (
                  <p className="mt-2 text-xs text-warning">TERMINAL_CWD was set when the desktop app launched, so it overrides this preference for this run.</p>
                )}
              </div>
            </Card>
          )}
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

function formatDuration(ms: number) {
  if (!ms) return "";
  const seconds = Math.max(1, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins ? `${hours}h ${mins}m` : `${hours}h`;
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
