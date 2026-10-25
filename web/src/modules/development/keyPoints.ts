/** Key operating points of a motor (owner 2026-10-26): any number of NAMED points (takeoff, cruise, landing, peak ...),
 *  each with torque, speed and duration (continuous or seconds) and an optional DC voltage.  The stored list is the source
 *  of truth; the S1 point (1_4) and the peak point (1_5) that the rest of the system reads are DERIVED from it with the
 *  same rule as the backend (`motor_ai_sim.development.models.derive_continuous_peak`).  A project that holds only 1_4 / 1_5
 *  reads as two points until a key point is edited.  Pure functions only. */
import type { SpecDraft, FieldAnswer } from './wizardState';

export const CONTINUOUS = 'continuous';
export const KEY_POINTS_ID = 'key_points';
export type KeyPointDuration = number | typeof CONTINUOUS | null;
export interface KeyPoint {
  row_id: string; name: string; torque_Nm: number | null; speed_rpm: number | null;
  duration_s: KeyPointDuration; voltage_V: number | null;
}
export type KeyPointProp = 'name' | 'torque_Nm' | 'speed_rpm' | 'duration_s';

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const rec = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {});
/** A plain number, or the maximum of a range stored before the one-value rule. */
const worst = (v: unknown): number | null => (isNum(v) ? v : isNum(rec(v).maximum) ? rec(v).maximum as number : null);

