import { terminalSetupCommand } from '../lib/terminalSetup';

export function setupHandoff(): string {
  return `Run ${terminalSetupCommand({ quick: true }).join(' ')} to finish AEGIS setup.`;
}
