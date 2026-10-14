// Pure helpers for Admin > Activity (working time per account); no runtime imports so
// web/src/components/admin/__tests__/activityPanel.test.mjs can import this file.

export interface ActivityTab { tab: string; seconds: number }
export interface ActivityRow {
  user: string; today_s: number; d7_s: number; d30_s: number; sessions_7d: number; sessions_30d: number;
  last_seen: number; top_tabs: ActivityTab[]; runs_7d: number; runs_30d: number;
}
export interface ActivityDay {
  day: string; active_s: number; sessions: number; first_ts: number | null; last_ts: number | null;
  tabs: Record<string, number>;
}

export type ActivitySortKey =
  'user' | 'today_s' | 'd7_s' | 'd30_s' | 'sessions_7d' | 'last_seen' | 'runs_30d';

/** "0 min", "42 min", "3 h 05 min"; under half a minute of real activity still reads "< 1 min". */
export function formatActive(seconds: unknown): string {
  const s = Math.max(0, Number(seconds) || 0);
  if (s === 0) return '0 min';
  if (s < 30) return '< 1 min';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}

/** Top tabs as "compare 2 h 10 min, motors 40 min". */
export function formatTopTabs(tabs: ActivityTab[], label: (tab: string) => string = (x) => x): string {
  return (tabs || []).map((t) => `${label(t.tab)} ${formatActive(t.seconds)}`).join(', ');
}

export function sortRows(rows: ActivityRow[], key: ActivitySortKey, dir: 'asc' | 'desc'): ActivityRow[] {
  const sign = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const c = key === 'user' ? a.user.localeCompare(b.user) : (a[key] as number) - (b[key] as number);
    return c === 0 ? a.user.localeCompare(b.user) : sign * c;
  });
}

/** Chart rows: day label MM-DD and whole minutes. */
export function dayChartData(days: ActivityDay[]) {
  return (days || []).map((d) => ({ label: d.day.slice(5), minutes: Math.round((d.active_s || 0) / 60) }));
}

export function totalSeconds(days: ActivityDay[]): number {
  return (days || []).reduce((a, d) => a + (d.active_s || 0), 0);
}
