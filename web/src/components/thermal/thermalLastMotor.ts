import type { LastMotorSelection } from '../../lib/lastMotor';

export interface ActiveFamilyContext {
  active?: boolean;
  die?: string | null;
  config?: string | null;
  duty?: string | null;
}

/** A Thermal propeller registry is safe to bind only to the family currently
 * loaded in the shared editor. A null catalogue reference remains valid. */
export function selectionMatchesActiveFamily(
  selection: LastMotorSelection | null | undefined,
  context: ActiveFamilyContext | null | undefined,
): boolean {
  if (!selection || !context || context.active !== true) return false;
  if (typeof selection.die !== 'string' || !selection.die.trim()
      || typeof selection.config !== 'string' || !selection.config.trim()
      || !(selection.duty === null || typeof selection.duty === 'string')
      || !(selection.ref_id === null
        || (typeof selection.ref_id === 'string' && !!selection.ref_id.trim()))) return false;
  if (typeof context.die !== 'string' || !context.die.trim()
      || typeof context.config !== 'string' || !context.config.trim()
      || !(context.duty === null || typeof context.duty === 'string')) return false;
  return selection.die === context.die
    && selection.config === context.config
    && selection.duty === (context.duty ?? null);
}
