// App shell: sidebar + top bar + routed content. Routes come from lib/nav so the
// sidebar and router never drift. Pages not yet rebuilt fall back to Placeholder.

import { lazy, Suspense, useEffect, useState } from "react";
import { HashRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { ThemeSwitcher } from "./components/ThemeSwitcher";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { TitleBar } from "./components/TitleBar";
import { CommandPalette, openCommandPalette } from "./components/CommandPalette";
import { Icon } from "./components/icons";
import { Loading, Toaster } from "./components/ui";
import { NAV_ITEMS } from "./lib/nav";
import { useApi } from "./lib/useApi";
import { Overview } from "./pages/Overview";
import { Sessions } from "./pages/Sessions";
import { Models } from "./pages/Models";
import { Memory } from "./pages/Memory";
import { Tools } from "./pages/Tools";
import { Skills } from "./pages/Skills";
import { Config } from "./pages/Config";
import { Cron } from "./pages/Cron";
import { Kanban } from "./pages/Kanban";
import { Mcp } from "./pages/Mcp";
import { Channels } from "./pages/Channels";
import { Webhooks } from "./pages/Webhooks";
import { Keys } from "./pages/Keys";
import { ProviderAuth } from "./pages/ProviderAuth";
import { Plugins } from "./pages/Plugins";
import { Profiles as PersonaProfiles } from "./pages/Profiles";
import { RuntimeProfileNew, RuntimeProfiles } from "./pages/RuntimeProfiles";
import { Files } from "./pages/Files";
import { Logs } from "./pages/Logs";
import { System } from "./pages/System";
import { Analytics } from "./pages/Analytics";
import { Pairing } from "./pages/Pairing";
import { Docs } from "./pages/Docs";
import { Placeholder } from "./pages/Placeholder";

// Code-split heavy pages so they load on demand: Chat/Terminal pull in xterm,
// and the desktop app keeps its own graphical chat surface.
const Terminal = lazy(() => import("./pages/Chat").then((m) => ({ default: m.Chat })));
// The desktop app opens into a focused, chat-first shell instead of the admin grid.
const DesktopShell = lazy(() =>
  import("./pages/DesktopShell").then((m) => ({ default: m.DesktopShell })),
);

function TopBar({ onOpenNav }: { onOpenNav: () => void }) {
  const loc = useLocation();
  const status = useApi<{
    active_sessions?: number;
    gateway_running?: boolean;
    gateway_state?: string;
    provider?: string;
    model?: string;
    tools?: number;
    skills?: number;
    provider_error?: string;
    version?: string;
  }>("status");
  const current = NAV_ITEMS.find(
    (i) => i.path === loc.pathname || (i.path !== "/" && loc.pathname.startsWith(i.path)),
  );
  const ready = !status.error && !!status.data && !status.data.provider_error;
  const gateway = status.data?.gateway_state || (status.data?.gateway_running ? "running" : "offline");
  return (
    <header className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-border bg-bg/80 px-3 backdrop-blur md:px-[var(--pad)]">
      <div className="flex min-w-0 items-center gap-3">
        <button
          onClick={onOpenNav}
          title="Open navigation"
          className="grid h-8 w-8 shrink-0 place-items-center rounded-[var(--radius)] border border-border bg-surface text-dim hover:text-text lg:hidden"
        >
          <Icon name="menu" size={17} />
        </button>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <div className="truncate font-mono text-sm font-semibold text-text">{current?.label || "AEGIS"}</div>
            <span className={ready ? "h-1.5 w-1.5 rounded-full bg-success" : "h-1.5 w-1.5 rounded-full bg-danger"} />
          </div>
          <div className="hidden truncate text-[11px] text-faint sm:block">
            {status.data?.provider || "provider"} / {status.data?.model || "model"} · {status.data?.tools ?? "-"} tools · {status.data?.skills ?? "-"} skills
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <div className="hidden items-center gap-1.5 rounded-[var(--radius)] border border-border bg-surface px-2.5 py-1.5 text-[11px] text-dim xl:flex">
          <span className={gateway === "running" ? "h-1.5 w-1.5 rounded-full bg-success" : "h-1.5 w-1.5 rounded-full bg-faint"} />
          {gateway}
          <span className="text-faint">·</span>
          {status.data?.active_sessions ?? 0} active
        </div>
        <button
          onClick={openCommandPalette}
          title="Command palette (Ctrl/⌘ K)"
          className="flex h-8 items-center gap-1.5 rounded-[var(--radius)] border border-border bg-surface px-2.5 font-mono text-[11px] text-dim hover:text-text"
        >
          <Icon name="search" size={13} />
          <span className="hidden sm:inline">Search</span>
          <kbd className="hidden rounded border border-border bg-surface-2 px-1 py-px font-mono text-[10px] text-faint sm:inline">⌘K</kbd>
        </button>
        <ThemeSwitcher />
      </div>
    </header>
  );
}

function Routed({ full }: { full?: boolean }) {
  const loc = useLocation();
  return (
    <div className={full ? "h-full animate-fade-in" : "mx-auto w-full max-w-[1500px] animate-fade-in"}>
      <ErrorBoundary key={loc.pathname}>
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/" element={<Navigate to="/sessions" replace />} />
            <Route path="/dashboard" element={<Overview />} />
            <Route path="/chat" element={<Terminal />} />
            <Route path="/terminal" element={<Terminal />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/models" element={<Models />} />
            <Route path="/memory" element={<Memory />} />
            <Route path="/tools" element={<Tools />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/config" element={<Config />} />
            <Route path="/cron" element={<Cron />} />
            <Route path="/kanban" element={<Kanban />} />
            <Route path="/mcp" element={<Mcp />} />
            <Route path="/channels" element={<Channels />} />
            <Route path="/webhooks" element={<Webhooks />} />
            <Route path="/pairing" element={<Pairing />} />
            <Route path="/accounts" element={<ProviderAuth />} />
            <Route path="/keys" element={<Keys />} />
            <Route path="/env" element={<Keys />} />
            <Route path="/plugins" element={<Plugins />} />
            <Route path="/profiles" element={<RuntimeProfiles />} />
            <Route path="/profiles/new" element={<RuntimeProfileNew />} />
            <Route path="/persona" element={<PersonaProfiles />} />
            <Route path="/files" element={<Files />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/system" element={<System />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/docs" element={<Docs />} />
            <Route path="*" element={<Placeholder title="Not found" />} />
          </Routes>
        </Suspense>
      </ErrorBoundary>
    </div>
  );
}

function AdminShell() {
  const loc = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => setNavOpen(false), [loc.pathname]);
  // The browser chat terminal fills its pane edge-to-edge; other pages keep
  // the padded scroll area.
  const fullBleed = loc.pathname === "/chat";
  return (
    <div className="flex h-full overflow-hidden bg-bg text-text">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onOpenNav={() => setNavOpen(true)} />
        <main className={fullBleed ? "min-h-0 flex-1 overflow-hidden" : "scroll-thin flex-1 overflow-y-auto p-3 md:p-4 xl:p-5"}>
          <Routed full={fullBleed} />
        </main>
      </div>
      <Toaster />
    </div>
  );
}

export function App() {
  return (
    <HashRouter>
      {/* Column root: the custom titlebar (desktop only) sits above the routed
          surface; the command palette overlays everything. */}
      <div className="flex h-screen flex-col overflow-hidden bg-bg text-text">
        <TitleBar />
        <div className="min-h-0 flex-1">
          <Suspense fallback={<Loading />}>
            <Routes>
              {/* The desktop app's chat-first surface — its own full-screen chrome. */}
              <Route path="/app" element={<DesktopShell />} />
              {/* Everything else is the admin control panel. */}
              <Route path="/*" element={<AdminShell />} />
            </Routes>
          </Suspense>
        </div>
        <CommandPalette />
      </div>
    </HashRouter>
  );
}
