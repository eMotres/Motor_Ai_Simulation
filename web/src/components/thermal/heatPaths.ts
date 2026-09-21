/**
 * THE HEAT PATHS — where the watts leave the machine, as something you can draw.
 *
 * User, 2026-09-15: *"лучше нарисовать 3D модель с катушками (end windings) и на
 * ней прямо показывать, куда и сколько тепла может отводиться, чтобы
 * пользователю было всё ясно и понятно"*.
 *
 * Every number here is ALREADY in the payload the Thermal tab has — `cooling
 * .outer`, `.mount`, `.end_faces`, `.inner`, `.shaft_ends` and
 * `.heat_budget` — and none of it is recomputed.  What this module adds is
 * WHERE each watt leaves: one sink per surface, with the radii and the axial
 * station of the cylinder, annulus or band it sits on, so `HeatPathView3D` can
 * build the picture out of primitives without an STL, a mesh or a round trip.
 *
 * DERIVED IN THE BROWSER ON PURPOSE.  The backend has the same model
 * (`motor_ai_sim/thermal_heat_paths.py`, `GET /api/thermal/heat_paths/last`)
 * and the two agree field for field — but a route only exists after the API is
 * next restarted, and every input is already on this page: the cooling block
 * from the solve, the geometry from `motorStore`.  So this is the primary path
 * and the route is the second reading of it.
 *
 * THE DERIVED GEOMETRY FIELDS ARE NOT READ.  `motorStore.geometry` carries
 * `stator_inner_radius` beside the primitives it comes from, and on the Ø85 die
 * the two disagree (33.1 stored against 42.5 − 2.4 − 7.4 = 32.7).  Every radius
 * below is recomputed from the primitives, which is what the solver meshes —
 * the same five subtractions `simulation/geometry_2d.params_from_config` makes.
 *
 * UNITS: millimetres, watts, °C.  The axis is +z, the stack is CENTRED on
 * z = 0 (it spans ±L/2) and the cross-section is in xy — the viewer's own
 * convention.
 */

/** Bumped when the shape changes in a way the view has to notice. Mirrors
 *  `thermal_heat_paths.HEAT_PATH_SCHEMA_VERSION`. */
export const HEAT_PATH_SCHEMA_VERSION = 1;

type Dict = Record<string, unknown>;

export type SinkId =
  | 'mount' | 'housing' | 'end_face_winding' | 'end_face_stator'
  | 'end_windings' | 'slot_channels' | 'bore' | 'shaft_ends'
  | 'end_face_rotor' | 'end_face_magnet';

export interface HeatPathPlacement {
  /** cylinder = a lateral surface; annulus = a flat ring on an end;
   *  band = a ring standing proud of the core (the end turns);
   *  stub = the shaft outside the housing. */
  kind: 'cylinder' | 'annulus' | 'band' | 'stub';
  facing?: 'in' | 'out';
  r_mm?: number | null;
  r_in_mm?: number | null;
  r_out_mm?: number | null;
  z_mm?: number | null;
  z0_mm?: number | null;
  z1_mm?: number | null;
  length_mm?: number | null;
  /** which axial end(s) this surface is on: −1, +1, or both */
  sides?: number[];
}

export interface HeatSink {
  id: SinkId;
  /** which side of the machine pays for it */
  group: 'stator' | 'rotor';
  label: string;
  short: string;
  mode: string | null;
  active: boolean;
  W: number;
  /** share of what LEFT, not of what was made */
  pct: number | null;
  /** 0…1 against the BIGGEST active path — what the colour and the arrow read */
  intensity: number;
  h_W_per_m2K: number | null;
  G_W_per_K: number | null;
  area_m2: number | null;
  t_surface_c: number | null;
  t_sink_c: number | null;
  detail: { label: string; W: number }[];
  placement: HeatPathPlacement;
  note: string;
}

export interface MachineEnvelope {
  units: 'mm';
  known: boolean;
  stack_length_mm?: number;
  z_stack_mm?: [number, number];
  z_extent_mm?: [number, number];
  housing_r_mm?: number;
  yoke_r_in_mm?: number;
  slot_r_in_mm?: number;
  slot_r_out_mm?: number;
  slot_height_mm?: number;
  air_gap_mm?: number;
  gap_r_in_mm?: number;
  gap_r_out_mm?: number;
  rotor_iron_r_in_mm?: number;
  rotor_iron_r_out_mm?: number;
  magnet_r_in_mm?: number;
  magnet_r_out_mm?: number;
  sleeve_thickness_mm?: number;
  sleeve_r_in_mm?: number | null;
  sleeve_r_out_mm?: number | null;
  shaft_r_out_mm?: number;
  shaft_r_in_mm?: number;
  bore_r_mm?: number;
  shaft_extension_mm?: number;
  end_winding_overhang_mm?: number;
  end_winding_r_in_mm?: number;
  end_winding_r_out_mm?: number;
  k_end?: number | null;
  num_slots?: number | null;
  num_poles?: number | null;
}

export interface HeatPathTotals {
  generated_W: number | null;
  removed_W: number;
  residual_W: number | null;
  residual_pct: number | null;
  stator_side_W: number;
  rotor_side_W: number;
  stator_side_pct: number | null;
  rotor_side_pct: number | null;
}

export interface HeatPathModel {
  ok: boolean;
  reason?: string;
  schema_version: number;
  cooling_mode: string;
  ambient_c: number | null;
  totals: HeatPathTotals;
  sinks: HeatSink[];
  geometry: MachineEnvelope;
}

