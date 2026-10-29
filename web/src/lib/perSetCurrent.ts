/**
 * Six-phase (two in-phase 3-phase sets) current, as the engineer ENTERS it
 * (owner 2026-10-29, option 1).
 *
 * The solver and every stored duty keep ONE convention, unchanged: the phase
 * current of the equivalent 3-phase machine, i.e. BOTH sets together
 * (`current_arms`, `I_phase_rms_A`).  Each set (each controller) carries half
 * of it.  In six-phase mode the Operating point asks for the PER-SET current
 * and shows the both-sets total read-only; this module is the one place the
 * factor lives, so nothing is migrated.
 *
 * ONLY `phases: 6` is a two-set winding.  The parallel paths of a 3-phase
 * winding are NOT sets: a 4S-2P three-phase machine is entered and stored as it
 * always was.
 *
 * Dependency-free (no `import.meta.env`) so node imports the shipped file.
 */

/** In-phase sets the winding is split into: 2 for `phases: 6`, else 1. */
export function setsOf(winding: { phases?: number | string | null } | null | undefined): 1 | 2 {
  return Number(winding?.phases) === 6 ? 2 : 1;
}

/** What one set carries, from the stored both-sets (equivalent 3-phase) current. */
export function perSetFromTotal(total: number, sets: number): number {
  return sets === 2 ? total / 2 : total;
}

/** The stored both-sets current, from what the engineer typed for one set. */
export function totalFromPerSet(perSet: number, sets: number): number {
  return sets === 2 ? perSet * 2 : perSet;
}

/** "99.8 A per set" for a two-set winding, "199.7 A" otherwise (messages). */
export function currentText(total: number, sets: number, digits = 1): string {
  if (!Number.isFinite(total)) return '—';
  return sets === 2
    ? `${perSetFromTotal(total, sets).toFixed(digits)} A per set`
    : `${total.toFixed(digits)} A`;
}
