import {terminalSetupCommand} from '../../../lib/terminalSetup.js';

// AEGIS setup slash command surface for the Ink client.
export function setupSlashCommand(args = ''): string[] {
  const command = terminalSetupCommand();
  const extra = args.trim();
  if (extra) command.push(...extra.split(/\s+/));
  return command;
}
