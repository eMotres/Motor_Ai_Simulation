/** Operating points of the wizard (step 2): the rows the cards edit, the values derived from them and the data of the
 *  torque-speed chart.  Pure functions, tested in node.  The stored data contract is unchanged (keyPoints.ts): the key
 *  points list is the source of truth and 1_4 / 1_5 are derived from it by `withKeyPoints`. */
import { CONTINUOUS, deriveContinuousPeak, effectiveKeyPoints, keyPointsStored, type KeyPoint, type KeyPointDuration } from '../../keyPoints.ts';
import { maximumSpeedRpm } from '../../speedLimits.ts';
import type { SpecDraft } from '../../wizardState.ts';
import { isUntouchedDefaults, profileDefaultRows, type WizardProfile } from './profile.ts';

/** Duration presets of a timed point, in seconds (the "Other" chip opens a free input). */
export const DURATION_PRESETS = [10, 30, 60] as const;

/** The rows the cards show: the stored key points, else the legacy S1 / peak points, else the defaults of the project's
 *  profile (takeoff / cruise / landing / peak, continuous / peak, cut-in / rated / peak).  A stored list that is still exactly
 *  an untouched default list follows the profile; a list the user changed is never replaced or renamed. */
export function operatingRows(draft: SpecDraft, o: { legacy: { continuous: string; peak: string }; profile: WizardProfile; nameOf: (key: string) => string }): KeyPoint[] {
  const rows = effectiveKeyPoints(draft, o.legacy, []);
  const storedList = keyPointsStored(draft) && Array.isArray((draft.fields?.key_points as { value?: unknown } | undefined)?.value);
  if ((!rows.length && !storedList) || isUntouchedDefaults(rows, o.nameOf)) return profileDefaultRows(o.profile, o.nameOf);
  return rows;
}

export type PointRole = 'continuous' | 'peak' | null;
/** Which row the system reads as the continuous (S1) and the peak (S2) point (the same rule as the backend). */
export function pointRoles(rows: KeyPoint[]): Map<string, PointRole> {
  const { continuous, peak } = deriveContinuousPeak(rows);
  return new Map(rows.map((row) => [row.row_id, row === continuous ? 'continuous' : row === peak ? 'peak' : null] as const));
}

export type DurationMode = 'continuous' | 'preset' | 'other' | 'unset';
/** How the duration chips read a duration; `otherOpen` keeps "Other" selected while its number is still empty. */
export function durationMode(duration: KeyPointDuration | undefined, otherOpen = false): DurationMode {
  if (duration === CONTINUOUS) return 'continuous';
  if (otherOpen) return 'other';
  if (typeof duration === 'number' && Number.isFinite(duration)) return (DURATION_PRESETS as readonly number[]).includes(duration) ? 'preset' : 'other';
  return 'unset';
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
/** Shaft power in watts of a torque (N·m) at a speed (rpm); null until both are numbers.  The magnitude: a generator
 *  point (negative torque) shows the power it takes at the shaft. */
export function shaftPowerOf(torque: unknown, rpm: unknown): number | null {
  if (!isNum(torque) || !isNum(rpm)) return null;
  const power = Math.abs(torque * 2 * Math.PI * rpm / 60);
  return Number.isFinite(power) ? power : null;          // two finite inputs can still overflow: no power, never "Infinity MW"
}
/** A speed the torque-power relation can use: finite and above zero (zero, negative and non-finite speeds never recalculate). */
export function usableSpeed(rpm: unknown): rpm is number { return isNum(rpm) && rpm > 0 && Number.isFinite(2 * Math.PI * rpm / 60); }
/** The torque (N·m) that gives `watts` of shaft power at `rpm`, P = T·ω with ω = 2π·rpm/60; `sign` keeps the sign of the stored
 *  torque (a generator point may be stored negative).  Null when the speed is unusable, the power is negative or not finite, or
 *  the result overflows - nothing is ever written as NaN or Infinity. */
export function torqueFromPower(watts: unknown, rpm: unknown, sign: number | null = 1): number | null {
  if (!isNum(watts) || watts < 0 || !usableSpeed(rpm)) return null;
  const torque = watts / (2 * Math.PI * rpm / 60);
  if (!Number.isFinite(torque)) return null;
  return torque === 0 ? 0 : (sign !== null && sign < 0 ? -torque : torque);
}
/** A number to `digits` significant digits without trailing zeros, for display only (the stored value keeps full precision). */
export function formatSig(n: number | null | undefined, digits = 6): string {
  if (!isNum(n)) return '';
  if (n === 0) return '0';
  const whole = Math.floor(Math.log10(Math.abs(n))) + 1;       // never drop integer digits: 1234567 stays 1234567
  return String(Number(n.toPrecision(Math.min(21, Math.max(digits, whole)))));
}
/** Typed text -> a number, 'empty' (cleared) or 'partial' (a half-typed "-", "." or "1e": nothing is written yet).  A comma
 *  counts as the decimal point. */
export function parseDecimal(text: string): number | 'empty' | 'partial' {
  const s = text.trim().replace(',', '.');
  if (s === '') return 'empty';
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(s)) return 'partial';
  const n = Number(s);
  return Number.isFinite(n) ? n : 'partial';
}
export type PowerUnit = 'W' | 'kW';
/** kW from 1000 W up, else W (the box keeps the unit the user picked). */
export const autoPowerUnit = (watts: number | null): PowerUnit => watts !== null && Math.abs(watts) >= 1000 ? 'kW' : 'W';

