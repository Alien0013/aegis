// Dependency-free, XSS-safe markdown renderer for the graphical chat.
//
// React escapes every text node, so there is no dangerouslySetInnerHTML and no
// sanitizer dependency. Handles the markdown the agent actually emits: fenced code
// blocks, inline code, bold/italic, headings, ordered/unordered lists, blockquotes,
// links, and paragraphs. Good enough to render an LLM reply cleanly; not a spec parser.

import type { ReactNode } from "react";

const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*|_[^_\n]+_)|(\[[^\]]+\]\([^)]+\))/g;
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

function Prose({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
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
      const items = list.items.map((it, i) => <li key={i}>{inline(it, `li${key}-${i}`)}</li>);
      blocks.push(
        list.ordered
          ? <ol key={`l${key++}`} className="ml-5 list-decimal space-y-1">{items}</ol>
          : <ul key={`l${key++}`} className="ml-5 list-disc space-y-1">{items}</ul>,
      );
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    const ul = /^[-*]\s+(.*)$/.exec(line);
    const ol = /^\d+\.\s+(.*)$/.exec(line);
    const bq = /^>\s?(.*)$/.exec(line);
    if (h) {
      flushPara(); flushList();
      const lvl = h[1].length;
      blocks.push(
        <div key={`h${key++}`} className={lvl <= 2 ? "mt-1 text-[15px] font-semibold text-text" : "mt-1 text-[13.5px] font-semibold text-dim"}>
          {inline(h[2], `h${key}`)}
        </div>,
      );
    } else if (ul || ol) {
      flushPara();
      const ordered = !!ol;
      if (!list || list.ordered !== ordered) { flushList(); list = { ordered, items: [] }; }
      list.items.push((ul ? ul[1] : ol![1]));
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
          return (
            <pre key={pi} className="scroll-thin overflow-x-auto rounded-[var(--radius)] border border-border bg-surface-2 p-3">
              {safeLang && <div className="mb-1.5 select-none text-[10px] uppercase tracking-wide text-faint">{safeLang}</div>}
              <code
                className={`font-mono text-[12.5px] leading-relaxed text-text${safeLang ? ` language-${safeLang}` : ""}`}
                data-language={safeLang || undefined}
              >
                {body}
              </code>
            </pre>
          );
        }
        return part.trim() ? <Prose key={pi} text={part} /> : null;
      })}
    </div>
  );
}
