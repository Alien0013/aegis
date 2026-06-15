import { useEffect, useMemo, useRef, useState } from "react";
import { useApi } from "../lib/useApi";
import { cn } from "../lib/cn";
import { Button, Card, Empty, Loading, PageHeader, Segmented } from "../components/ui";

type FileName = "agent" | "desktop" | "gui" | "gateway" | "errors" | "legacy";
type Level = "all" | "debug" | "info" | "warning" | "error";
type Lines = "50" | "100" | "200" | "500";

function lineTone(line: string): string {
  const l = line.toLowerCase();
  if (/(error|exception|traceback|failed|fatal)/.test(l)) return "text-danger";
  if (/(warn|warning)/.test(l)) return "text-warning";
  return "text-dim";
}

export function Logs() {
  const [name, setName] = useState<FileName>("agent");
  const [level, setLevel] = useState<Level>("all");
  const [component, setComponent] = useState("all");
  const [limit, setLimit] = useState<Lines>("200");
  const { data, loading, error, reload } = useApi<{ path?: string; lines?: string[]; errors?: string[]; files?: Record<string, string> }>(`logs?name=${name}&limit=${limit}`);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.scrollTo(0, ref.current.scrollHeight); }, [data, level, component]);

  const lines = useMemo(() => {
    return (data?.lines || []).filter((line) => {
      const l = line.toLowerCase();
      const levelOk = level === "all" || l.includes(level);
      const compOk = component === "all" || l.includes(component);
      return levelOk && compOk;
    });
  }, [data, level, component]);

  return (
    <>
      <PageHeader
        title="Logs"
        sub={`${name.toUpperCase()} / ${level.toUpperCase()} / ${component.toUpperCase()}`}
        actions={<Button icon="refresh" onClick={reload}>Refresh</Button>}
      />
      <div className="mb-[var(--gap)] space-y-2 border border-border bg-surface/70 p-[var(--pad)]">
        <Segmented<FileName> value={name} onChange={setName} items={[
          { value: "agent", label: "AGENT" },
          { value: "errors", label: "ERRORS" },
          { value: "gateway", label: "GATEWAY" },
          { value: "desktop", label: "DESKTOP" },
          { value: "gui", label: "GUI" },
          { value: "legacy", label: "LEGACY" },
        ]} />
        <div className="flex flex-wrap gap-2">
          <Segmented<Level> value={level} onChange={setLevel} items={[
            { value: "all", label: "ALL" },
            { value: "debug", label: "DEBUG" },
            { value: "info", label: "INFO" },
            { value: "warning", label: "WARNING" },
            { value: "error", label: "ERROR" },
          ]} />
          <Segmented<Lines> value={limit} onChange={setLimit} items={[
            { value: "50", label: "50" },
            { value: "100", label: "100" },
            { value: "200", label: "200" },
            { value: "500", label: "500" },
          ]} />
          <select value={component} onChange={(e) => setComponent(e.target.value)}
            className="min-h-9 border border-border bg-surface px-3 font-mono text-xs text-text outline-none">
            <option value="all">ALL COMPONENTS</option>
            <option value="gateway">GATEWAY</option>
            <option value="agent">AGENT</option>
            <option value="tools">TOOLS</option>
            <option value="cli">CLI</option>
            <option value="cron">CRON</option>
          </select>
        </div>
      </div>
      {error && <Card><Empty icon="alert">Couldn't load - {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <div className="space-y-[var(--gap)]">
          {!!(data.errors || []).length && name !== "errors" && (
            <Card title="Recent errors" pad={false}>
              <div className="scroll-thin max-h-48 overflow-auto p-3 font-mono text-xs leading-relaxed">
                {(data.errors || []).map((l, i) => <div key={i} className="whitespace-pre-wrap break-words text-danger">{l}</div>)}
              </div>
            </Card>
          )}
          <Card title={data.path || `${name}.log`} sub={`${lines.length} visible lines`} pad={false}>
            {!lines.length && <Empty icon="logs">No log lines.</Empty>}
            <div ref={ref} className="scroll-thin max-h-[68vh] overflow-auto bg-bg/50 p-3 font-mono text-xs leading-relaxed">
              {lines.map((l, i) => (
                <div key={i} className={cn("whitespace-pre-wrap break-words", lineTone(l))}>{l}</div>
              ))}
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
