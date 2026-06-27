export type UpdateCopyState = 'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'error';

const COPY: Record<UpdateCopyState, string> = {
  idle: 'AEGIS is ready.',
  checking: 'Checking for AEGIS updates...',
  available: 'AEGIS update available.',
  downloading: 'Downloading the AEGIS update...',
  ready: 'AEGIS update is ready to install.',
  error: 'AEGIS update check failed.',
};

export function formatUpdateCopy(state: UpdateCopyState, detail = ''): string {
  const base = COPY[state] || COPY.idle;
  return detail ? `${base} ${detail}` : base;
}
