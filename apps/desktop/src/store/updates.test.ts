import { createUpdateStore } from './updates';

const store = createUpdateStore();
store.setState('ready', 'Restart AEGIS to apply it.');
if (!store.message.includes('AEGIS')) {
  throw new Error('createUpdateStore should keep AEGIS update copy');
}
