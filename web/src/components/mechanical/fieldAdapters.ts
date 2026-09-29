/**
 * fieldAdapters — the Mechanical tab's half of the shared field contract.
 *
 * User 2026-09-06: "интерфейс должен быть единым для всех графиков —
 * электромагнитных, механических и термо".  Everything a mechanical or modal
 * picture needs is turned into the SAME `FieldOutput` the EM adapter produces,
 * so `common/FieldViewer` draws all of them with one camera, one banded shader
 * and one colour bar.  The three hand-rolled 2-D canvases this replaced each had
 * their own rainbow, their own legend and no zoom at all.
 *
 * What is NOT computed here, on purpose: the safety factor.  `strength / stress`
 * is only a safety factor once you know which strength and which stress, and
 * that is per part — the backend does the division and sends `sf_per_tri`.
 */
import {
  edgesScale, fmtAuto, floorOf, linScale, pctl,
} from '../simulation/fieldView';
import type { FieldScale } from '../simulation/fieldView';
import { buildMeshGeometry, outputStub } from '../common/fieldOutput';
import type { FieldOutput, FieldOverlay } from '../common/fieldOutput';
import type {
  CaseName, FieldPayload, MechMeshPayload, ModeField, StressField,
} from './api';

/* ── the safety-factor bands (Fusion's three) ─────────────────────────────── */
export const SF_BELOW: [number, number, number] = [224, 69, 58];
export const SF_IN: [number, number, number] = [63, 180, 99];
export const SF_ABOVE: [number, number, number] = [47, 111, 224];
/** Top of the safety-factor legend: everything above it is one blue band. */
export const SF_TOP = 8;

/** The red line of the acceptance view: an element below it has no margin
 *  worth the name.  It is what the AUTO bands fall back on while any of the
 *  rotor is still within reach of it (2026-09-09). */
export const SF_ACCEPT = 2;

/** Round a safety factor to a number a legend can carry — 1, 2, 5 × 10ⁿ.
 *  The auto bands are read off a distribution, and "the 5th percentile is
 *  11.83" is not a band edge anybody wants on a colour bar. */
export function niceSf(v: number): number {
  if (!Number.isFinite(v) || v <= 0) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / e;
  return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * e;
}

export type MechView = 'disp' | 'vm' | 's1' | 'hoop' | 'radial' | 'sf';

export const MECH_MENU: {
  id: MechView; menuLabel: string; label: string; unit: string; tip: string;
}[] = [
  { id: 'disp', menuLabel: 'Displacement', label: 'Displacement |u|', unit: 'µm',
    tip: 'Displacement magnitude on the DEFORMED shape; the dashed outline is the undeformed metal. Microns on a 100 mm part are invisible at true scale, so the shape is drawn exaggerated — set the factor in "deform ×".' },
  { id: 'vm', menuLabel: 'Von Mises', label: 'Von Mises stress', unit: 'MPa',
    tip: 'Equivalent stress. The colour range stops at the 99.5th percentile: a re-entrant corner is a stress SINGULARITY whose element can be ten times the field around it, and colouring to the raw max paints the whole rotor blue.' },
  { id: 's1', menuLabel: 'Principal σ₁', label: 'Maximum principal stress σ₁', unit: 'MPa',
    tip: 'Largest principal stress — the one a brittle part (a magnet, a bonded joint) is judged on, because it fails in tension across the plane where σ₁ acts.' },
  { id: 'hoop', menuLabel: 'Hoop σθ', label: 'Hoop stress σθ', unit: 'MPa',
    tip: 'Circumferential stress. This is the sleeve\'s number: a filament-wound band carries the magnets almost entirely in its fibre (hoop) direction.' },
  { id: 'radial', menuLabel: 'Radial σr', label: 'Radial stress σr', unit: 'MPa',
    tip: 'Radial stress. Negative (compressive) at an interface means the joint is still clamped; positive means it is being pulled apart.' },
  { id: 'sf', menuLabel: 'Safety factor', label: 'Safety factor', unit: '',
    tip: 'Per-element safety factor, already divided by the RIGHT strength for the part each element belongs to — steel on yield / von Mises, the magnets on both principals, the sleeve on its fibre-direction hoop strength. Below target red, in range green, above target blue: the same three bands Fusion shows.' },
];

// The number formatter is `fieldView.fmtAuto`, shared with every other output.
// It used to be a private `fmtMPa` here — user 2026-09-06: "вывод полей
// напряжения/деформации должен быть сделан так же как B — единый стиль везде",
// and a second copy of the formatter is a second place for the same value to be
// printed differently.  Same reason the outline colour below is the EM one.

