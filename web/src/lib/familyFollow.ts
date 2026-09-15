// FOLLOW the active machine — the rule that stops a SECOND browser from
// solving the machine it no longer has on screen.
//
// The Electromagnetic panel's operating point (sim.current, sim.rpm,
// sim.gamma, sim.opMode, sim.connection, sim.daxisDeg, sim.frequency) lives PER
// BROWSER, in localStorage, and exactly one place writes it: ▶ in the Motors
// catalog (FamilyCatalog.applyDuty).  A second window of the same app never
// learned that the machine had changed — its header strip follows
// /api/family/context and named the NEW die, while its panel still held the
// OLD one's excitation.  Pressing Run there PATCHes that stale point into the
// SHARED simulation config and solves it on the new machine.
//
// Live 2026-09-08 23:23: CILN28 / G2-L40 / "peak" (40 A, 3000 rpm, γ −15°,
// generator) was loaded from one browser; the other still held the Ø200's
// 687 A / 20 900 rpm and its coupled-loop temperatures (coil 111 °C, magnets
// 137 °C).  The coupled Run that followed solved the Ø200's point on the 40 mm
// generator: 194 kW, 11 891 V, 367 kW of losses, a "thermal runaway" to
// 48 873 °C and a refused rotor-stress step.  The user saw "temperature
// glitches" and "mechanics left from the old motor".
//
// This module holds the two PURE halves of the fix:
//   • the MARK — which die/config/duty this browser's panel last APPLIED,
//     persisted beside the panel state it describes;
//   • the DECISION — that mark against the polled context.
// The adoption itself is lib/dutyLocalApply.ts (the local half of ▶, shared
// with the catalog so the two cannot drift), driven by ActiveFamilyStrip.

import { activeDuty } from './dutySettings';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

const KEY = 'family.appliedContext';

/** /api/family/context, only the fields the decision reads. */
export interface FamilyCtxLike {
  active?: boolean;
  die?: string | null;
  config?: string | null;
  duty?: string | null;
  can_write?: boolean;
  /** The ISO stamp /api/family/activate writes into .family_context.json.
   *  The context ROUTE does not return it for an active context today
   *  (2026-09-08) — the die/config/duty triple is what decides, and this
   *  comparison is here so a backend that starts sending it can only make the
   *  rule stricter (never adopt a context OLDER than the one applied), never
   *  looser. */
  at?: string | null;
}

/** What this browser's panel last applied — written by lib/dutyLocalApply. */
export interface AppliedContext {
  die: string;
  config: string;
  duty: string | null;
  /** the server's own stamp for that context, when it had one */
  at?: string | null;
  /** when THIS browser applied it (ms) — what the strip's notice shows */
  appliedAt: number;
  /** the context was seen RELEASED since (a Compare / my-motors / preset load
   *  elsewhere).  A ▶ after a release is a fresh load even when it names the
   *  same duty, so the next active context is adopted rather than skipped. */
  released?: boolean;
}

export function readAppliedContext(): AppliedContext | null {
  try {
    const r = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (r && r.die && r.config) {
      return {
        die: String(r.die), config: String(r.config),
        duty: r.duty == null ? null : String(r.duty),
        at: r.at == null ? null : String(r.at),
        appliedAt: Number(r.appliedAt) || 0,
        released: r.released === true,
      };
    }
  } catch { /* unreadable — fall through to the migration below */ }
  // MIGRATION, read-side (2026-09-08, the day this key was added).  A browser
  // that has been loading duties for months already records the last one it
  // applied — the per-duty memory's own context marker, written by the same ▶
  // (lib/dutySettings).  Reading it as the baseline is what makes the fix bite
  // on the FIRST machine change after the deploy instead of the second: the
  // browser that held the Ø200's point would otherwise have adopted the CILN28
  // silently as its baseline and followed only the load after that.
  //
  // Not written back: the first seed or adopt writes the real key, and until
  // then this is a faithful answer to "what did this panel last apply?".  No
  // `at` — the marker's own stamp is a local Date.now(), not the server's.
  try {
    const a = activeDuty();
    if (a) {
      return { die: a.die, config: a.config, duty: a.duty, at: null, appliedAt: 0 };
    }
  } catch { /* no per-duty memory either — a browser that never applied one */ }
  return null;
}

/** This browser's panel now holds THIS duty.  Called by the catalog's ▶ and by
 *  the follower, through the one apply they share. */
