/** Display helpers shared by the M2 pages. */

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

/** GPU-hours arrive as decimal strings; render with a stable 4-dp scale. */
export function formatHours(value: string | number): string {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(4) : String(value);
}
