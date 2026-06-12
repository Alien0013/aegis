export function compact(value: unknown, max = 90): string {
  if (value === null || value === undefined || value === "") return "-";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return "-";
  return text.length > max ? `${text.slice(0, Math.max(0, max - 3))}...` : text;
}

export function dateish(value: unknown): string {
  if (!value) return "-";
  const text = String(value);
  const parsed = Date.parse(text);
  if (Number.isNaN(parsed)) return compact(text, 32);
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(parsed));
}

export function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function titleOf(row: any, fallback = "Detail"): string {
  return String(row?.title || row?.name || row?.id || row?.trace_id || row?.run_id || fallback);
}
