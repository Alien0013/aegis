// Collapsible tool-call row for the chat transcript. One row per tool call:
//   ▸ ⚡ read_file  path=/foo            ✓ 2.3s
// Click to expand args / streaming preview / result / error. Errors auto-expand.
// Original implementation (Hermes-inspired pattern, AEGIS event model).

import { useEffect, useState } from "react";
import { cn } from "../lib/cn";
import { Icon } from "./icons";

export interface ToolEntry {
  id: string;
  name: string;
  target: string;
  args?: string;
  preview?: string;
  summary?: string;
  error?: string;
  status: "running" | "ok" | "error";
  startedAt: number;
  completedAt?: number;
}

const TONE: Record<ToolEntry["status"], string> = {
  running: "border-primary/40 bg-primary/[0.05]",
  ok: "border-border bg-surface-2/40",
  error: "border-danger/50 bg-danger/[0.05]",
};

function fmtElapsed(ms: number): string {
  const s = Math.max(0, ms) / 1000;
  if (s < 1) return `${Math.round(ms)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return r ? `${m}m ${r}s` : `${m}m`;
}

function colorizeDiff(diff: string) {
  return diff.split("\n").map((line, i) => {
    let cls = "text-dim";
    if (line.startsWith("+") && !line.startsWith("+++")) cls = "text-success";
    else if (line.startsWith("-") && !line.startsWith("---")) cls = "text-danger";
    else if (line.startsWith("@@")) cls = "text-primary";
    return <div key={i} className={cls}>{line || " "}</div>;
  });
}

function Section({ label, children, mono = true }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex gap-3">
      <span className="w-16 shrink-0 pt-0.5 text-[10px] uppercase tracking-wider text-faint">{label}</span>
      <div className={cn("min-w-0 flex-1 whitespace-pre-wrap break-words text-dim", mono && "font-mono")}>{children}</div>
    </div>
  );
}

export function ToolCall({ tool }: { tool: ToolEntry }) {
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? tool.status === "error";

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (tool.status !== "running") return;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [tool.status]);

  const elapsed = tool.startedAt > 0
    ? fmtElapsed((tool.completedAt ?? now) - tool.startedAt) : null;
  const isDiff = !!tool.summary && /^(@@|[+-])/m.test(tool.summary) && tool.name.includes("edit");
  const hasBody = !!(tool.args || tool.preview || tool.summary || tool.error);

  return (
    <div className={cn("overflow-hidden rounded-[var(--radius)] border", TONE[tool.status])}>
      <button
        type="button"
        disabled={!hasBody}
        onClick={() => setOverride(!open)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-text/[0.03] disabled:cursor-default"
      >
        {hasBody
          ? <Icon name={open ? "chevronDown" : "chevronRight"} size={12} className="shrink-0 text-faint" />
          : <span className="w-3 shrink-0" />}
        <Icon name="zap" size={12} className={cn("shrink-0",
          tool.status === "error" ? "text-danger" : tool.status === "running" ? "text-primary" : "text-primary/70")} />
        <span className="shrink-0 font-mono font-medium text-text">{tool.name}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-faint">{tool.target}</span>
        {tool.status === "running" && (
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" style={{ animation: "pulse-dot 1s infinite" }} />
        )}
        {tool.status === "ok" && <Icon name="check" size={12} className="shrink-0 text-success" />}
        {tool.status === "error" && <Icon name="alert" size={12} className="shrink-0 text-danger" />}
        {elapsed && <span className="shrink-0 font-mono tabular-nums text-faint">{elapsed}</span>}
      </button>

      {open && hasBody && (
        <div className="space-y-2 border-t border-border/60 px-3 py-2 text-xs">
          {tool.args && <Section label="args">{tool.args}</Section>}
          {tool.preview && tool.status === "running" && <Section label="stream">{tool.preview}</Section>}
          {tool.summary && (isDiff
            ? <Section label="diff"><pre className="overflow-x-auto leading-snug">{colorizeDiff(tool.summary)}</pre></Section>
            : <Section label="result">{tool.summary}</Section>)}
          {tool.error && <Section label="error"><span className="text-danger">{tool.error}</span></Section>}
        </div>
      )}
    </div>
  );
}
