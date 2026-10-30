/**
 * APPLYING A SWEEP POINT ONTO A LOCKED CONFIGURATION.
 *
 * Owner 2026-10-30: after a reload the page re-activated the family
 * configuration "CIANO56 400_80 vadim 6 / L90-...", whose die is locked.  The
 * sweep varies lamination keys (tooth_width, cut_width, magnet_fill_*); Apply
 * wrote them with `PUT /api/geometry`, the server answered 423 Locked (input
 * guard 4, `family.geometry_lock_check`) twice, and the owner saw a button that
 * "did nothing".
 *
 * A locked die is not edited: a different lamination is a different die.  Apply
 * therefore takes the same road a duty save takes since #514 - release the
 * locked context, write the point, keep it as a NEW die (backend
 * `POST /api/family/save_as_new_die`: "<base> <your suffix> <n>", unlocked, with
 * one configuration and one duty at the applied operating point) - and says so.
 * The locked die is never written.
 *
 * Pure (no React, no fetch) so `node --test` pins the rules.
 */

export interface RefusedField { field: string; reason: string }

/** `/api/family/context` - the fields read here. */
export interface FamilyCtxLike {
  active?: boolean;
  die?: string | null;
  config?: string | null;
  duty?: string | null;
  die_locked?: boolean;
  config_locked?: boolean;
}

export type LockedApplyPlan =
  | { kind: 'new-die'; from: string; config: string; duty: string }
  | { kind: 'blocked'; text: string };

/** "tooth_width: locked by die 'X'; cut_width: ..." - the server's own words. */
export function refusedText(refused: ReadonlyArray<RefusedField> | null | undefined): string {
  const r = refused ?? [];
  return r.map((x) => (x.field && x.field !== '(geometry)' ? `${x.field}: ${x.reason}` : x.reason)).join('; ');
}

const names = (refused: ReadonlyArray<RefusedField>): string =>
  refused.map((x) => x.field).filter((f) => f && f !== '(geometry)').join(', ') || 'the swept geometry';

/**
 * What to do after `PUT /api/geometry` refused a picked point.
 *
 * - the active configuration is locked and the user may write the catalog and no
 *   sweep is running -> keep the point as a NEW die;
 * - a sweep is running -> blocked: a new die is another machine, and the running
 *   sweep stops the moment the loaded machine changes (machine binding, #496);
 * - the user may not write the catalog -> blocked, and says who can;
 * - anything else (not a lock) -> blocked with the server's own reason.
 */
export function planLockedApply(
  ctx: FamilyCtxLike | null | undefined,
  refused: ReadonlyArray<RefusedField> | null | undefined,
  opts: { sweepRunning: boolean; canWrite: boolean },
): LockedApplyPlan {
  const why = refusedText(refused);
  const locked = !!ctx?.active && !!ctx.die && (ctx.die_locked === true || ctx.config_locked === true);
  if (!locked) {
    return { kind: 'blocked', text: `the server refused the geometry${why ? ` - ${why}` : ''}` };
  }
  const where = `${ctx!.die}${ctx!.config ? ` / ${ctx!.config}` : ''}`;
  const lead = `${where} is locked, so ${names(refused ?? [])} cannot change on it`;
  if (opts.sweepRunning) {
    return { kind: 'blocked', text: `${lead}; a different lamination is kept as a new die, and loading another machine `
      + 'would stop the sweep that is still running. Stop the sweep, then Apply again.' };
  }
  if (!opts.canWrite) {
    return { kind: 'blocked', text: `${lead}; only a user who may write the motor catalog can keep it as a new die.` };
  }
  return { kind: 'new-die', from: where, config: String(ctx!.config ?? ''), duty: String(ctx!.duty ?? '') };
}

/** The line after a point landed on a new die. */
export function newDieNotice(from: string, die: string, config: string, duty: string): string {
  return `${from} is locked - the point is applied on a NEW die '${die}' (${config} / ${duty}); ${from} is untouched.`;
}
