// Bridge to the Electron desktop shell (exposed by preload-app.js as
// window.aegisDesktop). In a browser tab this is undefined, so `isDesktop` is
// the single source of truth for "are we running inside the native app?" —
// the custom titlebar and window controls only render when it's true.

export interface DesktopConnection {
  mode?: string;
  source?: string;
  baseUrl?: string;
  wsUrl?: string;
  desktop?: {
    updater?: DesktopUpdaterStatus;
    updateEligibility?: { ok?: boolean; reason?: string };
  };
  backend?: {
    running?: boolean;
    pid?: number | null;
    port?: number;
    command?: string;
    args?: string[];
    startedAt?: string;
    uptimeMs?: number;
    crashRestarts?: number;
    maxCrashRestarts?: number;
    logPath?: string;
    userDataPath?: string;
    env?: Record<string, string>;
  };
}

export interface DesktopUpdaterStatus {
  stage?: string;
  message?: string;
  error?: string;
  version?: string;
  checking?: boolean;
  lastCheckedAt?: string;
  updatedAt?: string;
}

export interface DesktopBridge {
  isDesktop: boolean;
  platform: string;
  minimize(): void;
  maximizeToggle(): void;
  close(): void;
  isMaximized(): Promise<boolean>;
  onMaximizeChange(cb: (maximized: boolean) => void): () => void;
  openExternal(url: string): void;
  restartBackend(): void;
  getConnection?(): Promise<DesktopConnection>;
  checkForUpdates?(): Promise<DesktopUpdaterStatus>;
  getUpdateStatus?(): Promise<DesktopUpdaterStatus>;
  api?<T = unknown>(request: { method?: string; path: string; body?: unknown }): Promise<T>;
}

export const desktop: DesktopBridge | undefined = (
  window as unknown as { aegisDesktop?: DesktopBridge }
).aegisDesktop;

export const isDesktop = !!desktop?.isDesktop;
export const isMac = desktop?.platform === "darwin";
