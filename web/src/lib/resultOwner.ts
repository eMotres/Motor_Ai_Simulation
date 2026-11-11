/**
 * WHOSE result is this? — the one rule the Thermal and Mechanical tabs use before they draw anything.
 *
 * Owner 2026-11-04: «why does thermal show the picture of a 400 mm motor when I loaded the 200 mm one? check the mechanics
 * too».  Both tabs restored "the last result" from a store that holds ONE slot per workspace — the last solve of ANY motor —
 * and drew it dimmed under "result is for a previous geometry".  The rule now (agreed with the owner): a result is drawn
 * only when it is the loaded point's own.  Another motor's result is not drawn at all, not even dimmed with a warning.
 *
 * TWO WITNESSES, because either alone has failed in this app:
 *   1. the geometry — the backend's fingerprint verdict for a restored answer (`stale_geometry`), this client's own stamp
 *      (`geoSig`, compared with `geoSigsDiffer`, never as strings) for a fresh solve;
 *   2. the point's identity — die / configuration / duty, stamped on the answer when it was solved (the backend files the
 *      `/last` entry and the stored point field under the same identity) and compared with the point the editor has loaded.
 *
 * This module imports only `geoSigsDiffer` (itself import-free), so a node test can drive every rule below.
 */
import { geoSigsDiffer } from '../components/common/geoSig.ts';

/** The catalogue point a result belongs to (`dutySettings.activeDuty()` has this shape). */
export interface PointId { die: string; config: string; duty: string }

const SEP = '\u001f';   // unit separator - cannot occur in a catalogue name

/** Key of a point, or `null` when any part is missing (no catalogue point is loaded). */
export function pointKeyOf(p: Partial<PointId> | null | undefined): string | null {
  if (!p || !p.die || !p.config || !p.duty) return null;
  return `${p.die}${SEP}${p.config}${SEP}${p.duty}`;
}

/** Same key from the `{die, config, duty}` object the backend stamps on `/last` entries. */
export const pointKeyOfContext = (c: unknown): string | null =>
  (c && typeof c === 'object') ? pointKeyOf(c as Partial<PointId>) : null;

/** A point key back into its parts (for the stored-result fetch and the labels). */
export function pointOfKey(key: string | null | undefined): PointId | null {
  if (!key) return null;
  const [die, config, duty] = key.split(SEP);
  return die && config && duty ? { die, config, duty } : null;
}

/** What a `/last` entry says about itself - the fields of `LastEntry` this rule reads. */
export interface RestoredEntry {
  stale_geometry: boolean | null;
  context?: { die?: string; config?: string; duty?: string } | null;
}

/**
 * May the backend's `/last` entry be adopted as THE LOADED MOTOR's result?
 *
 *   • geometry: only a PROVEN match (`stale_geometry === false`).  `true` is another machine; `null` is UNKNOWN, and an
 *     unknown is not a licence to draw (the old code drew both, dimmed).
 *   • identity: with a catalogue point loaded, the entry must carry the same point.  An entry with no stamp (stored before
 *     the stamp existed, or solved with no catalogue point) proves nothing about the point and is not adopted - the point's
 *     own stored result is shown instead.  With NO point loaded (a free-form machine, a copy) there is nothing to compare
 *     and the geometry alone decides.
 */
export function mayAdopt(entry: RestoredEntry | null | undefined, loaded: string | null): boolean {
  if (!entry) return false;
  if (entry.stale_geometry !== false) return false;
  if (loaded === null) return true;
  return pointKeyOfContext(entry.context) === loaded;
}

/** What a shown result carries about its own origin (the `Slice` fields of both stores). */
export interface Origin {
  /** the point loaded when it was solved / adopted; `null` = none was loaded */
  point: string | null;
  /** this client's geometry stamp at that moment; `null` = unknown */
  geoSig: string | null;
}

export type Standing =
  /** the loaded point's own result - drawn */
  | 'mine'
  /** the loaded point's own result, but the geometry was edited after it was solved - drawn with the stale note (the
   *  owner's 2026-09-06 rule: an edit made by the user is flagged, not erased) */
  | 'edited'
  /** another point's result - never drawn */
  | 'foreign';

