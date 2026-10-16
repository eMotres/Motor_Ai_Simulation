/**
 * The Mesh-tab settings a run request carries — owner 2026-09-30: mesh
 * settings come ONLY from the Mesh tab (the saved duty's `mesh.*`).
 *
 * Only the keys this browser actually holds are sent.  A key it does not hold
 * is OMITTED (never a browser-side default), and the backend then uses the
 * machine's saved Mesh settings (`mesh_settings.py`), falling back to a
 * labelled last resort only if the machine has none — the result says so.
 */
const KEYS: Array<[string, string]> = [
  ['meshSize', 'mesh_size_mm'], ['minSize', 'min_size_mm'],
  ['outerAir', 'outer_air_factor'], ['gapLayers', 'gap_layers'],
  ['nSectors', 'n_sectors'],
];

function stored(key: string): number | undefined {
  try {
    const raw = localStorage.getItem(`mesh.${key}`);
    if (raw == null) return undefined;
    const v = Number(JSON.parse(raw));
    return Number.isFinite(v) ? v : undefined;
  } catch { return undefined; }
}

/** The stored Mesh-tab numbers, by request name; missing keys are absent. */
export function storedMeshFields(): Partial<Record<string, number>> {
  const out: Partial<Record<string, number>> = {};
  for (const [ls, api] of KEYS) {
    const v = stored(ls);
    if (v !== undefined) out[api] = v;
  }
  return out;
}

/** The same, as query-string values (for URLSearchParams requests). */
export function storedMeshQuery(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(storedMeshFields())) {
    if (v !== undefined) out[k] = String(v);
  }
  return out;
}

/** Gap layers per side as stored, or undefined (= the machine's setting). */
export function storedGapLayers(): number | undefined {
  return stored('gapLayers');
}
