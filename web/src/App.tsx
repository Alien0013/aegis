import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Icon } from "./lib/icons";
import { api } from "./lib/api";
import { Loading } from "./lib/ui";
import { CommandPalette } from "./CommandPalette";
import { Tooltip, TooltipProvider } from "./lib/components/Tooltip";

// Pages are code-split: each loads as its own chunk on first visit so the initial
// bundle stays small. Components are named exports, so map them to `default` for lazy().
const Overview = lazy(() => import("./pages/Overview").then((m) => ({ default: m.Overview })));
const Chat = lazy(() => import("./pages/Chat").then((m) => ({ default: m.Chat })));
const ListPage = lazy(() => import("./pages/ListPage").then((m) => ({ default: m.ListPage })));
const ConfigPage = lazy(() => import("./pages/ConfigPage").then((m) => ({ default: m.ConfigPage })));
const SystemPage = lazy(() => import("./pages/SystemPage").then((m) => ({ default: m.SystemPage })));
const CronPage = lazy(() => import("./pages/CronPage").then((m) => ({ default: m.CronPage })));
const KanbanPage = lazy(() => import("./pages/KanbanPage").then((m) => ({ default: m.KanbanPage })));
const ModelsPage = lazy(() => import("./pages/ModelsPage").then((m) => ({ default: m.ModelsPage })));
const KeysPage = lazy(() => import("./pages/KeysPage").then((m) => ({ default: m.KeysPage })));
const MemoryPage = lazy(() => import("./pages/MemoryPage").then((m) => ({ default: m.MemoryPage })));
const ChannelsPage = lazy(() => import("./pages/ChannelsPage").then((m) => ({ default: m.ChannelsPage })));
const AgentsPage = lazy(() => import("./pages/AgentsPage").then((m) => ({ default: m.AgentsPage })));
const FilesPage = lazy(() => import("./pages/FilesPage").then((m) => ({ default: m.FilesPage })));
const McpPage = lazy(() => import("./pages/McpPage").then((m) => ({ default: m.McpPage })));
const WebhooksPage = lazy(() => import("./pages/WebhooksPage").then((m) => ({ default: m.WebhooksPage })));
const PluginsPage = lazy(() => import("./pages/PluginsPage").then((m) => ({ default: m.PluginsPage })));
const TerminalPage = lazy(() => import("./pages/TerminalPage").then((m) => ({ default: m.TerminalPage })));

type NavItem = { id: string; label: string; icon: string; group: string };
const NAV: NavItem[] = [
  { id: "overview", label: "Home", icon: "overview", group: "Home" },
  { id: "chat", label: "Chat", icon: "chat", group: "Home" },
  { id: "terminal", label: "Terminal", icon: "system", group: "Home" },
  { id: "agents", label: "Agents", icon: "agents", group: "Observe" },
  { id: "sessions", label: "Sessions", icon: "sessions", group: "Observe" },
  { id: "runs", label: "Runs", icon: "sessions", group: "Observe" },
  { id: "traces", label: "Traces", icon: "logs", group: "Observe" },
  { id: "logs", label: "Logs", icon: "logs", group: "Observe" },
  { id: "kanban", label: "Kanban", icon: "kanban", group: "Operate" },
  { id: "cron", label: "Cron", icon: "cron", group: "Operate" },
  { id: "webhooks", label: "Webhooks", icon: "channels", group: "Operate" },
  { id: "channels", label: "Channels", icon: "channels", group: "Operate" },
  { id: "models", label: "Models", icon: "models", group: "Configure" },
  { id: "keys", label: "API Keys", icon: "config", group: "Configure" },
  { id: "memory", label: "Memory", icon: "memory", group: "Configure" },
  { id: "skills", label: "Skills", icon: "skills", group: "Configure" },
  { id: "tools", label: "Tools", icon: "tools", group: "Configure" },
  { id: "mcp", label: "MCP", icon: "tools", group: "Configure" },
  { id: "plugins", label: "Plugins", icon: "skills", group: "Configure" },
  { id: "files", label: "Files", icon: "logs", group: "Workspace" },
  { id: "projects", label: "Projects", icon: "system", group: "Workspace" },
  { id: "worktrees", label: "Worktrees", icon: "sessions", group: "Workspace" },
  { id: "evals", label: "Evals", icon: "logs", group: "Workspace" },
  { id: "config", label: "Config", icon: "config", group: "System" },
  { id: "system", label: "System", icon: "system", group: "System" },
];
const THEMES = ["dark", "hacker", "paper", "mono"];

