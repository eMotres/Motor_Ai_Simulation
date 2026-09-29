/**
 * fieldOutput — the ONE data contract every 2-D field picture in this app hands
 * to `common/FieldViewer`.
 *
 * User 2026-09-06, looking at the Mechanical tab (three static side-by-side
 * canvases, no zoom): "сделай наш интерфейс для просмотра, чтобы можно было
 * приближать и удалять; нужно сделать одну картинку и меню для переключения
 * выводов графиков; интерфейс должен быть единым для всех графиков —
 * электромагнитных, механических и термо".
 *
 * Before this file there were four renderers of the same kind of picture:
 * FemFieldChart (three.js, zoomable, jet bands), StressMap (2-D canvas, three
 * pictures at once, Fusion rainbow, no zoom), ModeMap (a fourth canvas) — and
 * each one had its own legend.  Now there is one viewer and one legend, and a
 * host's only job is to produce `FieldOutput`s:
 *
 *   • an EM / thermal host   → `simulation/fieldView.emOutputs`
 *   • a mechanical host      → `mechanical/fieldAdapters.mechOutputs`
 *   • the modal host         → `mechanical/fieldAdapters.modeOutputs`
 *
 * Everything is in DISPLAY millimetres by the time it reaches this contract
 * (the EM payload is metres and its adapter scales it), so the viewer never has
 * to know which solver a picture came from.
 */
import * as THREE from 'three';
import { PART_COLORS } from '../../lib/partColors';
import {
  bandColor, linScale, logScale, N_BANDS, normaliser, pctl, rankScale,
} from '../simulation/fieldView';
import type { FieldScale, FieldProbeData } from '../simulation/fieldView';

export type { FieldScale, FieldProbeData } from '../simulation/fieldView';

/** The domain a quantity belongs to — the group heading in the output menu. */
export type FieldGroup = 'Electromagnetic' | 'Thermal' | 'Mechanical' | 'Modal';

/** Menu order.  Fixed here so every tab lists its groups the same way round. */
export const GROUP_ORDER: FieldGroup[] = [
  'Electromagnetic', 'Thermal', 'Mechanical', 'Modal',
];

/** Extra line geometry drawn over the fill: flux arrows, contact state, the
 *  denser A_z flux lines.  `colors` (rgb 0..1 per vertex) wins over `color`. */
export interface FieldOverlay {
  key?: string;
  positions: Float32Array;      // xyz triplets, consumed as LineSegments
  colors?: Float32Array;        // rgb per vertex, 0..1
  color?: number;               // flat colour when `colors` is absent
  opacity?: number;
  width?: number;
}

/**
 * ONE quantity, ready to draw.  Entries the host cannot currently draw are
 * legal and normal: `geometry: null` makes a menu-only stub, which is how "field
 * not solved — press Re-solve" stays a per-output state instead of blanking the
 * whole menu.
 */
export interface FieldOutput {
  /** stable id — the host's own mode/view key, and the menu's value */
  id: string;
  /** short name in the dropdown (A_z, |B|, Von Mises, …) */
  menuLabel: string;
  /** full name in the header line */
  label: string;
  group: FieldGroup;
  unit?: string;
  /** the ⓘ sentence on the header */
  tip?: string;

  /** colour scale — drives the fill AND the colour bar, so they cannot drift */
  scale: FieldScale | null;
  /** position + normalised `aVal` per vertex; null = nothing to draw */
  geometry: THREE.BufferGeometry | null;
  /** one RGB (0..255) per band.  Default: the jet band centres every EM view
   *  already uses, so a host only passes this for a banded semantic scale
   *  (the safety factor's red / green / blue). */
  palette?: [number, number, number][];
  /** darkness of the iso-line drawn on each band edge; 0 disables it */
  iso?: number;

  /** closed loops, mm — material/part boundaries drawn over the fill */
  outlines?: [number, number][][];
  outlineColor?: number;
  outlineOpacity?: number;
  /** dashed reference loops, mm — the UNDEFORMED silhouette under a deformed
   *  picture, so the growth is the sliver that sticks out */
  ghostOutlines?: [number, number][][];

  overlays?: FieldOverlay[];

  /** [xmin, xmax, ymin, ymax] mm — what "Fit" fits */
  extent?: [number, number, number, number];

  /** true extremes of the drawn field, display units (NOT the clipped colour
   *  range) — the header prints these */
  vMin?: number | null;
  vMax?: number | null;
  /** anything else the header should say about this output, e.g. "p99.5 clip" */
  statText?: string;