/** Contact state, drawn over the map: red where the interface has opened, green
 *  where it is still carrying compression, amber where the facet is part-open
 *  (its three node pairs vote separately). */
function contactOverlay(p: FieldPayload, f: StressField): FieldOverlay | null {
  const segs = p.contact_segments_per_pair;
  if (!segs) return null;
  const pos: number[] = [];
  const col: number[] = [];
  for (const [pair, list] of Object.entries(segs)) {
    const op = f.contact_open_per_pair?.[pair];
    for (let i = 0; i < list.length; i++) {
      const o = op ? op[i] : 0;
      const c = o > 0.66 ? [0.97, 0.31, 0.31]
        : (o > 0.15 ? [0.98, 0.75, 0.14] : [0.24, 0.78, 0.43]);
      const [x0, y0, x1, y1] = list[i];
      pos.push(x0, y0, 1.6, x1, y1, 1.6);
      col.push(c[0], c[1], c[2], c[0], c[1], c[2]);
    }
  }
  if (!pos.length) return null;
  return { key: 'contacts', positions: new Float32Array(pos),
           colors: new Float32Array(col) };
}

/** Symmetric range about zero when the field really does change sign, so jet's
 *  green midpoint lands on 0 — the same rule the EM current-density view uses.
 *  Percentile-clipped at both ends: corner singularities live at both. */
function divergingRange(v: ArrayLike<number>): [number, number] {
  const lo = pctl(v, 0.5);
  const hi = pctl(v, 99.5);
  if (lo < 0 && hi > 0) {
    const m = Math.max(Math.abs(lo), Math.abs(hi), 1e-9);
    return [-m, m];
  }
  return [Math.min(lo, hi - 1e-9), Math.max(hi, lo + 1e-9)];
}

export interface MechOpts {
  /** ×N deformation exaggeration on the displacement view; 0/NaN = auto, sized
   *  so the largest displacement reads as ~5 % of the rotor radius */
  exaggeration?: number;
  sfLow?: number;
  sfHigh?: number;
  contacts?: boolean;
}

