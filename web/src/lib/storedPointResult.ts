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
 */
import { isNotComputed } from '../modules/development/pointsLimited';
import type { FieldKind, StoredField } from '../modules/development/types';
import { fetchMotorField } from './motorViewApi';
import { pointOfKey, storedVerdict } from './resultOwner';

export type StoredKind = Extract<FieldKind, 'thermal' | 'rotor_stress'>;

export type StoredPoint =
  /** no catalogue point is loaded (or nothing asked yet): there is nothing to look up */
  | { key: string | null; status: 'idle' }
  | { key: string; status: 'loading' }
  /** the point has no stored result of this kind */
  | { key: string; status: 'none' }
  /** the point has one, but it was solved on another build of the machine than the one loaded now */
  | { key: string; status: 'older_build'; computedAt: string | null }
  /** the point has one that cannot be shown to belong to the loaded machine (no fingerprint on either side) */
  | { key: string; status: 'unproven'; computedAt: string | null }
  | { key: string; status: 'error'; error: string }
  | { key: string; status: 'ready'; field: StoredField };

export const IDLE_STORED: StoredPoint = { key: null, status: 'idle' };

/**
 * Look the point's stored result up and judge it against the machine loaded now.  `liveFingerprint` is the server's
 * fingerprint of that machine (the `/last` route's `live_geometry_fingerprint`); it is asked only when a file exists.
 */
export async function loadStoredPoint(
  key: string, kind: StoredKind, liveFingerprint: () => Promise<string | null>,
): Promise<StoredPoint> {
  const point = pointOfKey(key);
  if (!point) return { key, status: 'error', error: 'no catalogue point is loaded' };
  let field: StoredField;
  try {
    field = await fetchMotorField(point.die, point.config, point.duty, kind);
  } catch (e) {
    if (isNotComputed(e)) return { key, status: 'none' };
    return { key, status: 'error', error: e instanceof Error ? e.message : String(e) };
  }
  let live: string | null = null;
  try { live = await liveFingerprint(); } catch { live = null; }
  const verdict = storedVerdict(field.provenance?.geometry_fingerprint, live);
  const computedAt = field.provenance?.computed_at ?? null;
  if (verdict === 'older_build') return { key, status: 'older_build', computedAt };
  if (verdict === 'unproven') return { key, status: 'unproven', computedAt };
  return { key, status: 'ready', field };
}

/** One sentence for the line above a stored map (or in place of it) - the same words on both tabs. */
export function storedPointLine(s: StoredPoint, what: 'thermal' | 'mechanical'): string | null {
  switch (s.status) {
    case 'loading': return 'looking up this point\'s stored result…';
    case 'none': return `nothing is computed for this point yet - press Solve (${what})`;
    case 'older_build': return 'this point\'s stored result was solved on an older build of the machine - press Solve to compute it for this one';
    case 'unproven': return 'this point\'s stored result cannot be matched to the machine loaded now - press Solve to compute it for this one';
    case 'error': return `this point's stored result could not be read - ${s.error}`;
    default: return null;
  }
}
