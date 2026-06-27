import { formatLogUpdate } from './log-update';

if (!formatLogUpdate({ message: 'ready' }).includes('AEGIS')) {
  throw new Error('formatLogUpdate should include the AEGIS product label');
}
