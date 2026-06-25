// Collapsible tool-call row for the chat transcript. One row per tool call:
//   ▸ ⚡ read_file  path=/foo            ✓ 2.3s
// Click to expand args / streaming preview / result / error. Errors auto-expand.
// Original implementation for the AEGIS event model.

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
  diff?: string;
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
    let prefix = "";
    if (line.startsWith("diff --git") || line.startsWith("+++ ") || line.startsWith("--- ")) {
      cls = "bg-surface-2 px-2 py-1 text-text";
    } else if (line.startsWith("+")) {
      cls = "bg-success/10 px-2 text-success";
      prefix = "+";
    } else if (line.startsWith("-")) {
      cls = "bg-danger/10 px-2 text-danger";
      prefix = "-";
    } else if (line.startsWith("@@")) {
      cls = "bg-primary/10 px-2 py-0.5 text-primary";
    } else {
      cls = "px-2 text-dim";
      prefix = " ";
    }
    return (
      <div key={i} className={cls}>
        {prefix && <span className="mr-2 select-none text-faint">{prefix}</span>}
        {prefix ? line.slice(1) || " " : line || " "}
      </div>
    );
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

function looksLikeDiff(value: string): boolean {
  return /^(diff --git|@@ |[+-]{3}\s|[+-][^\n])/m.test(value);
}

// Per-tool glyph so the transcript reads at a glance (parity with the terminal UI),
// mapped onto the existing icon set.
function toolIcon(name: string): string {
  const n = name.toLowerCase();
  if (/(bash|shell|exec|run|process|command|terminal)/.test(n)) return "terminal";
  if (/(web|http|browser|url|fetch|request)/.test(n)) return "external";
  if (/(search|grep|glob|find)/.test(n)) return "search";
  if (/(memory|recall)/.test(n)) return "memory";
  if (/skill/.test(n)) return "skills";
  if (/(kanban|todo)/.test(n)) return "kanban";
  if (/(cron|schedul)/.test(n)) return "cron";
  if (/(subagent|spawn|agent)/.test(n)) return "agents";
  if (/(sql|database|\bdb\b)/.test(n)) return "database";
  if (/(read|write|edit|patch|file|dir|\bls\b|cat)/.test(n)) return "files";
  return "tools";
}

function diffStat(diff: string): { adds: number; dels: number } {
  const lines = diff.split("\n");
  return {
    adds: lines.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length,
    dels: lines.filter((l) => l.startsWith("-") && !l.startsWith("---")).length,
  };
}

function FileDiffPanel({ diff }: { diff: string }) {
  const lines = diff.split("\n");
  const files = lines.filter((line) => line.startsWith("diff --git ")).length;
  const adds = lines.filter((line) => line.startsWith("+") && !line.startsWith("+++")).length;
  const dels = lines.filter((line) => line.startsWith("-") && !line.startsWith("---")).length;
  return (
    <div className="overflow-hidden rounded-[var(--radius)] border border-border bg-bg">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-2 px-2 py-1.5 font-sans text-[11px] text-faint">
        <span className="font-medium text-text">{files ? `${files} file${files === 1 ? "" : "s"}` : "Diff preview"}</span>
        <span className="text-success">+{adds}</span>
        <span className="text-danger">-{dels}</span>
      </div>
      <pre className="scroll-thin max-h-80 overflow-auto py-1 font-mono text-[12px] leading-5">
        {colorizeDiff(diff)}
      </pre>
    </div>
  );
}

export function ToolCall({ tool }: { tool: ToolEntry }) {
  const [override, setOverride] = useState<boolean | null>(null);
  const diffText = [tool.diff || "", tool.summary || "", tool.preview || ""].find(looksLikeDiff) || "";
  const isDiff = !!diffText && (
    tool.name.includes("edit")
    || tool.name.includes("patch")
    || tool.name.includes("write")
    || looksLikeDiff(diffText)
  );
  const open = override ?? (tool.status === "error" || isDiff);

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (tool.status !== "running") return;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [tool.status]);

  const elapsed = tool.startedAt > 0
    ? fmtElapsed((tool.completedAt ?? now) - tool.startedAt) : null;
  const hasBody = !!(tool.args || tool.preview || tool.summary || tool.error);
  const stat = isDiff ? diffStat(diffText) : null;

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
        <Icon name={toolIcon(tool.name)} size={12} className={cn("shrink-0",
          tool.status === "error" ? "text-danger" : tool.status === "running" ? "text-primary" : "text-primary/70")} />
        <span className="shrink-0 font-mono font-medium text-text">{tool.name}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-faint">{tool.target}</span>
        {stat && (stat.adds > 0 || stat.dels > 0) && (
          <span className="shrink-0 font-mono text-[11px] tabular-nums">
            {stat.adds > 0 && <span className="text-success">+{stat.adds}</span>}
            {stat.adds > 0 && stat.dels > 0 && <span> </span>}
            {stat.dels > 0 && <span className="text-danger">-{stat.dels}</span>}
          </span>
        )}
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
          {isDiff
            ? (
                <>
                  <Section label="diff" mono={false}><FileDiffPanel diff={diffText} /></Section>
                  {tool.summary && tool.summary !== diffText && <Section label="result">{tool.summary}</Section>}
                  {tool.preview && tool.preview !== diffText && <Section label="preview">{tool.preview}</Section>}
                </>
              )
            : (
                <>
                  {tool.preview && <Section label={tool.status === "running" ? "stream" : "preview"}>{tool.preview}</Section>}
                  {tool.summary && <Section label="result">{tool.summary}</Section>}
                </>
              )}
          {tool.error && <Section label="error"><span className="text-danger">{tool.error}</span></Section>}
        </div>
      )}
    </div>
  );
}
