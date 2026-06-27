import { readFileSync } from 'node:fs';

const manifest = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));
if (manifest.name !== '@aegis/photon-sidecar') {
  throw new Error('unexpected Photon sidecar package name');
}
if (manifest.aegis?.native_bridge !== 'relay') {
  throw new Error('Photon sidecar surface must map to the native AEGIS relay bridge until a dedicated adapter ships');
}
if (!manifest.scripts?.start || !manifest.scripts?.typecheck) {
  throw new Error('Photon sidecar package must expose start and typecheck scripts');
}
console.log('AEGIS Photon sidecar package surface verified');
