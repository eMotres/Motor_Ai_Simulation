/** Resolve a loaded saved selection to a reference the signed-in catalog returned.
 *
 * An explicit reference id keeps the existing id + die binding. When the saved
 * selection has no id, it may bind only to one exact die/config card. This is
 * intentionally independent of geometry-distance matching: a family selection
 * must not borrow a nearby passport from another build.
 */
export interface ConfigureReferenceSelection {
  ref_id: string | null;
  die: string;
  config: string;
}

export interface ConfigureSelectionReference {
  id: string;
  card?: { die?: string; config?: string } | null;
  geo?: {
    numSlots?: number;
    numPoles?: number;
    statorOR_mm?: number;
    magnetHeight_mm?: number;
  };
}

export interface ConfigureLiveGeometry {
  num_slots?: unknown;
  num_poles?: unknown;
  stator_outer_radius?: unknown;
  magnet_height?: unknown;
}

export function resolveConfigureSelectionReference<T extends ConfigureSelectionReference>(
  selection: ConfigureReferenceSelection,
  accessibleCatalogReferences: readonly T[],
  liveGeometry?: ConfigureLiveGeometry | null,
): T | undefined {
  if (!selection || typeof selection.die !== 'string' || !selection.die
      || typeof selection.config !== 'string' || !selection.config
      || !(typeof selection.ref_id === 'string' || selection.ref_id === null)) {
    return undefined;
  }

  const matches = selection.ref_id !== null
    ? accessibleCatalogReferences.filter((reference) =>
      reference.id === `cat:${selection.ref_id}`
      && reference.card?.die === selection.die)
    : accessibleCatalogReferences.filter((reference) =>
      reference.id.startsWith('cat:')
      && reference.card?.die === selection.die
      && reference.card?.config === selection.config);

  if (matches.length !== 1) return undefined;
  const match = matches[0];
  if (selection.ref_id !== null) return match;

  // A null id needs a safe exact-family fallback. The loaded geometry must
  // also match the card's stamped cross-section, so a stale geometry store
  // cannot graft a new card onto another motor's live knobs.
  const geo = match.geo;
  const near = (actual: unknown, expected: unknown, tolerance: number) => {
    const a = Number(actual);
    const b = Number(expected);
    return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= tolerance;
  };
  if (!geo || !liveGeometry
      || Number(liveGeometry.num_slots) !== Number(geo.numSlots)
      || Number(liveGeometry.num_poles) !== Number(geo.numPoles)
      || !Number.isFinite(Number(liveGeometry.num_slots))
      || !Number.isFinite(Number(liveGeometry.num_poles))
      || !near(liveGeometry.stator_outer_radius, geo.statorOR_mm, 0.5)
      || !near(liveGeometry.magnet_height, geo.magnetHeight_mm, 0.3)) {
    return undefined;
  }
  return match;
}
