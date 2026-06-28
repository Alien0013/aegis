import { AEGIS_INSTALL_THEME } from '../themes/install';
import { desktopSetupReadinessCopy, type DesktopSetupReadiness } from '../lib/setup-readiness';

export type DesktopInstallOverlayProps = {
  stage?: string;
  readiness?: DesktopSetupReadiness;
};

export function desktopInstallOverlayCopy(props: DesktopInstallOverlayProps = {}): string {
  const stage = props.stage || 'Preparing installer';
  const readiness = props.readiness ? ` ${desktopSetupReadinessCopy(props.readiness)}` : '';
  return `${AEGIS_INSTALL_THEME.product} desktop install: ${stage}.${readiness}`.trim();
}

export function DesktopInstallOverlay(props: DesktopInstallOverlayProps): string {
  return desktopInstallOverlayCopy(props);
}
