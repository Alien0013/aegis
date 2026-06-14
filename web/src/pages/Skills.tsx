import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Card, Empty, Input, Loading, PageHeader } from "../components/ui";
import { Icon } from "../components/icons";

interface Skill { name: string; description: string }

export function Skills() {
  const { data, loading, error } = useApi<Skill[]>("skills");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string>("");
  const [content, setContent] = useState<string>("");

  const skills = useMemo(() => (data || []).filter((s) =>
    !q || s.name.toLowerCase().includes(q.toLowerCase()) || (s.description || "").toLowerCase().includes(q.toLowerCase())),
    [data, q]);

  async function view(name: string) {
    if (open === name) { setOpen(""); return; }
    setOpen(name); setContent("");
    try {
      const d = await api<Record<string, unknown>>(`skills/${encodeURIComponent(name)}`);
      const body = (d.content || d.body || d.markdown || d.text) as string | undefined;
      setContent(body || JSON.stringify(d, null, 2));
    } catch (e) { setContent(String(e)); }
  }

  return (
    <>
      <PageHeader title="Skills" sub={data ? `${data.length} available` : "Reusable SKILL.md packages"}
        actions={<Input value={q} placeholder="Filter skills…" onChange={(e) => setQ(e.target.value)} className="w-52" />} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <Card pad={false}>
          {!skills.length && <Empty icon="skills">No skills match.</Empty>}
          {skills.map((s) => (
            <div key={s.name} className="border-b border-border last:border-0">
              <button onClick={() => view(s.name)} className="flex w-full items-start gap-3 px-[var(--pad)] py-2.5 text-left hover:bg-surface-2">
                <Icon name={open === s.name ? "chevronDown" : "chevronRight"} size={14} className="mt-0.5 shrink-0 text-faint" />
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-sm text-text">{s.name}</div>
                  <div className="text-xs text-faint">{s.description || "—"}</div>
                </div>
              </button>
              {open === s.name && (
                <pre className="scroll-thin max-h-96 overflow-auto whitespace-pre-wrap break-words border-t border-border bg-surface-2/40 px-[var(--pad)] py-3 font-mono text-xs text-dim">
                  {content || "Loading…"}
                </pre>
              )}
            </div>
          ))}
        </Card>
      )}
    </>
  );
}
