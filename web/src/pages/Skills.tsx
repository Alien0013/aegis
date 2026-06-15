import type { ReactNode } from "react";
import { useMemo, useRef, useState } from "react";
import { api, del, patch, post, put } from "../lib/api";
import { useApi } from "../lib/useApi";
import { cn } from "../lib/cn";
import { Icon } from "../components/icons";
import { Badge, Button, Card, Empty, Field, Input, Loading, PageHeader, Spinner, Toggle, toast } from "../components/ui";

interface SkillRow {
  name: string;
  description: string;
  category: string;
  path: string;
  tier?: number;
  platforms?: string[];
  environments?: string[];
  toolsets?: string[];
  available: boolean;
  unavailable_reason?: string;
  enabled: boolean;
  installed: boolean;
  source?: string;
  installed_at?: string;
  editable: boolean;
}

interface Registry { name: string; kind: string; ref: string }
interface SkillsPayload {
  skills: SkillRow[];
  count: number;
  enabled_count?: number;
  categories?: Record<string, number>;
  taps?: Record<string, string>;
  registries?: Registry[];
}

interface ToolsetRow {
  name: string;
  label: string;
  description?: string;
  enabled: boolean;
  available: boolean;
  configured: boolean;
  tools: string[];
  enabled_tools: string[];
  tool_count: number;
  enabled_count: number;
}

interface BundleRow {
  name: string;
  slug: string;
  description?: string;
  skills: string[];
  instruction?: string;
  path?: string;
}
interface BundlePayload { bundles: BundleRow[] }

interface HubResult {
  name: string;
  description?: string;
  source: string;
  hub: string;
  detail_url?: string;
  installed?: boolean;
}

type Tab = "skills" | "toolsets" | "bundles" | "hub";
type SkillFilter = "all" | "enabled" | "disabled" | "unavailable" | "installed" | "local" | "builtin";

const FILTERS: { key: SkillFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "enabled", label: "Enabled" },
  { key: "disabled", label: "Disabled" },
  { key: "unavailable", label: "Unavailable" },
  { key: "installed", label: "Installed" },
  { key: "local", label: "Local" },
  { key: "builtin", label: "Built in" },
];

