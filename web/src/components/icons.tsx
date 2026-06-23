// Minimal inline-SVG icon set (no external dependency). Stroke-based, inherits
// currentColor. Add a path under PATHS and reference it by name.

import type { SVGProps } from "react";

const PATHS: Record<string, string> = {
  overview: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z",
  chat: "M21 11.5a8.38 8.38 0 0 1-8.5 8.5 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8A8.5 8.5 0 0 1 21 11.5z",
  sessions: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  models: "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  keys: "M21 2l-2 2m-7.6 7.6a5 5 0 1 0-2 2L13 11l2 2 2-2 2 2 2-2-4-4z",
  tools: "M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2-2 2.6-2.6z",
  skills: "M12 2l3 7h7l-5.5 4 2 7L12 16l-6.5 4 2-7L2 9h7l3-7z",
  memory: "M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 7h10v10H7z",
  cron: "M12 6v6l4 2m6-2a10 10 0 1 1-20 0 10 10 0 0 1 20 0z",
  mcp: "M12 22v-5m0-10V2M5 9a3 3 0 0 0 0 6m14-6a3 3 0 0 1 0 6M9 12h6",
  channels: "M4 4h16v12H5.2L4 17.2V4zm4 4h8M8 11h5",
  webhooks: "M18 16.5a3.5 3.5 0 1 0-3.4-4.4M9 8a3.5 3.5 0 1 0 1 6.8M6.5 18a3.5 3.5 0 1 0 3.4-4.4",
  plugins: "M14 7h3a2 2 0 0 1 2 2v3m0 0v3a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-3m0 0V9a2 2 0 0 1 2-2h3M9 3v4m6-4v4",
  profiles: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm13 10v-2a4 4 0 0 0-3-3.9",
  files: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z",
  logs: "M4 4h16v16H4zM8 8h8M8 12h8M8 16h5",
  system: "M9 3h6v3H9zM3 9v6h3V9zm15 0v6h3V9zM9 18h6v3H9zM7 7h10v10H7z",
  analytics: "M3 21h18M7 21V10m5 11V4m5 17v-7",
  config: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm8-3a8 8 0 0 0-.2-1.8l2-1.6-2-3.4-2.4 1a8 8 0 0 0-3-1.8L14 1h-4l-.4 2.6a8 8 0 0 0-3 1.8l-2.4-1-2 3.4 2 1.6A8 8 0 0 0 4 12a8 8 0 0 0 .2 1.8l-2 1.6 2 3.4 2.4-1a8 8 0 0 0 3 1.8L10 23h4l.4-2.6a8 8 0 0 0 3-1.8l2.4 1 2-3.4-2-1.6A8 8 0 0 0 20 12z",
  chevronRight: "M9 18l6-6-6-6",
  chevronDown: "M6 9l6 6 6-6",
  check: "M20 6L9 17l-5-5",
  alert: "M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  x: "M18 6L6 18M6 6l12 12",
  plus: "M12 5v14M5 12h14",
  refresh: "M21 12a9 9 0 1 1-3-6.7L21 8m0-5v5h-5",
  search: "M21 21l-4.3-4.3M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z",
  zap: "M13 2L3 14h8l-1 8 10-12h-8l1-8z",
  send: "M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z",
  trash: "M3 6h18M8 6V4h8v2m-9 0v14a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6",
  terminal: "M4 17l6-6-6-6M12 19h8",
  agents: "M16 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2M9.5 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM17 11l2 2 4-5M18 18h3m-1.5-1.5V20",
  shield: "M12 2l8 3v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V5l8-3z",
  database: "M12 8c5 0 8-1.3 8-3s-3-3-8-3-8 1.3-8 3 3 3 8 3zm8 0v5c0 1.7-3 3-8 3s-8-1.3-8-3V8m16 5v5c0 1.7-3 3-8 3s-8-1.3-8-3v-5",
  activity: "M22 12h-4l-3 9L9 3l-3 9H2",
  external: "M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6",
  command: "M6 6a3 3 0 1 1 3 3v6a3 3 0 1 1-3-3h12a3 3 0 1 1-3 3V9a3 3 0 1 1 3-3H6z",
  cornerDownLeft: "M9 10 4 15l5 5M20 4v7a4 4 0 0 1-4 4H4",
  menu: "M4 6h16M4 12h16M4 18h16",
  panelLeft: "M4 4h16v16H4zM9 4v16",
  circle: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z",
  more: "M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM19 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM5 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
  winMin: "M5 12h14",
  winMax: "M5 5h14v14H5z",
  winRestore: "M8 8V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2M4 10a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z",
  kanban: "M4 4h4v16H4zM10 4h4v10h-4zM16 4h4v13h-4z",
  play: "M6 4l14 8-14 8V4z",
  download: "M12 3v12m0 0l-4-4m4 4l4-4M5 21h14",
  upload: "M12 21V9m0 0 4 4m-4-4-4 4M5 3h14",
};

export type IconName = keyof typeof PATHS;

export function Icon({
  name, size = 16, ...rest
}: { name: string; size?: number } & SVGProps<SVGSVGElement>) {
  const d = PATHS[name] ?? PATHS.activity;
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={1.8} strokeLinecap="round"
      strokeLinejoin="round" aria-hidden {...rest}
    >
      <path d={d} />
    </svg>
  );
}
