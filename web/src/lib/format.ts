// Small formatting helpers shared across pages.

const numFmt = new Intl.NumberFormat();

export function num(value: unknown): string {
  const n = Number(value || 0);
  return Number.isFinite(n) ? numFmt.format(n) : "-";
}

export function tokens(value: unknown): string {
  const n = Number(value || 0);
  return n > 0 ? numFmt.format(n) : "-";
}

export function usd(value: unknown): string {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

export function bytes(value: unknown): string {
  let n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function ago(value: unknown): string {
  if (!value) return "";
  const t = typeof value === "number" ? value * (value < 1e12 ? 1000 : 1) : Date.parse(String(value));
  if (Number.isNaN(t)) return String(value);
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 604800) return `${Math.floor(s / 86400)}d ago`;
  return new Date(t).toLocaleDateString();
}

export function dateish(value: unknown): string {
  if (!value) return "";
  const t = typeof value === "number" ? value * (value < 1e12 ? 1000 : 1) : Date.parse(String(value));
  if (Number.isNaN(t)) return String(value);
  return new Date(t).toLocaleString();
}

export function compact(value: unknown, max = 60): string {
  const s = String(value ?? "");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
