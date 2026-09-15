/**
 * fieldView — ONE renderer for every 2-D field view (A_z, |B|, J, J⟳, Loss,
 * Demag, Temp).
 *
 * Before this module each view built its own geometry, its own colour ramp and
 * its own legend, in seven copies inside FemFieldChart.  They had drifted into
 * seven different pictures of the same machine: |B| was flat per-element while
 * Demag was nodal-smoothed, Loss banded in the shader while J quantised on the
 * CPU with a hand-written 9-stop ramp, the legend recomputed its range with its
 * own percentile code (so bar and fill could disagree), and the band count was
 * 20 / 13 / 11 / none depending on which view you were looking at.  The user's
 * complaint — "our maps look ugly next to Ansys's" — was mostly that: coarse,
 * flat-shaded, few levels, and no two views alike.
 *
 * What every view now gets, identically:
 *
 *   • per-vertex interpolation WITHIN a material class, never across one.
 *     Element-constant fields (|B|, loss density, J, demag) are averaged onto
 *     the mesh vertices with area weights, but a vertex is duplicated per
 *     (vertex, material class), so a slot-air vertex never reads half of the
 *     copper's density and a magnet corner never bleeds into the air gap.
 *     Smooth inside a part, crisp at every boundary — the Ansys reading.
 *   • ~11 DISCRETE colour bands, quantised PER PIXEL from the interpolated
 *     scalar (not at the vertices — quantising there and letting the GPU blend
 *     the resulting RGB mixes neighbouring band colours into blotches).  Every
 *     band edge therefore lands exactly on an iso-line of the field, and the
 *     shader draws that edge as a thin dark line: Ansys's banded contour plot,
 *     for free, in every view.
 *   • ONE scale object shared by the fill and the colour bar, so the legend
 *     cannot describe a range the picture does not use.  The bar prints the
 *     BAND EDGES, which is what a banded plot's legend is for.
 */
import * as THREE from 'three';
import type { FemPayload } from './fem-types';
// TYPE-ONLY (the common module imports `bandColor` / `classOf` from here at
// runtime, so a value import back would be a cycle).
import type { FieldOutput, FieldOverlay } from '../common/fieldOutput';

// ── domain tags (backend simulation/geo_mesh.py) ──────────────────────────
export const DOM = {
  AIR: 0, STATOR: 1, COIL: 2, MAG_N: 4, ROTOR: 5, SHAFT: 6, OUTER: 8,
  MAG_S: 44, MAG_BASE: 100, COIL_BASE: 200,
  // The thermal solve's own air vocabulary (backend routes/thermal.py
  // `THERMAL_EXTRA_DOMAINS`).  Added 2026-09-07, when the air the conduction
  // solve conducts through stopped being dropped and became five named domains.
  SLEEVE: 11,
  SLOT_LINER: 61, WIRE_ENAMEL: 62, SLOT_FILL: 63, GAP_AIR: 64, POCKET_AIR: 65,
} as const;

/** Material class for the smoothing split.  The SHAFT is its own class, not
 *  "air-like": under the coupled eddy solve it carries a real solved loss, and
 *  lumping it with the air smeared that loss into the bore.
 *
 *  Each thermal air domain is its own class for the same reason: they sit
 *  directly against each other (liner on tooth, enamel on copper, fill on
 *  both) and their conductivities differ by more than an order of magnitude, so
 *  a shared class would average a 0.14 W/m·K liner's heat flux together with
 *  the 0.25 W/m·K fill beside it and smear away the one boundary the picture
 *  exists to show. */
export function classOf(d: number): number {
  if (d === DOM.COIL || d >= DOM.COIL_BASE) return 4;
  if (d === DOM.MAG_N || d === DOM.MAG_S
      || (d >= DOM.MAG_BASE && d < DOM.COIL_BASE)) return 3;
  if (d === DOM.STATOR) return 1;
  if (d === DOM.ROTOR) return 2;
  if (d === DOM.SHAFT) return 5;
  if (d === DOM.SLEEVE) return 6;
  if (d === DOM.SLOT_LINER) return 7;
  if (d === DOM.WIRE_ENAMEL) return 8;
  if (d === DOM.SLOT_FILL) return 9;
  if (d === DOM.GAP_AIR) return 10;
  if (d === DOM.POCKET_AIR) return 11;
  return 0;                                   // air / gap / band
}
// One more than the highest class above.  It is only a KEY STRIDE into a sparse
// Map (`iv * N_CLASS + cls`), so growing it costs nothing but must never shrink
// below the classes `classOf` can return, or two parts would share a key.
const N_CLASS = 12;

/** Name of the material class a domain tag belongs to — what the max/min marker
 *  says the peak is IN (user 2026-09-06: the marker has to name the part, or
 *  "5408 MPa somewhere on the rotor" is not an engineering answer).
 *
 *  This is the FALLBACK vocabulary: a payload that carries `part_names` (the
 *  thermal one does) is labelled from that instead.  The thermal entries are
 *  here so a cached payload from before `part_names` reached /field still names
 *  its liner rather than calling it air. */
const CLASS_NAME: Record<number, string> = {
  0: 'air', 1: 'stator iron', 2: 'rotor iron', 3: 'magnet', 4: 'copper',
  5: 'shaft', 6: 'sleeve', 7: 'insulation', 8: 'wire enamel', 9: 'wire coating',
  10: 'air gap', 11: 'pocket air',
};
export function emClassName(domain: number): string | undefined {
  return CLASS_NAME[classOf(domain)];
}

/**
 * Domain outlines straight from a mesh: the boundary of every material class
 * (stator, rotor, magnets, coils, shaft), chained into closed loops in the
 * same shape the backend's CadQuery outlines use — so a payload assembled
 * on the client (the run's demag map read off the stored transient) draws
 * the motor around the coloured magnets instead of magnets floating in
 * nothing (user 2026-09-05: "а почему мотора не видно?").
 */
