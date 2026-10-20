// The RATED speed never overheats (owner 2026-10-19: "loading a preset must ALWAYS give the rated
// point with no overheat at all").
//
// The red "overheats" line is judged by Configure's own lumped estimate (lib/thermalEstimate.ts)
// with the losses of the drive in use (Sine, or the PWM variant's extra motor loss); the server's
// propeller S1 limit uses the calibrated passport model.  The two can disagree, so the rated speed
// is bisected on THE SAME predicate that draws the red line (`bad`), below the S1 limit, 1 % inside
// and on a round 10 rpm.  One function for every drive mode: the caller builds `bad` from the very
// numbers the tiles use.
//
// Pure, no imports: web/src/lib/__tests__/configureRatedCap.test.mjs imports this file.

/** Round DOWN to `step` rpm. */
export const floorTo = (rpm: number, step = 10): number => Math.floor(rpm / step) * step;

/**
 * The highest speed at or below `start` where `bad(rpm)` is false, 1 % inside the border and
 * rounded down to 10 rpm, checked again on the predicate itself.  `start` itself when it is fine.
 * `null` when no speed down to `floor` is fine (then the caller keeps the stored point - the red
 * line stays honest instead of a speed the model cannot defend).
 */
export function ratedSpeedCap(start: number, bad: (rpm: number) => boolean,
                              opts: { floor?: number; scanSteps?: number; bisectSteps?: number } = {}): number | null {
  if (!(start > 0) || !Number.isFinite(start)) return null;
  if (!bad(start)) return start;
  const floor = Math.max(1, opts.floor ?? start * 0.05);
  const n = Math.max(4, opts.scanSteps ?? 40);
  // walk down from the start until a fine speed is found: [okRpm, badRpm] brackets the border
  let badRpm = start, okRpm: number | null = null;
  for (let k = 1; k <= n; k++) {
    const r = start - ((start - floor) * k) / n;
    if (!bad(r)) { okRpm = r; break; }
    badRpm = r;
  }
  if (okRpm == null) return null;
  let ok: number = okRpm;
  for (let k = 0; k < (opts.bisectSteps ?? 16); k++) {
    const mid: number = 0.5 * (ok + badRpm);
    if (bad(mid)) badRpm = mid; else ok = mid;
  }
  // 1 % inside, a round number - and that number itself must be fine (the predicate need not be
  // monotone below the border: step down until it is)
  let r = floorTo(ok * 0.99);
  for (let k = 0; k < 200 && r >= floor; k++) {
    if (!bad(r)) return r;
    r = floorTo(r * 0.99);
  }
  return null;
}

/** Loaded rated speed above the S1 motor limit by more than this fraction counts as stale data. */
export const STALE_ABOVE_S1 = 0.01;

export interface LoadedRated {
  /** the speed to put the loaded rated point at (`null` = nothing to apply yet) */
  rpm: number | null;
  /** true = the loaded duty's own speed, untouched (no 1 % offset, no rounding, no bisect) */
  exact: boolean;
  /** exact speed is more than 1 % above the S1 motor limit: stale data, the caller logs one warning */
  stale: boolean;
}

/**
 * The rated speed on the FIRST load of a motor (owner 2026-10-20: "on the first load the current point and
 * rated must coincide").  When the server answered an S1 motor limit for this propeller, the loaded rated
 * duty is already computed on that line (backend), so the speed is the duty's OWN speed, exactly - the page
 * does not move it and does not force a verdict.  Without an S1 answer (no request / refused / no
 * `limit_motor`) the old guard applies: `fallbackCap` (the `ratedSpeedCap` bisect), unchanged.
 * `s1IsBound` = the S1 "limit" is only the end of the scanned range (no real limit): never stale.
 */
export function loadedRatedRpm(a: { presetRpm: number | null | undefined; s1MotorRpm: number | null | undefined;
                                    s1IsBound?: boolean; fallbackCap: number | null }): LoadedRated {
  const preset = a.presetRpm, s1 = a.s1MotorRpm;
  if (preset != null && Number.isFinite(preset) && preset > 0 && s1 != null && Number.isFinite(s1) && s1 > 0) {
    return { rpm: preset, exact: true, stale: !a.s1IsBound && preset > s1 * (1 + STALE_ABOVE_S1) };
  }
  return { rpm: a.fallbackCap, exact: false, stale: false };
}
