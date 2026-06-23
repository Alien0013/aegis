// DesktopShell — the chat-first surface for the AEGIS desktop app.
//
// Unlike the admin dashboard (a 20-tab control panel), this is a focused, native-feeling
// chat app: a slim session rail on the left and the real AEGIS terminal filling the rest.
// The Electron app opens straight into `#/app`; the full control panel stays one click away.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useApi } from "../lib/useApi";
import { Icon } from "../components/icons";
import { Mark } from "../components/Mark";
import { ThemeSwitcher } from "../components/ThemeSwitcher";
import { openCommandPalette } from "../components/CommandPalette";
import { Badge, Toaster } from "../components/ui";
import { ago, compact } from "../lib/format";
import { desktop, isDesktop } from "../lib/desktop";
import { GraphicalChat } from "./GraphicalChat";

interface SessionRow {
  id: string;
  title?: string;
  updated_at?: string;
  message_count?: number;
}
interface DesktopStatus {
  active_sessions?: number;
  gateway_running?: boolean;
  gateway_state?: string;
  provider?: string;
  model?: string;
  tools?: number;
  skills?: number;
  provider_error?: string;
  version?: string;
}

export function DesktopShell() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const activeId = params.get("id") || "";
  const { data, reload } = useApi<SessionRow[]>("sessions");
  const cfg = useApi<Record<string, unknown>>("config");
  const status = useApi<DesktopStatus>("status");
  const model = String(cfg.data?.["model.default"] ?? "");
  const provider = String(cfg.data?.["model.provider"] ?? "");
  const [runtime, setRuntime] = useState({ model: "", provider: "" });
  const [chatResetToken, setChatResetToken] = useState(0);
  const [sessionHudOpen, setSessionHudOpen] = useState(false);
  const shownModel = runtime.model || status.data?.model || model;
  const shownProvider = runtime.provider || status.data?.provider || provider;
  const sessions = (data || []).slice(0, 50);
  const gateway = status.data?.gateway_state || (status.data?.gateway_running ? "running" : "offline");
  const ready = !status.error && !status.data?.provider_error;

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeId),
    [sessions, activeId],
  );
  const open = useCallback((id: string) => {
    nav(`/app?id=${encodeURIComponent(id)}`);
    setSessionHudOpen(false);
  }, [nav]);
  const newChat = () => {
    nav("/app");
    setSessionHudOpen(false);
    setChatResetToken((value) => value + 1);
    setTimeout(reload, 500);
  };
  const recoverMissingSession = useCallback(() => {
    nav("/app", { replace: true });
    setChatResetToken((value) => value + 1);
    reload();
  }, [nav, reload]);

  const openAgents = useCallback(() => {
    if (isDesktop && desktop?.openAgentsWindow) {
      desktop.openAgentsWindow();
      return;
    }
    nav("/agents");
  }, [nav]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      if ((event.ctrlKey || event.metaKey) && key === "j") {
        event.preventDefault();
        setSessionHudOpen((openNow) => !openNow);
      } else if (event.key === "Escape") {
        setSessionHudOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex h-full overflow-hidden bg-bg text-text">
      <aside className="flex w-64 shrink-0 flex-col border-r border-border bg-surface/50">
        <div className="flex h-14 items-center gap-2 border-b border-border px-4">
          <Mark size={22} />
          <span className="font-semibold tracking-wide">AEGIS</span>
          <button
            onClick={openCommandPalette}
            title="Command palette (Ctrl/⌘ K)"
            className="ml-auto rounded-[var(--radius)] p-1.5 text-faint transition hover:bg-surface-2 hover:text-text"
          >
            <Icon name="search" size={15} />
          </button>
          <button
            onClick={() => setSessionHudOpen(true)}
            title="Sessions"
            className="rounded-[var(--radius)] p-1.5 text-faint transition hover:bg-surface-2 hover:text-text"
          >
            <Icon name="sessions" size={15} />
          </button>
        </div>

        <div className="p-3">
          <button
            onClick={newChat}
            className="flex w-full items-center justify-center gap-2 rounded-[var(--radius)] bg-primary py-2 font-medium text-primary-fg transition hover:opacity-90"
          >
            <Icon name="plus" size={16} /> New chat
          </button>
        </div>

        <div className="flex items-center justify-between px-4 pb-1 text-xs text-faint">
          <span>Recent</span>
          <button onClick={reload} className="hover:text-primary" title="Refresh sessions">
            <Icon name="refresh" size={13} />
          </button>
        </div>

        <nav className="scroll-thin flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
          {sessions.length === 0 && (
            <div className="px-2 py-8 text-center text-xs text-faint">
              No sessions yet.<br />Start a new chat.
            </div>
          )}
          {sessions.map((s) => (
            <button
              key={s.id}
              onClick={() => open(s.id)}
              className={`w-full rounded-[var(--radius)] px-2.5 py-2 text-left transition ${
                s.id === activeId
                  ? "bg-surface-2 text-text"
                  : "text-dim hover:bg-surface-2/60 hover:text-text"
              }`}
            >
              <div className="truncate text-sm">{compact(s.title || s.id, 30)}</div>
              <div className="text-[11px] text-faint">
                {ago(s.updated_at)}
                {s.message_count ? ` · ${s.message_count} msg` : ""}
              </div>
            </button>
          ))}
        </nav>

        <div className="space-y-2 border-t border-border p-3">
          {shownModel && (
            <div className="flex items-center gap-2 text-xs text-dim" title={`${shownProvider} ${shownModel}`}>
              <Icon name="models" size={13} />
              <span className="truncate">{shownModel}</span>
            </div>
          )}
          <ThemeSwitcher up />
          <button
            onClick={() => nav("/")}
            className="flex w-full items-center gap-2 rounded-[var(--radius)] border border-border px-2.5 py-1.5 text-xs text-dim transition hover:text-primary"
            title="Open the full control panel"
          >
            <Icon name="system" size={14} /> Control Panel
          </button>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1">
        <section className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-bg/80 px-3 backdrop-blur">
            <button
              onClick={() => setSessionHudOpen(true)}
              className="flex min-w-0 flex-1 items-center gap-2 rounded-[var(--radius)] px-2 py-1.5 text-left text-xs text-dim transition hover:bg-surface-2/60 hover:text-text"
              title={activeSession?.title || activeId || "Current session"}
            >
              <Icon name="sessions" size={14} className="shrink-0 text-primary" />
              <span className="truncate">{activeSession?.title || activeId || "New session"}</span>
              {activeSession?.message_count ? (
                <span className="shrink-0 text-faint">{activeSession.message_count} msg</span>
              ) : null}
            </button>
            <div className="hidden items-center gap-1.5 rounded-[var(--radius)] border border-border bg-surface/70 px-2 py-1.5 text-[11px] text-dim lg:flex">
              <span className={ready ? "h-1.5 w-1.5 rounded-full bg-success" : "h-1.5 w-1.5 rounded-full bg-danger"} />
              <span className="truncate">{gateway}</span>
            </div>
            {shownModel && (
              <div className="hidden min-w-0 max-w-[42%] items-center gap-2 rounded-[var(--radius)] border border-border bg-surface/70 px-2 py-1.5 text-xs text-dim sm:flex">
                <Icon name="models" size={13} className="shrink-0 text-primary" />
                <span className="truncate">{shownProvider ? `${shownProvider} / ${shownModel}` : shownModel}</span>
              </div>
            )}
            <button
              onClick={() => nav("/chat")}
              className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius)] border border-border text-faint transition hover:border-border-2 hover:text-text md:flex"
              title="Terminal"
              aria-label="Terminal"
            >
              <Icon name="terminal" size={14} />
            </button>
            <button
              onClick={openCommandPalette}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius)] border border-border text-faint transition hover:border-border-2 hover:text-text"
              title="Command palette"
              aria-label="Command palette"
            >
              <Icon name="command" size={14} />
            </button>
            <button
              onClick={openAgents}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius)] border border-border text-faint transition hover:border-border-2 hover:text-text"
              title={isDesktop ? "Open live agents window" : "Live agents"}
              aria-label="Live agents"
            >
              <Icon name="agents" size={14} />
            </button>
            <button
              onClick={() => nav("/")}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius)] border border-border text-faint transition hover:border-border-2 hover:text-text"
              title="Control panel"
              aria-label="Control panel"
            >
              <Icon name="system" size={14} />
            </button>
          </div>
          <div className="min-h-0 flex-1">
            <GraphicalChat
              sessionId={activeId}
              resetToken={chatResetToken}
              onRuntime={setRuntime}
              onMissingSession={recoverMissingSession}
              onSession={(id) => {
                if (id && id !== activeId) nav(`/app?id=${encodeURIComponent(id)}`, { replace: true });
                reload();
                status.reload();
              }}
            />
          </div>
        </section>
        <DesktopOpsRail
          sessions={sessions}
          activeId={activeId}
          status={status.data || undefined}
          model={shownModel}
          provider={shownProvider}
          ready={ready}
          onOpen={open}
          onNew={newChat}
          onNavigate={(path) => nav(path)}
          onOpenAgents={openAgents}
        />
      </main>
      {sessionHudOpen && (
        <SessionSwitcherHud
          sessions={sessions}
          activeId={activeId}
          onClose={() => setSessionHudOpen(false)}
          onOpen={open}
          onNew={newChat}
        />
      )}
      <Toaster />
    </div>
  );
}