export type PointEdit = { kind: 'torque' | 'speed' | 'power'; value: number | null };
/** What one edit of the torque / speed / power boxes writes.  P = T·ω links them; the stored point holds torque and speed (the
 *  schema's key point has no power property) and the power is always derived from them, so the three never disagree:
 *   - torque edited: stored; the power follows.  - speed edited: stored; the power follows, the torque is kept - except a
 *     point whose torque is still empty and whose power was typed first, which now gets its torque from that power.
 *   - power edited: the torque follows (sign kept) at a usable speed; without one the power waits in `pending` and nothing else
 *     changes.  A negative or non-finite power is not applied.
 *  `pending` is the power typed before the speed was known (UI state, never stored). */
export function applyPointEdit(row: { torque_Nm: number | null; speed_rpm: number | null }, edit: PointEdit, pending: number | null): { change: { torque_Nm?: number | null; speed_rpm?: number | null }; pending: number | null } {
  if (edit.kind === 'torque') return { change: { torque_Nm: edit.value }, pending: null };
  if (edit.kind === 'speed') {
    const change: { speed_rpm: number | null; torque_Nm?: number | null } = { speed_rpm: edit.value };
    if (row.torque_Nm === null && pending !== null && usableSpeed(edit.value)) {
      const torque = torqueFromPower(pending, edit.value);
      if (torque !== null) { change.torque_Nm = torque; return { change, pending: null }; }
    }
    return { change, pending: row.torque_Nm === null ? pending : null };
  }
  if (edit.value === null) return { change: usableSpeed(row.speed_rpm) && row.torque_Nm !== null ? { torque_Nm: null } : {}, pending: null };
  if (!isNum(edit.value) || edit.value < 0) return { change: {}, pending };
  const torque = torqueFromPower(edit.value, row.speed_rpm, row.torque_Nm !== null && row.torque_Nm < 0 ? -1 : 1);
  return torque === null ? { change: {}, pending: edit.value } : { change: { torque_Nm: torque }, pending: null };
}
/** The power the box shows: derived from torque and speed, else the power typed before the speed was known. */
export function displayPower(row: { torque_Nm: number | null; speed_rpm: number | null }, pending: number | null): number | null {
  return shaftPowerOf(row.torque_Nm, row.speed_rpm) ?? pending;
}
/** A number to three significant digits without trailing zeros: 89.1, 1250, 0.512. */
export function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return '';
  const a = Math.abs(n);
  const digits = a >= 1000 ? 0 : a >= 100 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : 3;
  return String(Number(n.toFixed(digits)));
}
/** 850 W, 89.2 kW, 1.25 MW. */
export function formatPower(watts: number): string {
  const a = Math.abs(watts);
  if (a >= 1e6) return `${formatNumber(watts / 1e6)} MW`;
  if (a >= 1e3) return `${formatNumber(watts / 1e3)} kW`;
  return `${formatNumber(watts)} W`;
}

