export type TerminalSetupOptions = {
  quick?: boolean;
  toolsets?: string;
  skills?: string;
};

export function terminalSetupCommand(options: TerminalSetupOptions = {}): string[] {
  const command = ['aegis', 'setup'];
  if (options.quick) command.push('--quick');
  if (options.toolsets) command.push('--toolsets', options.toolsets);
  if (options.skills) command.push('--skills', options.skills);
  return command;
}
