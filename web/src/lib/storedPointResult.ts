/**
 * The loaded point's OWN stored result, for the Thermal and Mechanical tabs.
 *
 * Every solve files its map under `<die>/runs/<configuration>/<duty>/fields/<kind>.npz` (`duty_fields`), and the read-only
 * route `/api/motor-view/<die>/<config>/<duty>/field/<kind>` serves it shaped like the tab payload.  That file is what a tab
 * shows for a loaded point when it holds no fresh solve of its own - never the workspace's `/last`, which is whichever motor
 * was solved last.  Nothing is solved and nothing is written here.
 *
 * WHAT THE FILE HOLDS, and so what can be drawn from it: the mesh, the per-node temperature (thermal) or the per-element
 * stress / safety factor / displacement (rotor stress), the part outlines and names, and a few scalars of the point.  Not the
 * component table, the heat budget or the per-part stress rows - those live in the full result only, so a stored result is a
 * MAP (and says so), and the tiles come back with the next Solve.
 *
 * …and, for the Thermal tab, the point's stored TEMPERATURES (owner 2026-11-11: «a catalog motor is already computed»): the
 * converged coupled loop's winding / magnet / housing temperatures the Motors view keeps per duty (`thermal.duty_temps`).
 *
 * WHETHER IT IS STILL THIS MACHINE'S (`resultOwner.storedPointVerdict`): the point's build stamp first - the rule the Motors
 * row uses, every spelling of the build (#640) - then "has the geometry been edited since the load"; the geometry
 * fingerprint only when the point carries no stamp.
 */
import { geoSignature } from '../components/common/geoSig';
import { isNotComputed } from '../modules/development/pointsLimited';
import type { FieldKind, MotorView, StoredField, StoredThermal } from '../modules/development/types';
import { fetchConfigGeometry, fetchMotorField, fetchMotorView } from './motorViewApi';
import { pointOfKey, storedPointVerdict } from './resultOwner';
import type { PointId, StoredPointVerdict } from './resultOwner';

export type StoredKind = Extract<FieldKind, 'thermal' | 'rotor_stress'>;

/** The point's stored temperatures (the Motors view's `thermal.duty_temps`). */
export type StoredTemps = NonNullable<StoredThermal['duty_temps']>;

export type StoredPoint =
  /** no catalogue point is loaded (or nothing asked yet): there is nothing to look up */
  | { key: string | null; status: 'idle' }
  | { key: string; status: 'loading' }
  /** the point has no stored map of this kind (its stored temperatures, when it has them, still are shown) */
  | { key: string; status: 'none'; temps?: StoredTemps | null }
  /** the point has one, but it was solved on another build of the machine than the one loaded now */
  | { key: string; status: 'older_build'; computedAt: string | null }
  /** the point's result is of its configuration, but the geometry on screen was edited after the load */
  | { key: string; status: 'edited'; computedAt: string | null }
  /** the point has one that cannot be shown to belong to the loaded machine (no fingerprint on either side) */
  | { key: string; status: 'unproven'; computedAt: string | null }
  | { key: string; status: 'error'; error: string }
  | { key: string; status: 'ready'; field: StoredField; temps?: StoredTemps | null };

export const IDLE_STORED: StoredPoint = { key: null, status: 'idle' };

/** What the lookup needs from the tab: the server's fingerprint of the machine loaded now (the `/last` route's
 *  `live_geometry_fingerprint`, asked only when a file has no build stamp) and this client's stamp of it. */
export interface StoredLookup {
  liveFingerprint: () => Promise<string | null>;
  liveGeoSig?: () => string;
  /** also look the point's stored temperatures up (the Thermal tab) */
  withTemps?: boolean;
}

/** The point's stored temperatures and its build verdict, from the Motors view - or nothing (never throws). */
async function viewDuty(point: PointId): Promise<MotorView['duties'][number] | null> {
  try {
    const v = await fetchMotorView(point.die, point.config);
    return (v?.duties ?? []).find((d) => d.duty === point.duty) ?? null;
  } catch { return null; }
}

