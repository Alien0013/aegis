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

type NavItem = { id: string; label: string; icon: string };
const NAV: NavItem[] = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "chat", label: "Chat", icon: "chat" },
  { id: "sessions", label: "Sessions", icon: "sessions" },
  { id: "models", label: "Models", icon: "models" },
  { id: "channels", label: "Channels", icon: "channels" },
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
    case "config": return <ConfigPage />;
    case "cron": return <CronPage />;
    case "models": return <ModelsPage />;
    case "keys": return <KeysPage />;
    case "system": return <SystemPage />;
    case "sessions": return <ListPage key="sessions" endpoint="sessions" title="Sessions"
      cols={[["title", "Title"], ["updated_at", "Updated"]]} />;
    case "channels": return <ListPage key="channels" endpoint="pairing" arrayKey="pending" title="Channels & Pairing"
      cols={[["platform", "Platform"], ["code", "Code"], ["user", "User"]]} empty="No pending pairings. Connect a channel with `aegis gateway --channels telegram`." />;
    case "skills": return <ListPage key="skills" endpoint="skills" arrayKey="skills" title="Skills"
      cols={[["name", "Skill"], ["description", "Description"]]} />;
    case "memory": return <ListPage key="memory" endpoint="memory" arrayKey="memory" title="Memory"
      cols={[["text", "Entry"]]} raw />;
    case "tools": return <ListPage key="tools" endpoint="tools" arrayKey="tools" title="Tools"
      cols={[["name", "Tool"], ["description", "Description"]]} />;
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