  /** value-at-cursor source — AND the source of the max / min markers, which
   *  read the raw display-unit values out of it, never the normalised colour */
  probe?: FieldProbeData | null;
  /** name of the part / material a `probe.cls` tag belongs to.  The EM adapter
   *  maps a domain tag through `emClassName`, the mechanical one through the
   *  payload's `part_names`; the viewer only prints what comes back.
   *  User 2026-09-06: the max marker has to say which part the peak is in. */
  classLabel?: (cls: number) => string | undefined;
  /**
   * The DISPLAY-unit value behind every drawn VERTEX, in geometry order (3 per
   * drawn triangle) — i.e. `aVal` BEFORE it was normalised against `scale`.
   *
   * User 2026-09-06: "нужно ещё добавить в вывод название частей ротора и
   * статора, чтобы можно было смотреть отдельно на каждую часть и видеть только
   * её деформации и стрессы".  Showing one part alone is not a matter of hiding
   * triangles: the whole point is that the part gets its OWN colour range (a
   * magnet's stress read against the rotor lips' 5 GPa is one flat colour band),
   * and re-normalising needs the raw values back.  `aVal` cannot be inverted —
   * it is clipped at both percentile ends — so the builders keep the raw column
   * alongside it and `restrictToParts` re-normalises from here.
   */
  vertexValues?: Float32Array;

  /** one line under the picture saying what the colours mean */
  note?: string;
  /** when there is nothing to draw but the reason is known, say it here */
  blank?: string;
}

/** A menu entry with no data behind it yet. */
export function outputStub(
  id: string, menuLabel: string, label: string, group: FieldGroup,
  extra: Partial<FieldOutput> = {},
): FieldOutput {
  return { id, menuLabel, label, group, scale: null, geometry: null, ...extra };
}

/** The default ramp: the jet band CENTRES — bit-identical to what the banded
 *  shader painted before there was a palette uniform (`jet((floor(t*n)+0.5)/n)`
 *  is exactly `bandColor(k, n)`), so no EM view changed colour on 2026-09-06. */
export function jetPalette(bands = N_BANDS): [number, number, number][] {
  return Array.from({ length: bands }, (_, k) => bandColor(k, bands));
}

/** Flatten closed loops (mm) into LineSegments positions at depth `z`. */
export function loopsToSegments(loops: [number, number][][], z: number): Float32Array {
  const arr: number[] = [];
  for (const loop of loops) {
    if (loop.length < 2) continue;
    for (let i = 0; i < loop.length; i++) {
      const a = loop[i];
      const b = loop[(i + 1) % loop.length];
      arr.push(a[0], a[1], z, b[0], b[1], z);
    }
  }
  return new Float32Array(arr);
}

/** Bounding box of a vertex list, mm. */
export function extentOf(
  vertices: ReadonlyArray<readonly [number, number]>,
): [number, number, number, number] {
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  for (const [x, y] of vertices) {
    if (x < xmin) xmin = x; if (x > xmax) xmax = x;
    if (y < ymin) ymin = y; if (y > ymax) ymax = y;
  }
  if (!Number.isFinite(xmin)) return [-1, 1, -1, 1];
  return [xmin, xmax, ymin, ymax];
}

export interface MeshGeometryInput {
  /** mm (already scaled by the adapter) */
  vertices: ReadonlyArray<readonly [number, number]>;
  triangles: ReadonlyArray<readonly [number, number, number]>;
  /** the scale the values are normalised against */
  scale: FieldScale;
  /** element-constant field → area-weighted nodal average WITHIN a class */
  valuesPerTri?: ArrayLike<number>;
  /** already-continuous nodal field → read straight off the node */
  valuesPerNode?: ArrayLike<number>;
  /** smoothing class per triangle (a part id is fine — it is remapped).  A
   *  vertex is duplicated per (vertex, class) so one part never bleeds its
   *  value into the next: smooth inside a part, crisp at every boundary. */
  classPerTri?: ArrayLike<number>;
  /** which triangles are drawn at all */
  include?: (ti: number) => boolean;
  /** per node, same units as `vertices` once multiplied by `deform` */
  displacement?: ReadonlyArray<readonly [number, number]>;
  deform?: number;
  /** rank mapping needs its sorted sample */
  sorted?: Float64Array;
  /** Build the colour scale from the DRAWN nodal values instead of handing one
   *  in.  The values that reach the screen are the averaged nodal field, not
   *  the element field the adapter starts from, so an adapter that wants its
   *  bar to end exactly where the picture ends (ANSYS/Fusion convention, user
   *  2026-09-10) cannot compute that range before this function has averaged.
   *  Called once, after the nodal pass, before normalisation. */
  scaleFrom?: (nodal: Float32Array) => FieldScale;
}

/**
 * The generic counterpart of `fieldView.buildFieldView`: same nodal-averaging,
 * same per-(vertex, class) split, same normalised `aVal` attribute — but for a
 * mesh that is NOT a FemPayload (mechanical parts, mode shapes).  One geometry
 * layout for every output is one less thing that can differ between them.
 */
