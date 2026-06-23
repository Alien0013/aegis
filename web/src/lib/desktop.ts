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
    repair?: DesktopRepairPanel;
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
    cwdSource?: string;
    env?: Record<string, string>;
    remote?: {
      url?: string;
      source?: string;
      tokenConfigured?: boolean;
    } | null;
  };
  settings?: DesktopSettings;
}

export interface DesktopRepairAction {
  id: string;
  label?: string;
  description?: string;
}

export interface DesktopRepairPanel {
  available?: boolean;
  actions?: DesktopRepairAction[];
}

export interface DesktopRepairResult {
  ok?: boolean;
  action?: string;
  cancelled?: boolean;
  restarting?: boolean;
  path?: string;
  key?: string;
  value?: string;
  error?: string;
  settings?: DesktopSettings;
}

export interface DesktopUpdaterStatus {
  stage?: string;
  message?: string;
  error?: string;
  version?: string;
  checking?: boolean;
  installable?: boolean;
  installing?: boolean;
  lastCheckedAt?: string;
  updatedAt?: string;
  downloadProgress?: {
    percent?: number;
    transferred?: number;
    total?: number;
    bytesPerSecond?: number;
  };
}

export interface DesktopInstallUpdateResult {
  ok?: boolean;
  error?: string;
  gateway?: unknown;
  status?: DesktopUpdaterStatus;
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
  openAgentsWindow?(): Promise<{ ok?: boolean; route?: string }>;
  restartBackend(): void;
  runRepairAction?(action: string | DesktopRepairAction): Promise<DesktopRepairResult>;
  getConnection?(): Promise<DesktopConnection>;
  getSettings?(): Promise<DesktopSettings>;
  setDefaultProjectDir?(path: string): Promise<{ ok?: boolean; settings?: DesktopSettings }>;
  setRemoteBackend?(settings: DesktopRemoteBackend): Promise<{
    ok?: boolean;
    restarting?: boolean;
    settings?: DesktopSettings;
  }>;
  chooseProjectDir?(): Promise<{ ok?: boolean; cancelled?: boolean; settings?: DesktopSettings }>;
  checkForUpdates?(): Promise<DesktopUpdaterStatus>;
  getUpdateStatus?(): Promise<DesktopUpdaterStatus>;
  installUpdate?(): Promise<DesktopInstallUpdateResult>;
  api?<T = unknown>(request: { method?: string; path: string; body?: unknown }): Promise<T>;
}

export interface DesktopSettings {
  defaultProjectDir?: string;
  backendEnv?: {
    AEGIS_HOME?: string;
    AEGIS_BIN?: string;
  };
  remoteBackend?: DesktopRemoteBackend;
  explicitLaunchCwd?: boolean;
  settingsPath?: string;
}

export interface DesktopRemoteBackend {
  url?: string;
  token?: string;
}

export const desktop: DesktopBridge | undefined = (
  window as unknown as { aegisDesktop?: DesktopBridge }
).aegisDesktop;

export const isDesktop = !!desktop?.isDesktop;
export const isMac = desktop?.platform === "darwin";