function DesktopOpsRail({
  sessions,
  activeId,
  status,
  model,
  provider,
  ready,
  onOpen,
  onNew,
  onNavigate,
  onOpenAgents,
}: {
  sessions: SessionRow[];
  activeId: string;
  status?: DesktopStatus;
  model: string;
  provider: string;
  ready: boolean;
  onOpen: (id: string) => void;
  onNew: () => void;
  onNavigate: (path: string) => void;
  onOpenAgents: () => void;
}) {
  const activeSession = sessions.find((session) => session.id === activeId);
  const gateway = status?.gateway_state || (status?.gateway_running ? "running" : "offline");
  const recent = sessions.filter((session) => session.id !== activeId).slice(0, 5);
  return (
    <aside className="hidden w-[310px] shrink-0 flex-col border-l border-border bg-surface/38 2xl:flex">
      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="font-mono text-[10px] uppercase tracking-wide text-faint">Operations</div>
            <div className="mt-1 truncate text-sm font-semibold text-text">{gateway}</div>
          </div>
          <Badge tone={ready ? "success" : "danger"}>{ready ? "ready" : "attention"}</Badge>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <ShellMetric label="active" value={String(status?.active_sessions ?? 0)} />
          <ShellMetric label="tools" value={String(status?.tools ?? "-")} />
          <ShellMetric label="skills" value={String(status?.skills ?? "-")} />
          <ShellMetric label="version" value={status?.version ? `v${status.version}` : "-"} />
        </div>
      </div>

      <div className="border-b border-border px-4 py-3">
        <div className="font-mono text-[10px] uppercase tracking-wide text-faint">Runtime</div>
        <div className="mt-2 flex items-start gap-2">
          <Icon name="models" size={15} className="mt-0.5 shrink-0 text-primary" />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-text">{model || "model unavailable"}</div>
            <div className="truncate text-xs text-faint">{provider || "provider not configured"}</div>
          </div>
        </div>
        {status?.provider_error && (
          <div className="mt-3 rounded-[var(--radius)] border border-danger/35 bg-danger/10 p-2 text-xs text-danger">
            {compact(status.provider_error, 140)}
          </div>
        )}
      </div>

      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <div className="font-mono text-[10px] uppercase tracking-wide text-faint">Now</div>
          <button onClick={onNew} className="text-faint transition hover:text-primary" title="New chat">
            <Icon name="plus" size={14} />
          </button>
        </div>
        <button
          onClick={() => activeId ? onOpen(activeId) : onNew()}
          className="mt-2 w-full rounded-[var(--radius)] border border-border bg-surface-2/55 px-3 py-2 text-left transition hover:border-border-2"
        >
          <div className="truncate text-sm font-medium text-text">{activeSession?.title || activeId || "New session"}</div>
          <div className="mt-0.5 text-xs text-faint">
            {activeSession?.message_count ? `${activeSession.message_count} msg` : "ready for input"}
            {activeSession?.updated_at ? ` / ${ago(activeSession.updated_at)}` : ""}
          </div>
        </button>
      </div>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <div className="px-4 pb-1 pt-3 font-mono text-[10px] uppercase tracking-wide text-faint">Recent</div>
        {recent.map((session) => (
          <button
            key={session.id}
            onClick={() => onOpen(session.id)}
            className="flex w-full items-center justify-between gap-3 border-b border-border/70 px-4 py-2.5 text-left transition hover:bg-surface-2/55"
          >
            <span className="min-w-0">
              <span className="block truncate text-sm text-text">{compact(session.title || session.id, 34)}</span>
              <span className="block text-[11px] text-faint">{session.message_count || 0} msg</span>
            </span>
            <span className="shrink-0 text-[11px] text-faint">{ago(session.updated_at)}</span>
          </button>
        ))}
        {!recent.length && <div className="px-4 py-6 text-sm text-faint">No other sessions yet.</div>}
      </div>

      <div className="grid grid-cols-4 gap-px border-t border-border bg-border p-px">
        <ShellNavButton icon="chat" label="Chat" onClick={onNew} />
        <ShellNavButton icon="agents" label="Agents" onClick={onOpenAgents} />
        <ShellNavButton icon="terminal" label="Terminal" onClick={() => onNavigate("/chat")} />
        <ShellNavButton icon="logs" label="Logs" onClick={() => onNavigate("/logs")} />
      </div>
    </aside>
  );
}

function ShellMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-[var(--radius)] border border-border bg-surface-2/55 px-2 py-1.5">
      <div className="truncate font-mono text-sm font-semibold text-text">{value}</div>
      <div className="truncate text-[10px] uppercase tracking-wide text-faint">{label}</div>
    </div>
  );
}

function ShellNavButton({ icon, label, onClick }: { icon: string; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex min-h-12 flex-col items-center justify-center gap-1 bg-surface px-1 text-faint transition hover:bg-surface-2 hover:text-text" title={label}>
      <Icon name={icon} size={15} />
      <span className="text-[10px]">{label}</span>
    </button>
  );
}

function SessionSwitcherHud({
  sessions,
  activeId,
  onClose,
  onOpen,
  onNew,
}: {
  sessions: SessionRow[];
  activeId: string;
  onClose: () => void;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return sessions.slice(0, 20);
    return sessions
      .filter((session) => `${session.title || ""} ${session.id}`.toLowerCase().includes(needle))
      .slice(0, 20);
  }, [query, sessions]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/45 px-3 pt-[12vh] backdrop-blur-sm">
      <button className="absolute inset-0 cursor-default" onClick={onClose} aria-label="Close sessions" />
      <div className="relative flex w-full max-w-2xl flex-col overflow-hidden rounded-[var(--radius)] border border-border bg-bg shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <Icon name="sessions" size={16} className="text-primary" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Find session"
            className="min-h-9 flex-1 bg-transparent text-sm text-text outline-none placeholder:text-faint"
          />
          <button
            onClick={onNew}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius)] border border-border text-faint transition hover:border-border-2 hover:text-text"
            title="New chat"
            aria-label="New chat"
          >
            <Icon name="plus" size={14} />
          </button>
          <button
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius)] border border-border text-faint transition hover:border-border-2 hover:text-text"
            title="Close"
            aria-label="Close"
          >
            <Icon name="x" size={14} />
          </button>
        </div>
        <div className="scroll-thin max-h-[56vh] overflow-y-auto p-2">
          {filtered.length === 0 && (
            <div className="px-3 py-10 text-center text-sm text-faint">No matching sessions</div>
          )}
          {filtered.map((session) => (
            <button
              key={session.id}
              onClick={() => onOpen(session.id)}
              className={`mb-1 grid w-full grid-cols-[1fr_auto] items-center gap-3 rounded-[var(--radius)] px-3 py-2.5 text-left transition ${
                session.id === activeId
                  ? "bg-surface-2 text-text"
                  : "text-dim hover:bg-surface-2/60 hover:text-text"
              }`}
            >
              <span className="min-w-0">
                <span className="block truncate text-sm">{compact(session.title || session.id, 72)}</span>
                <span className="mt-0.5 block truncate font-mono text-[11px] text-faint">{session.id}</span>
              </span>
              <span className="text-right text-[11px] text-faint">
                <span className="block">{ago(session.updated_at)}</span>
                {session.message_count ? <span className="block">{session.message_count} msg</span> : null}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