export function buildMeshGeometry(o: MeshGeometryInput): {
  geometry: THREE.BufferGeometry | null;
  probe: FieldProbeData | null;
  /** raw display-unit value per drawn vertex — see `FieldOutput.vertexValues` */
  vertexValues: Float32Array | null;
  vMin: number | null;
  vMax: number | null;
  /** the scale the values were normalised against — `o.scale` unless
   *  `o.scaleFrom` built one from the drawn nodal field */
  scale: FieldScale;
} {
  const { vertices, triangles } = o;
  const nTri = triangles.length;
  const empty = { geometry: null, probe: null, vertexValues: null,
                  vMin: null, vMax: null, scale: o.scale };
  if (!nTri || !vertices.length) return empty;

  const kept: number[] = [];
  for (let ti = 0; ti < nTri; ti++) if (!o.include || o.include(ti)) kept.push(ti);
  if (!kept.length) return empty;

  // Classes are remapped to a dense 0..K-1 so the (vertex, class) key stays a
  // small integer even when the host's domain ids are sparse part tags.
  const clsIdx = new Map<number, number>();
  const cls = (ti: number): number => {
    const raw = o.classPerTri ? o.classPerTri[ti] : 0;
    let i = clsIdx.get(raw);
    if (i === undefined) { i = clsIdx.size; clsIdx.set(raw, i); }
    return i;
  };
  for (const ti of kept) cls(ti);
  const K = Math.max(clsIdx.size, 1);

  let nodal: Map<number, number> | null = null;
  if (o.valuesPerTri) {
    const vt = o.valuesPerTri;
    const sum = new Map<number, number>();
    const wgt = new Map<number, number>();
    for (const ti of kept) {
      const v = ti < vt.length ? vt[ti] : 0;
      const c = cls(ti);
      const [ia, ib, ic] = triangles[ti];
      const area = Math.abs(
        (vertices[ib][0] - vertices[ia][0]) * (vertices[ic][1] - vertices[ia][1])
        - (vertices[ic][0] - vertices[ia][0]) * (vertices[ib][1] - vertices[ia][1])
      ) * 0.5 || 1e-12;
      for (const iv of [ia, ib, ic]) {
        const k = iv * K + c;
        sum.set(k, (sum.get(k) ?? 0) + v * area);
        wgt.set(k, (wgt.get(k) ?? 0) + area);
      }
    }
    nodal = new Map<number, number>();
    wgt.forEach((w, k) => nodal!.set(k, w > 0 ? (sum.get(k) ?? 0) / w : 0));
  }

  const vn = o.valuesPerNode;
  const disp = o.displacement;
  const kD = disp && o.deform ? o.deform : 0;

  const positions = new Float32Array(kept.length * 9);
  const vals = new Float32Array(kept.length * 3);
  // The same column BEFORE normalisation.  Filled in this pass rather than
  // recomputed later: `restrictToParts` re-normalises the visible parts against
  // their own range, and a second walk of a 100k-triangle mesh per eye toggle is
  // exactly what would make the part tree feel slow.
  const raw = new Float32Array(kept.length * 3);
  const pcx = new Float32Array(kept.length);
  const pcy = new Float32Array(kept.length);
  const pv = new Float32Array(kept.length);
  // RAW class tag (a part id here), not the dense remap: the host's
  // `classLabel` names its own tags, and that is what the max/min marker prints.
  const pcl = new Int32Array(kept.length);
  let vMin = Infinity, vMax = -Infinity;
  let p = 0, q = 0, t = 0;
  for (const ti of kept) {
    const c = cls(ti);
    let sx = 0, sy = 0, sv = 0;
    for (const iv of triangles[ti]) {
      const x = vertices[iv][0] + (kD ? kD * disp![iv][0] : 0);
      const y = vertices[iv][1] + (kD ? kD * disp![iv][1] : 0);
      positions[p++] = x; positions[p++] = y; positions[p++] = 0;
      const v = nodal ? (nodal.get(iv * K + c) ?? 0) : (vn ? vn[iv] : 0);
      raw[q++] = v;
      sx += x; sy += y; sv += v;
    }
    const av = sv / 3;
    pcx[t] = sx / 3; pcy[t] = sy / 3; pv[t] = av;
    pcl[t] = o.classPerTri ? o.classPerTri[ti] : 0;
    if (av < vMin) vMin = av;
    if (av > vMax) vMax = av;
    t++;
  }

  // The scale AFTER the nodal pass, so an adapter can end its bar exactly
  // where the drawn field ends (see `scaleFrom`), then one cheap pass to
  // normalise — no second walk of the mesh.
  const scale = o.scaleFrom ? o.scaleFrom(raw) : o.scale;
  const norm = normaliser(scale, o.sorted);
  for (let i = 0; i < raw.length; i++) vals[i] = norm(raw[i]);

  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  g.setAttribute('aVal', new THREE.BufferAttribute(vals, 1));
  return {
    geometry: g,
    probe: { cx: pcx, cy: pcy, val: pv, cls: pcl },
    vertexValues: raw,
    vMin: Number.isFinite(vMin) ? vMin : null,
    vMax: Number.isFinite(vMax) ? vMax : null,
    scale,
  };
}

/* ── where the field peaks ────────────────────────────────────────────────── */

/** One extremum of a drawn field, in DISPLAY units and DISPLAY coordinates. */
export interface FieldExtremum {
  v: number;
  /** mm — the DEFORMED position when the picture is deformed, because that is
   *  where the marker has to sit on the shape the user is looking at */
  x: number;
  y: number;
  /** mm from the axis, and degrees CCW from +x — how a motor engineer says
   *  "where": the header prints "max 5408 MPa @ r 61.0 mm, 17.3°" */
  r: number;
  deg: number;
  /** raw class tag, for the host's `classLabel` */
  cls?: number;
}

