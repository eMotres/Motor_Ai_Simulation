// Thread relief for the 3D model tab: the pure maths (no three.js, no DOM) so node --test can pin it.
//
// The CAD model keeps screws, tapped holes and the shaft thread SMOOTH (plain cylinders, light STEP); the GLB says which cylindrical
// surfaces are threaded (node `extras.thread`, see cad/fc_scripts/threads.py).  The viewer draws the relief with ONE shared,
// procedurally generated normal map, like the cosmetic thread of Fusion: a lighting effect on the smooth cylinder, no geometry.
//
// Texture space of a thread mesh: u = angle round the thread axis / 2 pi (right-handed about the axis), v = distance along the axis
// / pitch.  A single-start right-hand thread is the height field h(s), s = v - u (one pitch of rise per turn), so the texture is the
// diagonal pattern h(fract(v - u)) and tiles seamlessly in both directions.  The pitch is therefore exact on every size; the
// circumferential slope depends on the radius (helix angle), which the material corrects with `normalScale`.

export type Vec3 = [number, number, number];

export interface ThreadInfo {
  kind: 'external' | 'internal';
  d_mm: number;
  pitch_mm: number;
  length_mm: number;
  standard: string;
  designation: string;
  radius_mm: number | null;
  axis: Vec3 | null;
  origin: Vec3 | null;
  hand: 'right' | 'left';
}

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const vec3 = (x: unknown): Vec3 | null => (Array.isArray(x) && x.length === 3 && x.every(isNum) ? [x[0], x[1], x[2]] : null);

/** The `thread` record of a GLB node's extras, validated; null for anything that is not a usable record (old GLBs, junk). */
export function parseThread(extras: unknown): ThreadInfo | null {
  const t = (extras as { thread?: Record<string, unknown> } | null | undefined)?.thread;
  if (!t || typeof t !== 'object') return null;
  if (t.kind !== 'external' && t.kind !== 'internal') return null;
  if (!isNum(t.d_mm) || !isNum(t.pitch_mm) || t.d_mm <= 0 || t.pitch_mm <= 0) return null;
  const standard = typeof t.standard === 'string' ? t.standard : 'ISO 261';
  return {
    kind: t.kind,
    d_mm: t.d_mm,
    pitch_mm: t.pitch_mm,
    length_mm: isNum(t.length_mm) ? t.length_mm : 0,
    standard,
    designation: typeof t.designation === 'string' ? t.designation : `M${t.d_mm}x${t.pitch_mm}`,
    radius_mm: isNum(t.radius_mm) && t.radius_mm > 0 ? t.radius_mm : null,
    axis: vec3(t.axis),
    origin: vec3(t.origin),
    hand: t.hand === 'left' ? 'left' : 'right',
  };
}

/** Tooltip text: "M2.5x0.45, ISO 261". */
export const threadLabel = (t: Pick<ThreadInfo, 'designation' | 'standard'>): string => `${t.designation}, ${t.standard}`;

// ------------------------------------------------------------------------------------------------ profile

/** ISO metric profile in units of the pitch, phase s in [0, 1): crest flat P/8 at the top, root flat P/4 at the bottom, two 60 deg
 *  flanks.  Depth 0.54 P (5/8 H); the flank slope is then tan 60 deg = sqrt 3. */
export const THREAD_DEPTH = 0.54;
const CREST = 1 / 8;
const ROOT = 1 / 4;
const FLANK = (1 - CREST - ROOT) / 2;

/** Height (pitches, 0 at the root, THREAD_DEPTH at the crest) of the sharp profile at phase s (any real number). */
export function profileHeight(s: number): number {
  const x = s - Math.floor(s);
  // root flat [0, ROOT/2] and [1 - ROOT/2, 1) is centred on s = 0; flank up; crest flat; flank down
  const a = ROOT / 2;
  if (x < a || x >= 1 - a) return 0;
  if (x < a + FLANK) return THREAD_DEPTH * ((x - a) / FLANK);
  if (x < a + FLANK + CREST) return THREAD_DEPTH;
  return THREAD_DEPTH * (1 - (x - (a + FLANK + CREST)) / FLANK);
}

/** Profile smoothed with a box of width w (pitches): softens the corners so the lit edges do not alias. */
export function smoothHeight(s: number, w = 0.05, taps = 9): number {
  let acc = 0;
  for (let i = 0; i < taps; i++) acc += profileHeight(s + (i / (taps - 1) - 0.5) * w);
  return acc / taps;
}

/** d h / d s of the smoothed profile (central difference). */
export function slope(s: number, eps = 0.004): number {
  return (smoothHeight(s + eps) - smoothHeight(s - eps)) / (2 * eps);
}

/** Reference helix factor baked into the texture: rho = pitch / (2 pi r).  M2 .. M5 shanks lie within 0.05 .. 0.065. */
export const RHO_REF = 0.06;

