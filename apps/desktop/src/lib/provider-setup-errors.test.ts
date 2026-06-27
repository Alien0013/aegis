import { providerSetupErrorMessage } from './provider-setup-errors';

if (!providerSetupErrorMessage({ provider: 'openrouter', message: 'missing key' }).includes('openrouter')) {
  throw new Error('providerSetupErrorMessage should include provider context');
}
