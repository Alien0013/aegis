import { useEffect, useState } from "react";
import { Icon } from "./lib/icons";
import { api } from "./lib/api";
import { Overview } from "./pages/Overview";
import { Chat } from "./pages/Chat";
import { ListPage } from "./pages/ListPage";
import { ConfigPage } from "./pages/ConfigPage";
import { SystemPage } from "./pages/SystemPage";
import { CronPage } from "./pages/CronPage";
import { KanbanPage } from "./pages/KanbanPage";
import { ModelsPage } from "./pages/ModelsPage";
import { KeysPage } from "./pages/KeysPage";
import { MemoryPage } from "./pages/MemoryPage";
import { ChannelsPage } from "./pages/ChannelsPage";
import { McpPage } from "./pages/McpPage";
import { WebhooksPage } from "./pages/WebhooksPage";
import { PluginsPage } from "./pages/PluginsPage";

type NavItem = { id: string; label: string; icon: string; group: string };
const NAV: NavItem[] = [
  { id: "overview", label: "Overview", icon: "overview", group: "Home" },
  { id: "chat", label: "Chat", icon: "chat", group: "Home" },
  { id: "sessions", label: "Sessions", icon: "sessions", group: "Observe" },
  { id: "runs", label: "Runs", icon: "sessions", group: "Observe" },
  { id: "traces", label: "Traces", icon: "logs", group: "Observe" },
  { id: "agents", label: "Agents", icon: "tools", group: "Observe" },
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
  { id: "projects", label: "Projects", icon: "system", group: "Workspace" },
  { id: "worktrees", label: "Worktrees", icon: "sessions", group: "Workspace" },
  { id: "evals", label: "Evals", icon: "logs", group: "Workspace" },
  { id: "config", label: "Config", icon: "config", group: "System" },
  { id: "system", label: "System", icon: "system", group: "System" },
];
const THEMES = ["dark", "paper", "mono"];

function pageFor(id: string, go: (id: string) => void) {
  switch (id) {
    case "overview": return <Overview go={go} />;
    case "chat": return <Chat />;
    case "runs": return <ListPage key="runs" endpoint="runs?limit=100" arrayKey="runs" title="Runs"
      detailEndpoint="run" cols={[["title", "Run"], ["status", "Status"], ["surface", "Surface"], ["updated_at", "Updated"]]} />;
    case "traces": return <ListPage key="traces" endpoint="traces?limit=100" arrayKey="traces" title="Traces"
      detailEndpoint="trace" cols={[["id", "Trace"], ["status", "Status"], ["source", "Source"], ["spans.span_count", "Spans"]]} />;
    case "agents": return <ListPage key="agents" endpoint="agents" arrayKey="agents" title="Agents"
      detailEndpoint="agent" cols={[["id", "Agent"], ["status", "Status"], ["type", "Type"], ["model", "Model"]]} />;
    case "kanban": return <KanbanPage />;
    case "config": return <ConfigPage />;
    case "cron": return <CronPage />;
    case "models": return <ModelsPage />;
    case "keys": return <KeysPage />;
    case "memory": return <MemoryPage />;
    case "channels": return <ChannelsPage />;
    case "system": return <SystemPage />;
    case "mcp": return <McpPage />;
    case "webhooks": return <WebhooksPage />;
    case "plugins": return <PluginsPage />;
    case "projects": return <ListPage key="projects" endpoint="projects" arrayKey="projects" title="Projects"
      cols={[["name", "Project"], ["kind", "Kind"], ["path", "Path"]]} />;
    case "worktrees": return <ListPage key="worktrees" endpoint="worktrees" arrayKey="worktrees" title="Worktrees"
      cols={[["worktree", "Worktree"], ["branch", "Branch"], ["path", "Path"]]} />;
    case "evals": return <ListPage key="evals" endpoint="evals" arrayKey="evals" title="Evals"
      detailEndpoint="eval" idKey="id" cols={[["name", "Eval"], ["status", "Status"], ["source", "Source"]]} />;
    case "sessions": return <ListPage key="sessions" endpoint="sessions" title="Sessions"
      detailEndpoint="session" cols={[["title", "Title"], ["updated_at", "Updated"], ["id", "ID"]]} />;
    case "logs": return <ListPage key="logs" endpoint="logs" arrayKey="lines" title="Logs" cols={[["line", "Line"]]} raw />;
    default: return <Overview go={go} />;
  }
}

export function App() {
  const [view, setView] = useState(location.hash.slice(1) || "overview");
  const [theme, setTheme] = useState(localStorage.getItem("aegis_theme") || "dark");
  const [navOpen, setNavOpen] = useState(false);
  const [status, setStatus] = useState<any>(null);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("aegis_theme", theme); }, [theme]);
  useEffect(() => {
    const onHash = () => setView(location.hash.slice(1) || "overview");
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    let mounted = true;
    const load = () => api("status").then((s) => mounted && setStatus(s)).catch(() => mounted && setStatus({ error: true }));
    load();
    const timer = setInterval(load, 15000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);
  const go = (id: string) => { setView(id); location.hash = id; setNavOpen(false); };
  const groups = [...new Set(NAV.map((n) => n.group))];
  const current = NAV.find((n) => n.id === view);
  return (
    <div className="app">
      <header className="topbar">
        <button className="iconbtn" aria-label="Open navigation" onClick={() => setNavOpen(true)}><Icon n="menu" /></button>
        <div>
          <b>{current?.label || "AEGIS"}</b>
          <span>{status?.model || "source dashboard"}</span>
        </div>
      </header>
      {navOpen && <button className="scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} />}
      <aside className={"side" + (navOpen ? " open" : "")}>
        <div className="brand"><span className="dot" /> AEGIS</div>
        {groups.map((group) => (
          <div className="navgroup" key={group}>
            <div className="navlabel">{group}</div>
            {NAV.filter((n) => n.group === group).map((n) => (
              <button key={n.id} className={"nav" + (view === n.id ? " active" : "")} onClick={() => go(n.id)}>
                <Icon n={n.icon} /> <span>{n.label}</span>
              </button>
            ))}
          </div>
        ))}
        <div className="sidefoot">
          <div className="statusbox">
            <span className={"statusdot" + (status?.error ? " err" : "")} />
            <div>
              <b>{status?.provider || "AEGIS"}</b>
              <span>{status?.model || (status?.error ? "backend offline" : "loading...")}</span>
            </div>
          </div>
          <div className="themebar" aria-label="Theme">
            {THEMES.map((t) => (
              <button key={t} className={theme === t ? "active" : ""} onClick={() => setTheme(t)}>{t}</button>
            ))}
          </div>
        </div>
      </aside>
      <main className="main">{pageFor(view, go)}</main>
    </div>
  );
}