/* ── readers: a missing key is null, never 0 ─────────────────────────────── */

const f = (v: unknown): number | null => {
  const x = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(x) ? x : null;
};
const f0 = (v: unknown, def = 0): number => f(v) ?? def;
const d = (v: unknown): Dict => (v && typeof v === 'object' ? v as Dict : {});
const r3 = (v: number | null | undefined, nd = 3): number | null =>
  (v === null || v === undefined || !Number.isFinite(v)
    ? null : Math.round(v * 10 ** nd) / 10 ** nd);

/* ── 1. the machine, as primitives ───────────────────────────────────────── */

/** `masses.end_winding_factor` on a bare geometry dict: the half-loop
 *  centreline runs round the tooth plus the whole wire COLUMN, so
 *  k_end = (π·span/2 + L)/L.  The same estimator the solver uses for auto. */
export function kEndFromGeometry(geo: Dict): number {
  const L = f0(geo.motor_length);
  if (L <= 0) return 1;
  const tooth = Math.max(f0(geo.tooth_width), 0);
  // the wire column: `wire_split` strips side by side with 2·spacing between
  // (winding.winding_footprint_mm); identical to wire_width at split 1.
  const w = Math.max(f0(geo.wire_width), 0);
  const n = Math.max(Math.round(f0(geo.wire_split, 1)), 1);
  const col = n * w + 2 * (n - 1) * Math.max(f0(geo.wire_spacing_x), 0);
  const span = tooth + col;
  if (!(span > 0)) return 1;
  return (Math.PI * span / 2 + L) / L;
}

/** Every radius and length a primitive-built 3-D of this machine needs, mm. */
export function machineEnvelope(
  geometry: Dict | null | undefined,
  opts: { kEnd?: number | null; shaftExtMm?: number; boreRmm?: number | null } = {},
): MachineEnvelope {
  const g = d(geometry);
  if (!Object.keys(g).length) return { units: 'mm', known: false };

  const rSo = f0(g.stator_diameter) / 2;
  const coreT = f0(g.core_thickness);
  const slotH = f0(g.slot_height);
  const gap = f0(g.air_gap);
  const magH = f0(g.magnet_height);
  const houseH = f0(g.rotor_house_height);
  const shaftH = f0(g.shaft_height);
  const sleeveT = Math.max(f0(g.sleeve_thickness), 0);
  const L = f0(g.motor_length);

  const rSi = rSo - coreT - slotH;
  const rRo = rSi - gap;
  const rRi = rRo - magH - houseH;
  const rSh = rRi - shaftH;

  const bore = opts.boreRmm && opts.boreRmm > 0 ? opts.boreRmm : rSh;
  const rBore = Math.max(Math.min(bore, rRi), 0);

  const rMagOut = Math.max(rRo - sleeveT, rRi);
  const rMagIn = Math.max(rRi + houseH, rRi);

  const k = opts.kEnd && opts.kEnd > 1 ? opts.kEnd : kEndFromGeometry(g);
  const lEnd = k > 1 && L > 0 ? (k - 1) * L / 2 : 0;
  const ext = Math.max(opts.shaftExtMm ?? 0, 0);
  const half = L / 2;
  const reach = Math.max(lEnd, ext);

  return {
    units: 'mm',
    known: rSo > 0 && L > 0,
    stack_length_mm: r3(L)!,
    z_stack_mm: [r3(-half)!, r3(half)!],
    z_extent_mm: [r3(-(half + reach))!, r3(half + reach)!],
    housing_r_mm: r3(rSo)!,
    yoke_r_in_mm: r3(rSi + slotH)!,
    slot_r_in_mm: r3(rSi)!,
    slot_r_out_mm: r3(rSi + slotH)!,
    slot_height_mm: r3(slotH)!,
    air_gap_mm: r3(gap)!,
    gap_r_in_mm: r3(rRo)!,
    gap_r_out_mm: r3(rSi)!,
    rotor_iron_r_in_mm: r3(rRi)!,
    rotor_iron_r_out_mm: r3(rMagIn)!,
    magnet_r_in_mm: r3(rMagIn)!,
    magnet_r_out_mm: r3(rMagOut)!,
    sleeve_thickness_mm: r3(sleeveT)!,
    sleeve_r_in_mm: sleeveT > 0 ? r3(rMagOut) : null,
    sleeve_r_out_mm: sleeveT > 0 ? r3(rRo) : null,
    shaft_r_out_mm: r3(rRi)!,
    shaft_r_in_mm: r3(rBore)!,
    bore_r_mm: r3(rBore)!,
    shaft_extension_mm: r3(ext)!,
    end_winding_overhang_mm: r3(lEnd)!,
    end_winding_r_in_mm: r3(rSi)!,
    end_winding_r_out_mm: r3(rSi + slotH)!,
    k_end: r3(k, 4),
    num_slots: Math.round(f0(g.num_slots)) || null,
    num_poles: Math.round(f0(g.num_poles)) || null,
  };
}

/* ── 2. the sinks ────────────────────────────────────────────────────────── */

/** The legend's order: the big one first on a robot joint, then the axial
 *  faces, then the rotor's own paths. */
const SINK_ORDER: SinkId[] = [
  'mount', 'housing', 'end_face_winding', 'end_face_stator',
  'end_windings', 'slot_channels', 'bore', 'shaft_ends',
  'end_face_rotor', 'end_face_magnet',
];

