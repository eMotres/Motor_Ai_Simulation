/** The project TIMELINE (owner 2026-11-03): coarse lanes per iteration (tooling, motor manufacturing, free-run, dyno,
 *  propeller when the iteration has one, shipping), the plan from the admin's start date and weeks, the forecast the server
 *  derives from the production of the motor.  `GET /api/projects/{id}/timeline` (members + admins) returns the customer
 *  view; admins edit through `/api/admin/projects/{id}/timeline`.  Pure helpers, tested without a browser
 *  (timeline.test.mjs). */

export type TimelineLaneId = 'tooling' | 'motor' | 'free_run' | 'dyno' | 'propeller' | 'shipping';
export const TIMELINE_LANES: TimelineLaneId[] = ['tooling', 'motor', 'free_run', 'dyno', 'propeller', 'shipping'];
export type TimelineStatus = 'planned' | 'in_progress' | 'done' | 'late';

/** One lane as the customer sees it (the server's whitelist). */
export interface TimelineLaneView {
  lane: TimelineLaneId; weeks: number | null;
  plan_start: string | null; plan_end: string | null; forecast_start: string | null; forecast_end: string | null;
  status: TimelineStatus; readiness_pct: number | null; note: string;
}
export interface TimelineIterationView { index: number; start_date: string | null; lanes: TimelineLaneView[] }
export interface ProjectTimeline {
  today: string; motor: { en: string; zh: string } | null; baseline_on: string | null; iterations: TimelineIterationView[];
}

/** The stored, admin-edited document. */
export interface TimelineLaneDoc { plan_weeks: number | null; start: string | null; end: string | null; done_date: string | null; note: string }
export interface TimelineIterationDoc { index: number; start_date: string | null; lanes: Partial<Record<TimelineLaneId, TimelineLaneDoc>> }
export interface TimelineDoc {
  version: 1; revision: number; production: { die: string; config: string } | null; iterations: TimelineIterationDoc[];
  baseline: { frozen_at: string; frozen_by: string } | null;
}
export interface AdminTimeline {
  timeline: TimelineDoc; view: ProjectTimeline; today: string; estimate_available: boolean;
  linked_motors: { die: string; config: string; status: string }[];
  production: { die: string; config: string; title: { en: string; zh: string } | null } | null;
}

export const DAY_MS = 86_400_000;
const isDate = (s: unknown): s is string => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s);
export const dayNumber = (iso: string): number => Math.round(Date.parse(`${iso}T00:00:00Z`) / DAY_MS);
export const isoOfDay = (n: number): string => new Date(n * DAY_MS).toISOString().slice(0, 10);

/** True when the iteration has dates (its start date is set); without one the tab shows the durations only. */
export const iterationDated = (it: TimelineIterationView): boolean => it.lanes.some((l) => isDate(l.plan_start) || isDate(l.forecast_start));

/** The day range the Gantt spans: every plan / forecast date and today, padded by a week before and two after. */
export function ganttRange(view: Pick<ProjectTimeline, 'today' | 'iterations'>): { start: number; end: number; days: number } {
  const days = [view.today, ...view.iterations.flatMap((it) => it.lanes.flatMap((l) => [l.plan_start, l.plan_end, l.forecast_start, l.forecast_end]))]
    .filter(isDate).map(dayNumber);
  const lo = Math.min(...days) - 7;
  const hi = Math.max(...days) + 14;
  return { start: lo, end: hi, days: Math.max(28, hi - lo) };
}

/** Left edge and width (px) of the bar [start, end] on a chart starting at day `origin`; null without dates. */
export function barGeometry(start: string | null, end: string | null, origin: number, pxPerDay: number): { x: number; w: number } | null {
  if (!isDate(start) || !isDate(end)) return null;
  const s = dayNumber(start); const e = Math.max(s, dayNumber(end));
  return { x: (s - origin) * pxPerDay, w: Math.max(2, (e - s) * pxPerDay) };
}

