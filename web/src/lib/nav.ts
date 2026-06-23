// Single source of truth for navigation + routing. Sidebar renders these
// groups; App maps each item's `path` to its page component.

export interface NavItem {
  path: string;
  label: string;
  icon: string;
}
export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    label: "Workspace",
    items: [
      { path: "/sessions", label: "Sessions", icon: "sessions" },
      { path: "/chat", label: "Chat", icon: "chat" },
      { path: "/terminal", label: "Terminal", icon: "terminal" },
      { path: "/dashboard", label: "Overview", icon: "overview" },
    ],
  },
  {
    label: "Agent",
    items: [
      { path: "/models", label: "Models", icon: "models" },
      { path: "/tools", label: "Tools", icon: "tools" },
      { path: "/skills", label: "Skills", icon: "skills" },
      { path: "/memory", label: "Memory", icon: "memory" },
      { path: "/persona", label: "Persona", icon: "profiles" },
      { path: "/cron", label: "Schedules", icon: "cron" },
      { path: "/kanban", label: "Kanban", icon: "kanban" },
    ],
  },
  {
    label: "Integrations",
    items: [
      { path: "/mcp", label: "MCP", icon: "mcp" },
      { path: "/channels", label: "Channels", icon: "channels" },
      { path: "/webhooks", label: "Webhooks", icon: "webhooks" },
      { path: "/pairing", label: "Pairing", icon: "shield" },
      { path: "/accounts", label: "Accounts", icon: "keys" },
      { path: "/plugins", label: "Plugins", icon: "plugins" },
      { path: "/env", label: "Env", icon: "keys" },
    ],
  },
  {
    label: "System",
    items: [
      { path: "/command-center", label: "Command Center", icon: "command" },
      { path: "/analytics", label: "Analytics", icon: "analytics" },
      { path: "/files", label: "Files", icon: "files" },
      { path: "/logs", label: "Logs", icon: "logs" },
      { path: "/profiles", label: "Profiles", icon: "profiles" },
      { path: "/docs", label: "Docs", icon: "files" },
      { path: "/system", label: "System", icon: "system" },
      { path: "/config", label: "Config", icon: "config" },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV.flatMap((g) => g.items);
