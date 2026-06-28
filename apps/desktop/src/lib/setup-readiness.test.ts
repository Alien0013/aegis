import { desktopSetupReadinessCopy, desktopSetupChecklist } from './setup-readiness';

const missing = desktopSetupReadinessCopy({ provider_configured: false, provider: 'openrouter', next_command: 'aegis setup' });
if (!missing.includes('AEGIS desktop setup') || !missing.includes('aegis setup')) {
  throw new Error('desktopSetupReadinessCopy should guide missing provider setup');
}

const ready = desktopSetupReadinessCopy({ provider_configured: true, provider: 'openai', model: 'gpt-5.5', next_command: 'aegis' });
if (!ready.includes('openai') || !ready.includes('gpt-5.5')) {
  throw new Error('desktopSetupReadinessCopy should include ready provider/model context');
}

const checklist = desktopSetupChecklist({
  checks: [
    { id: 'provider', label: 'Provider auth', ok: true, detail: 'openai' },
    { id: 'gateway', label: 'Gateway channels', ok: false, command: 'aegis setup gateway' },
  ],
});
if (checklist.length !== 2 || !checklist[1].includes('aegis setup gateway')) {
  throw new Error('desktopSetupChecklist should preserve dashboard readiness checks');
}
