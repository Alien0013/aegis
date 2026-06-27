export type BootstrapState = {
  product: 'AEGIS';
  providerConfigured: boolean;
  nextCommand: string;
};

export function createBootstrapState(providerConfigured = false): BootstrapState {
  return {
    product: 'AEGIS',
    providerConfigured,
    nextCommand: providerConfigured ? 'aegis' : 'aegis setup',
  };
}
