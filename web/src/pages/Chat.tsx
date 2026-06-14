// Chat = the real AEGIS TUI embedded in an xterm terminal over the /api/pty
// WebSocket (the Hermes approach). The TUI renders the conversation, tool trail,
// and reasoning cleanly — no raw scaffolding — because it's the actual agent CLI.

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { TOKEN, api } from "../lib/api";
import { ago, compact } from "../lib/format";
import { cn } from "../lib/cn";
import { useTheme } from "../themes/ThemeProvider";
import { Badge } from "../components/ui";
import { Icon } from "../components/icons";

interface SessionRow { id: string; title?: string; updated_at?: string }

function ptyUrl(term: Terminal, resume: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const q = new URLSearchParams();
  if (TOKEN) q.set("token", TOKEN);
  q.set("cols", String(term.cols));
  q.set("rows", String(term.rows));
  if (resume) q.set("resume", resume);
  return `${proto}//${window.location.host}/api/pty?${q.toString()}`;
}

const resizeFrame = (t: Terminal) => `\x1b]1337;Resize=cols=${t.cols};rows=${t.rows}\x07`;

export function Chat() {
  const { theme } = useTheme();
  const [params] = useSearchParams();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<"connecting" | "connected" | "closed" | "error">("connecting");
  const [generation, setGeneration] = useState(0);
  const [resume, setResume] = useState(params.get("id") || "");
  const [sessions, setSessions] = useState<SessionRow[]>([]);

  useEffect(() => {
    api<SessionRow[]>("sessions").then((s) => setSessions(Array.isArray(s) ? s : [])).catch(() => setSessions([]));
  }, [generation]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    host.innerHTML = "";
    const p = theme.palette;
    const term = new Terminal({
      cursorBlink: true, convertEol: true, fontFamily: theme.typography.fontMono,
      fontSize: 13, lineHeight: 1.3, scrollback: 8000,
      theme: {
        background: theme.termBg, foreground: p.text, cursor: p.primary,
        selectionBackground: p.primary + "55",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();
    term.focus();

    const ws = new WebSocket(ptyUrl(term, resume));
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    setStatus("connecting");

    const sendResize = () => {
      try { fit.fit(); if (ws.readyState === WebSocket.OPEN) ws.send(resizeFrame(term)); } catch { /* disposing */ }
    };
    const ro = new ResizeObserver(sendResize);
    ro.observe(host);
    const dataSub = term.onData((d) => { if (ws.readyState === WebSocket.OPEN) ws.send(d); });

    ws.onopen = () => { setStatus("connected"); sendResize(); };
    ws.onmessage = async (e) => {
      if (typeof e.data === "string") term.write(e.data);
      else if (e.data instanceof ArrayBuffer) term.write(new Uint8Array(e.data));
      else if (e.data instanceof Blob) term.write(new Uint8Array(await e.data.arrayBuffer()));
    };
    ws.onerror = () => setStatus("error");
    ws.onclose = () => setStatus((s) => (s === "error" ? "error" : "closed"));

    return () => {
      dataSub.dispose(); ro.disconnect();
      try { ws.close(); } catch { /* closed */ }
      term.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generation, theme]);

  function open(id: string) { setResume(id); setGeneration((n) => n + 1); }
  function fresh() { setResume(""); setGeneration((n) => n + 1); }

  return (
    <div className="flex h-[calc(100vh-7rem)] gap-[var(--gap)]">
      <aside className="hidden w-60 shrink-0 flex-col rounded-[calc(var(--radius)+2px)] border border-border bg-surface lg:flex">
        <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-faint">Sessions</span>
          <button onClick={fresh} className="text-dim hover:text-text" title="New chat"><Icon name="plus" size={15} /></button>
        </div>
        <div className="scroll-thin flex-1 overflow-y-auto p-1.5">
          {!sessions.length && <div className="px-2 py-6 text-center text-xs text-faint">No sessions yet.</div>}
          {sessions.slice(0, 40).map((s) => (
            <button key={s.id} onClick={() => open(s.id)}
              className={cn("block w-full rounded-[var(--radius)] px-2.5 py-1.5 text-left hover:bg-surface-2",
                resume === s.id && "bg-surface-2")}>
              <div className="truncate text-sm text-text">{compact(s.title || s.id, 28)}</div>
              <div className="text-[11px] text-faint">{ago(s.updated_at)}</div>
            </button>
          ))}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[calc(var(--radius)+2px)] border border-border bg-surface">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <div className="flex items-center gap-2 text-xs text-dim">
            <Icon name="terminal" size={14} />
            <span>{resume ? `resumed · ${compact(resume, 16)}` : "new session"}</span>
          </div>
          <div className="flex items-center gap-2">
            <Badge status={status === "connected" ? "ok" : status === "error" ? "error" : "pending"}>{status}</Badge>
            <button onClick={() => setGeneration((n) => n + 1)} className="text-dim hover:text-text" title="Reconnect">
              <Icon name="refresh" size={14} />
            </button>
          </div>
        </div>
        <div ref={hostRef} className="min-h-0 flex-1 p-2" style={{ background: theme.termBg }} />
      </section>
    </div>
  );
}
