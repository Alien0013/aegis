import { AEGIS_INSTALL_THEME, installThemeToken } from './install';

if (AEGIS_INSTALL_THEME.product !== 'AEGIS' || installThemeToken('accent') !== '#a855f7') {
  throw new Error('AEGIS_INSTALL_THEME should expose install tokens');
}
