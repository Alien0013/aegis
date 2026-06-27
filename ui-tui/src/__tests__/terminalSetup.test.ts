import { terminalSetupCommand } from '../lib/terminalSetup';

const command = terminalSetupCommand({ quick: true, toolsets: 'core,web' });
if (command.join(' ') !== 'aegis setup --quick --toolsets core,web') {
  throw new Error('terminalSetupCommand should build the AEGIS setup command');
}
