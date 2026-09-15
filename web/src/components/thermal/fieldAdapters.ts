/**
 * fieldAdapters — the Thermal tab's half of the shared field contract.
 *
 * User 2026-09-06: "интерфейс должен быть единым для всех графиков —
 * электромагнитных, механических и термо".  So a temperature map is turned into
 * exactly the same `FieldOutput` the EM and mechanical adapters produce, and
 * `common/FieldViewer` draws all of them with one camera, one banded shader, one
 * colour bar and one part tree.
 *
 * Nothing here computes physics.  The temperature is the solver's, the heat flux
 * is the solver's q = −k∇T, and the component maxima are the solver's — this
 * file only decides how they are COLOURED.
 */
import {
  emClassName, fmt0, fmtSI, floorOf, linScale, pctl, rankScale,
} from '../simulation/fieldView';
import type { FieldScale } from '../simulation/fieldView';
import { buildMeshGeometry, extentOf, outputStub } from '../common/fieldOutput';
import type { FieldOutput, FieldOverlay } from '../common/fieldOutput';
import type { ThermView, ThermalMeshPayload } from './api';
import type { ThermalPayload } from './types';

export const THERMAL_MENU: {
  id: ThermView; menuLabel: string; label: string; unit: string; tip: string;
}[] = [
  { id: 'temp', menuLabel: 'Temperature', label: 'Temperature', unit: '°C',
    tip: 'Steady-state temperature of the solids — a conduction solve fed by the electromagnetic losses, with the cooling applied at the housing. A machine under steady cooling is a tight hot plateau, so the default colour mapping is EQUALISED (band edges are the temperature quantiles, which spends the whole palette on the distribution that exists); switch to Linear for a true °C scale.' },
  { id: 'flux', menuLabel: 'Heat flux |q|', label: 'Heat flux magnitude |q|', unit: 'W/m²',
    tip: 'Magnitude of q = −k∇T per element: how hard heat is being pushed through each part on its way out. The bright paths are the ones actually carrying the loss — a cool winding beside a dark slot wall means the heat is leaving somewhere else. Clipped at the 99.5th percentile, because a single corner element can be ten times the field around it.' },
  { id: 'grad', menuLabel: 'Gradient |∇T|', label: 'Temperature gradient |∇T|', unit: 'K/mm',
    tip: 'How fast the temperature falls per millimetre in each element: the WORST heat-transfer places are the brightest — the insulation, the enamel between conductors, the air gap and the sleeve, where a few tenths of a millimetre cost tens of kelvin. Copper and iron are dark (they conduct), a bright thin band is a resistance worth attacking. Clipped at the 99.5th percentile.' },
];

export interface ThermOpts {
  /** rank-equalised colours (default) vs a true linear °C axis */
  eqTemp?: boolean;
  /** draw the heat-flux arrows over the fill */
  showFlux?: boolean;
}

/**
 * Solver payloads in this app are in METRES and the shared viewer works in
 * millimetres — but the mesh route and the field route are built by two
 * different backend helpers, so rather than trusting one convention this
 * measures the model: no motor cross-section this app builds is 5 units wide in
 * millimetres, and none is 5 units wide in metres either, so the largest
 * coordinate settles it.  Getting it wrong would not be subtle (a 200 mm rotor
 * drawn 0.2 mm across), and it costs one pass over the vertices.
 */
function unitScale(vertices: ReadonlyArray<readonly [number, number]>): number {
  let m = 0;
  for (const [x, y] of vertices) {
    const a = Math.abs(x), b = Math.abs(y);
    if (a > m) m = a;
    if (b > m) m = b;
  }
  return m > 5 ? 1 : 1000;
}

const scaled = (vertices: ReadonlyArray<readonly [number, number]>, k: number):
    [number, number][] => vertices.map(([x, y]) => [x * k, y * k]);

/** Part / material name for a domain tag: the payload's own names when the
 *  backend sends them, otherwise the EM vocabulary — the thermal mesh comes off
 *  the same `geo_mesh` pipeline, so its tags are the same domain ids. */
function labeller(p: { part_names?: Record<string, string> }): (c: number) => string | undefined {
  const names = p.part_names;
  return names
    ? ((c: number) => names[String(c)] ?? emClassName(c))
    : emClassName;
}

/**
 * Heat-flux arrows — q = −k∇T per element, drawn from the element centroid,
 * length ∝ |q| and clamped, so the picture shows heat FLOWING out of the
 * winding rather than only where it is hot.
 *
 * Sampled (~500 arrows however fine the mesh is) because an arrow per element
 * on a 100k-triangle mesh is a grey wash, not a direction field.  Lived in
 * `simulation/fieldView` until 2026-09-07, when the thermal solve got its own
 * tab and stopped being an entry in the EM output menu.
 */
