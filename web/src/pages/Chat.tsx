// Chat = the real AEGIS TUI embedded in an xterm terminal over the /api/pty
// WebSocket (the dashboard terminal approach). The TUI renders the conversation, tool trail,
// and reasoning cleanly — no raw scaffolding — because it's the actual agent CLI.
// Just the terminal: session browsing lives on the Sessions page (AEGIS keeps
// Chat and Sessions separate). Opening a session there deep-links to /chat?id=…,
// which resumes it here.

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { TOKEN } from "../lib/api";
import { compact } from "../lib/format";
import { useTheme } from "../themes/ThemeProvider";
import { Badge } from "../components/ui";
import { Icon } from "../components/icons";

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

  // Deep-link from the Sessions page: /chat?id=<sid> resumes that session.
  useEffect(() => {
    const id = params.get("id") || "";
    if (id && id !== resume) { setResume(id); setGeneration((n) => n + 1); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

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

  function fresh() { setResume(""); setGeneration((n) => n + 1); }

  return (
    <div className="h-[calc(100vh-7rem)]">
      <section className="flex h-full min-w-0 flex-col overflow-hidden rounded-[calc(var(--radius)+2px)] border border-border bg-surface">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <div className="flex items-center gap-2 text-xs text-dim">
            <Icon name="terminal" size={14} />
            <span>{resume ? `resumed · ${compact(resume, 16)}` : "new session"}</span>
          </div>
          <div className="flex items-center gap-2">
            <Badge status={status === "connected" ? "ok" : status === "error" ? "error" : "pending"}>{status}</Badge>
            <button onClick={fresh} className="text-dim hover:text-text" title="New session">
              <Icon name="plus" size={15} />
            </button>
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
