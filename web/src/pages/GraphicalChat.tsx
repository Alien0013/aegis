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
import { ToolCall, type ToolEntry } from "../components/ToolCall";

const SUGGESTIONS = [
  "Summarize this repository's structure",
  "What can you do?",
  "Run the tests and report failures",
  "Find and explain the entry point",
];

type ToolStatus = ToolEntry["status"];

interface ToolEvent extends ToolEntry {
  id: string;
  name: string;
  target: string;
  status: ToolStatus;
  kind?: string; // tool | subagent
  diff?: string;
}

interface Turn {
  role: "user" | "assistant";
  text: string;
  reasoning?: string;
  tools?: ToolEvent[];
}

type BusyAction = "queue" | "steer" | "interrupt";

interface RunStatus {
  phase: string;
  startedAt: number;
  sessionId?: string;
  runId?: string;
  traceId?: string;
  turnId?: string;
  iteration?: number;
  maxIterations?: number;
  providerCalls: number;
  toolCalls: number;
  toolErrors: number;
  activeProvider?: string;
  activeTool?: string;
  lastTool?: string;
  compactions: number;
  note?: string;
}

interface BrowserManageResponse {
  connected?: boolean;
  url?: string;
  messages?: string[];
  error?: string;
}

interface ModelCapabilities {
  tool_calls?: boolean;
  streaming?: boolean;
  images?: boolean;
  reasoning_effort?: boolean;
  reasoning_stream?: boolean;
  response_state?: boolean;
  response_cancel?: boolean;
  dynamic_tools?: boolean;
  fast_mode?: boolean;
}

interface ModelRow {
  id: string;
  label?: string;
  capabilities?: ModelCapabilities;
  capability_summary?: string;
  context_length?: number;
}

interface ModelsPayload {
  provider?: string;
  model?: string;
  providers?: string[];
  presets?: Record<string, string[]>;
  preset_rows?: Record<string, ModelRow[]>;
}

interface SessionPayload {
  found?: boolean;
  error?: string;
  messages?: { role: string; content: string }[];
  meta?: {
    model?: string;
    provider?: string;
    runtime_controls?: Record<string, unknown>;
  };
}

const MODEL_KEY = "aegis.chat.composer.model";
const PROVIDER_KEY = "aegis.chat.composer.provider";
const MODEL_PRESETS_KEY = "aegis.chat.modelPresets";
const CUSTOM_VALUE = "__custom";
const REASONING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh"] as const;
type ReasoningLevel = typeof REASONING_LEVELS[number];
type ModelPreset = { effort?: ReasoningLevel; fast?: boolean };

function stored(key: string): string {
  try { return localStorage.getItem(key) || ""; } catch { return ""; }
}

function persist(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch { /* ignore storage failures */ }
}

function presetKey(provider: string, model: string): string {
  const p = provider.trim();
  const m = model.trim();
  return p && m ? `${p}::${m}` : "";
}

function normalizeReasoning(value: unknown): ReasoningLevel | "" {
  const v = String(value || "").trim().toLowerCase();
  return (REASONING_LEVELS as readonly string[]).includes(v) ? (v as ReasoningLevel) : "";
}

function normalizeToolStatus(value: unknown, fallback: ToolStatus = "ok"): ToolStatus {
  const text = String(value || "").trim().toLowerCase();
  if (text === "running" || text === "ok" || text === "error") return text;
  if (text === "failed" || text === "fail") return "error";
  return fallback;
}

function renderToolArgs(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "";
  }
}

function isDiffLike(text: string): boolean {
  return /^(diff --git|@@ |[+-]{3}\s|[+-][^\n])/m.test(text);
}