export function buildFluxArrows(payload: ThermalPayload, k: number): Float32Array | null {
  const flux = payload.heat_flux_per_tri ?? [];
  const fmag = payload.flux_mag_per_tri ?? [];
  if (!flux.length) return null;
  const { vertices, triangles } = payload;
  if (!vertices?.length || !triangles?.length) return null;
  // The reference length is the 90th percentile, not the maximum: one singular
  // corner element would otherwise shrink every honest arrow to a dot.
  const pos = Float64Array.from(fmag.filter(v => v > 0)).sort();
  const qref = pos.length ? pos[Math.floor(0.9 * (pos.length - 1))] : 1;
  const [xmin, xmax, ymin, ymax] = extentOf(scaled(vertices, k));
  const span = Math.max(xmax - xmin, ymax - ymin);
  const Lmax = span * 0.035;
  const step = Math.max(1, Math.floor(triangles.length / 500));
  const arr: number[] = [];
  for (let ti = 0; ti < triangles.length; ti += step) {
    const f = flux[ti]; if (!f) continue;
    const m = fmag[ti] || Math.hypot(f[0], f[1]);
    if (m <= 1e-9) continue;
    const [a, b, c] = triangles[ti];
    const cx = (vertices[a][0] + vertices[b][0] + vertices[c][0]) / 3 * k;
    const cy = (vertices[a][1] + vertices[b][1] + vertices[c][1]) / 3 * k;
    const len = Math.min(1, m / qref) * Lmax;
    const ux = f[0] / m, uy = f[1] / m;
    const ex = cx + ux * len, ey = cy + uy * len;
    arr.push(cx, cy, 1.4, ex, ey, 1.4);                                    // shaft
    const hb = len * 0.32;
    arr.push(ex, ey, 1.4, ex - hb * (ux + uy), ey - hb * (uy - ux), 1.4);   // barb
    arr.push(ex, ey, 1.4, ex - hb * (ux - uy), ey - hb * (uy + ux), 1.4);   // barb
  }
  return arr.length ? new Float32Array(arr) : null;
}

/** ONE thermal output, ready for the shared viewer. */
export function thermalOutputs(
  payload: ThermalPayload | null | undefined,
  view: ThermView,
  opts: ThermOpts = {},
): FieldOutput {
  const meta = THERMAL_MENU.find(m => m.id === view) ?? THERMAL_MENU[0];
  const stub = outputStub(meta.id, meta.menuLabel, meta.label, 'Thermal',
    { unit: meta.unit, tip: meta.tip });
  if (!payload?.vertices?.length || !payload.triangles?.length) return stub;

  const k = unitScale(payload.vertices);
  const verts = scaled(payload.vertices, k);
  const extent = extentOf(verts);
  const nTri = payload.triangles.length;

  let scale: FieldScale | null = null;
  let valuesPerTri: ArrayLike<number> | undefined;
  let valuesPerNode: ArrayLike<number> | undefined;
  let sorted: Float64Array | undefined;
  let statText: string | undefined;

  if (view === 'temp') {
    const T = payload.temperature_per_node ?? [];
    if (T.length !== payload.vertices.length) {
      return { ...stub, extent,
               blank: 'no temperature field in this result — press Solve again' };
    }
    // A conduction solve under steady cooling puts almost every node inside a
    // few degrees of the plateau, so a linear axis paints one colour and the
    // structure that matters (which part is 12 K above its neighbour) is
    // invisible.  Rank-equalised spends the whole palette on the distribution
    // that exists; "linear" is one click away and says what it is.
    sorted = Float64Array.from(T).sort();
    valuesPerNode = T;
    const note = opts.eqTemp
      ? 'temperature, rank-equalised — the band edges ARE the temperature quantiles, so a colour step is a share of the nodes, not a fixed number of degrees'
      : 'temperature, linear °C scale';
    scale = opts.eqTemp
      ? rankScale(sorted, '°C', note, fmt0)
      : linScale(sorted[0], sorted[sorted.length - 1] || sorted[0] + 1e-9, '°C', note, fmt0);
  } else if (view === 'grad') {
    const g = payload.grad_T_mag_per_tri ?? [];
    if (g.length !== nTri) {
      return { ...stub, extent,
               blank: 'no gradient field in this result — press Solve again (the backend adds |∇T| since 2026-09-07)' };
    }
    // K/m → K/mm: an engineer reads a liner as "12 K per 0.25 mm", not per metre
    const gmm = g.map((v) => v * 1e-3);
    valuesPerTri = gmm;
    const hi = Math.max(pctl(gmm, 99.5), 1e-9);
    let raw = 0;
    for (const v of gmm) if (v > raw) raw = v;
    scale = linScale(Math.min(floorOf(gmm), hi * 0.999), hi, 'K/mm',
      '|∇T| per element — the temperature drop per millimetre; the brightest bands are the worst heat paths; clipped at the 99.5th percentile',
      fmtSI);
    if (raw > hi * 1.02) statText = `p99.5 clip at ${fmtSI(hi)}`;
  } else {
    const q = payload.flux_mag_per_tri ?? [];
    if (q.length !== nTri) {
      return { ...stub, extent,
               blank: 'no heat-flux field in this result — press Solve again' };
    }
    valuesPerTri = q;
    const hi = Math.max(pctl(q, 99.5), 1e-9);
    let raw = 0;
    for (const v of q) if (v > raw) raw = v;
    scale = linScale(Math.min(floorOf(q), hi * 0.999), hi, 'W/m²',
      '|q| = |−k∇T| per element — clipped at the 99.5th percentile so one singular corner does not flatten the map',
      fmtSI);
    if (raw > hi * 1.02) statText = `p99.5 clip at ${fmtSI(hi)}`;
  }

  const built = buildMeshGeometry({
    vertices: verts,
    triangles: payload.triangles,
    classPerTri: payload.domain_per_tri,
    valuesPerTri, valuesPerNode, sorted,
    scale: scale!,
  });

  const overlays: FieldOverlay[] = [];
  if (opts.showFlux) {
    const f = buildFluxArrows(payload, k);
    if (f) overlays.push({ key: 'flux', positions: f, color: 0xe2e8f0, opacity: 0.7 });
  }

  const outlines: [number, number][][] = [];
  for (const entry of payload.outlines ?? []) {
    for (const loop of entry.loops) {
      if (loop.length < 2) continue;
      outlines.push(loop.map(([x, y]) => [x * k, y * k] as [number, number]));
    }
  }

  return {
    ...stub,
    scale,
    geometry: built.geometry,
    probe: built.probe,
    // What the viewer's part tree re-colours one part from: a magnet's own 6 K
    // spread read against the winding's 90 K is a single flat band.
    vertexValues: built.vertexValues ?? undefined,
    classLabel: labeller(payload),
    vMin: built.vMin, vMax: built.vMax,
    statText,
    outlines,
    // EXACTLY the EM / mechanical outline ink — a different one would be a
    // visible difference between two pictures of the same machine.
    outlineColor: 0x0f172a,
    outlineOpacity: 0.55,
    overlays,
    extent,
    note: scale?.note,
  };
}

