import { accessSync, constants, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '../../..');
const required = ['install.sh', 'install.ps1', 'scripts/install.sh', 'scripts/install.ps1', 'scripts/install.cmd'];

for (const rel of required) {
  const path = resolve(root, rel);
  accessSync(path, constants.R_OK);
}

const shell = readFileSync(resolve(root, 'install.sh'), 'utf8');
const powershell = readFileSync(resolve(root, 'install.ps1'), 'utf8');
if (!shell.includes('AEGIS Installer') || !shell.includes('aegis update')) {
  throw new Error('install.sh does not expose the expected AEGIS installer surface');
}
if (!powershell.includes('AEGIS installed') || !powershell.includes('aegis setup')) {
  throw new Error('install.ps1 does not expose the expected AEGIS installer surface');
}

console.log('AEGIS installer surface verified');