/** The bore radius the SOLVE used: `inner.r_bore_mm` on a live payload, else
 *  inverted from the area it measured (`area = 2πrL`), which gives the same
 *  number back on a compact per-duty record that does not keep the radius. */
export function boreRadiusMm(cooling: Dict, stackMm: number): number | null {
  const inner = d(cooling.inner);
  const r = f(inner.r_bore_mm);
  if (r !== null && r > 0) return r;
  const a = f(inner.area_m2);
  if (a && a > 0 && stackMm > 0) return a / (2 * Math.PI * (stackMm * 1e-3)) * 1e3;
  return null;
}

interface SinkSeed {
  id: SinkId; group: 'stator' | 'rotor'; label: string; short: string;
  W: number; active: boolean; placement: HeatPathPlacement;
  mode?: string | null; detail?: { label: string; W: number }[];
  h?: number | null; G?: number | null; area?: number | null;
  tSurface?: number | null; tSink?: number | null; note?: string;
}

const mkSink = (s: SinkSeed): HeatSink => ({
  id: s.id, group: s.group, label: s.label, short: s.short,
  mode: s.mode ?? null, active: s.active,
  W: r3(s.W)!, pct: null, intensity: 0,
  h_W_per_m2K: r3(s.h ?? null), G_W_per_K: r3(s.G ?? null, 5),
  area_m2: r3(s.area ?? null, 6),
  t_surface_c: r3(s.tSurface ?? null, 2), t_sink_c: r3(s.tSink ?? null, 2),
  detail: s.detail ?? [], placement: s.placement, note: s.note ?? '',
});

/**
 * The heat-path model of ONE thermal result.
 *
 * `res` is the `/api/thermal/field` payload (or a compact per-duty record — the
 * `cooling` block is the only thing read).  `geometry` is `motorStore.geometry`
 * as the API serves it.  `point` supplies the ambient when the cooling block
 * does not.
 *
 * Returns `{ ok: false, reason }` when there is no cooling block: a view that
 * cannot say where the heat went must say so, not draw an empty machine that
 * looks like a solved one.
 */
