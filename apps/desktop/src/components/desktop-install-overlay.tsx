import { AEGIS_INSTALL_THEME } from '../themes/install';

export type DesktopInstallOverlayProps = {
  stage?: string;
};

export function desktopInstallOverlayCopy(props: DesktopInstallOverlayProps = {}): string {
  const stage = props.stage || 'Preparing installer';
  return `${AEGIS_INSTALL_THEME.product} desktop install: ${stage}`;
}

export function DesktopInstallOverlay(props: DesktopInstallOverlayProps): string {
  return desktopInstallOverlayCopy(props);
}