/**
 * Where does a result stand against the point and the geometry loaded NOW?
 *
 *   • two known points that differ → `foreign` (a different duty of the same die is a different operating point);
 *   • one side known and the other not → the identity cannot be shown to match: `foreign` unless the geometry stamps
 *     are both known and equal;
 *   • otherwise (same point, or neither side known) → the geometry decides: a differing stamp is `edited`, anything else
 *     `mine`.  An UNKNOWN stamp never claims a mismatch (the rule `geoStaleAgainstLive` has always followed).
 */
export function standingOf(origin: Origin, live: { point: string | null; geoSig: string }): Standing {
  const geoDiffers = !!origin.geoSig && !!live.geoSig && geoSigsDiffer(origin.geoSig, live.geoSig);
  const geoEqual = !!origin.geoSig && !!live.geoSig && !geoSigsDiffer(origin.geoSig, live.geoSig);
  if (origin.point !== null && live.point !== null) {
    if (origin.point !== live.point) return 'foreign';
    return geoDiffers ? 'edited' : 'mine';
  }
  if (origin.point !== live.point) return geoEqual ? 'mine' : 'foreign';
  return geoDiffers ? 'edited' : 'mine';
}

/** The verdict on a point's STORED field file (`/api/motor-view/.../field/<kind>`), against the machine loaded now. */
export type StoredVerdict = 'draw' | 'older_build' | 'unproven';

/**
 * Is the stored field of the loaded point still of the machine on screen?  `stored` / `live` are the SERVER's fingerprints
 * (the file's `geometry_fingerprint` and the `live_geometry_fingerprint` the `/last` route reports for this request) - the
 * server's own dialect, compared by the server's own rule (equality).  Missing on either side is unproven, not a pass.
 */
export function storedVerdict(stored: string | null | undefined, live: string | null | undefined): StoredVerdict {
  if (!stored || !live || live === 'nofp') return 'unproven';
  return stored === live ? 'draw' : 'older_build';
}

/** What a stored point result is judged on (all optional: a missing witness is unknown, never a pass). */
export interface StoredWitnesses {
  /** the server's verdict on the point's BUILD STAMP (`family.stored_build_stale`): true = solved on another build of
   *  the configuration, false = on its current build (every spelling - the #640 alias rule), null/undefined = unknown */
  buildStale?: boolean | null;
  /** the configuration's own geometry, as `geoSignature` stamps it, and the machine on screen now (`liveGeoSig`) */
  configGeoSig?: string | null;
  liveGeoSig?: string | null;
  /** the geometry fingerprints: the stored file's and the server's live one (the fallback witness) */
  storedFingerprint?: string | null;
  liveFingerprint?: string | null;
}

export type StoredPointVerdict = StoredVerdict | 'edited';

/**
 * May the loaded CATALOG point's stored result be drawn for the machine on screen?  (owner 2026-11-11: «a catalog motor is
 * already computed» - loading it shows its stored results at once, Thermal and Mechanical included.)
 *
 *   1. the build stamp decides first - the rule the Motors row's amber chip uses, so a tab never calls "older build" what
 *      the row calls current.  The geometry fingerprint is a hash of the whole geometry dict and moved for unchanged
 *      machines whenever a field was added to it: on 2026-11-11 every 40 mm point read "older build" on Thermal while its
 *      row was current.
 *   2. a current stamp says the result is the CONFIGURATION's; the machine on screen must still be that configuration - a
 *      geometry edited after the load is `edited` (compared by value, `geoSigsDiffer`; an unknown side is not an edit).
 *   3. no stamp: the old fingerprint rule (`storedVerdict`).
 */
export function storedPointVerdict(w: StoredWitnesses): StoredPointVerdict {
  if (w.buildStale === true) return 'older_build';
  if (w.buildStale === false) {
    return (w.configGeoSig && w.liveGeoSig && geoSigsDiffer(w.configGeoSig, w.liveGeoSig)) ? 'edited' : 'draw';
  }
  return storedVerdict(w.storedFingerprint, w.liveFingerprint);
}