export function buildHeatPathModel(
  res: Dict | null | undefined,
  geometry?: Dict | null,
  point?: Dict | null,
): HeatPathModel {
  const blank: MachineEnvelope = { units: 'mm', known: false };
  const emptyTotals: HeatPathTotals = {
    generated_W: null, removed_W: 0, residual_W: null, residual_pct: null,
    stator_side_W: 0, rotor_side_W: 0, stator_side_pct: null, rotor_side_pct: null,
  };
  const r = d(res);
  const cooling = d(r.cooling);
  if (!Object.keys(cooling).length) {
    return {
      ok: false, reason: 'no cooling block in this result',
      schema_version: HEAT_PATH_SCHEMA_VERSION, cooling_mode: 'none',
      ambient_c: null, totals: emptyTotals, sinks: [], geometry: blank,
    };
  }

  const pt = point ? d(point) : d(r.point);
  const budget = d(cooling.heat_budget);
  const outer = d(cooling.outer);
  const inner = d(cooling.inner);
  const mount = d(cooling.mount);
  const ends = d(cooling.end_faces);
  const stubs = d(cooling.shaft_ends);
  const ew = d(cooling.end_windings);
  const ch = d(cooling.slot_channels);

  const geo = d(geometry);
  const stackMm = f0(geo.motor_length);
  const env = machineEnvelope(geo, {
    kEnd: f(ends.k_end),
    shaftExtMm: f0(stubs.length_each_side_mm, f0(pt.shaft_ext_length_mm)),
    boreRmm: boreRadiusMm(cooling, stackMm),
  });

  const half = env.known ? (env.z_stack_mm?.[1] ?? 0) : 0;
  const rHouse = env.housing_r_mm ?? null;
  const lEnd = env.end_winding_overhang_mm ?? 0;
  const ext = env.shaft_extension_mm ?? 0;

  const efSides = Math.round(f0(ends.sides));
  // WHICH ends are open. 1 = the flange side is shut, so the open face is +z
  // alone; 2 = both. 0 = none, and the band is drawn grey.
  const efZ = efSides === 1 ? [1] : (efSides >= 2 ? [-1, 1] : []);
  const stubMode = String(stubs.mode ?? 'off');
  const stubSides = stubMode !== 'off' ? Math.round(f0(stubs.sides, 2)) : 0;
  const stubZ = stubSides === 1 ? [1] : (stubSides >= 2 ? [-1, 1] : []);

  const ambient = f(pt.ambient_temp) ?? f(outer.t_sink_c);
  const sinks: HeatSink[] = [];

  /* the housing cylinder */
  const oMode = String(outer.mode ?? 'none');
  const convW = f(budget.housing_convection_W) ?? f(outer.convection_W);
  const radW = f(budget.housing_radiation_W) ?? f(outer.radiation_W);
  const oW = f0(outer.heat_removed_W, f0(budget.housing_W));
  sinks.push(mkSink({
    id: 'housing', group: 'stator', mode: oMode,
    label: oMode === 'liquid' ? 'Housing — liquid jacket'
      : oMode === 'robotics' ? 'Housing — still air + radiation'
        : oMode === 'air' ? 'Housing — forced air' : 'Housing',
    short: 'housing', W: oW, active: oMode !== 'none' && Math.abs(oW) > 1e-9,
    detail: (convW !== null && radW !== null && (convW || radW))
      ? [{ label: 'convection', W: r3(convW)! }, { label: 'radiation', W: r3(radW)! }]
      : [],
    h: f(outer.h_total) ?? f(outer.h_conv), area: f(outer.area_m2),
    tSurface: f(outer.t_wall_c), tSink: f(outer.t_sink_c),
    placement: { kind: 'cylinder', facing: 'out', r_mm: rHouse, z0_mm: r3(-half), z1_mm: r3(half) },
    note: String(outer.regime ?? ''),
  }));

  /* the bolted mount — ONE end annulus of the housing */
  const mMode = String(mount.mode ?? 'off');
  const mW = f0(mount.heat_removed_W, f0(budget.mount_W));
  sinks.push(mkSink({
    id: 'mount', group: 'stator', mode: mMode,
    label: 'Mount — bolted flange (conduction)', short: 'mount',
    W: mW, active: mMode === 'conduction' && Math.abs(mW) > 1e-9,
    G: f(mount.G_W_per_K), tSurface: f(mount.t_housing_mean_c), tSink: f(mount.t_sink_c),
    // The conductance is LUMPED: the solve never says which end the arm is on,
    // so the picture draws one side and must not claim two.
    placement: {
      kind: 'annulus', r_in_mm: env.yoke_r_in_mm ?? null, r_out_mm: rHouse,
      sides: [-1], z_mm: r3(half),
    },
    note: String(mount.t_sink_source ?? ''),
  }));

  /* the four axial end faces */
  const efMode = String(ends.mode ?? 'off');
  const faces: [string, SinkId, 'stator' | 'rotor', string, string,
    number | null | undefined, number | null | undefined][] = [
    ['winding', 'end_face_winding', 'stator', 'End windings — axial faces',
      'end turns', env.end_winding_r_in_mm, env.end_winding_r_out_mm],
    ['stator', 'end_face_stator', 'stator', 'Stator core — end annulus',
      'stator ends', env.slot_r_out_mm, env.housing_r_mm],
    ['rotor', 'end_face_rotor', 'rotor', 'Rotor core — end annulus',
      'rotor ends', env.rotor_iron_r_in_mm, env.rotor_iron_r_out_mm],
    ['magnet', 'end_face_magnet', 'rotor', 'Magnets — end annulus',
      'magnet ends', env.magnet_r_in_mm, env.magnet_r_out_mm],
  ];
  for (const [node, id, group, label, short, rIn, rOut] of faces) {
    const blk = d(ends[node]);
    const w = f0(blk.heat_removed_W);
    const mode = String(blk.mode ?? efMode);
    const nF = Math.round(f0(blk.n_faces));
    sinks.push(mkSink({
      id, group, mode, label, short, W: w,
      active: mode === 'still' && Math.abs(w) > 1e-12,
      h: f(blk.h_total), area: f(blk.area_m2), G: f(blk.G_W_per_K),
      tSurface: f(blk.t_mean_c), tSink: f(blk.t_sink_c),
      placement: node === 'winding'
        // The end turns are a BAND: they stand ℓ_end proud of the core on each
        // side, and that is the surface the film acts on.
        ? {
          kind: 'band', r_in_mm: rIn ?? null, r_out_mm: rOut ?? null,
          length_mm: r3(lEnd), sides: efZ.length ? efZ : [-1, 1], z_mm: r3(half),
        }
        : { kind: 'annulus', r_in_mm: rIn ?? null, r_out_mm: rOut ?? null, sides: efZ, z_mm: r3(half) },
      note: nF ? `${nF} face(s)` : '',
    }));
  }

  /* the open frame's two paths */
  const ewMode = String(ew.mode ?? 'housed');
  const ewW = f0(ew.heat_removed_W, f0(budget.end_windings_W));
  sinks.push(mkSink({
    id: 'end_windings', group: 'stator', mode: ewMode,
    label: 'End windings — in the wash (open frame)', short: 'end turns',
    W: ewW, active: ewMode !== 'housed' && ewMode !== 'off' && Math.abs(ewW) > 1e-9,
    h: f(ew.h_conv), tSink: f(ew.t_sink_c),
    placement: {
      kind: 'band', r_in_mm: env.end_winding_r_in_mm ?? null,
      r_out_mm: env.end_winding_r_out_mm ?? null,
      length_mm: r3(lEnd), sides: [-1, 1], z_mm: r3(half),
    },
  }));
  const chMode = String(ch.mode ?? 'housed');
  const chW = f0(ch.heat_removed_W, f0(budget.slot_channels_W));
  sinks.push(mkSink({
    id: 'slot_channels', group: 'stator', mode: chMode,
    label: 'Slot channels — axial ducts (open frame)', short: 'slot ducts',
    W: chW, active: chMode !== 'housed' && chMode !== 'off' && Math.abs(chW) > 1e-9,
    h: f(ch.h_conv), tSink: f(ch.t_sink_c),
    placement: {
      kind: 'cylinder', facing: 'in', r_mm: env.slot_r_out_mm ?? null,
      z0_mm: r3(-half), z1_mm: r3(half),
    },
  }));

  /* the bore */
  const iMode = String(inner.mode ?? 'none');
  const iW = f0(inner.heat_removed_W, f0(budget.bore_W));
  const iConv = f(inner.convection_W); const iRad = f(inner.radiation_W);
  sinks.push(mkSink({
    id: 'bore', group: 'rotor', mode: iMode,
    label: iMode === 'still' ? 'Bore — open, still air'
      : iMode === 'air' ? 'Bore — forced air'
        : iMode === 'liquid' ? 'Bore — liquid' : 'Bore',
    short: 'bore', W: iW, active: iMode !== 'none' && Math.abs(iW) > 1e-9,
    detail: (iConv !== null && iRad !== null)
      ? [{ label: 'convection', W: r3(iConv)! }, { label: 'radiation', W: r3(iRad)! }] : [],
    h: f(inner.h_total) ?? f(inner.h_conv), area: f(inner.area_m2),
    tSurface: f(inner.t_wall_c), tSink: f(inner.t_sink_c),
    placement: {
      kind: 'cylinder', facing: 'in', r_mm: env.bore_r_mm ?? null,
      z0_mm: r3(-half), z1_mm: r3(half),
    },
    note: String(inner.regime ?? ''),
  }));

  /* the shaft stubs */
  const sW = f0(stubs.heat_removed_W, f0(budget.shaft_ends_W));
  const fin = f(stubs.fin_efficiency);
  sinks.push(mkSink({
    id: 'shaft_ends', group: 'rotor', mode: stubMode,
    label: 'Shaft ends — fin in ambient air', short: 'shaft ends',
    W: sW, active: stubMode !== 'off' && Math.abs(sW) > 1e-9,
    h: f(stubs.h_conv), G: f(stubs.G_W_per_K),
    tSurface: f(stubs.t_shaft_mean_c), tSink: f(stubs.t_sink_c),
    placement: {
      kind: 'stub', r_mm: env.shaft_r_out_mm ?? null, r_in_mm: env.bore_r_mm ?? null,
      length_mm: r3(ext), sides: stubZ.length ? stubZ : [-1, 1], z_mm: r3(half),
    },
    note: fin !== null ? `fin efficiency ${Math.round(fin * 100)} %` : '',
  }));

  /* shares, and the balance */
  const order = new Map(SINK_ORDER.map((id, i) => [id, i] as const));
  sinks.sort((a, b) => (order.get(a.id) ?? 99) - (order.get(b.id) ?? 99));
  const removed = sinks.reduce((acc, s) => acc + s.W, 0);
  // The share is of what LEFT, not of what was made: a share of the generation
  // moves with the closure error and would read as physics.
  for (const s of sinks) s.pct = Math.abs(removed) > 1e-9 ? r3(100 * s.W / removed, 1) : null;

  const generated = f(budget.losses_W) ?? f(r.P_loss_total_W);
  const resid = f(budget.residual_W) ?? (generated !== null ? generated - removed : null);

  // 0…1 against the BIGGEST path, not the share: on the Ø85 joint the mount is
  // 86 % and everything else would be invisible on a share scale, while against
  // the peak the end turns still show.
  const peak = sinks.reduce((m, s) => (s.active ? Math.max(m, Math.abs(s.W)) : m), 0);
  for (const s of sinks) {
    s.intensity = peak > 1e-12 && s.active ? r3(Math.abs(s.W) / peak, 4)! : 0;
  }

  const statorW = sinks.reduce((a, s) => a + (s.group === 'stator' ? s.W : 0), 0);
  const rotorW = sinks.reduce((a, s) => a + (s.group === 'rotor' ? s.W : 0), 0);

  return {
    ok: true,
    schema_version: HEAT_PATH_SCHEMA_VERSION,
    cooling_mode: String(pt.cooling_mode ?? outer.mode ?? 'none'),
    ambient_c: r3(ambient, 2),
    totals: {
      generated_W: r3(generated),
      removed_W: r3(removed)!,
      residual_W: r3(resid),
      residual_pct: (resid !== null && generated && Math.abs(generated) > 1e-9)
        ? r3(100 * resid / generated, 2) : null,
      stator_side_W: r3(statorW)!,
      rotor_side_W: r3(rotorW)!,
      stator_side_pct: Math.abs(removed) > 1e-9 ? r3(100 * statorW / removed, 1) : null,
      rotor_side_pct: Math.abs(removed) > 1e-9 ? r3(100 * rotorW / removed, 1) : null,
    },
    sinks,
    geometry: env,
  };
}

