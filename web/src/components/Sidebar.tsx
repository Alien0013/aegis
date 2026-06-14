// Left navigation rail — grouped links with active highlighting.

import { NavLink } from "react-router-dom";
import { cn } from "../lib/cn";
import { NAV } from "../lib/nav";
import { Icon } from "./icons";

export function Sidebar() {
  return (
    <aside
      className="flex shrink-0 flex-col border-r border-border bg-surface/60"
      style={{ width: "var(--side)" }}
    >
      <div className="flex items-center gap-2 px-4 py-4">
        <span className="grid h-8 w-8 place-items-center rounded-[var(--radius)] bg-primary text-primary-fg">
          <Icon name="shield" size={18} />
        </span>
        <div className="leading-tight">
          <div className="text-sm font-semibold tracking-tight text-text">AEGIS</div>
          <div className="text-[10px] uppercase tracking-widest text-faint">Control Panel</div>
        </div>
      </div>

      <nav className="scroll-thin flex-1 space-y-4 overflow-y-auto px-2 pb-4">
        {NAV.map((group) => (
          <div key={group.label}>
            <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-widest text-faint">
              {group.label}
            </div>
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  end={item.path === "/"}
                  className={({ isActive }) => cn(
                    "flex items-center gap-2.5 rounded-[var(--radius)] px-2.5 py-1.5 text-sm transition-colors",
                    isActive
                      ? "bg-primary/15 font-medium text-primary"
                      : "text-dim hover:bg-surface-2 hover:text-text",
                  )}
                >
                  <Icon name={item.icon} size={16} className="shrink-0" />
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>
    </aside>
  );
}
