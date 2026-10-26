// Live activity of a project (owner 2026-10-27): which of the project's motor variants is being computed on the server right now.
// One request per project (never per row), polled every 30 s while the page is visible.  Pure helpers, no React.
import { variantKey } from './motorGroups.ts';
import type { ProjectMotor } from './types';

/** One live run on a linked motor of the project, as `GET /api/projects/{id}/activity` answers it (customer-safe: no run ids). */
export interface ActivityRow {
  die: string; config: string; duty: string; kind: string; label: string;
  state: 'running' | 'queued'; started_at: string | null; progress: number | null; eta_min?: number;
}

export const ACTIVITY_POLL_MS = 30_000;

type T = (key: string, options?: Record<string, unknown>) => string;

/** The rows that belong to one variant (die + configuration, case-insensitive; the canonical die decides, the alias also matches). */
export function activityForMotor(rows: ActivityRow[], motor: Pick<ProjectMotor, 'die' | 'canonical_die' | 'config'>): ActivityRow[] {
  const wanted = new Set([variantKey(motor), variantKey({ die: motor.die, config: motor.config })]);
  return rows.filter((r) => wanted.has(variantKey({ die: r.die, config: r.config })));
}

/** Whole minutes since `startedAt`; null when the start is unknown.  Never negative (clock skew between the server and the page). */
export function elapsedMinutes(startedAt: string | null | undefined, nowMs: number): number | null {
  const t = Date.parse(String(startedAt ?? ''));
  return Number.isFinite(t) ? Math.max(0, Math.floor((nowMs - t) / 60_000)) : null;
}

const minutesText = (minutes: number | null, t: T): string => minutes === null ? '' : minutes < 1 ? t('activity.underMinute') : t('activity.minutes', { count: minutes });
const withDuty = (row: ActivityRow): string => [row.label, row.duty].filter(Boolean).join(' · ');

export interface ActivityChipModel {
  state: 'running' | 'queued';
  /** the one short line: "Computing on the server: <label>[ · <duty>] · <N> min" or "Queued: <label>" (+N when there are more) */
  text: string;
  /** every run, one line each, for the tooltip */
  lines: string[];
}

/** The chip of a variant (or of the whole project): null when nothing runs.  A running run wins over a queued one. */
export function activityChip(rows: ActivityRow[], nowMs: number, t: T): ActivityChipModel | null {
  if (!rows.length) return null;
  const running = rows.filter((r) => r.state === 'running');
  const primary = running[0] ?? rows[0];
  const state = running.length ? 'running' : 'queued';
  const what = state === 'running'
    ? [withDuty(primary), minutesText(elapsedMinutes(primary.started_at, nowMs), t)].filter(Boolean).join(' · ')
    : primary.label;
  const more = rows.length > 1 ? ` ${t('activity.more', { count: rows.length - 1 })}` : '';
  const text = `${t(state === 'running' ? 'activity.running' : 'activity.queued', { what })}${more}`;
  const lines = rows.map((r) => {
    const pct = typeof r.progress === 'number' && r.progress > 0 ? `${Math.round(r.progress * 100)} %` : '';
    const detail = [withDuty(r), r.state === 'running' ? minutesText(elapsedMinutes(r.started_at, nowMs), t) : '', pct].filter(Boolean).join(' · ');
    return t(r.state === 'running' ? 'activity.lineRunning' : 'activity.lineQueued', { what: detail });
  });
  return { state, text, lines };
}

/** What the poller needs from its environment (injected so the logic runs without a browser). */
export interface PollerDeps {
  fetchRows: () => Promise<ActivityRow[]>;
  onRows: (rows: ActivityRow[]) => void;
  isVisible: () => boolean;
  /** subscribe to the page becoming visible / hidden; returns the unsubscribe */
  onVisibility: (listener: () => void) => () => void;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (handle: unknown) => void;
  intervalMs?: number;
}

/** Poll every `intervalMs` while the page is visible, stop while it is hidden, refresh at once when it comes back.
 *  A failed request keeps the last rows (a stale chip is better than a flickering one) and is retried at the next tick. */
export function createActivityPoller(deps: PollerDeps): { start: () => void; stop: () => void } {
  const interval = deps.intervalMs ?? ACTIVITY_POLL_MS;
  let timer: unknown = null;
  let stopped = true;
  let inFlight = false;
  let unsubscribe: (() => void) | null = null;
  const clear = () => { if (timer !== null) { deps.clearTimer(timer); timer = null; } };
  const schedule = () => { clear(); if (!stopped && deps.isVisible()) timer = deps.setTimer(() => { timer = null; void tick(); }, interval); };
  const tick = async () => {
    if (stopped || !deps.isVisible() || inFlight) return;
    inFlight = true;
    try {
      const rows = await deps.fetchRows();
      if (!stopped) deps.onRows(rows);
    } catch { /* keep the last rows */ } finally { inFlight = false; }
    schedule();
  };
  return {
    start() {
      if (!stopped) return;
      stopped = false;
      unsubscribe = deps.onVisibility(() => { if (deps.isVisible()) { clear(); void tick(); } else clear(); });
      if (deps.isVisible()) void tick();
    },
    stop() {
      stopped = true;
      clear();
      unsubscribe?.();
      unsubscribe = null;
    },
  };
}