/**
 * Look the point's stored result up and judge it against the machine loaded now (`storedPointVerdict`).  Accepts a bare
 * fingerprint reader as the third argument too (the shape the two stores used before the build stamp was a witness).
 */
export async function loadStoredPoint(
  key: string, kind: StoredKind, lookup: StoredLookup | (() => Promise<string | null>),
): Promise<StoredPoint> {
  const opts: StoredLookup = typeof lookup === 'function' ? { liveFingerprint: lookup } : lookup;
  const point = pointOfKey(key);
  if (!point) return { key, status: 'error', error: 'no catalogue point is loaded' };
  const dutyP = opts.withTemps ? viewDuty(point) : Promise.resolve(null);
  let field: StoredField | null = null;
  let fieldErr: string | null = null;
  try {
    field = await fetchMotorField(point.die, point.config, point.duty, kind);
  } catch (e) {
    if (!isNotComputed(e)) fieldErr = e instanceof Error ? e.message : String(e);
  }
  const duty = await dutyP;
  if (fieldErr && !duty?.thermal?.duty_temps) return { key, status: 'error', error: fieldErr };

  // the witnesses - each asked only when the rule needs it
  const buildStale = field?.build_stale ?? duty?.build_stale ?? null;
  let configGeoSig: string | null = null;
  if (buildStale === false && opts.liveGeoSig) {
    try { configGeoSig = geoSignature(await fetchConfigGeometry(point.die, point.config)) || null; }
    catch { configGeoSig = null; }
  }
  const storedFingerprint = field?.provenance?.geometry_fingerprint
    ?? duty?.thermal?.duty_temps?.provenance?.geometry_fingerprint ?? null;
  let liveFingerprint: string | null = null;
  if (buildStale == null && storedFingerprint) {
    try { liveFingerprint = await opts.liveFingerprint(); } catch { liveFingerprint = null; }
  }
  const verdict: StoredPointVerdict = storedPointVerdict({
    buildStale, configGeoSig, liveGeoSig: opts.liveGeoSig?.() ?? null, storedFingerprint, liveFingerprint,
  });
  const computedAt = field?.provenance?.computed_at ?? duty?.thermal?.duty_temps?.provenance?.computed_at ?? null;
  const temps = duty?.thermal?.duty_temps ?? null;
  if (!field && !temps) return { key, status: 'none' };
  if (verdict === 'older_build') return { key, status: 'older_build', computedAt };
  if (verdict === 'edited') return { key, status: 'edited', computedAt };
  if (verdict === 'unproven') return { key, status: 'unproven', computedAt };
  return field ? { key, status: 'ready', field, temps } : { key, status: 'none', temps };
}

/** The point's stored temperatures, when the lookup found them current (any status that carries them). */
export function storedTempsOf(s: StoredPoint): StoredTemps | null {
  return (s.status === 'ready' || s.status === 'none') ? (s.temps ?? null) : null;
}

/** One sentence for the line above a stored map (or in place of it) - the same words on both tabs. */
export function storedPointLine(s: StoredPoint, what: 'thermal' | 'mechanical'): string | null {
  switch (s.status) {
    case 'loading': return 'looking up this point\'s stored result…';
    case 'none': return s.temps ? 'no stored map for this point - press Solve for the map' : `nothing is computed for this point yet - press Solve (${what})`;
    case 'older_build': return 'this point\'s stored result was solved on an older build of the machine - press Solve to compute it for this one';
    case 'edited': return 'the geometry was edited after this point was loaded - its stored result is for the saved build; press Solve for this one';
    case 'unproven': return 'this point\'s stored result cannot be matched to the machine loaded now - press Solve to compute it for this one';
    case 'error': return `this point's stored result could not be read - ${s.error}`;
    default: return null;
  }
}
