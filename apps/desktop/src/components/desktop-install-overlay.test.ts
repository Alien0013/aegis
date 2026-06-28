import { DesktopInstallOverlay } from './desktop-install-overlay';

const copy = DesktopInstallOverlay({
  stage: 'Checking setup readiness',
  readiness: { provider_configured: false, provider: 'openrouter', next_command: 'aegis setup' },
});
if (!copy.includes('AEGIS desktop setup') || !copy.includes('aegis setup')) {
  throw new Error('DesktopInstallOverlay should surface setup readiness guidance');
}
