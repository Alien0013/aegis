export type DesktopSetupCheck = {
  id?: string;
  label?: string;
  ok?: boolean;
  detail?: string;
  command?: string;
};

export type DesktopSetupReadiness = {
  provider_configured?: boolean;
  provider?: string;
  model?: string;
  next_command?: string;
  checks?: DesktopSetupCheck[];
};

export function desktopSetupReadinessCopy(payload: DesktopSetupReadiness | null | undefined): string {
  const provider = payload?.provider || 'provider';
  const model = payload?.model ? ` / ${payload.model}` : '';
  const command = payload?.next_command || 'aegis setup';
  if (payload?.provider_configured) {
    return `AEGIS desktop setup is ready: ${provider}${model}.`;
  }
  return `AEGIS desktop setup needs provider auth. Run ${command}.`;
}

export function desktopSetupChecklist(payload: DesktopSetupReadiness | null | undefined): string[] {
  return (payload?.checks || []).map((check) => {
    const marker = check.ok ? 'ready' : 'needs attention';
    const label = check.label || check.id || 'setup check';
    const detail = check.detail || check.command || '';
    return detail ? `${label}: ${marker} — ${detail}` : `${label}: ${marker}`;
  });
}