export function outlinesFromMesh(
  vertices: [number, number][],
  triangles: [number, number, number][],
  domain_per_tri: number[],
  nSectors = 1,
): { domain: number; loops: [number, number][][] }[] {
  // A sector mesh ends on two radial cut lines (angles 0 and 2π/N); the
  // edges lying ON them are mesh boundary, not material boundary, and would
  // draw as spokes across the tiled ring.  Recognise a vertex on a cut by its
  // angle; an edge with both ends on a cut and only one triangle is dropped.
  const sect = nSectors > 1 ? 2 * Math.PI / nSectors : 0;
  const onCut = (vi: number) => {
    if (!sect) return false;
    const [x, y] = vertices[vi];
    const r = Math.hypot(x, y);
    if (r < 1e-9) return true;
    const a = ((Math.atan2(y, x) % sect) + sect) % sect;
    return Math.min(a, sect - a) * r < 1e-6;      // within 1 nm of the cut line
  };
  // edge → the classes of the triangles on its two sides
  const sides = new Map<number, number[]>();
  const nv = vertices.length;
  const ek = (a: number, b: number) => (a < b ? a * nv + b : b * nv + a);
  for (let ti = 0; ti < triangles.length; ti++) {
    const c = classOf(domain_per_tri[ti]);
    const [a, b, d] = triangles[ti];
    for (const [p, q] of [[a, b], [b, d], [d, a]] as [number, number][]) {
      const k = ek(p, q);
      const s = sides.get(k);
      if (s) s.push(c); else sides.set(k, [c]);
    }
  }
  // per class: the edges where that class meets something else
  const perClass = new Map<number, Map<number, number[]>>();   // cls → vertex → nbrs
  sides.forEach((cs, k) => {
    const a = Math.floor(k / nv), b = k - a * nv;
    const c0 = cs[0], c1 = cs.length > 1 ? cs[1] : -1;
    if (c0 === c1) return;
    if (cs.length === 1 && onCut(a) && onCut(b)) return;   // sector cut, not an outline
    for (const c of [c0, c1]) {
      if (c <= 0) continue;                       // air / gap / band: no outline
      let adj = perClass.get(c);
      if (!adj) { adj = new Map(); perClass.set(c, adj); }
      (adj.get(a) ?? adj.set(a, []).get(a)!).push(b);
      (adj.get(b) ?? adj.set(b, []).get(b)!).push(a);
    }
  });
  // One two-point "loop" per boundary edge.  NOT chained into polylines: the
  // renderer closes every loop with a segment from its last vertex back to
  // its first, so a chain that is open (the stator and rotor boundaries are,
  // once the sector-cut edges are dropped) drew a chord straight across the
  // machine (user 2026-09-05: "какие-то чёрточки внутри").  A two-point loop
  // closes onto itself — the edge is drawn twice, nothing else is drawn.
  const out: { domain: number; loops: [number, number][][] }[] = [];
  perClass.forEach((adj, cls) => {
    const loops: [number, number][][] = [];
    adj.forEach((nbrs, a) => {
      for (const b of nbrs) if (a < b) loops.push([vertices[a], vertices[b]]);
    });
    out.push({ domain: cls, loops });
  });
  return out;
}

// ── colour ────────────────────────────────────────────────────────────────
/** Classic Ansys rainbow (blue → cyan → green → yellow → red). */
export function jet01(t: number): [number, number, number] {
  const x = Math.max(0, Math.min(1, t));
  return [
    Math.max(0, Math.min(1, 1.5 - Math.abs(4 * x - 3))) * 255,
    Math.max(0, Math.min(1, 1.5 - Math.abs(4 * x - 2))) * 255,
    Math.max(0, Math.min(1, 1.5 - Math.abs(4 * x - 1))) * 255,
  ];
}
/** Colour of band k of n — the legend's swatch, and exactly what the shader
 *  paints inside that band (band CENTRE, same formula). */
export function bandColor(k: number, n: number): [number, number, number] {
  return jet01((Math.min(Math.max(k, 0), n - 1) + 0.5) / n);
}

/** Number of colour bands.  ONE value for every view — "все графики одинаково"
 *  was the request, and a plot whose band count changes with the quantity is
 *  a plot you cannot compare with the one beside it. */
export const N_BANDS = 11;

// ── shader: band per pixel + iso-line on every band edge ──────────────────
export const BAND_VERT = `
  attribute float aVal;
  varying float vVal;
  void main() {
    vVal = aVal;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
export const BAND_FRAG = `
  uniform float uBands;          // 0 = continuous
  uniform float uIso;            // 0..1 — darkness of the band-edge iso-line
  varying float vVal;
  vec3 jet(float x) {
    x = clamp(x, 0.0, 1.0);
    return vec3(clamp(1.5 - abs(4.0 * x - 3.0), 0.0, 1.0),
                clamp(1.5 - abs(4.0 * x - 2.0), 0.0, 1.0),
                clamp(1.5 - abs(4.0 * x - 1.0), 0.0, 1.0));
  }
  void main() {
    float t = clamp(vVal, 0.0, 1.0);
    float band = t;
    if (uBands > 0.5) band = (floor(t * uBands) + 0.5) / uBands;
    vec3 col = jet(band);
    // Iso-line: the band edges ARE iso-levels of the field, so drawing them
    // costs one fwidth().  Screen-space width => the line stays one pixel at
    // every zoom instead of fattening as the user zooms in.
    //
    // NOT at the CLAMPED ends.  vmin and vmax are percentiles, so whole regions
    // sit outside them and clamp to t = 0 or t = 1 exactly — where fract(s) = 0
    // and this drew a full-strength iso-line over every one of those pixels.
    // On this machine that is 52 % of the shaft (its σE² runs 6 decades below
    // the 5-percentile floor) painted a flat dark wash instead of the bottom
    // blue, which reads as "something is filled in here that should be empty",
    // and 1.5 % of the copper — the hottest material on the map — rendered near
    // BLACK instead of red, i.e. the one component the picture exists to show
    // looked cold.  t = 0 and t = 1 are the edges of the DOMAIN, not band
    // edges; there is no iso-level there to draw.
    if (uBands > 0.5 && uIso > 0.0 && t > 0.0 && t < 1.0) {
      float s = t * uBands;
      float d = min(fract(s), 1.0 - fract(s)) / max(fwidth(s), 1e-6);
      col = mix(col, col * 0.30, uIso * (1.0 - smoothstep(0.0, 1.0, d)));
    }
    gl_FragColor = vec4(col, 1.0);
  }