export function rememberAppliedContext(die: string, config: string,
                                       duty: string | null,
                                       at?: string | null): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({
      die, config, duty: duty ?? null, at: at ?? null,
      appliedAt: Date.now(), released: false,
    }));
  } catch { /* quota — the follower then re-seeds from the next poll */ }
}

/** The context went RELEASED while this browser held a duty. */
export function markContextReleased(): void {
  const a = readAppliedContext();
  if (!a || a.released) return;
  try { localStorage.setItem(KEY, JSON.stringify({ ...a, released: true })); }
  catch { /* quota */ }
}

export type AdoptionVerdict = 'skip' | 'seed' | 'adopt' | 'release';

/**
 * PURE.  What should a browser do with the context it just polled?
 *
 *   skip     nothing to follow (no context, not an owner, already on it);
 *   seed     record it as the baseline WITHOUT touching the panel — a browser
 *            that has never applied a duty must not have its own un-saved
 *            point reset by a page reload (the strip's "≠ point changed" is a
 *            legitimate state, and F5 must not undo it);
 *   release  the context is released — remember that, so the next ▶ elsewhere
 *            counts as a fresh load even if it names the same duty;
 *   adopt    the header names a machine this panel has not applied — take that
 *            duty's operating point.
 */
export function adoptionDecision(ctx: FamilyCtxLike | null | undefined,
                                 applied: AppliedContext | null): AdoptionVerdict {
  if (!ctx) return 'skip';                       // fetch failed — say nothing
  if (ctx.active !== true) return applied ? 'release' : 'skip';
  // An ordinary user's server context is the OWNER's editor, not this client's
  // machine (the strip substitutes family.localContext for them) — there is
  // nothing to follow, and following it would load a stranger's duty.
  if (ctx.can_write !== true) return 'skip';
  if (!ctx.die || !ctx.config) return 'skip';
  if (!applied) return 'seed';
  const newer = !!(ctx.at && applied.at) && String(ctx.at) > String(applied.at);
  const older = !!(ctx.at && applied.at) && String(ctx.at) < String(applied.at);
  if (older) return 'skip';                      // never adopt backwards
  const changed = ctx.die !== applied.die
    || ctx.config !== applied.config
    || (ctx.duty ?? null) !== (applied.duty ?? null);
  if (!changed && !applied.released && !newer) return 'skip';
  // A configuration activated with no duty carries no operating point (the
  // payload's sim block has no current/rpm/γ) — there is nothing to adopt, so
  // just move the baseline and let the next duty selection be the change.
  if (!ctx.duty) return 'seed';
  return 'adopt';
}

// ── "not while a solve is in flight" ─────────────────────────────────────────
// A run's fetch is already out with the panel's numbers; swapping the fields
// under it would leave the charts describing one point and the panel another.
// Adoption is POSTPONED, never dropped — the strip re-checks on its next poll.

const BUSY = new Set<string>();

/** A panel reports its own solve state here (SimulationPanel: `simBusy`). */
export function setSolveBusy(who: string, busy: boolean): void {
  if (busy) BUSY.add(who); else BUSY.delete(who);
}

/** Does THIS browser have a solve of its own in flight? */
export function solveBusyHere(): boolean { return BUSY.size > 0; }

/** …and the server's own verdict, for a run this browser started before a
 *  reload (its `busy` flag went with the page) or one the panel launched
 *  without going through `simBusy` (the field viewers, the animation).
 *  Read-only GETs; a failed poll is read as "busy" so a lost backend can never
 *  be the reason the fields were swapped under a run. */
export async function solverRunning(): Promise<boolean> {
  const paths = ['/api/simulation/physics/fem_transient/progress',
                 '/api/coupled/progress'];
  try {
    const answers = await Promise.all(paths.map(async (p) => {
      const r = await fetch(`${API}${p}`, { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return (await r.json())?.running === true;
    }));
    return answers.some(Boolean);
  } catch { return true; }
}

// ── one apply at a time ──────────────────────────────────────────────────────
// The catalog's ▶ activates the context BEFORE it writes the mark, so a poll
// landing in that window would see "the header moved" and start following the
// duty this very browser is applying.  Both paths raise this guard.

let _applying = 0;

export function beginDutyApply(): void { _applying += 1; }
export function endDutyApply(): void { _applying = Math.max(0, _applying - 1); }
export function dutyApplyInProgress(): boolean { return _applying > 0; }