/**
 * The max and the min of what is actually drawn.
 *
 * User 2026-09-06: "нужно ещё, чтобы можно было подсвечивать точки максимальных
 * деформаций, напряжений и полей".  Read off `probe` — the RAW per-triangle
 * display-unit values and their display-space centroids — and NEVER off the
 * normalised `aVal`: that one is clipped at the percentile ends, so on any
 * clipped view (von Mises, |B|, Loss) whole regions share t = 1 and the "max"
 * would be wherever the first of them happened to be indexed.  It is also the
 * same array the header's max/min come from, so the number by the marker and
 * the number in the header cannot disagree.
 */
export function extremaOf(
  p: FieldProbeData | null | undefined,
  vv?: Float32Array | null,
  positions?: ArrayLike<number> | null,
): { max: FieldExtremum | null; min: FieldExtremum | null } {
  if (!p || !p.val.length) return { max: null, min: null };

  // THE DRAWN FIELD'S OWN ENDS, read on the nodes it is drawn from.
  //
  // User 2026-09-10: "как нам теперь объяснять пользователям эти две разные
  // цифры 1728 и 1426? нас не поймут, везде и в Ansys и Fusion полное
  // соответствие".  They are right, and one of those two numbers was ours to
  // fix.  The picture is drawn from NODAL values (each element's value
  // area-averaged onto the nodes of its own part, which is what ANSYS and
  // Fusion call an averaged plot).  The marker, though, used to be read off
  // `probe.val` — the mean of a triangle's three nodal values — which is a
  // SECOND smoothing on top, and one no other tool applies.  On the live band
  // that second step alone took the peak from 1559 to 1430: a number nobody
  // could reproduce anywhere, because it is not a statistic anyone quotes.
  // So the marker now reads the drawn nodal field, and the colour bar is built
  // over the same array: the brightest node on the picture IS the number
  // beside it.
  if (vv && positions && vv.length === p.val.length * 3
      && positions.length >= vv.length * 3) {
    let hi = 0, lo = 0;
    for (let i = 1; i < vv.length; i++) {
      if (vv[i] > vv[hi]) hi = i;
      if (vv[i] < vv[lo]) lo = i;
    }
    const atNode = (i: number): FieldExtremum => {
      const x = positions[i * 3], y = positions[i * 3 + 1];
      const deg = (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
      return { v: vv[i], x, y, r: Math.hypot(x, y), deg,
               cls: p.cls ? p.cls[Math.floor(i / 3)] : undefined };
    };
    return { max: atNode(hi), min: atNode(lo) };
  }

  // Fallback for an output with no raw vertex column (an overlay-only view).
  let hi = 0, lo = 0;
  for (let i = 1; i < p.val.length; i++) {
    if (p.val[i] > p.val[hi]) hi = i;
    if (p.val[i] < p.val[lo]) lo = i;
  }
  const at = (i: number): FieldExtremum => {
    const x = p.cx[i], y = p.cy[i];
    const deg = (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
    return { v: p.val[i], x, y, r: Math.hypot(x, y), deg,
             cls: p.cls ? p.cls[i] : undefined };
  };
  return { max: at(hi), min: at(lo) };
}

/* ── the mesh overlay ─────────────────────────────────────────────────────── */

/**
 * Triangle edges of a drawn field geometry, as LineSegments positions.
 *
 * User 2026-09-06: "также чтобы была возможность отображения сетки или без
 * неё".  The field geometry is NON-INDEXED (3 corners per triangle, duplicated
 * per material class — see `buildMeshGeometry`), so drawing three edges per
 * triangle would emit every interior edge twice: 6 vertices per triangle, which
 * on a 100k-triangle mesh is 1.8 M floats of line data and a visibly heavier
 * line than the same edge drawn once.
 *
 * So: two hashing passes, once per geometry, never per frame.  Corners are
 * welded by their quantised coordinate (0.1 µm — finer than any mesh this app
 * builds, coarse enough that a Float32 round-trip cannot split one corner in
 * two), then each undirected edge is emitted once.  ~1.5 edges per triangle
 * instead of 3, and the whole thing is memoised on the geometry: the camera
 * never touches it.
 */
export function buildEdgeGeometry(
  src: THREE.BufferGeometry | null | undefined, z = 0.5,
): THREE.BufferGeometry | null {
  const attr = src?.getAttribute('position') as THREE.BufferAttribute | undefined;
  if (!attr) return null;
  const a = attr.array as ArrayLike<number>;
  const nTri = Math.floor(a.length / 9);
  if (!nTri) return null;

  const Q = 1e4;                       // mm → 0.1 µm buckets
  const OFF = 1 << 23;                 // keep the key positive for negative mm
  const SPAN = 1 << 24;
  const idOf = new Map<number, number>();
  const vid = new Int32Array(nTri * 3);
  const cx: number[] = [];
  const cy: number[] = [];
  for (let i = 0; i < nTri * 3; i++) {
    const x = a[i * 3], y = a[i * 3 + 1];
    const key = (Math.round(x * Q) + OFF) * SPAN + (Math.round(y * Q) + OFF);
    let id = idOf.get(key);
    if (id === undefined) { id = cx.length; idOf.set(key, id); cx.push(x); cy.push(y); }
    vid[i] = id;
  }
  const n = cx.length;
  const seen = new Set<number>();
  const out: number[] = [];
  for (let t = 0; t < nTri; t++) {
    const v0 = vid[t * 3], v1 = vid[t * 3 + 1], v2 = vid[t * 3 + 2];
    const e: [number, number][] = [[v0, v1], [v1, v2], [v2, v0]];
    for (const [p, q] of e) {
      if (p === q) continue;                       // degenerate, nothing to draw
      const lo = p < q ? p : q, hi = p < q ? q : p;
      const k = lo * n + hi;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(cx[lo], cy[lo], z, cx[hi], cy[hi], z);
    }
  }
  if (!out.length) return null;
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(out), 3));
  return g;
}

