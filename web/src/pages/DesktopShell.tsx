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
import { Toaster } from "../components/ui";
import { ago, compact } from "../lib/format";
import { GraphicalChat } from "./GraphicalChat";

interface SessionRow {
  id: string;
  title?: string;
  updated_at?: string;
  message_count?: number;
}

export function DesktopShell() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const activeId = params.get("id") || "";
  const { data, reload } = useApi<SessionRow[]>("sessions");
  const cfg = useApi<Record<string, unknown>>("config");
  const model = String(cfg.data?.["model.default"] ?? "");
  const provider = String(cfg.data?.["model.provider"] ?? "");
  const [runtime, setRuntime] = useState({ model: "", provider: "" });
  const [chatResetToken, setChatResetToken] = useState(0);
  const [sessionHudOpen, setSessionHudOpen] = useState(false);
  const shownModel = runtime.model || model;
  const shownProvider = runtime.provider || provider;
  const sessions = (data || []).slice(0, 50);

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

      <main className="flex min-w-0 flex-1 flex-col">
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
          {shownModel && (
            <div className="hidden min-w-0 max-w-[42%] items-center gap-2 rounded-[var(--radius)] border border-border bg-surface/70 px-2 py-1.5 text-xs text-dim sm:flex">
              <Icon name="models" size={13} className="shrink-0 text-primary" />
              <span className="truncate">{shownProvider ? `${shownProvider} / ${shownModel}` : shownModel}</span>
            </div>
          )}
          <button
            onClick={openCommandPalette}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius)] border border-border text-faint transition hover:border-border-2 hover:text-text"
            title="Command palette"
            aria-label="Command palette"
          >
            <Icon name="command" size={14} />
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
            }}
          />
        </div>
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
