// Built-in AEGIS dashboard themes — original palettes, each pairing a color
// scheme with its own typography and layout feel. Add a theme here and it shows
// up in the picker automatically.

import type { DashboardTheme, ThemeLayout, ThemeTypography } from "./types";

const SANS =
  'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const MONO = 'ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace';

const BASE_TYPE: ThemeTypography = {
  fontSans: SANS,
  fontMono: MONO,
  baseSize: "14px",
  lineHeight: "1.5",
  letterSpacing: "0",
};

const BASE_LAYOUT: ThemeLayout = { radius: "10px", density: "comfortable" };

export const aegisDark: DashboardTheme = {
  name: "aegis-dark",
  label: "AEGIS Dark",
  description: "Warm gold on deep charcoal — the signature look",
  swatch: ["#0b0d10", "#e0a564", "#7ecf8f"],
  palette: {
    bg: "#0b0d10",
    surface: "#14171c",
    surface2: "#1c2027",
    border: "#262b33",
    border2: "#363d48",
    text: "#f1efe8",
    textDim: "#a4a89f",
    textFaint: "#6c7269",
    primary: "#e0a564",
    primaryFg: "#1a1206",
    success: "#7ecf8f",
    warning: "#e8b86d",
    danger: "#ff6b6b",
    info: "#6fb7d8",
  },
  typography: BASE_TYPE,
  layout: BASE_LAYOUT,
  termBg: "#0b0d10",
};

export const aegisLight: DashboardTheme = {
  name: "aegis-light",
  label: "AEGIS Light",
  description: "Clean ink-on-paper with a cobalt accent",
  swatch: ["#f6f7f9", "#2f6bff", "#0c8f88"],
  palette: {
    bg: "#f5f6f8",
    surface: "#ffffff",
    surface2: "#eef0f3",
    border: "#e2e5ea",
    border2: "#cfd4dc",
    text: "#16191f",
    textDim: "#586072",
    textFaint: "#8a92a1",
    primary: "#2f6bff",
    primaryFg: "#ffffff",
    success: "#0c8f88",
    warning: "#b8770a",
    danger: "#d83a52",
    info: "#2f6bff",
  },
  typography: { ...BASE_TYPE, fontSans: `"Inter", ${SANS}`,
    fontUrl: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    letterSpacing: "-0.005em" },
  layout: BASE_LAYOUT,
  termBg: "#1a1d24",
};

export const midnight: DashboardTheme = {
  name: "midnight",
  label: "Midnight",
  description: "Blue-violet with cool neon accents",
  swatch: ["#0a0a1f", "#a78bfa", "#22d3ee"],
  palette: {
    bg: "#0a0a1f",
    surface: "#13132e",
    surface2: "#1c1c40",
    border: "#272750",
    border2: "#3a3a6e",
    text: "#ebe9ff",
    textDim: "#a5a3cc",
    textFaint: "#6f6d99",
    primary: "#a78bfa",
    primaryFg: "#160a2e",
    success: "#34d399",
    warning: "#fbbf24",
    danger: "#fb7185",
    info: "#22d3ee",
  },
  typography: { ...BASE_TYPE, fontSans: `"Inter", ${SANS}`, fontMono: `"JetBrains Mono", ${MONO}`,
    fontUrl: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap",
    letterSpacing: "-0.005em" },
  layout: { radius: "13px", density: "comfortable" },
  termBg: "#0a0a1f",
};

export const ember: DashboardTheme = {
  name: "ember",
  label: "Ember",
  description: "Warm crimson and bronze — forge vibes",
  swatch: ["#1a0a06", "#f97316", "#fbbf24"],
  palette: {
    bg: "#1a0a06",
    surface: "#26120b",
    surface2: "#321a10",
    border: "#42241681",
    border2: "#5c3420",
    text: "#ffe9d6",
    textDim: "#c9a288",
    textFaint: "#8a6750",
    primary: "#f97316",
    primaryFg: "#1a0a06",
    success: "#84cc16",
    warning: "#fbbf24",
    danger: "#ef4444",
    info: "#fb923c",
  },
  typography: { ...BASE_TYPE, fontSans: `"Spectral", Georgia, serif`, fontMono: `"IBM Plex Mono", ${MONO}`,
    fontUrl: "https://fonts.googleapis.com/css2?family=Spectral:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" },
  layout: { radius: "5px", density: "comfortable" },
  termBg: "#1a0a06",
};

