// Theme picker — swatch grid in a popover. Persists via ThemeProvider.

import { useEffect, useRef, useState } from "react";
import { cn } from "../lib/cn";
import { useTheme } from "../themes/ThemeProvider";
import { Icon } from "./icons";

export function ThemeSwitcher({ up }: { up?: boolean }) {
  const { theme, themes, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title="Theme"
        className="flex items-center gap-2 rounded-[var(--radius)] border border-border bg-surface px-2.5 py-1.5 text-xs text-dim hover:text-text"
      >
        <span className="flex gap-0.5">
          {theme.swatch.map((c, i) => (
            <span key={i} className="h-3 w-3 rounded-full border border-black/20" style={{ background: c }} />
          ))}
        </span>
        <span className="hidden sm:inline">{theme.label}</span>
        <Icon name="chevronDown" size={12} />
      </button>

      {open && (
        <div className={cn(
          "animate-fade-in absolute right-0 z-50 w-60 rounded-[calc(var(--radius)+2px)] border border-border bg-surface p-1.5 shadow-2xl",
          up ? "bottom-full mb-1.5" : "mt-1.5",
        )}>
          {themes.map((t) => (
            <button
              key={t.name}
              type="button"
              onClick={() => { setTheme(t.name); setOpen(false); }}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-[var(--radius)] px-2 py-1.5 text-left hover:bg-surface-2",
                t.name === theme.name && "bg-surface-2",
              )}
            >
              <span className="flex shrink-0 gap-0.5">
                {t.swatch.map((c, i) => (
                  <span key={i} className="h-4 w-4 rounded-full border border-black/20" style={{ background: c }} />
                ))}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-text">{t.label}</span>
                <span className="block truncate text-[11px] text-faint">{t.description}</span>
              </span>
              {t.name === theme.name && <Icon name="check" size={14} className="shrink-0 text-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
