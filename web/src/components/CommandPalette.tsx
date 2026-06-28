// ⌘K / Ctrl+K command palette — fuzzy launcher for navigation, new chats, theme
// switching, and desktop actions. Mounted once at the app root. Open it from the
// keyboard, the titlebar trigger, or anywhere via openCommandPalette().

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { desktop, isDesktop } from "../lib/desktop";
import { NAV_ITEMS } from "../lib/nav";
import { pluginNavItems } from "../lib/pluginNav";
import { useDashboardPluginHost } from "../plugins/host";
import { useTheme } from "../themes/ThemeProvider";
import { Icon } from "./icons";

/** Open the command palette from anywhere (titlebar, buttons, shortcuts). */
export function openCommandPalette() {
  window.dispatchEvent(new Event("aegis:cmdk"));
}

interface Command {
  id: string;
  label: string;
  group: string;
  icon: string;
  hint?: string;
  run: () => void;
}

// Subsequence fuzzy score: lower is better, -1 = no match. Rewards prefixes and
// contiguous runs so "ses" ranks Sessions above a scattered match.
function score(query: string, text: string): number {
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  if (!q) return 0;
  if (t.startsWith(q)) return -100;
  const idx = t.indexOf(q);
  if (idx >= 0) return idx; // contiguous match, earlier is better
  let qi = 0, last = -1, gaps = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      if (last >= 0) gaps += ti - last - 1;
      last = ti;
      qi++;
    }
  }
  return qi === q.length ? 200 + gaps : -1;
}

export function CommandPalette() {
  const nav = useNavigate();
  const { themes, setTheme, theme } = useTheme();
  const { routes: pluginRoutes } = useDashboardPluginHost();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const commands = useMemo<Command[]>(() => {
    const go = (path: string) => () => nav(path);
    const list: Command[] = [
      { id: "new-chat", label: "New chat", group: "Actions", icon: "plus", hint: "Start a fresh conversation", run: () => nav("/app") },
      { id: "chat-app", label: "Open Chat app", group: "Actions", icon: "chat", run: () => nav("/app") },
      { id: "sessions", label: "Open Sessions", group: "Actions", icon: "sessions", run: () => nav("/sessions") },
      { id: "command-center", label: "Open Command Center", group: "Actions", icon: "command", run: () => nav("/command-center") },
    ];
    for (const i of NAV_ITEMS) {
      list.push({ id: `nav:${i.path}`, label: i.label, group: "Go to", icon: i.icon, run: go(i.path) });
    }
    for (const i of pluginNavItems(pluginRoutes, NAV_ITEMS)) {
      list.push({
        id: `plugin:${i.path}`,
        label: i.label,
        group: "Plugins",
        icon: i.icon,
        hint: i.plugin ? `Plugin: ${i.plugin}` : "Dashboard plugin route",
        run: go(i.path),
      });
    }
    for (const t of themes) {
      list.push({
        id: `theme:${t.name}`,
        label: `Theme: ${t.label}`,
        group: "Theme",
        icon: t.name === theme.name ? "check" : "config",
        hint: t.description,
        run: () => setTheme(t.name),
      });
    }
    if (isDesktop) {
      list.push({
        id: "open-browser", label: "Open in browser", group: "Desktop", icon: "external",
        run: () => desktop?.openExternal(`${location.origin}/`),
      });
      list.push({
        id: "restart-backend", label: "Restart backend", group: "Desktop", icon: "refresh",
        run: () => desktop?.restartBackend(),
      });
      const checkForUpdates = desktop?.checkForUpdates;
      if (checkForUpdates) {
        list.push({
          id: "check-updates", label: "Check for updates", group: "Desktop", icon: "refresh",
          run: () => { void checkForUpdates(); },
        });
      }
    }
    return list;
  }, [nav, pluginRoutes, themes, setTheme, theme.name]);

  const results = useMemo(() => {
    if (!query.trim()) return commands;
    return commands
      .map((c) => ({ c, s: score(query.trim(), `${c.label} ${c.group}`) }))
      .filter((x) => x.s >= 0)
      .sort((a, b) => a.s - b.s)
      .map((x) => x.c);
  }, [commands, query]);

  // Global open shortcut + the openCommandPalette() event.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("aegis:cmdk", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("aegis:cmdk", onOpen);
    };
  }, []);

  // Reset + focus on open.
  useEffect(() => {
    if (open) {
      setQuery("");
      setSel(0);
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [open]);

  useEffect(() => setSel(0), [query]);

  // Keep the selected row in view.
  useEffect(() => {
    listRef.current?.querySelector(`[data-i="${sel}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  if (!open) return null;

  const run = (c?: Command) => {
    if (!c) return;
    setOpen(false);
    c.run();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { setOpen(false); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); run(results[sel]); }
  };

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center bg-black/50 pt-[12vh] backdrop-blur-sm"
      onMouseDown={() => setOpen(false)}
    >
      <div
        className="animate-fade-in w-full max-w-xl overflow-hidden rounded-[calc(var(--radius)+4px)] border border-border bg-surface shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Icon name="search" size={16} className="shrink-0 text-faint" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search commands, pages, themes…"
            className="w-full bg-transparent py-3 text-sm text-text outline-none placeholder:text-faint"
          />
          <kbd className="hidden shrink-0 rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-faint sm:block">esc</kbd>
        </div>

        <div ref={listRef} className="scroll-thin max-h-[52vh] overflow-y-auto p-1.5">
          {results.length === 0 && (
            <div className="px-3 py-8 text-center text-sm text-faint">No matches</div>
          )}
          {results.map((c, i) => (
            <button
              key={c.id}
              data-i={i}
              onMouseMove={() => setSel(i)}
              onClick={() => run(c)}
              className={`flex w-full items-center gap-3 rounded-[var(--radius)] px-2.5 py-2 text-left transition-colors ${
                i === sel ? "bg-surface-2 text-text" : "text-dim hover:bg-surface-2/50"
              }`}
            >
              <Icon name={c.icon} size={15} className={i === sel ? "text-primary" : "text-faint"} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">{c.label}</span>
                {c.hint && <span className="block truncate text-[11px] text-faint">{c.hint}</span>}
              </span>
              <span className="shrink-0 text-[11px] text-faint">{c.group}</span>
              {i === sel && <Icon name="cornerDownLeft" size={13} className="shrink-0 text-faint" />}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