`;

// ── scale ─────────────────────────────────────────────────────────────────
export type Mapping = 'linear' | 'log' | 'rank' | 'edges';

export interface FieldScale {
  vmin: number;
  vmax: number;
  unit: string;
  bands: number;
  mapping: Mapping;
  /** 'edges' mapping: the bands+1 fixed band edges, ascending, display units. */
  edges?: number[];
  /** Display-unit value at band edge k (0…bands) — what the legend prints. */
  edge: (k: number) => number;
  fmt: (v: number) => string;
  /** One line saying what the colours mean, printed under the view. */
  note: string;
}

/** Per drawn triangle: centroid in DISPLAY mm and the value in DISPLAY units.
 *  Feeds the value-at-cursor readout the shared viewer prints in its header —
 *  the picture can be read as numbers, not only as colours (user 2026-09-06). */
export interface FieldProbeData {
  cx: Float32Array;
  cy: Float32Array;
  val: Float32Array;
  /** RAW class tag per drawn triangle (an EM domain id, a mechanical part id).
   *  Not the dense remap: the HOST owns the names, and it names its own tags.
   *  User 2026-09-06: "подсвечивать точки максимальных деформаций, напряжений и
   *  полей" — a peak that does not say WHICH part it is in is half an answer. */
  cls?: Int32Array;
}

export interface FieldView {
  geometry: THREE.BufferGeometry | null;
  scale: FieldScale | null;
  /** hover readout source — same triangles the geometry drew */
  probe?: FieldProbeData | null;
  /** raw display-unit value per drawn VERTEX, before normalisation.  The shared
   *  viewer's Part selector re-colours one part against its OWN range, and the
   *  normalised attribute is percentile-clipped so it cannot be inverted back
   *  to a value — see common/fieldOutput.FieldOutput.vertexValues (2026-09-06). */
  vertexValues?: Float32Array | null;
  /** TRUE extremes of what is drawn, in display units.  Not the colour range:
   *  `scale.vmin/vmax` are percentile-clipped on purpose, and a header that
   *  printed those as "max" would under-report every singular corner. */
  vMin?: number | null;
  vMax?: number | null;
}

export const pctl = (arr: ArrayLike<number>, p: number): number => {
  if (!arr.length) return 0;
  const a = Float64Array.from(arr as any).sort();
  const i = Math.max(0, Math.min(a.length - 1,
    Math.floor((p / 100) * (a.length - 1))));
  return a[i];
};

export const fmt2 = (v: number) => v.toFixed(2);
export const fmt0 = (v: number) => v.toFixed(0);
/** Significant-figure-ish formatter: 2 decimals under 10, 1 under 100, none
 *  above.  Lived as a private `fmtMPa` inside the mechanical adapter until
 *  2026-09-06 — the user asked that the stress/strain views be drawn "так же
 *  как B — единый стиль везде", and two copies of the number formatter is
 *  exactly how two views start printing the same value differently. */
export const fmtAuto = (v: number): string => {
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
};
export const fmtSI = (v: number) => {
  const a = Math.abs(v);
  return a >= 1e9 ? `${(v / 1e9).toFixed(1)}G`
       : a >= 1e6 ? `${(v / 1e6).toFixed(1)}M`
       : a >= 1e3 ? `${(v / 1e3).toFixed(0)}k`
       : v.toFixed(a < 10 ? 1 : 0);
};

/** The FLOOR of a colour scale: the field's own minimum, not zero.
 *
 *  User 2026-09-07: "почему шкала от нуля — нужно везде от минимума строить".
 *  A bar that starts at 0 for a field that lives between 1.5 and 8 spends a
 *  fifth of the palette on values nobody has; starting at the minimum gives the
 *  whole palette to the range that exists.  Non-finite values are skipped; an
 *  empty field floors at 0. */
export function floorOf(values: ArrayLike<number>): number {
  let m = Infinity;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (Number.isFinite(v) && v < m) m = v;
  }
  return Number.isFinite(m) ? m : 0;
}

export function linScale(vmin: number, vmax: number, unit: string, note: string,
                         fmt = fmt2, bands = N_BANDS): FieldScale {
  const span = (vmax - vmin) || 1e-12;
  return { vmin, vmax, unit, bands, mapping: 'linear', fmt, note,
           edge: (k) => vmin + span * (k / bands) };
}
export function logScale(vmin: number, vmax: number, unit: string, note: string,
                         fmt = fmtSI, bands = N_BANDS): FieldScale {
  const a = Math.log10(Math.max(vmin, 1e-30));
  const b = Math.log10(Math.max(vmax, vmin * 10));
  return { vmin, vmax, unit, bands, mapping: 'log', fmt, note,
           edge: (k) => Math.pow(10, a + (b - a) * (k / bands)) };
}
export function rankScale(sorted: Float64Array, unit: string, note: string,
                          fmt = fmt0, bands = N_BANDS): FieldScale {
  const q = (f: number) => sorted.length
    ? sorted[Math.min(sorted.length - 1, Math.round(f * (sorted.length - 1)))]
    : 0;
  return { vmin: q(0), vmax: q(1), unit, bands, mapping: 'rank', fmt, note,
           edge: (k) => q(k / bands) };
}

/** Fixed, hand-placed band edges — for a quantity whose MEANING lives in a
 *  narrow part of its range (Br retention: everything that matters happens
 *  between 90 and 100 %).  Each band is one entry of `edges`, whatever its
 *  width, so a 1 %-wide band near the top gets the same colour step as a
 *  50 %-wide band at the bottom. */
export function edgesScale(edges: number[], unit: string, note: string,
                           fmt = fmt2): FieldScale {
  const bands = edges.length - 1;
  return { vmin: edges[0], vmax: edges[bands], unit, bands, mapping: 'edges',
           edges, fmt, note, edge: (k) => edges[Math.max(0, Math.min(bands, k))] };
}

/** value → 0..1 for the LUT, under this scale. */
export function normaliser(sc: FieldScale, sorted?: Float64Array): (v: number) => number {
  if (sc.mapping === 'edges' && sc.edges && sc.edges.length > 1) {
    const e = sc.edges, n = e.length - 1;
    return (v) => {
      if (v <= e[0]) return 0;
      if (v >= e[n]) return 1;
      let k = 0;
      while (k < n - 1 && v >= e[k + 1]) k++;
      const w = Math.max(e[k + 1] - e[k], 1e-12);
      return Math.max(0, Math.min(1, (k + (v - e[k]) / w) / n));
    };
  }
  if (sc.mapping === 'log') {
    const a = Math.log10(Math.max(sc.vmin, 1e-30));
    const b = Math.log10(Math.max(sc.vmax, sc.vmin * 10));
    const d = Math.max(b - a, 1e-9);
    return (v) => (v <= 0 ? 0 : Math.max(0, Math.min(1, (Math.log10(v) - a) / d)));
  }
  if (sc.mapping === 'rank' && sorted && sorted.length > 1) {
    const n = sorted.length;
    return (v) => {
      let lo = 0, hi = n - 1;
      while (lo < hi) {                       // first index with sorted[i] >= v
        const mid = (lo + hi) >> 1;
        if (sorted[mid] < v) lo = mid + 1; else hi = mid;
      }
      return lo / (n - 1);
    };
  }
  const span = Math.max(sc.vmax - sc.vmin, 1e-12);
  return (v) => Math.max(0, Math.min(1, (v - sc.vmin) / span));
}

// ── per-mode source ───────────────────────────────────────────────────────
type Source = {
  /** element-constant scalar → area-weighted nodal average WITHIN its class */
  elem?: (ti: number) => number;
  /** already-continuous nodal scalar (A_z, T) → read straight off the node */
  node?: (vi: number) => number;
  /** which triangles are drawn at all */
  include: (ti: number, dom: number) => boolean;
  scale: FieldScale;
  /** sorted sample, for a rank ('equalised') mapping */
  sorted?: Float64Array;
};

/**
 * Colour range for the Demag map: [worst, best] remaining Br over the magnet
 * triangles only.  Auto-ranged at BOTH ends — with the top pinned at 100 % a
 * magnet sitting between 15 % and 31 % gets the bottom sixth of the palette and
 * reads as one flat colour.
 */
export function demagRange(dc: number[] | undefined,
                           dom: ArrayLike<number>, nTri: number): [number, number] {
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < nTri; i++) {
    if (classOf(dom[i]) !== 3) continue;
    const cc = dc ? Math.max(0, Math.min(1, dc[i])) : 1;
    if (cc < lo) lo = cc;
    if (cc > hi) hi = cc;
  }
  if (!isFinite(lo) || !isFinite(hi)) return [0, 1];
  if (hi - lo < 0.02) {
    const mid = 0.5 * (lo + hi);
    lo = Math.max(0, mid - 0.01);
    hi = Math.min(1, Math.max(lo + 0.02, mid + 0.01));
  }
  return [lo, hi];
}

export interface ViewOpts {
  logLoss?: boolean;
  /** Loss view: colour each material against its OWN range (reading aid). */
  perMaterialLoss?: boolean;
}

function sourceFor(payload: FemPayload, mode: string, o: ViewOpts): Source | null {
  const dom = payload.domain_per_tri as unknown as number[];
  const tris = payload.triangles;
  const nTri = tris.length;
  const interior = (_ti: number, d: number) => d !== DOM.OUTER;

  /* The 'Temp' source moved to `thermal/fieldAdapters` on 2026-09-07 with the
     rest of the thermal solve: a temperature map is not an electromagnetic
     output, and this file's payload (`FemPayload`) is the EM one. */

  if (mode === 'Az') {
    // LINEAR, symmetric about zero: A_z varies linearly across a uniformly
    // magnetised magnet, so linear band spacing is what makes the bands (and
    // the flux lines that coincide with them) evenly spaced inside it.
    const A = payload.A_z_per_node;
    const seen = new Set<number>();
    for (let ti = 0; ti < nTri; ti++) {
      if (dom[ti] === DOM.OUTER) continue;
      for (const vi of tris[ti]) seen.add(vi);
    }
    const abs: number[] = [];
    seen.forEach(vi => abs.push(Math.abs(A[vi])));
    const amax = Math.max(pctl(abs, 99), 1e-12);
    return {
      node: (vi) => A[vi] * 1e3,
      include: interior,
      scale: linScale(-amax * 1e3, amax * 1e3, 'mWb/m',
                      'magnetic vector potential A_z — the band edges are flux lines',
                      fmt2),
    };
  }

  if (mode === 'Loss') {
    const ld = payload.loss_density_per_tri ?? [];
    // Classes no model produced a value for are NOT DRAWN.  Painting them the
    // bottom of the scale is indistinguishable from air, i.e. from "no loss
    // here" — and the magnets are exactly the component that kept coming back
    // unmodelled (a run without the coupled eddy solve leaves P_mag at 0) and
    // exactly the one the user was looking at.
    const unmod = new Set<number>();
    for (const n of (payload.loss_density_unmodelled ?? [])) {
      if (n === 'magnets') unmod.add(3);
      else if (n === 'copper') unmod.add(4);
      else if (n === 'iron') { unmod.add(1); unmod.add(2); }
      else if (n === 'shaft') unmod.add(5);
      else if (n === 'air') unmod.add(0);
    }
    // AIR IS NEVER DRAWN ON A LOSS MAP — unconditionally, not only when the
    // backend remembered to say so.  There is no air-loss model to run (σ=0,
    // no hysteresis, windage is not a magnetic solve), so every air element is
    // exactly 0 — and 0 on a LOG scale is a COLOUR, band 0, the same deep blue
    // a genuinely-measured tiny loss gets.  That is what painted the air gap
    // as a smooth ring and put contour bands through the rotor's air pockets
    // and the bore: not a smear, just zero being coloured in.  A payload from
    // an older backend (or the single-frame analytic estimate) never carries
    // the flag, so the rule lives here, where the picture is made.
    unmod.add(0);
    const drawn = (_ti: number, d: number) =>
      d !== DOM.OUTER && !unmod.has(classOf(d));
    // The colour range — and so the legend's floor — is built from MODELLED,
    // NON-ZERO elements only: `drawn` drops air and every unmodelled class,
    // then v > 0 drops the genuine zeros inside a material that IS modelled.
    // So the bottom of the log scale is a real density somebody computed, and
    // nothing sits below it except the blanks.
    const pos: number[] = [];
    for (let ti = 0; ti < nTri; ti++) {
      if (!drawn(ti, dom[ti])) continue;
      const v = ti < ld.length ? ld[ti] : 0;
      if (v > 0) pos.push(v);
    }
    const vmax = Math.max(pctl(pos, 99.5), 1e-3);
    const vmin = Math.max(pctl(pos, 5), vmax * 1e-4);
    const label = (payload as any).loss_density_label as string | undefined;

    // ── PER-MATERIAL scaling (opt-in) ────────────────────────────────────
    // On the shared scale this machine's copper sits at 3.7e7 W/m³ mean and
    // the magnets at 1.7e6 — 1.5 decades apart — so a LINEAR map puts every
    // magnet element in band 0-1 and the whole rotor reads "no loss".  The
    // log map is honest and does separate them (magnets land in bands 4-7 of
    // 11), but the copper is pinned at the top and the eye calibrates on it.
    //
    // Per-material mode normalises each material class to its OWN 5/99.5
    // percentiles, which is what "scale per body" does in Ansys and the only
    // way to read structure inside the weakest component.  It buys that by
    // giving up cross-material comparison entirely: the same colour means a
    // different number in copper and in a magnet.  So it is OFF by default,
    // it is a separate toggle, and the note below says so in those words —
    // this is a reading aid, never the number.
    if (o.perMaterialLoss) {
      const rng = new Map<number, [number, number]>();
      const byCls = new Map<number, number[]>();
      for (let ti = 0; ti < nTri; ti++) {
        if (!drawn(ti, dom[ti])) continue;
        const v = ti < ld.length ? ld[ti] : 0;
        if (v <= 0) continue;
        const c = classOf(dom[ti]);
        const a = byCls.get(c); if (a) a.push(v); else byCls.set(c, [v]);
      }
      byCls.forEach((arr, c) => {
        const hi = Math.max(pctl(arr, 99.5), 1e-3);
        rng.set(c, [Math.max(pctl(arr, 5), hi * 1e-4), hi]);
      });
      // The log/lin state has to be IN the note here, not just on the button.
      // Per-material + log compresses ratios hard: the spoke magnets' hub end
      // is 29-36 % of their air-gap corner peak, and log puts that at band 6.7
      // against 9.0 — it reads as "nearly as hot".  On linear the same 36 %
      // reads as band 4 against 10, which is what it is.  A reader who cannot
      // see which mapping is on cannot know which of those they are looking at.
      const note = (label ? label : 'loss density')
        + (o.logLoss ? ' · log' : ' · linear')
        + ' PER-MATERIAL colour scale: every material spans its own'
        + ' 5-99.5 % range, so a colour is NOT comparable between'
        + ' materials — read structure here, read levels on the shared scale'
        + (o.logLoss ? '; log compresses ratios, switch to lin to judge how'
                       + ' much hotter one spot is than another' : '');
      return {
        // Normalised to PER CENT of the element's own class range right here
        // (the shared FieldScale carries exactly one range by construction, so
        // a per-class range cannot live in it).  The nodal average then runs on
        // the already-normalised value — correct, because it never mixes
        // classes anyway.
        elem: (ti) => {
          const v = ti < ld.length ? ld[ti] : 0;
          if (v <= 0) return 0;
          const r = rng.get(classOf(dom[ti]));
          if (!r) return 0;
          const [lo, hi] = r;
          const t = o.logLoss
            ? (Math.log10(v) - Math.log10(lo))
              / Math.max(Math.log10(hi) - Math.log10(lo), 1e-9)
            : v / hi;
          return 100 * Math.max(0, Math.min(1, t));
        },
        include: drawn,
        // The bar can only be a fraction: there is no single W/m³ axis left.
        scale: linScale(0, 100, '% of each material’s own range', note, fmt0),
      };
    }

    const note = (label ? label : 'loss density')
      + (o.logLoss ? ' · log colour scale' : ' · linear colour scale')
      + ' · one shared scale across all materials'
      + ' · air and any unmodelled material are left BLANK — every coloured'
      + ' element is a modelled one, and the scale floor is the 5th percentile'
      + ' of the modelled NON-ZERO densities';
    return {
      elem: (ti) => (ti < ld.length ? ld[ti] : 0),
      include: drawn,
      scale: o.logLoss ? logScale(vmin, vmax, 'W/m³', note)
                       : linScale(Math.min(vmin, vmax * 0.999), vmax, 'W/m³', note, fmtSI),
    };
  }

  if (mode === 'J' || mode === 'Jeddy') {
    // Signed current density.  DIVERGING by construction: the range is
    // symmetric, so jet's green midpoint lands exactly on J = 0 — the Ansys J
    // legend, without a second hand-written ramp to keep in step with the
    // first.
    //
    // WINDINGS ONLY, in both J modes.  The coupled solve does drive eddy
    // current in the magnets and the shaft, but it is ~2 decades below the
    // winding current, so on the linear symmetric scale the copper needs they
    // would paint the whole rotor mid-scale green — "no current here", which
    // is the one thing that is not true.  Where the magnet current matters is
    // its LOSS, and the Loss view shows that on a log scale.
    const jz = payload.J_z_per_tri ?? [];
    const inJ = (_ti: number, d: number) => classOf(d) === 4;
    const s: number[] = [];
    for (let ti = 0; ti < nTri; ti++) {
      if (!inJ(ti, dom[ti])) continue;
      if (ti < jz.length) s.push(Math.abs(jz[ti]));
    }
    // THE FIELD'S OWN MAX, not its 99th percentile (2026-09-10).
    //
    // The clip was hiding the picture rather than taming it.  On the live O200
    // the ordinary DC current density is 52.8 MA/m2 peak (671.75 A rms over 4
    // parallel 0.5x9 mm strands) while p99 of the drawn field came out at
    // 46.6 MA/m2 — BELOW it.  Every conductor therefore sat past the end of the
    // scale and every slot painted as one saturated block, which is the one
    // thing a current-density map must never do; and the header, which reads the
    // drawn field honestly, printed a max of 158.5 MA/m2 that could not be found
    // anywhere on the bar.  Two numbers for one field again — the same complaint
    // the stress views were fixed for ("везде и в Ansys и в Fusion полное
    // соответствие").
    //
    // Unclipped, the DC level lands around a third of the scale and the
    // proximity crowding reads as what it is: brighter copper near the field,
    // not a solid rectangle.  The bar's top IS the number in the header.
    let vmax = 1e-12;
    for (const v of s) if (v > vmax) vmax = v;
    return {
      elem: (ti) => (ti < jz.length ? jz[ti] : 0),
      include: inJ,
      scale: linScale(-vmax, vmax, 'A/m²',
                      mode === 'Jeddy'
                        ? 'solved eddy current density σ(−∂A/∂t + U) in the windings'
                        : 'applied source current density in the windings',
                      fmtSI),
    };
  }

  if (mode === 'Demag') {
    const dc = (payload as any).demag_coef_per_tri as number[] | undefined;
    const [lo, hi] = demagRange(dc, dom, nTri);
    // FIXED bands, dense near 100 % (user 2026-09-04: the card said 94.4 %
    // retained while the map looked intact — a linear worst→best scale put
    // 90.5…99.3 % into ONE dark-red band, so a uniform 5 % loss was
    // invisible).  1 % steps at the top, where a real de-rating lives; the
    // bottom band swallows the destroyed corners.  Falls back to the
    // auto-range only when the whole magnet sits below 90 % — there the
    // fixed top bands would all be empty.
    const DEMAG_EDGES = [0, 50, 70, 80, 85, 90, 93, 95, 97, 98, 99, 100];
    const fixed = hi * 100 >= 90;
    // The band EDGES stay fixed (comparable between runs), but the bar starts
    // at the band the field's minimum falls in: a magnet that kept 98–100 %
    // does not need the 0…97 % bands drawn (user 2026-09-09: "почему шкала
    // от 0?").  At least the top two bands are always shown.
    const loPct = lo * 100;
    const firstIdx = Math.max(0, Math.min(DEMAG_EDGES.length - 3,
      DEMAG_EDGES.findIndex((e) => e > loPct) - 1));
    const edges = DEMAG_EDGES.slice(firstIdx);
    return {
      elem: (ti) => (dc ? Math.max(0, Math.min(1, dc[ti])) : 1) * 100,
      include: (_ti, d) => classOf(d) === 3,
      scale: fixed
        ? edgesScale(edges, '% Br',
                     `irreversible demagnetisation — % of Br remaining (fixed 1 % bands near 100 %; bar starts at the ${edges[0]} % band, where this magnet's minimum lies)`,
                     (v) => v.toFixed(0))
        : linScale(lo * 100, hi * 100, '% Br',
                   'irreversible demagnetisation — % of Br remaining (auto range: the whole magnet is below 90 %)',
                   (v) => v.toFixed(1)),
    };
  }

  // |B| — the default.  vmax = 99.5-percentile of the interior, capped at 4 T:
  // wide enough for the real tooth-tip field, while the percentile keeps a
  // single sharp-corner spike from squashing the whole LUT.
  const Bm = payload.Bmag_per_tri;
  const bs: number[] = [];
  for (let ti = 0; ti < nTri; ti++) {
    if (dom[ti] === DOM.OUTER) continue;
    bs.push(Bm[ti]);
  }
  const vmax = Math.min(Math.max(pctl(bs, 99.5), 0.05), 4.0);
  return {
    elem: (ti) => Bm[ti] * 1e3,
    include: interior,
    scale: linScale(Math.min(floorOf(bs), vmax * 0.999) * 1e3, vmax * 1e3, 'mT', 'flux density |B|', fmt0),
  };
}