/* ── the parts of a picture (the component tree's model) ──────────────────── */

/**
 * User 2026-09-06: "нужно ещё добавить в вывод название частей ротора и
 * статора, чтобы можно было смотреть отдельно на каждую часть и видеть только
 * её деформации и стрессы".
 *
 * The second half of that sentence is the hard half.  Hiding the other parts is
 * a triangle filter; "видеть только её" means the picture has to be REBUILT for
 * the part — its own colour range, its own max/min, its own mesh, its own
 * cursor readout.  On the Ø200 rotor the iron lips run to ~5 GPa and the magnets
 * to ~40 MPa: on the shared scale every magnet element is band 0, so "look at
 * the magnet" on the full picture shows one flat colour and answers nothing.
 *
 * That is why this lives HERE, in the shared contract, and not in a host: it is
 * the same operation for a stress map, a |B| map and a mode shape, and it is
 * driven entirely off data every output already carries (`probe.cls` +
 * `classLabel`) plus the raw value column added the same day
 * (`FieldOutput.vertexValues`).
 */
export interface FieldPart {
  /** the part's NAME — the tree row's key, and what makes a selection survive an
   *  output switch (`probe.cls` tags do not: the EM path tags every magnet with
   *  its own domain id, and a different physics numbers its parts differently) */
  key: string;
  label: string;
  /** the heading it is listed under */
  group: 'Rotor' | 'Stator' | 'Other';
  /** every raw class tag that carries this name (one EM domain per magnet /
   *  coil, a single part id on the mechanical side) */
  tags: number[];
  /** drawn triangles — how much of the picture this part is */
  n: number;
  /** the swatch the shared component tree draws next to it.  Straight out of
   *  `lib/partColors`, so the field viewer's tree and the 3-D one give the same
   *  part the same colour (user 2026-09-06: "то же самое дерево"). */
  colour: string;
}

/** UNNAMED air is not a part.  On the EM views the whole outside of the machine
 *  is one anonymous "air" class with no field worth reading on its own, so it is
 *  never offered — the same rule the outline builder applies when it refuses to
 *  outline class 0.
 *
 *  "air gap" left this set on 2026-09-07.  It used to be here because no payload
 *  could tell one lump of air from another; the thermal solve now names five of
 *  them (insulation, wire enamel, wire coating, air gap, pocket air), every one of
 *  them a SOLVED domain with its own conductivity and its own temperature, and a
 *  part the solve conducts through has to be a row the user can switch off.
 *  Nothing else in the app names a domain "air gap", so removing it costs the EM
 *  and mechanical trees nothing. */
const NOT_A_PART = new Set(['air', 'gap', 'band', 'outer']);

/** Rotor or stator, from the part's own name.  Every vocabulary in the app is
 *  covered: the EM one (`fieldView.emClassName` — stator iron / rotor iron /
 *  magnet / copper / shaft), the mechanical one (the payload's `part_names` —
 *  rotor / magnet / sleeve / shaft / stator) and the thermal one (insulation /
 *  wire enamel / wire coating / air gap / pocket air).
 *
 *  The insulation and the wire coating live in the SLOT, so they belong under the
 *  stator with the winding they insulate; the rotor's pocket air belongs with
 *  the rotor it is trapped in.  The air gap belongs to neither — that is the
 *  whole point of it — so it stays under Other. */
export function partGroup(name: string): FieldPart['group'] {
  const s = name.toLowerCase();
  if (/^air gap$/.test(s)) return 'Other';
  if (/liner|enamel|insulation|coating/.test(s)) return 'Stator';
  if (/pocket/.test(s)) return 'Rotor';
  if (/rotor|magnet|sleeve|shaft/.test(s)) return 'Rotor';
  if (/stator|coil|copper|winding/.test(s)) return 'Stator';
  return 'Other';
}

export const PART_GROUP_ORDER: FieldPart['group'][] = ['Rotor', 'Stator', 'Other'];

/** The tree swatch for a part NAME, in every vocabulary (EM: "stator iron",
 *  "copper", …; mechanical: "rotor", "sleeve", …; thermal: "insulation",
 *  "air gap", …).  ORDER IS THE CONTRACT: the specific names are tested before
 *  the generic ones, so "wire enamel" cannot fall into the copper branch on the
 *  word "wire" and "pocket air" cannot fall into the rotor branch.  Sleeve is
 *  tested before rotor because it is spelled "retaining sleeve" in places, and
 *  magnet before rotor for the same reason.
 *
 *  The liner and the enamel take the colours the 3-D tree already gives them
 *  (`viewer3d/ComponentTree`'s "Insulation" / "Wire enamel" rows) rather than
 *  new ones: the user's rule for this tree is that it IS the 3-D tree, and one
 *  part in two colours in two trees is the bug that rule exists to prevent.
 *
 *  Exported for the node test — the mapping is the whole visual contract of the
 *  Part tree, and it is a pure string→string function. */
