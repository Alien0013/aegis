import { accessSync, constants, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const app = resolve(here, '..');
const root = resolve(app, '../..');
const manifest = JSON.parse(readFileSync(resolve(app, 'package.json'), 'utf8'));
const desktopPackage = JSON.parse(readFileSync(resolve(root, 'desktop/package.json'), 'utf8'));

const surfaces = manifest.aegis?.native_install_update_surfaces || {};
const required = {
  'desktop-uninstall': {
    rel: '../../desktop/electron/desktop-uninstall.cjs',
    contains: ['candidateDesktopUninstallScripts', 'desktopUninstallPlan'],
  },
  'update-status': {
    rel: '../../desktop/electron/updater-status.cjs',
    contains: ['initialUpdaterStatus', 'transitionUpdaterStatus'],
  },
  'gateway-update-coordination': {
    rel: '../../desktop/electron/gateway-update-coordination.cjs',
    contains: ['pauseGatewayForUpdate', 'resumeGatewayAfterUpdate'],
  },
  'build-stamp': {
    rel: '../../desktop/scripts/write-build-stamp.cjs',
    contains: ['writeBuildStamp', 'releaseBuildFailures'],
  },
  'stage-backend': {
    rel: '../../desktop/scripts/stage-backend.cjs',
    contains: ['stageBackend', 'backendStagePaths'],
  },
  'stage-uninstall': {
    rel: '../../desktop/scripts/stage-uninstall.cjs',
    contains: ['copyDesktopUninstallScript'],
  },
};

for (const [name, spec] of Object.entries(required)) {
  if (surfaces[name] !== spec.rel) {
    throw new Error(`desktop wrapper surface ${name} must map to ${spec.rel}`);
  }
  const target = resolve(app, spec.rel);
  accessSync(target, constants.R_OK);
  const body = readFileSync(target, 'utf8');
  for (const token of spec.contains) {
    if (!body.includes(token)) {
      throw new Error(`desktop native surface ${name} is missing token ${token}`);
    }
  }
}

const scripts = desktopPackage.scripts || {};
for (const key of ['build:stamp', 'build:backend', 'build:prepare', 'pack', 'dist']) {
  if (!scripts[key]) throw new Error(`desktop package is missing ${key}`);
}
if (!String(scripts['build:prepare']).includes('stage-uninstall.cjs')) {
  throw new Error('desktop build:prepare must stage the uninstall helper');
}

const resources = desktopPackage.build?.extraResources || [];
const resourceTargets = new Set(resources.map((item) => item && item.to).filter(Boolean));
for (const target of ['install-stamp.json', 'backend-manifest.json', 'backend', 'uninstall.sh']) {
  if (!resourceTargets.has(target)) {
    throw new Error(`desktop package extraResources must include ${target}`);
  }
}

console.log('AEGIS desktop wrapper surface verified');
