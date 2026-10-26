// Simulation stages of a project variant (owner 2026-10-27): ONE segmented bar per variant - five equal segments
// (EM points, Thermal, Mechanics, 3-D end effect, Report), each filled to its own completion - plus the project-level line.
// Pure functions, no React: the segment fill / percent, the tooltips, the summary text and the "seen" memory.
import { etaClock, defaultClock, type ActivityClock, type ActivityEta, type ActivityRow } from './projectActivity.ts';

export type StageId = 'em' | 'thermal' | 'mechanics' | 'end3d' | 'report';
export type StageState = 'not_started' | 'queued' | 'computing' | 'partial' | 'done' | 'failed';

export interface StageDetail { duty: string; state: StageState; progress: number | null }
export interface StageInfo {
  id: StageId; state: StageState; done: number; total: number;
  /** 0..1: finished units + the progress of the running one(s) */
  fraction: number;
  position: number | null; ahead: number | null; eta: ActivityEta | null; finished_at: string | null;
  detail: StageDetail[]; note?: string;
}
export interface VariantStages {
  die: string; config: string; stages: StageInfo[]; percent: number; stage_index: number; stage_count: number; complete: boolean;
  runs_running: number; runs_queued: number; eta: ActivityEta | null; last_finished_at: string | null;
}
export interface ActivitySummary {
  state: 'idle' | 'queued' | 'computing' | 'results_ready';
  runs_running: number; runs_queued: number; expected_all_done: string | null; confidence: 'low' | 'medium' | 'high' | null;
  basis: string | null; last_finished_at: string | null; server?: { running: number; queued: number };
}
export interface ActivityData { rows: ActivityRow[]; summary: ActivitySummary | null; stages: VariantStages[] }

export const EMPTY_ACTIVITY: ActivityData = { rows: [], summary: null, stages: [] };

type T = (key: string, options?: Record<string, unknown>) => string;

export const STAGE_ORDER: StageId[] = ['em', 'thermal', 'mechanics', 'end3d', 'report'];

/** Anything the server sends is read defensively: an older server (no summary / stages) just shows the chips. */
export function parseActivity(data: unknown): ActivityData {
  const d = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  const rows = Array.isArray(d.activity) ? (d.activity as ActivityRow[]) : [];
  const summary = d.summary && typeof d.summary === 'object' ? (d.summary as ActivitySummary) : null;
  const stages = Array.isArray(d.stages) ? (d.stages as VariantStages[]).filter((v) => v && Array.isArray(v.stages)) : [];
  return { rows, summary, stages };
}

const clamp01 = (n: number): number => (Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0);

export interface SegmentFill {
  /** 0..100: how much of the segment is filled with the brand colour */
  pct: number;
  /** queued: the unfilled rest carries a subtle hatch */
  hatch: boolean;
  /** failed: a thin red outline only */
  outline: boolean;
  /** computing: the fill edge pulses (off for reduced motion in the component) */
  active: boolean;
}

/** How one segment is drawn.  done = full; computing / partial / queued = filled to the finished share; not started = empty. */
export function segmentFill(stage: Pick<StageInfo, 'state' | 'fraction'>): SegmentFill {
  const state = stage.state;
  const pct = state === 'done' ? 100 : state === 'not_started' ? 0 : Math.round(clamp01(stage.fraction) * 100);
  return { pct, hatch: state === 'queued', outline: state === 'failed', active: state === 'computing' };
}

/** The variant's overall percent, 0..100 (the server's number when it sent one, else the mean of the segments). */
export function variantPercent(v: Pick<VariantStages, 'stages' | 'percent'>): number {
  if (typeof v.percent === 'number' && Number.isFinite(v.percent)) return Math.min(100, Math.max(0, Math.round(v.percent)));
  if (!v.stages.length) return 0;
  return Math.round((100 * v.stages.reduce((s, x) => s + segmentFill(x).pct / 100, 0)) / v.stages.length);
}

/** The project's overall percent: the mean over its variants. */
export function projectPercent(list: VariantStages[]): number {
  return list.length ? Math.round(list.reduce((s, v) => s + variantPercent(v), 0) / list.length) : 0;
}

/** The stages of one variant (die + configuration, case-insensitive; the canonical die decides, the alias also matches). */
export function stagesForMotor(list: VariantStages[], motor: { die: string; canonical_die?: string; config: string }): VariantStages | null {
  const norm = (a: string, b: string) => `${a}\u0000${b}`.toLowerCase();
  const wanted = new Set([norm(motor.canonical_die ?? motor.die, motor.config), norm(motor.die, motor.config)]);
  return list.find((v) => wanted.has(norm(v.die, v.config))) ?? null;
}

const stageName = (id: StageId, t: T): string => t(`stages.names.${id}`);

