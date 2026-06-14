// App shell: sidebar + top bar + routed content. Routes come from lib/nav so the
// sidebar and router never drift. Pages not yet rebuilt fall back to Placeholder.

import { lazy, Suspense } from "react";
import { HashRouter, Route, Routes, useLocation } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { ThemeSwitcher } from "./components/ThemeSwitcher";
import { ErrorBoundary } from "./components/ErrorBoundary";
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

// Code-split heavy pages (e.g. Chat pulls in xterm) so they load on demand.
const Chat = lazy(() => import("./pages/Chat").then((m) => ({ default: m.Chat })));

function TopBar() {
  const loc = useLocation();
  const current = NAV_ITEMS.find(
    (i) => i.path === loc.pathname || (i.path !== "/" && loc.pathname.startsWith(i.path)),
  );
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface/40 px-[var(--pad)] backdrop-blur">
      <div className="text-sm font-medium text-dim">{current?.label || "AEGIS"}</div>
      <ThemeSwitcher />
    </header>
  );
}

function Routed() {
  const loc = useLocation();
  return (
    <div className="mx-auto max-w-6xl animate-fade-in">
      <ErrorBoundary key={loc.pathname}>
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/models" element={<Models />} />
            <Route path="/memory" element={<Memory />} />
            <Route path="/tools" element={<Tools />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/config" element={<Config />} />
            <Route path="/cron" element={<Cron />} />
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

export function App() {
  return (
    <HashRouter>
      <div className="flex h-screen overflow-hidden bg-bg text-text">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar />
          <main className="scroll-thin flex-1 overflow-y-auto p-[var(--pad)] md:p-6">
            <Routed />
          </main>
        </div>
        <Toaster />
      </div>
    </HashRouter>
  );
}
