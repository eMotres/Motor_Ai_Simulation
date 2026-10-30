/**
 * The pure rules behind the one-row stage strip (owner 2026-10-30).
 *
 * The backend (`motor_ai_sim/progress_stages.py`) sends an ordered `stages` list
 * beside the old flat `step / total` fields.  This module is the part with rules
 * worth pinning — what a bar's width is for each state, when a count is shown as
 * still growing, when a stage has no count at all — kept free of React and of
 * `import.meta.env` so `node --test` can import it directly
 * (see `__tests__/progressStages.test.mjs`).  Type-only imports and no enums, so
 * Node's type stripping reads it as is.
 */

export type StageState = 'pending' | 'running' | 'done' | 'skipped' | 'stopped';

export interface ProgressStage {
  /** Stable identifier the UI translates ("prepare", "final"…). */
  key: string;
  /** Short English fallback label from the backend. */
  name: string;
  state: StageState;
  /** Steps finished in THIS stage. */
  done: number;
  /** Steps expected.  For an `adaptive` stage a moving target: 0 = not known
   *  yet, and it may GROW while the stage runs. */
  planned: number;
  adaptive: boolean;
  /** "frames" (FEM frames) or "steps" (coupled runs). */
  unit?: string;
  elapsed_s: number;
  /** The solver's own breakdown ("2×36 settle + 8 pre-roll + 144 reported"). */
  note?: string;
}

/** Which stages the row shows.  A payload without any (thermal, mechanical, an
 *  idle backend, an older server) keeps the strip's single bar. */
export function hasStages(stages: ProgressStage[] | undefined | null): stages is ProgressStage[] {
  return Array.isArray(stages) && stages.length > 0;
}

/** A running stage that cannot say how much is left: no planned count. */
export function isIndeterminate(s: Pick<ProgressStage, 'state' | 'planned'>): boolean {
  return s.state === 'running' && !(Number(s.planned) > 0);
}

/**
 * Bar fill, 0…1.  A finished stage is full whatever its counts say (an adaptive
 * warm-up that settled early reads n / n); a skipped, stopped-before-start or
 * pending one is empty; a running one is done / planned, clamped.
 */
export function stageFraction(s: Pick<ProgressStage, 'state' | 'done' | 'planned'>): number {
  if (s.state === 'done') return 1;
  if (s.state === 'pending' || s.state === 'skipped') return 0;
  const planned = Number(s.planned) || 0;
  if (planned <= 0) return 0;
  return Math.min(1, Math.max(0, (Number(s.done) || 0) / planned));
}

export type CountKind =
  /** "19 / 20" */ | 'exact'
  /** "19 / 20+" — adaptive and still running: the total may grow */ | 'growing'
  /** "19" — running, nothing planned yet */ | 'open'
  /** "—" — pending, skipped: nothing to count */ | 'none';

export interface StageCount {
  kind: CountKind;
  done: number;
  planned: number;
}

/**
 * What the compact "n / N frames" label says.  The point of the `growing` kind:
 * the adaptive warm-up's total keeps rising until the start-up transient
 * settles, and printing it as a plain "19 / 20" made the old single bar look
 * like it was going backwards.  A finished adaptive stage is `exact` — its
 * planned count has snapped to what it took.
 */
export function stageCount(s: ProgressStage): StageCount {
  const done = Math.max(0, Math.round(Number(s.done) || 0));
  const planned = Math.max(0, Math.round(Number(s.planned) || 0));
  if (s.state === 'pending' || s.state === 'skipped') {
    return { kind: 'none', done: 0, planned };
  }
  // Nothing planned and nothing counted (a second whole solve, a stage that has
  // only just begun): there is no number to print, the strip animates it.
  if (planned <= 0) return { kind: done > 0 ? 'open' : 'none', done, planned };
  if (s.adaptive && s.state === 'running') return { kind: 'growing', done, planned: Math.max(planned, done) };
  return { kind: 'exact', done, planned: Math.max(planned, done) };
}

/** `unit` → the ICU `select` branch the locale files use ("steps" or frames). */
export function stageUnit(s: Pick<ProgressStage, 'unit'>): 'steps' | 'frames' {
  return s.unit === 'steps' ? 'steps' : 'frames';
}

/** Keys with their own tooltip text in the locale files; anything else the
 *  backend might add later falls back to its English `name`. */
export const KNOWN_STAGE_KEYS = [
  'prepare', 'calibration', 'gap_probe', 'warm_up', 'demag_prepass', 'final', 'extra', 'post',
  'loop', 'pwm_final',
] as const;

export function isKnownStage(key: string): boolean {
  return (KNOWN_STAGE_KEYS as readonly string[]).includes(key);
}

/** Seconds as the strip prints them: "4.2 s", "83 s", "2 m 05 s". */
export function formatStageSeconds(sec: number): string {
  const v = Math.max(0, Number(sec) || 0);
  if (v < 10) return `${v.toFixed(1)} s`;
  if (v < 100) return `${Math.round(v)} s`;
  const m = Math.floor(v / 60);
  const r = Math.round(v - m * 60);
  return `${m} m ${String(r).padStart(2, '0')} s`;
}

/**
 * Is the ETA only a LOWER bound?  While an adaptive stage runs its planned
 * count can still grow (the eddy warm-up splices a longer march in front when
 * the start-up transient has not settled), so "N s left" can only get larger —
 * the strip prints it as "≥ N s" instead of a number that then jumps.
 */
export function etaIsLowerBound(stages: ProgressStage[] | undefined | null): boolean {
  return hasStages(stages) && stages.some(s => s.state === 'running' && s.adaptive);
}
