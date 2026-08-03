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

// ── domain tags (backend simulation/geo_mesh.py) ──────────────────────────
export const DOM = {
  AIR: 0, STATOR: 1, COIL: 2, MAG_N: 4, ROTOR: 5, SHAFT: 6, OUTER: 8,
  MAG_S: 44, MAG_BASE: 100, COIL_BASE: 200,
} as const;

/** Material class for the smoothing split.  The SHAFT is its own class, not
 *  "air-like": under the coupled eddy solve it carries a real solved loss, and
 *  lumping it with the air smeared that loss into the bore. */
export function classOf(d: number): number {
  if (d === DOM.COIL || d >= DOM.COIL_BASE) return 4;
  if (d === DOM.MAG_N || d === DOM.MAG_S
      || (d >= DOM.MAG_BASE && d < DOM.COIL_BASE)) return 3;
  if (d === DOM.STATOR) return 1;
  if (d === DOM.ROTOR) return 2;
  if (d === DOM.SHAFT) return 5;
  return 0;                                   // air / gap / band
}
const N_CLASS = 8;

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
    if (uBands > 0.5 && uIso > 0.0) {
      float s = t * uBands;
      float d = min(fract(s), 1.0 - fract(s)) / max(fwidth(s), 1e-6);
      col = mix(col, col * 0.30, uIso * (1.0 - smoothstep(0.0, 1.0, d)));
    }
    gl_FragColor = vec4(col, 1.0);
  }
`;

// ── scale ─────────────────────────────────────────────────────────────────
export type Mapping = 'linear' | 'log' | 'rank';

export interface FieldScale {
  vmin: number;
  vmax: number;
  unit: string;
  bands: number;
  mapping: Mapping;
  /** Display-unit value at band edge k (0…bands) — what the legend prints. */
  edge: (k: number) => number;
  fmt: (v: number) => string;
  /** One line saying what the colours mean, printed under the view. */
  note: string;
}

export interface FieldView {
  geometry: THREE.BufferGeometry | null;
  scale: FieldScale | null;
}

const pctl = (arr: ArrayLike<number>, p: number): number => {
  if (!arr.length) return 0;
  const a = Float64Array.from(arr as any).sort();
  const i = Math.max(0, Math.min(a.length - 1,
    Math.floor((p / 100) * (a.length - 1))));
  return a[i];
};

const fmt2 = (v: number) => v.toFixed(2);
const fmt0 = (v: number) => v.toFixed(0);
const fmtSI = (v: number) => {
  const a = Math.abs(v);
  return a >= 1e9 ? `${(v / 1e9).toFixed(1)}G`
       : a >= 1e6 ? `${(v / 1e6).toFixed(1)}M`
       : a >= 1e3 ? `${(v / 1e3).toFixed(0)}k`
       : v.toFixed(a < 10 ? 1 : 0);
};

function linScale(vmin: number, vmax: number, unit: string, note: string,
                  fmt = fmt2, bands = N_BANDS): FieldScale {
  const span = (vmax - vmin) || 1e-12;
  return { vmin, vmax, unit, bands, mapping: 'linear', fmt, note,
           edge: (k) => vmin + span * (k / bands) };
}
function logScale(vmin: number, vmax: number, unit: string, note: string,
                  fmt = fmtSI, bands = N_BANDS): FieldScale {
  const a = Math.log10(Math.max(vmin, 1e-30));
  const b = Math.log10(Math.max(vmax, vmin * 10));
  return { vmin, vmax, unit, bands, mapping: 'log', fmt, note,
           edge: (k) => Math.pow(10, a + (b - a) * (k / bands)) };
}
function rankScale(sorted: Float64Array, unit: string, note: string,
                   fmt = fmt0, bands = N_BANDS): FieldScale {
  const q = (f: number) => sorted.length
    ? sorted[Math.min(sorted.length - 1, Math.round(f * (sorted.length - 1)))]
    : 0;
  return { vmin: q(0), vmax: q(1), unit, bands, mapping: 'rank', fmt, note,
           edge: (k) => q(k / bands) };
}

/** value → 0..1 for the LUT, under this scale. */
function normaliser(sc: FieldScale, sorted?: Float64Array): (v: number) => number {
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
  eqTemp?: boolean;
  /** Loss view: colour each material against its OWN range (reading aid). */
  perMaterialLoss?: boolean;
}

function sourceFor(payload: FemPayload, mode: string, o: ViewOpts): Source | null {
  const dom = payload.domain_per_tri as unknown as number[];
  const tris = payload.triangles;
  const nTri = tris.length;
  const interior = (_ti: number, d: number) => d !== DOM.OUTER;

  if (mode === 'Temp') {
    // The thermal payload carries its OWN sub-mesh (outer air and the gap are
    // already gone), so every triangle is drawn.  A motor under steady cooling
    // is a tight hot plateau, so the default mapping is rank-EQUALISED: colour
    // by each node's rank among all nodes, which spends the whole palette on
    // the distribution that exists rather than on the empty span above it.
    const Tn = payload.temperature_per_node ?? [];
    if (!Tn.length) return null;
    const sorted = Float64Array.from(Tn).sort();
    const note = o.eqTemp
      ? 'temperature, rank-equalised — band edges are the temperature quantiles'
      : 'temperature, linear scale';
    return {
      node: (vi) => Tn[vi] ?? sorted[0],
      include: () => true,
      scale: o.eqTemp ? rankScale(sorted, '°C', note)
                      : linScale(sorted[0], sorted[sorted.length - 1], '°C', note, fmt0),
      sorted,
    };
  }

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
                       : linScale(0, vmax, 'W/m³', note, fmtSI),
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
    const vmax = Math.max(pctl(s, 99), 1e-12);
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
    return {
      elem: (ti) => (dc ? Math.max(0, Math.min(1, dc[ti])) : 1) * 100,
      include: (_ti, d) => classOf(d) === 3,
      scale: linScale(lo * 100, hi * 100, '% Br',
                      'irreversible demagnetisation — % of Br remaining',
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
    scale: linScale(0, vmax * 1e3, 'mT', 'flux density |B|', fmt0),
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
  let p = 0, q = 0;
  for (const ti of kept) {
    const cls = classOf(dom[ti]);
    for (const iv of triangles[ti]) {
      positions[p++] = vertices[iv][0] * S;
      positions[p++] = vertices[iv][1] * S;
      positions[p++] = 0;
      const v = nodal ? (nodal.get(iv * N_CLASS + cls) ?? 0) : src.node!(iv);
      vals[q++] = norm(v);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  g.setAttribute('aVal', new THREE.BufferAttribute(vals, 1));
  return { geometry: g, scale: src.scale };
}
