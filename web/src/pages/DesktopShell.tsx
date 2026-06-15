// DesktopShell — the chat-first surface for the AEGIS desktop app.
//
// Unlike the admin dashboard (a 20-tab control panel), this is a focused, native-feeling
// chat app: a slim session rail on the left and the real AEGIS terminal filling the rest.
// The Electron app opens straight into `#/app`; the full control panel stays one click away.

import { useNavigate, useSearchParams } from "react-router-dom";
import { useApi } from "../lib/useApi";
import { Icon } from "../components/icons";
import { ThemeSwitcher } from "../components/ThemeSwitcher";
import { Toaster } from "../components/ui";
import { ago, compact } from "../lib/format";
import { Chat } from "./Chat";

interface SessionRow {
  id: string;
  title?: string;
  updated_at?: string;
  message_count?: number;
}

function Mark() {
  return (
    <svg width="22" height="22" viewBox="0 0 256 256" aria-hidden>
      <defs>
        <linearGradient id="dsmark" x1="78" y1="56" x2="178" y2="208" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#f2c878" />
          <stop offset="0.55" stopColor="#d8913f" />
          <stop offset="1" stopColor="#7ecf8f" />
        </linearGradient>
      </defs>
      <path d="M128 50 196 76 V128 C196 168 166 196 128 210 C90 196 60 168 60 128 V76 Z" fill="url(#dsmark)" />
      <path d="M104 104 L128 128 L104 152" fill="none" stroke="#14100a" strokeWidth="13" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M134 152 H158" fill="none" stroke="#14100a" strokeWidth="13" strokeLinecap="round" />
    </svg>
  );
}

export function DesktopShell() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const activeId = params.get("id") || "";
  const { data, reload } = useApi<SessionRow[]>("sessions");
  const cfg = useApi<Record<string, unknown>>("config");
  const model = String(cfg.data?.["model.default"] ?? "");
  const provider = String(cfg.data?.["model.provider"] ?? "");
  const sessions = (data || []).slice(0, 50);

  const open = (id: string) => nav(`/app?id=${encodeURIComponent(id)}`);
  const newChat = () => {
    nav("/app");
    setTimeout(reload, 500);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-bg text-text">
      <aside className="flex w-64 shrink-0 flex-col border-r border-border bg-surface/50">
        <div className="flex h-14 items-center gap-2 border-b border-border px-4">
          <Mark />
          <span className="font-semibold tracking-wide">AEGIS</span>
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
          {model && (
            <div className="flex items-center gap-2 text-xs text-dim" title={`${provider} ${model}`}>
              <Icon name="models" size={13} />
              <span className="truncate">{model}</span>
            </div>
          )}
          <div className="flex items-center justify-between">
            <ThemeSwitcher />
            <button
              onClick={() => nav("/")}
              className="flex items-center gap-1.5 text-xs text-dim transition hover:text-primary"
              title="Open the full control panel"
            >
              <Icon name="system" size={14} /> Control Panel
            </button>
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        <Chat />
      </main>
      <Toaster />
    </div>
  );
}