// Current view id, derived from the router so nav highlighting and the topbar title
// stay in sync with the URL (e.g. #/sessions -> "sessions").
const viewFromPath = (pathname: string) => pathname.replace(/^\/+/, "").split("/")[0] || "overview";

function Pages({ go }: { go: (id: string) => void }) {
  return (
    <Routes>
      <Route path="/" element={<Overview go={go} />} />
      <Route path="/overview" element={<Overview go={go} />} />
      <Route path="/cockpit" element={<Overview go={go} />} />
      <Route path="/chat" element={<Chat />} />
      <Route path="/terminal" element={<TerminalPage />} />
      <Route path="/runs" element={<ListPage endpoint="runs?limit=100" arrayKey="runs" title="Runs"
        detailEndpoint="run" cols={[["title", "Run"], ["status", "Status"], ["surface", "Surface"], ["updated_at", "Updated"]]} />} />
      <Route path="/traces" element={<ListPage endpoint="traces?limit=100" arrayKey="traces" title="Traces"
        detailEndpoint="trace" cols={[["id", "Trace"], ["status", "Status"], ["source", "Source"], ["spans.span_count", "Spans"]]} />} />
      <Route path="/agents" element={<AgentsPage />} />
      <Route path="/kanban" element={<KanbanPage />} />
      <Route path="/config" element={<ConfigPage />} />
      <Route path="/cron" element={<CronPage />} />
      <Route path="/models" element={<ModelsPage />} />
      <Route path="/keys" element={<KeysPage />} />
      <Route path="/memory" element={<MemoryPage />} />
      <Route path="/channels" element={<ChannelsPage />} />
      <Route path="/system" element={<SystemPage />} />
      <Route path="/files" element={<FilesPage />} />
      <Route path="/mcp" element={<McpPage />} />
      <Route path="/webhooks" element={<WebhooksPage />} />
      <Route path="/plugins" element={<PluginsPage />} />
      <Route path="/projects" element={<ListPage endpoint="projects" arrayKey="projects" title="Projects"
        cols={[["name", "Project"], ["kind", "Kind"], ["path", "Path"]]} />} />
      <Route path="/worktrees" element={<ListPage endpoint="worktrees" arrayKey="worktrees" title="Worktrees"
        cols={[["worktree", "Worktree"], ["branch", "Branch"], ["path", "Path"]]} />} />
      <Route path="/evals" element={<ListPage endpoint="evals" arrayKey="evals" title="Evals"
        detailEndpoint="eval" idKey="id" cols={[["name", "Eval"], ["status", "Status"], ["source", "Source"]]} />} />
      <Route path="/sessions" element={<ListPage endpoint="sessions" title="Sessions"
        detailEndpoint="session" cols={[["title", "Title"], ["updated_at", "Updated"], ["id", "ID"]]} />} />
      <Route path="/skills" element={<ListPage endpoint="skills" title="Skills"
        cols={[["name", "Skill"], ["description", "Description"]]} />} />
      <Route path="/tools" element={<ListPage endpoint="tools" title="Tools"
        cols={[["name", "Tool"], ["toolset", "Toolset"], ["enabled", "Enabled"], ["description", "Description"]]} />} />
      <Route path="/logs" element={<ListPage endpoint="logs" arrayKey="lines" title="Logs" cols={[["line", "Line"]]} raw />} />
      <Route path="*" element={<Overview go={go} />} />
    </Routes>
  );
}

