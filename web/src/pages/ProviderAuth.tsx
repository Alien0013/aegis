import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { del } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Loading, MetricStrip, PageHeader, toast } from "../components/ui";

interface ProviderAuthRow {
  name: string;
  display_name?: string;
  model?: string;
  api_mode?: string;
  auth?: { available?: boolean; source?: string; detail?: string };
  auth_methods?: string[];
  auth_scheme?: string;
  env_vars?: string[];
  missing_env_vars?: string[];
  oauth?: boolean;
  oauth_status?: string;
  credential_pool?: { total?: number; ready?: number; benched?: number; strategy?: string };
  capability_summary?: string;
  suggested_action?: string;
  ready?: boolean;
}

interface ProviderAuthPayload {
  active?: ProviderAuthRow | null;
  providers?: ProviderAuthRow[];
  oauth_catalog?: Array<{ name?: string; display_name?: string; status?: string }>;
}

function providerStatus(row: ProviderAuthRow): { label: string; tone?: "success" | "warning" | "danger" | "neutral" } {
  if (row.ready) return { label: "connected", tone: "success" };
  if (row.missing_env_vars?.length) return { label: "missing", tone: "warning" };
  if (row.oauth && row.oauth_status && row.oauth_status !== "configured") return { label: row.oauth_status, tone: "warning" };
  return { label: "available", tone: row.auth?.available ? "success" : "neutral" };
}

export function ProviderAuth() {
  const { data, loading, error, reload } = useApi<ProviderAuthPayload>("provider-auth");
  const [busy, setBusy] = useState("");
  const rows = data?.providers || [];
  const connected = rows.filter((row) => row.ready || row.auth?.available).length;
  const missing = rows.filter((row) => !!row.missing_env_vars?.length).length;
  const oauth = rows.filter((row) => row.oauth).length;

  const activeName = data?.active?.display_name || data?.active?.name || "";
  const sortedRows = useMemo(() => {
    return [...rows].sort((a, b) => {
      const ar = a.ready || a.auth?.available ? 0 : 1;
      const br = b.ready || b.auth?.available ? 0 : 1;
      return ar - br || (a.display_name || a.name).localeCompare(b.display_name || b.name);
    });
  }, [rows]);

  async function disconnect(row: ProviderAuthRow) {
    setBusy(row.name);
    try {
      const res = await del<{ ok?: boolean; removed_env?: string[]; error?: string }>(
        `provider-auth/${encodeURIComponent(row.name)}`,
      );
      if (res.ok === false) toast(res.error || "Disconnect failed", "err");
      else toast(res.removed_env?.length ? `Removed ${res.removed_env.length} secret(s)` : "Account disconnected");
      reload();
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <PageHeader title="Accounts" sub={activeName ? `Active: ${activeName}` : "Provider authentication"} />
      {error && <Card><Empty icon="alert">Couldn't load - {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <MetricStrip items={[
            { label: "connected", value: connected },
            { label: "missing", value: missing },
            { label: "oauth", value: oauth },
            { label: "providers", value: rows.length },
          ]} />

          <div className="grid gap-[var(--gap)] md:grid-cols-2 xl:grid-cols-3">
            {sortedRows.map((row) => {
              const status = providerStatus(row);
              const removable = !!row.env_vars?.length || !!row.auth?.available || row.ready;
              return (
                <Card key={row.name} pad={false}>
                  <div className="border-b border-border p-[var(--pad)]">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate font-mono text-base font-semibold text-text">
                          {row.display_name || row.name}
                        </div>
                        <div className="truncate font-mono text-xs text-faint">{row.name}</div>
                      </div>
                      <Badge tone={status.tone}>{status.label}</Badge>
                    </div>
                  </div>
                  <div className="space-y-3 p-[var(--pad)]">
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <div className="uppercase tracking-wide text-faint">Model</div>
                        <div className="truncate font-mono text-text">{row.model || "-"}</div>
                      </div>
                      <div>
                        <div className="uppercase tracking-wide text-faint">Mode</div>
                        <div className="truncate font-mono text-text">{row.api_mode || row.auth_scheme || "-"}</div>
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-1.5">
                      {(row.auth_methods || []).slice(0, 4).map((method) => (
                        <Badge key={method} tone="neutral">{method}</Badge>
                      ))}
                      {row.credential_pool?.total ? (
                        <Badge tone="info">{row.credential_pool.ready || 0}/{row.credential_pool.total} keys</Badge>
                      ) : null}
                    </div>

                    {!!row.missing_env_vars?.length && (
                      <div className="flex flex-wrap gap-1.5">
                        {row.missing_env_vars.map((key) => (
                          <Link
                            key={key}
                            to={`/env?key=${encodeURIComponent(key)}`}
                            className="inline-flex"
                            title={`Set ${key}`}
                          >
                            <Badge tone="warning">{key}</Badge>
                          </Link>
                        ))}
                      </div>
                    )}

                    {row.capability_summary && (
                      <div className="line-clamp-2 text-xs text-dim">{row.capability_summary}</div>
                    )}

                    <div className="flex flex-wrap gap-2">
                      <Button
                        sm
                        icon="trash"
                        disabled={!removable || busy === row.name}
                        onClick={() => disconnect(row)}
                      >
                        Disconnect
                      </Button>
                      {row.suggested_action === "codex_login" && <Badge tone="info">codex login</Badge>}
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}
