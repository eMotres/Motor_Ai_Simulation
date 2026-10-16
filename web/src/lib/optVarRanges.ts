/**
 * Default ranges for the OPTIMIZATION VARIABLES list.
 *
 * Owner 2026-10-15 (CIANO28 85 loaded): "+ Add all geometry" gave Slot Height
 * 3.9-4 while the motor is at 7.4, Tooth Width 0.9-1.1 vs 5, Wires per Slot
 * 10-11 vs 18 ...  Cause: re-adding a variable restored the range it "last ran
 * with" from the stored sweep config - one config per account, persisted in
 * localStorage and mirrored to the server - so ranges typed for ANOTHER (smaller)
 * motor came back for this one.  A remembered range is now reused only when it
 * (a) was not stamped for another motor and (b) still contains the motor's current
 * value; otherwise the range is bracketed around the CURRENT geometry value.
 *
 * Pure and dependency-free: `node --test` loads this file directly
 * (lib/__tests__/optVarRanges.test.mjs).
 */

export interface VarRange { min: number; max: number; step: number }

export interface RangeSchema {
  type?: string;
  min?: number | null;
  max?: number | null;
  step?: number | null;
}

/** Half-width of the default window, as a fraction of the current value. */
export const BRACKET_PCT = 0.10;

const r6 = (x: number) => Math.round(x * 1e6) / 1e6;
const floorTo = (x: number, g: number) => r6(Math.floor(x / g + 1e-9) * g);
const ceilTo = (x: number, g: number) => r6(Math.ceil(x / g - 1e-9) * g);

const num = (x: unknown): number | null => {
  if (x === null || x === undefined || x === '') return null;
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
};

/**
 * The default window around the CURRENT value: +-10 %, rounded OUTWARD to the
 * step (so the current value is always inside), clamped to the schema limits
 * (never so far that the current value falls out); integers get +-1..2.
 */
export function bracketRange(cur: number, sch?: RangeSchema | null, pct: number = BRACKET_PCT): VarRange {
  const isInt = sch?.type === 'int';
  const lo = num(sch?.min), hi = num(sch?.max);
  const stepRaw = num(sch?.step);
  const step = stepRaw !== null && stepRaw > 0 ? stepRaw : (isInt ? 1 : 0.1);

  if (!Number.isFinite(cur)) {
    // nothing to centre on: the schema window, or a harmless unit range
    if (lo !== null && hi !== null && hi > lo) return { min: lo, max: hi, step };
    return { min: 0, max: isInt ? 1 : step, step };
  }

  let dev = isInt
    ? Math.min(2, Math.max(1, Math.round(Math.abs(cur) * pct)))
    : Math.abs(cur) * pct;
  if (!(dev > 0)) dev = lo !== null && hi !== null && hi > lo ? (hi - lo) * pct : (isInt ? 1 : step);

  let min = cur - dev, max = cur + dev;
  if (isInt) {
    min = Math.floor(min + 1e-9); max = Math.ceil(max - 1e-9);
  } else {
    const g = step <= dev ? step : 0.001;
    min = floorTo(min, g); max = ceilTo(max, g);
  }
  if (lo !== null) min = Math.min(Math.max(min, lo), cur);
  if (hi !== null) max = Math.max(Math.min(max, hi), cur);
  if (!(max > min)) max = isInt ? min + 1 : r6(min + step);
  return { min, max, step };
}

/** Does the stored range contain the value (with a hair of float tolerance)? */
export function rangeContains(v: { min?: unknown; max?: unknown } | null | undefined, cur: number): boolean {
  if (!v || !Number.isFinite(cur)) return false;
  const lo = num(v.min), hi = num(v.max);
  if (lo === null || hi === null) return false;
  const tol = 1e-9 * Math.max(1, Math.abs(cur));
  return cur >= lo - tol && cur <= hi + tol;
}

/** A usable stored range: finite, non-degenerate, positive step. */
export function isUsableRange(v: { min?: unknown; max?: unknown; step?: unknown } | null | undefined): boolean {
  if (!v) return false;
  const lo = num(v.min), hi = num(v.max), st = num(v.step);
  return lo !== null && hi !== null && hi > lo && st !== null && st > 0;
}

/**
 * May "add variable" restore the range the variable last ran with?  Only if the
 * range is a real one, was NOT stamped for a different motor (an unstamped
 * range - written before stamping existed - passes this test and relies on the
 * next one), and still contains the motor's current value.  Everything else
 * gets the bracket around the current value.
 */
export function reuseRemembered(
  prev: { min?: unknown; max?: unknown; step?: unknown; scope?: string } | null | undefined,
  cur: number,
  scope: string,
): boolean {
  if (!isUsableRange(prev)) return false;
  const stamped = prev?.scope;
  if (stamped !== undefined && stamped !== scope) return false;
  return rangeContains(prev, cur);
}

/** Identity of the loaded motor for scoping stored ranges: die + configuration
 *  when a die is active, plus the lamination keys of the live geometry. */
export function identityScope(
  geometry: Record<string, unknown> | null | undefined,
  die?: string | null,
  config?: string | null,
): string {
  const g = geometry ?? {};
  const k = (n: string) => { const v = num(g[n]); return v === null ? '' : String(v); };
  return [
    die ?? '', config ?? '',
    k('stator_diameter'), k('num_seg'), k('num_slots_per_segment'), k('num_poles_per_segment'),
  ].join('|');
}

/** Selected (mode !== 'fixed') variables whose range does not contain the
 *  current geometry value.  `skip` lists non-geometry variables (gamma, current). */
export function outOfRangeNames(
  vars: Record<string, { mode?: string; min?: unknown; max?: unknown } | undefined>,
  geometry: Record<string, unknown> | null | undefined,
  skip: readonly string[] = ['gamma_deg', 'current_a'],
): string[] {
  const out: string[] = [];
  for (const [name, v] of Object.entries(vars ?? {})) {
    if (!v || v.mode === 'fixed' || skip.includes(name)) continue;
    const cur = num(geometry?.[name]);
    if (cur === null) continue;
    if (!rangeContains(v, cur)) out.push(name);
  }
  return out;
}