export function partColour(name: string): string {
  const s = name.toLowerCase();
  if (/wire coating|impregnation/.test(s)) return PART_COLORS.slotFill;
  if (/pocket air/.test(s))            return PART_COLORS.pocketAir;
  if (/air gap|gap air/.test(s))       return PART_COLORS.gapAir;
  if (/enamel/.test(s))                return PART_COLORS.enamel;
  if (/liner|insulation/.test(s))      return PART_COLORS.slotLiner;
  if (/sleeve|bandage|retain/.test(s)) return PART_COLORS.sleeve;
  if (/shaft/.test(s))                 return PART_COLORS.shaft;
  if (/magnet/.test(s))                return PART_COLORS.magnetN;
  if (/coil|copper|wind/.test(s))      return PART_COLORS.copper;
  if (/stator/.test(s))                return PART_COLORS.statorIron;
  if (/rotor/.test(s))                 return PART_COLORS.rotorIron;
  return PART_COLORS.statorIron;
}

/** The parts present in ONE output, named and grouped.  Empty when the output
 *  cannot be restricted (no geometry, no class tags, no names, or no raw value
 *  column) — the viewer then shows no Part control at all rather than one that
 *  would do nothing. */
export function partsOf(out: FieldOutput | null | undefined): FieldPart[] {
  const cls = out?.probe?.cls;
  if (!out || !cls || !out.geometry || !out.vertexValues || !out.classLabel) return [];
  const byTag = new Map<number, number>();
  for (let i = 0; i < cls.length; i++) byTag.set(cls[i], (byTag.get(cls[i]) ?? 0) + 1);
  const byName = new Map<string, FieldPart>();
  byTag.forEach((n, tag) => {
    const name = out.classLabel!(tag);
    if (!name || NOT_A_PART.has(name.trim().toLowerCase())) return;
    const e = byName.get(name);
    if (e) { e.tags.push(tag); e.n += n; }
    else byName.set(name, { key: name, label: name, group: partGroup(name),
                            tags: [tag], n, colour: partColour(name) });
  });
  // Tags ascending: the tree numbers a part's per-item rows ("Magnet 3") off
  // this order, and mesh-traversal order would renumber them per output.
  byName.forEach(p => p.tags.sort((a, b) => a - b));
  return [...byName.values()].sort((a, b) =>
    PART_GROUP_ORDER.indexOf(a.group) - PART_GROUP_ORDER.indexOf(b.group)
    || a.label.localeCompare(b.label));
}

/**
 * Rebuild `sc` over `vals` AS THE SAME KIND OF SCALE — that is the contract:
 * a part view differs from the full view only in what it is computed over, not
 * in how it is coloured (linear stays linear, a diverging range stays symmetric
 * about zero so jet's green midpoint keeps meaning "0", a rank-equalised map
 * stays equalised, a log map stays log).
 *
 * `ends` — when given — is what the LINEAR range is taken from: the drawn
 * per-triangle values, the same array `extremaOf` reads the max / min markers
 * off.  User 2026-09-10: "надо, чтобы шкала автоматом перестраивалась по
 * min max именно выбранных в дереве элементов, а не по всем".  It already did,
 * but over `vals` — the per-VERTEX values, percentile-clipped the way the full
 * view was — so the bar's ends never equalled the numbers the markers printed
 * beside them (1463 on the bar over "max 1426 MPa"), and a rebuilt scale that
 * disagrees with the picture's own numbers reads as a scale that was not
 * rebuilt.  Off a selection the ends are the selection's own honest min and
 * max: the bar bottom IS the min marker and the bar top IS the max marker.
 * The handful of nodal values outside that span saturate in the end bands, and
 * `singular` says so when one element is carrying the top of the range.
 *
 * `trueMin/trueMax` are the full output's HONEST extremes, and they are what
 * tells us whether the adapter percentile-CLIPPED: a scale that stops short of
 * the true maximum was clipped on purpose (von Mises at a re-entrant corner,
 * |B| at a tooth tip).  That still decides the FULL view's rescale; a selection
 * shows its own ends instead, because isolating a part in the tree is asking
 * for that part's own range and a p99.5 clip would throw away the peak it was
 * isolated to see.
 */
