// DesktopShell — the chat-first surface for the AEGIS desktop app.
//
// Unlike the admin dashboard (a 20-tab control panel), this is a focused, native-feeling
// chat app: a slim session rail on the left and the real AEGIS terminal filling the rest.
// The Electron app opens straight into `#/app`; the full control panel stays one click away.

import { useCallback, useState } from "react";
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
  const shownModel = runtime.model || model;
  const shownProvider = runtime.provider || provider;
  const sessions = (data || []).slice(0, 50);

  const open = (id: string) => nav(`/app?id=${encodeURIComponent(id)}`);
  const newChat = () => {
    nav("/app");
    setChatResetToken((value) => value + 1);
    setTimeout(reload, 500);
  };
  const recoverMissingSession = useCallback(() => {
    nav("/app", { replace: true });
    setChatResetToken((value) => value + 1);
    reload();
  }, [nav, reload]);

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

      <main className="min-w-0 flex-1">
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
      </main>
      <Toaster />
    </div>
  );
}
