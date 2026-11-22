/**
 * Rules for mirroring the Sweep / Optimization variable set between this browser and the server copy
 * (owner bug 2026-11-19: after leaving the page for the admin pages the panel showed 3 of 5 variables).
 *
 * Two rules, both pure so `node --test` loads this file directly:
 *
 * 1. An automatic store change that does not change the CONTENT (the panel re-applying the Simulation tab's operating
 *    point on every mount, the schema init, a re-hydrate) is not an edit: it is neither stamped "now" nor pushed.  A
 *    stale tab used to win the "newest copy wins" rule just by being opened.
 * 2. A push is a compare-and-swap decided BY THE SERVER (review 2026-11-21): the push states the server-issued revision this
 *    browser last saw, and the server refuses (409) when the stored copy has moved on.  The refused local copy is kept
 *    (`sweepKeepConflictCopy`), the stored copy is adopted, and nothing is written back.  `mayPush` is the older
 *    client-side timestamp check and no longer guards the push.
 */

export interface SweepContent {
  variations?: unknown;
  operatingPoints?: unknown;
  rippleThreshold?: unknown;
  ratedTorqueNm?: unknown;
  vBusV?: unknown;
  modulation?: unknown;
}

/** The part of a sweep config a user edits (everything but the edit stamp), as a stable string. */
export function sweepContentKey(c: SweepContent | null | undefined): string {
  const o = c ?? {};
  const norm = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(norm);
    if (v && typeof v === 'object') {
      return Object.fromEntries(Object.keys(v as object).sort().map((k) => [k, norm((v as Record<string, unknown>)[k])]));
    }
    return v;
  };
  return JSON.stringify(norm({
    variations: o.variations, operatingPoints: o.operatingPoints, rippleThreshold: o.rippleThreshold,
    ratedTorqueNm: o.ratedTorqueNm, vBusV: o.vBusV, modulation: o.modulation,
  }));
}

/** Is this change a real edit (content differs from the last content this browser knew to be current)? */
export function isRealEdit(lastKey: string | null, next: SweepContent | null | undefined): boolean {
  return lastKey === null || lastKey !== sweepContentKey(next);
}

/** May this browser overwrite the server copy?  Not when the server copy is newer than the one this browser last saw. */
export function mayPush(baseAt: number, serverAt: number): boolean {
  const b = Number(baseAt) || 0, s = Number(serverAt) || 0;
  return s === 0 || b >= s;
}

/** Did the server refuse a second scan because one is already running (HTTP 409)?  The panel then attaches to the running scan. */
export function isScanAlreadyRunning(status: number, body: string): boolean {
  return status === 409 && /already running/i.test(String(body ?? ''));
}

/** The PUT address of the sweep config: ``expected_revision`` is stated whenever the revision this browser saw is known. */
export function sweepPutUrl(base: string, revision: number | null | undefined): string {
  const url = `${base}/api/sweep/config`;
  return revision === null || revision === undefined || !Number.isFinite(Number(revision))
    ? url : `${url}?expected_revision=${Number(revision)}`;
}

export const SWEEP_CONFLICT_KEY = 'motor-sweep-conflict-copy';

/** Keep the local copy a refused push would have lost, so the user can get it back (localStorage; never throws). */
export function sweepKeepConflictCopy(cfg: unknown, now: number = Date.now()): void {
  try {
    const ls = (globalThis as { localStorage?: Storage }).localStorage;
    ls?.setItem(SWEEP_CONFLICT_KEY, JSON.stringify({ at: now, config: cfg }));
  } catch { /* storage blocked: nothing more to do */ }
}
