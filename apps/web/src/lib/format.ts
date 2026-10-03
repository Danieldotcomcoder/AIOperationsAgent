/** Display formatting. Every function here is total: bad input renders as an em dash. */

const DASH = '—';

export function dateTime(value: string | null | undefined): string {
  if (!value) return DASH;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? DASH
    : parsed.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

/** A calendar date from the API (YYYY-MM-DD) shown without a timezone shift. */
export function day(value: string | null | undefined): string {
  if (!value) return DASH;
  const [y, m, d] = value.slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return DASH;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, {
    dateStyle: 'medium',
    timeZone: 'UTC',
  });
}

export function duration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return DASH;
  if (ms < 1_000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1_000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  return `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1_000)} s`;
}

/** Cost estimates are tiny; show enough digits that a real cost never reads as zero. */
export function usd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  if (value === 0) return '$0';
  if (value < 0.01) return `$${value.toFixed(5)}`;
  return `$${value.toFixed(value < 1 ? 3 : 2)}`;
}

export function count(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return new Intl.NumberFormat(undefined, { notation: value >= 10_000 ? 'compact' : 'standard' }).format(value);
}

export function percent(ratio: number | null | undefined): string {
  if (ratio == null || !Number.isFinite(ratio)) return DASH;
  return `${(ratio * 100).toFixed(ratio === 1 || ratio === 0 ? 0 : 1)}%`;
}

export function money(value: string | number): string {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n)
    ? new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
    : DASH;
}

/** snake_case enum value to words: "needs_review" -> "needs review". */
export function words(value: string | null | undefined): string {
  return value ? value.replaceAll('_', ' ') : DASH;
}
