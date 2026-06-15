// App shell: sidebar + top bar + routed content. Routes come from lib/nav so the
// sidebar and router never drift. Pages not yet rebuilt fall back to Placeholder.

import { lazy, Suspense } from "react";
import { HashRouter, Route, Routes, useLocation } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { ThemeSwitcher } from "./components/ThemeSwitcher";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { TitleBar } from "./components/TitleBar";
import { CommandPalette, openCommandPalette } from "./components/CommandPalette";
import { Icon } from "./components/icons";
import { Loading, Toaster } from "./components/ui";
import { NAV_ITEMS } from "./lib/nav";
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
import { Plugins } from "./pages/Plugins";
import { Profiles } from "./pages/Profiles";
import { Files } from "./pages/Files";
import { Logs } from "./pages/Logs";
import { System } from "./pages/System";
import { Analytics } from "./pages/Analytics";
import { Placeholder } from "./pages/Placeholder";

// Code-split heavy pages so they load on demand: the Terminal pulls in xterm,
// and the graphical Chat shares the desktop app's chat surface.
const Terminal = lazy(() => import("./pages/Chat").then((m) => ({ default: m.Chat })));
const ChatGraphical = lazy(() => import("./pages/ChatGraphical").then((m) => ({ default: m.ChatGraphical })));
// The desktop app opens into a focused, chat-first shell instead of the admin grid.
const DesktopShell = lazy(() =>
  import("./pages/DesktopShell").then((m) => ({ default: m.DesktopShell })),
);

function TopBar() {
  const loc = useLocation();
  const current = NAV_ITEMS.find(
    (i) => i.path === loc.pathname || (i.path !== "/" && loc.pathname.startsWith(i.path)),
  );
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface/40 px-[var(--pad)] backdrop-blur">
      <div className="text-sm font-medium text-dim">{current?.label || "AEGIS"}</div>
      <div className="flex items-center gap-2">
        <button
          onClick={openCommandPalette}
          title="Command palette (Ctrl/⌘ K)"
          className="flex items-center gap-1.5 rounded-[var(--radius)] border border-border bg-surface px-2.5 py-1.5 text-xs text-dim hover:text-text"
        >
          <Icon name="search" size={13} /> Search
          <kbd className="rounded border border-border bg-surface-2 px-1 py-px font-mono text-[10px] text-faint">⌘K</kbd>
        </button>
        <a
          href="#/app"
          title="Open the focused chat app"
          className="rounded-[var(--radius)] border border-border bg-surface px-2.5 py-1.5 text-xs text-dim hover:text-text"
        >
          Chat app ↗
        </a>
        <ThemeSwitcher />
      </div>
    </header>
  );
}

function Routed({ full }: { full?: boolean }) {
  const loc = useLocation();
  return (
    <div className={full ? "h-full animate-fade-in" : "mx-auto max-w-6xl animate-fade-in"}>
      <ErrorBoundary key={loc.pathname}>
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/chat" element={<ChatGraphical />} />
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
            <Route path="/keys" element={<Keys />} />
            <Route path="/plugins" element={<Plugins />} />
            <Route path="/profiles" element={<Profiles />} />
            <Route path="/files" element={<Files />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/system" element={<System />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="*" element={<Placeholder title="Not found" />} />
          </Routes>
        </Suspense>
      </ErrorBoundary>
    </div>
  );
}

function AdminShell() {
  const loc = useLocation();
  // The graphical Chat tab fills its pane edge-to-edge (it manages its own
  // scroll), like the desktop chat app; other pages keep the padded scroll area.
  const fullBleed = loc.pathname === "/chat";
  return (
    <div className="flex h-full overflow-hidden bg-bg text-text">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className={fullBleed ? "min-h-0 flex-1 overflow-hidden" : "scroll-thin flex-1 overflow-y-auto p-[var(--pad)] md:p-6"}>
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