export const mono: DashboardTheme = {
  name: "mono",
  label: "Mono",
  description: "Minimal grayscale — maximum focus",
  swatch: ["#0e0e0e", "#eaeaea", "#9a9a9a"],
  palette: {
    bg: "#0e0e0e",
    surface: "#161616",
    surface2: "#1f1f1f",
    border: "#2a2a2a",
    border2: "#3d3d3d",
    text: "#f2f2f2",
    textDim: "#9a9a9a",
    textFaint: "#646464",
    primary: "#eaeaea",
    primaryFg: "#0e0e0e",
    success: "#9fd39f",
    warning: "#d8c879",
    danger: "#e08585",
    info: "#8fb8d8",
  },
  typography: { ...BASE_TYPE, fontSans: `"IBM Plex Sans", ${SANS}`, fontMono: `"IBM Plex Mono", ${MONO}`,
    fontUrl: "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" },
  layout: { radius: "2px", density: "compact" },
  termBg: "#0e0e0e",
};

export const cyberpunk: DashboardTheme = {
  name: "cyberpunk",
  label: "Cyberpunk",
  description: "Neon green on black — matrix terminal",
  swatch: ["#040608", "#00ff88", "#ffd700"],
  palette: {
    bg: "#040608",
    surface: "#08130d",
    surface2: "#0d1e14",
    border: "#123022",
    border2: "#1d4a35",
    text: "#c9f7da",
    textDim: "#5fae80",
    textFaint: "#356b4f",
    primary: "#00ff88",
    primaryFg: "#04110a",
    success: "#00ff88",
    warning: "#ffd700",
    danger: "#ff0055",
    info: "#22d3ee",
  },
  typography: { ...BASE_TYPE, fontSans: `"Share Tech Mono", ${MONO}`, fontMono: `"Share Tech Mono", ${MONO}`,
    fontUrl: "https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" },
  layout: { radius: "0", density: "compact" },
  termBg: "#040608",
};

export const rose: DashboardTheme = {
  name: "rose",
  label: "Rosé",
  description: "Soft pink and ivory — easy on the eyes",
  swatch: ["#1a0f15", "#f9a8d4", "#fbcfe8"],
  palette: {
    bg: "#1a0f15",
    surface: "#26161f",
    surface2: "#321e2a",
    border: "#3d2532",
    border2: "#553446",
    text: "#ffe4ef",
    textDim: "#caa0b4",
    textFaint: "#8a6878",
    primary: "#f9a8d4",
    primaryFg: "#1a0f15",
    success: "#86efac",
    warning: "#fcd34d",
    danger: "#fb7185",
    info: "#a5b4fc",
  },
  typography: { ...BASE_TYPE, fontSans: `"Fraunces", Georgia, serif`, fontMono: `"DM Mono", ${MONO}`,
    fontUrl: "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=DM+Mono:wght@400;500&display=swap" },
  layout: { radius: "16px", density: "spacious" },
  termBg: "#1a0f15",
};

export const nord: DashboardTheme = {
  name: "nord",
  label: "Nord",
  description: "Arctic blues — calm and balanced",
  swatch: ["#2e3440", "#88c0d0", "#a3be8c"],
  palette: {
    bg: "#2e3440",
    surface: "#343c4a",
    surface2: "#3b4252",
    border: "#434c5e",
    border2: "#4c566a",
    text: "#eceff4",
    textDim: "#b8c0cf",
    textFaint: "#7b869c",
    primary: "#88c0d0",
    primaryFg: "#22272f",
    success: "#a3be8c",
    warning: "#ebcb8b",
    danger: "#bf616a",
    info: "#81a1c1",
  },
  typography: BASE_TYPE,
  layout: { radius: "8px", density: "comfortable" },
  termBg: "#2e3440",
};

export const THEMES: DashboardTheme[] = [
  aegisDark, aegisLight, midnight, ember, mono, cyberpunk, rose, nord,
];

export const THEME_MAP: Record<string, DashboardTheme> = Object.fromEntries(
  THEMES.map((t) => [t.name, t]),
);

export const DEFAULT_THEME = "aegis-dark";