function diffPreviewFromArgs(name: string, args: unknown): string {
  if (!args || typeof args !== "object" || Array.isArray(args)) return "";
  const row = args as Record<string, unknown>;
  const toolName = name.toLowerCase();
  const patch = String(row.patch || row.diff || "");
  if ((toolName.includes("patch") || toolName.includes("apply")) && isDiffLike(patch)) return patch;
  if (toolName === "edit_file" || toolName.includes("edit")) {
    const path = String(row.path || row.file_path || "file");
    const oldText = String(row.old_string || "");
    const newText = String(row.new_string || "");
    if (oldText || newText) {
      const oldLines = oldText.split("\n").slice(0, 80);
      const newLines = newText.split("\n").slice(0, 80);
      return [
        `--- ${path}`,
        `+++ ${path}`,
        "@@ requested replacement @@",
        ...oldLines.map((line) => `-${line}`),
        ...newLines.map((line) => `+${line}`),
      ].join("\n");
    }
  }
  return "";
}

function loadModelPresets(): Record<string, ModelPreset> {
  try {
    const raw = localStorage.getItem(MODEL_PRESETS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: Record<string, ModelPreset> = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (!key || !value || typeof value !== "object" || Array.isArray(value)) continue;
      const row = value as Record<string, unknown>;
      const effort = normalizeReasoning(row.effort);
      out[key] = {
        ...(effort ? { effort } : {}),
        ...(typeof row.fast === "boolean" ? { fast: row.fast } : {}),
      };
    }
    return out;
  } catch {
    return {};
  }
}

function saveModelPresets(presets: Record<string, ModelPreset>): void {
  try { localStorage.setItem(MODEL_PRESETS_KEY, JSON.stringify(presets)); } catch { /* ignore storage failures */ }
}

