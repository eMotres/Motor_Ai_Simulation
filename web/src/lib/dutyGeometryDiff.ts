/**
 * Pure formatting for the "duty saved on a different geometry" dialog
 * (web/src/lib/dutyApply.ts, family.py `duty_geometry_diff`).
 *
 * The raw diff off the wire is `{key, scope, live, duty}` with UNROUNDED
 * floats — the old `window.confirm()` this replaced printed
 * "rotor_inner_radius 4.800000000000002 → 5.000000000000002" and the owner
 * could not tell what actually differs (incident 2026-09-29). Round for
 * DISPLAY only, never for what is sent back to the server: the dialog's
 * answer is always the `geometry_choice` string, never a rounded number.
 *
 * Dependency-free (no `import.meta.env`) so node imports the SHIPPED file —
 * same convention as lib/timeToLimitChip.ts.
 */

export interface DutyGeometryDiffRow {
  key: string;
  scope: 'identity' | 'die' | 'winding' | 'free' | string;
  live: number;
  duty: number;
}

/** Geometry keys the schema carries as plain counts (segments, slots,
 *  poles, wires, strands) — no unit, no fractional rounding. */
const COUNT_KEYS = new Set([
  'num_seg', 'num_slots_per_segment', 'num_poles_per_segment',
  'num_wires_per_slot', 'wire_split', 'wire_parallel',
]);

export function isCountKey(key: string): boolean {
  return COUNT_KEYS.has(key) || key.startsWith('num_');
}

/** Angle keys read in degrees; everything else that is not a count is a
 *  physical dimension in mm — which covers every key duty_geometry_diff
 *  actually compares today (FREE_GEO_KEYS / DIE_IDENTITY_KEYS / die.geometry). */
function unitOf(key: string): 'count' | 'deg' | 'mm' {
  if (isCountKey(key)) return 'count';
  if (key.endsWith('_deg')) return 'deg';
  return 'mm';
}

/** 4 significant digits — the fallback rounding for whatever isn't a plain
 *  mm dimension (an angle, or an unrecognised key an older die stamp carries). */
export function toSignificantDigits(value: number, digits = 4): number {
  if (!Number.isFinite(value) || value === 0) return value;
  const mag = Math.ceil(Math.log10(Math.abs(value)));
  const factor = 10 ** (digits - mag);
  return Math.round(value * factor) / factor;
}

/** The dialog's own rounding rule: counts → nearest integer, mm dimensions →
 *  0.001 mm, everything else → 4 significant digits.  Values that are not
 *  finite numbers pass through unchanged so the table can still say
 *  something about a malformed stamp rather than throwing. */
export function roundDutyGeometryValue(key: string, value: number): number {
  if (!Number.isFinite(value)) return value;
  const unit = unitOf(key);
  if (unit === 'count') return Math.round(value);
  if (unit === 'mm') return Math.round(value * 1000) / 1000;
  return toSignificantDigits(value, 4);
}

/** The rounded value with its unit, e.g. "5.000 mm", "6", "30.00°". */
export function formatDutyGeometryValue(key: string, value: number): string {
  if (!Number.isFinite(value)) return String(value);
  const rounded = roundDutyGeometryValue(key, value);
  const unit = unitOf(key);
  if (unit === 'count') return String(rounded);
  if (unit === 'deg') return `${rounded}°`;
  return `${rounded.toFixed(3)} mm`;
}

/** One table row's plain-text rendering, for the fallback/log path and for
 *  tests — the dialog itself renders the same numbers into MUI table cells. */
export function formatDutyGeometryDiffRow(row: DutyGeometryDiffRow): string {
  return `${row.key} (${row.scope}): die ${formatDutyGeometryValue(row.key, row.live)}`
    + ` → duty ${formatDutyGeometryValue(row.key, row.duty)}`;
}

/** Why "apply duty geometry" cannot be offered, or null when it can.  Mirrors
 *  the two ways family.py's `activate` refuses geometry_choice=apply_duty: a
 *  foreign lamination (422 duty_geometry_foreign_lamination) and a locked die
 *  (423 die_locked) — surfaced HERE, before the owner picks the choice and
 *  hits a second, confusing failure (incident 2026-09-29: "die is locked ...
 *  sign in again and retry" appended to a plain lock, not an auth problem).
 *
 *  `dieLocked` is trusted AS GIVEN — it is the 409 payload's own
 *  `die_locked` field, which the backend already scopes to "the lock would
 *  actually block this apply" (a winding-only diff never touches die.yaml,
 *  so a locked die does not block it, and the backend's own flag says so).
 *  This function does not re-derive that scoping from `scope: 'die'` rows —
 *  one place decides it, see `activate()`'s `die_locked` computation. */
export function dutyGeometryApplyDisabledReason(
  diffs: DutyGeometryDiffRow[], dieLocked: boolean,
): string | null {
  if (diffs.some((d) => d.scope === 'identity')) {
    return 'different lamination (Ø / slots / poles) — save the duty as a new die instead';
  }
  if (dieLocked) {
    return 'die is released/locked — unlock it in the Motors catalog first';
  }
  return null;
}

/** The three choices the dialog can resolve to.  `null` only when no dialog
 *  could be shown at all (e.g. the render tree that owns it never mounted) —
 *  the load stops rather than silently picking one side. */
export type DutyGeometryChoice = 'apply_duty' | 'keep_die' | null;

/** The load-cancelled reason `applyDutyEverywhere` throws for every non-choice
 *  outcome (Cancel, or no dialog at all) — one string, so a test can pin it
 *  and a caller never has to special-case "which kind of cancel was this". */
export function dutyGeometryCancelledMessage(): string {
  return 'load cancelled — die geometry differs from the duty';
}