export function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const view = viewFromPath(location.pathname);
  const [theme, setTheme] = useState(localStorage.getItem("aegis_theme") || "dark");
  const [navOpen, setNavOpen] = useState(false);
  const [cmdOpen, setCmdOpen] = useState(false);
  const [status, setStatus] = useState<any>(null);
  const [navQuery, setNavQuery] = useState("");
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("aegis_theme", theme); }, [theme]);
  const loadStatus = () => api("status").then((s) => setStatus(s)).catch(() => setStatus({ error: true }));
  useEffect(() => {
    let mounted = true;
    const load = () => api("status").then((s) => mounted && setStatus(s)).catch(() => mounted && setStatus({ error: true }));
    load();
    const timer = setInterval(load, 15000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen(true);
      }
      if (e.key === "Escape") setCmdOpen(false);
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);
  const go = (id: string) => { navigate("/" + id); setNavOpen(false); };
  const visibleNav = useMemo(() => {
    const q = navQuery.trim().toLowerCase();
    if (!q) return NAV;
    return NAV.filter((n) => `${n.label} ${n.group} ${n.id}`.toLowerCase().includes(q));
  }, [navQuery]);
  const groups = [...new Set(visibleNav.map((n) => n.group))];
  const current = NAV.find((n) => n.id === view);
  const online = status && !status.error;
  return (
    <TooltipProvider>
    <div className="app">
      <header className="topbar">
        <Tooltip label="Menu"><button className="iconbtn" aria-label="Open navigation" onClick={() => setNavOpen(true)}><Icon n="menu" /></button></Tooltip>
        <div>
          <b>{current?.label || "AEGIS Agent"}</b>
          <span>{online ? `${status.provider || "provider"} / ${status.model || "model"}` : "dashboard"}</span>
        </div>
        <Tooltip label="Command palette  ·  Ctrl K"><button className="iconbtn topcmd" aria-label="Open command palette" onClick={() => setCmdOpen(true)}><Icon n="search" /></button></Tooltip>
      </header>
      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} go={go} reload={loadStatus} />
      {navOpen && <button className="scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} />}
      <aside className={"side" + (navOpen ? " open" : "")}>
        <div className="brand">
          <span className="mark">A</span>
          <div>
            <b>AEGIS Agent</b>
            <span>Operator workspace</span>
          </div>
        </div>
        <div className="navsearch">
          <Icon n="search" />
          <input
            aria-label="Filter navigation"
            value={navQuery}
            onChange={(e) => setNavQuery(e.target.value)}
            placeholder="Find page"
          />
        </div>
        <button className="cmd-trigger" onClick={() => setCmdOpen(true)}>
          <Icon n="search" /><span>Command palette</span><kbd>Ctrl K</kbd>
        </button>
        {groups.map((group) => (
          <div className="navgroup" key={group}>
            <div className="navlabel">{group}</div>
            {visibleNav.filter((n) => n.group === group).map((n) => (
              <button key={n.id} className={"nav" + (view === n.id ? " active" : "")} onClick={() => go(n.id)}>
                <Icon n={n.icon} /> <span>{n.label}</span>
              </button>
            ))}
          </div>
        ))}
        {!visibleNav.length && <div className="empty small">No pages match</div>}
        <div className="sidefoot">
          <div className="statusbox">
            <span className={"statusdot" + (!online ? " err" : "")} />
            <div>
              <b>{online ? status.provider || "AEGIS" : "Backend"}</b>
              <span>{online ? status.model || "ready" : status?.error ? "offline" : "connecting..."}</span>
            </div>
          </div>
          <div className="themebar" aria-label="Theme">
            {THEMES.map((t) => (
              <button key={t} className={theme === t ? "active" : ""} onClick={() => setTheme(t)}>
                <span className={`swatch ${t}`} />
                {t}
              </button>
            ))}
          </div>
        </div>
      </aside>
      <main className="main"><Suspense fallback={<Loading />}><Pages go={go} /></Suspense></main>
    </div>
    </TooltipProvider>
  );
}
