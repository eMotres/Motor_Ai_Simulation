/** The side-by-side comparison of the specification's requirements with a linked motor's duties (owner 2026-11-25).  Pure: the
 *  server decides the state (development/spec_sync.py); this file only shapes rows for the table and the tests. */
import type { PointCheck, PointComparison, PointState, ProjectMotor } from './types';

export const STATE_ORDER: PointState[] = ['no_point', 'stale', 'differs', 'matched'];
export const STATE_COLOR: Record<PointState, 'default' | 'warning' | 'error' | 'success'> = { no_point: 'default', stale: 'warning', differs: 'error', matched: 'success' };

/** A requirement that needs somebody: not matched. */
export const needsAttention = (c: PointComparison): boolean => c.state !== 'matched';

/** Motors that carry a comparison, in the order given; a motor without one (older server) is left out. */
export function comparedMotors(motors: ProjectMotor[]): ProjectMotor[] {
  return motors.filter((m) => m.kind !== 'history' && m.available !== false && Array.isArray(m.point_comparisons) && m.point_comparisons.length > 0);
}

/** `+0.5` / `-1.2` / `` for the difference column; the sign shows which side is larger (result minus requirement). */
export function deltaText(check: PointCheck | undefined, digits = 2): string {
  if (!check || check.delta === null || !Number.isFinite(check.delta)) return '';
  const v = Number(check.delta.toFixed(digits));
  return `${v > 0 ? '+' : ''}${v}`;
}

export interface RequirementRow { id: string; name: string; state: PointState; duty: string | null; suggested: string | null; reasons: string[]; cells: Array<{ key: 'torque' | 'speed' | 'voltage' | 'thermal'; required: number | null; result: number | null; delta: string; status: string }> }

export function requirementRows(motor: ProjectMotor): RequirementRow[] {
  return (motor.point_comparisons ?? []).map((c) => ({
    id: c.spec_row_id, name: c.requirement.name, state: c.state, duty: c.duty, suggested: c.suggested ?? null, reasons: c.reasons,
    cells: (['torque', 'speed', 'voltage', 'thermal'] as const).map((key) => {
      const k = c.checks[key];
      return { key, required: k?.required ?? (key === 'torque' ? c.requirement.torque_Nm : key === 'speed' ? c.requirement.speed_rpm : key === 'voltage' ? c.requirement.voltage_V : c.requirement.duration_s),
               result: k?.result ?? null, delta: deltaText(k), status: k?.status ?? 'n/a' };
    }),
  }));
}

/** The unlinked requirements with ONE unambiguous suggestion: what "link automatically" would do. */
export const suggestedCount = (motor: ProjectMotor): number => (motor.point_comparisons ?? []).filter((c) => c.state === 'no_point' && !!c.suggested && !c.link).length;
