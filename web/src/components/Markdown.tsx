// Dependency-free, XSS-safe markdown renderer for the graphical chat.
//
// React escapes every text node, so there is no dangerouslySetInnerHTML and no
// sanitizer dependency. Handles the markdown the agent actually emits: fenced code
// blocks (with a header bar, copy button, and lightweight syntax highlighting),
// GFM tables, task lists, inline code, bold/italic/strikethrough, headings,
// ordered/unordered lists, blockquotes, horizontal rules, links, and paragraphs.
// Good enough to render an LLM reply at chat quality; not a spec parser.

import { useState, type ReactNode } from "react";
import { Icon } from "./icons";

const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(~~[^~]+~~)|(\*[^*\n]+\*|_[^_\n]+_)|(\[[^\]]+\]\([^)]+\))/g;
const LANGUAGE_ID = /^[a-z0-9_+.-]+$/i;

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  INLINE.lastIndex = 0;
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${key}-${i++}`;
    if (tok.startsWith("`")) {
      out.push(
        <code key={k} className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[0.85em] text-text">
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("**")) {
      out.push(<strong key={k} className="font-semibold text-text">{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith("~~")) {
      out.push(<span key={k} className="text-dim line-through">{tok.slice(2, -2)}</span>);
    } else if (tok.startsWith("[")) {
      const mm = /\[([^\]]+)\]\(([^)]+)\)/.exec(tok);
      out.push(
        <a key={k} href={mm?.[2]} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">
          {mm?.[1]}
        </a>,
      );
    } else {
      out.push(<em key={k}>{tok.replace(/^[*_]|[*_]$/g, "")}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// --- lightweight, language-agnostic syntax highlighting ---------------------
// One pass over the source classifying comments, strings, numbers, and a broad
// cross-language keyword set. Not a real grammar — just enough color to read code
// the way a chat surface does, with zero dependencies. Theme tokens supply color.
const HL =
  /(\/\/[^\n]*|#[^\n]*|--[^\n]*|\/\*[\s\S]*?\*\/)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)|\b(0x[0-9a-fA-F]+|\d[\d_.]*)\b|\b(const|let|var|function|fn|def|class|struct|enum|interface|type|return|yield|if|elif|else|for|while|do|switch|case|break|continue|in|of|import|from|export|package|use|new|delete|await|async|try|except|catch|finally|throw|raise|with|as|public|private|protected|static|final|void|int|float|double|string|str|bool|boolean|true|false|null|nil|none|None|True|False|undefined|self|this|super|lambda|match|select|insert|update|where|and|or|not)\b/g;

function highlight(code: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  HL.lastIndex = 0;
  while ((m = HL.exec(code)) !== null) {
    if (m.index > last) out.push(code.slice(last, m.index));
    const k = `h${i++}`;
    if (m[1] != null) out.push(<span key={k} className="text-faint italic">{m[1]}</span>);
    else if (m[2] != null) out.push(<span key={k} className="text-success">{m[2]}</span>);
    else if (m[3] != null) out.push(<span key={k} className="text-warning">{m[3]}</span>);
    else if (m[4] != null) out.push(<span key={k} className="font-medium text-info">{m[4]}</span>);
    last = m.index + m[0].length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(code).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    });
  };
  return (
    <div className="overflow-hidden rounded-[var(--radius)] border border-border bg-surface-2">
      <div className="flex items-center justify-between border-b border-border/70 px-3 py-1">
        <span className="select-none font-mono text-[10px] uppercase tracking-wide text-faint">{lang || "code"}</span>
        <button
          type="button"
          onClick={copy}
          aria-label="Copy code"
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-faint transition-colors hover:text-text"
        >
          <Icon name={copied ? "check" : "copy"} size={11} />
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="scroll-thin overflow-x-auto p-3">
        <code className={`font-mono text-[12.5px] leading-relaxed text-text${lang ? ` language-${lang}` : ""}`} data-language={lang || undefined}>
          {highlight(code)}
        </code>
      </pre>
    </div>
  );
}

function splitRow(line: string): string[] {
  return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
}

function Table({ rows, keyBase }: { rows: string[]; keyBase: string }) {
  const header = splitRow(rows[0]);
  const body = rows.slice(2).map(splitRow);
  return (
    <div className="scroll-thin overflow-x-auto rounded-[var(--radius)] border border-border">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="bg-surface-2/60">
            {header.map((h, i) => (
              <th key={i} className="border-b border-border px-2.5 py-1.5 text-left font-semibold text-text">
                {inline(h, `${keyBase}-th${i}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri} className="border-b border-border/40 last:border-0">
              {r.map((c, ci) => (
                <td key={ci} className="px-2.5 py-1.5 align-top text-dim">{inline(c, `${keyBase}-td${ri}-${ci}`)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Prose({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: ReactNode[] } | null = null;
  let para: string[] = [];
  let key = 0;

  const flushPara = () => {
    if (para.length) {
      blocks.push(<p key={`p${key++}`}>{inline(para.join(" "), `p${key}`)}</p>);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push(
        list.ordered
          ? <ol key={`l${key++}`} className="ml-5 list-decimal space-y-1">{list.items}</ol>
          : <ul key={`l${key++}`} className="ml-5 list-disc space-y-1">{list.items}</ul>,
      );
      list = null;
    }
  };

  for (let li = 0; li < lines.length; li++) {
    const line = lines[li].trimEnd();
    // GFM table: a header row, then a |---|:--:| separator row.
    if (/\|/.test(line) && li + 1 < lines.length && /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(lines[li + 1]) && /\|/.test(lines[li + 1])) {
      flushPara(); flushList();
      const tableRows = [line, lines[li + 1]];
      let j = li + 2;
      while (j < lines.length && /\|/.test(lines[j]) && lines[j].trim()) { tableRows.push(lines[j]); j++; }
      blocks.push(<Table key={`tbl${key++}`} rows={tableRows} keyBase={`tbl${key}`} />);
      li = j - 1;
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    const task = /^[-*]\s+\[([ xX])\]\s+(.*)$/.exec(line);
    const ul = /^[-*]\s+(.*)$/.exec(line);
    const ol = /^\d+\.\s+(.*)$/.exec(line);
    const bq = /^>\s?(.*)$/.exec(line);
    const hr = /^(\s*[-*_])(\s*\1){2,}\s*$/.test(line);
    if (h) {
      flushPara(); flushList();
      const lvl = h[1].length;
      blocks.push(
        <div key={`h${key++}`} className={lvl <= 2 ? "mt-1 text-[15px] font-semibold text-text" : "mt-1 text-[13.5px] font-semibold text-dim"}>
          {inline(h[2], `h${key}`)}
        </div>,
      );
    } else if (hr) {
      flushPara(); flushList();
      blocks.push(<hr key={`hr${key++}`} className="border-border/70" />);
    } else if (task) {
      flushPara();
      if (!list || list.ordered) { flushList(); list = { ordered: false, items: [] }; }
      const done = task[1].toLowerCase() === "x";
      list.items.push(
        <li key={list.items.length} className="-ml-5 flex list-none items-start gap-2">
          <span className={done ? "mt-0.5 text-success" : "mt-0.5 text-faint"}>
            <Icon name={done ? "check" : "square"} size={13} />
          </span>
          <span className={done ? "text-dim line-through" : ""}>{inline(task[2], `tk${key}-${list.items.length}`)}</span>
        </li>,
      );
    } else if (ul || ol) {
      flushPara();
      const ordered = !!ol;
      if (!list || list.ordered !== ordered) { flushList(); list = { ordered, items: [] }; }
      list.items.push(<li key={list.items.length}>{inline((ul ? ul[1] : ol![1]), `li${key}-${list.items.length}`)}</li>);
    } else if (bq) {
      flushPara(); flushList();
      blocks.push(
        <blockquote key={`bq${key++}`} className="border-l-2 border-border pl-3 text-dim">
          {inline(bq[1], `bq${key}`)}
        </blockquote>,
      );
    } else if (!line.trim()) {
      flushPara(); flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara(); flushList();
  return <>{blocks}</>;
}

export function Markdown({ text }: { text: string }) {
  const parts = text.split(/(```[\s\S]*?```)/g);
  return (
    <div className="space-y-2 text-[14px] leading-relaxed text-text">
      {parts.map((part, pi) => {
        if (part.startsWith("```")) {
          const lang = /^```([^\n]*)/.exec(part)?.[1]?.trim() || "";
          const safeLang = LANGUAGE_ID.test(lang) ? lang : "";
          const body = part.replace(/^```[^\n]*\n?/, "").replace(/\n?```$/, "");
          return <CodeBlock key={pi} code={body} lang={safeLang} />;
        }
        return part.trim() ? <Prose key={pi} text={part} /> : null;
      })}
    </div>
  );
}