function rescaleLike(
  sc: FieldScale, vals: Float32Array,
  trueMin: number | null | undefined, trueMax: number | null | undefined,
  ends?: ArrayLike<number> | null,
): { scale: FieldScale; sorted?: Float64Array; clipped: boolean;
     singular?: number } {
  // FIXED bands are THRESHOLDS, not a range: the safety factor's red/green edge
  // is 2.0 whichever part you are looking at, and the demag bands mean per cent
  // of Br.  Rescaling them would move the line a part is judged against, which
  // is the one thing this feature may never do.
  if (sc.mapping === 'edges' || !vals.length) return { scale: sc, clipped: false };

  const sorted = Float64Array.from(vals).sort();
  const n = sorted.length;
  const q = (f: number) =>
    sorted[Math.min(n - 1, Math.max(0, Math.round(f * (n - 1))))];
  const lo0 = sorted[0], hi0 = sorted[n - 1];

  if (sc.mapping === 'rank') {
    // Equalised against THIS part's distribution — the band edges become the
    // part's own quantiles, which is the whole point of an equalised map.
    return { scale: rankScale(sorted, sc.unit, sc.note, sc.fmt, sc.bands),
             sorted, clipped: false };
  }

  if (sc.mapping === 'log') {
    // Same 5 / 99.5 percentiles of the POSITIVE values the Loss view builds its
    // shared log axis from (a zero has no place on a log scale).
    const pos = sorted.filter(v => v > 0);
    const hi = Math.max(pctl(pos, 99.5), 1e-30);
    const lo = Math.max(pctl(pos, 5), hi * 1e-4);
    return { scale: logScale(lo, hi, sc.unit, sc.note, sc.fmt, sc.bands),
             clipped: hi0 > hi * 1.02 };
  }

  const clipHi = trueMax != null && Number.isFinite(trueMax)
    && sc.vmax < trueMax - Math.abs(trueMax) * 1e-6;
  const clipLo = trueMin != null && Number.isFinite(trueMin)
    && sc.vmin > trueMin + Math.abs(trueMin) * 1e-6;
  let lo = clipLo ? q(0.005) : lo0;
  let hi = clipHi ? q(0.995) : hi0;
  let singular: number | undefined;
  if (ends && ends.length) {
    // A selection's own ends, unclipped — see this function's header.
    const es = Float64Array.from(ends as ArrayLike<number>).sort();
    lo = es[0];
    hi = es[es.length - 1];
    const p995 = es[Math.min(es.length - 1,
                             Math.round(0.995 * (es.length - 1)))];
    // One element ten per cent above the 99.5th percentile is a singularity
    // carrying the top band on its own; the picture still shows it, but the
    // stat line says where the rest of the field actually stops.
    if (hi > p995 * 1.1 && p995 > 0) singular = p995;
  }
  if (!(hi > lo)) hi = lo + Math.max(Math.abs(lo) * 1e-6, 1e-12);
  if (sc.vmax > 0 && Math.abs(sc.vmin + sc.vmax) <= sc.vmax * 1e-9) {
    // Symmetric by construction (A_z, J, the principal / hoop / radial
    // stresses): keep it symmetric or zero stops being the green midpoint.
    const m = Math.max(Math.abs(lo), Math.abs(hi), 1e-12);
    lo = -m; hi = m;
  } else if (sc.vmin === 0 && sc.vmax > 0) {
    lo = 0;                       // magnitude quantity — its floor is zero
  }
  return { scale: linScale(lo, hi, sc.unit, sc.note, sc.fmt, sc.bands),
           clipped: !ends && hi0 > hi * 1.02, singular };
}

/** "magnet", "magnet + shaft", "4 parts" — how the note and the blank message
 *  name what is on screen without turning into a paragraph (UI rule: one short
 *  line). */
function nameList(names: string[]): string {
  if (names.length === 1) return names[0];
  if (names.length <= 3) return names.join(' + ');
  return `${names.length} parts`;
}

/**
 * The VISIBLE parts of an output, as a complete `FieldOutput` the viewer draws
 * exactly as it draws any other — so every feature (Max markers, Mesh overlay,
 * cursor readout, colour bar, Fit) restricts itself for free.
 *
 * User 2026-09-06 replaced the viewer's single-choice Part dropdown with the
 * app's own component tree ("используй то же самое дерево, которое у нас уже
 * есть, чтобы всё было универсально"), and a tree of eye toggles is not a
 * single choice: any SUBSET of the parts can be on.  Hence a set of names
 * rather than one `FieldPart` — everything else (the per-part re-scaling that
 * is the whole point, the ghost outlines, the honest clip note) is unchanged.
 *
 * @param visible  part NAMES to keep — `null` means everything, and so does a
 *                 set that happens to cover the whole picture: both return the
 *                 ORIGINAL output untouched, overlays and outlines included.
 * @param hiddenTags  individual class tags switched off in the tree's per-item
 *                 rows (one EM domain per magnet / coil).  Only the EM path
 *                 distinguishes them; the mechanical one has a single tag per
 *                 part and never passes this.
 *
 * Cost: one counting pass over the class tags, then one fill pass that copies
 * the kept triangles' positions, raw values and probe columns into
 * exactly-sized typed arrays.  Nothing is re-derived from the source mesh, and
 * the whole thing is memoised per (output, selection) by the caller — a 100k
 * triangle EM mesh restricts in a few milliseconds and never per frame.
 */
