// Skills — installed skills (view / edit / create / uninstall) and a Browse Hub
// that searches multiple connected sources (well-known agentskills.io + the
// official GitHub repos) and installs with one click. Backed by /api/skills/*.

import { useMemo, useState } from "react";
import { api, post, patch, del } from "../lib/api";
import { useApi } from "../lib/useApi";
import { PageHeader, Spinner, toast } from "../components/ui";
import { Icon } from "../components/icons";
import { cn } from "../lib/cn";

interface SkillRow {
  name: string; description: string; path: string; tier?: string;
  available: boolean; unavailable_reason?: string; installed: boolean;
  source?: string; editable: boolean; usage?: Record<string, unknown>;
}
interface Registry { name: string; kind: string; ref: string }
interface SkillsPayload {
  skills: SkillRow[]; count: number; taps?: Record<string, string>; registries?: Registry[];
}
interface HubResult { name: string; description: string; source: string; hub: string; detail_url?: string; installed?: boolean }

export function Skills() {
  const { data, loading, error, reload } = useApi<SkillsPayload>("skills/manage");
  const [tab, setTab] = useState<"installed" | "hub">("installed");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState("");
  const [content, setContent] = useState("");
  const [editing, setEditing] = useState<SkillRow | null>(null);
  const [creating, setCreating] = useState(false);

  const skills = useMemo(() => (data?.skills || []).filter((s) =>
    !q || s.name.toLowerCase().includes(q.toLowerCase()) || (s.description || "").toLowerCase().includes(q.toLowerCase())),
    [data, q]);

  async function view(name: string) {
    if (open === name) { setOpen(""); return; }
    setOpen(name); setContent("");
    try { const d = await api<{ content?: string }>(`skills/${encodeURIComponent(name)}`); setContent(d.content || ""); }
    catch (e) { setContent(String(e)); }
  }
  async function uninstall(s: SkillRow) {
    if (!confirm(`Remove skill "${s.name}"?`)) return;
    try {
      if (s.installed) await post("skills/marketplace/uninstall", { name: s.name });
      else await del(`skills/${encodeURIComponent(s.name)}`);
      toast(`Removed ${s.name}`); reload();
    } catch (e) { toast(String(e), "err"); }
  }

  return (
    <>
      <PageHeader title="Skills" sub={data ? `${data.count} installed` : "Reusable SKILL.md packages"}
        actions={tab === "installed" ? (
          <button onClick={() => setCreating(true)}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-primary px-3 py-1.5 text-sm font-medium text-primary-fg hover:opacity-90">
            <Icon name="plus" size={14} /> New skill
          </button>
        ) : undefined} />

      {/* Prominent tabs: Installed (view/edit/uninstall) vs Browse Hub (search & install). */}
      <div className="mb-[var(--gap)] inline-flex gap-1 rounded-[var(--radius)] border border-border bg-surface p-1">
        <button onClick={() => setTab("installed")}
          className={cn("inline-flex items-center gap-1.5 rounded-[calc(var(--radius)-2px)] px-3 py-1.5 text-sm transition",
            tab === "installed" ? "bg-primary font-medium text-primary-fg" : "text-dim hover:text-text")}>
          <Icon name="skills" size={14} /> Installed{data ? ` · ${data.count}` : ""}
        </button>
        <button onClick={() => setTab("hub")}
          className={cn("inline-flex items-center gap-1.5 rounded-[calc(var(--radius)-2px)] px-3 py-1.5 text-sm transition",
            tab === "hub" ? "bg-primary font-medium text-primary-fg" : "text-dim hover:text-text")}>
          <Icon name="download" size={14} /> Browse Hub — install skills
        </button>
      </div>

      {error &&<div className="rounded-[var(--radius)] border border-danger/40 bg-danger/10 p-3 text-sm text-danger">Couldn't load — {error}</div>}
      {loading && !data && <div className="flex justify-center py-12"><Spinner size={20} /></div>}

      {data && tab === "installed" && (
        <>
          <div className="mb-[var(--gap)] flex items-center gap-2 rounded-[var(--radius)] border border-border bg-surface-2 px-2.5">
            <Icon name="search" size={13} className="text-faint" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter installed skills…"
              className="w-full bg-transparent py-2 text-sm text-text outline-none placeholder:text-faint" />
          </div>
          <div className="overflow-hidden rounded-[calc(var(--radius)+2px)] border border-border bg-surface">
            {skills.length === 0 && <div className="py-10 text-center text-sm text-faint">No skills match.</div>}
            {skills.map((s) => (
              <div key={s.name} className="border-b border-border last:border-0">
                <div className="flex items-start gap-3 px-[var(--pad)] py-2.5 hover:bg-surface-2/40">
                  <button onClick={() => view(s.name)} className="mt-0.5 shrink-0 text-faint hover:text-primary">
                    <Icon name={open === s.name ? "chevronDown" : "chevronRight"} size={14} />
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm text-text">{s.name}</span>
                      {s.installed && <Badge tone="info">installed</Badge>}
                      {s.editable && !s.installed && <Badge tone="neutral">local</Badge>}
                      {!s.editable && !s.installed && <Badge tone="neutral">builtin</Badge>}
                      {!s.available && <Badge tone="warning">unavailable</Badge>}
                    </div>
                    <div className="text-xs text-faint">{s.description || "—"}</div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {s.editable && (
                      <button onClick={() => openEdit(s, setEditing, setContent)} title="Edit"
                        className="rounded-[var(--radius)] border border-border px-2 py-1 text-xs text-dim hover:text-text">
                        <Icon name="config" size={13} />
                      </button>
                    )}
                    {(s.installed || s.editable) && (
                      <button onClick={() => uninstall(s)} title="Uninstall"
                        className="rounded-[var(--radius)] border border-border px-2 py-1 text-xs text-danger hover:bg-danger/10">
                        <Icon name="trash" size={13} />
                      </button>
                    )}
                  </div>
                </div>
                {open === s.name && (
                  <pre className="scroll-thin max-h-96 overflow-auto whitespace-pre-wrap break-words border-t border-border bg-surface-2/40 px-[var(--pad)] py-3 font-mono text-xs text-dim">
                    {content || "Loading…"}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {data && tab === "hub" && <BrowseHub registries={data.registries || []} taps={data.taps || {}} onChange={reload} />}

      {creating && <SkillEditor title="New skill" onClose={() => setCreating(false)} onSaved={() => { setCreating(false); reload(); }} />}
      {editing && <SkillEditor title={`Edit ${editing.name}`} skill={editing} initial={content}
        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
    </>
  );
}

function openEdit(s: SkillRow, setEditing: (s: SkillRow) => void, setContent: (c: string) => void) {
  setContent("");
  api<{ content?: string }>(`skills/${encodeURIComponent(s.name)}`)
    .then((d) => setContent(d.content || ""))
    .catch(() => {});
  setEditing(s);
}

function Badge({ children, tone }: { children: React.ReactNode; tone: "info" | "neutral" | "warning" }) {
  const c = tone === "info" ? "bg-info/15 text-info border-info/30"
    : tone === "warning" ? "bg-warning/15 text-warning border-warning/30"
    : "bg-surface-2 text-dim border-border";
  return <span className={cn("rounded-full border px-1.5 py-px text-[10px]", c)}>{children}</span>;
}

function BrowseHub({ registries, taps, onChange }: { registries: Registry[]; taps: Record<string, string>; onChange: () => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<HubResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [direct, setDirect] = useState("");
  const [busy, setBusy] = useState("");

  async function run() {
    setSearching(true);
    try {
      const d = await api<{ results?: HubResult[] }>(`skills/marketplace/search?q=${encodeURIComponent(q)}`);
      setResults(d.results || []);
    } catch (e) { toast(String(e), "err"); setResults([]); }
    finally { setSearching(false); }
  }
  async function install(source: string, label: string) {
    setBusy(source);
    try { await post("skills/marketplace/install", { source }); toast(`Installed ${label}`); onChange(); if (results) run(); }
    catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }
  async function installHub(hub: string) {
    setBusy(`hub:${hub}`);
    try { const r = await post<{ installed?: string[] }>("skills/marketplace/install", { hub }); toast(`Installed ${r.installed?.length ?? 0} from ${hub}`); onChange(); }
    catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  const counts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const r of results || []) m[r.hub] = (m[r.hub] || 0) + 1;
    return m;
  }, [results]);

  return (
    <>
      <div className="rounded-[calc(var(--radius)+2px)] border border-border bg-surface p-[var(--pad)]">
        <div className="flex items-center gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-[var(--radius)] border border-border bg-surface-2 px-2.5">
            <Icon name="search" size={14} className="text-faint" />
            <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()}
              placeholder="Search the skill hub (GitHub, agentskills, community)…"
              className="w-full bg-transparent py-2 text-sm text-text outline-none placeholder:text-faint" />
          </div>
          <button onClick={run} disabled={searching}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-primary px-3 py-2 text-sm font-medium text-primary-fg hover:opacity-90 disabled:opacity-50">
            {searching ? <Spinner size={14} /> : <Icon name="search" size={14} />} Search
          </button>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="text-faint">Connected hubs:</span>
          {registries.map((r) => (
            <span key={`${r.kind}:${r.name}`} title={`${r.kind} · ${r.ref}`}
              className="rounded-full border border-border bg-surface-2 px-2 py-0.5 text-dim">
              {r.name}{counts[r.name] ? ` · ${counts[r.name]}` : ""}
            </span>
          ))}
          {Object.keys(taps).map((t) => (
            <button key={t} onClick={() => installHub(t)} disabled={busy === `hub:${t}`}
              title={`Install all from ${t} (${taps[t]})`}
              className="rounded-full border border-primary/40 px-2 py-0.5 text-primary hover:bg-primary/10 disabled:opacity-50">
              {busy === `hub:${t}` ? "installing…" : `+ ${t}`}
            </button>
          ))}
        </div>

        <div className="mt-2 flex items-center gap-2">
          <input value={direct} onChange={(e) => setDirect(e.target.value)}
            placeholder="Install from URL / GitHub  (e.g. owner/repo, git:owner/repo/sub, https://…/SKILL.md)"
            className="flex-1 rounded-[var(--radius)] border border-border bg-surface-2 px-2.5 py-1.5 text-xs text-text outline-none placeholder:text-faint" />
          <button onClick={() => direct.trim() && install(direct.trim(), direct.trim())} disabled={!direct.trim() || busy === direct.trim()}
            className="rounded-[var(--radius)] border border-border px-2.5 py-1.5 text-xs text-dim hover:text-text disabled:opacity-50">
            <Icon name="download" size={13} /> Install
          </button>
        </div>
      </div>

      <div className="mt-[var(--gap)] space-y-2">
        {results === null && (
          <div className="rounded-[calc(var(--radius)+2px)] border border-border bg-surface py-12 text-center text-sm text-faint">
            Search the hub above to browse installable skills from the connected sources.
          </div>
        )}
        {results?.length === 0 && (
          <div className="rounded-[calc(var(--radius)+2px)] border border-border bg-surface py-10 text-center text-sm text-faint">No results.</div>
        )}
        {results?.map((r) => (
          <div key={`${r.hub}/${r.name}`} className="flex items-start gap-3 rounded-[calc(var(--radius)+2px)] border border-border bg-surface px-[var(--pad)] py-2.5">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-text">{r.name}</span>
                <span className="rounded-full border border-border bg-surface-2 px-1.5 py-px text-[10px] text-dim">{r.hub}</span>
                {r.installed && <span className="rounded-full border border-info/30 bg-info/15 px-1.5 py-px text-[10px] text-info">installed</span>}
              </div>
              {r.description && <div className="text-xs text-faint">{r.description}</div>}
              <div className="mt-0.5 truncate font-mono text-[11px] text-faint">{r.source}</div>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              {r.detail_url && (
                <a href={r.detail_url} target="_blank" rel="noreferrer" title="Details"
                  className="rounded-[var(--radius)] border border-border px-2 py-1 text-xs text-dim hover:text-text">
                  <Icon name="external" size={13} />
                </a>
              )}
              <button onClick={() => install(r.source, r.name)} disabled={busy === r.source || r.installed}
                className="inline-flex items-center gap-1 rounded-[var(--radius)] bg-primary px-2.5 py-1 text-xs font-medium text-primary-fg hover:opacity-90 disabled:opacity-50">
                {busy === r.source ? <Spinner size={12} /> : <Icon name="download" size={12} />}
                {r.installed ? "installed" : "Install"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function SkillEditor({ title, skill, initial, onClose, onSaved }: {
  title: string; skill?: SkillRow; initial?: string; onClose: () => void; onSaved: () => void;
}) {
  const edit = !!skill;
  const [content, setContent] = useState(initial ?? "");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      if (edit) {
        const text = content || initial || "";
        await patch(`skills/${encodeURIComponent(skill!.name)}`, { content: text });
      } else {
        await post("skills", { name: name.trim(), description: description.trim(), body: body.trim() });
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
        {edit ? (
          <textarea value={content || initial || ""} onChange={(e) => setContent(e.target.value)} rows={18}
            className="scroll-thin w-full resize-none rounded-[var(--radius)] border border-border bg-surface-2 p-3 font-mono text-xs text-text outline-none" />
        ) : (
          <div className="space-y-2">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="skill-name (lowercase-with-hyphens)"
              className="w-full rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-2 text-sm text-text outline-none placeholder:text-faint" />
            <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="One-line description"
              className="w-full rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-2 text-sm text-text outline-none placeholder:text-faint" />
            <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={12} placeholder="Skill body (markdown instructions)…"
              className="scroll-thin w-full resize-none rounded-[var(--radius)] border border-border bg-surface-2 p-3 font-mono text-xs text-text outline-none placeholder:text-faint" />
          </div>
        )}
        <div className="mt-3 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-[var(--radius)] border border-border px-3 py-1.5 text-sm text-dim hover:text-text">Cancel</button>
          <button onClick={save} disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-primary px-3 py-1.5 text-sm font-medium text-primary-fg hover:opacity-90 disabled:opacity-50">
            {saving && <Spinner size={13} />} Save
          </button>
        </div>
      </div>
    </div>
  );
}
