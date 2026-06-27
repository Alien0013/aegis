import { terminalSetupCommand } from '../../../lib/terminalSetup';

export function setupSlashCommand(args = ''): string[] {
  const command = terminalSetupCommand();
  if (args.trim()) command.push(...args.trim().split(/\s+/));
  return command;
}
