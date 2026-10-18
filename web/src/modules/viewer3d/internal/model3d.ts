export type Axis = 'x' | 'y' | 'z';
export type StandardView = 'front' | 'back' | 'left' | 'right' | 'top' | 'bottom'
  | 'frontTop' | 'frontBottom' | 'backTop' | 'backBottom' | 'leftTop' | 'leftBottom' | 'rightTop' | 'rightBottom'
  | 'frontLeft' | 'frontRight' | 'backLeft' | 'backRight';
export type Vec3 = [number, number, number];
export interface Box { min: Vec3; max: Vec3 }
export interface PartGroup {
  name: string;
  family: string;
  explode: number;
  children?: string[];
  colour?: string;
  explodeDirection?: Vec3;
}
export interface ViewerSettings {
  hidden: ReadonlySet<string>;
  explode: number;
  section: { on: boolean; axis: Axis; position: number };
  measure: boolean;
  hints?: boolean;
  /** feature-edge outlines; kept in the simplified view too (a display style, not a tool) */
  edges?: boolean;
}
export const effectiveSettings = (full: boolean, s: ViewerSettings): ViewerSettings => full
  ? { ...s, hints: true }
  : { hidden: new Set(), explode: 0, section: { ...s.section, on: false }, measure: false, hints: false, edges: s.edges ?? true };
export const explodeShift = (factor: number, length: number, slider: number): number =>
  factor * length * Math.max(0, Math.min(1, slider));
export function standardViewDirection(view: StandardView): Vec3 {
  const c: Record<StandardView, Vec3> = {
    front:[0,-1,0],back:[0,1,0],left:[-1,0,0],right:[1,0,0],top:[0,0,1],bottom:[0,0,-1],
    frontTop:[0,-1,1],frontBottom:[0,-1,-1],backTop:[0,1,1],backBottom:[0,1,-1],leftTop:[-1,0,1],leftBottom:[-1,0,-1],
    rightTop:[1,0,1],rightBottom:[1,0,-1],frontLeft:[-1,-1,0],frontRight:[1,-1,0],backLeft:[-1,1,0],backRight:[1,1,0],
  };
  const v = c[view], n = Math.hypot(...v);
  return [v[0]/n,v[1]/n,v[2]/n];
}
export function sectionPlane(axis: Axis, position: number): { normal: Vec3; constant: number } {
  return { normal: axis === 'x' ? [-1,0,0] : axis === 'y' ? [0,-1,0] : [0,0,-1], constant: position };
}
export function sectionRange(box: Box, axis: Axis): { min: number; max: number } {
  const i = axis === 'x' ? 0 : axis === 'y' ? 1 : 2, pad = 0.005 * (box.max[i] - box.min[i]);
  return { min: box.min[i] - pad, max: box.max[i] + pad };
}
export const distance = (a: Vec3, b: Vec3): number => Math.hypot(a[0]-b[0], a[1]-b[1], a[2]-b[2]);
export const formatMm = (v: number): string => `${Math.abs(v) < 100 ? v.toFixed(3) : v.toFixed(2)} mm`;