/** ONE mechanical output, ready for the shared viewer. */
export function mechOutputs(
  payload: FieldPayload | null,
  caseName: CaseName,
  view: MechView,
  opts: MechOpts = {},
): FieldOutput {
  const meta = MECH_MENU.find(m => m.id === view) ?? MECH_MENU[0];
  const stub = outputStub(meta.id, meta.menuLabel, meta.label, 'Mechanical',
    { unit: meta.unit, tip: meta.tip });
  const f = payload?.cases?.[caseName];
  if (!payload || !f) return stub;

  const nTri = payload.triangles.length;
  const ext = payload.extent || 1;
  const extent: [number, number, number, number] = [-ext, ext, -ext, ext];

  // ── the deformation factor (displacement view only) ─────────────────────
  let autoK = 0;
  for (const u of f.u_mag_per_node) if (u > autoK) autoK = u;
  autoK = autoK > 0 ? (0.05 * ext) / (autoK * 1e-3) : 0;
  const exagg = view !== 'disp' ? 0
    : (Number.isFinite(opts.exaggeration) && (opts.exaggeration as number) > 0
        ? (opts.exaggeration as number) : autoK);

  // ── values + scale ──────────────────────────────────────────────────────
  let scale: FieldScale | null = null;
  let valuesPerTri: number[] | undefined;
  let valuesPerNode: number[] | undefined;
  let palette: [number, number, number][] | undefined;
  let iso: number | undefined;
  let statText: string | undefined;
  /** builds the colour scale from the DRAWN nodal values (see below) */
  let scaleFrom: ((nodal: Float32Array) => FieldScale) | undefined;

  if (view === 'disp') {
    valuesPerNode = f.u_mag_per_node;
    let mx = 0;
    for (const u of f.u_mag_per_node) if (u > mx) mx = u;
    const mn = floorOf(f.u_mag_per_node);
    scale = linScale(Math.min(mn, (mx || 1e-9) * 0.999), mx || 1e-9, 'µm',
      'displacement magnitude on the deformed shape — the dashed outline is the undeformed metal',
      fmtAuto);
    if (exagg > 0) statText = `deform ×${exagg >= 100 ? exagg.toFixed(0) : exagg.toFixed(1)}`;
  } else if (view === 'sf') {
    const sf = f.sf_per_tri ?? [];
    // A result solved before the safety-factor field existed has no sf_per_tri.
    // SAY so — painting every element red because the array is missing would be
    // a rotor condemned by a plumbing gap.
    if (sf.length !== nTri) {
      return { ...stub, extent,
        blank: 'no safety-factor field in this result — press Solve again' };
    }
    valuesPerTri = sf;
    // The bar starts at the field's own MINIMUM, not at 0 (user 2026-09-07):
    // a band nobody's element falls into is not drawn, so a rotor whose worst
    // element is at 2.6 shows green and blue only, from 2.6 up.
    const sfMin = Math.max(0, floorOf(sf));
    // AUTO BANDS (user 2026-09-09: "давай это делать автоматом").  A blank
    // low/high hands the choice to the map, and there are two regimes, because
    // a safety factor is an ACCEPTANCE test only while something is close to
    // failing:
    //   * anything within reach of the line (worst element under 2x SF_ACCEPT)
    //     keeps the fixed Fusion bands — a scale that slid upward could paint
    //     an element with no margin "safe", which is the one thing this view
    //     must never do;
    //   * a rotor whose WORST element sits far above the line has no failing
    //     metal to point at, and 2/4 then paints the whole picture one flat
    //     blue.  There the bands move onto the field's own 5th percentile and
    //     median — where the metal is least happy, which is what the picture is
    //     for — and the legend says they are RELATIVE, not a verdict.
    const asked = (v: unknown) =>
      (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : null);
    const wantLow = asked(opts.sfLow);
    const wantHigh = asked(opts.sfHigh);
    let low: number; let high: number; let top: number; let bandNote = '';
    if (wantLow != null || wantHigh != null) {
      low = Math.max(0.01, wantLow ?? SF_ACCEPT);
      high = Math.max(low + 0.01, wantHigh ?? low * 2);
      top = Math.max(high + 0.01, SF_TOP);
    } else if (sfMin < SF_ACCEPT * 2) {
      low = SF_ACCEPT;
      high = SF_ACCEPT * 2;
      top = Math.max(high + 0.01, SF_TOP);
      bandNote = ' — AUTO, acceptance bands: the worst element is close enough '
        + `to the ${SF_ACCEPT} line to be judged against it`;
    } else {
      low = niceSf(pctl(sf, 5));
      high = Math.max(niceSf(pctl(sf, 50)), low * 1.5);
      top = Math.max(niceSf(pctl(sf, 99)), high * 1.5);
      bandNote = ' — AUTO, RELATIVE bands: nothing on this rotor is anywhere '
        + `near the ${SF_ACCEPT} acceptance line (the worst element is at `
        + `${sfMin.toFixed(1)}), so the colours split the field itself at its `
        + '5th percentile and its median. These are not a pass/fail verdict';
    }
    const edges: number[] = [Math.min(sfMin, top - 0.02)];
    const bands: [number, number, number][] = [];
    if (sfMin < low) { edges.push(low); bands.push(SF_BELOW); }
    if (sfMin < high) { edges.push(high); bands.push(SF_IN); }
    edges.push(top); bands.push(SF_ABOVE);
    const d1 = (v: number) => (v >= 10 ? v.toFixed(0) : v.toFixed(1));
    scale = edgesScale(edges, '',
      `safety factor — below ${d1(low)} red, in range green, above ${d1(high)} blue; `
      + `the bar starts at the field's minimum (${sfMin.toFixed(2)}) and stops at `
      + `${d1(top)}: everything safer than that is one blue${bandNote}`,
      (v) => (v >= 10 ? v.toFixed(0) : v.toFixed(1)));
    palette = bands;
    // Fusion draws no contour line between the three bands, and the band edge
    // here is a THRESHOLD, not an iso-level of a continuous field.
    iso = 0;
  } else {
    const src = view === 'vm' ? f.vm_per_tri
      : view === 's1' ? f.s_p1_per_tri
      : view === 'hoop' ? f.s_hoop_per_tri
      : f.s_rad_per_tri;
    const v = src ?? [];
    if (v.length !== nTri) {
      return { ...stub, extent,
        blank: `no ${meta.menuLabel} field in this result — press Solve again` };
    }
    valuesPerTri = v;
    // THE BAR ENDS WHERE THE PICTURE ENDS.
    //
    // User 2026-09-10: "я думаю шкалу визуализации нужно сдвигать", after
    // asking how two different numbers for one band could ever be explained to
    // a user who has ANSYS open beside us.  They cannot, so both moved onto one
    // convention: the AVERAGED nodal field, which is what ANSYS and Fusion plot
    // and report, and which the solver now quotes in every table.
    //
    // The range therefore has to be measured on that field, and it does not
    // exist until `buildMeshGeometry` has averaged — hence `scaleFrom`, which
    // is handed the drawn nodal values.  No percentile clip any more: the clip
    // was there to keep ONE singular element from flattening a picture drawn
    // from raw element values, and averaging already does that job (on the live
    // band the element peak is 1770 MPa and the averaged one 1558).  Clipping
    // on top would put the bar's top back where nothing on the picture is.
    scaleFrom = (nodal: Float32Array) => {
      const lo = floorOf(nodal);
      let hi = -Infinity;
      for (let i = 0; i < nodal.length; i++) {
        if (Number.isFinite(nodal[i]) && nodal[i] > hi) hi = nodal[i];
      }
      if (!Number.isFinite(hi)) hi = lo + 1e-9;
      if (view === 'vm' || view === 's1') {
        return linScale(lo, Math.max(hi, lo + 1e-9), 'MPa',
          `${meta.label} — AVERAGED, the ANSYS/Fusion convention: each element value area-averaged onto the nodes of its own part. The bar runs from the picture's own minimum to its own maximum, and those are the numbers the result table quotes`,
          fmtAuto);
      }
      // Signed components keep zero on jet's green midpoint.
      const [dlo, dhi] = divergingRange(nodal);
      return linScale(dlo, dhi, 'MPa',
        `${meta.label} — AVERAGED (element values area-averaged onto the nodes of their own part), symmetric about zero so the midpoint is 0`,
        fmtAuto);
    };
    // A provisional scale for the type: `scaleFrom` replaces it before any
    // value is normalised against it.
    scale = linScale(0, 1, 'MPa', '', fmtAuto);
  }

  const built = buildMeshGeometry({
    vertices: payload.vertices,
    triangles: payload.triangles,
    classPerTri: payload.domain_per_tri,
    valuesPerTri, valuesPerNode,
    scale: scale!, scaleFrom,
    displacement: exagg > 0 ? f.u_per_node : undefined,
    deform: exagg > 0 ? exagg * 1e-3 : 0,        // µm → mm
  });

  const overlays: FieldOverlay[] = [];
  if (opts.contacts) {
    const c = contactOverlay(payload, f);
    if (c) overlays.push(c);
  }

  return {
    ...stub,
    // `built.scale` — the one the values were actually normalised against, so
    // the bar and the fill can never describe different ranges.
    scale: built.scale ?? scale, palette, iso,
    geometry: built.geometry,
    probe: built.probe,
    // Raw per-vertex values: what the viewer's Part menu re-colours one part
    // from, so a magnet's own 40 MPa range is not the rotor lips' 5 GPa one.
    vertexValues: built.vertexValues ?? undefined,
    // The payload names its own domain tags; the viewer prints whatever comes
    // back next to the max / min markers.
    classLabel: (c) => payload.part_names?.[String(c)],
    vMin: built.vMin, vMax: built.vMax,
    statText,
    // Deformed: only the DASHED undeformed silhouette, drawn under the fill, so
    // the growth is the sliver that sticks out.  Undeformed: the solid outline.
    outlines: exagg > 0 ? undefined : payload.outlines,
    ghostOutlines: exagg > 0 ? payload.outlines : undefined,
    // EXACTLY the EM outline (0x0f172a / 0.55), not the black-45 % this used to
    // draw.  User 2026-09-06: "вывод полей напряжения/деформации должен быть
    // сделан так же как B" — a different outline ink is a visible difference
    // between a stress map and a |B| map of the same machine.
    outlineColor: 0x0f172a,
    outlineOpacity: 0.55,
    overlays,
    extent,
    note: (built.scale ?? scale)?.note,
  };
}

