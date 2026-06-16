import type { NavGroup, NavItem } from "./nav";
import type { DashboardPluginRoute } from "../plugins/host";

export interface PluginNavItem extends NavItem {
  plugin?: string;
  position?: string;
}

function normalizePath(path: string | undefined): string {
  const raw = String(path || "").trim();
  if (!raw) return "";
  return raw.startsWith("/") ? raw : `/${raw}`;
}

function targetKeys(item: NavItem): Set<string> {
  const path = normalizePath(item.path);
  const cleanPath = path.replace(/^\/+/, "").toLowerCase();
  const label = item.label.toLowerCase().replace(/\s+/g, "-");
  return new Set([path.toLowerCase(), cleanPath, label].filter(Boolean));
}

function routeToNavItem(route: DashboardPluginRoute): PluginNavItem | null {
  if (route.hidden) return null;
  const path = normalizePath(route.path);
  if (!path) return null;
  return {
    path,
    label: route.label || route.plugin || path.replace(/^\/+/, ""),
    icon: route.icon || "plugins",
    plugin: route.plugin,
    position: route.position || "end",
  };
}

export function pluginNavItems(
  routes: DashboardPluginRoute[],
  existingItems: NavItem[] = [],
): PluginNavItem[] {
  const existingPaths = new Set(existingItems.map((item) => normalizePath(item.path).toLowerCase()));
  const seen = new Set(existingPaths);
  const out: PluginNavItem[] = [];
  for (const route of routes) {
    const item = routeToNavItem(route);
    if (!item) continue;
    const key = item.path.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

function findTarget(groups: NavGroup[], target: string): { groupIndex: number; itemIndex: number } | null {
  const normalized = target.trim().toLowerCase().replace(/^\/+/, "");
  if (!normalized) return null;
  for (let groupIndex = 0; groupIndex < groups.length; groupIndex++) {
    const group = groups[groupIndex];
    for (let itemIndex = 0; itemIndex < group.items.length; itemIndex++) {
      const keys = targetKeys(group.items[itemIndex]);
      if (keys.has(normalized) || keys.has(`/${normalized}`)) {
        return { groupIndex, itemIndex };
      }
    }
  }
  return null;
}

function pluginGroupIndex(groups: NavGroup[]): number {
  const integrations = groups.findIndex((group) => group.label.toLowerCase() === "integrations");
  return integrations >= 0 ? integrations + 1 : Math.max(0, groups.length - 1);
}

export function navWithPluginRoutes(baseNav: NavGroup[], routes: DashboardPluginRoute[]): NavGroup[] {
  const groups = baseNav.map((group) => ({
    ...group,
    items: group.items.map((item) => ({ ...item })),
  }));
  const baseItems = groups.flatMap((group) => group.items);
  const items = pluginNavItems(routes, baseItems);
  const pluginBucket: PluginNavItem[] = [];

  for (const item of items) {
    const match = /^((?:before|after)):(.+)$/i.exec(item.position || "");
    if (!match) {
      pluginBucket.push(item);
      continue;
    }
    const target = findTarget(groups, match[2]);
    if (!target) {
      pluginBucket.push(item);
      continue;
    }
    const offset = match[1].toLowerCase() === "after" ? 1 : 0;
    groups[target.groupIndex].items.splice(target.itemIndex + offset, 0, item);
  }

  if (pluginBucket.length) {
    groups.splice(pluginGroupIndex(groups), 0, {
      label: "Plugins",
      items: pluginBucket,
    });
  }
  return groups;
}
