export type ProviderSetupError = {
  provider?: string;
  code?: string;
  message?: string;
};

export function providerSetupErrorMessage(error: ProviderSetupError | string | null | undefined): string {
  if (!error) return 'AEGIS provider setup needs attention.';
  if (typeof error === 'string') return error || 'AEGIS provider setup needs attention.';
  const provider = error.provider ? `${error.provider} ` : '';
  const message = error.message || error.code || 'setup failed';
  return `AEGIS ${provider}provider ${message}`.trim();
}
