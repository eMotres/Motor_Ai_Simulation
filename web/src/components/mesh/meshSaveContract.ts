/**
 * Mesh-settings load/save contract — pure functions, no React, no fetch, so the
 * rule below can be unit-tested without a browser.
 *
 * WHY this exists (user, 2026-09-07: "захожу в Mesh и опять не сохранено то,
 * что было до этого — там точно стояло 1/2; почему параметры опять не
 * сохраняются?"):
 *
 *   09:0x  config/motor_config.yaml holds mesh.n_sectors: 2 (the user's 1/2).
 *   09:06:45 the API restarts while the app is open.
 *   09:2x  GET /api/mesh/config answers n_sectors: 1, outer_air_factor: 1.3 —
 *          i.e. exactly the panel's CONSTANT defaults ("Default symmetry is
 *          Full").  The browser pane had just lost its localStorage (the Claude
 *          app recreates the in-app pane with empty storage), so the panel
 *          seeded from constants, its mount fetch hit the restarting API, and a
 *          save path that was NOT gated on the config load PATCHed those
 *          constants into the server config.
 *   09:25:41 the user re-set 1/2 by hand and the file was rewritten with 2.
 *
 * The rule that follows from it: a constant default must NEVER reach the
 * server.  Only a setting the user changed in THIS session may be written, and
 * only after the server config has actually been read.
 */

/** The mesh settings that live in config/motor_config.yaml (`mesh:` block). */
export const MESH_CONFIG_KEYS = [
  'mesh_size_mm', 'min_size_mm', 'outer_air_factor', 'gap_layers', 'n_sectors',
] as const;

export type MeshConfigKey = (typeof MESH_CONFIG_KEYS)[number];
export type MeshSettings = Record<MeshConfigKey, number>;

export type MeshSaveReason =
  | 'config-unknown'        // server config not read yet → saving would guess
  | 'nothing-user-changed'  // no per-setting dirty flag → nothing to persist
  | 'ok';

export interface MeshSaveDecision {
  save: boolean;
  reason: MeshSaveReason;
  /** PATCH body: ONLY the keys the user changed in this session. */
  patch: Partial<MeshSettings>;
}

/**
 * Decide whether (and what) to PATCH to /api/mesh/config.
 *
 * `dirty` carries one flag per setting the USER moved in this session; keys the
 * user never touched are left out of the body entirely.  The backend PATCH
 * merges partial bodies (`updates = {k: v for … if v is not None}`), so an
 * omitted key keeps whatever the server holds — the only shape that cannot
 * clobber a value this browser never learned.
 */
export function decideMeshSave(
  state: MeshSettings,
  dirty: Iterable<string>,
  serverLoaded: boolean,
): MeshSaveDecision {
  if (!serverLoaded) return { save: false, reason: 'config-unknown', patch: {} };
  const flagged = new Set<string>(dirty);
  const patch: Partial<MeshSettings> = {};
  for (const k of MESH_CONFIG_KEYS) {
    if (!flagged.has(k)) continue;
    const v = state[k];
    if (typeof v === 'number' && Number.isFinite(v)) patch[k] = v;
  }
  if (Object.keys(patch).length === 0) {
    return { save: false, reason: 'nothing-user-changed', patch: {} };
  }
  return { save: true, reason: 'ok', patch };
}

/** Air-gap rows per side actually offered by the slider (1…6). */
export const GAP_LAYERS_MIN = 1;
export const GAP_LAYERS_MAX = 6;

/**
 * Parse the server payload into the settings the panel adopts.
 *
 * Only finite numbers are taken; anything else is ignored so a half-written or
 * error payload cannot poison the panel.  gap_layers is clamped to the range
 * the slider actually offers — the old clamp was 1…3 while the slider goes to
 * 6, so a saved 4 came back as 3 on the next load: another silent "it did not
 * save".
 */
export function adoptMeshConfig(raw: unknown): Partial<MeshSettings> {
  const d = (raw ?? {}) as Record<string, unknown>;
  const out: Partial<MeshSettings> = {};
  const num = (k: string): number | null => {
    const v = d[k];
    return typeof v === 'number' && Number.isFinite(v) ? v : null;
  };
  const ms = num('mesh_size_mm');         if (ms !== null) out.mesh_size_mm = ms;
  const mn = num('min_size_mm');          if (mn !== null) out.min_size_mm = mn;
  const oa = num('outer_air_factor');     if (oa !== null) out.outer_air_factor = oa;
  const gl = num('gap_layers');
  if (gl !== null) {
    out.gap_layers = Math.min(GAP_LAYERS_MAX, Math.max(GAP_LAYERS_MIN, Math.round(gl)));
  }
  const ns = num('n_sectors');            if (ns !== null) out.n_sectors = ns;
  return out;
}

/**
 * Backoff for the mount config fetch: 1, 2, 4, 8, 16, 32 s, then a 60 s ceiling.
 * The API can be down for a minute (a restart at 09:06:45 is what started the
 * incident above), and the panel must keep waiting — with saves blocked — until
 * it answers, instead of falling back to constants.
 */
export function configRetryDelayMs(attempt: number): number {
  const a = Math.max(0, Math.floor(attempt));
  return Math.min(60_000, 1000 * 2 ** Math.min(a, 6));
}