export function Skills() {
  const skillsQ = useApi<SkillsPayload>("skills/manage");
  const toolsetsQ = useApi<ToolsetRow[]>("tools/toolsets");
  const bundlesQ = useApi<BundlePayload>("skills/bundles");
  const [tab, setTab] = useState<Tab>("skills");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("All");
  const [filter, setFilter] = useState<SkillFilter>("all");
  const [open, setOpen] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState<SkillRow | null>(null);
  const [creating, setCreating] = useState(false);
  const contentSeq = useRef(0);

  const categories = useMemo(() => {
    const keys = Object.keys(skillsQ.data?.categories || {}).sort((a, b) => a.localeCompare(b));
    return ["All", ...keys];
  }, [skillsQ.data]);

  const skills = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (skillsQ.data?.skills || []).filter((s) => {
      const matchesText = !needle
        || s.name.toLowerCase().includes(needle)
        || (s.description || "").toLowerCase().includes(needle)
        || (s.category || "").toLowerCase().includes(needle);
      const matchesCategory = category === "All" || s.category === category;
      const matchesFilter = filter === "all"
        || (filter === "enabled" && s.enabled && s.available)
        || (filter === "disabled" && !s.enabled)
        || (filter === "unavailable" && !s.available)
        || (filter === "installed" && s.installed)
        || (filter === "local" && s.editable && !s.installed)
        || (filter === "builtin" && !s.editable && !s.installed);
      return matchesText && matchesCategory && matchesFilter;
    });
  }, [skillsQ.data, q, category, filter]);

  async function reloadAll() {
    skillsQ.reload();
    toolsetsQ.reload();
    bundlesQ.reload();
  }

  async function view(name: string) {
    if (open === name) {
      contentSeq.current += 1;
      setOpen("");
      return;
    }
    const seq = ++contentSeq.current;
    setOpen(name);
    setContent("");
    try {
      const d = await api<{ content?: string }>(`skills/${encodeURIComponent(name)}`);
      if (seq === contentSeq.current) setContent(d.content || "");
    } catch (e) {
      if (seq === contentSeq.current) setContent(String(e));
    }
  }

  async function editSkill(s: SkillRow) {
    const seq = ++contentSeq.current;
    setBusy(`edit:${s.name}`);
    try {
      const d = await api<{ content?: string }>(`skills/${encodeURIComponent(s.name)}`);
      if (seq === contentSeq.current) {
        setContent(d.content || "");
        setEditing(s);
      }
    } catch (e) {
      if (seq === contentSeq.current) toast(String(e), "err");
    } finally {
      if (seq === contentSeq.current) setBusy("");
    }
  }

  async function toggleSkill(s: SkillRow) {
    setBusy(`skill:${s.name}`);
    try {
      await put(`skills/${encodeURIComponent(s.name)}/toggle`, { enabled: !s.enabled });
      skillsQ.reload();
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy("");
    }
  }

  async function removeSkill(s: SkillRow) {
    if (!window.confirm(`Remove skill "${s.name}"?`)) return;
    setBusy(`remove:${s.name}`);
    try {
      if (s.installed) await post("skills/marketplace/uninstall", { name: s.name });
      else await del(`skills/${encodeURIComponent(s.name)}`);
      toast(`Removed ${s.name}`);
      skillsQ.reload();
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy("");
    }
  }

  const loading = skillsQ.loading || (tab === "toolsets" && toolsetsQ.loading) || (tab === "bundles" && bundlesQ.loading);
  const error = skillsQ.error || (tab === "toolsets" ? toolsetsQ.error : "") || (tab === "bundles" ? bundlesQ.error : "");
  const enabledCount = skillsQ.data?.enabled_count ?? (skillsQ.data?.skills || []).filter((s) => s.enabled).length;

  return (
    <>
      <PageHeader
        title="Skills"
        sub={skillsQ.data ? `${enabledCount}/${skillsQ.data.count} enabled` : "Reusable procedures and tool groups"}
        actions={<div className="flex gap-2">
          <Button icon="refresh" onClick={reloadAll}>Refresh</Button>
          <Button variant="primary" icon="plus" onClick={() => setCreating(true)}>New skill</Button>
        </div>}
      />

      {error && <Card><Empty icon="alert">Could not load - {error}</Empty></Card>}
      {loading && !skillsQ.data && <Loading />}

      {skillsQ.data && (
        <div className="grid gap-[var(--gap)] xl:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="space-y-[var(--gap)]">
            <Card pad={false}>
              <div className="border-b border-border px-3 py-2 font-mono text-[10px] uppercase tracking-wide text-faint">Filters</div>
              <RailButton active={tab === "skills" && category === "All"} icon="skills" label={`All (${skillsQ.data.count})`} onClick={() => { setTab("skills"); setCategory("All"); }} />
              <RailButton active={tab === "toolsets"} icon="tools" label={`Toolsets (${toolsetsQ.data?.length || 0})`} onClick={() => setTab("toolsets")} />
              <RailButton active={tab === "hub"} icon="download" label="Browse Hub" onClick={() => setTab("hub")} />
              <RailButton active={tab === "bundles"} icon="command" label="Bundles" onClick={() => setTab("bundles")} />
              <div className="border-t border-border px-3 pb-2 pt-3 font-mono text-[10px] uppercase tracking-wide text-faint">Categories</div>
              {categories.filter((c) => c !== "All").map((c) => (
                <button
                  key={c}
                  onClick={() => { setTab("skills"); setCategory(c); }}
                  className={cn(
                    "flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left font-mono text-xs",
                    tab === "skills" && category === c ? "bg-primary text-primary-fg" : "text-dim hover:bg-surface-2 hover:text-text",
                  )}
                >
                  <span className="truncate">{c}</span>
                  <span>{skillsQ.data?.categories?.[c] || 0}</span>
                </button>
              ))}
            </Card>
          </aside>

          <main className="min-w-0 space-y-[var(--gap)]">
            {tab === "skills" && (
              <>
                <Card>
                  <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto]">
                    <div className="flex items-center gap-2 border border-border bg-surface-2 px-2.5">
                      <Icon name="search" size={14} className="text-faint" />
                      <input
                        value={q}
                        onChange={(e) => setQ(e.target.value)}
                        placeholder="Search skills..."
                        className="w-full bg-transparent py-2 font-mono text-sm text-text outline-none placeholder:text-faint"
                      />
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {FILTERS.map((f) => (
                        <button
                          key={f.key}
                          onClick={() => setFilter(f.key)}
                          className={cn(
                            "border px-2.5 py-1 font-mono text-xs transition",
                            filter === f.key ? "border-primary bg-primary text-primary-fg" : "border-border text-dim hover:bg-surface-2 hover:text-text",
                          )}
                        >
                          {f.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </Card>

                <Card pad={false}>
                  <div className="flex items-center justify-between gap-3 border-b border-border px-[var(--pad)] py-3">
                    <div>
                      <div className="font-mono text-base font-semibold text-text">{category === "All" ? "All" : category}</div>
                      <div className="text-xs text-faint">{skills.length} visible / {enabledCount} enabled</div>
                    </div>
                    <Badge tone="success">{enabledCount}/{skillsQ.data.count} enabled</Badge>
                  </div>
                  {!skills.length && <Empty icon="skills">No skills match.</Empty>}
                  {skills.map((s) => (
                    <div key={s.name} className="border-b border-border last:border-0">
                      <div className="grid gap-3 px-[var(--pad)] py-3 hover:bg-surface-2/35 md:grid-cols-[auto_minmax(0,1fr)_auto]">
                        <Toggle on={s.enabled} disabled={busy === `skill:${s.name}`} onChange={() => toggleSkill(s)} />
                        <button onClick={() => view(s.name)} className="min-w-0 text-left">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-sm text-text">{s.name}</span>
                            <Badge tone="neutral">{s.category || "General"}</Badge>
                            {s.installed && <Badge tone="info">installed</Badge>}
                            {s.editable && !s.installed && <Badge tone="neutral">local</Badge>}
                            {!s.editable && !s.installed && <Badge tone="neutral">built in</Badge>}
                            {!s.available && <Badge tone={s.enabled ? "warning" : "neutral"}>{s.enabled ? "gated" : "disabled"}</Badge>}
                          </div>
                          <div className="mt-1 line-clamp-2 text-xs text-faint">
                            {s.unavailable_reason || s.description || "-"}
                          </div>
                          {!![...(s.platforms || []), ...(s.environments || []), ...(s.toolsets || [])].length && (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {(s.platforms || []).map((x) => <MiniTag key={`p:${x}`}>platform:{x}</MiniTag>)}
                              {(s.environments || []).map((x) => <MiniTag key={`e:${x}`}>env:{x}</MiniTag>)}
                              {(s.toolsets || []).map((x) => <MiniTag key={`t:${x}`}>toolset:{x}</MiniTag>)}
                            </div>
                          )}
                        </button>
                        <div className="flex shrink-0 items-center gap-2">
                          <button onClick={() => view(s.name)} className="grid h-8 w-8 place-items-center border border-border text-faint hover:text-primary" title="View">
                            <Icon name={open === s.name ? "chevronDown" : "chevronRight"} size={14} />
                          </button>
                          {s.editable && (
                            <Button sm variant="ghost" icon="config" onClick={() => editSkill(s)} disabled={busy === `edit:${s.name}`}>Edit</Button>
                          )}
                          {(s.installed || s.editable) && (
                            <button onClick={() => removeSkill(s)} title="Remove" className="grid h-8 w-8 place-items-center border border-border text-faint hover:text-danger">
                              <Icon name="trash" size={15} />
                            </button>
                          )}
                        </div>
                      </div>
                      {open === s.name && (
                        <pre className="scroll-thin max-h-96 overflow-auto whitespace-pre-wrap break-words border-t border-border bg-bg/45 px-[var(--pad)] py-3 font-mono text-xs text-dim">
                          {content || "Loading..."}
                        </pre>
                      )}
                    </div>
                  ))}
                </Card>
              </>
            )}

            {tab === "toolsets" && (
              <ToolsetsTab data={toolsetsQ.data || []} loading={toolsetsQ.loading} reload={toolsetsQ.reload} />
            )}

            {tab === "bundles" && (
              <BundlesTab data={bundlesQ.data?.bundles || []} reload={reloadAll} />
            )}

            {tab === "hub" && (
              <BrowseHub registries={skillsQ.data.registries || []} taps={skillsQ.data.taps || {}} onChange={reloadAll} />
            )}
          </main>
        </div>
      )}

      {creating && (
        <SkillEditor title="New skill" onClose={() => setCreating(false)} onSaved={() => { setCreating(false); skillsQ.reload(); }} />
      )}
      {editing && (
        <SkillEditor
          title={`Edit ${editing.name}`}
          skill={editing}
          initial={content}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); skillsQ.reload(); }}
        />
      )}
    </>
  );
}

