import { useState } from "react";
import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Field, Input, Loading, PageHeader, toast } from "../components/ui";
import { Icon } from "../components/icons";

type PairingPayload = {
  approved?: Record<string, string[]>;
  pending?: Record<string, Record<string, { user_id?: string; ts?: string }>>;
};

export function Pairing() {
  const { data, loading, error, reload } = useApi<PairingPayload>("pairing");
  const [platform, setPlatform] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState("");

  const approved = Object.entries(data?.approved || {});
  const pending = Object.entries(data?.pending || {}).flatMap(([plat, codes]) =>
    Object.entries(codes || {}).map(([pairCode, info]) => ({ platform: plat, code: pairCode, ...info })),
  );

  async function act(body: Record<string, unknown>, label: string) {
    setBusy(label);
    try {
      const r = await post<{ ok?: boolean; error?: string }>("pairing", body);
      if (r.error || r.ok === false) toast(r.error || "Pairing update failed", "err");
      else { toast("Updated"); reload(); }
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <PageHeader title="Pairing" sub="Gateway user approvals" />
      {error && <Card><Empty icon="alert">Couldn't load - {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          <Card title="Approve">
            <div className="grid gap-2 md:grid-cols-[220px_minmax(0,1fr)_auto]">
              <Field label="Platform"><Input value={platform} onChange={(e) => setPlatform(e.target.value)} placeholder="telegram" /></Field>
              <Field label="Code or user id"><Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="ABCD2345" /></Field>
              <div className="flex items-end">
                <Button variant="primary" icon="check" disabled={!platform.trim() || !code.trim() || busy === "approve"}
                  onClick={() => act({ action: "approve", platform: platform.trim(), code: code.trim() }, "approve")}>
                  Approve
                </Button>
              </div>
            </div>
          </Card>

          <Card title="Pending" pad={false}>
            {!pending.length && <Empty icon="channels">No pending pairing codes.</Empty>}
            {pending.map((row) => (
              <div key={`${row.platform}:${row.code}`} className="flex items-center gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm text-text">{row.code}</span>
                    <Badge tone="neutral">{row.platform}</Badge>
                  </div>
                  <div className="truncate text-xs text-faint">{row.user_id || "unknown user"}{row.ts ? ` - ${row.ts}` : ""}</div>
                </div>
                <Button sm variant="primary" icon="check"
                  onClick={() => act({ action: "approve", platform: row.platform, code: row.code }, `${row.platform}:${row.code}`)}>
                  Approve
                </Button>
              </div>
            ))}
          </Card>

          <Card title="Approved" pad={false}>
            {!approved.length && <Empty icon="shield">No approved users.</Empty>}
            {approved.map(([plat, users]) => (
              <div key={plat} className="border-b border-border px-[var(--pad)] py-2.5 last:border-0">
                <div className="mb-2 flex items-center gap-2">
                  <Icon name="channels" size={14} className="text-primary" />
                  <span className="font-mono text-sm text-text">{plat}</span>
                  <Badge tone="neutral">{users.length}</Badge>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {users.map((user) => (
                    <button key={user} onClick={() => act({ action: "revoke", platform: plat, user_id: user }, `revoke:${plat}:${user}`)}
                      className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 px-2 py-0.5 font-mono text-[11px] text-dim hover:border-danger/50 hover:text-danger">
                      {user} <Icon name="x" size={11} />
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </Card>
        </div>
      )}
    </>
  );
}
