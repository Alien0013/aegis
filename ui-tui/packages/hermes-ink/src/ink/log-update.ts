export type LogUpdate = {
  level?: 'info' | 'warn' | 'error';
  message: string;
};

export function formatLogUpdate(update: LogUpdate): string {
  const level = (update.level || 'info').toUpperCase();
  return `[AEGIS ${level}] ${update.message}`;
}