/** Month ticks (the first day of every month inside the range) for the axis. */
export function monthTicks(range: { start: number; end: number }): string[] {
  const out: string[] = [];
  const first = new Date(range.start * DAY_MS);
  let y = first.getUTCFullYear(); let m = first.getUTCMonth() + 1;
  for (;;) {
    if (m > 11) { m = 0; y += 1; }
    const iso = `${y}-${String(m + 1).padStart(2, '0')}-01`;
    if (dayNumber(iso) > range.end) break;
    out.push(iso);
    m += 1;
  }
  return out;
}

/** The forecast bar colour: red when late, green when done, blue otherwise. */
export const statusColor = (s: TimelineStatus): string => (s === 'late' ? '#ef4444' : s === 'done' ? '#10b981' : s === 'in_progress' ? '#3b82f6' : '#60a5fa');

export const timelineWeeks = (w: number | null | undefined): string => (w === null || w === undefined ? '—' : String(Number(Number(w).toFixed(1))));

/** The one-line tooltip of a lane: plan and forecast dates, readiness, the public note. */
export function laneTip(l: TimelineLaneView, t: (key: string, args?: Record<string, unknown>) => string): string {
  const parts: string[] = [t(`timeline.status.${l.status}`)];
  if (l.plan_start && l.plan_end) parts.push(t('timeline.planSpan', { start: l.plan_start, end: l.plan_end }));
  if (l.forecast_start && l.forecast_end) parts.push(t('timeline.forecastSpan', { start: l.forecast_start, end: l.forecast_end }));
  if (l.readiness_pct !== null && l.readiness_pct !== undefined) parts.push(t('timeline.readiness', { n: Math.round(l.readiness_pct) }));
  if (l.note) parts.push(l.note);
  return parts.join(' · ');
}

// ── admin editing (pure; the component keeps the document in state and PUTs it) ───────────────────────────────────

export const blankLane = (weeks: number | null = null): TimelineLaneDoc => ({ plan_weeks: weeks, start: null, end: null, done_date: null, note: '' });

/** A copy of `doc` with one field of one lane set ('' clears a date; weeks are parsed, empty = null). */
export function setLaneField(doc: TimelineDoc, index: number, lane: TimelineLaneId, field: keyof TimelineLaneDoc, raw: string): TimelineDoc {
  return {
    ...doc,
    iterations: doc.iterations.map((it) => {
      if (it.index !== index || !it.lanes[lane]) return it;
      const cur = it.lanes[lane] as TimelineLaneDoc;
      let value: string | number | null = raw;
      if (field === 'plan_weeks') { const x = Number(String(raw).replace(',', '.')); value = String(raw).trim() === '' || !Number.isFinite(x) ? null : x; }
      else if (field !== 'note') value = String(raw).trim() || null;
      return { ...it, lanes: { ...it.lanes, [lane]: { ...cur, [field]: value } } };
    }),
  };
}

export function setIterationStart(doc: TimelineDoc, index: number, raw: string): TimelineDoc {
  return { ...doc, iterations: doc.iterations.map((it) => (it.index === index ? { ...it, start_date: raw.trim() || null } : it)) };
}

/** Add (1 week) or remove the propeller lane of an iteration. */
export function setPropeller(doc: TimelineDoc, index: number, on: boolean): TimelineDoc {
  return {
    ...doc,
    iterations: doc.iterations.map((it) => {
      if (it.index !== index) return it;
      const lanes = { ...it.lanes };
      if (on && !lanes.propeller) lanes.propeller = blankLane(1);
      if (!on) delete lanes.propeller;
      return { ...it, lanes };
    }),
  };
}

/** Append an iteration with the default lanes (free-run 1 week), or drop the last one. */
export function addIteration(doc: TimelineDoc): TimelineDoc {
  const lanes: Partial<Record<TimelineLaneId, TimelineLaneDoc>> = { tooling: blankLane(), motor: blankLane(), free_run: blankLane(1), dyno: blankLane(), shipping: blankLane() };
  return { ...doc, iterations: [...doc.iterations, { index: doc.iterations.length + 1, start_date: null, lanes }] };
}
export const removeLastIteration = (doc: TimelineDoc): TimelineDoc => ({ ...doc, iterations: doc.iterations.slice(0, -1) });

/** The PUT body: only what the admin edits (the baseline is never sent). */
export const putBody = (doc: TimelineDoc) => ({ revision: doc.revision, production: doc.production, iterations: doc.iterations });
