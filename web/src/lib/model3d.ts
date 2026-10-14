// 3D model menu: the pure helpers behind the viewer (no three.js, no React, no fetch) so node --test can pin them.
//
// One model for everybody (owner 2026-10-13): every signed-in user who may see a motor can view it; the STEP download is for
// NDA accounts and the owner (the server answers 403 otherwise, `can_download` on the list says which).

export type Axis = 'x' | 'y' | 'z';

// What the tab shows (owner 2026-10-13: "all this beauty after the NDA, together with the download; without NDA just a 3D model you
// can rotate").  ONE flag decides, the server's `can_download`; there is no second permission.  Without it: the viewer (orbit, zoom,
// pan), the motor picker, the fit button, the "Download after NDA" note and the Sign button - no side panel, no tools.
export type PanelPart = 'viewer' | 'motor' | 'fit' | 'signNote' | 'signButton'
  | 'parts' | 'explode' | 'section' | 'measure' | 'download' | 'yourNda' | 'version';

export function panelParts(canDownload: boolean): PanelPart[] {
  const base: PanelPart[] = ['viewer', 'motor', 'fit'];
  return canDownload
    ? [...base, 'parts', 'explode', 'section', 'measure', 'download', 'yourNda', 'version']
    : [...base, 'signNote', 'signButton'];
}

export interface ViewerSettings {
  hidden: ReadonlySet<string>;
  explode: number;
  section: { on: boolean; axis: Axis; position: number };
  measure: boolean;
}

/** The settings the scene gets: the user's tool state with NDA, the plain model without it (a revoked NDA resets the tools at once). */
export function effectiveSettings(canDownload: boolean, s: ViewerSettings): ViewerSettings {
  return canDownload ? s : { hidden: new Set(), explode: 0, section: { ...s.section, on: false }, measure: false };
}

export interface ModelInfo {
  die: string;
  config: string;
  motor_id: string;
  glb_bytes?: number | null;
  glb_sha256?: string | null;
  step_available?: boolean;
  step_bytes?: number | null;
  version?: { template?: string | null; built_at?: string | null; param_hash?: string };
}

export interface PartGroup {
  /** node name in the GLB, e.g. "Holder_front" */
  name: string;
  /** toggle family, e.g. "PhaseBoard" for PhaseBoard_FR4 / PhaseBoard_copper / PhaseBoard_pins */
  family: string;
  /** axial factor of the exploded view, in motor lengths (+ = propeller side) */
  explode: number;
}

export type Vec3 = [number, number, number];
export interface Box { min: Vec3; max: Vec3 }

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

/** Toggle families, in the order the GLB lists its groups, each with the node names it switches. */
export function togglesOf(groups: readonly PartGroup[]): Array<{ family: string; nodes: string[] }> {
  const out: Array<{ family: string; nodes: string[] }> = [];
  for (const g of groups) {
    let row = out.find((r) => r.family === g.family);
    if (!row) { row = { family: g.family, nodes: [] }; out.push(row); }
    row.nodes.push(g.name);
  }
  return out;
}

/** Visible state of a node: its family switch decides. */
export function isVisible(hidden: ReadonlySet<string>, g: Pick<PartGroup, 'family'>): boolean {
  return !hidden.has(g.family);
}

/** Axial shift (mm) of a group in the exploded view: factor x motor length x slider (0..1). */
export const explodeShift = (factor: number, motorLength: number, slider: number): number =>
  factor * motorLength * Math.max(0, Math.min(1, slider));

/** Clip-plane parameters {normal, constant} for three.Plane: keeps the half-space `axis < position`. */
export function sectionPlane(axis: Axis, position: number): { normal: Vec3; constant: number } {
  const normal: Vec3 = axis === 'x' ? [-1, 0, 0] : axis === 'y' ? [0, -1, 0] : [0, 0, -1];
  return { normal, constant: position };
}

/** Slider range of the section plane: the box extent along the axis, padded by 0.5 %. */
export function sectionRange(box: Box, axis: Axis): { min: number; max: number } {
  const i = axis === 'x' ? 0 : axis === 'y' ? 1 : 2;
  const pad = 0.005 * (box.max[i] - box.min[i]);
  return { min: box.min[i] - pad, max: box.max[i] + pad };
}

export const distance = (a: Vec3, b: Vec3): number => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/** "12.34 mm" with the precision a CAD user expects (3 significant decimals under 100 mm). */
export const formatMm = (v: number): string => `${Math.abs(v) < 100 ? v.toFixed(3) : v.toFixed(2)} mm`;

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

/** Axis-aligned box of a list of points. */
export function boxOf(points: readonly Vec3[]): Box {
  const min: Vec3 = [Infinity, Infinity, Infinity];
  const max: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (const p of points) for (let i = 0; i < 3; i++) { min[i] = Math.min(min[i], p[i]); max[i] = Math.max(max[i], p[i]); }
  return { min, max };
}
