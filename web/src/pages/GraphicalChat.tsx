// GraphicalChat — a real graphical chat surface (message bubbles, streaming markdown,
// inline tool-call cards), backed by the agent over POST /api/chat/stream (SSE).
//
// This is the desktop app's primary surface — the graphical equivalent of the terminal
// REPL: the user's words render as bubbles, the agent's reply streams as markdown, and
// each tool call shows as a live card (running → ok/error) paired by event id.

import { useEffect, useMemo, useRef, useState } from "react";
import { api, post, postStream } from "../lib/api";
import { desktop, isDesktop } from "../lib/desktop";
import { Icon } from "../components/icons";
import { Mark } from "../components/Mark";
import { Markdown } from "../components/Markdown";

const SUGGESTIONS = [
  "Summarize this repository's structure",
  "What can you do?",
  "Run the tests and report failures",
  "Find and explain the entry point",
];

interface ToolEvent {
  id: string;
  name: string;
  target: string;
  status: string; // running | ok | error
  kind?: string; // tool | subagent
}

interface Turn {
  role: "user" | "assistant";
  text: string;
  reasoning?: string;
  tools?: ToolEvent[];
}

interface BrowserManageResponse {
  connected?: boolean;
  url?: string;
  messages?: string[];
  error?: string;
}

interface ModelsPayload {
  provider?: string;
  model?: string;
  providers?: string[];
  presets?: Record<string, string[]>;
}

interface SessionPayload {
  messages?: { role: string; content: string }[];
  meta?: {
    model?: string;
    provider?: string;
    runtime_controls?: Record<string, unknown>;
  };
}

const MODEL_KEY = "aegis.chat.composer.model";
const PROVIDER_KEY = "aegis.chat.composer.provider";
const CUSTOM_VALUE = "__custom";

function stored(key: string): string {
  try { return localStorage.getItem(key) || ""; } catch { return ""; }
}

function persist(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch { /* ignore storage failures */ }
}

