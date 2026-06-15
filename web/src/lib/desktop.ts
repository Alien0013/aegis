// Bridge to the Electron desktop shell (exposed by preload-app.js as
// window.aegisDesktop). In a browser tab this is undefined, so `isDesktop` is
// the single source of truth for "are we running inside the native app?" —
// the custom titlebar and window controls only render when it's true.

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
}

export const desktop: DesktopBridge | undefined = (
  window as unknown as { aegisDesktop?: DesktopBridge }
).aegisDesktop;

export const isDesktop = !!desktop?.isDesktop;
export const isMac = desktop?.platform === "darwin";
