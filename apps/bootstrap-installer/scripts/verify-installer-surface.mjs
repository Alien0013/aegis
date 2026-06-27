import { accessSync, constants, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const app = resolve(here, '..');
const root = resolve(app, '../..');
const requiredRoot = ['install.sh', 'install.ps1', 'scripts/install.sh', 'scripts/install.ps1', 'scripts/install.cmd'];
const requiredApp = [
  '.gitignore',
  'index.html',
  'src/app.tsx',
  'src/main.tsx',
  'src/store.ts',
  'src/styles.css',
  'src/lib/utils.ts',
  'src/components/button.tsx',
  'src/routes/welcome.tsx',
  'src/routes/progress.tsx',
  'src/routes/success.tsx',
  'src/routes/failure.tsx',
  'src/vite-env.d.ts',
  'src-tauri/Cargo.toml',
  'src-tauri/build.rs',
  'src-tauri/tauri.conf.json',
  'src-tauri/capabilities/default.json',
  'src-tauri/src/bootstrap.rs',
  'src-tauri/src/events.rs',
  'src-tauri/src/install_script.rs',
  'src-tauri/src/lib.rs',
  'src-tauri/src/main.rs',
  'src-tauri/src/paths.rs',
  'src-tauri/src/powershell.rs',
  'src-tauri/src/update.rs',
  'tsconfig.json',
  'tsconfig.node.json',
  'vite.config.ts',
];

for (const rel of requiredRoot) accessSync(resolve(root, rel), constants.R_OK);
for (const rel of requiredApp) accessSync(resolve(app, rel), constants.R_OK);

const manifest = JSON.parse(readFileSync(resolve(app, 'package.json'), 'utf8'));
if (JSON.stringify(manifest.aegis?.ui_routes) !== JSON.stringify(['welcome', 'progress', 'success', 'failure'])) {
  throw new Error('bootstrap installer manifest must declare the expected AEGIS UI routes');
}
const shell = readFileSync(resolve(root, 'install.sh'), 'utf8');
const powershell = readFileSync(resolve(root, 'install.ps1'), 'utf8');
const index = readFileSync(resolve(app, 'index.html'), 'utf8');
const welcome = readFileSync(resolve(app, 'src/routes/welcome.tsx'), 'utf8');
const installScript = readFileSync(resolve(app, 'src-tauri/src/install_script.rs'), 'utf8');
const powershellBridge = readFileSync(resolve(app, 'src-tauri/src/powershell.rs'), 'utf8');
const tauri = readFileSync(resolve(app, 'src-tauri/src/bootstrap.rs'), 'utf8');
if (!shell.includes('AEGIS Installer') || !shell.includes('aegis update')) {
  throw new Error('install.sh does not expose the expected AEGIS installer surface');
}
if (!powershell.includes('AEGIS installed') || !powershell.includes('aegis setup')) {
  throw new Error('install.ps1 does not expose the expected AEGIS installer surface');
}
if (!index.includes('AEGIS Bootstrap Installer') || !welcome.includes('Install AEGIS')) {
  throw new Error('bootstrap UI does not expose the expected AEGIS install copy');
}
if (!installScript.includes('install.sh') || !powershellBridge.includes('install.ps1')) {
  throw new Error('bootstrap native bridge must delegate to native install.sh and install.ps1');
}
if (!tauri.includes('run_bootstrap_install') || !tauri.includes('bootstrap_plan')) {
  throw new Error('bootstrap Tauri bridge must expose install plan and execution commands');
}

console.log('AEGIS installer surface verified');
