import type { ReactNode } from "react";
import { useState } from "react";
import { api, del, patch, post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { ago, compact, dateish } from "../lib/format";
import { Badge, Button, Card, Empty, Field, Input, Loading, MetricStrip, PageHeader, Segmented, Toggle, toast } from "../components/ui";
import { Icon } from "../components/icons";

interface Job {
  id: string;
  name?: string;
  schedule: string;
  prompt: string;
  enabled: boolean;
  next_run?: number | string;
  last_run?: number | string;
  last_status?: string;
  last_error?: string;
  one_shot?: boolean;
  no_agent?: boolean;
  channel?: string;
  context_from?: string[];
  script?: string;
  skills?: string[];
  deliver?: string;
  max_runs?: number;
  model?: string;
  enabled_toolsets?: string[];
  workdir?: string;
  state?: string;
  history?: Array<{ id?: string; status?: string; started_at?: string; created_at?: string }>;
  latest_output?: string;
}
interface JobsPayload { jobs?: Job[] }
interface ServicePayload { service?: string; status?: string }
interface CronPreview {
  ok?: boolean;
  job_id?: string;
  mode?: string;
  due?: boolean;
  next_run?: number | string;
  next_run_iso?: string;
  targets?: string[];
  model?: string;
  enabled_toolsets?: string[];
  resolved_toolsets?: string[];
  disabled_tools?: string[];
  cron_skip_memory?: boolean;
  workdir?: { path?: string; exists?: boolean; is_dir?: boolean };
  script?: { configured?: boolean; path?: string; resolved_path?: string; exists?: boolean; is_file?: boolean };
  validation?: { ok?: boolean; errors?: string[]; warnings?: string[] };
}
interface FormState {
  id: string; name: string; schedule: string; prompt: string; skills: string;
  context_from: string; script: string; deliver: string; channel: string;
  max_runs: string; model: string; enabled_toolsets: string; workdir: string; no_agent: boolean;
}

type Tab = "jobs" | "blueprints";

const EMPTY: FormState = {
  id: "", name: "", schedule: "", prompt: "", skills: "", context_from: "",
  script: "", deliver: "", channel: "", max_runs: "", model: "", enabled_toolsets: "",
  workdir: "", no_agent: false,
};

function csv(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function fromJob(job: Job): FormState {
  return {
    id: job.id,
    name: job.name || "",
    schedule: job.schedule || "",
    prompt: job.prompt || "",
    skills: (job.skills || []).join(", "),
    context_from: (job.context_from || []).join(", "),
    script: job.script || "",
    deliver: job.deliver || "",
    channel: job.channel || "",
    max_runs: job.max_runs ? String(job.max_runs) : "",
    model: job.model || "",
    enabled_toolsets: (job.enabled_toolsets || []).join(", "),
    workdir: job.workdir || "",
    no_agent: !!job.no_agent,
  };
}

export function Cron() {
  const jobsQ = useApi<JobsPayload>("cron/jobs");
  const serviceQ = useApi<ServicePayload>("cron/service");
  const [tab, setTab] = useState<Tab>("jobs");
  const [profile, setProfile] = useState("all");
  const [form, setForm] = useState<FormState>(EMPTY);
  const [editorOpen, setEditorOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [previews, setPreviews] = useState<Record<string, CronPreview>>({});

  const jobs = jobsQ.data?.jobs || [];
  const enabled = jobs.filter((job) => job.enabled).length;
  const failed = jobs.filter((job) => /fail|error/i.test(job.last_status || job.state || "") || job.last_error).length;
  const next = jobs.filter((job) => job.next_run).sort((a, b) => String(a.next_run).localeCompare(String(b.next_run)))[0];

  async function save() {
    if (!form.schedule.trim() || !form.prompt.trim()) return;
    setBusy("save");
    const payload = {
      name: form.name.trim(),
      schedule: form.schedule.trim(),
      prompt: form.prompt.trim(),
      skills: csv(form.skills),
      context_from: csv(form.context_from),
      script: form.script.trim(),
      deliver: form.deliver.trim(),
      channel: form.channel.trim(),
      no_agent: form.no_agent,
      max_runs: Number(form.max_runs || 0),
      model: form.model.trim(),
      enabled_toolsets: csv(form.enabled_toolsets),
      workdir: form.workdir.trim(),
    };
    try {
      const r = form.id
        ? await patch<{ ok?: boolean; error?: string }>(`cron/jobs/${encodeURIComponent(form.id)}`, payload)
        : await post<{ ok?: boolean; error?: string }>("cron/jobs", payload);
      if (r.ok === false) toast(r.error || "Save failed", "err");
      else {
        toast(form.id ? "Schedule updated" : "Schedule created");
        setForm(EMPTY);
        setEditorOpen(false);
        jobsQ.reload();
      }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  async function toggle(job: Job) {
    setBusy(job.id);
    try {
      const r = await patch<{ ok?: boolean; error?: string }>(`cron/jobs/${encodeURIComponent(job.id)}`, { enabled: !job.enabled });
      if (r.ok === false) toast(r.error || "Toggle failed", "err");
      else jobsQ.reload();
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  async function run(job: Job) {
    setBusy(`run:${job.id}`);
    try {
      const r = await post<{ ok?: boolean; error?: string }>(`cron/jobs/${encodeURIComponent(job.id)}/run`, {});
      if (r.ok === false) toast(r.error || "Run failed", "err");
      else { toast("Run started"); jobsQ.reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  async function preview(job: Job) {
    setBusy(`preview:${job.id}`);
    try {
      const r = await api<CronPreview>(`cron/jobs/${encodeURIComponent(job.id)}/preview`);
      setPreviews((prev) => ({ ...prev, [job.id]: r }));
      if (r.ok === false) toast(r.validation?.errors?.[0] || "Preview has validation errors", "err");
      else toast("Dry-run preview ready");
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  async function remove(job: Job) {
    if (!window.confirm(`Delete schedule "${job.name || job.id}"?`)) return;
    setBusy(`delete:${job.id}`);
    try {
      const r = await del<{ ok?: boolean; error?: string }>(`cron/jobs/${encodeURIComponent(job.id)}`);
      if (r.ok === false) toast(r.error || "Delete failed", "err");
      else { toast("Schedule deleted"); jobsQ.reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  async function service(action: string) {
    setBusy(`service:${action}`);
    try {
      const r = await post<{ ok?: boolean; error?: string; message?: string }>("cron/service", { action });
      if (r.ok === false) toast(r.error || r.message || "Service action failed", "err");
      else { toast(r.message || "Service updated"); serviceQ.reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(""); }
  }

  function edit(job?: Job) {
    setForm(job ? fromJob(job) : EMPTY);
    setEditorOpen(true);
  }

  return (
    <>
      <PageHeader
        title="Cron"
        sub="Scheduled autonomous jobs"
        actions={<div className="flex gap-2">
          <Button icon="refresh" onClick={() => { jobsQ.reload(); serviceQ.reload(); }}>Refresh</Button>
          <Button variant="primary" icon="plus" onClick={() => edit()}>New job</Button>
        </div>}
      />

      <Segmented<Tab>
        value={tab}
        onChange={setTab}
        items={[
          { value: "jobs", label: "Jobs", count: jobs.length },
          { value: "blueprints", label: "Blueprints" },
        ]}
      />

      {jobsQ.error && <Card className="mt-[var(--gap)]"><Empty icon="alert">Couldn't load - {jobsQ.error}</Empty></Card>}
      {jobsQ.loading && <Loading />}

      {jobsQ.data && (
        <div className="mt-[var(--gap)] grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0 space-y-[var(--gap)]">
            <MetricStrip items={[
              { label: "scheduled jobs", value: jobs.length },
              { label: "enabled", value: enabled, tone: "success" },
              { label: "attention", value: failed, tone: failed ? "danger" : "neutral" },
              { label: "next", value: next ? dateish(next.next_run) : "none" },
            ]} />

            {tab === "jobs" ? (
              <Card pad={false}>
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-[var(--pad)] py-3">
                  <div>
                    <div className="font-mono text-base font-semibold text-text">Scheduled Jobs ({jobs.length})</div>
                    <div className="text-xs text-faint">Profile scope and runtime overrides are shown inline.</div>
                  </div>
                  <select value={profile} onChange={(e) => setProfile(e.target.value)}
                    className="min-h-9 border border-border bg-surface-2 px-3 font-mono text-xs text-text outline-none">
                    <option value="all">All profiles</option>
                    <option value="agent">Agent jobs</option>
                    <option value="script">Script jobs</option>
                  </select>
                </div>
                {!jobs.length && <Empty icon="cron">No cron jobs configured. Create one above.</Empty>}
                {jobs
                  .filter((job) => profile === "all" || (profile === "script" ? job.no_agent : !job.no_agent))
                  .map((job) => (
                    <JobRow
                      key={job.id}
                      job={job}
                      busy={busy}
                      onToggle={toggle}
                      onRun={run}
                      onPreview={preview}
                      onEdit={edit}
                      onRemove={remove}
                      preview={previews[job.id]}
                    />
                  ))}
              </Card>
            ) : (
              <Card pad={false}>
                <div className="grid gap-px bg-border md:grid-cols-3">
                  <Blueprint title="Daily digest" schedule="@daily" prompt="Summarize open work, recent logs, and next actions." onUse={() => { setForm({ ...EMPTY, name: "Daily digest", schedule: "@daily", prompt: "Summarize open work, recent logs, and next actions." }); setEditorOpen(true); }} />
                  <Blueprint title="Repo sweep" schedule="0 9 * * 1" prompt="Review the workspace for stale tasks and risky changes." onUse={() => { setForm({ ...EMPTY, name: "Repo sweep", schedule: "0 9 * * 1", prompt: "Review the workspace for stale tasks and risky changes." }); setEditorOpen(true); }} />
                  <Blueprint title="Channel check" schedule="30m" prompt="Probe gateway channels and report failures." onUse={() => { setForm({ ...EMPTY, name: "Channel check", schedule: "30m", prompt: "Probe gateway channels and report failures." }); setEditorOpen(true); }} />
                </div>
              </Card>
            )}
          </div>

          <aside className="space-y-[var(--gap)]">
            <Card title="Service" sub={serviceQ.data?.service || "cron worker"}>
              {serviceQ.loading && <Loading />}
              {serviceQ.data && (
                <div className="space-y-3">
                  <Badge status={/active|running/i.test(serviceQ.data.status || "") ? "running" : "pending"}>
                    {serviceQ.data.status || "unknown"}
                  </Badge>
                  <div className="grid grid-cols-2 gap-2">
                    {["install", "start", "restart", "stop"].map((action) => (
                      <Button key={action} sm icon={action === "restart" ? "refresh" : "play"} disabled={busy === `service:${action}`} onClick={() => service(action)}>
                        {action}
                      </Button>
                    ))}
                  </div>
                </div>
              )}
            </Card>
            <Card title="Runtime Fields" sub="Per-job overrides">
              <div className="space-y-2 text-xs text-dim">
                <RuntimeLine label="model" />
                <RuntimeLine label="enabled_toolsets" />
                <RuntimeLine label="workdir" />
                <RuntimeLine label="deliver" />
              </div>
            </Card>
          </aside>
        </div>
      )}

      {editorOpen && (
        <ScheduleEditor
          form={form}
          setForm={setForm}
          busy={busy}
          onClose={() => { setEditorOpen(false); setForm(EMPTY); }}
          onSave={save}
        />
      )}
    </>
  );
}

function JobRow({ job, busy, onToggle, onRun, onPreview, onEdit, onRemove, preview }: {
  job: Job;
  busy: string;
  onToggle: (job: Job) => void;
  onRun: (job: Job) => void;
  onPreview: (job: Job) => void;
  onEdit: (job: Job) => void;
  onRemove: (job: Job) => void;
  preview?: CronPreview;
}) {
  const previewErrors = preview?.validation?.errors || [];
  const previewWarnings = preview?.validation?.warnings || [];
  return (
    <div className="border-b border-border px-[var(--pad)] py-3 last:border-0 hover:bg-surface-2/35">
      <div className="grid gap-3 md:grid-cols-[auto_minmax(0,1fr)_auto]">
        <Toggle on={job.enabled} disabled={busy === job.id} onChange={() => onToggle(job)} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="border border-border bg-surface-2 px-2 py-0.5 font-mono text-xs text-primary">{job.schedule}</span>
            <span className="truncate font-mono text-sm font-semibold text-text">{job.name || job.id}</span>
            {job.one_shot && <Badge tone="info">one-shot</Badge>}
            {job.no_agent && <Badge tone="warning">script</Badge>}
            {job.last_status && <Badge status={job.last_status}>{job.last_status}</Badge>}
            {job.state && <Badge status={job.state}>{job.state}</Badge>}
          </div>
          <div className="mt-1 line-clamp-2 text-xs text-dim">{compact(job.prompt, 180)}</div>
          <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
            {!!job.skills?.length && <Mini>skills {job.skills.join(", ")}</Mini>}
            {!!job.context_from?.length && <Mini>from {job.context_from.join(", ")}</Mini>}
            {job.model && <Mini>model {job.model}</Mini>}
            {!!job.enabled_toolsets?.length && <Mini>toolsets {job.enabled_toolsets.join(", ")}</Mini>}
            {job.workdir && <Mini>cwd {job.workdir}</Mini>}
            {job.deliver && <Mini>deliver {job.deliver}</Mini>}
            {job.channel && <Mini>channel {job.channel}</Mini>}
            {job.script && <Mini>script {compact(job.script, 36)}</Mini>}
          </div>
          <div className="mt-2 text-[11px] text-faint">
            {job.next_run ? `next ${dateish(job.next_run)}` : "no next run"}
            {job.last_run ? ` / last ${ago(job.last_run)}` : ""}
            {job.last_error ? ` / ${job.last_error}` : ""}
          </div>
          {(job.latest_output || !!job.history?.length) && (
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {job.latest_output && (
                <pre className="scroll-thin max-h-32 overflow-auto whitespace-pre-wrap break-words border border-border bg-bg/60 p-2 font-mono text-[11px] text-dim">{job.latest_output}</pre>
              )}
              {!!job.history?.length && (
                <div className="border border-border bg-bg/45 p-2 text-xs">
                  {job.history.slice(0, 4).map((run) => (
                    <div key={run.id || `${run.status}:${run.started_at}`} className="flex justify-between gap-2 py-0.5">
                      <span className="font-mono text-faint">{compact(run.id || "run", 18)}</span>
                      <span className="text-dim">{run.status || "unknown"}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {preview && (
            <div className="mt-3 border border-border bg-bg/55 p-3 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={preview.ok === false ? "danger" : "success"}>{preview.ok === false ? "preview blocked" : "preview ok"}</Badge>
                <Mini>{preview.mode || "agent"}</Mini>
                {preview.due && <Mini>due now</Mini>}
                <Mini>next {dateish(preview.next_run || preview.next_run_iso) || "none"}</Mini>
                {preview.cron_skip_memory && <Mini>memory off</Mini>}
              </div>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                <PreviewLine label="targets" value={(preview.targets || []).join(", ") || "local only"} />
                <PreviewLine label="model" value={preview.model || "default"} />
                <PreviewLine label="toolsets" value={(preview.enabled_toolsets || []).join(", ") || "default"} />
                <PreviewLine label="disabled" value={(preview.disabled_tools || []).join(", ") || "none"} />
                <PreviewLine label="workdir" value={preview.workdir?.path ? `${preview.workdir.path} ${preview.workdir.exists && preview.workdir.is_dir ? "(ok)" : "(check)"}` : "default"} />
                <PreviewLine label="script" value={preview.script?.configured ? `${preview.script.path} ${preview.script.exists && preview.script.is_file ? "(ok)" : "(check)"}` : "none"} />
              </div>
              {!!previewErrors.length && <div className="mt-2 text-danger">{previewErrors.join(" / ")}</div>}
              {!!previewWarnings.length && <div className="mt-2 text-warning">{previewWarnings.join(" / ")}</div>}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-start gap-2">
          <button onClick={() => onEdit(job)} className="grid h-8 w-8 place-items-center border border-border text-faint hover:text-primary" title="Edit"><Icon name="config" size={15} /></button>
          <button onClick={() => onPreview(job)} className="grid h-8 w-8 place-items-center border border-border text-faint hover:text-primary" title="Dry-run preview"><Icon name="activity" size={15} /></button>
          <button onClick={() => onRun(job)} className="grid h-8 w-8 place-items-center border border-border text-faint hover:text-primary" title="Run now"><Icon name="zap" size={15} /></button>
          <button onClick={() => onRemove(job)} className="grid h-8 w-8 place-items-center border border-border text-faint hover:text-danger" title="Delete"><Icon name="trash" size={15} /></button>
        </div>
      </div>
    </div>
  );
}

function ScheduleEditor({ form, setForm, busy, onClose, onSave }: {
  form: FormState;
  setForm: (form: FormState) => void;
  busy: string;
  onClose: () => void;
  onSave: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center bg-black/55 pt-[6vh] backdrop-blur-sm" onMouseDown={onClose}>
      <div className="scroll-thin max-h-[88vh] w-full max-w-4xl overflow-auto border border-border bg-bg shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <div className="font-mono text-base font-semibold text-text">{form.id ? "Edit schedule" : "New schedule"}</div>
            <div className="text-xs text-faint">{form.id || "typed cron job"}</div>
          </div>
          <button onClick={onClose} className="text-faint hover:text-text"><Icon name="x" size={18} /></button>
        </div>
        <div className="grid gap-3 p-4 md:grid-cols-2">
          <Field label="Name"><Input value={form.name} placeholder="Daily digest" onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Schedule"><Input value={form.schedule} placeholder="@daily, 30m, or 0 9 * * *" onChange={(e) => setForm({ ...form, schedule: e.target.value })} /></Field>
          <Field label="Model"><Input value={form.model} placeholder="gpt-5.5" onChange={(e) => setForm({ ...form, model: e.target.value })} /></Field>
          <Field label="Toolsets"><Input value={form.enabled_toolsets} placeholder="core, web" onChange={(e) => setForm({ ...form, enabled_toolsets: e.target.value })} /></Field>
          <Field label="Workdir"><Input value={form.workdir} placeholder="/path/to/project" onChange={(e) => setForm({ ...form, workdir: e.target.value })} /></Field>
          <Field label="Delivery"><Input value={form.deliver} placeholder="telegram:123, discord:456" onChange={(e) => setForm({ ...form, deliver: e.target.value })} /></Field>
          <Field label="Skills"><Input value={form.skills} placeholder="summarize, research" onChange={(e) => setForm({ ...form, skills: e.target.value })} /></Field>
          <Field label="Context from"><Input value={form.context_from} placeholder="job id or name, comma separated" onChange={(e) => setForm({ ...form, context_from: e.target.value })} /></Field>
          <Field label="Script path"><Input value={form.script} placeholder="/path/to/script.py" onChange={(e) => setForm({ ...form, script: e.target.value })} /></Field>
          <Field label="Legacy channel"><Input value={form.channel} placeholder="telegram:123" onChange={(e) => setForm({ ...form, channel: e.target.value })} /></Field>
          <Field label="Max runs"><Input value={form.max_runs} placeholder="0" inputMode="numeric" onChange={(e) => setForm({ ...form, max_runs: e.target.value })} /></Field>
          <label className="flex items-end gap-2 pb-1 text-sm text-dim">
            <Toggle on={form.no_agent} onChange={(value) => setForm({ ...form, no_agent: value })} />
            Script only
          </label>
          <div className="md:col-span-2">
            <Field label="Prompt">
              <textarea
                value={form.prompt}
                onChange={(e) => setForm({ ...form, prompt: e.target.value })}
                rows={6}
                placeholder="What the scheduled agent should do"
                className="w-full resize-y border border-border bg-surface-2/80 px-3 py-2 text-sm text-text outline-none focus:border-primary/60"
              />
            </Field>
          </div>
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon="check" disabled={busy === "save" || !form.schedule.trim() || !form.prompt.trim()} onClick={onSave}>
            {form.id ? "Save" : "Create"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Blueprint({ title, schedule, prompt, onUse }: { title: string; schedule: string; prompt: string; onUse: () => void }) {
  return (
    <div className="bg-surface/72 p-[var(--pad)]">
      <div className="font-mono text-sm font-semibold text-text">{title}</div>
      <div className="mt-1 font-mono text-xs text-primary">{schedule}</div>
      <p className="mt-3 min-h-12 text-xs text-dim">{prompt}</p>
      <Button sm icon="plus" onClick={onUse}>Use blueprint</Button>
    </div>
  );
}

function RuntimeLine({ label }: { label: string }) {
  return (
    <div className="flex justify-between gap-3 border-b border-border pb-1 last:border-0">
      <span className="font-mono text-faint">{label}</span>
      <span className="text-text">supported</span>
    </div>
  );
}

function PreviewLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 justify-between gap-3 border-b border-border/70 pb-1 last:border-0">
      <span className="font-mono text-faint">{label}</span>
      <span className="min-w-0 truncate text-right text-dim">{value}</span>
    </div>
  );
}

function Mini({ children }: { children: ReactNode }) {
  return <span className="border border-border bg-surface-2 px-1.5 py-px font-mono text-faint">{children}</span>;
}
