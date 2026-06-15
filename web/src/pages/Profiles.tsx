// Profiles — personality/soul profiles (workspace personalities/*.md). List,
// create additional profiles, edit the soul markdown, activate/clear, copy the
// CLI activation command, and delete. Backed by the /api/profiles CRUD API.

import { useState } from "react";
import { api, post, patch, del } from "../lib/api";
import { useApi } from "../lib/useApi";
import { PageHeader, Spinner, toast } from "../components/ui";
import { Icon } from "../components/icons";
import { cn } from "../lib/cn";

interface ProfileRow { name: string; active: boolean; path?: string }
interface ProfilesPayload { active?: string; profiles?: ProfileRow[]; path?: string }

export function Profiles() {
  const { data, loading, error, reload } = useApi<ProfilesPayload>("profiles");
  const [editing, setEditing] = useState<{ name: string; content: string } | null>(null);
  const [creating, setCreating] = useState(false);
  const active = data?.active || "";
  const profiles = data?.profiles || [];

  async function activate(name: string) {
    try { await post("profiles", { name }); toast(name ? `Activated ${name}` : "Cleared profile"); reload(); }
    catch (e) { toast(String(e), "err"); }
  }
  async function remove(name: string) {
    if (!confirm(`Delete profile "${name}"?`)) return;
    try { await del(`profiles/${encodeURIComponent(name)}`); toast(`Deleted ${name}`); reload(); }
    catch (e) { toast(String(e), "err"); }
  }
  async function edit(name: string) {
    try { const d = await api<{ content?: string }>(`profiles/${encodeURIComponent(name)}`); setEditing({ name, content: d.content || "" }); }
    catch (e) { toast(String(e), "err"); }
  }
  function copyCli(name: string) {
    navigator.clipboard?.writeText(`aegis config set agent.personality ${name}`);
    toast("Copied CLI command");
  }

  return (
    <>
      <PageHeader title="Profiles"
        sub={data?.path || "Personality / soul profiles"}
        actions={
          <div className="flex items-center gap-2">
            {active && (
              <button onClick={() => activate("")} className="rounded-[var(--radius)] border border-border px-2.5 py-1.5 text-xs text-dim hover:text-text">
                Clear active
              </button>
            )}
            <button onClick={() => setCreating(true)}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-primary px-3 py-1.5 text-sm font-medium text-primary-fg hover:opacity-90">
              <Icon name="plus" size={14} /> New profile
            </button>
          </div>
        } />

      {active && (
        <div className="mb-[var(--gap)] flex items-center gap-2 rounded-[var(--radius)] border border-success/30 bg-success/10 px-3 py-2 text-sm text-success">
          <Icon name="check" size={14} /> Active profile: <span className="font-mono">{active}</span>
        </div>
      )}

      {error && <div className="rounded-[var(--radius)] border border-danger/40 bg-danger/10 p-3 text-sm text-danger">Couldn't load — {error}</div>}
      {loading && !data && <div className="flex justify-center py-12"><Spinner size={20} /></div>}

      {data && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {profiles.length === 0 && (
            <div className="col-span-full rounded-[calc(var(--radius)+2px)] border border-border bg-surface py-10 text-center text-sm text-faint">
              No profiles yet. Create one to give the agent a personality.
            </div>
          )}
          {profiles.map((p) => (
            <div key={p.name} className={cn("flex flex-col rounded-[calc(var(--radius)+2px)] border bg-surface p-[var(--pad)]",
              p.active ? "border-primary/50" : "border-border")}>
              <div className="flex items-center gap-2">
                <Icon name="profiles" size={15} className="text-primary" />
                <span className="font-mono text-sm text-text">{p.name}</span>
                {p.active && <span className="ml-auto rounded-full border border-success/30 bg-success/15 px-2 py-0.5 text-[10px] text-success">active</span>}
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                {p.active
                  ? <button onClick={() => activate("")} className="rounded-[var(--radius)] border border-border px-2 py-1 text-xs text-dim hover:text-text">Deactivate</button>
                  : <button onClick={() => activate(p.name)} className="rounded-[var(--radius)] bg-primary px-2 py-1 text-xs font-medium text-primary-fg hover:opacity-90">Activate</button>}
                <button onClick={() => edit(p.name)} className="inline-flex items-center gap-1 rounded-[var(--radius)] border border-border px-2 py-1 text-xs text-dim hover:text-text">
                  <Icon name="config" size={12} /> Edit soul
                </button>
                <button onClick={() => copyCli(p.name)} title="Copy CLI activation command" className="rounded-[var(--radius)] border border-border px-2 py-1 text-xs text-dim hover:text-text">
                  <Icon name="terminal" size={12} />
                </button>
                <button onClick={() => remove(p.name)} title="Delete" className="ml-auto rounded-[var(--radius)] border border-border px-2 py-1 text-xs text-danger hover:bg-danger/10">
                  <Icon name="trash" size={12} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {creating && <ProfileEditor title="New profile" onClose={() => setCreating(false)} onSaved={() => { setCreating(false); reload(); }} />}
      {editing && <ProfileEditor title={`Edit ${editing.name}`} name={editing.name} initial={editing.content}
        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
    </>
  );
}

function ProfileEditor({ title, name, initial, onClose, onSaved }: {
  title: string; name?: string; initial?: string; onClose: () => void; onSaved: () => void;
}) {
  const editMode = !!name;
  const [pname, setPname] = useState(name ?? "");
  const [content, setContent] = useState(initial ?? "");
  const [activate, setActivate] = useState(false);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      if (editMode) {
        await patch(`profiles/${encodeURIComponent(name!)}`, { content });
      } else {
        const n = pname.trim();
        if (!n) { toast("Name required", "err"); setSaving(false); return; }
        await post("profiles", { name: n, content: content || `# ${n}\n\n`, activate });
      }
      toast("Saved"); onSaved();
    } catch (e) { toast(String(e), "err"); }
    finally { setSaving(false); }
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center bg-black/50 pt-[8vh] backdrop-blur-sm" onMouseDown={onClose}>
      <div className="animate-fade-in w-full max-w-2xl rounded-[calc(var(--radius)+4px)] border border-border bg-surface p-4 shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text">{title}</h2>
          <button onClick={onClose} className="text-faint hover:text-text"><Icon name="x" size={16} /></button>
        </div>
        {!editMode && (
          <input value={pname} onChange={(e) => setPname(e.target.value)} placeholder="profile-name (lowercase-with-hyphens)"
            className="mb-2 w-full rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-2 text-sm text-text outline-none placeholder:text-faint" />
        )}
        <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={16}
          placeholder={"# Personality\n\nDescribe how the agent should think, speak, and behave…"}
          className="scroll-thin w-full resize-none rounded-[var(--radius)] border border-border bg-surface-2 p-3 font-mono text-xs text-text outline-none placeholder:text-faint" />
        <div className="mt-3 flex items-center justify-between">
          {!editMode
            ? <label className="flex items-center gap-1.5 text-xs text-dim"><input type="checkbox" checked={activate} onChange={(e) => setActivate(e.target.checked)} /> Activate after creating</label>
            : <span />}
          <div className="flex gap-2">
            <button onClick={onClose} className="rounded-[var(--radius)] border border-border px-3 py-1.5 text-sm text-dim hover:text-text">Cancel</button>
            <button onClick={save} disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-primary px-3 py-1.5 text-sm font-medium text-primary-fg hover:opacity-90 disabled:opacity-50">
              {saving && <Spinner size={13} />} Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
