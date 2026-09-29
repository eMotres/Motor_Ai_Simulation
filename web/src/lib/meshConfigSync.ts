/**
 * Adopt the SERVER's mesh block into the browser's `mesh.*` keys at app boot.
 *
 * WHY (2026-09-07, twice in one day): every solve request in this app — the
 * Simulation transient, the field views, the sweep / DOE / descent panels, the
 * Thermal tab before it was fixed — builds its mesh half from `localStorage`
 * `mesh.<key>` with a constant fallback (`nSectors` → 1 or −1 = the FULL ring).
 * The Mesh tab writes those keys only while it is MOUNTED, and the server's
 * `mesh:` block (config/motor_config.yaml, `/api/mesh/config`) is the source of
 * truth it persists to.  A browser that has not opened the Mesh tab since its
 * storage was wiped — a new profile, a cleared site, the in-app pane after a
 * restart — therefore solves on the fallback: the user's 24-point sweep ran the
 * full ring at 48 steps (n_sectors −1, "10 of 10 ran out of time") while the
 * Mesh tab, had it been opened, would have shown ½.
 *
 * This runs ONCE at boot: it reads the server block and writes the five keys the
 * readers use.  It never writes the server — the Mesh tab's save contract
 * (meshSaveContract.ts) owns that direction, with its dirty flags — and it
 * writes nothing when the server cannot be reached, so an offline page keeps
 * whatever it had.
 */
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

/** server key → browser key, exactly the pairs the Mesh tab persists. */
const KEYS: ReadonlyArray<readonly [server: string, local: string]> = [
  ['mesh_size_mm', 'meshSize'],
  ['min_size_mm', 'minSize'],
  ['outer_air_factor', 'outerAir'],
  ['gap_layers', 'gapLayers'],
  ['n_sectors', 'nSectors'],
];

export interface MeshSyncResult {
  adopted: Record<string, number>;
  /** keys whose browser value differed from the server's (or was missing) */
  changed: string[];
}

/** Pure: which keys to write, given the server block and the browser's current
 *  values.  Exported for the node test. */
export function planMeshSync(
  server: Record<string, unknown>,
  local: Record<string, string | null>,
): MeshSyncResult {
  const adopted: Record<string, number> = {};
  const changed: string[] = [];
  for (const [sk, lk] of KEYS) {
    const v = Number(server?.[sk]);
    if (!Number.isFinite(v)) continue;            // the server did not say — leave the key alone
    adopted[lk] = v;
    let cur: number | null = null;
    try { const raw = local[lk]; cur = raw == null ? null : Number(JSON.parse(raw)); } catch { cur = null; }
    if (cur === null || !Number.isFinite(cur) || Math.abs(cur - v) > 1e-9) changed.push(lk);
  }
  return { adopted, changed };
}

/** Read the server block and write the browser keys.  Resolves to what was
 *  adopted; never throws. */
export async function syncMeshConfigFromServer(): Promise<MeshSyncResult | null> {
  try {
    const r = await fetch(`${API.replace(/\/$/, '')}/api/mesh/config`, { cache: 'no-store' });
    if (!r.ok) return null;
    const server = await r.json() as Record<string, unknown>;
    const local: Record<string, string | null> = {};
    for (const [, lk] of KEYS) {
      try { local[lk] = localStorage.getItem(`mesh.${lk}`); } catch { local[lk] = null; }
    }
    const plan = planMeshSync(server, local);
    for (const lk of plan.changed) {
      // Said, not swallowed: a write that fails here (a full store — the run
      // cache alone can be tens of MB) leaves the browser solving on a stale
      // symmetry, which is the very failure this module exists to prevent.
      try { localStorage.setItem(`mesh.${lk}`, JSON.stringify(plan.adopted[lk])); }
      catch (e) { console.warn('[meshConfigSync] could not write mesh.' + lk, e); }
    }
    if (plan.changed.length) {
      // Let a mounted reader (the Mesh tab's persisted fields, the Thermal
      // tab's context line) re-read — the same event a duty restore fires.
      try { window.dispatchEvent(new CustomEvent('mesh-config-synced', { detail: plan })); } catch { /* not in a browser */ }
    }
    return plan;
  } catch {
    return null;
  }
}

/* Boot ONLY — deliberately not on `sim-settings-restored`: a duty restore
   writes the duty's own mesh keys and fires that event, and a server sync
   right after would overwrite the duty's snapshot with the live block. */