export function GraphicalChat({
  sessionId,
  onSession,
  onRuntime,
}: {
  sessionId?: string;
  onSession?: (id: string) => void;
  onRuntime?: (runtime: { model: string; provider: string }) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sid, setSid] = useState(sessionId || "");
  const [modelData, setModelData] = useState<ModelsPayload | null>(null);
  const [model, setModelState] = useState(() => stored(MODEL_KEY));
  const [provider, setProviderState] = useState(() => stored(PROVIDER_KEY));
  const [runtimeDirty, setRuntimeDirty] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const setModel = (value: string, dirty = true) => {
    setModelState(value);
    persist(MODEL_KEY, value);
    if (dirty) setRuntimeDirty(true);
  };

  const setProvider = (value: string, dirty = true) => {
    setProviderState(value);
    persist(PROVIDER_KEY, value);
    if (dirty) setRuntimeDirty(true);
  };

  useEffect(() => {
    let cancelled = false;
    api<ModelsPayload>("models")
      .then((data) => {
        if (cancelled) return;
        setModelData(data);
        setProviderState((current) => {
          if (current) return current;
          const next = data.provider || "";
          persist(PROVIDER_KEY, next);
          return next;
        });
        setModelState((current) => {
          if (current) return current;
          const next = data.model || "";
          persist(MODEL_KEY, next);
          return next;
        });
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    onRuntime?.({ model, provider });
  }, [model, onRuntime, provider]);

  // Load a session's transcript when one is opened from the rail.
  useEffect(() => {
    let cancelled = false;
    setSid(sessionId || "");
    setTurns([]);
    setRuntimeDirty(false);
    if (!sessionId) return;
    fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      headers: { "X-Aegis-Token": localStorage.getItem("aegis_token") || "" },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: SessionPayload | null) => {
        if (cancelled || !data?.messages) return;
        const controls = data.meta?.runtime_controls || {};
        const sessionModel = String(controls.model || data.meta?.model || "");
        const sessionProvider = String(controls.provider || data.meta?.provider || "");
        if (sessionModel) setModel(sessionModel, false);
        if (sessionProvider) setProvider(sessionProvider, false);
        setTurns(
          data.messages
            .filter((t) => (t.role === "user" || t.role === "assistant") && (t.content || "").trim())
            .map((t) => ({ role: t.role as "user" | "assistant", text: t.content || "" })),
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const providers = modelData?.providers || (provider ? [provider] : []);
  const presets = (modelData?.presets || {})[provider] || [];
  const knownModel = presets.includes(model);
  const selectedModel = knownModel ? model : CUSTOM_VALUE;
  const customModel = model && !knownModel ? model : "";

  const switchProvider = (nextProvider: string) => {
    setProvider(nextProvider);
    const nextPresets = (modelData?.presets || {})[nextProvider] || [];
    if (nextPresets.length) setModel(nextPresets[0]);
  };

  const sendRuntime = useMemo(() => {
    const shouldSend = !sid || runtimeDirty;
    if (!shouldSend || !model.trim()) return {};
    return {
      model: model.trim(),
      ...(provider.trim() ? { provider: provider.trim() } : {}),
    };
  }, [model, provider, runtimeDirty, sid]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, busy]);

  // Auto-grow the composer with its content (capped), shrinking back after send.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, [input]);

  const fill = (text: string) => {
    setInput(text);
    taRef.current?.focus();
  };

  const patchLast = (fn: (turn: Turn) => Turn) =>
    setTurns((t) => {
      const copy = t.slice();
      copy[copy.length - 1] = fn(copy[copy.length - 1]);
      return copy;
    });

  const isBrowserSlash = (text: string) => {
    const raw = text.trim().toLowerCase();
    return raw === "/browser" || raw.startsWith("/browser ");
  };

  const runBrowserSlash = async (text: string) => {
    setInput("");
    setBusy(true);
    try {
      const finish = (reply: string) => {
        setTurns((t) => [...t, { role: "user", text }, { role: "assistant", text: reply }]);
      };
      if (isDesktop) {
        const connection = await desktop?.getConnection?.();
        if (connection?.mode === "remote") {
          finish("/browser manages a Chromium-family browser on the gateway host and is only available with a local gateway.");
          return;
        }
      }

      const [_command, rawAction = "status", ...rest] = text.trim().split(/\s+/).filter(Boolean);
      const action = rawAction.toLowerCase();
      if (!["connect", "disconnect", "status"].includes(action)) {
        finish("usage: /browser [connect|disconnect|status] [url]\npersistent: set browser.cdp_url in config.yaml");
        return;
      }

      const url = action === "connect" ? rest.join(" ").trim() || "http://127.0.0.1:9222" : undefined;
      const lines: string[] = [];
      if (url) lines.push(`checking Chromium-family browser remote debugging at ${url}...`);
      const result = await post<BrowserManageResponse>("browser/manage", { action, url, session_id: sid || undefined });
      (result.messages || []).forEach((message) => lines.push(message));
      if (result.error) lines.push(`error: ${result.error}`);
      else if (action === "status") {
        lines.push(
          result.connected
            ? `browser connected: ${result.url || "(url unavailable)"}`
            : "browser not connected (try /browser connect <url> or set browser.cdp_url in config.yaml)",
        );
      } else if (action === "disconnect") {
        lines.push("browser disconnected");
      } else if (result.connected) {
        lines.push("Browser connected to live Chromium-family browser via CDP");
        lines.push(`Endpoint: ${result.url || "(url unavailable)"}`);
        lines.push("next browser tool call will use this CDP endpoint");
      } else {
        lines.push("Browser not connected - start a Chromium-family browser with remote debugging and retry /browser connect");
      }
      finish(lines.join("\n"));
    } catch (e) {
      setTurns((t) => [
        ...t,
        { role: "user", text },
        { role: "assistant", text: `error: ${e instanceof Error ? e.message : String(e)}` },
      ]);
    } finally {
      setBusy(false);
      taRef.current?.focus();
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    if (isBrowserSlash(text)) {
      await runBrowserSlash(text);
      return;
    }
    setInput("");
    setBusy(true);
    setTurns((t) => [...t, { role: "user", text }, { role: "assistant", text: "", tools: [] }]);

    try {
      await postStream("chat/stream", { message: text, session_id: sid || undefined, ...sendRuntime }, (data) => {
        const type = String(data.type || "");
        if (type === "start") {
          const s = String(data.session_id || "");
          if (s) { setSid(s); onSession?.(s); }
          return;
        }
        if (type === "final") {
          const reply = String(data.reply || "");
          const s = String(data.session_id || "");
          patchLast((turn) => ({ ...turn, text: reply || turn.text }));
          if (s) { setSid(s); onSession?.(s); }
          setRuntimeDirty(false);
          return;
        }
        if (type === "error") {
          patchLast((turn) => ({ ...turn, text: String(data.reply || "error") }));
          return;
        }
        if (type !== "event") return;
        const ev = (data.event || {}) as Record<string, unknown>;
        const et = String(ev.type || "");
        if (et === "assistant_delta" || et === "assistant_message") {
          const delta = String(ev.text || "");
          patchLast((turn) => ({ ...turn, text: turn.text + delta }));
        } else if (et === "reasoning_delta") {
          const delta = String(ev.text || "");
          patchLast((turn) => ({ ...turn, reasoning: (turn.reasoning || "") + delta }));
        } else if (et === "tool_start") {
          patchLast((turn) => ({
            ...turn,
            tools: [
              ...(turn.tools || []),
              { id: String(ev.id || ev.name), name: String(ev.name || "tool"), target: String(ev.target || ""), status: "running", kind: "tool" },
            ],
          }));
        } else if (et === "tool_result") {
          patchLast((turn) => ({
            ...turn,
            tools: (turn.tools || []).map((x) =>
              x.id === String(ev.id || ev.name)
                ? { ...x, status: String(ev.status || "ok"), target: String(ev.target || x.target) }
                : x,
            ),
          }));
        } else if (et === "subagent_start") {
          patchLast((turn) => ({
            ...turn,
            tools: [...(turn.tools || []), { id: String(ev.id || ev.task), name: `subagent: ${String(ev.agent_type || "")}`, target: String(ev.task || ""), status: "running", kind: "subagent" }],
          }));
        } else if (et === "subagent_done") {
          patchLast((turn) => ({
            ...turn,
            tools: (turn.tools || []).map((x) => (x.id === String(ev.id || ev.task) ? { ...x, status: String(ev.status || "ok") } : x)),
          }));
        }
      });
    } catch (e) {
      patchLast((turn) => ({ ...turn, text: turn.text || `connection error: ${String(e)}` }));
    } finally {
      setBusy(false);
      taRef.current?.focus();
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="flex h-full flex-col bg-bg">
      <div ref={scrollRef} className="scroll-thin flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6">
          {turns.length === 0 && (
            <div className="mt-20 flex flex-col items-center text-center">
              <div className="mb-4 opacity-90"><Mark size={48} /></div>
              <div className="text-xl font-semibold text-text">How can I help?</div>
              <div className="mt-1.5 text-sm text-faint">
                Ask anything — the agent can read files, run commands, search the web, and more.
              </div>
              <div className="mt-6 grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => fill(s)}
                    className="rounded-[var(--radius)] border border-border bg-surface/60 px-3 py-2.5 text-left text-sm text-dim transition-colors hover:border-border-2 hover:bg-surface-2/60 hover:text-text"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t, i) => (
            <Bubble key={i} turn={t} streaming={busy && i === turns.length - 1 && t.role === "assistant"} />
          ))}
        </div>
      </div>

      <div className="border-t border-border bg-surface/40 px-4 py-3 backdrop-blur">
        <div className="mx-auto w-full max-w-3xl">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <div className="flex min-h-9 max-w-full flex-wrap items-center gap-1.5 rounded-[var(--radius)] border border-border bg-surface px-2 py-1 text-xs text-dim">
              <Icon name="models" size={14} className="text-primary" />
              <select
                value={provider}
                onChange={(e) => switchProvider(e.target.value)}
                className="max-w-[150px] bg-transparent font-mono text-xs text-text outline-none"
                title="Provider"
              >
                {!providers.includes(provider) && provider && <option value={provider}>{provider}</option>}
                {providers.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <select
                value={selectedModel}
                onChange={(e) => {
                  const value = e.target.value;
                  if (value === CUSTOM_VALUE) setModel(customModel || model || "");
                  else setModel(value);
                }}
                className="max-w-[220px] bg-transparent font-mono text-xs text-text outline-none"
                title="Model"
              >
                <option value={CUSTOM_VALUE}>custom</option>
                {presets.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              {selectedModel === CUSTOM_VALUE && (
                <input
                  value={customModel}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="model id"
                  className="min-h-7 w-44 max-w-full rounded-[var(--radius)] border border-border bg-surface-2 px-2 font-mono text-xs text-text outline-none placeholder:text-faint focus:border-border-2"
                />
              )}
            </div>
          </div>
          <div className="flex items-end gap-2">
            <textarea
              ref={taRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              rows={1}
              placeholder="Message AEGIS…  (Enter to send, Shift+Enter for newline)"
              className="scroll-thin max-h-40 min-h-[44px] flex-1 resize-none rounded-[var(--radius)] border border-border bg-surface px-3 py-2.5 text-sm text-text outline-none placeholder:text-faint focus:border-border-2"
            />
            <button
              onClick={send}
              disabled={busy || !input.trim()}
              className="flex h-[44px] w-[44px] shrink-0 items-center justify-center rounded-[var(--radius)] bg-primary text-primary-fg transition enabled:hover:opacity-90 disabled:opacity-40"
              title="Send"
            >
              <Icon name={busy ? "refresh" : "send"} size={18} className={busy ? "animate-spin" : ""} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ToolCard({ ev }: { ev: ToolEvent }) {
  const color =
    ev.status === "error" ? "text-danger" : ev.status === "running" ? "text-warning" : "text-success";
  const dot =
    ev.status === "error" ? "bg-danger" : ev.status === "running" ? "bg-warning animate-pulse" : "bg-success";
  return (
    <div className="flex items-start gap-2 rounded-[var(--radius)] border border-border bg-surface-2/60 px-2.5 py-1.5 text-xs">
      <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      <div className="min-w-0 flex-1">
        <span className={`font-mono font-medium ${color}`}>{ev.name}</span>
        {ev.target && <span className="ml-2 truncate text-faint">{ev.target}</span>}
      </div>
    </div>
  );
}

function Bubble({ turn, streaming }: { turn: Turn; streaming: boolean }) {
  if (turn.role === "user") {
    return (
      <div className="mb-5 flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm text-primary-fg">
          {turn.text}
        </div>
      </div>
    );
  }
  return (
    <div className="mb-6">
      {turn.reasoning && (
        <details className="mb-2 text-xs text-faint">
          <summary className="cursor-pointer select-none hover:text-dim">Reasoning</summary>
          <div className="mt-1 whitespace-pre-wrap border-l-2 border-border pl-3 text-dim">{turn.reasoning}</div>
        </details>
      )}
      {(turn.tools?.length ?? 0) > 0 && (
        <div className="mb-2 space-y-1">
          {turn.tools!.map((ev, i) => <ToolCard key={`${ev.id}-${i}`} ev={ev} />)}
        </div>
      )}
      {turn.text ? (
        <Markdown text={turn.text} />
      ) : streaming ? (
        <span className="inline-block h-4 w-2 animate-pulse rounded-sm bg-dim align-middle" />
      ) : null}
    </div>
  );
}
