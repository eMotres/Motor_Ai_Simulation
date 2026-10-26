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
  return isNum(torque) && isNum(rpm) ? Math.abs(torque * 2 * Math.PI * rpm / 60) : null;
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
/** The points the chart draws: rows with a torque and a speed (a half-filled row is not drawn). */
export function chartPoints(rows: KeyPoint[]): ChartPoint[] {
  const roles = pointRoles(rows);
  return rows.filter((r) => isNum(r.torque_Nm) && isNum(r.speed_rpm)).map((r) => ({
    id: r.row_id, name: r.name, torque: r.torque_Nm as number, speed: r.speed_rpm as number, duration: r.duration_s, role: roles.get(r.row_id) ?? null,
  }));
}
/** The maximum speed of the specification (rpm) for the chart's reference line; null while not set. */
export function chartMaxSpeed(draft: SpecDraft): number | null { return maximumSpeedRpm(draft); }
/** Mission-profile points (grey markers) - only rows with both numbers. */
export function missionChartPoints(draft: SpecDraft): Array<{ id: string; speed: number; torque: number }> {
  if (draft.mission_profile_status === 'not_known_yet') return [];
  return (draft.mission_profile ?? []).flatMap((row, i) => isNum(row.speed_rpm) && isNum(row.torque_Nm) ? [{ id: String(row.row_id ?? `m${i}`), speed: row.speed_rpm as number, torque: row.torque_Nm as number }] : []);
}

/** Round tick values covering [min, max] with about `target` steps (1, 2, 2.5, 5 x 10^n). */
export function niceTicks(min: number, max: number, target = 5): number[] {
  if (!(max > min)) return min === 0 ? [0, 1] : [min, min + 1];
  const raw = (max - min) / Math.max(1, target);
  const pow = 10 ** Math.floor(Math.log10(raw)), f = raw / pow;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * pow;
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