function ToolsetsTab({ data, loading, reload }: { data: ToolsetRow[]; loading: boolean; reload: () => void }) {
  const [busy, setBusy] = useState("");

  async function toggle(row: ToolsetRow) {
    setBusy(row.name);
    try {
      await put(`tools/toolsets/${encodeURIComponent(row.name)}`, { enabled: !row.enabled });
      reload();
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy("");
    }
  }

  if (loading && !data.length) return <Loading />;
  return (
    <div className="grid gap-[var(--gap)] md:grid-cols-2 xl:grid-cols-3">
      {!data.length && <Card><Empty icon="tools">No toolsets found.</Empty></Card>}
      {data.map((row) => (
        <Card
          key={row.name}
          title={<span className="font-mono">{row.label || row.name}</span>}
          sub={`${row.enabled_count}/${row.tool_count} tools enabled`}
          actions={<Toggle on={row.enabled} disabled={busy === row.name} onChange={() => toggle(row)} />}
        >
          <div className="min-h-10 text-xs text-faint">{row.description || "Tool group"}</div>
          <div className="mt-3 flex flex-wrap gap-1">
            {row.tools.slice(0, 12).map((tool) => (
              <MiniTag key={tool}>{tool}</MiniTag>
            ))}
            {row.tools.length > 12 && <MiniTag>+{row.tools.length - 12}</MiniTag>}
          </div>
          {!row.available && <div className="mt-3"><Badge tone="warning">not available on this host</Badge></div>}
        </Card>
      ))}
    </div>
  );
}

