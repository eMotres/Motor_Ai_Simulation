/**
 * Steps per electrical period — the eddy-run default and the slip-ring snap.
 *
 * Owner decision 2026-09-26: runs with the coupled eddy solve (the Simulation
 * tab — eddy is always on there — the coupled loop, and the duty runs a report
 * quotes) default to 72 steps per period instead of 36/40.  BDF2 reads the L155
 * magnet loss −4.3 % at 36 steps and −1 % at 72
 * (docs/EDDY_TIME_INTEGRATION_2026-09-25.md §1).  The backend uses the same
 * number when a request names none (simulation/eddy_steps.py).
 *
 * It is a DEFAULT: the picker stays visible and any count the user picks is the
 * one solved («должен быть всегда выбор»).  Optimizer screening keeps its own
 * count while the tab holds only this default (routes/optimization.py).
 *
 * Pure functions only — `lib/__tests__/eddySteps.test.mjs` restates them.
 */

export const EDDY_DEFAULT_STEPS = 72;

/** The tab's factory defaults before 2026-09-26.  A count equal to one of them
 *  with no record of who set it is the old default, not a choice. */
export const LEGACY_DEFAULT_STEPS: readonly number[] = [36, 40];

/** Who set the count: the eddy default, or the user. */
export type StepsSource = 'eddy_default' | 'user';

export const isStepsSource = (v: unknown): v is StepsSource =>
  v === 'eddy_default' || v === 'user';

/** Nearest count the sliding band can run: a divisor of the ring's nodes per
 *  period (ties go up).  Above the ring any count is legal — the solver raises
 *  the ring to it — so it is returned unchanged.  Same rule as the solver's
 *  `_snap_steps_to_nodes` and the panel's picker. */
export function snapStepsToRing(v: number, ring: number): number {
  if (!(ring > 0) || !(v > 0)) return v;
  if (v > ring) return v;
  if (ring % v === 0) return v;
  let best = ring;
  for (let d = 1; d <= ring; d++)
    if (ring % d === 0 && (Math.abs(d - v) < Math.abs(best - v)
        || (Math.abs(d - v) === Math.abs(best - v) && d > best))) best = d;
  return best;
}

/** What the panel adopts from the shared config on mount.
 *
 *  `stored` is the config's count (else this browser's), `source` the config's
 *  record of who set it.  A recorded source is obeyed as is.  With no record:
 *  nothing stored, or one of the old factory defaults → the eddy default
 *  (`migratedFrom` names the old count so the panel can say so); any other
 *  count was chosen by someone and is kept as the user's. */
export function adoptSteps(stored: number | null, source: unknown): {
  steps: number; source: StepsSource; migratedFrom: number | null;
} {
  const n = typeof stored === 'number' && Number.isFinite(stored) && stored > 0
    ? Math.round(stored) : null;
  if (source === 'user' && n !== null)
    return { steps: n, source: 'user', migratedFrom: null };
  if (source === 'eddy_default')
    return { steps: n ?? EDDY_DEFAULT_STEPS, source: 'eddy_default', migratedFrom: null };
  if (n === null)
    return { steps: EDDY_DEFAULT_STEPS, source: 'eddy_default', migratedFrom: null };
  if (LEGACY_DEFAULT_STEPS.includes(n) && n !== EDDY_DEFAULT_STEPS)
    return { steps: EDDY_DEFAULT_STEPS, source: 'eddy_default', migratedFrom: n };
  return { steps: n, source: 'user', migratedFrom: null };
}

/** ONE line, or null, when the count that runs is not the count asked for. */
export function stepsNote(requested: number, ran: number, ring: number,
                          migratedFrom: number | null = null): string | null {
  if (migratedFrom !== null) {
    return `Steps ${migratedFrom} → ${ran}: eddy runs now default to ${EDDY_DEFAULT_STEPS}`
      + (ran !== EDDY_DEFAULT_STEPS
        ? ` (${ran} = nearest divisor of the ${ring}-node slip ring)` : '')
      + '.';
  }
  if (requested === ran) return null;
  return `Steps ${requested} → ${ran}: the count must divide this machine's `
    + `${ring}-node slip ring.`;
}