export interface ChartPoint { id: string; name: string; torque: number; speed: number; duration: KeyPointDuration; role: PointRole }
/** Values beyond this magnitude (no motor reaches them) are not plotted, so no scale calculation can overflow or underflow. */
export const PLOT_LIMIT = 1e12;
const plottable = (v: unknown): v is number => isNum(v) && Math.abs(v) <= PLOT_LIMIT;
/** The points the chart draws: rows with a torque and a speed (a half-filled row, a negative speed - a typing error that the
 *  validation still reports - and an absurd magnitude are not drawn). */
export function chartPoints(rows: KeyPoint[]): ChartPoint[] {
  const roles = pointRoles(rows);
  return rows.filter((r) => plottable(r.torque_Nm) && plottable(r.speed_rpm) && (r.speed_rpm as number) >= 0).map((r) => ({
    id: r.row_id, name: r.name, torque: r.torque_Nm as number, speed: r.speed_rpm as number, duration: r.duration_s, role: roles.get(r.row_id) ?? null,
  }));
}
/** The maximum speed of the specification (rpm) for the chart's reference line; null while not set. */
export function chartMaxSpeed(draft: SpecDraft): number | null { const v = maximumSpeedRpm(draft); return v !== null && plottable(v) ? v : null; }
/** Mission-profile points (grey markers) - only rows with both numbers. */
export function missionChartPoints(draft: SpecDraft): Array<{ id: string; speed: number; torque: number }> {
  if (draft.mission_profile_status === 'not_known_yet') return [];
  return (draft.mission_profile ?? []).flatMap((row, i) => plottable(row.speed_rpm) && (row.speed_rpm as number) >= 0 && plottable(row.torque_Nm) ? [{ id: String(row.row_id ?? `m${i}`), speed: row.speed_rpm as number, torque: row.torque_Nm as number }] : []);
}

/** Round tick values covering [min, max] with about `target` steps (1, 2, 2.5, 5 x 10^n). */
export function niceTicks(min: number, max: number, target = 5): number[] {
  const fallback = [0, 1];
  if (!Number.isFinite(min) || !Number.isFinite(max)) return fallback;
  if (!(max > min)) return min === 0 ? fallback : [min, min + 1];
  const raw = (max - min) / Math.max(1, target);
  const pow = 10 ** Math.floor(Math.log10(raw)), f = raw / pow;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * pow;
  if (!Number.isFinite(step) || !(step > 0) || !Number.isFinite(Math.floor(min / step) * step)) return fallback;      // subnormal or overflowing range
  const out: number[] = [];
  for (let v = Math.floor(min / step) * step; out.length < 50; v += step) { out.push(Number(v.toPrecision(12))); if (v >= max - step * 1e-9) break; }
  return out;
}

export interface ChartModel {
  width: number; height: number; plot: { left: number; top: number; right: number; bottom: number };
  xTicks: Array<{ value: number; x: number }>; yTicks: Array<{ value: number; y: number }>;
  points: Array<ChartPoint & { x: number; y: number }>; mission: Array<{ id: string; x: number; y: number }>;
  maxSpeed: { value: number; x: number } | null; xMax: number; yMin: number; yMax: number;
}
/** Scales and ticks of the torque-speed chart: the axes start at zero (or the lowest negative torque) and end at a round
 *  value above the largest point, the maximum speed included. */