export function GraphicalChat({
  sessionId,
  resetToken,
  onSession,
  onMissingSession,
  onRuntime,
}: {
  sessionId?: string;
  resetToken?: string | number;
  onSession?: (id: string) => void;
  onMissingSession?: (id: string) => void;
  onRuntime?: (runtime: { model: string; provider: string }) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyAction, setBusyAction] = useState<BusyAction>("queue");
  const [queuedPrompts, setQueuedPrompts] = useState<string[]>([]);
  const queuedRef = useRef<string[]>([]);
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null);
  const [statusTick, setStatusTick] = useState(0);
  const [sid, setSid] = useState(sessionId || "");
  const [modelData, setModelData] = useState<ModelsPayload | null>(null);
  const [model, setModelState] = useState(() => stored(MODEL_KEY));
  const [provider, setProviderState] = useState(() => stored(PROVIDER_KEY));
  const [modelPresets, setModelPresets] = useState<Record<string, ModelPreset>>(() => loadModelPresets());
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningLevel>("medium");
  const [fastMode, setFastMode] = useState(false);
  const [runtimeDirty, setRuntimeDirty] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const presetHydratedRef = useRef(false);
  const streamRef = useRef<{ token: number; controller: AbortController | null }>({ token: 0, controller: null });

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

  const rowsForProvider = (name: string) => (modelData?.preset_rows || {})[name] || [];

  const reasoningForModel = (nextProvider: string, nextModel: string): ReasoningLevel => {
    const row = rowsForProvider(nextProvider).find((entry) => entry.id === nextModel);
    if (row && row.capabilities?.reasoning_effort !== true) return "off";
    const saved = modelPresets[presetKey(nextProvider, nextModel)]?.effort;
    return saved || "medium";
  };

  const applyReasoningForModel = (nextProvider: string, nextModel: string, dirty = true) => {
    setReasoningEffort(reasoningForModel(nextProvider, nextModel));
    if (dirty) setRuntimeDirty(true);
  };

  const changeReasoning = (value: string) => {
    const next = normalizeReasoning(value) || "medium";
    setReasoningEffort(next);
    setRuntimeDirty(true);
    const key = presetKey(provider, model);
    if (!key) return;
    setModelPresets((current) => {
      const updated = { ...current, [key]: { ...(current[key] || {}), effort: next } };
      saveModelPresets(updated);
      return updated;
    });
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

  useEffect(() => {
    if (!busy) return;
    const id = window.setInterval(() => setStatusTick((value) => value + 1), 1000);
    return () => window.clearInterval(id);
  }, [busy]);

  useEffect(() => () => {
    streamRef.current.token += 1;
    streamRef.current.controller?.abort();
    streamRef.current.controller = null;
  }, []);

  useEffect(() => {
    if (presetHydratedRef.current || sessionId || !modelData || !provider || !model) return;
    presetHydratedRef.current = true;
    setReasoningEffort(reasoningForModel(provider, model));
    setFastMode(fastForModel(provider, model));
  }, [model, modelData, provider, sessionId]);

  // Load a session's transcript when one is opened from the rail, or hard-reset
  // when the shell asks for a fresh chat while already on the empty route.
  useEffect(() => {
    let cancelled = false;
    streamRef.current.token += 1;
    streamRef.current.controller?.abort();
    streamRef.current.controller = null;
    setBusy(false);
    setSid(sessionId || "");
    setTurns([]);
    setRunStatus(null);
    queuedRef.current = [];
    setQueuedPrompts([]);
    setRuntimeDirty(false);
    if (!sessionId) return;
    fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      headers: { "X-Aegis-Token": localStorage.getItem("aegis_token") || "" },
    })
      .then((r) => (r.ok ? r.json() : { found: r.status === 404 ? false : undefined, error: `session load failed: ${r.status}` }))
      .then((data: SessionPayload | null) => {
        if (cancelled) return;
        if (!data || data.found === false) {
          setSid("");
          setTurns([]);
          onMissingSession?.(sessionId);
          return;
        }
        if (!data.messages) return;
        const controls = data.meta?.runtime_controls || {};
        const sessionModel = String(controls.model || data.meta?.model || "");
        const sessionProvider = String(controls.provider || data.meta?.provider || "");
        const sessionReasoning = normalizeReasoning(controls.reasoning_effort);
        const sessionServiceTier = String(controls.service_tier || "").trim().toLowerCase();
        if (sessionModel) setModel(sessionModel, false);
        if (sessionProvider) setProvider(sessionProvider, false);
        if (sessionReasoning) setReasoningEffort(sessionReasoning);
        if (sessionServiceTier) setFastMode(sessionServiceTier === "priority");
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
  }, [onMissingSession, resetToken, sessionId]);

  const providers = modelData?.providers || (provider ? [provider] : []);
  const presetRows = rowsForProvider(provider);
  const presets = presetRows.length ? presetRows.map((row) => row.id) : (modelData?.presets || {})[provider] || [];
  const knownModel = presets.includes(model);
  const selectedModel = knownModel ? model : CUSTOM_VALUE;
  const customModel = model && !knownModel ? model : "";
  const selectedRow = presetRows.find((row) => row.id === model);
  const supportsReasoning = selectedRow ? selectedRow.capabilities?.reasoning_effort === true : true;
  const reasoningDisabled = knownModel && !supportsReasoning;

  const supportsFast = selectedRow ? selectedRow.capabilities?.fast_mode === true : true;
  const fastDisabled = knownModel && !supportsFast;

  const fastForModel = (nextProvider: string, nextModel: string): boolean => {
    const row = rowsForProvider(nextProvider).find((entry) => entry.id === nextModel);
    if (row && row.capabilities?.fast_mode !== true) return false;
    return modelPresets[presetKey(nextProvider, nextModel)]?.fast === true;
  };

  const applyFastForModel = (nextProvider: string, nextModel: string, dirty = true) => {
    setFastMode(fastForModel(nextProvider, nextModel));
    if (dirty) setRuntimeDirty(true);
  };

  const changeFastMode = (next: boolean) => {
    setFastMode(next);
    setRuntimeDirty(true);
    const key = presetKey(provider, model);
    if (!key) return;
    setModelPresets((current) => {
      const updated = { ...current, [key]: { ...(current[key] || {}), fast: next } };
      saveModelPresets(updated);
      return updated;
    });
  };

  const switchProvider = (nextProvider: string) => {
    setProvider(nextProvider);
    const nextRows = rowsForProvider(nextProvider);
    const nextPresets = nextRows.length ? nextRows.map((row) => row.id) : (modelData?.presets || {})[nextProvider] || [];
    const nextModel = nextPresets[0] || model;
    if (nextPresets.length) setModel(nextModel);
    applyReasoningForModel(nextProvider, nextModel);
    applyFastForModel(nextProvider, nextModel);
  };

  const sendRuntime = useMemo(() => {
    const shouldSend = !sid || runtimeDirty;
    if (!shouldSend || !model.trim()) return {};
    return {
      model: model.trim(),
      ...(provider.trim() ? { provider: provider.trim() } : {}),
      ...((supportsReasoning || reasoningEffort === "off") ? { reasoning: reasoningEffort } : {}),
      fast: supportsFast ? fastMode : false,
    };
  }, [fastMode, model, provider, reasoningEffort, runtimeDirty, sid, supportsFast, supportsReasoning]);

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
      if (!t.length) return t;
      const copy = t.slice();
      copy[copy.length - 1] = fn(copy[copy.length - 1]);
      return copy;
    });

  const setQueue = (items: string[]) => {
    queuedRef.current = items;
    setQueuedPrompts(items);
  };

  const updateRunStatus = (updater: (current: RunStatus) => RunStatus) => {
    setRunStatus((current) => updater(current || {
      phase: "running",
      startedAt: Date.now(),
      sessionId: sid || undefined,
      providerCalls: 0,
      toolCalls: 0,
      toolErrors: 0,
      compactions: 0,
    }));
  };

  const applyStreamFrameToStatus = (data: Record<string, unknown>) => {
    const type = String(data.type || "");
    if (type === "start") {
      const session = String(data.session_id || sid || "");
      setRunStatus({
        phase: "starting",
        startedAt: Date.now(),
        sessionId: session || undefined,
        providerCalls: 0,
        toolCalls: 0,
        toolErrors: 0,
        compactions: 0,
      });
      return;
    }
    if (type === "final" || type === "cancelled" || type === "error") {
      updateRunStatus((current) => ({
        ...current,
        phase: type === "final" ? "completed" : type,
        sessionId: String(data.session_id || current.sessionId || "") || undefined,
        runId: String(data.run_id || current.runId || "") || undefined,
        traceId: String(data.trace_id || current.traceId || "") || undefined,
        turnId: String(data.turn_id || current.turnId || "") || undefined,
        activeProvider: "",
        activeTool: "",
      }));
      return;
    }
    if (type !== "event") return;
    const ev = (data.event || {}) as Record<string, unknown>;
    const et = String(ev.type || "");
    updateRunStatus((current) => {
      const next: RunStatus = {
        ...current,
        runId: String(ev.run_id || current.runId || "") || undefined,
        traceId: String(ev.trace_id || current.traceId || "") || undefined,
        turnId: String(ev.turn_id || current.turnId || "") || undefined,
      };
      if (et === "iteration") {
        next.phase = "thinking";
        next.iteration = Number(ev.n || 0) || undefined;
        next.maxIterations = Number(ev.max || 0) || undefined;
      } else if (et === "provider_start") {
        const p = String(ev.provider || "provider");
        const m = String(ev.model || "");
        next.phase = "model";
        next.activeProvider = m ? `${p}/${m}` : p;
        next.providerCalls += 1;
      } else if (et === "provider_end") {
        next.activeProvider = "";
      } else if (et === "tool_start") {
        next.phase = "tool";
        next.activeTool = String(ev.name || "tool");
        next.lastTool = next.activeTool;
        next.toolCalls += 1;
      } else if (et === "tool_result") {
        next.phase = "tool result";
        next.lastTool = String(ev.name || next.lastTool || "tool");
        next.activeTool = "";
        if (normalizeToolStatus(ev.status, "ok") === "error") next.toolErrors += 1;
      } else if (et === "subagent_start") {
        next.phase = "subagent";
        next.activeTool = String(ev.agent_type || "subagent");
        next.toolCalls += 1;
      } else if (et === "subagent_done") {
        next.phase = "subagent done";
        next.activeTool = "";
      } else if (et === "compacting" || et === "compacted") {
        next.phase = et;
        if (et === "compacted") next.compactions += 1;
      } else if (et === "budget_exhausted") {
        next.phase = "budget";
        next.note = "step budget reached";
      } else if (et === "assistant_delta" || et === "assistant_message") {
        next.phase = "writing";
      } else if (et === "error") {
        next.phase = "error";
      }
      return next;
    });
  };

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

  const controlActiveRun = async (action: "steer" | "interrupt", text?: string) => {
    await post("chat/control", {
      action,
      session_id: sid || undefined,
      run_id: runStatus?.runId || undefined,
      text,
    });
  };

  const submitWhileBusy = async (text: string) => {
    if (busyAction === "queue") {
      setQueue([...queuedRef.current, text]);
      setInput("");
      updateRunStatus((current) => ({ ...current, note: `${queuedRef.current.length + 1} queued` }));
      return;
    }
    if (busyAction === "steer") {
      setInput("");
      try {
        await controlActiveRun("steer", text);
        updateRunStatus((current) => ({ ...current, note: `steer: ${text.slice(0, 80)}` }));
      } catch (e) {
        updateRunStatus((current) => ({ ...current, note: `steer failed: ${e instanceof Error ? e.message : String(e)}` }));
      }
      return;
    }
    setInput("");
    setQueue([text, ...queuedRef.current]);
    try {
      await controlActiveRun("interrupt", text);
    } catch {
      // Closing the stream still asks the backend to cancel through disconnect handling.
    }
    streamRef.current.token += 1;
    streamRef.current.controller?.abort();
    streamRef.current.controller = null;
    setBusy(false);
    patchLast((turn) => (
      turn.role === "assistant" && !turn.text
        ? { ...turn, text: "Interrupted. Starting the replacement prompt." }
        : turn
    ));
    window.setTimeout(() => {
      const next = queuedRef.current.shift();
      setQueuedPrompts([...queuedRef.current]);
      if (next) void sendText(next);
    }, 0);
  };

  const sendText = async (text: string) => {
    if (!text) return;
    if (isBrowserSlash(text)) {
      await runBrowserSlash(text);
      return;
    }
    setInput("");
    setBusy(true);
    setTurns((t) => [...t, { role: "user", text }, { role: "assistant", text: "", tools: [] }]);
    streamRef.current.controller?.abort();
    const controller = new AbortController();
    const token = streamRef.current.token + 1;
    streamRef.current = { token, controller };
    const streamActive = () =>
      streamRef.current.token === token
      && streamRef.current.controller === controller
      && !controller.signal.aborted;

    try {
      await postStream("chat/stream", { message: text, session_id: sid || undefined, ...sendRuntime }, (data) => {
        if (!streamActive()) return;
        applyStreamFrameToStatus(data as Record<string, unknown>);
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
          const name = String(ev.name || "tool");
          const argsDiff = diffPreviewFromArgs(name, ev.args);
          patchLast((turn) => ({
            ...turn,
            tools: [
              ...(turn.tools || []),
              {
                id: String(ev.id || ev.name),
                name,
                target: String(ev.target || ""),
                args: renderToolArgs(ev.args),
                preview: argsDiff || String(ev.preview || ""),
                summary: String(ev.summary || ""),
                diff: argsDiff,
                status: "running",
                startedAt: Date.now(),
                kind: "tool",
              },
            ],
          }));
        } else if (et === "tool_result") {
          patchLast((turn) => ({
            ...turn,
            tools: (turn.tools || []).map((x) =>
              x.id === String(ev.id || ev.name)
                ? {
                    ...x,
                    status: normalizeToolStatus(ev.status, "ok"),
                    target: String(ev.target || x.target),
                    preview: String(ev.preview || x.diff || x.preview || ""),
                    summary: String(ev.summary || x.summary || ""),
                    error: normalizeToolStatus(ev.status, "ok") === "error" ? String(ev.summary || ev.preview || x.error || "") : x.error,
                    completedAt: Date.now(),
                  }
                : x,
            ),
          }));
        } else if (et === "subagent_start") {
          patchLast((turn) => ({
            ...turn,
            tools: [...(turn.tools || []), {
              id: String(ev.id || ev.task),
              name: `subagent: ${String(ev.agent_type || "")}`,
              target: String(ev.task || ""),
              status: "running",
              startedAt: Date.now(),
              kind: "subagent",
            }],
          }));
        } else if (et === "subagent_done") {
          patchLast((turn) => ({
            ...turn,
            tools: (turn.tools || []).map((x) => (x.id === String(ev.id || ev.task) ? {
              ...x,
              status: normalizeToolStatus(ev.status, "ok"),
              completedAt: Date.now(),
            } : x)),
          }));
        } else if (et === "subagent_text") {
          const id = String(ev.subagent_id || ev.id || ev.task || "subagent");
          const delta = String(ev.text || "");
          patchLast((turn) => {
            const tools = [...(turn.tools || [])];
            const idx = tools.findIndex((x) => x.id === id);
            if (idx >= 0) {
              const target = `${tools[idx].target || ""}${delta}`.slice(-900);
              tools[idx] = {
                ...tools[idx],
                target,
                status: normalizeToolStatus(ev.status, tools[idx].status || "running"),
              };
            } else {
              tools.push({
                id,
                name: `subagent: ${String(ev.agent_type || "")}`,
                target: delta.slice(-900),
                status: "running",
                startedAt: Date.now(),
                kind: "subagent",
              });
            }
            return { ...turn, tools };
          });
        }
      }, { signal: controller.signal });
    } catch (e) {
      if (streamActive()) {
        patchLast((turn) => ({ ...turn, text: turn.text || `connection error: ${String(e)}` }));
      }
    } finally {
      if (streamRef.current.token === token) {
        streamRef.current.controller = null;
        setBusy(false);
        taRef.current?.focus();
        const next = queuedRef.current.shift();
        setQueuedPrompts([...queuedRef.current]);
        if (next) window.setTimeout(() => void sendText(next), 0);
      }
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text) return;
    if (busy) {
      await submitWhileBusy(text);
      return;
    }
    await sendText(text);
  };

  const stopStream = () => {
    if (!busy) return;
    void controlActiveRun("interrupt").catch(() => {});
    streamRef.current.token += 1;
    streamRef.current.controller?.abort();
    streamRef.current.controller = null;
    setBusy(false);
    patchLast((turn) => (
      turn.role === "assistant" && !turn.text
        ? { ...turn, text: "Stopped." }
        : turn
    ));
    taRef.current?.focus();
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
          <RunStatusBar
            busy={busy}
            action={busyAction}
            setAction={setBusyAction}
            queued={queuedPrompts.length}
            status={runStatus}
            sessionId={sid}
            tick={statusTick}
          />
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
                  else {
                    setModel(value);
                    applyReasoningForModel(provider, value);
                    applyFastForModel(provider, value);
                  }
                }}
                className="max-w-[220px] bg-transparent font-mono text-xs text-text outline-none"
                title="Model"
              >
                <option value={CUSTOM_VALUE}>custom</option>
                {presets.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              <select
                value={reasoningDisabled ? "off" : reasoningEffort}
                onChange={(e) => changeReasoning(e.target.value)}
                disabled={reasoningDisabled}
                className="max-w-[120px] bg-transparent font-mono text-xs text-text outline-none disabled:text-faint"
                title={reasoningDisabled ? "Reasoning is not advertised for this model" : "Reasoning effort"}
              >
                {REASONING_LEVELS.map((level) => <option key={level} value={level}>{level}</option>)}
              </select>
              <button
                type="button"
                onClick={() => changeFastMode(!fastMode)}
                disabled={fastDisabled}
                aria-pressed={supportsFast && fastMode}
                title={
                  fastDisabled
                    ? "Fast mode is not advertised for this model"
                    : fastMode ? "Fast mode priority" : "Fast mode normal"
                }
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--radius)] border transition-colors ${
                  supportsFast && fastMode
                    ? "border-warning/50 bg-warning/15 text-warning"
                    : "border-border bg-surface-2 text-faint hover:border-border-2 hover:text-text"
                } disabled:cursor-not-allowed disabled:opacity-40`}
              >
                <Icon name="zap" size={14} />
              </button>
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
              placeholder={busy ? "Queue, steer, or interrupt this run..." : "Message AEGIS...  (Enter to send, Shift+Enter for newline)"}
              className="scroll-thin max-h-40 min-h-[44px] flex-1 resize-none rounded-[var(--radius)] border border-border bg-surface px-3 py-2.5 text-sm text-text outline-none placeholder:text-faint focus:border-border-2"
            />
            {busy ? (
              <button
                onClick={stopStream}
                className="flex h-[44px] w-[44px] shrink-0 items-center justify-center rounded-[var(--radius)] border border-danger/45 text-danger transition hover:bg-danger/10"
                title="Stop"
                aria-label="Stop response"
              >
                <Icon name="x" size={18} />
              </button>
            ) : (
              <button
                onClick={send}
                disabled={!input.trim()}
                className="flex h-[44px] w-[44px] shrink-0 items-center justify-center rounded-[var(--radius)] bg-primary text-primary-fg transition enabled:hover:opacity-90 disabled:opacity-40"
                title="Send"
              >
                <Icon name="send" size={18} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function elapsedText(startedAt?: number, tick = 0): string {
  void tick;
  if (!startedAt) return "0s";
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

function RunStatusBar({
  busy,
  action,
  setAction,
  queued,
  status,
  sessionId,
  tick,
}: {
  busy: boolean;
  action: BusyAction;
  setAction: (action: BusyAction) => void;
  queued: number;
  status: RunStatus | null;
  sessionId: string;
  tick: number;
}) {
  const elapsed = elapsedText(status?.startedAt, tick);
  const longRunning = busy && status?.startedAt && Date.now() - status.startedAt > 30000;
  const chips = [
    status?.phase || (busy ? "running" : "ready"),
    status?.iteration && status.maxIterations ? `iter ${status.iteration}/${status.maxIterations}` : "",
    status?.activeProvider ? `model ${status.activeProvider}` : "",
    status?.activeTool ? `tool ${status.activeTool}` : status?.lastTool ? `last ${status.lastTool}` : "",
    status?.providerCalls ? `${status.providerCalls} model call${status.providerCalls === 1 ? "" : "s"}` : "",
    status?.toolCalls ? `${status.toolCalls} tool${status.toolCalls === 1 ? "" : "s"}${status.toolErrors ? ` / ${status.toolErrors} err` : ""}` : "",
    status?.compactions ? `${status.compactions} compression${status.compactions === 1 ? "" : "s"}` : "",
    queued ? `${queued} queued` : "",
  ].filter(Boolean);

  return (
    <div className="mb-2 rounded-[var(--radius)] border border-border bg-surface px-2.5 py-2 text-xs text-dim">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center gap-1 font-medium ${busy ? "text-primary" : "text-faint"}`}>
          <Icon name={busy ? "activity" : "check"} size={13} />
          {busy ? elapsed : "idle"}
        </span>
        {chips.map((chip) => (
          <span key={chip} className="rounded-[var(--radius)] bg-surface-2 px-2 py-0.5 text-[11px] text-dim">
            {chip}
          </span>
        ))}
        {status?.note && <span className="truncate text-[11px] text-faint">{status.note}</span>}
        <span className="ml-auto truncate font-mono text-[11px] text-faint">
          {status?.runId ? `run ${status.runId.slice(0, 12)} · ` : ""}
          {sessionId ? `session ${sessionId.slice(0, 18)}` : "new session"}
        </span>
      </div>
      {busy && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {(["queue", "steer", "interrupt"] as BusyAction[]).map((mode) => (
            <button
              key={mode}
              onClick={() => setAction(mode)}
              className={`rounded-[var(--radius)] border px-2 py-1 text-[11px] capitalize transition ${
                action === mode
                  ? "border-primary/60 bg-primary/15 text-primary"
                  : "border-border bg-bg text-faint hover:text-text"
              }`}
            >
              {mode}
            </button>
          ))}
          {longRunning && (
            <span className="ml-1 text-[11px] text-warning">
              Long run active: send guidance with steer, or interrupt with a replacement prompt.
            </span>
          )}
        </div>
      )}
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
        <div className="mb-2 space-y-1.5">
          {turn.tools!.map((ev, i) => <ToolCall key={`${ev.id}-${i}`} tool={ev} />)}
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
