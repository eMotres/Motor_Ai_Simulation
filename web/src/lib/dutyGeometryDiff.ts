/**
 * Pure formatting for "these saved results were computed on another build":
 * the summary card's stale chip (components/simulation/SummaryTable.tsx)
 * compares a result's `_geoSig` stamp with the live geometry and names what
 * moved.  The rows are the same `{key, scope, live, duty}` shape family.py's
 * `duty_geometry_diff` answers in `/api/family/activate`.
 *
 * Until 2026-10-29 these rows fed a blocking "This duty was saved on a
 * different geometry" dialog whose default button reverted the configuration
 * to the duty's older build.  The dialog is gone: a duty always opens on the
 * configuration's current build and its older results are shown stale.
 *
 * Round for DISPLAY only (incident 2026-09-29: "rotor_inner_radius
 * 4.800000000000002 → 5.000000000000002" was unreadable).
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

/** One side of the diff as a plain list: "magnet_up_gap 0.200 mm, rotor_hole
 *  0.900 mm".  `live` = the die as it is now, `duty` = what the duty was
 *  computed on.  Rounded for display only (formatDutyGeometryValue). */
export function formatDutyGeometryKeyList(
  diffs: DutyGeometryDiffRow[], side: 'live' | 'duty',
): string {
  return diffs.map((d) => `${d.key} ${formatDutyGeometryValue(d.key, d[side])}`).join(', ');
}

/** Parse a `_geoSig` stamp ("key:value|key:value", components/common/geoSig.ts)
 *  into numbers.  Unreadable pairs are skipped; '' or null gives {}. */
export function parseGeoSig(stamp: string | null | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  for (const pair of String(stamp ?? '').split('|')) {
    const i = pair.indexOf(':');
    if (i <= 0) continue;
    const v = Number(pair.slice(i + 1));
    if (Number.isFinite(v)) out[pair.slice(0, i)] = v;
  }
  return out;
}

/** Where a result's stamp differs from the live geometry: keys BOTH sides carry
 *  as numbers, relative tolerance 1e-6 (family.py `duty_geometry_diff`).
 *  `live` = the build now, `duty` = what the result was computed on. */
export function geoSigDiffRows(stamp: string | null | undefined,
                               live: Record<string, unknown> | null | undefined,
): DutyGeometryDiffRow[] {
  const saved = parseGeoSig(stamp);
  const rows: DutyGeometryDiffRow[] = [];
  for (const key of Object.keys(saved).sort()) {
    const lv = (live ?? {})[key];
    if (typeof lv !== 'number' || !Number.isFinite(lv)) continue;
    const sv = saved[key];
    if (Math.abs(lv - sv) <= 1e-6 * Math.max(1, Math.abs(lv))) continue;
    rows.push({ key, scope: '', live: lv, duty: sv });
  }
  return rows;
}

/** "insulation_thickness 0.200 → 0.250 mm, wire_spacing_x 0.100 → 0.120 mm
 *  (+2 more)": saved → now, at most `max` keys, for the stale tooltip. */
export function staleBuildKeysText(rows: DutyGeometryDiffRow[], max = 4): string {
  const shown = rows.slice(0, max).map((r) => {
    const now = formatDutyGeometryValue(r.key, r.live);
    const was = formatDutyGeometryValue(r.key, r.duty);
    // one unit per pair: "0.200 → 0.250 mm", not "0.200 mm → 0.250 mm"
    const unit = now.endsWith(' mm') && was.endsWith(' mm') ? ' mm' : '';
    return `${r.key} ${unit ? was.slice(0, -3) : was} → ${unit ? now.slice(0, -3) : now}${unit}`;
  });
  const more = rows.length - shown.length;
  return shown.join(', ') + (more > 0 ? ` (+${more} more)` : '');
}
