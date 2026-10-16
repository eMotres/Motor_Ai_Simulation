// 3D model menu: the pure helpers behind the viewer (no three.js, no React, no fetch) so node --test can pin them.
//
// One model for everybody (owner 2026-10-13): every signed-in user who may see a motor can view it; the STEP download is for
// NDA accounts and the owner (the server answers 403 otherwise, `can_download` on the list says which).

export type ModelView = 'full' | 'simplified' | 'none';

/** The view this caller gets of `m`: the server's `view`; an older server that sends none: the NDA flag decides. */
export function viewOf(m: Pick<ModelInfo, 'view'> | null | undefined, canDownload = false): ModelView {
  if (m?.view === 'full' || m?.view === 'simplified' || m?.view === 'none') return m.view;
  return canDownload ? 'full' : 'simplified';
}

/** The GLB route the viewer loads for `m` (below /api/model3d/{die}/{config}): the full model only for view 'full'; 'none' loads nothing. */
export function glbRoute(m: Pick<ModelInfo, 'view'> | null | undefined, canDownload = false): string | null {
  const v = viewOf(m, canDownload);
  return v === 'full' ? 'model.glb' : v === 'simplified' ? 'simplified_view.glb' : null;
}

/** The full-model tools (Motor Assembly tree, explode, section, measure, part hints, per-part switches) exist ONLY for the full view. */
export const fullToolsOpen = (view: ModelView): boolean => view === 'full';

export interface ModelInfo {
  die: string;
  config: string;
  motor_id: string;
  glb_bytes?: number | null;
  glb_sha256?: string | null;
  /** 'full' | 'frameless' (the CAD assembly variant; a frameless kit has no housing, holders, bearings, shaft, screws) */
  assembly?: string;
  step_available?: boolean;
  step_bytes?: number | null;
  /** the SIMPLIFIED shell model exists (downloadable by every signed-in user, no NDA; owner 2026-10-15) */
  simplified_available?: boolean;
  simplified_step_bytes?: number | null;
  simplified_glb_bytes?: number | null;
  simplified_glb_sha256?: string | null;
  /** WHAT THIS CALLER SEES (server, owner 2026-10-15): 'full' (NDA + verification, owner / admin), 'simplified' (everybody else), 'none' (simplified not built:
   *  never a fallback to full) */
  view?: 'full' | 'simplified' | 'none';
  view_reason?: string;
  version?: { template?: string | null; built_at?: string | null; param_hash?: string };
}

export interface PartGroup {
  /** node name in the GLB, e.g. "Holder_front" */
  name: string;
  /** toggle family, e.g. "PhaseBoard" for PhaseBoard_FR4 / PhaseBoard_copper / PhaseBoard_pins */
  family: string;
  /** axial factor of the exploded view, in motor lengths (+ = propeller side) */
  explode: number;
  /** names of the nodes inside the group (Coil_01 ... Coil_12): the per-instance rows of the assembly tree */
  children?: string[];
  /** the colour the group is drawn with (#rrggbb) - the swatch of its tree row */
  colour?: string;
  explodeDirection?: [number, number, number];
}

/** "<die> / <config>" key of a model, also the picker value. */
export const modelKey = (m: Pick<ModelInfo, 'die' | 'config'>): string => `${m.die}\u0000${m.config}`;

export const modelLabel = (m: Pick<ModelInfo, 'die' | 'config'>): string => `${m.die} · ${m.config}`;

/** Which model the picker opens on: the remembered one when it is still offered, else the first. */
export function pickModel<T extends Pick<ModelInfo, 'die' | 'config'>>(models: readonly T[], remembered?: string | null): T | null {
  if (!models.length) return null;
  return models.find((m) => modelKey(m) === remembered) ?? models[0];
}

/** What the tab opens on.  `wanted` is the motor the person has open in Configure (die + configuration): its model when one is
 *  built, else NO model and `preparing: true` (the tab says so in one line instead of showing some other motor's model or a blank
 *  canvas).  With no motor chosen it is the remembered model, else the first. */
export function resolveModel<T extends Pick<ModelInfo, 'die' | 'config'>>(
  models: readonly T[], remembered?: string | null, wanted?: Pick<ModelInfo, 'die' | 'config'> | null,
): { model: T | null; preparing: boolean } {
  if (wanted?.die && wanted?.config) {
    const hit = models.find((m) => modelKey(m) === modelKey(wanted));
    return hit ? { model: hit, preparing: false } : { model: null, preparing: models.length > 0 };
  }
  return { model: pickModel(models, remembered), preparing: false };
}


/** Size of a download, "4.6 MB". */
export function formatBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} kB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** File name from a Content-Disposition header (the server stamps customer + date into it); a fallback when absent. */
export function filenameFromDisposition(header: string | null | undefined, fallback: string): string {
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header ?? '');
  const name = m ? m[1].trim() : '';
  return name && !/[\\/]/.test(name) ? name : fallback;
}
