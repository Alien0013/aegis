export const AEGIS_PRODUCT_NAME = "AEGIS" as const;
export const AEGIS_COMMAND_NAME = "aegis" as const;
export const AEGIS_PROTOCOL_SCHEME = "aegis" as const;

export const AEGIS_RUNTIME_SURFACES = [
  "terminal-chat",
  "tui",
  "dashboard",
  "desktop",
  "gateway",
] as const;

export type AegisRuntimeSurface = (typeof AEGIS_RUNTIME_SURFACES)[number];