/* ── 3. how a sink READS: the colour and the label ───────────────────────── */

/** A path that is switched off. Grey is a statement — "this machine is bolted
 *  to nothing" — and must not look like a path carrying almost no heat. */
export const SINK_OFF_COLOUR = '#4b5563';

/** Three stops, slate→amber→red, sampled on `intensity` (share of the BIGGEST
 *  path). Chosen so the dominant sink is unmistakable and a 2 % path is still
 *  visibly not grey — on the Ø85 joint the mount must read as the answer and
 *  the end turns must still be findable.
 *
 *  The stops are picked so RED RISES and BLUE FALLS across the whole ramp,
 *  which is what makes it read as one scale rather than three colours: a hot
 *  stop with more blue in it than the amber (tailwind's #ef4444 has 0x44
 *  against 0x0b) inverts halfway and the picture stops meaning anything. */
const RAMP: [number, [number, number, number]][] = [
  [0.0, [0x2f, 0x4f, 0x6f]],   // slate — barely any of it goes this way
  [0.5, [0xe8, 0xa2, 0x1c]],   // amber
  [1.0, [0xff, 0x2a, 0x10]],   // red — this is where the heat leaves
];

const hex2 = (n: number): string =>
  Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');

/** `intensity` (0…1) → a CSS hex colour on the ramp above. Out-of-range and
 *  non-finite values clamp rather than throw: a colour is not a place to
 *  discover a NaN. */