export function newKeyPointId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `kp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
export function blankKeyPoint(name = ''): KeyPoint {
  return { row_id: newKeyPointId(), name, torque_Nm: null, speed_rpm: null, duration_s: null, voltage_V: null };
}
/** The default rows of a new project (propulsion: takeoff, cruise, landing, peak); free names are allowed afterwards. */
export function defaultKeyPoints(names: string[]): KeyPoint[] {
  return names.map((name, i) => ({ ...blankKeyPoint(name), row_id: `default-${i}` }));
}

/** The properties of a point that are missing or invalid (a loud error each): a name, torque and speed, and a duration
 *  that is continuous or more than 0 seconds. */
export function keyPointErrors(row: Partial<KeyPoint>): KeyPointProp[] {
  const out: KeyPointProp[] = [];
  if (typeof row.name !== 'string' || !row.name.trim()) out.push('name');
  if (!isNum(row.torque_Nm)) out.push('torque_Nm');
  if (!isNum(row.speed_rpm) || row.speed_rpm < 0) out.push('speed_rpm');
  if (!(row.duration_s === CONTINUOUS || (isNum(row.duration_s) && row.duration_s > 0))) out.push('duration_s');
  return out;
}
export const keyPointUsable = (row: Partial<KeyPoint>) => keyPointErrors(row).length === 0;
export const keyPointsValid = (rows: KeyPoint[]) => rows.length > 0 && rows.every(keyPointUsable);

/** A key points list (or an explicit "not known yet") is stored; a bare placeholder leaves the legacy S1 / peak in charge. */
export function keyPointsStored(draft: SpecDraft): boolean {
  const answer = draft.fields?.[KEY_POINTS_ID];
  return !!answer && typeof answer === 'object' && (answer.status === 'not_known_yet' || Array.isArray(answer.value));
}

/** The S1 point (1_4) and the peak point (1_5) of a project without stored key points, as two points. */
export function legacyKeyPoints(draft: SpecDraft, names: { continuous: string; peak: string }): KeyPoint[] {
  const out: KeyPoint[] = [];
  for (const [id, name, rowId] of [['1_4', names.continuous, 'legacy-s1'], ['1_5', names.peak, 'legacy-peak']] as const) {
    const answer = draft.fields?.[id];
    const value = rec(answer && typeof answer === 'object' && !Array.isArray(answer) ? (answer as FieldAnswer).value : undefined);
    if ((answer as FieldAnswer | undefined)?.status === 'not_known_yet') continue;
    const torque = worst(value.torque_Nm), speed = worst(value.speed_rpm);
    if (torque === null && speed === null) continue;
    out.push({ row_id: rowId, name, torque_Nm: torque, speed_rpm: speed, duration_s: id === '1_4' ? CONTINUOUS : worst(value.duration_s), voltage_V: null });
  }
  return out;
}

/** The rows the key card shows: the stored list, else the two legacy points, else the default rows. */
export function effectiveKeyPoints(draft: SpecDraft, names: { continuous: string; peak: string }, defaults: string[]): KeyPoint[] {
  if (keyPointsStored(draft)) {
    const value = (draft.fields?.[KEY_POINTS_ID] as FieldAnswer).value;
    return Array.isArray(value) ? value.map((row) => ({ ...blankKeyPoint(), ...(row as Partial<KeyPoint>) })) : [];
  }
  const legacy = legacyKeyPoints(draft, names);
  return legacy.length ? legacy : defaultKeyPoints(defaults);
}

/** Continuous = a point with duration "continuous" (the highest torque among several), else the longest-duration point.
 *  Peak = the highest-torque point other than the continuous one, only if it exceeds the continuous torque.
 *  Ties: higher torque, then higher speed, then the earlier row. */
export function deriveContinuousPeak(rows: KeyPoint[]): { continuous: KeyPoint | null; peak: KeyPoint | null } {
  const usable = rows.map((row, i) => ({ row, i })).filter(({ row }) => keyPointUsable(row));
  if (!usable.length) return { continuous: null, peak: null };
  const best = <T extends { row: KeyPoint; i: number }>(items: T[], key: (item: T) => number[]): T => items.reduce((a, b) => {
    const ka = key(a), kb = key(b);
    for (let n = 0; n < ka.length; n++) if (ka[n] !== kb[n]) return ka[n] > kb[n] ? a : b;
    return a;
  });
  const t = (r: KeyPoint) => Math.abs(r.torque_Nm as number), s = (r: KeyPoint) => r.speed_rpm as number;
  const cont = usable.filter(({ row }) => row.duration_s === CONTINUOUS);
  const pick = cont.length
    ? best(cont, ({ row, i }) => [t(row), s(row), -i])
    : best(usable, ({ row, i }) => [row.duration_s as number, t(row), s(row), -i]);
  const others = usable.filter(({ i }) => i !== pick.i);
  const top = others.length ? best(others, ({ row, i }) => [t(row), s(row), -i]) : null;
  return { continuous: pick.row, peak: top && t(top.row) > t(pick.row) ? top.row : null };
}

const shaftPower = (torque: number, rpm: number) => torque * 2 * Math.PI * rpm / 60;

/** The draft with the key points stored and 1_4 / 1_5 derived from them (what the backend stores on save). Nothing is
 *  cleared while no point is usable; the peak's frequency / repeat period are kept. */
export function withKeyPoints(draft: SpecDraft, rows: KeyPoint[]): SpecDraft {
  const prev = draft.fields?.[KEY_POINTS_ID] as FieldAnswer | undefined;
  const status = prev?.status === 'not_known_yet' ? 'not_known_yet' : keyPointsValid(rows) ? 'filled' : 'missing';
  const fields: Record<string, FieldAnswer> = { ...(draft.fields ?? {}), [KEY_POINTS_ID]: { value: rows, status } };
  const { continuous, peak } = deriveContinuousPeak(rows);
  if (continuous && status !== 'not_known_yet') {
    const t = continuous.torque_Nm as number, n = continuous.speed_rpm as number;
    fields['1_4'] = { value: { torque_Nm: t, speed_rpm: n, shaft_power_W: shaftPower(t, n) }, status: 'filled' };
    const old = rec((fields['1_5'] as FieldAnswer | undefined)?.value);
    const kept = Object.fromEntries(['frequency', 'period_s'].filter((k) => old[k] !== undefined && old[k] !== null && old[k] !== '').map((k) => [k, old[k]]));
    if (peak) {
      const pt = peak.torque_Nm as number, pn = peak.speed_rpm as number;
      fields['1_5'] = { value: { torque_Nm: pt, speed_rpm: pn, output_power_W: Math.abs(shaftPower(pt, pn)), ...(peak.duration_s !== CONTINUOUS ? { duration_s: peak.duration_s } : {}), ...kept }, status: 'filled' };
    } else fields['1_5'] = { value: Object.keys(kept).length ? kept : null, status: 'missing' };
  }
  return { ...draft, fields };
}

/** Completeness of a legacy S1 point (no key points stored): a speed, and a torque or a stated power. */
export function legacyS1State(draft: SpecDraft): 'answered' | 'unknown' | 'invalid' | 'missing' {
  const answer = draft.fields?.['1_4'];
  if (!answer || typeof answer !== 'object' || Array.isArray(answer)) return 'missing';
  if (answer.status === 'not_known_yet' || answer.not_known_yet === true) return 'unknown';
  const value = rec(answer.value);
  const speed = worst(value.speed_rpm), torque = worst(value.torque_Nm), power = worst(value.power_W);
  if (answer.status === 'filled' && speed !== null && (torque !== null || (power !== null && power >= 0))) return 'answered';
  return Object.values(value).some((v) => v !== undefined && v !== null && v !== '') ? 'invalid' : 'missing';
}

/** The state of the required "key operating points" slot (stored points, else the legacy S1 rules). */
export function keyPointsSlotState(draft: SpecDraft): 'answered' | 'unknown' | 'invalid' | 'missing' {
  if (!keyPointsStored(draft)) return legacyS1State(draft);
  const answer = draft.fields?.[KEY_POINTS_ID] as FieldAnswer;
  if (answer.status === 'not_known_yet') return 'unknown';
  const rows = Array.isArray(answer.value) ? answer.value as KeyPoint[] : [];
  if (!rows.length) return 'missing';
  return keyPointsValid(rows) ? 'answered' : 'invalid';
}