/* ── the cross-section, before anything is solved ─────────────────────────── */

/** Neutral slate: a fill that is plainly NOT a colour scale, so an empty tab
 *  cannot be misread as a result whose field happens to be uniform. */
const GEOM_FILL: [number, number, number] = [100, 116, 139];

/**
 * The solids as geometry alone — no values, no colour scale.
 *
 * The same rule the Mechanical tab is built to (2026-09-06: "если нет расчётов —
 * рисуется просто геометрия"): an empty tab shows the picture Solve will colour
 * in, drawn by the SAME viewer with the same camera, so pressing Solve fills the
 * picture instead of creating it.
 *
 * A one-band scale rather than `scale: null`: the viewer hides a colour bar
 * whose labels are blank, but the part tree needs a scale to re-normalise
 * against, and without one it would list the parts and then do nothing.
 */
export function geometryOutput(
  mesh: ThermalMeshPayload | null | undefined,
  statText?: string,
): FieldOutput {
  const stub = outputStub('geom', 'Geometry', 'Cross-section', 'Thermal', {
    unit: '',
    tip: 'The cross-section as it will be meshed and solved — stator, coils, rotor, magnets, shaft and sleeve, plus everything the conduction solve conducts THROUGH: the insulation, the wire enamel, the wire coating, the air gap and the rotor pocket air. Only the far-field air, the slip band and the bore air are dropped (the bore is a cooled surface, not a solid). Nothing has been computed yet: press Solve for the temperature map.',
  });
  if (!mesh?.triangles?.length || !mesh.vertices?.length) return stub;

  const k = unitScale(mesh.vertices);
  const verts = scaled(mesh.vertices, k);
  const scale = linScale(0, 1, '', 'cross-section — no result yet', () => '', 1);
  const built = buildMeshGeometry({
    vertices: verts,
    triangles: mesh.triangles,
    classPerTri: mesh.domain_per_tri,
    valuesPerTri: new Float64Array(mesh.triangles.length),   // flat, all zero
    scale,
  });

  const outlines: [number, number][][] = [];
  for (const entry of mesh.outlines ?? []) {
    for (const loop of entry.loops) {
      if (loop.length < 2) continue;
      outlines.push(loop.map(([x, y]) => [x * k, y * k] as [number, number]));
    }
  }

  return {
    ...stub,
    scale,
    palette: [GEOM_FILL],
    iso: 0,
    geometry: built.geometry,
    probe: built.probe,
    vertexValues: built.vertexValues ?? undefined,
    classLabel: labeller(mesh),
    // NOT built.vMin/vMax (they are the zeros of a fill that means nothing):
    // the header prints "max — · min —", which is the truth here.
    vMin: null,
    vMax: null,
    statText: statText ?? 'geometry only — nothing solved yet',
    outlines,
    outlineColor: 0x0f172a,
    outlineOpacity: 0.55,
    extent: extentOf(verts),
    note: `${mesh.n_triangles.toLocaleString()} triangles at ${mesh.mesh_size_mm} mm — the mesh the thermal solve will use. Press Solve to fill it with a temperature.`,
  };
}