export function heatColour(intensity: number): string {
  const t = Number.isFinite(intensity) ? Math.max(0, Math.min(1, intensity)) : 0;
  let i = 0;
  while (i < RAMP.length - 2 && t > RAMP[i + 1][0]) i++;
  const [t0, c0] = RAMP[i];
  const [t1, c1] = RAMP[i + 1];
  const u = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
  return `#${c0.map((c, k) => hex2(c + (c1[k] - c) * u)).join('')}`;
}

/** The colour a SINK is drawn in: grey when it is off, the ramp otherwise. */
export function sinkColour(sink: Pick<HeatSink, 'active' | 'intensity'>): string {
  return sink.active ? heatColour(sink.intensity) : SINK_OFF_COLOUR;
}

/** The same colour, mixed `f` of the way towards white — what an arrow is drawn
 *  in while the pointer is on it.  Mixing towards white rather than switching to
 *  a highlight colour keeps the ramp readable: a hovered 2 % path must still
 *  look like a 2 % path, only brighter. */
export function brighten(hex: string, f = 0.45): string {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const t = Number.isFinite(f) ? Math.max(0, Math.min(1, f)) : 0;
  return `#${[(n >> 16) & 255, (n >> 8) & 255, n & 255]
    .map((c) => hex2(c + (255 - c) * t)).join('')}`;
}

/** Watts, at a resolution that does not pretend: 48.5 W, 0.59 W, 5.6 kW. */
export function fmtW(w: number | null | undefined): string {
  if (w === null || w === undefined || !Number.isFinite(w)) return '—';
  const a = Math.abs(w);
  if (a >= 1000) return `${(w / 1000).toFixed(a >= 10000 ? 0 : 2)} kW`;
  if (a >= 10) return `${w.toFixed(1)} W`;
  if (a >= 1) return `${w.toFixed(2)} W`;
  return `${w.toFixed(2)} W`;
}

/**
 * The billboard over a surface: what it is, how much left through it, and what
 * share of the outflow that is — "mount 48.5 W · 86 %".
 *
 * A path that is OFF says so by name ("shaft ends — none"): a blank label would
 * read as a measurement of zero, and "nothing sticks out of this housing" is an
 * answer about the machine.  The convection/radiation split rides along when
 * the solver reported it, because on a small machine in still air radiation
 * carries more than convection and the single number hides it.
 */
export function sinkLabel(sink: HeatSink, setting?: string | null): string {
  const set = setting ? ` ${setting}` : '';
  if (!sink.active) return `${sink.short}${set || ' — none'}`;
  const pct = sink.pct === null ? '' : ` · ${Math.round(sink.pct)} %`;
  const parts = sink.detail.length === 2
    ? ` (${sink.detail.map((p) => p.W.toFixed(sink.detail.some((q) => Math.abs(q.W) < 1) ? 2 : 1)).join(' + ')})`
    : '';
  return `${sink.short}${set ? `${set} ·` : ''} ${fmtW(sink.W)}${parts}${pct}`;
}

/* ── 4. the SETTINGS side: what each surface is set TO ───────────────────── */

/**
 * The cooling fields this view can read and write — the thermalStore's own
 * names and its own string-typed values, so the panel's fields and the model's
 * popovers are literally the same state and cannot drift apart.
 *
 * Strings, not numbers, because that is what a text field holds: `''` is "not
 * typed" and must not become `Number('') === 0` (a 0 °C ambient nobody asked
 * for).  `mountT` blank means the ambient, which is the panel's own rule.
 */
export interface CoolingSettings {
  coolMode: string; ambientT: string; airSpeed: string; hConv: string;
  fluid?: string; tIn: string; flowLpm: string;
  boreMode: string; boreAirSpeed: string; boreTIn: string; boreFlowLpm: string;
  shaftExtMm: string; shaftExtSides: string;
  frame: string; openAirSpeed: string;
  emissivity: string; mountG: string; mountT: string;
  endFaces: string; endFaceSides: string;
}

/** Which popover a surface opens.  `null` = this path is not set anywhere —
 *  it is a consequence of the others, so clicking it offers nothing. */
export type SinkEditor = 'housing' | 'mount' | 'end_faces' | 'bore'
  | 'shaft_ends' | 'frame';

export function editorFor(id: SinkId): SinkEditor | null {
  switch (id) {
    case 'housing': return 'housing';
    case 'mount': return 'mount';
    case 'end_face_winding': case 'end_face_stator':
    case 'end_face_rotor': case 'end_face_magnet': return 'end_faces';
    case 'bore': return 'bore';
    case 'shaft_ends': return 'shaft_ends';
    case 'end_windings': case 'slot_channels': return 'frame';
    default: return null;
  }
}

const nz = (s: string, def: string): string => (s ?? '').trim() || def;

/**
 * What this surface is SET to, in the fewest characters that still say it —
 * "2 W/K @ 40 °C", "ε 0.9 @ 40 °C", "2 ends", "still air", "20 mm × 2".
 *
 * It goes in front of the watts on the billboard, so one line answers both
 * questions a cooling design asks of a surface: what did I tell it to be, and
 * what did that buy.  `off` is a setting and is printed as one — a surface with
 * no label at all would read as a surface nobody had decided about.
 */