function BundlesTab({ data, reload }: { data: BundleRow[]; reload: () => void }) {
  const [name, setName] = useState("");
  const [members, setMembers] = useState("");
  const [description, setDescription] = useState("");
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState("");

  async function save() {
    setBusy("save");
    try {
      const skills = members.split(",").map((s) => s.trim()).filter(Boolean);
      await post("skills/bundles", { name, skills, description, instruction });
      toast("Bundle saved");
      setName("");
      setMembers("");
      setDescription("");
      setInstruction("");
      reload();
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy("");
    }
  }

  async function remove(slug: string) {
    if (!window.confirm(`Remove bundle "${slug}"?`)) return;
    setBusy(slug);
    try {
      await del(`skills/bundles/${encodeURIComponent(slug)}`);
      toast("Bundle removed");
      reload();
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="space-y-[var(--gap)]">
      <Card title="Bundle" sub="Load several skills together by name or slash command">
        <div className="grid gap-2 md:grid-cols-2">
          <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="frontend-stack" /></Field>
          <Field label="Skills"><Input value={members} onChange={(e) => setMembers(e.target.value)} placeholder="frontend-design, write-tests" /></Field>
          <Field label="Description"><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
          <Field label="Extra guidance"><Input value={instruction} onChange={(e) => setInstruction(e.target.value)} /></Field>
        </div>
        <div className="mt-3">
          <Button variant="primary" icon="check" disabled={busy === "save" || !name.trim() || !members.trim()} onClick={save}>
            Save bundle
          </Button>
        </div>
      </Card>

      <Card title="Saved bundles" pad={false}>
        {!data.length && <Empty icon="command">No bundles yet.</Empty>}
        {data.map((b) => (
          <div key={b.slug} className="flex items-start gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-text">{b.slug}</span>
                <Badge tone="neutral">{b.skills.length} skills</Badge>
              </div>
              {b.description && <div className="text-xs text-faint">{b.description}</div>}
              <div className="mt-1 flex flex-wrap gap-1">
                {b.skills.map((s) => <MiniTag key={s}>{s}</MiniTag>)}
              </div>
            </div>
            <button onClick={() => remove(b.slug)} disabled={busy === b.slug} className="text-faint hover:text-danger" title="Remove">
              <Icon name="trash" size={15} />
            </button>
          </div>
        ))}
      </Card>
    </div>
  );
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
    } catch (e) {
      toast(String(e), "err");
      setResults([]);
    } finally {
      setSearching(false);
    }
  }

  async function install(source: string, label: string) {
    setBusy(source);
    try {
      await post("skills/marketplace/install", { source });
      toast(`Installed ${label}`);
      onChange();
      if (results) run();
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy("");
    }
  }

  async function installHub(hub: string) {
    setBusy(`hub:${hub}`);
    try {
      const r = await post<{ installed?: string[] }>("skills/marketplace/install", { hub });
      toast(`Installed ${r.installed?.length ?? 0} from ${hub}`);
      onChange();
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy("");
    }
  }

  const counts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const r of results || []) m[r.hub] = (m[r.hub] || 0) + 1;
    return m;
  }, [results]);

  return (
    <div className="space-y-[var(--gap)]">
      <Card>
        <div className="flex items-center gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-[var(--radius)] border border-border bg-surface-2 px-2.5">
            <Icon name="search" size={14} className="text-faint" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
              placeholder="Search connected skill sources..."
              className="w-full bg-transparent py-2 text-sm text-text outline-none placeholder:text-faint"
            />
          </div>
          <Button variant="primary" icon={searching ? undefined : "search"} onClick={run} disabled={searching}>
            {searching && <Spinner size={14} />} Search
          </Button>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="text-faint">Connected hubs:</span>
          {registries.map((r) => (
            <span key={`${r.kind}:${r.name}`} title={`${r.kind} - ${r.ref}`} className="rounded-full border border-border bg-surface-2 px-2 py-0.5 text-dim">
              {r.name}{counts[r.name] ? ` - ${counts[r.name]}` : ""}
            </span>
          ))}
          {Object.keys(taps).map((t) => (
            <button
              key={t}
              onClick={() => installHub(t)}
              disabled={busy === `hub:${t}`}
              title={`Install all from ${t}`}
              className="rounded-full border border-primary/40 px-2 py-0.5 text-primary hover:bg-primary/10 disabled:opacity-50"
            >
              {busy === `hub:${t}` ? "installing..." : `+ ${t}`}
            </button>
          ))}
        </div>

        <div className="mt-3 flex items-center gap-2">
          <Input value={direct} onChange={(e) => setDirect(e.target.value)} placeholder="Install from URL, owner/repo, or local path" />
          <Button sm icon="download" disabled={!direct.trim() || busy === direct.trim()} onClick={() => install(direct.trim(), direct.trim())}>
            Install
          </Button>
        </div>
      </Card>

      {results === null && <Card><Empty icon="download">Search to browse installable skills.</Empty></Card>}
      {results?.length === 0 && <Card><Empty icon="search">No results.</Empty></Card>}
      {results?.map((r) => (
        <Card key={`${r.hub}/${r.name}`} pad={false}>
          <div className="flex items-start gap-3 px-[var(--pad)] py-2.5">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-text">{r.name}</span>
                <Badge tone="neutral">{r.hub}</Badge>
                {r.installed && <Badge tone="info">installed</Badge>}
              </div>
              {r.description && <div className="text-xs text-faint">{r.description}</div>}
              <div className="mt-0.5 truncate font-mono text-[11px] text-faint">{r.source}</div>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              {r.detail_url && (
                <a href={r.detail_url} target="_blank" rel="noreferrer" title="Details" className="text-faint hover:text-primary">
                  <Icon name="external" size={15} />
                </a>
              )}
              <Button sm variant="primary" icon="download" disabled={busy === r.source || r.installed} onClick={() => install(r.source, r.name)}>
                {r.installed ? "Installed" : "Install"}
              </Button>
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}

function SkillEditor({ title, skill, initial = "", onClose, onSaved }: {
  title: string;
  skill?: SkillRow;
  initial?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const edit = !!skill;
  const [content, setContent] = useState(initial);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      if (edit) {
        await patch(`skills/${encodeURIComponent(skill.name)}`, { content });
      } else {
        await post("skills", { name: name.trim(), description: description.trim(), body: body.trim() });
      }
      toast("Saved");
      onSaved();
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center bg-black/50 pt-[8vh] backdrop-blur-sm" onMouseDown={onClose}>
      <div className="animate-fade-in w-full max-w-2xl rounded-[calc(var(--radius)+4px)] border border-border bg-surface p-4 shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text">{title}</h2>
          <button onClick={onClose} className="text-faint hover:text-text"><Icon name="x" size={16} /></button>
        </div>
        {edit ? (
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={18}
            className="scroll-thin w-full resize-none rounded-[var(--radius)] border border-border bg-surface-2 p-3 font-mono text-xs text-text outline-none"
          />
        ) : (
          <div className="space-y-2">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="skill-name" />
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="One-line description" />
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={12}
              placeholder="Skill body (markdown instructions)..."
              className="scroll-thin w-full resize-none rounded-[var(--radius)] border border-border bg-surface-2 p-3 font-mono text-xs text-text outline-none placeholder:text-faint"
            />
          </div>
        )}
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon="check" onClick={save} disabled={saving || (!edit && (!name.trim() || !description.trim() || !body.trim()))}>
            Save
          </Button>
        </div>
      </div>
    </div>
  );
}

function RailButton({ active, icon, label, onClick }: {
  active: boolean;
  icon: string;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 px-3 py-2 text-left font-mono text-xs transition",
        active ? "bg-primary text-primary-fg" : "text-dim hover:bg-surface-2 hover:text-text",
      )}
    >
      <Icon name={icon} size={14} />
      <span className="truncate">{label}</span>
    </button>
  );
}

function Tabs({ value, onChange, items }: {
  value: Tab;
  onChange: (value: Tab) => void;
  items: [Tab, string, string][];
}) {
  return (
    <div className="mb-[var(--gap)] flex flex-wrap gap-1 rounded-[var(--radius)] border border-border bg-surface p-1">
      {items.map(([key, label, icon]) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-[calc(var(--radius)-2px)] px-3 py-1.5 text-sm transition",
            value === key ? "bg-primary font-medium text-primary-fg" : "text-dim hover:bg-surface-2 hover:text-text",
          )}
        >
          <Icon name={icon} size={14} /> {label}
        </button>
      ))}
    </div>
  );
}

function MiniTag({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full border border-border bg-surface-2 px-1.5 py-px font-mono text-[10px] text-faint">
      {children}
    </span>
  );
}
