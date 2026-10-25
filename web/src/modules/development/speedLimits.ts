// "Maximum speed" is the ONE speed limit the specification asks (owner 2026-10-26): the single highest speed the motor ever
// sees, and the mechanics are checked at exactly that speed (proof speed = maximum speed, no factor).  An older project may
// hold an explicitly stored overspeed value (it can come from an engine or generator limit): when that is LARGER it wins
// and is shown read-only in the field's tooltip.  The same rule as the backend's `proof_speed_rpm` (development/models.py).
// Torque and voltage requirements come from the key operating points, never from this field.
import type { SpecDraft } from './wizardState.ts';

const worst = (v: unknown): number | null => {
  if (v && typeof v === 'object' && !Array.isArray(v)) return worst((v as Record<string, unknown>).maximum);
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
};
const value1_6 = (draft: SpecDraft): Record<string, unknown> | null => {
  const answer = (draft.fields as Record<string, unknown> | undefined)?.['1_6'] as { status?: string; value?: unknown } | undefined;
  if (!answer || typeof answer !== 'object' || answer.status === 'not_known_yet') return null;
  return answer.value && typeof answer.value === 'object' && !Array.isArray(answer.value) ? answer.value as Record<string, unknown> : null;
};
/** The specification's maximum speed (rpm); a stored range reads as its maximum. */
export function maximumSpeedRpm(draft: SpecDraft): number | null {
  const n = worst(value1_6(draft)?.maximum_rpm);
  return n !== null && n > 0 ? n : null;
}
/** An older explicit overspeed (rpm) that is larger than the maximum speed - the one the mechanics use instead. */
export function storedOverspeedRpm(draft: SpecDraft): number | null {
  const stored = worst(value1_6(draft)?.overspeed_rpm);
  if (stored === null || stored <= 0) return null;
  const top = maximumSpeedRpm(draft);
  return top === null || stored > top ? stored : null;
}
/** The speed the mechanics are checked at: the maximum speed, or the larger stored overspeed. */
export function proofSpeedRpm(draft: SpecDraft): number | null {
  const top = maximumSpeedRpm(draft), stored = storedOverspeedRpm(draft);
  return stored ?? top;
}