export function buildChartModel(points: ChartPoint[], maxSpeed: number | null, mission: Array<{ id: string; speed: number; torque: number }> = [], size = { width: 640, height: 300 }): ChartModel {
  // whatever the caller passes, only plottable values reach the scales
  points = points.filter((p) => plottable(p.speed) && plottable(p.torque) && p.speed >= 0);
  mission = mission.filter((m) => plottable(m.speed) && plottable(m.torque) && m.speed >= 0);
  maxSpeed = maxSpeed !== null && plottable(maxSpeed) && maxSpeed > 0 ? maxSpeed : null;
  const plot = { left: 64, top: 16, right: size.width - 16, bottom: size.height - 44 };
  const speeds = [...points.map((p) => p.speed), ...mission.map((m) => m.speed), ...(maxSpeed ? [maxSpeed] : [])];
  const torques = [...points.map((p) => p.torque), ...mission.map((m) => m.torque)];
  const xTop = Math.max(...speeds, 0), yTop = Math.max(...torques, 0), yBottom = Math.min(...torques, 0);
  const xTicks = niceTicks(0, xTop > 0 ? xTop * 1.08 : 1);
  const yTicks = niceTicks(yBottom < 0 ? yBottom * 1.1 : 0, yTop > 0 ? yTop * 1.15 : 1);
  const xMax = xTicks[xTicks.length - 1], yMin = yTicks[0], yMax = yTicks[yTicks.length - 1];
  const px = (v: number) => plot.left + (v / xMax) * (plot.right - plot.left);
  const py = (v: number) => plot.bottom - ((v - yMin) / (yMax - yMin)) * (plot.bottom - plot.top);
  return {
    ...size, plot, xMax, yMin, yMax,
    xTicks: xTicks.map((value) => ({ value, x: px(value) })), yTicks: yTicks.map((value) => ({ value, y: py(value) })),
    points: points.map((p) => ({ ...p, x: px(p.speed), y: py(p.torque) })),
    mission: mission.map((m) => ({ id: m.id, x: px(m.speed), y: py(m.torque) })),
    maxSpeed: maxSpeed ? { value: maxSpeed, x: px(maxSpeed) } : null,
  };
}


/** The earlier S1 range kept by the migration (provenance), as the key points tooltip shows it; null when none. */
export function retainedS1Range(draft: SpecDraft): Record<string, unknown> | null {
  const provenance = draft.provenance as Record<string, unknown> | undefined;
  const history = provenance?.schema_migrations as Array<Record<string, unknown>> | undefined;
  const legacy = history?.at(-1)?.legacy_1_4;
  if (!legacy || typeof legacy !== 'object') return null;
  const answer = legacy as Record<string, unknown>;
  return answer.value && typeof answer.value === 'object' ? answer.value as Record<string, unknown> : answer;
}
/** A stored value or range as "lo–hi" text. */
export function readableRange(value: unknown): string {
  if (value == null) return '';
  if (typeof value !== 'object') return String(value);
  const range = value as Record<string, unknown>;
  if ('value' in range) return readableRange(range.value);
  const lo = range.minimum ?? range.min ?? range.from, hi = range.maximum ?? range.max ?? range.to;
  return lo == null && hi == null ? '' : `${lo ?? '—'}–${hi ?? '—'}`;
}

/** Where the maximum speed (1_6) is drawn: as the card beside the key points when the schema has them (version 2), else - a
 *  version-1 draft has no key points - as its own schema-driven field, so the required field is never unreachable (review F02). */
export function maxSpeedPlacement(fieldIds: string[]): 'card' | 'field' | null {
  if (!fieldIds.includes('1_6')) return null;
  return fieldIds.includes('key_points') ? 'card' : 'field';
}
/** What a mission-profile editor locks: marking the mission "not known yet" locks its inputs but never the control that takes
 *  the mark back (review F03); a busy page or a read-only viewer locks everything. */
export function missionLocks(o: { canEdit: boolean; busy: boolean; migrationBusy: boolean; unknown: boolean }): { inputs: boolean; toggle: boolean } {
  const all = !o.canEdit || o.busy || o.migrationBusy;
  return { inputs: all || o.unknown, toggle: all };
}