/** Per-material normalScale that turns the baked slope into the real one for a cylinder of radius r (mm) and the given pitch. */
export function normalScaleFor(radiusMm: number, pitchMm: number, hand: 'right' | 'left' = 'right'): [number, number] {
  const rho = pitchMm / (2 * Math.PI * radiusMm);
  return [(hand === 'left' ? -1 : 1) * (rho / RHO_REF), 1];
}

/** RGBA bytes (size x size, row j = v) of the tangent-space normal map of the diagonal helix pattern.  Periodic in u and v. */
export function buildThreadNormalMap(size = 128): Uint8Array {
  const out = new Uint8Array(size * size * 4);
  const table = new Float64Array(size);
  for (let k = 0; k < size; k++) table[k] = slope(k / size);
  for (let j = 0; j < size; j++) {
    for (let i = 0; i < size; i++) {
      const hp = table[(((j - i) % size) + size) % size];     // h'(s) at s = (j - i) / size
      // height gradient: d h / d v = h' (axial), d h / d u = -h' * rho (circumferential); normal = (-h_u, -h_v, 1)
      let nx = hp * RHO_REF;
      let ny = -hp;
      let nz = 1;
      const n = Math.hypot(nx, ny, nz);
      nx /= n; ny /= n; nz /= n;
      const o = 4 * (j * size + i);
      out[o] = Math.round((nx * 0.5 + 0.5) * 255);
      out[o + 1] = Math.round((ny * 0.5 + 0.5) * 255);
      out[o + 2] = Math.round((nz * 0.5 + 0.5) * 255);
      out[o + 3] = 255;
    }
  }
  return out;
}

// ------------------------------------------------------------------------------------------------ cylindrical projection

const dot = (a: ArrayLike<number>, b: ArrayLike<number>): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: ArrayLike<number>, b: ArrayLike<number>): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a: ArrayLike<number>): Vec3 => { const n = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / n, a[1] / n, a[2] / n]; };

/** An orthonormal frame (e1, e2, axis), right-handed (e1 x e2 = axis), for a thread axis. */
export function axisFrame(axis: ArrayLike<number>): { e1: Vec3; e2: Vec3; a: Vec3 } {
  const a = unit(axis);
  const helper: Vec3 = Math.abs(a[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  const e1 = unit(cross(helper, a));        // perpendicular to the axis
  const e2 = cross(a, e1);                  // e1 x e2 = a
  return { e1, e2, a };
}

/** Cylindrical texture coordinates of NON-INDEXED triangle positions (x y z per vertex, three vertices per triangle):
 *  u = angle / 2 pi about `axis` through `origin`, v = distance along the axis / pitch.  The angle is unwrapped inside each
 *  triangle, so a triangle that crosses the atan2 seam keeps continuous (and for the repeat-wrapped texture correct) coordinates.
 *  A left-hand thread mirrors u. */
export function threadUVs(positions: ArrayLike<number>, axis: ArrayLike<number>, origin: ArrayLike<number>, pitch: number,
  hand: 'right' | 'left' = 'right'): Float32Array {
  const n = Math.floor(positions.length / 3);
  const { e1, e2, a } = axisFrame(axis);
  const uv = new Float32Array(n * 2);
  const sign = hand === 'left' ? -1 : 1;
  const p: number[] = [0, 0, 0];
  const u = [0, 0, 0];
  for (let t = 0; t + 2 < n; t += 3) {
    for (let k = 0; k < 3; k++) {
      const i = 3 * (t + k);
      p[0] = positions[i] - origin[0]; p[1] = positions[i + 1] - origin[1]; p[2] = positions[i + 2] - origin[2];
      u[k] = Math.atan2(dot(p, e2), dot(p, e1)) / (2 * Math.PI);
      uv[2 * (t + k) + 1] = dot(p, a) / pitch;
    }
    for (let k = 1; k < 3; k++) {
      while (u[k] - u[0] > 0.5) u[k] -= 1;
      while (u[k] - u[0] < -0.5) u[k] += 1;
    }
    for (let k = 0; k < 3; k++) uv[2 * (t + k)] = sign * u[k];
  }
  return uv;
}

/** Axis and origin of a thread mesh: the record's, else estimated from the geometry (a cylinder along Z through the centre of
 *  its bounding box - the threaded faces of the motor models are full cylinders on the motor axis or on a Z-parallel bolt axis). */
export function resolveAxis(info: Pick<ThreadInfo, 'axis' | 'origin'>, positions: ArrayLike<number>): { axis: Vec3; origin: Vec3 } {
  if (info.axis) {
    if (info.origin) return { axis: info.axis, origin: info.origin };
    const o = bboxCentre(positions);
    return { axis: info.axis, origin: o };
  }
  return { axis: [0, 0, 1], origin: bboxCentre(positions) };
}

function bboxCentre(positions: ArrayLike<number>): Vec3 {
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i + 2 < positions.length; i += 3) {
    for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], positions[i + k]); hi[k] = Math.max(hi[k], positions[i + k]); }
  }
  return [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
}
