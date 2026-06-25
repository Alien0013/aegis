// Left navigation rail: permanent on desktop, off-canvas on smaller screens.

import { useMemo } from "react";
import { NavLink } from "react-router-dom";
import { cn } from "../lib/cn";
import { NAV } from "../lib/nav";
import { navWithPluginRoutes } from "../lib/pluginNav";
import { useApi } from "../lib/useApi";
import { useDashboardPluginHost } from "../plugins/host";
import { Icon } from "./icons";

interface StatusPayload {
  provider?: string;
  model?: string;
  provider_error?: string;
  tools?: number;
  skills?: number;
  active_sessions?: number;
  gateway_running?: boolean;
  gateway_state?: string;
  version?: string;
}

export function Sidebar({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const { routes: pluginRoutes } = useDashboardPluginHost();
  const navGroups = useMemo(() => navWithPluginRoutes(NAV, pluginRoutes), [pluginRoutes]);
  return (
    <>
      <button
        type="button"
        aria-label="Close navigation"
        onClick={onClose}
        className={cn(
          "fixed inset-0 z-40 bg-black/55 backdrop-blur-sm transition-opacity lg:hidden",
          open ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      />
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[min(86vw,var(--side))] shrink-0 flex-col border-r border-border bg-bg/95 shadow-2xl backdrop-blur",
          "transition-transform duration-200 lg:sticky lg:top-0 lg:z-auto lg:h-full lg:w-[var(--side)] lg:translate-x-0 lg:bg-surface/42 lg:shadow-none",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
          <div className="flex min-w-0 items-center gap-2">
            <div className="min-w-0 font-mono leading-none">
              <div className="text-lg font-bold tracking-wide text-text">AEGIS</div>
              <div className="text-lg font-bold tracking-wide text-text">AGENT</div>
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close navigation"
            title="Close navigation"
            className="grid h-8 w-8 place-items-center rounded-[var(--radius)] text-faint hover:bg-surface-2 hover:text-text lg:hidden"
          >
            <Icon name="x" size={17} />
          </button>
        </div>

        <nav className="scroll-thin flex-1 space-y-2 overflow-y-auto px-2 py-3">
          {navGroups.map((group) => (
            <div key={group.label}>
              <div className="px-2 pb-1 pt-2 font-mono text-[10px] text-faint">
                {group.label}
              </div>
              <div className="space-y-0.5">
                {group.items.map((item) => (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    end={item.path === "/"}
                    className={({ isActive }) => cn(
                      "group relative flex h-8 items-center gap-2.5 rounded-[var(--radius)] border px-2.5 font-mono text-[12px] uppercase tracking-wide transition-colors",
                      isActive
                        ? "border-primary/55 bg-primary/15 font-semibold text-primary"
                        : "border-transparent text-dim hover:border-border hover:bg-surface-2 hover:text-text",
                    )}
                  >
                    <Icon name={item.icon} size={16} className="shrink-0" />
                    <span className="truncate">{item.label}</span>
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>
        <StatusFooter />
      </aside>
    </>
  );
}

function StatusFooter() {
  const status = useApi<StatusPayload>("status");
  const s = status.data;
  const ready = !status.error && !s?.provider_error;
  const gateway = s?.gateway_state || (s?.gateway_running ? "Running" : "Offline");
  return (
    <div className="border-t border-border">
      <div className="space-y-1 px-3 py-3 font-mono text-[11px]">
        <div className="text-faint">System</div>
        <div className="flex items-center justify-between gap-2 text-dim">
          <span>Gateway Status:</span>
          <span className={ready ? "text-success" : "text-danger"}>{gateway}</span>
        </div>
        <div className="flex items-center justify-between gap-2 text-dim">
          <span>Active Sessions:</span>
          <span className="text-text">{s?.active_sessions ?? 0}</span>
        </div>
      </div>
      <div className="border-t border-border px-3 py-3">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <span className="font-mono text-[10px] uppercase tracking-wide text-faint">Runtime</span>
          <span className={cn("h-2 w-2 rounded-full", ready ? "bg-success" : "bg-danger")} />
        </div>
        <div className="truncate font-mono text-xs text-text">{s?.model || "model unavailable"}</div>
        <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-faint">
          <span className="truncate">{s?.provider || "provider"}</span>
          <span>{s?.tools ?? "-"} tools</span>
        </div>
        <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-faint">
          <span>{s?.skills ?? "-"} skills loaded</span>
          <span>{s?.version ? `v${s.version}` : ""}</span>
        </div>
      </div>
    </div>
  );
}