/* ── the cross-section, before anything is solved ─────────────────────────── */

/** Neutral slate: a fill that is plainly NOT a colour scale, so an empty tab
 *  cannot be misread as a result whose field happens to be uniform. */
const GEOM_FILL: [number, number, number] = [100, 116, 139];

/**
 * The rotor as geometry alone — no values, no colour scale.
 *
 * User 2026-09-06: "если нет расчётов — рисуется просто геометрия".  Built from
 * `/api/mechanical/mesh`, i.e. the SAME `build_rotor_mesh` the stress solve
 * runs, so the empty tab shows the picture the solve will colour in — down to
 * the element edges, which is what makes the viewer's Mesh and Part toggles
 * work before the first Solve.
 *
 * A one-band scale rather than `scale: null`: the viewer's colour bar hides
 * itself for a scale whose labels are blank, but `restrictToPart` needs a scale
 * to re-normalise against, and without one the Part menu would list the parts
 * and then do nothing when you pick one.
 */
export function geometryOutput(
  mesh: MechMeshPayload | null | undefined,
  statText?: string,
): FieldOutput {
  const stub = outputStub('geom', 'Geometry', 'Rotor cross-section', 'Mechanical', {
    unit: '',
    tip: 'The rotor solids as they will be meshed and solved — the same mesh the stress solve builds. Nothing has been computed yet: press Solve for the stresses, the deformation and the safety factor.',
  });
  if (!mesh || !mesh.triangles?.length) return stub;

  const ext = mesh.extent || 1;
  // Blank labels and no unit: there is no quantity here to put numbers on.
  const scale = linScale(0, 1, '', 'rotor cross-section — no result yet',
    () => '', 1);
  const built = buildMeshGeometry({
    vertices: mesh.vertices,
    triangles: mesh.triangles,
    classPerTri: mesh.domain_per_tri,
    valuesPerTri: new Float64Array(mesh.triangles.length),   // flat, all zero
    scale,
  });

  return {
    ...stub,
    scale,
    palette: [GEOM_FILL],
    iso: 0,
    geometry: built.geometry,
    probe: built.probe,
    vertexValues: built.vertexValues ?? undefined,
    classLabel: (c) => mesh.part_names?.[String(c)],
    // NOT built.vMin/vMax (they are the zeros of a fill that means nothing):
    // the header prints "max — · min —", which is the truth here.
    vMin: null,
    vMax: null,
    statText: statText ?? 'geometry only — nothing solved yet',
    outlines: mesh.outlines,
    outlineColor: 0x0f172a,
    outlineOpacity: 0.55,
    extent: [-ext, ext, -ext, ext],
    note: `${mesh.n_triangles.toLocaleString()} triangles at ${mesh.mesh_size_mm} mm — the mesh the stress solve will use. Press Solve to fill it with a field.`,
  };
}

