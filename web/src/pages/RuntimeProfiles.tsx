import { useState } from "react";
import { Link } from "react-router-dom";
import { del, post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Badge, Button, Card, Empty, Field, Input, Loading, PageHeader, Toggle, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface RuntimeProfile {
  name: string;
  active: boolean;
  path: string;
  default?: boolean;
  model?: string;
  provider?: string;
  skills?: number;
  memories?: number;
  cron_jobs?: number;
}
interface Payload { active?: string; profiles?: RuntimeProfile[] }

export function RuntimeProfiles() {
  const { data, loading, error, reload } = useApi<Payload>("runtime-profiles");

  async function activate(name: string) {
    try {
      await post(`runtime-profiles/${encodeURIComponent(name)}/activate`, {});
      toast(`Active profile: ${name}`);
      reload();
    } catch (e) {
      toast(String(e), "err");
    }
  }

  async function remove(row: RuntimeProfile) {
    if (row.default) return;
    if (!window.confirm(`Delete runtime profile "${row.name}"?`)) return;
    try {
      await del(`runtime-profiles/${encodeURIComponent(row.name)}`);
      toast("Profile deleted");
      reload();
    } catch (e) {
      toast(String(e), "err");
    }
  }

  const rows = data?.profiles || [];

  return (
    <>
      <PageHeader
        title="Profiles"
        sub={data?.active ? `active: ${data.active}` : "Isolated runtime homes"}
        actions={<Link to="/profiles/new"><Button variant="primary" icon="plus">New profile</Button></Link>}
      />
      {error && <Card><Empty icon="alert">Couldn't load - {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="grid gap-[var(--gap)] md:grid-cols-2 xl:grid-cols-3">
          {!rows.length && <Card><Empty icon="profiles">No runtime profiles.</Empty></Card>}
          {rows.map((row) => (
            <Card
              key={row.name}
              title={<span className="font-mono">{row.name}</span>}
              sub={row.path}
              actions={<Toggle on={row.active} disabled={row.active} onChange={() => activate(row.name)} />}
              className={row.active ? "border-primary/50" : undefined}
            >
              <div className="grid grid-cols-3 gap-2 text-center">
                <Metric label="skills" value={row.skills || 0} />
                <Metric label="memory" value={row.memories || 0} />
                <Metric label="cron" value={row.cron_jobs || 0} />
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {row.default && <Badge tone="neutral">default</Badge>}
                {row.active && <Badge status="active">active</Badge>}
                {row.provider && <Badge tone="info">{row.provider}</Badge>}
                {row.model && <Badge tone="neutral">{row.model}</Badge>}
              </div>
              <div className="mt-3 flex justify-end">
                {!row.default && (
                  <button onClick={() => remove(row)} className="text-faint hover:text-danger" title="Delete">
                    <Icon name="trash" size={15} />
                  </button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}

export function RuntimeProfileNew() {
  const profilesQ = useApi<Payload>("runtime-profiles");
  const [name, setName] = useState("");
  const [cloneFrom, setCloneFrom] = useState("");
  const [clone, setClone] = useState(false);
  const [cloneAll, setCloneAll] = useState(false);
  const [activate, setActivate] = useState(true);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await post("runtime-profiles", {
        name: name.trim(),
        clone_from: cloneFrom || undefined,
        clone,
        clone_all: cloneAll,
        activate,
      });
      toast("Profile created");
      window.location.hash = "#/profiles";
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  const profiles = profilesQ.data?.profiles || [];

  return (
    <>
      <PageHeader title="New Profile" sub="Create an isolated runtime home" actions={<Link to="/profiles"><Button icon="chevronRight">Profiles</Button></Link>} />
      <Card title="Profile settings">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="research" /></Field>
          <Field label="Clone from">
            <select value={cloneFrom} onChange={(e) => setCloneFrom(e.target.value)}
              className="w-full rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-1.5 text-sm text-text outline-none">
              <option value="">Active profile</option>
              {profiles.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
            </select>
          </Field>
        </div>
        <div className="mt-4 space-y-3">
          <SwitchRow label="Clone config, memory, and skills" checked={clone || !!cloneFrom || cloneAll} disabled={!!cloneFrom || cloneAll} onChange={setClone} />
          <SwitchRow label="Clone full profile state except history/logs" checked={cloneAll} onChange={setCloneAll} />
          <SwitchRow label="Activate after creation" checked={activate} onChange={setActivate} />
        </div>
        <div className="mt-4">
          <Button variant="primary" icon="check" disabled={busy || !name.trim()} onClick={save}>Create profile</Button>
        </div>
      </Card>
    </>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[var(--radius)] border border-border bg-surface-2 px-2 py-2">
      <div className="text-lg font-semibold tabular-nums text-text">{value}</div>
      <div className="text-[10px] uppercase text-faint">{label}</div>
    </div>
  );
}

function SwitchRow({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled?: boolean; onChange: (value: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-2">
      <span className="text-sm text-dim">{label}</span>
      <Toggle on={checked} disabled={disabled} onChange={onChange} />
    </div>
  );
}
