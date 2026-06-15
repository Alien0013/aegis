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

const BASE_LAYOUT: ThemeLayout = { radius: "4px", density: "compact" };

export const aegisDark: DashboardTheme = {
  name: "aegis-dark",
  label: "AEGIS Teal",
  description: "Deep teal operator console with ivory controls",
  swatch: ["#031c19", "#f3dfbf", "#39d98a"],
  palette: {
    bg: "#031c19",
    surface: "#062823",
    surface2: "#0b312b",
    border: "#25534b",
    border2: "#3f766b",
    text: "#fff2df",
    textDim: "#d3c8b0",
    textFaint: "#8f9b86",
    primary: "#f3dfbf",
    primaryFg: "#05221e",
    success: "#39d98a",
    warning: "#e6c36f",
    danger: "#ff6b6b",
    info: "#7dc8d8",
  },
  typography: BASE_TYPE,
  layout: BASE_LAYOUT,
  termBg: "#031c19",
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

export const dracula: DashboardTheme = {
  name: "dracula",
  label: "Dracula",
  description: "Purple and pink on slate — the classic",
  swatch: ["#282a36", "#bd93f9", "#50fa7b"],
  palette: {
    bg: "#1e2029",
    surface: "#282a36",
    surface2: "#343746",
    border: "#3c4055",
    border2: "#4d5273",
    text: "#f8f8f2",
    textDim: "#b9bcd0",
    textFaint: "#6f7494",
    primary: "#bd93f9",
    primaryFg: "#1e2029",
    success: "#50fa7b",
    warning: "#f1fa8c",
    danger: "#ff5555",
    info: "#8be9fd",
  },
  typography: { ...BASE_TYPE, fontMono: `"JetBrains Mono", ${MONO}`,
    fontUrl: "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" },
  layout: { radius: "9px", density: "comfortable" },
  termBg: "#282a36",
};

export const gruvbox: DashboardTheme = {
  name: "gruvbox",
  label: "Gruvbox",
  description: "Retro warm earth tones — cozy and readable",
  swatch: ["#282828", "#fabd2f", "#b8bb26"],
  palette: {
    bg: "#1d2021",
    surface: "#282828",
    surface2: "#32302f",
    border: "#3c3836",
    border2: "#504945",
    text: "#ebdbb2",
    textDim: "#bdae93",
    textFaint: "#7c6f64",
    primary: "#fabd2f",
    primaryFg: "#1d2021",
    success: "#b8bb26",
    warning: "#fe8019",
    danger: "#fb4934",
    info: "#83a598",
  },
  typography: { ...BASE_TYPE, fontMono: `"IBM Plex Mono", ${MONO}`,
    fontUrl: "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&display=swap" },
  layout: { radius: "6px", density: "comfortable" },
  termBg: "#1d2021",
};

export const solarized: DashboardTheme = {
  name: "solarized",
  label: "Solarized",
  description: "Precision teal on deep navy — low fatigue",
  swatch: ["#002b36", "#268bd2", "#2aa198"],
  palette: {
    bg: "#002b36",
    surface: "#073642",
    surface2: "#0a4250",
    border: "#0f4d5c",
    border2: "#13647a",
    text: "#eee8d5",
    textDim: "#93a1a1",
    textFaint: "#657b83",
    primary: "#268bd2",
    primaryFg: "#00212b",
    success: "#2aa198",
    warning: "#b58900",
    danger: "#dc322f",
    info: "#6c71c4",
  },
  typography: BASE_TYPE,
  layout: { radius: "7px", density: "comfortable" },
  termBg: "#002b36",
};

export const latte: DashboardTheme = {
  name: "latte",
  label: "Latte",
  description: "Soft pastel light — gentle daytime mode",
  swatch: ["#eff1f5", "#8839ef", "#40a02b"],
  palette: {
    bg: "#eff1f5",
    surface: "#ffffff",
    surface2: "#e6e9ef",
    border: "#dce0e8",
    border2: "#ccd0da",
    text: "#4c4f69",
    textDim: "#6c6f85",
    textFaint: "#9ca0b0",
    primary: "#8839ef",
    primaryFg: "#ffffff",
    success: "#40a02b",
    warning: "#df8e1d",
    danger: "#d20f39",
    info: "#1e66f5",
  },
  typography: { ...BASE_TYPE, fontSans: `"Inter", ${SANS}`,
    fontUrl: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    letterSpacing: "-0.005em" },
  layout: { radius: "12px", density: "comfortable" },
  termBg: "#1e1e2e",
};

export const THEMES: DashboardTheme[] = [
  aegisDark, aegisLight, midnight, ember, mono, cyberpunk, rose, nord,
  dracula, gruvbox, solarized, latte,
];

export const THEME_MAP: Record<string, DashboardTheme> = Object.fromEntries(
  THEMES.map((t) => [t.name, t]),
);

export const DEFAULT_THEME = "aegis-dark";
