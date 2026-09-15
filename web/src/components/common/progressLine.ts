/**
 * The pure arithmetic behind the solve-progress strip.
 *
 * Split out of the component (2026-09-07, when Mechanical and Thermal asked for
 * the same bar the Simulation tab has) for one reason: it is the only part with
 * rules worth pinning — the clamps that keep "step 0 / 0" or a backend that
 * over-counts from printing nonsense, and the choice of which field drives the
 * bar width.  A React component cannot be exercised by `node --test`; this can
 * (see `__tests__/progressStrip.test.mjs`).
 */

/** What every solve-progress endpoint returns (the transient contract, reused
 *  verbatim by /api/mechanical/progress and /api/thermal/progress). */
export interface ProgressInfo {
  running: boolean;
  step: number;
  total: number;
  elapsed_s: number;
  eta_s: number;
  per_step_s?: number;
  /** 0…1, for solves that know their fraction but not a step count. */
  frac?: number;
  phase: string;
  /** Backend-authored breakdown of `total` ("2×36 settle + 8 pre-roll + 144
   *  reported").  Only the code that built the schedule can state it — see the
   *  fallback comment at the render site. */
  composition?: string;
  /** Which solve is running ("rotor_stress" | "modes" | "field" | …), so a tab
   *  with several Solve buttons can name the one the bar belongs to. */
  kind?: string;
  /** Backend Stage 4: this run is WAITING for a worker slot, not solving.  The
   *  key is present only while that is true — an idle or running bar carries
   *  neither, which is what keeps the payload's key set invariant. */
  queued?: boolean;
  /** 1 = next.  Present with `queued`. */
  position?: number;
}

export interface ProgressLine {
  /** Leading words, already punctuated: "Computing" or "Rotor stress ·". */
  prefix: string;
  step: number;
  total: number;
  unit: string;
  /** Bar width, 0…100. */
  pct: number;
}

/**
 * Turn a raw progress payload into the pieces the strip prints.
 *
 * `kindLabels` names the running solve when the backend says which one it is;
 * an unknown kind falls back to "Computing" rather than printing a raw
 * identifier like `critical_speeds` at the user.
 */
export function formatProgressLine(
  p: Pick<ProgressInfo, 'step' | 'total'> & Partial<ProgressInfo>,
  unit = 'points',
  kindLabels?: Record<string, string>,
): ProgressLine {
  const total = Math.max(1, Number(p.total) || 0);
  const step = Math.min(Math.max(Number(p.step) || 0, 0), total);
  // The step count drives the bar whenever there is one; `frac` is the escape
  // hatch for a solve that reports a fraction and leaves `total` at 0 (a
  // nonlinear contact iteration count is not known before it converges).
  const byStep = (100 * step) / total;
  const pct = (!Number(p.total) && typeof p.frac === 'number')
    ? Math.min(100, Math.max(0, p.frac * 100))
    : Math.min(100, byStep);
  const named = p.kind ? kindLabels?.[p.kind] : undefined;
  return { prefix: named ? `${named} ·` : 'Computing', step, total, unit, pct };
}


/**
 * The one line a QUEUED run prints instead of a bar.
 *
 * With several accounts on one server a solve can be admitted a minute after it
 * was asked for (`motor_ai_sim.jobs`: N workers, one running job per user,
 * priority by interactivity).  A spinner over that reads as a hung server —
 * exactly the failure the progress strip was built to end — so the wait is
 * NAMED and its place in the queue is printed.  `null` when the run is not
 * queued, so the caller renders its normal bar.
 */
export function formatQueueLine(
  p: Pick<ProgressInfo, 'queued' | 'position'> & Partial<ProgressInfo>,
): string | null {
  if (!p.queued) return null;
  const n = Number(p.position) || 0;
  return n > 0 ? `Queued · position ${n}` : 'Queued';
}
