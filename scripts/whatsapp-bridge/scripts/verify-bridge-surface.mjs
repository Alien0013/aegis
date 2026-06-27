import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '../../..');

function read(rel) {
  return readFileSync(resolve(root, rel), 'utf8');
}

function requireText(rel, needle) {
  const source = read(rel);
  if (!source.includes(needle)) {
    throw new Error(`${rel} is missing expected WhatsApp bridge contract: ${needle}`);
  }
}

requireText('aegis/gateway/channels.py', 'if name == "whatsapp"');
requireText('aegis/gateway/channels.py', 'env_prefix="WHATSAPP_CHANNEL"');
requireText('aegis/gateway/channels.py', 'default_port=18792');
requireText('aegis/dashboard_fastapi.py', '"id": "whatsapp"');
requireText('aegis/dashboard_fastapi.py', '"whatsapp_bridge_aliases"');
requireText('aegis/dashboard_fastapi.py', '"WHATSAPP_CHANNEL_OUTBOUND_URL"');
requireText('aegis/doctor.py', 'def probe_whatsapp()');
requireText('aegis/doctor.py', 'WHATSAPP_CHANNEL');

console.log('AEGIS WhatsApp bridge surface verified');