export function settingLabel(id: SinkId, s: CoolingSettings | null | undefined): string | null {
  if (!s) return null;
  const amb = nz(s.ambientT, '—');
  switch (editorFor(id)) {
    case 'housing':
      if (s.coolMode === 'robotics') return `ε ${nz(s.emissivity, '0.9')} @ ${amb} °C`;
      if (s.coolMode === 'air') return `${nz(s.airSpeed, '0')} m/s @ ${amb} °C`;
      if (s.coolMode === 'liquid') return `${nz(s.flowLpm, '8')} L/min @ ${nz(s.tIn, amb)} °C`;
      if (s.coolMode === 'manual') return `h ${nz(s.hConv, '—')} W/m²K`;
      return 'no cooling';
    case 'mount': {
      const g = Number(s.mountG);
      if (!(g > 0)) return 'off';
      // blank mount °C means the ambient — the panel's rule, said out loud
      return `${s.mountG} W/K @ ${nz(s.mountT, amb)} °C`;
    }
    case 'end_faces':
      if (s.coolMode !== 'robotics' || s.endFaces === 'none') return 'closed';
      return `${nz(s.endFaceSides, '2')} end${nz(s.endFaceSides, '2') === '1' ? '' : 's'} open`;
    case 'bore':
      if (s.boreMode === 'none') return 'closed';
      if (s.boreMode === 'still') return 'still air';
      if (s.boreMode === 'air') return `${nz(s.boreAirSpeed, '0')} m/s`;
      if (s.boreMode === 'liquid') return `${nz(s.boreFlowLpm, '0')} L/min`;
      return null;
    case 'shaft_ends': {
      const mm = Number(s.shaftExtMm);
      if (!(mm > 0)) return 'off';
      return `${s.shaftExtMm} mm × ${nz(s.shaftExtSides, '2')}`;
    }
    case 'frame':
      // The wash speed is no longer its own setting (2026-09-21): an open
      // frame always takes the housing's own outer-surface air speed, so a
      // leftover `openAirSpeed` from before that date is not read here —
      // printing it would claim a control that no longer exists.
      return s.frame === 'open' ? 'open, housing air' : 'housed';
    default:
      return null;
  }
}

/**
 * A cooling block built from the SETTINGS alone, for a machine nothing has been
 * solved for yet.
 *
 * Every watt is 0 and every share is therefore absent — this is not a result
 * and must never be mistaken for one — but the modes are the real ones, so the
 * picture shows which surfaces are open and lets them be set before the first
 * Solve rather than after it.  `heat_budget` is deliberately absent: a budget
 * with no numbers in it is what a reader would quote.
 */
export function coolingFromSettings(s: CoolingSettings): Dict {
  const robot = s.coolMode === 'robotics';
  const ef = robot && s.endFaces !== 'none' ? 'still' : 'off';
  const sides = ef === 'still' ? (Number(s.endFaceSides) === 1 ? 1 : 2) : 0;
  const face = { mode: ef, heat_removed_W: 0 };
  return {
    outer: { mode: s.coolMode, heat_removed_W: 0 },
    inner: { mode: s.boreMode, heat_removed_W: 0 },
    mount: { mode: Number(s.mountG) > 0 ? 'conduction' : 'off', heat_removed_W: 0 },
    end_faces: {
      mode: ef, sides, heat_removed_W: 0,
      winding: face, stator: face, rotor: face, magnet: face,
    },
    shaft_ends: {
      mode: Number(s.shaftExtMm) > 0 ? 'still' : 'off', heat_removed_W: 0,
      length_each_side_mm: Number(s.shaftExtMm) || 0,
      sides: Number(s.shaftExtSides) === 1 ? 1 : 2,
    },
    end_windings: { mode: s.frame === 'open' ? 'air' : 'housed', heat_removed_W: 0 },
    slot_channels: { mode: s.frame === 'open' ? 'air' : 'housed', heat_removed_W: 0 },
  };
}

/** The one-line hover behind the billboard: the film or the conductance, the
 *  area, and the two temperatures the watts came out of.  Project rule: one
 *  short line on screen, everything else in the tooltip. */
export function sinkTooltip(sink: HeatSink): string {
  if (!sink.active) {
    return `${sink.label}. This machine has no such path (mode: ${sink.mode ?? '—'}), so nothing leaves here.`;
  }
  const bits: string[] = [`${sink.label}: ${fmtW(sink.W)}`];
  if (sink.pct !== null) bits.push(`${sink.pct} % of everything that left`);
  if (sink.detail.length === 2) {
    bits.push(sink.detail.map((p) => `${p.label} ${fmtW(p.W)}`).join(' + '));
  }
  if (sink.G_W_per_K !== null) bits.push(`G ${sink.G_W_per_K} W/K`);
  if (sink.h_W_per_m2K !== null) bits.push(`h ${sink.h_W_per_m2K} W/m²K`);
  if (sink.area_m2 !== null) bits.push(`over ${sink.area_m2} m²`);
  if (sink.t_surface_c !== null && sink.t_sink_c !== null) {
    bits.push(`${sink.t_surface_c} °C → ${sink.t_sink_c} °C`);
  }
  if (sink.note) bits.push(sink.note);
  return `${bits.join(' · ')}.`;
}

/* ── 5. the ARROWS: what the channel IS, and how much goes down it ────────── */

