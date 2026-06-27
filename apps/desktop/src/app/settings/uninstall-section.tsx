import { providerSetupErrorMessage } from '../../lib/provider-setup-errors';

export type UninstallSectionProps = {
  canUninstall?: boolean;
  error?: string;
};

export function uninstallSectionCopy(props: UninstallSectionProps = {}): string {
  if (props.error) return providerSetupErrorMessage({ provider: 'desktop uninstall', message: props.error });
  return props.canUninstall === false
    ? 'AEGIS uninstall is unavailable until the desktop bundle finishes installing.'
    : 'Remove AEGIS desktop files while keeping your agent data unless purge is selected.';
}

export function UninstallSection(props: UninstallSectionProps): string {
  return uninstallSectionCopy(props);
}
