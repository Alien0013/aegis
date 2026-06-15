// Theme model for the AEGIS dashboard. A theme is more than colors: it also
// carries typography (fonts + scale) and layout (corner radius + density), so
// switching themes changes the whole personality of the UI — the same idea
// AEGIS uses, implemented here from scratch.

export interface ThemePalette {
  /** Page background. */
  bg: string;
  /** Card / panel surface. */
  surface: string;
  /** Raised surface (inputs, hovered rows). */
  surface2: string;
  /** Hairline borders. */
  border: string;
  /** Stronger borders / dividers. */
  border2: string;
  /** Primary body text. */
  text: string;
  /** Muted secondary text. */
  textDim: string;
  /** Faint tertiary text (timestamps, hints). */
  textFaint: string;
  /** Accent / primary action color. */
  primary: string;
  /** Text drawn on top of the primary color. */
  primaryFg: string;
  success: string;
  warning: string;
  danger: string;
  info: string;
}

export interface ThemeTypography {
  fontSans: string;
  fontMono: string;
  baseSize: string;
  lineHeight: string;
  letterSpacing?: string;
  /** Optional web-font stylesheet URL loaded when the theme activates. */
  fontUrl?: string;
}

export type Density = "compact" | "comfortable" | "spacious";

export interface ThemeLayout {
  radius: string;
  density: Density;
}

export interface DashboardTheme {
  name: string;
  label: string;
  description: string;
  /** Swatch shown in the theme picker: [bg, primary, accent/midtone]. */
  swatch: [string, string, string];
  palette: ThemePalette;
  typography: ThemeTypography;
  layout: ThemeLayout;
  /** Background for the embedded xterm terminal pane. */
  termBg: string;
}
