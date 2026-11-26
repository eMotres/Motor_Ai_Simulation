// Gear outlines (transverse polygons from the core) -> triangle meshes for the existing 3-D viewer, placed by the pose operation.
// Viewer geometry only: sampled outlines are not manufacturing CAD (the core says so; the page says so too).
import type { AssemblyResult, Outline, Placement, PoseMember } from './types';

export interface MeshPart { key: string; family: string; colour: string; vertices: number[]; faces: number[][] }

export const ROLE_COLOUR: Record<string, string> = {
  sun: '#e0a030', planet: '#4a90c8', planet2: '#5fb0a0', ring: '#9a9aa6', gear1: '#e0a030', gear2: '#4a90c8',
};

const rad = (deg: number) => (deg * Math.PI) / 180;

/** Extruded external gear: fan from the axis on both faces, quads on the flank. Points are in the gear's own frame. */
export function extrudeSolid(points: ReadonlyArray<readonly [number, number]>, z0: number, z1: number): { vertices: number[]; faces: number[][] } {
  const pts = trimClosing(points);
  const n = pts.length;
  const vertices: number[] = [];
  pts.forEach(([x, y]) => vertices.push(x, y, z0));
  pts.forEach(([x, y]) => vertices.push(x, y, z1));
  vertices.push(0, 0, z0, 0, 0, z1);
  const c0 = 2 * n, c1 = 2 * n + 1;
  const faces: number[][] = [];
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    faces.push([c0, j, i], [c1, n + i, n + j], [i, j, n + j], [i, n + j, n + i]);
  }
  return { vertices, faces };
}

/** Internal gear: the ring between the tooth outline (inside) and a circle of the blank diameter (outside). */
export function extrudeRing(points: ReadonlyArray<readonly [number, number]>, outerDiameter: number, z0: number, z1: number): { vertices: number[]; faces: number[][] } {
  const pts = trimClosing(points);
  const n = pts.length;
  const R = outerDiameter / 2;
  const vertices: number[] = [];
  const outer = pts.map(([x, y]) => { const a = Math.atan2(y, x); return [R * Math.cos(a), R * Math.sin(a)] as const; });
  pts.forEach(([x, y]) => vertices.push(x, y, z0));       // 0..n-1   inner bottom
  pts.forEach(([x, y]) => vertices.push(x, y, z1));       // n..2n-1  inner top
  outer.forEach(([x, y]) => vertices.push(x, y, z0));     // 2n..3n-1 outer bottom
  outer.forEach(([x, y]) => vertices.push(x, y, z1));     // 3n..4n-1 outer top
  const faces: number[][] = [];
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    faces.push([i, n + j, n + i], [i, j, n + j]);                             // bore wall
    faces.push([2 * n + i, 3 * n + i, 3 * n + j], [2 * n + i, 3 * n + j, 2 * n + j]); // outer wall
    faces.push([i, 2 * n + i, 2 * n + j], [i, 2 * n + j, j]);                 // bottom annulus
    faces.push([n + i, n + j, 3 * n + j], [n + i, 3 * n + j, 3 * n + i]);     // top annulus
  }
  return { vertices, faces };
}

function trimClosing(points: ReadonlyArray<readonly [number, number]>): ReadonlyArray<readonly [number, number]> {
  const n = points.length;
  if (n > 1 && points[0][0] === points[n - 1][0] && points[0][1] === points[n - 1][1]) return points.slice(0, n - 1);
  return points;
}

/** Rotate about the z axis by `deg` and move to `centre`. */
export function place(vertices: number[], deg: number, centre: readonly [number, number, number]): number[] {
  const c = Math.cos(rad(deg)), s = Math.sin(rad(deg));
  const out = new Array<number>(vertices.length);
  for (let i = 0; i < vertices.length; i += 3) {
    const x = vertices[i], y = vertices[i + 1];
    out[i] = c * x - s * y + centre[0];
    out[i + 1] = s * x + c * y + centre[1];
    out[i + 2] = vertices[i + 2] + centre[2];
  }
  return out;
}

export const outlineOf = (p: Placement, outlines: Record<string, Outline>): Outline | null => p.outline ?? outlines[p.outline_id] ?? null;

/** One mesh per gear.  With `pose` every member sits where the pose operation put it; without, where the assembly built it. */
export function buildParts(assembly: AssemblyResult, pose: Record<string, PoseMember> | null): MeshPart[] {
  const parts: MeshPart[] = [];
  for (const p of assembly.members) {
    const outline = outlineOf(p, assembly.outlines);
    if (!outline || outline.outline_mm.length < 3) continue;
    const at = pose?.[p.name];
    const centre = at?.center_mm ?? p.center_mm;
    const rotation = at?.rotation_deg ?? p.reference_angle_deg;
    const z0 = 0, z1 = p.face_width_mm;
    const solid = outline.is_internal && outline.outer_diameter_mm
      ? extrudeRing(outline.outline_mm, outline.outer_diameter_mm, z0, z1)
      : extrudeSolid(outline.outline_mm, z0, z1);
    parts.push({ key: p.name, family: p.role, colour: ROLE_COLOUR[p.role] ?? '#8a8a8a', vertices: place(solid.vertices, rotation, centre), faces: solid.faces });
  }
  return parts;
}
