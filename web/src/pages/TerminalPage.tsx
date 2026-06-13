import { useEffect, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { TOKEN } from "../lib/api";
import { Badge, Button, PageHeader } from "../lib/ui";

function terminalUrl(term?: Terminal): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const q = new URLSearchParams();
  if (TOKEN) q.set("token", TOKEN);
  if (term) {
    q.set("cols", String(term.cols));
    q.set("rows", String(term.rows));
  }
  const suffix = q.toString();
  return `${proto}//${window.location.host}/api/pty${suffix ? `?${suffix}` : ""}`;
}

function resizeFrame(term: Terminal): string {
  return `\x1b]1337;Resize=cols=${term.cols};rows=${term.rows}\x07`;
}

export function TerminalPage() {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<"connecting" | "connected" | "closed" | "error">("closed");
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    host.innerHTML = "";
    const term = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: 'ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace',
      fontSize: 13,
      lineHeight: 1.25,
      scrollback: 6000,
      theme: {
        background: "#05070a",
        foreground: "#e9ebf0",
        cursor: "#37d4cf",
        selectionBackground: "#31405f",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();
    term.focus();
    termRef.current = term;
    fitRef.current = fit;

    const ws = new WebSocket(terminalUrl(term));
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    setStatus("connecting");

    const sendResize = () => {
      try {
        fit.fit();
        if (ws.readyState === WebSocket.OPEN) ws.send(resizeFrame(term));
      } catch {
        /* terminal may be disposing */
      }
    };
    const resizeObserver = new ResizeObserver(sendResize);
    resizeObserver.observe(host);
    const dataSub = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(data);
    });

    ws.onopen = () => {
      setStatus("connected");
      sendResize();
    };
    ws.onmessage = async (event) => {
      if (typeof event.data === "string") {
        term.write(event.data);
      } else if (event.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(event.data));
      } else if (event.data instanceof Blob) {
        term.write(new Uint8Array(await event.data.arrayBuffer()));
      }
    };
    ws.onerror = () => setStatus("error");
    ws.onclose = () => setStatus((s) => (s === "error" ? "error" : "closed"));

    return () => {
      dataSub.dispose();
      resizeObserver.disconnect();
      try { ws.close(); } catch { /* already closed */ }
      term.dispose();
      if (wsRef.current === ws) wsRef.current = null;
      if (termRef.current === term) termRef.current = null;
      if (fitRef.current === fit) fitRef.current = null;
    };
  }, [generation]);

  const reconnect = () => {
    try { wsRef.current?.close(); } catch { /* already closed */ }
    setGeneration((n) => n + 1);
  };

  return (
    <>
      <PageHeader
        title="Terminal"
        sub={<span className="terminal-sub"><Badge status={status}>{status}</Badge><span>AEGIS TUI over WebSocket PTY</span></span>}
        actions={
          <>
            <Button variant="ghost" icon="refresh" onClick={reconnect}>Reconnect</Button>
            <Button variant="ghost" icon="close" onClick={() => wsRef.current?.close()}>Disconnect</Button>
          </>
        }
      />
      <div className="terminal-shell" ref={hostRef} />
    </>
  );
}