/**
 * The MECHANISM one arrow stands for, in the fewest words that still name it.
 *
 * User, 2026-09-16: *"добавить подсказки, когда наводишь курсором на стрелки:
 * что она означает и сколько тепла уходит через этот канал"*.  An arrow on the
 * picture is a channel, and a channel is not identified by its watts: 48.5 W
 * out of a flange is conduction into an arm and 48.5 W off a cylinder is a film
 * in the room, and the reader has to be told which one he is looking at.
 */
export function arrowMechanism(sink: Pick<HeatSink, 'id' | 'mode'>): string {
  const mode = sink.mode ?? '';
  switch (sink.id) {
    case 'mount': return 'conduction into the mount';
    case 'housing':
      return mode === 'liquid' ? 'liquid jacket on the housing'
        : mode === 'air' ? 'forced air over the housing'
          : mode === 'robotics' ? 'still air + radiation off the housing'
            : mode === 'manual' ? 'imposed film on the housing'
              : 'no cooling on the housing';
    case 'end_face_winding': return 'still air on the end-winding faces';
    case 'end_face_stator': return 'still air on the stator end annulus';
    case 'end_face_rotor': return 'still air on the rotor end annulus';
    case 'end_face_magnet': return 'still air on the magnet end annulus';
    case 'bore':
      return mode === 'air' ? 'forced air through the bore'
        : mode === 'liquid' ? 'liquid through the bore'
          : mode === 'still' ? 'still air in the bore' : 'closed bore';
    case 'shaft_ends': return 'shaft stub as a fin in ambient air';
    case 'end_windings': return 'air over the end turns';
    case 'slot_channels': return 'air through the slot ducts';
    default: return 'convection';
  }
}

/** What the two temperatures on an arrow are called.  "64 → 40 °C" is a number
 *  pair; "housing 64 → mount 40 °C" is the equation the watts came out of. */
const TEMP_NAMES: Record<string, [string, string]> = {
  mount: ['housing', 'mount'],
  housing: ['wall', 'air'],
  bore: ['wall', 'air'],
  shaft_ends: ['shaft', 'air'],
  end_face_winding: ['end turns', 'air'],
  end_face_stator: ['face', 'air'],
  end_face_rotor: ['face', 'air'],
  end_face_magnet: ['face', 'air'],
  end_windings: ['end turns', 'air'],
  slot_channels: ['slot', 'air'],
};

/** "both sides" / "one side" — an axial path drawn on one end of the machine is
 *  usually paid for on two, and the arrow cannot show that by itself. */
function sidesPhrase(sink: HeatSink): string | null {
  const k = sink.placement.kind;
  if (k !== 'annulus' && k !== 'band' && k !== 'stub') return null;
  const n = sink.placement.sides?.length ?? 0;
  if (n >= 2) return 'both sides';
  if (n === 1) return 'one side';
  return null;
}

export interface ArrowTip {
  /** the one short line: which path, how many watts, what share of the outflow */
  head: string;
  /** the second line: the mechanism, its coefficient and the two temperatures */
  mech: string;
}

/**
 * The hover on ONE arrow — two lines, never a paragraph.
 *
 *   head: "Mount — bolted flange (conduction) — 48.5 W · 86.4 % of what leaves"
 *   mech: "conduction into the mount, G 2 W/K, housing 64.27 → mount 40 °C."
 *
 * Everything is read off the SAME sink the tint and the billboard use, so the
 * arrow cannot say a different number from the label beside it.  An arrow is
 * only drawn for an active path, but an off sink is handled anyway: the legend
 * hands this function whatever the user points at.
 */
export function arrowTooltip(sink: HeatSink): ArrowTip {
  if (!sink.active) {
    return {
      head: `${sink.label} — nothing leaves here`,
      mech: `${arrowMechanism(sink)} is off (mode: ${sink.mode ?? '—'}).`,
    };
  }
  const share = sink.pct === null ? '' : ` · ${sink.pct} % of what leaves`;
  const head = `${sink.label} — ${fmtW(sink.W)}${share}`;

  const mech = arrowMechanism(sink);
  const bits: string[] = [mech];
  const sides = sidesPhrase(sink);
  if (sides) bits.push(sides);
  if (sink.G_W_per_K !== null) bits.push(`G ${sink.G_W_per_K} W/K`);
  else if (sink.h_W_per_m2K !== null) bits.push(`h ${sink.h_W_per_m2K} W/m²K`);
  if (sink.detail.length === 2) {
    bits.push(sink.detail.map((p) => `${p.label} ${fmtW(p.W)}`).join(' + '));
  }
  const [hot, cold] = TEMP_NAMES[sink.id] ?? ['surface', 'sink'];
  if (sink.t_surface_c !== null && sink.t_sink_c !== null) {
    bits.push(`${hot} ${sink.t_surface_c} → ${cold} ${sink.t_sink_c} °C`);
  } else if (sink.t_sink_c !== null) {
    bits.push(`into ${cold} at ${sink.t_sink_c} °C`);
  }
  // The note earns its place only if it says something new: the face count is
  // already "both sides", and the regime ("still air") is already the
  // mechanism.  A line that says the same thing twice reads as two findings.
  if (sink.note && !/face\(s\)/.test(sink.note)
      && !mech.toLowerCase().includes(sink.note.toLowerCase())) {
    bits.push(sink.note);
  }
  return { head, mech: `${bits.join(', ')}.` };
}