/**
 * Build the geometry + the scale for one view.  Non-indexed (3 corners per
 * drawn triangle) in EVERY mode: the material-class split needs a vertex per
 * (vertex, class), and one geometry layout for all views is one less thing that
 * can differ between them.
 */
export function buildFieldView(payload: FemPayload | null, mode: string,
                               opts: ViewOpts = {}): FieldView {
  if (!payload || !payload.vertices || !payload.triangles) {
    return { geometry: null, scale: null };
  }
  let src: Source | null = null;
  try {
    src = sourceFor(payload, mode, opts);
  } catch {
    src = null;
  }
  if (!src) return { geometry: null, scale: null };

  const { vertices, triangles } = payload;
  const dom = payload.domain_per_tri as unknown as number[];
  const nTri = triangles.length;
  const S = 1000;                                     // metres → mm
  const norm = normaliser(src.scale, src.sorted);

  const kept: number[] = [];
  for (let ti = 0; ti < nTri; ti++) {
    if (src.include(ti, dom[ti])) kept.push(ti);
  }
  if (!kept.length) return { geometry: null, scale: src.scale };

  // Area-weighted nodal average, per (vertex, material class).  Only for
  // element-constant sources — a nodal scalar is already continuous.
  let nodal: Map<number, number> | null = null;
  if (src.elem) {
    const sum = new Map<number, number>();
    const wgt = new Map<number, number>();
    for (const ti of kept) {
      const v = src.elem(ti);
      const cls = classOf(dom[ti]);
      const [ia, ib, ic] = triangles[ti];
      const area = Math.abs(
        (vertices[ib][0] - vertices[ia][0]) * (vertices[ic][1] - vertices[ia][1])
        - (vertices[ic][0] - vertices[ia][0]) * (vertices[ib][1] - vertices[ia][1])
      ) * 0.5 || 1e-12;
      for (const iv of [ia, ib, ic]) {
        const k = iv * N_CLASS + cls;
        sum.set(k, (sum.get(k) ?? 0) + v * area);
        wgt.set(k, (wgt.get(k) ?? 0) + area);
      }
    }
    nodal = new Map<number, number>();
    wgt.forEach((w, k) => nodal!.set(k, w > 0 ? (sum.get(k) ?? 0) / w : 0));
  }

  const positions = new Float32Array(kept.length * 9);
  const vals = new Float32Array(kept.length * 3);
  // The SAME column before normalisation, filled in this very pass: the shared
  // viewer's Part menu (user 2026-09-06 — "видеть только её деформации и
  // стрессы") re-normalises one part against its own range, and a second walk
  // of a 100k-triangle mesh per part switch is what would make it feel slow.
  const raw = new Float32Array(kept.length * 3);
  // Hover readout: one centroid + one DISPLAY-unit value per drawn triangle,
  // filled in the same pass that builds the geometry so a 100k-triangle mesh
  // is walked once, not twice (user 2026-09-06 asked for one shared viewer;
  // a second full pass per output switch is what would make it feel slow).
  const pcx = new Float32Array(kept.length);
  const pcy = new Float32Array(kept.length);
  const pv  = new Float32Array(kept.length);
  // Raw domain tag per drawn triangle: the max/min marker names the part it
  // landed in, and `emClassName` turns the tag into that name.
  const pcl = new Int32Array(kept.length);
  let vMin = Infinity, vMax = -Infinity;
  let p = 0, q = 0, t = 0;
  for (const ti of kept) {
    const cls = classOf(dom[ti]);
    let sx = 0, sy = 0, sv = 0;
    for (const iv of triangles[ti]) {
      positions[p++] = vertices[iv][0] * S;
      positions[p++] = vertices[iv][1] * S;
      positions[p++] = 0;
      const v = nodal ? (nodal.get(iv * N_CLASS + cls) ?? 0) : src.node!(iv);
      raw[q] = v;
      vals[q++] = norm(v);
      sx += vertices[iv][0] * S; sy += vertices[iv][1] * S; sv += v;
    }
    const av = sv / 3;
    pcx[t] = sx / 3; pcy[t] = sy / 3; pv[t] = av; pcl[t] = dom[ti];
    if (av < vMin) vMin = av;
    if (av > vMax) vMax = av;
    t++;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  g.setAttribute('aVal', new THREE.BufferAttribute(vals, 1));
  return {
    geometry: g, scale: src.scale,
    probe: { cx: pcx, cy: pcy, val: pv, cls: pcl },
    vertexValues: raw,
    vMin: Number.isFinite(vMin) ? vMin : null,
    vMax: Number.isFinite(vMax) ? vMax : null,
  };
}

/* ═══════════════════════════════════════════════════════════════════════════
 * The EM / thermal adapter for the shared viewer
 *
 * User 2026-09-06: "нужно сделать одну картинку и меню для переключения выводов
 * графиков; интерфейс должен быть единым для всех графиков — электромагнитных,
 * механических и термо".  Everything below turns a FemPayload into the ONE
 * `FieldOutput` shape `common/FieldViewer` renders — the same shape the
 * Mechanical and Modal adapters produce, so the three tabs cannot drift into
 * three viewers again.
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * Iso-A_z contour segments via per-triangle linear interpolation
 * (marching-segments on tris).  For each iso level L, find triangles where
 * A_min ≤ L ≤ A_max, locate the two edges L crosses and emit one segment.
 * Lived in FemFieldChart until 2026-09-06; it is field GEOMETRY, so it belongs
 * with the rest of the view construction and not in a host component.
 */
export function buildIsoLines(
  vertices: [number, number][],
  triangles: [number, number, number][],
  domain_per_tri: number[],
  A_z_per_node: number[],
  A_min: number,
  A_max: number,
  nLevels: number,
  S: number,        // metres → mm
  z: number,        // depth for visibility
): Float32Array {
  const DOM_OUTER = 8;
  // LINEAR distribution of iso-levels — required so the lines have UNIFORM
  // spacing inside each magnet (where A_z varies linearly with the local
  // coordinate of constant ∇A_z = constant B).  Log-spaced levels would cluster
  // lines around A=0, making them look "denser at the middle of the magnet".
  const range = Math.max(A_max - A_min, 1e-12);
  const pos: number[] = [];
  for (let k = 1; k < nLevels; k++) {
    const t  = k / nLevels;
    const L  = A_min + t * range;
    for (let ti = 0; ti < triangles.length; ti++) {
      if (domain_per_tri[ti] === DOM_OUTER) continue;
      const [a, b1, c] = triangles[ti];
      const Aa = A_z_per_node[a];
      const Ab = A_z_per_node[b1];
      const Ac = A_z_per_node[c];
      const lo = Math.min(Aa, Ab, Ac);
      const hi = Math.max(Aa, Ab, Ac);
      if (L < lo || L > hi) continue;
      const ix: [number, number][] = [];
      const ed: number[][] = [[a, b1], [b1, c], [c, a]];
      for (let e = 0; e < 3; e++) {
        const i0 = ed[e][0], i1 = ed[e][1];
        const f0 = A_z_per_node[i0] - L;
        const f1 = A_z_per_node[i1] - L;
        if (f0 * f1 > 0 || (f0 === 0 && f1 === 0)) continue;
        const denom = (f0 - f1);
        const u = denom === 0 ? 0.5 : f0 / denom;
        const x = vertices[i0][0] + u * (vertices[i1][0] - vertices[i0][0]);
        const y = vertices[i0][1] + u * (vertices[i1][1] - vertices[i0][1]);
        ix.push([x, y]);
        if (ix.length === 2) break;
      }
      if (ix.length === 2) {
        pos.push(ix[0][0] * S, ix[0][1] * S, z,
                 ix[1][0] * S, ix[1][1] * S, z);
      }
    }
  }
  return new Float32Array(pos);
}

/* `buildFluxArrows` moved to `thermal/fieldAdapters` on 2026-09-07: heat flux is
   the thermal solve's output, and it is drawn over the thermal solve's own
   sub-mesh, which this file's `FemPayload` is not. */

/** The menu entries the Simulation / animation hosts pick from.  A host passes
 *  only the ones it has (or could have) data for — J⟳ / Loss need their own
 *  multi-frame solve and are hidden inside the animation viewer, Demag only
 *  exists when demag modelling is on. */
export const EM_MENU: {
  id: string; menuLabel: string; label: string;
  group: 'Electromagnetic'; unit: string; tip: string;
}[] = [
  { id: 'Az',    menuLabel: 'A_z',   label: 'Magnetic potential A_z',
    group: 'Electromagnetic', unit: 'mWb/m',
    tip: '2-D magnetostatic vector potential at the current rotor angle — the band edges ARE the flux lines.' },
  { id: 'Bmag',  menuLabel: '|B|',   label: 'Flux density |B|',
    group: 'Electromagnetic', unit: 'mT',
    tip: 'Magnitude of the flux density per element, smoothed within each material class.' },
  { id: 'J',     menuLabel: 'J',     label: 'Source current density J',
    group: 'Electromagnetic', unit: 'A/m²',
    tip: 'Applied source current density in the windings — uniform over each slot by construction.' },
  { id: 'Jeddy', menuLabel: 'J⟳',    label: 'Eddy current density J⟳',
    group: 'Electromagnetic', unit: 'A/m²',
    tip: 'Coupled eddy-current density σ(−∂A/∂t+U) — the proximity crowding the uniform "J" view cannot show. Instant when the last Simulation run solved this operating point with the coupled eddy solve on; otherwise it runs a 10-frame transient here (~25 s) and says so.' },
  { id: 'Loss',  menuLabel: 'Loss',  label: 'Loss density',
    group: 'Electromagnetic', unit: 'W/m³',
    tip: 'Ansys-style loss-density map. Uses the last Simulation run\'s own cycle-averaged map when it matches this operating point; otherwise the single-frame analytic estimate. The header says which one you are looking at.' },
  { id: 'Demag', menuLabel: 'Demag', label: 'Demagnetisation',
    group: 'Electromagnetic', unit: '% Br',
    tip: 'Irreversible demagnetisation — per cent of Br remaining. The honest map is the run\'s worst field over the full electrical period.' },
];

/**
 * ONE electromagnetic output for the shared viewer.  `payload` null (or a mode the
 * payload cannot answer) yields a menu-only stub: the viewer shows the host's
 * placeholder and the entry is still selectable, which is how "field not solved
 * — press Re-solve" keeps working per output.
 */
export function emOutputs(
  payload: FemPayload | null,
  mode: string,
  opts: ViewOpts = {},
): FieldOutput {
  const meta = EM_MENU.find(m => m.id === mode) ?? EM_MENU[0];
  const base: FieldOutput = {
    id: meta.id, menuLabel: meta.menuLabel, label: meta.label,
    group: meta.group, unit: meta.unit, tip: meta.tip,
    scale: null, geometry: null,
  };
  if (!payload || !payload.vertices || !payload.triangles) return base;

  const view = buildFieldView(payload, mode, opts);
  const S = 1000;                                            // metres → mm

  // Class boundaries on top of the fill.  The backend sends metres, the shared
  // viewer works in mm like every other output.
  const outlines: [number, number][][] = [];
  for (const entry of payload.outlines ?? []) {
    for (const loop of entry.loops) {
      if (loop.length < 2) continue;
      outlines.push(loop.map(([x, y]) => [x * S, y * S] as [number, number]));
    }
  }

  const overlays: FieldOverlay[] = [];
  // A_z gets a SECOND, denser set of iso-lines (2× the bands) on top of the
  // shader's band edges: for the vector potential the iso-lines are the flux
  // lines and they are the point of the picture, not a decoration on it.
  if (mode === 'Az' && view.scale) {
    const iso = buildIsoLines(
      payload.vertices, payload.triangles, payload.domain_per_tri as number[],
      payload.A_z_per_node, view.scale.vmin * 1e-3, view.scale.vmax * 1e-3,
      N_BANDS * 2, S, 1.0);
    if (iso.length) overlays.push({ key: 'iso', positions: iso, color: 0x0b1220, opacity: 0.85 });
  }
  const [xmin, xmax, ymin, ymax] = payload.extent;
  return {
    ...base,
    scale: view.scale,
    geometry: view.geometry,
    probe: view.probe ?? null,
    // What the viewer's Part menu re-colours one part from (2026-09-06).
    vertexValues: view.vertexValues ?? undefined,
    // Names the material the max/min marker landed in.  Same job the mechanical
    // adapter's `part_names` lookup does — one contract, two vocabularies.
    classLabel: emClassName,
    vMin: view.vMin ?? null,
    vMax: view.vMax ?? null,
    outlines,
    outlineColor: 0x0f172a,
    outlineOpacity: 0.55,
    overlays,
    extent: [xmin * S, xmax * S, ymin * S, ymax * S],
    note: view.scale?.note,
  };
}
