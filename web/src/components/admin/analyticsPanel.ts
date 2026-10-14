// Pure helpers for Admin > Analytics (no runtime imports: web/src/components/admin/__tests__/
// analyticsPanel.test.mjs imports this file).

export type AnalyticsRange = '24h' | '7d' | '30d';
export const ANALYTICS_RANGES: readonly AnalyticsRange[] = ['24h', '7d', '30d'];

export interface AnalyticsTotals {
  visitors: number; visits: number; pageviews: number; bounce_rate: number; avg_visit_s: number;
}
export interface AnalyticsPoint { t: string; pageviews: number; visitors: number }
export interface AnalyticsRow { name: string; count: number }
export interface AnalyticsPayload {
  range: AnalyticsRange; unit: 'hour' | 'day'; totals: AnalyticsTotals; series: AnalyticsPoint[];
  pages: AnalyticsRow[]; countries: AnalyticsRow[]; referrers: AnalyticsRow[];
  browsers: AnalyticsRow[]; os: AnalyticsRow[]; devices: AnalyticsRow[];
}

/** "42 s", "3 min 05 s", "1 h 02 min"; 0 -> "0 s". */
export function formatDuration(seconds: unknown): string {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')} min`;
}

export function formatPercent(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? `${n.toFixed(1)} %` : '–';
}

/** Axis label for one bucket: hours show "HH:00", days "MM-DD". Umami sends "YYYY-MM-DD HH:MM:SS". */
export function bucketLabel(t: string, unit: 'hour' | 'day'): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(t));
  if (!m) return String(t);
  return unit === 'hour' ? `${m[4]}:00` : `${m[2]}-${m[3]}`;
}

export function chartData(p: Pick<AnalyticsPayload, 'series' | 'unit'>) {
  return (p.series || []).map((r) => ({
    label: bucketLabel(r.t, p.unit), pageviews: r.pageviews, visitors: r.visitors,
  }));
}

/** Each row's share of the list total, in percent (one decimal); an all-zero list -> zeros. */
export function withShare(rows: AnalyticsRow[]): (AnalyticsRow & { share: number })[] {
  const total = rows.reduce((a, r) => a + (r.count || 0), 0);
  return rows.map((r) => ({ ...r, share: total > 0 ? Math.round((1000 * r.count) / total) / 10 : 0 }));
}

/** Country code -> English name when Intl knows it; the raw value otherwise. */
export function countryName(code: string, locale = 'en'): string {
  try {
    const n = new Intl.DisplayNames([locale], { type: 'region' }).of(String(code).toUpperCase());
    return n && n !== String(code).toUpperCase() ? n : String(code);
  } catch { return String(code); }
}

/** What the panel says for a failed request: not configured / unreachable (503) vs. anything else. */
export function analyticsErrorKind(status: number): 'unavailable' | 'forbidden' | 'error' {
  if (status === 503) return 'unavailable';
  if (status === 401 || status === 403) return 'forbidden';
  return 'error';
}
