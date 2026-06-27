export type BootstrapState = {
  product: 'AEGIS';
  ready: boolean;
  nextCommand: string;
};

export function createBootstrapState(ready = false): BootstrapState {
  return {
    product: 'AEGIS',
    ready,
    nextCommand: ready ? 'aegis' : 'aegis setup',
  };
}
