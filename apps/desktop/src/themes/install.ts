export const AEGIS_INSTALL_THEME = {
  product: 'AEGIS',
  accent: '#a855f7',
  background: '#09090b',
  success: '#22c55e',
  warning: '#f59e0b',
  danger: '#ef4444',
} as const;

export function installThemeToken(name: keyof typeof AEGIS_INSTALL_THEME): string {
  return AEGIS_INSTALL_THEME[name];
}
