// Applies the active theme to the document as CSS custom properties, loads the
// theme's web font (once), and persists the choice. Components read the tokens
// via Tailwind utilities mapped in index.css (bg-surface, text-dim, …).

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { DashboardTheme, Density } from "./types";
import { DEFAULT_THEME, THEMES, THEME_MAP } from "./presets";

const STORAGE_KEY = "aegis_theme";

const DENSITY_SCALE: Record<Density, { gap: string; pad: string; row: string }> = {
  compact: { gap: "8px", pad: "10px", row: "30px" },
  comfortable: { gap: "12px", pad: "14px", row: "36px" },
  spacious: { gap: "16px", pad: "18px", row: "42px" },
};

function applyTheme(t: DashboardTheme): void {
  const r = document.documentElement;
  const p = t.palette;
  const set = (k: string, v: string) => r.style.setProperty(k, v);
  set("--bg", p.bg);
  set("--surface", p.surface);
  set("--surface-2", p.surface2);
  set("--border", p.border);
  set("--border-2", p.border2);
  set("--text", p.text);
  set("--text-dim", p.textDim);
  set("--text-faint", p.textFaint);
  set("--primary", p.primary);
  set("--primary-fg", p.primaryFg);
  set("--success", p.success);
  set("--warning", p.warning);
  set("--danger", p.danger);
  set("--info", p.info);
  set("--term-bg", t.termBg);
  set("--font-sans", t.typography.fontSans);
  set("--font-mono", t.typography.fontMono);
  set("--base-size", t.typography.baseSize);
  set("--line-height", t.typography.lineHeight);
  set("--letter-spacing", t.typography.letterSpacing ?? "0");
  set("--radius", t.layout.radius);
  const d = DENSITY_SCALE[t.layout.density];
  set("--gap", d.gap);
  set("--pad", d.pad);
  set("--row-h", d.row);
  r.dataset.theme = t.name;
}

function loadFont(url?: string): void {
  if (!url) return;
  const id = `themefont:${url}`;
  if (document.getElementById(id)) return;
  const link = document.createElement("link");
  link.id = id;
  link.rel = "stylesheet";
  link.href = url;
  document.head.appendChild(link);
}

interface ThemeCtx {
  theme: DashboardTheme;
  themes: DashboardTheme[];
  setTheme: (name: string) => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [name, setName] = useState<string>(
    () => localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME,
  );
  const theme = THEME_MAP[name] ?? THEME_MAP[DEFAULT_THEME];

  useEffect(() => {
    loadFont(theme.typography.fontUrl);
    applyTheme(theme);
    localStorage.setItem(STORAGE_KEY, theme.name);
  }, [theme]);

  const value = useMemo<ThemeCtx>(
    () => ({ theme, themes: THEMES, setTheme: setName }),
    [theme],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTheme must be used inside ThemeProvider");
  return ctx;
}