/**
 * A mode shape.
 *
 * NOT a mechanical "field": it has no units — the solver normalises it so the
 * peak component is 1 — so the exaggeration is a fraction of the model's own
 * size, not a micron scale, and the colour bar reads 0…1 of the peak.
 */
export function modeOutputs(
  field: ModeField | null | undefined,
  modeIndex: number,
  exaggPct: number,
  label?: string,
): FieldOutput {
  const stub = outputStub('mode', 'Mode shape', label ?? 'Mode shape', 'Modal', {
    unit: '',
    tip: 'In-plane eigenmode of the iron cross-section. A mode shape has no amplitude — only a shape — so it is drawn exaggerated: "deform %" is the peak displacement as a percentage of the model\'s own radius. The dashed outline is the undeformed metal.',
  });
  const shape = field?.modes?.[modeIndex];
  if (!field || !shape) return stub;

  const mag = new Float64Array(shape.length);
  let mx = 0;
  for (let i = 0; i < shape.length; i++) {
    mag[i] = Math.hypot(shape[i][0], shape[i][1]);
    if (mag[i] > mx) mx = mag[i];
  }
  const span = mx || 1;
  const rel = new Float64Array(shape.length);
  for (let i = 0; i < shape.length; i++) rel[i] = mag[i] / span;

  const ext = field.extent || 1;
  const scale = linScale(0, 1, 'of peak',
    'mode shape, normalised to its own peak — a mode has a shape, not an amplitude',
    (v) => v.toFixed(2));
  const built = buildMeshGeometry({
    vertices: field.vertices,
    triangles: field.triangles,
    classPerTri: field.domain_per_tri,
    valuesPerNode: rel,
    scale,
    displacement: shape,
    // the shape's peak component is 1, so this is literally "this fraction of
    // the model's own radius at the most displaced point"
    deform: (exaggPct / 100) * ext,
  });

  return {
    ...stub,
    scale,
    geometry: built.geometry,
    probe: built.probe,
    vertexValues: built.vertexValues ?? undefined,
    classLabel: (c) => field.part_names?.[String(c)],
    vMin: built.vMin, vMax: built.vMax,
    ghostOutlines: field.outlines,
    extent: [-ext, ext, -ext, ext],
    note: scale.note,
  };
}
