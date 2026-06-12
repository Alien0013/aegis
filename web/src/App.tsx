import { useEffect, useState } from "react";
import { Icon } from "./lib/icons";
import { Overview } from "./pages/Overview";
import { Chat } from "./pages/Chat";
import { ListPage } from "./pages/ListPage";
import { ConfigPage } from "./pages/ConfigPage";
import { SystemPage } from "./pages/SystemPage";
import { CronPage } from "./pages/CronPage";
import { ModelsPage } from "./pages/ModelsPage";
import { KeysPage } from "./pages/KeysPage";
import { MemoryPage } from "./pages/MemoryPage";
import { ChannelsPage } from "./pages/ChannelsPage";
import { McpPage } from "./pages/McpPage";
import { WebhooksPage } from "./pages/WebhooksPage";
import { PluginsPage } from "./pages/PluginsPage";

type NavItem = { id: string; label: string; icon: string };
const NAV: NavItem[] = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "chat", label: "Chat", icon: "chat" },
  { id: "runs", label: "Runs", icon: "sessions" },
  { id: "traces", label: "Traces", icon: "logs" },
  { id: "agents", label: "Agents", icon: "tools" },
  { id: "sessions", label: "Sessions", icon: "sessions" },
  { id: "kanban", label: "Kanban", icon: "kanban" },
  { id: "models", label: "Models", icon: "models" },
  { id: "channels", label: "Channels", icon: "channels" },
  { id: "mcp", label: "MCP", icon: "tools" },
  { id: "webhooks", label: "Webhooks", icon: "channels" },
  { id: "plugins", label: "Plugins", icon: "skills" },
  { id: "projects", label: "Projects", icon: "system" },
  { id: "worktrees", label: "Worktrees", icon: "sessions" },
  { id: "evals", label: "Evals", icon: "logs" },
  { id: "keys", label: "API Keys", icon: "config" },
  { id: "skills", label: "Skills", icon: "skills" },
  { id: "memory", label: "Memory", icon: "memory" },
  { id: "cron", label: "Cron", icon: "cron" },
  { id: "tools", label: "Tools", icon: "tools" },
  { id: "logs", label: "Logs", icon: "logs" },
  { id: "config", label: "Config", icon: "config" },
  { id: "system", label: "System", icon: "system" },
];
const THEMES = ["dark", "paper", "mono"];

function pageFor(id: string, go: (id: string) => void) {
  switch (id) {
    case "overview": return <Overview go={go} />;
    case "chat": return <Chat />;
    case "runs": return <ListPage key="runs" endpoint="runs?limit=100" arrayKey="runs" title="Runs"
      cols={[["title", "Run"], ["status", "Status"], ["surface", "Surface"]]} />;
    case "traces": return <ListPage key="traces" endpoint="traces?limit=100" arrayKey="traces" title="Traces"
      cols={[["id", "Trace"], ["status", "Status"], ["source", "Source"]]} />;
    case "agents": return <ListPage key="agents" endpoint="agents" arrayKey="agents" title="Agents"
      cols={[["id", "Agent"], ["status", "Status"], ["type", "Type"]]} />;
    case "kanban": return <ListPage key="kanban" endpoint="kanban" title="Kanban"
      cols={[["title", "Card"], ["status", "Status"], ["assignee", "Assignee"]]} />;
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
      cols={[["name", "Eval"], ["status", "Status"], ["source", "Source"]]} />;
    case "sessions": return <ListPage key="sessions" endpoint="sessions" title="Sessions"
      cols={[["title", "Title"], ["updated_at", "Updated"]]} />;
    case "logs": return <ListPage key="logs" endpoint="logs" arrayKey="lines" title="Logs" cols={[["line", "Line"]]} raw />;
    default: return <Overview go={go} />;
  }
}

export function App() {
  const [view, setView] = useState(location.hash.slice(1) || "overview");
  const [theme, setTheme] = useState(localStorage.getItem("aegis_theme") || "dark");
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("aegis_theme", theme); }, [theme]);
  const go = (id: string) => { setView(id); location.hash = id; };
  return (
    <div className="app">
      <aside className="side">
        <div className="brand"><span className="dot" /> AEGIS</div>
        {NAV.map((n) => (
          <div key={n.id} className={"nav" + (view === n.id ? " active" : "")} onClick={() => go(n.id)}>
            <Icon n={n.icon} /> {n.label}
          </div>
        ))}
        <div className="sidefoot">
          theme
          <select style={{ width: "auto" }} value={theme} onChange={(e) => setTheme(e.target.value)}>
            {THEMES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </aside>
      <main className="main">{pageFor(view, go)}</main>
    </div>
  );
}