export function restrictToParts(
  out: FieldOutput,
  visible: ReadonlySet<string> | null,
  hiddenTags?: ReadonlySet<number> | null,
): FieldOutput {
  // `out` can be undefined at runtime — a host that renders the viewer before it
  // has any outputs falls through `outputs[0]`, which is why every read of it in
  // FieldViewer is optional-chained.  Guard before touching it.
  if (!out || !visible) return out;
  const probe = out.probe;
  const cls = probe?.cls;
  const vv = out.vertexValues;
  const sc = out.scale;
  const nameOf = out.classLabel;
  if (!probe || !cls || !out.geometry || !vv || !sc || !nameOf) return out;
  const posAttr = out.geometry.getAttribute('position') as THREE.BufferAttribute | undefined;
  const src = posAttr?.array as ArrayLike<number> | undefined;
  if (!src) return out;

  // Membership decided ONCE per distinct tag — the EM path tags every magnet and
  // every coil with its own domain id, and this question is asked once per
  // triangle, on meshes of 100k of them.
  const decided = new Map<number, boolean>();
  const keptNames = new Set<string>();
  const inPart = (t: number): boolean => {
    let k = decided.get(t);
    if (k === undefined) {
      const name = nameOf(t);
      k = !!name && visible.has(name) && !hiddenTags?.has(t);
      if (k) keptNames.add(name!);
      decided.set(t, k);
    }
    return k;
  };

  const nTri = cls.length;
  let m = 0;
  for (let i = 0; i < nTri; i++) if (inPart(cls[i])) m++;
  // Nothing selected is drawn in this output (J is windings-only, Demag is
  // magnets-only): say so rather than showing an empty canvas.
  if (!m) return { ...out, geometry: null, probe: null, vertexValues: undefined,
                   blank: visible.size
                     ? `${nameList([...visible])} — not drawn on this output`
                     : 'nothing selected — switch a part back on in the tree' };
  // The output IS this selection already — restricting would produce the same
  // picture, so hand back the original and keep its overlays and outlines.
  if (m === nTri) return out;
  const what = nameList([...keptNames]);

  const positions = new Float32Array(m * 9);
  const rawVals = new Float32Array(m * 3);
  const pcx = new Float32Array(m);
  const pcy = new Float32Array(m);
  const pv = new Float32Array(m);
  const pcl = new Int32Array(m);
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  let vMin = Infinity, vMax = -Infinity;
  let w = 0;
  for (let i = 0; i < nTri; i++) {
    if (!inPart(cls[i])) continue;
    const s = i * 9, d = w * 9;
    for (let k = 0; k < 9; k++) positions[d + k] = src[s + k];
    for (let k = 0; k < 3; k++) {
      const x = src[s + k * 3], y = src[s + k * 3 + 1];
      if (x < xmin) xmin = x; if (x > xmax) xmax = x;
      if (y < ymin) ymin = y; if (y > ymax) ymax = y;
      rawVals[w * 3 + k] = vv[i * 3 + k];
    }
    const v = probe.val[i];
    pcx[w] = probe.cx[i]; pcy[w] = probe.cy[i]; pv[w] = v; pcl[w] = cls[i];
    if (v < vMin) vMin = v;
    if (v > vMax) vMax = v;
    w++;
  }

  const r = rescaleLike(sc, rawVals, out.vMin, out.vMax, rawVals);
  const norm = normaliser(r.scale, r.sorted);
  const vals = new Float32Array(m * 3);
  for (let i = 0; i < m * 3; i++) vals[i] = norm(rawVals[i]);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  g.setAttribute('aVal', new THREE.BufferAttribute(vals, 1));

  // A clip note from the FULL picture would name the wrong number here, so drop
  // it and re-state it only if THIS part is clipped.  Anything else the host put
  // in the stat line (the case maximum, "deform ×") is about the case, not the
  // colour range, and stays.
  let statText = (out.statText ?? '').replace(/\s*·?\s*p[\d.]+\s*clip at[^·]*/i, '').trim();
  if (r.clipped) {
    statText = `${statText ? `${statText} · ` : ''}p99.5 clip at ${r.scale.fmt(r.scale.vmax)}`;
  } else if (r.singular != null) {
    statText = `${statText ? `${statText} · ` : ''}top band is one element — p99.5 at ${r.scale.fmt(r.singular)}`;
  }

  return {
    ...out,
    scale: r.scale,
    geometry: g,
    vertexValues: rawVals,
    probe: { cx: pcx, cy: pcy, val: pv, cls: pcl },
    vMin: Number.isFinite(vMin) ? vMin : null,
    vMax: Number.isFinite(vMax) ? vMax : null,
    statText: statText || undefined,
    extent: Number.isFinite(xmin)
      ? [xmin, xmax, ymin, ymax] as [number, number, number, number]
      : out.extent,
    // Orientation without noise: the HIDDEN parts survive as the faint dashed
    // grey ghost the deformed views already draw, so a lone magnet is still
    // somewhere on a rotor rather than floating in black.
    outlines: undefined,
    ghostOutlines: [...(out.ghostOutlines ?? []), ...(out.outlines ?? [])],
    // The overlays were built for the WHOLE picture — A_z flux lines placed at
    // the full model's levels, heat-flux arrows sampled across every element,
    // contact segments that by definition join two parts — and none of them can
    // be honestly cut down to one part, so a part view does without them.
    overlays: undefined,
    note: `${r.scale.note} · ${what} only — colour scale, max/min and mesh are this selection's own`,
  };
}
