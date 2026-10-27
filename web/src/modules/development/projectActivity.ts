// Live activity of a project (owner 2026-10-27): which of the project's motor variants is being computed on the server right now.
// One request per project (never per row), polled every 30 s while the page is visible.  Pure helpers, no React.
import { variantKey } from './motorGroups.ts';
import type { ProjectMotor } from './types';

/** One live run on a linked motor of the project, as `GET /api/projects/{id}/activity` answers it (customer-safe: no run ids). */
export interface ActivityRow {
  die: string; config: string; duty: string; kind: string; label: string;
  state: 'running' | 'queued'; started_at: string | null; progress: number | null; eta_min?: number;
  /** queued runs only: 1 = the next to start on the server, and how many queued runs start before this one (a count, never whose) */
  position?: number | null; ahead?: number | null;
  /** when the run is expected to start / finish (rounded: 5 min under an hour away, 15 min beyond); null times = no history yet */
  eta?: ActivityEta | null;
}

export interface ActivityEta { expected_start: string | null; expected_finish: string | null; confidence: 'low' | 'medium' | 'high'; basis: string }

/** How a clock time is written (injected so the tests do not depend on the machine's time zone or language). */
export interface ActivityClock { time: (ms: number) => string; day: (ms: number) => string; dayTime: (ms: number) => string }

export const defaultClock: ActivityClock = {
  time: (ms) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  day: (ms) => new Date(ms).toDateString(),
  dayTime: (ms) => new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
};

/** "~15:05" in the viewer's local time (with the date when it is not today); null when the time is unknown.  Never a bare
 *  time without the tilde: the server rounds to 5 / 15 minutes and this is an estimate. */
export function etaClock(iso: string | null | undefined, nowMs: number, clock: ActivityClock = defaultClock): string | null {
  const ms = Date.parse(String(iso ?? ''));
  if (!Number.isFinite(ms)) return null;
  return `~${clock.day(ms) === clock.day(nowMs) ? clock.time(ms) : clock.dayTime(ms)}`;
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
  /** the one short line: "Computing on the server: <label>[ · <duty>] · <N> min · results ~15:05" or
   *  "Queued: <label> · 3 ahead · starts ~14:20 · results ~15:05" (+N when there are more) */
  text: string;
  /** every run, one line each, for the tooltip */
  lines: string[];
}

const CONFIDENCE_KEY = { low: 'activity.confidence.low', medium: 'activity.confidence.medium', high: 'activity.confidence.high' } as const;

/** "starts ~14:20 · results ~15:05", or "estimate after the first runs" when there is no number to show. */
function etaParts(row: ActivityRow, nowMs: number, t: T, clock: ActivityClock, withStart: boolean): string[] {
  const finish = etaClock(row.eta?.expected_finish, nowMs, clock);
  const start = withStart ? etaClock(row.eta?.expected_start, nowMs, clock) : null;
  const out: string[] = [];
  if (start) out.push(t('activity.starts', { time: start }));
  out.push(finish ? t('activity.results', { time: finish }) : t('activity.noEstimate'));
  return out;
}

const aheadText = (row: ActivityRow, t: T): string => typeof row.ahead === 'number' && row.ahead > 0 ? t('activity.ahead', { count: row.ahead }) : typeof row.ahead === 'number' ? t('activity.nextInLine') : '';

/** The chip of a variant (or of the whole project): null when nothing runs.  A running run wins over a queued one. */
export function activityChip(rows: ActivityRow[], nowMs: number, t: T, clock: ActivityClock = defaultClock): ActivityChipModel | null {
  if (!rows.length) return null;
  const running = rows.filter((r) => r.state === 'running');
  const queuedRows = rows.filter((r) => r.state === 'queued').sort((a, b) => (a.position ?? 1e9) - (b.position ?? 1e9));
  const primary = running[0] ?? queuedRows[0] ?? rows[0];
  const state = running.length ? 'running' : 'queued';
  const what = state === 'running'
    ? [withDuty(primary), minutesText(elapsedMinutes(primary.started_at, nowMs), t)].filter(Boolean).join(' · ')
    : primary.label;
  const head = t(state === 'running' ? 'activity.running' : 'activity.queued', { what });
  const tail = state === 'running' ? etaParts(primary, nowMs, t, clock, false) : [aheadText(primary, t), ...etaParts(primary, nowMs, t, clock, true)];
  const more = rows.length > 1 ? ` ${t('activity.more', { count: rows.length - 1 })}` : '';
  const text = `${[head, ...tail].filter(Boolean).join(' · ')}${more}`;
  const lines = rows.map((r) => {
    const pct = typeof r.progress === 'number' && r.progress > 0 ? `${Math.round(r.progress * 100)} %` : '';
    const queue = r.state === 'queued' ? [typeof r.position === 'number' ? t('activity.position', { count: r.position }) : '', aheadText(r, t)] : [];
    const detail = [withDuty(r), r.state === 'running' ? minutesText(elapsedMinutes(r.started_at, nowMs), t) : '', pct, ...queue, ...etaParts(r, nowMs, t, clock, r.state === 'queued'),
      r.eta?.expected_finish ? t(CONFIDENCE_KEY[r.eta.confidence] ?? CONFIDENCE_KEY.low) : ''].filter(Boolean).join(' · ');
    return t(r.state === 'running' ? 'activity.lineRunning' : 'activity.lineQueued', { what: detail });
  });
  return { state, text, lines };
}

/** What the poller needs from its environment (injected so the logic runs without a browser). */
export interface PollerDeps<D = ActivityRow[]> {
  fetchRows: () => Promise<D>;
  onRows: (data: D) => void;
  isVisible: () => boolean;
  /** subscribe to the page becoming visible / hidden; returns the unsubscribe */
  onVisibility: (listener: () => void) => () => void;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (handle: unknown) => void;
  intervalMs?: number;
}

/** Poll every `intervalMs` while the page is visible, stop while it is hidden, refresh at once when it comes back.
 *  A failed request keeps the last rows (a stale chip is better than a flickering one) and is retried at the next tick. */
export function createActivityPoller<D = ActivityRow[]>(deps: PollerDeps<D>): { start: () => void; stop: () => void } {
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