/** Tooltip lines of one segment: name and state, counts, queue place, estimate (with its confidence), date when done. */
export function stageTooltip(stage: StageInfo, nowMs: number, t: T, clock: ActivityClock = defaultClock): string[] {
  const lines: string[] = [];
  const pct = Math.round(clamp01(stage.fraction) * 100);
  const stateText = stage.state === 'computing' ? `${t('stages.state.computing')} · ${pct} %` : t(`stages.state.${stage.state}`);
  lines.push(`${stageName(stage.id, t)}: ${stateText}`);
  if (stage.id !== 'end3d' && stage.id !== 'report' && stage.total > 0) lines.push(t('stages.points', { done: stage.done, total: stage.total }));
  if (stage.state === 'queued') {
    const q = [typeof stage.position === 'number' ? t('activity.position', { count: stage.position }) : '',
      typeof stage.ahead === 'number' ? (stage.ahead > 0 ? t('activity.ahead', { count: stage.ahead }) : t('activity.nextInLine')) : ''].filter(Boolean);
    if (q.length) lines.push(q.join(' · '));
  }
  if (stage.state === 'queued' || stage.state === 'computing') {
    const start = stage.state === 'queued' ? etaClock(stage.eta?.expected_start, nowMs, clock) : null;
    const finish = etaClock(stage.eta?.expected_finish, nowMs, clock);
    if (start) lines.push(t('activity.starts', { time: start }));
    lines.push(finish ? `${t('activity.results', { time: finish })} · ${t(`activity.confidence.${stage.eta?.confidence ?? 'low'}`)}` : t('activity.noEstimate'));
  }
  if (stage.id === 'report') {
    if (stage.state === 'done') lines.push(t('stages.reviewed'));
    else if (stage.note === 'awaiting_review') lines.push(t('stages.awaitingReview'));
  }
  if (stage.state === 'done' && stage.finished_at) {
    const ms = Date.parse(stage.finished_at);
    if (Number.isFinite(ms)) lines.push(t('stages.doneOn', { date: clock.dayTime(ms) }));
  }
  if (stage.detail.length > 1) {
    for (const d of stage.detail) {
      const p = d.state === 'computing' && typeof d.progress === 'number' ? ` ${Math.round(clamp01(d.progress) * 100)} %` : '';
      lines.push(`${d.duty}: ${t(`stages.state.${d.state}`)}${p}`);
    }
  }
  return lines;
}

/** Screen-reader label of a segment. */
export function stageAria(stage: StageInfo, t: T): string {
  const pct = Math.round(segmentFill(stage).pct);
  return `${stageName(stage.id, t)}: ${t(`stages.state.${stage.state}`)}, ${pct} %`;
}

/** The right-hand end of a variant's bar: "42 % · results ~15:05", "42 %" when nothing runs, "Complete" when all five are done. */
export function variantEndText(v: VariantStages, nowMs: number, t: T, clock: ActivityClock = defaultClock): string {
  if (v.complete) return t('stages.complete');
  const pct = `${variantPercent(v)} %`;
  if (!v.runs_running && !v.runs_queued) return pct;
  const finish = etaClock(v.eta?.expected_finish, nowMs, clock);
  return [pct, finish ? t('activity.results', { time: finish }) : t('activity.noEstimate')].join(' · ');
}

const runsText = (n: number, t: T): string => t(n === 1 ? 'statusLine.runOne' : 'statusLine.runMany', { count: n });

/** The project line: "Simulation: computing 2 runs · stage 2 of 5 · results expected ~15:05" / "Results ready" / null when idle.
 *  `variants` are the variants that have live runs (used for the stage number when exactly one is active). */
export function summaryLine(summary: ActivitySummary | null, rows: ActivityRow[], list: VariantStages[], nowMs: number, t: T,
                            clock: ActivityClock = defaultClock): string | null {
  if (!summary) return null;
  if (summary.state === 'results_ready') return t('statusLine.ready');
  if (summary.state !== 'computing' && summary.state !== 'queued') return null;
  const n = summary.runs_running + summary.runs_queued;
  const parts = [t(summary.state === 'computing' ? 'statusLine.computing' : 'statusLine.queued', { runs: runsText(n, t) })];
  const active = list.filter((v) => v.runs_running + v.runs_queued > 0);
  if (active.length === 1) parts.push(t('stages.stageOf', { index: active[0].stage_index, count: active[0].stage_count }));
  if (summary.state === 'queued') {
    const queued = rows.filter((r) => r.state === 'queued');
    const best = queued.reduce<ActivityRow | null>((a, r) => (a === null || (r.position ?? 1e9) < (a.position ?? 1e9) ? r : a), null);
    if (best && typeof best.ahead === 'number') parts.push(best.ahead > 0 ? t('activity.ahead', { count: best.ahead }) : t('activity.nextInLine'));
    const starts = queued.map((r) => Date.parse(String(r.eta?.expected_start ?? ''))).filter(Number.isFinite);
    if (starts.length) parts.push(t('activity.starts', { time: etaClock(new Date(Math.min(...starts)).toISOString(), nowMs, clock) }));
  }
  const finish = etaClock(summary.expected_all_done, nowMs, clock);
  parts.push(finish ? t('statusLine.expected', { time: finish }) : t('activity.noEstimate'));
  return parts.join(' · ');
}

/** Tooltip lines of the project line: the server load (counts only) and the confidence of the estimate. */
export function summaryTooltip(summary: ActivitySummary | null, t: T): string[] {
  if (!summary) return [];
  const out: string[] = [];
  if (summary.expected_all_done && summary.confidence) out.push(t(`activity.confidence.${summary.confidence}`));
  if (summary.server) out.push(t('statusLine.server', { running: summary.server.running, queued: summary.server.queued }));
  return out;
}

// ── "results ready" memory: the viewer's last visit to the project, per browser ─────────────────

const seenKey = (projectId: string): string => `activity.seen.${projectId}`;

export interface SeenStore { getItem(key: string): string | null; setItem(key: string, value: string): void }

/** The ISO time the viewer last left this project page, or null (never: the server then uses the last 24 h). */
export function readSeen(store: SeenStore | null | undefined, projectId: string): string | null {
  try {
    const v = store?.getItem(seenKey(projectId));
    return v && Number.isFinite(Date.parse(v)) ? v : null;
  } catch { return null; }
}

export function writeSeen(store: SeenStore | null | undefined, projectId: string, nowMs: number): void {
  try { store?.setItem(seenKey(projectId), new Date(nowMs).toISOString()); } catch { /* private window: no memory */ }
}
