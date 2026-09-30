/**
 * Owner 2026-09-30: when the rotor- and stator-side Coulomb rings of a run
 * disagree, the solver steps the air-gap layers up one per side until they
 * agree, and the level that PASSED becomes the machine's default from then on.
 *
 * The browser half of that: write the level into `mesh.gapLayers` (with the
 * one-line note in `mesh.gapLayersNote`) so the next runs send it, and so the
 * duty save — which carries every `mesh.*` key verbatim — stores it on the
 * active duty.  For a champion re-check (solved on a geometry override, so the
 * server did not touch the live config) `patchServer` also writes the server
 * mesh config, exactly as a Mesh-tab slider change would.
 */
const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export interface GapAdoptSource {
  gap_refinement?: { persist_gap_layers?: number | null } | null;
  gap_layers_persist?: number | null;
  gap_layers_note?: string | null;
}

/** The passing level a run asks to make the default, or null. */
export function gapLayersToAdopt(src: GapAdoptSource | null | undefined): number | null {
  const v = src?.gap_refinement?.persist_gap_layers ?? src?.gap_layers_persist ?? null;
  return typeof v === 'number' && Number.isFinite(v) && v >= 1 ? v : null;
}

export function adoptGapLayers(src: GapAdoptSource | null | undefined,
                               opts: { patchServer?: boolean } = {}): number | null {
  const level = gapLayersToAdopt(src);
  if (level == null) return null;
  try {
    localStorage.setItem('mesh.gapLayers', JSON.stringify(level));
    if (src?.gap_layers_note) {
      localStorage.setItem('mesh.gapLayersNote', JSON.stringify(src.gap_layers_note));
    }
  } catch { /* storage is a convenience */ }
  if (opts.patchServer) {
    void fetch(`${API}/api/mesh/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ gap_layers: level }),
    }).catch(() => { /* the duty save still carries mesh.gapLayers */ });
  }
  return level;
}
