import { formatUpdateCopy, type UpdateCopyState } from '../lib/update-copy';

export type DesktopUpdateStore = {
  state: UpdateCopyState;
  detail: string;
  message: string;
  setState: (state: UpdateCopyState, detail?: string) => DesktopUpdateStore;
};

export function createUpdateStore(initial: UpdateCopyState = 'idle'): DesktopUpdateStore {
  const store: DesktopUpdateStore = {
    state: initial,
    detail: '',
    message: formatUpdateCopy(initial),
    setState(state, detail = '') {
      store.state = state;
      store.detail = detail;
      store.message = formatUpdateCopy(state, detail);
      return store;
    },
  };
  return store;
}
