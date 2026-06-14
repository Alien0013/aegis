import { useEffect, useRef } from "react";
import { useApi } from "../lib/useApi";
import { cn } from "../lib/cn";
import { Button, Card, Empty, Loading, PageHeader } from "../components/ui";

function lineTone(line: string): string {
  const l = line.toLowerCase();
  if (/(error|exception|traceback|failed|fatal)/.test(l)) return "text-danger";
  if (/(warn|warning)/.test(l)) return "text-warning";
  return "text-dim";
}

export function Logs() {
  const { data, loading, error, reload } = useApi<{ path?: string; lines?: string[] }>("logs");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.scrollTo(0, ref.current.scrollHeight); }, [data]);

  return (
    <>
      <PageHeader title="Logs" sub={data?.path || "~/.aegis/logs/aegis.log"}
        actions={<Button variant="ghost" icon="refresh" onClick={reload}>Refresh</Button>} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <Card pad={false}>
          {!(data.lines || []).length && <Empty icon="logs">No log lines.</Empty>}
          <div ref={ref} className="scroll-thin max-h-[75vh] overflow-auto p-3 font-mono text-xs leading-relaxed">
            {(data.lines || []).map((l, i) => (
              <div key={i} className={cn("whitespace-pre-wrap break-words", lineTone(l))}>{l}</div>
            ))}
          </div>
        </Card>
      )}
    </>
  );
}
