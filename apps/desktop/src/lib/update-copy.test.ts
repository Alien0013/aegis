import { formatUpdateCopy } from './update-copy';

if (!formatUpdateCopy('ready').includes('AEGIS')) {
  throw new Error('formatUpdateCopy should use AEGIS product copy');
}
