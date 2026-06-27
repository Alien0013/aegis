import { createUpdateStore } from '../store/updates';

export type UpdatesOverlayProps = {
  detail?: string;
};

export function updatesOverlayCopy(props: UpdatesOverlayProps = {}): string {
  const store = createUpdateStore('checking');
  if (props.detail) store.setState('available', props.detail);
  return store.message;
}

export function UpdatesOverlay(props: UpdatesOverlayProps): string {
  return `AEGIS updates: ${updatesOverlayCopy(props)}`;
}
