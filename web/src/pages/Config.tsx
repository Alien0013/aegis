import { useMemo, useState } from "react";
import { patch, post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { titleCase } from "../lib/format";
import { cn } from "../lib/cn";
import { Button, Card, Empty, Loading, PageHeader, toast } from "../components/ui";
import { AutoField, type FieldSchema } from "../components/AutoField";

interface Schema { sections: Record<string, { fields: FieldSchema[] }> }

function getByPath(obj: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((acc, k) =>
    (acc && typeof acc === "object" ? (acc as Record<string, unknown>)[k] : undefined), obj);
}

export function Config() {
  const schemaQ = useApi<Schema>("config/schema");
  const valuesQ = useApi<Record<string, unknown>>("config");
  const [active, setActive] = useState("");
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [mode, setMode] = useState<"settings" | "yaml">("settings");
  const [busy, setBusy] = useState(false);

  const sections = useMemo(() => Object.entries(schemaQ.data?.sections || {})
    .sort(([a], [b]) => a.localeCompare(b)), [schemaQ.data]);
  const current = active || sections[0]?.[0] || "";
  const fields = (schemaQ.data?.sections[current]?.fields || [])
    .filter((f) => !["dict", "null"].includes(f.type) || f.enum); // skip complex nested

  function valueOf(f: FieldSchema): unknown {
    if (f.path in edits) return edits[f.path];
    const v = getByPath(valuesQ.data, f.path);
    return v === undefined ? f.default : v;
  }
  const dirtyCount = Object.keys(edits).length;

  async function save() {
    setBusy(true);
    try {
      const updates = Object.entries(edits).map(([path, value]) => ({ path, value }));
      const r = await patch<{ ok?: boolean; errors?: Record<string, string> }>("config/fields", { updates });
      if (r.ok === false) toast(`Validation failed: ${Object.values(r.errors || {})[0] || "error"}`, "err");
      else { toast(`Saved ${updates.length} setting${updates.length === 1 ? "" : "s"}`); setEdits({}); valuesQ.reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }

  return (
    <>
      <PageHeader title="Config" sub="~/.aegis/config.yaml — grouped settings"
        actions={<>
          <div className="flex rounded-[var(--radius)] border border-border p-0.5 text-xs">
            {(["settings", "yaml"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)}
                className={cn("rounded-[calc(var(--radius)-2px)] px-2.5 py-1", mode === m ? "bg-primary text-primary-fg" : "text-dim")}>
                {m === "settings" ? "Settings" : "YAML"}</button>
            ))}
          </div>
          {mode === "settings" && (
            <Button variant="primary" icon="check" onClick={save} disabled={busy || !dirtyCount}>
              {dirtyCount ? `Save ${dirtyCount}` : "Saved"}
            </Button>
          )}
        </>} />

      {(schemaQ.error || valuesQ.error) && <Card><Empty icon="alert">Couldn't load — {schemaQ.error || valuesQ.error}</Empty></Card>}
      {(schemaQ.loading || valuesQ.loading) && <Loading />}

      {mode === "yaml" && schemaQ.data && <YamlEditor />}

      {mode === "settings" && schemaQ.data && (
        <div className="flex gap-[var(--gap)]">
          <aside className="hidden w-48 shrink-0 md:block">
            <div className="scroll-thin sticky top-0 max-h-[80vh] space-y-0.5 overflow-y-auto rounded-[calc(var(--radius)+2px)] border border-border bg-surface p-1.5">
              {sections.map(([name]) => (
                <button key={name} onClick={() => setActive(name)}
                  className={cn("block w-full rounded-[var(--radius)] px-2.5 py-1.5 text-left text-sm",
                    current === name ? "bg-primary/15 font-medium text-primary" : "text-dim hover:bg-surface-2")}>
                  {titleCase(name)}
                </button>
              ))}
            </div>
          </aside>
          <div className="min-w-0 flex-1">
            <Card title={titleCase(current)} sub={`${fields.length} settings`}>
              {!fields.length && <Empty>No editable settings in this section.</Empty>}
              <div className="divide-y divide-border">
                {fields.map((f) => (
                  <AutoField key={f.path} field={f} value={valueOf(f)}
                    onChange={(v) => setEdits((e) => ({ ...e, [f.path]: v }))} />
                ))}
              </div>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}

// Raw YAML escape hatch (advanced).
function YamlEditor() {
  const { data, loading, reload } = useApi<{ path?: string; raw?: string }>("config/yaml");
  const [raw, setRaw] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const text = raw ?? data?.raw ?? "";

  async function save() {
    setBusy(true);
    try {
      const r = await post<{ ok?: boolean; error?: string }>("config/yaml", { raw: text });
      if (r.ok === false) toast(r.error || "Invalid YAML", "err");
      else { toast("YAML saved"); setRaw(null); reload(); }
    } catch (e) { toast(String(e), "err"); }
    finally { setBusy(false); }
  }

  if (loading) return <Loading />;
  return (
    <Card title="config.yaml" sub="Advanced — validated on save, previous version backed up"
      actions={<Button variant="primary" icon="check" onClick={save} disabled={busy || raw === null}>Save</Button>}>
      <textarea value={text} spellCheck={false} onChange={(e) => setRaw(e.target.value)}
        className="scroll-thin h-[60vh] w-full resize-none rounded-[var(--radius)] border border-border bg-surface-2 p-3 font-mono text-xs text-text outline-none focus:border-primary/60" />
    </Card>
  );
}
