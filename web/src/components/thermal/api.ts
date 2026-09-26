/** Types and fetches for the Thermal tab.
 *
 * Mirrors `routes/thermal.py` one-for-one, and is written to the same rule the
 * mechanical API is: the fields that look like decoration (`cached`,
 * `elapsed_s`, `geometry_fingerprint`, the echoed cooling block) are the ones
 * that say how much the headline number is worth.  A 160 °C winding whose
 * provenance is hidden is a number nobody can act on.
 *
 * The thermal solve lived inside the Electromagnetic tab's field viewer until
 * 2026-09-07 (a "Temp" entry in the EM output menu, with the cooling inputs in
 * that viewer's toolbar).  It is a different physics with its own mesh, its own
 * boundary conditions and its own minute-long solve, so it is now its own tab —
 * modelled on Mechanical, which had already been split out for the same reason.
 */
import { currentGeoJson, currentMatJson } from '../../lib/apiAuth';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${API.replace(/\/$/, '')}/api/thermal`;

export type {
  ThermalComponent, ThermalCooling, ThermalCoolingSurface, ThermalEndFace,
  ThermalEndFaces, ThermalEndWindings, ThermalGapInfo, ThermalHeatBudget,
  ThermalMount, ThermalPayload, ThermalShaftEnds, ThermalSleeveInfo,
  ThermalSlotChannels, ThermalStatorHeatSplit,
} from './types';
/** The one reader of the cooling block — nested today, flat in a payload cached
 *  before the two-surface split (2026-09-07). */
export { outerCooling } from './types';
import type { ThermalPayload } from './types';

/* The number / duration formatters are the mechanical tab's, imported rather
   than copied: two spellings of `fmt` is two ways for the same value to be
   printed differently, which is exactly what the shared field viewer was built
   to stop (2026-09-06). */
export { fmt, fmtSecs, fmtClock, readSimSetting } from '../mechanical/api';
import { readSimSetting } from '../mechanical/api';
import { EDDY_DEFAULT_STEPS } from '../../lib/eddySteps';

/** How the OUTER stator surface is cooled: air blown at a speed, a liquid loop,
 *  a hand-typed film coefficient — or nothing at all.
 *
 *  `manual` exists because a real cooling design is often quoted to an engineer
 *  as an h — and inventing an air speed that happens to produce it would be a
 *  boundary condition nobody chose.  `none` exists because a machine cooled
 *  through its BORE alone is a real machine (2026-09-07), and pretending the
 *  housing is still losing heat would flatter it.
 *
 *  `robotics` (2026-09-14) is ONE mode and not four switches, by the user's
 *  decision: a joint bolted to an arm and standing in a room — still-air housing
 *  with an emissivity, an OPEN bore in still air, the exposed AXIAL end faces of
 *  the coils / cores / magnets, and a bolted MOUNT conductance.  Scattered as
 *  independent fields, a half-configured machine would look exactly like a
 *  converged answer.  Mirrors `thermal_settings.COOL_MODES`. */
export type CoolMode = 'air' | 'liquid' | 'manual' | 'none' | 'robotics';

/** How the INNER rotor bore (the hollow shaft's inner diameter) is cooled.  Off
 *  by default: most machines have a solid shaft and nothing flows through it.
 *  There is no `manual` here — a bore h nobody can quote is a number, not a
 *  cooling system; use the outer surface's manual mode for that.
 *
 *  `still` is the unventilated, open bore of the robotics mode and is refused by
 *  name outside it: it is evaluated with that mode's `emissivity`, an input no
 *  other mode sends. */
export type BoreMode = 'none' | 'air' | 'liquid' | 'still';

/** The AXIAL end faces, robotics only: `still` (the end turns stand proud of the
 *  core on both sides and the core / magnet end faces are uncovered) or `none`
 *  (both ends buried against a gearbox and the arm).  Mirrors
 *  `thermal_settings.END_FACE_MODES`. */
export type EndFaceMode = 'still' | 'none';

/** How the machine is BUILT — which decides whether the end windings and the
 *  slot air are in the airflow at all.
 *
 *  `housed` is every normally-built motor and the model this tab has always
 *  solved: the end turns and the slot air are inside a closed housing, so
 *  whatever they hand to that air comes straight back through the housing.
 *  `open` is the 40 mm CIANO14 (user 2026-09-09: *нет корпуса*) — the tooth
 *  blocks with their coils hang between two end plates on standoff pins and the
 *  end turns plus the axial channels between neighbouring coils sit in the
 *  propeller wash. */
export type FrameMode = 'housed' | 'open';

/** Which quantity the map draws.  Two, because the thermal solve produces
 *  exactly two fields: the temperature it solved for and the heat flux that
 *  follows from it. */
export type ThermView = 'temp' | 'flux' | 'grad';

/* ═══════════════════════════════════════════════════════════════════════════
 * The requests
 * ═══════════════════════════════════════════════════════════════════════════ */

/** Everything both solves take.  Split in two on purpose: the OPERATING POINT
 *  half is filled from the Electromagnetic tab's settings by the store and never
 *  from a default typed in this tab (standing project rule — every physics
 *  value of a run is read from where the user set it), the COOLING half is
 *  this tab's own. */
export interface ThermalRequest {
  /** the History notice's Recompute (2026-09-22): ignore a stored history
   *  answer and solve again, even for byte-identical inputs. */
  fresh?: boolean;
  // ── the OUTER stator surface ──────────────────────────────────────────────
  cooling_mode: CoolMode;
  /** ambient / air temperature, °C — also the temperature of the air blown
   *  through the bore, because it is the same air */
  ambient_temp: number;
  h_conv?: number;
  air_speed_mps?: number;
  fluid?: string;
  /** coolant INLET, °C.  There is no outlet input: the outlet is a result of
   *  the flow below (2026-09-07). */
  fluid_temp_in_c?: number;
  /** litres per minute — REQUIRED and > 0 in liquid mode */
  flow_lpm?: number;

  // ── the INNER rotor bore (hollow shaft), 'none' unless the user turns it on ─
  bore_mode: BoreMode;
  bore_air_speed_mps?: number;
  bore_fluid?: string;
  bore_fluid_temp_in_c?: number;
  bore_flow_lpm?: number;

  // ── the SHAFT OUTSIDE the housing, 0 mm (off) unless the user gives it one ─
  /* The rotor's third heat path (2026-09-07).  The rotor's end faces and the
     end windings turn inside a CLOSED housing and are deliberately not
     modelled — "торцы и лобовые части — только для вала, всё остальное
     вращается внутри мотора" — but the shaft comes out through the bearings and
     the exposed stubs lose heat to the room. */
  /** exposed length on EACH side, mm; 0 = the path is off */
  shaft_ext_length_mm?: number;
  /** OD of the exposed shaft, mm; omitted / 0 = derived from the geometry */
  shaft_ext_diameter_mm?: number;
  /** how many ends come out of the housing: 2 = through shaft, 1 = one capped */
  shaft_ext_sides?: number;

  // ── the FRAME, sent only when the machine is OPEN (2026-09-09) ─────────────
  /* Same rule as the shaft above and as everything else in `coolingFields`: a
     parameter the chosen mode does not use is not sent, because the solver keys
     its cache on it.  A `frame: 'housed'` on the wire would split the cache in
     two for one machine. */
  frame?: FrameMode;
  /** frame=open: air over the end turns and through the slot channels, m/s.
   *  0 is meaningful — the backend then takes the housing's own air speed when
   *  the outer surface is in air, and still air when it is not — so it is sent
   *  WITH the frame rather than gated on being > 0. */
  open_air_speed_mps?: number;

  // ── the ROBOTICS mode's own fields (2026-09-14) ───────────────────────────
  /* Same rule again: sent ONLY by the mode that reads them, so a liquid-jacket
     request keys the cache on exactly the tuple it always did. */
  /** total hemispherical emissivity of the housing, 0…1 — it decides more than
   *  half of what leaves a small machine standing in still air */
  emissivity?: number;
  /** are the machine's AXIAL faces exposed?  `still` = the end turns and the
   *  core / magnet end faces lose heat to the room */
  end_faces?: EndFaceMode;
  /** how many ends are exposed, 1 or 2 — never sent beside `end_faces: none` */
  end_face_sides?: number;
  /** the bolted flange's contact conductance to the arm, W/K.  Sent when there
   *  IS one: a zero conductance is the machine bolted to nothing, which is what
   *  the request without the field already means. */
  mount_g_w_per_k?: number;
  /** the temperature the mount is HELD at, °C — omitted means the ambient, and
   *  sending the ambient in its place would make a default look like a choice */
  mount_temp_c?: number;
  /** the mount's far side (2026-09-26): 'link' says it is a robot arm that
   *  heats up (see `link_preset`/`link_material`); omitted = 'sink', today's
   *  behaviour — the mount held at `mount_temp_c` for ever. */
  mount_mode?: 'sink' | 'link';
  /** the link's size, only read with `mount_mode: 'link'` */
  link_preset?: 'finger' | 'wrist' | 'arm';
  /** the link's material, only read with `mount_mode: 'link'` */
  link_material?: 'aluminium' | 'steel' | 'plastic';

  /** effective in-slot conductivity, W/m·K — omitted means the solver's own
   *  default, which is what this tab always sends today.  There is deliberately
   *  no `gap_k`: the air-gap conductivity is COMPUTED from the gap width and
   *  the rotor speed for every machine and comes back as a result. */
  slot_k?: number;
  // operating point (from the Electromagnetic tab)
  rpm: number;
  gamma_deg: number;
  I_phase_rms: number;
  coil_temp_c: number;
  n_steps_per_period: number;
  n_periods: number;
  // mesh (from the Mesh tab)
  mesh_size_mm: number;
  min_size_mm: number;
  outer_air_factor: number;
  n_sectors: number;
  /** JSON: per-part element size overrides, as the Mesh tab persists them */
  component_mesh: string;
}

/** A field answer, with what it cost and which machine it is of. */
export interface ThermalField extends ThermalPayload {
  elapsed_s?: number;
  solve_time_s?: number;
  cached?: boolean;
  geometry_fingerprint?: string | null;
  /** persistent history (2026-09-22) — see mechanical/api.ts's RotorStream
   *  for the same three fields and why they are named this way everywhere. */
  served_from_history?: boolean;
  computed_at?: string;
  history_key?: string;
}

/** The coupled EM↔thermal loop: losses raise the copper temperature, the hotter
 *  copper dissipates more, and the two are iterated to the fixed point (or the
 *  loop diverges, which IS the answer — see `runaway`). */
export interface CoupledResult {
  /** copper temperature after each iteration, °C — the feedback made visible */
  coil_temp_history_C: number[];
  coil_temp_converged_C: number;
  converged: boolean;
  runaway: boolean;
  iterations: number;
  field: ThermalField;
  P_cu_W?: number;
  elapsed_s?: number;
  solve_time_s?: number;
  cached?: boolean;
  geometry_fingerprint?: string | null;
}

/** One remembered answer, with the machine it was solved for.  Same shape the
 *  mechanical `/last` uses, for the same reason: re-entering a tab must restore
 *  what was on screen without solving anything. */
export interface LastEntry<T> {
  result: T;
  params: Record<string, unknown>;
  geometry_fingerprint: string | null;
  computed_at: string | null;
  /** the BACKEND's own verdict, from its fingerprint of the machine this was
   *  solved on against the live one.  `null` = UNKNOWN, never "fine". */
  stale_geometry: boolean | null;
}

export interface LastThermal {
  has_result: boolean;
  live_geometry_fingerprint: string | null;
  field: LastEntry<ThermalField> | null;
  coupled: LastEntry<CoupledResult> | null;
}

/** The solid cross-section as triangles — no fields, no solve. */
export interface ThermalMeshPayload {
  vertices: [number, number][];
  triangles: [number, number, number][];
  domain_per_tri: number[];
  part_names?: Record<string, string>;
  /** what the air re-tagger found — the SAME classification /field will solve,
   *  so the preview and the answer cannot disagree about where the liner is */
  air_domains?: import('./types').ThermalAirDomains;
  outlines?: { domain: number; loops: [number, number][][] }[];
  extent?: [number, number, number, number];
  n_vertices?: number;
  n_triangles: number;
  mesh_size_mm: number;
  n_sectors?: number;
  /** seconds of the mesher this cost to build */
  mesh_s?: number;
  cached?: boolean;
  elapsed_s?: number;
  geo_fingerprint?: string | null;
}

/** The mesh half of a request — the Build-mesh button sends only this. */
export interface ThermalMeshRequest {
  mesh_size_mm: number;
  min_size_mm: number;
  outer_air_factor: number;
  n_sectors: number;
  component_mesh: string;
}

/** The machine-readable tag on the ONE thermal refusal a caller can DO
 *  something about (`routes/thermal.NO_EM_RUN_CODE`): no Electromagnetic run of
 *  this machine at this operating point, so there is no loss map to heat it
 *  with.  A caller keys on THIS, never on the English sentence beside it — the
 *  sentence is written for a human and will be reworded, the code is a
 *  contract. */
export const NO_EM_RUN = 'no_electromagnetic_run';

/** A refusal, with that tag beside its sentence. */
export interface ApiRefusal extends Error { code?: string | null }

/** Is this the refusal the Thermal tab answers by making the run itself
 *  (through the orchestrator — never by solving anything here)? */
export const isNoEmRun = (e: unknown): boolean =>
  e instanceof Error && (e as ApiRefusal).code === NO_EM_RUN;

async function get<T>(
  path: string,
  params: Record<string, string | number | boolean | null | undefined>,
): Promise<T> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined) qs.set(k, String(v));
  });
  // The fetch interceptor appends `geo=` only to the simulation/geometry
  // compute routes (see lib/apiAuth.COMPUTE_RE), so this tab attaches the
  // active machine ITSELF.  Without it the backend would solve the shared
  // config while every other tab shows this client's own copy — two different
  // motors on one screen.
  const geo = currentGeoJson();
  if (geo && !qs.has('geo')) qs.set('geo', geo);
  // Same for the material assignment: the interceptor adds `mat=` only under
  // /api/simulation/physics, yet the loss field this solve heats the machine
  // with is an EM solve — it must use the duty's magnet grade and steel, not
  // whatever the shared config last saved (the "solved with the other die's
  // steel" trap, 2026-09-03).  The router accepts it through the same
  // `_material_override_dep` the Electromagnetic routes use.
  const mat = currentMatJson();
  if (mat && !qs.has('mat')) qs.set('mat', mat);
  const r = await fetch(`${BASE}${path}?${qs.toString()}`, { cache: 'no-store' });
  if (!r.ok) {
    let msg = await r.text();
    let code: string | null = null;
    // The 422 carries {detail: {error, error_code, invalid_parameters}} — show
    // the sentence an engineer can act on, not the JSON around it, and keep the
    // tag beside it so a caller can recognise the one refusal it may answer.
    try {
      const j = JSON.parse(msg);
      const d = j?.detail;
      msg = typeof d === 'string' ? d : (d?.error ?? msg);
      if (d && typeof d === 'object' && typeof d.error_code === 'string') code = d.error_code;
    } catch { /* not JSON — keep the raw text */ }
    const err = new Error(msg.slice(0, 400)) as ApiRefusal;
    err.code = code;
    throw err;
  }
  return r.json() as Promise<T>;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * THE DUTY CYCLE (2026-09-14)
 *
 * A steady map answers "how hot does this machine get if it runs this point
 * forever".  A robot joint never does: it spends two seconds at its peak and a
 * minute at nothing, and the steady answer to the peak is a temperature the
 * machine never reaches.  The cycle is part of the DUTY's definition (it lives
 * in the yaml with it, `routes/family.DutySpec.duty_cycle`), and the solve fits
 * a four-node lumped network to ONE steady map and integrates the profile on it.
 * ═══════════════════════════════════════════════════════════════════════════ */

/** S1 continuous · S2 one pull · S3 ED % of a cycle · an explicit segment list.
 *  Mirrors `thermal_duty_cycle._KINDS`. */
export type DutyCycleKind = 'S1' | 'S2' | 'S3' | 'segments';

/** The four lumped nodes — `thermal_capacities.NODES`. */
export type DutyCycleNode = 'winding' | 'stator' | 'rotor' | 'magnet';
export const DUTY_CYCLE_NODES: readonly DutyCycleNode[] =
  ['winding', 'stator', 'rotor', 'magnet'];

export interface DutyCycleSegmentSpec {
  /** a duty of THIS configuration, or null — the machine standing unpowered */
  duty: string | null;
  t_s: number;
}

/** The `duty_cycle:` block as the duty stores it.  Everything past `kind` is
 *  optional, and which fields mean anything depends on the kind. */
export interface DutyCycleSpec {
  kind: DutyCycleKind;
  /** which duty RUNS (S1/S2/S3); absent = the calibration duty */
  duty?: string | null;
  /** S2: the length of the pull, s */
  t_on_s?: number | null;
  /** S3: the powered share of each cycle, % (0 < ED ≤ 100) — OPTIONAL since
   *  2026-09-15: a block that states none is a request to FIND the allowable
   *  one, and what comes back is the cycle at THAT ratio … */
  ed_pct?: number | null;
  /** … which `ed_given: false` is what says (the answer's spec only) */
  ed_given?: boolean;
  /** … and the cycle time, s */
  cycle_s?: number | null;
  /** S3: what the machine does while resting — null = unpowered */
  rest_duty?: string | null;
  segments?: DutyCycleSegmentSpec[];
  /** where the machine starts, °C — null = the ambient */
  t_start_c?: number | null;
  n_cycles_max?: number | null;
  /** which duty's steady map the network's conductances are fitted at —
   *  null = the configuration's rated duty */
  calibration_duty?: string | null;
  saved_at?: string | null;
}

/** The integrated cycle: the periodic state and its decimated series. */
export interface DutyCycleTrace {
  /** null for S2 — one pull is never repeated, so there is no periodic state */
  converged: boolean | null;
  n_cycles: number;
  residual_K: number | null;
  peak_c: Record<string, number>;
  min_c: Record<string, number>;
  mean_c: Record<string, number>;
  /** the winding MEAN plus the calibration map's own max − mean offset */
  winding_hot_peak_c: number;
  winding_hot_mean_c: number;
  hot_spot_offset_K: number;
  closure_pct: number;
  energy_in_J: number;
  energy_out_J: number;
  stored_J: number;
  /** [t_start, t_end, duty-or-null] per segment of one cycle */
  segments: [number, number, string | null][];
  /** the series, ≤ 400 samples each */
  t_s: number[];
  T_c: Record<string, number[]>;
  winding_hot_c: number[];
  P_W: Record<string, number[]>;
  n_samples: number;
  n_solved: number;
  start_state_c?: Record<string, number>;
  note?: string;
}

/** Where the heat went, time-averaged over one cycle. */
export interface DutyCycleSplit {
  basis?: string;
  generated_W?: Record<string, number>;
  generated_total_W?: number;
  stator_side_W?: number;
  rotor_side_W?: number;
  stator_pct?: number | null;
  rotor_pct?: number | null;
  housing_W?: number;
  mount_W?: number;
  winding_end_faces_W?: number;
  stator_end_faces_W?: number;
  rotor_end_faces_W?: number;
  magnet_end_faces_W?: number;
  bore_W?: number;
  shaft_ends_W?: number;
  gap_W?: number;
  winding_to_core_W?: number;
  closure_W?: number;
  closure_pct?: number | null;
  note?: string;
}

/** What the cycle is judged against — and what it allows. */
export interface DutyCycleLimits {
  winding_limit_c: number;
  winding_limit_note?: string;
  /** null = the magnet cards carry no maximum, so the magnets are NOT judged */
  magnet_limit_c?: number | null;
  magnet_limit_note?: string;
  limits_c?: Record<string, number>;
  /** how long an S2 pull lasts before something crosses its limit; null = the
   *  machine settles below every limit, i.e. this point is S1 */
  s2_time_to_limit_s?: number | null;
  s2_limiting_part?: string | null;
  s2_horizon_s?: number;
  s2_end_state_c?: Record<string, number>;
  s2_winding_hot_end_c?: number;
  s2_note?: string;
  /** the duty ratio the class allows, against the one that was asked for */
  ed_allowable_pct?: number | null;
  ed_requested_pct?: number | null;
  ed_limiting_part?: string | null;
  /** [ED %, peak hot-spot °C] on a coarse grid */
  ed_curve?: [number, number][];
  ed_note?: string;

  /* ── THE FOUND REGIME (2026-09-15) ─────────────────────────────────────────
     The tool does not grade a duty ratio somebody typed, it finds the one this
     machine holds.  `ed_found` says which of the two this answer is: true = the
     cycle below IS the one at `ed_allowable_pct`, because the request asked for
     no ratio.  `ed_requested_pct` is then null. */
  ed_found?: boolean;
  ed_found_note?: string;
  /** the cycle length `ed_allowable_pct` is the ratio OF, s */
  ed_cycle_s?: number | null;
  /** every node at the allowable point — the machine the answer describes */
  at_allowable?: {
    winding_hot_peak_c?: number | null;
    winding_hot_mean_c?: number | null;
    magnet_peak_c?: number | null;
    peak_c?: Record<string, number | null>;
    mean_c?: Record<string, number | null>;
  } | null;
  /** which part the found regime is limited by */
  limiting_part?: string | null;
  /** the allowable ED over a span of CYCLE LENGTHS — a ratio means nothing
   *  without the period it is a ratio of */
  ed_vs_cycle?: {
    cycle_s: number;
    ed_allowable_pct: number | null;
    t_on_s: number | null;
    limiting_part?: string | null;
    winding_hot_peak_c?: number | null;
    magnet_peak_c?: number | null;
    note?: string;
  }[];
  /** the SAME pull, from a machine that has already been working: from the
   *  calibration duty's steady map, and out of the settled cycle's mean state */
  s2_from_rated_s?: number | null;
  s2_from_rated_part?: string | null;
  s2_from_rated_start_c?: Record<string, number> | null;
  s2_from_rated_note?: string;
  s2_from_cycle_mean_s?: number | null;
  s2_from_cycle_mean_part?: string | null;
  s2_from_cycle_mean_start_c?: Record<string, number> | null;
  s2_from_cycle_mean_note?: string;
}

/** The whole answer — the record `duty_results` files under kind `duty_cycle`,
 *  plus this router's four provenance fields. */
export interface DutyCycleResult {
  kind?: string;
  computed_at?: string;
  die?: string;
  configuration?: string;
  duty?: string;
  spec: DutyCycleSpec & {
    cycle_s?: number; duration_s?: number; note?: string;
    calibration_source?: string;
    segments?: (DutyCycleSegmentSpec & {
      rpm?: number; coil_ref_c?: number; total_W?: number;
      losses_W?: Record<string, number>; note?: string;
    })[];
  };
  /** the fitted conductances, the capacities and the point they came from */
  network: {
    G_W_per_K?: Record<string, number>;
    areas_m2?: Record<string, number>;
    t_ambient_c?: number;
    t_mount_c?: number;
    emissivity?: number | null;
    hot_spot_offset_K?: number;
    C_J_per_K?: Record<string, number>;
    C_total_J_per_K?: number;
    cp_sources?: Record<string, string>;
    calibration_duty?: string;
    active_nodes?: string[];
    merged?: string[][];
    notes?: string[];
    [k: string]: unknown;
  };
  cycle: DutyCycleTrace;
  split: DutyCycleSplit;
  limits: DutyCycleLimits;
  point?: Record<string, unknown>;
  calibration_map?: Record<string, unknown>;
  elapsed_s?: number;
  solve_time_s?: number;
  cached?: boolean;
  geometry_fingerprint?: string | null;
}

/** The body `POST /api/thermal/duty_cycle` takes.  `die`/`config`/`duty` may be
 *  omitted — the route then uses the active family context — but this app always
 *  names them: the panel knows which duty the user is looking at, and a solve
 *  that silently picked another one would be the wrong answer, confidently. */
export interface DutyCycleRequest {
  die?: string;
  config?: string;
  duty?: string;
  /** the block to integrate; omitted = the one saved on that duty */
  duty_cycle?: DutyCycleSpec;
  /** the Thermal panel's OWN field names (coolMode, ambientT, emissivity,
   *  mountG, …) — mapped by `thermal_settings.cooling_fields`, exactly as
   *  POST /api/coupled/run maps them.  Omitted = the caller's remembered
   *  panel settings, which is not what the user is looking at. */
  thermal_settings?: Record<string, string>;
  /** per-request geometry / material overrides, as this client always sends */
  geo?: string | null;
  mat?: string | null;
  magnet_limit_c?: number | null;
  [k: string]: unknown;
}

export interface LastDutyCycle {
  has_result: boolean;
  live_geometry_fingerprint: string | null;
  duty_cycle: {
    result: DutyCycleResult;
    params: Record<string, unknown>;
    geometry_fingerprint: string | null;
    computed_at: string | null;
    stale_geometry: boolean | null;
  } | null;
}

/** POST, because the request carries a whole block and a settings dict — and
 *  because it is the only thermal call whose body is not a query string.  The
 *  422 is unwrapped exactly as `get` does it: the backend refuses a cycle it
 *  cannot answer BY NAME (`duty_cycle_missing`, `duty_cycle_mixed_speed`,
 *  `duty_cycle_no_periodic_state`, …) and the sentence beside the code is
 *  written for the engineer to read. */
async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST', cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let msg = await r.text();
    let code: string | null = null;
    try {
      const j = JSON.parse(msg);
      const d = j?.detail;
      msg = typeof d === 'string' ? d : (d?.error ?? msg);
      if (d && typeof d === 'object' && typeof d.error_code === 'string') code = d.error_code;
      // `_dc_refuse` puts the whole story — the refusal AND what to do about it
      // — in `invalid_parameters[].message`; `error` alone is only the first
      // half, and the remedy is the half an engineer can act on.
      const inv = d?.invalid_parameters;
      if (Array.isArray(inv) && typeof inv[0]?.message === 'string') msg = inv[0].message;
    } catch { /* not JSON — keep the raw text */ }
    const err = new Error(String(msg).slice(0, 600)) as ApiRefusal;
    err.code = code;
    throw err;
  }
  return r.json() as Promise<T>;
}

/** Integrate a duty's cycle.  Nothing about this is implicit: the machine, the
 *  duty, the block and the boundary conditions all ride the body. */
export const fetchDutyCycle = (p: DutyCycleRequest) =>
  post<DutyCycleResult>('/duty_cycle', {
    ...p,
    // Same reason the GET helper attaches them: without the active machine's
    // own geometry and material assignment the backend would solve the shared
    // config while every other tab shows this client's copy.
    geo: p.geo ?? currentGeoJson() ?? undefined,
    mat: p.mat ?? currentMatJson() ?? undefined,
  });

/** The last cycle this backend answered — a lookup, never a solve. */
export const fetchLastDutyCycle = () =>
  get<LastDutyCycle>('/duty_cycle/last', {});

export const fetchThermalField = (p: ThermalRequest) =>
  get<ThermalField>('/field', { ...p });

export const fetchCoupled = (p: ThermalRequest & { max_iter: number }) =>
  get<CoupledResult>('/coupled', { ...p });

/** What the tab was showing before — a lookup, never a solve. */
export const fetchLastThermal = () => get<LastThermal>('/last', { field: true });
/** The same answer without the heavy arrays — for another tab that only wants
 *  the numbers (the Mechanical tab reads the rotor / sleeve temperatures). */
export const fetchLastThermalLight = () => get<LastThermal>('/last', { field: false });

/** Build (or fetch) the solid sub-mesh alone: stator, coils, rotor, magnets,
 *  shaft and sleeve, with the air dropped.  This is the Build-mesh button, and
 *  the solve that follows reuses what it built (the backend memoises the mesh on
 *  the geometry), so pressing it costs the mesher's seconds ONCE. */
export const fetchThermalMesh = (p: ThermalMeshRequest) =>
  get<ThermalMeshPayload>('/mesh', { ...p });

/* ═══════════════════════════════════════════════════════════════════════════
 * Settings this tab owns
 *
 * Under `therm.*`, exactly as the Mechanical tab uses `mech.*`.  The operating
 * point is NOT among them: it always comes from the Electromagnetic tab.
 * ═══════════════════════════════════════════════════════════════════════════ */

export function readTherm<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`therm.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

export function writeTherm(key: string, v: unknown): void {
  try { localStorage.setItem(`therm.${key}`, JSON.stringify(v)); } catch { /* private mode */ }
}

/** Mesh-tab settings, read exactly the way every solving panel in this app
 *  reads them (`mesh.*`, JSON-encoded) — one source, or the request would key a
 *  different mesh than the one the Mesh tab is showing. */
export function readMeshSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`mesh.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

/**
 * The mesh half of every request, as the Mesh tab left it.
 *
 * Read HERE and nowhere else, so the field solve, the coupled solve and the
 * Build-mesh button all key the same mesh — a Build that did not produce the
 * mesh the Solve then asks for is gmsh seconds paid twice.  The keys and the
 * defaults are the ones the old thermal fetch inside `FemFieldChart` used, so a
 * machine meshed before this tab existed still hits the backend's memo.
 */
/** The last mesh block read from the SERVER — the source of truth the Mesh tab
 *  itself persists to (`/api/mesh/config`, i.e. the `mesh:` block of
 *  motor_config.yaml).  Filled by `fetchMeshParams`; `meshParams()` hands it
 *  out synchronously for display until the next refresh. */
let _serverMesh: ThermalMeshRequest | null = null;

function _localMeshParams(): ThermalMeshRequest {
  return {
    mesh_size_mm: Number(readMeshSetting('meshSize', 4.0)) || 4.0,
    min_size_mm: Number(readMeshSetting('minSize', 0.3)) || 0.3,
    outer_air_factor: Number(readMeshSetting('outerAir', 1.3)) || 1.3,
    n_sectors: Number(readMeshSetting('nSectors', 1)) || 1,
    component_mesh: JSON.stringify(
      readMeshSetting<Record<string, number>>('componentMesh', {})),
  };
}

/** Synchronous view of the mesh block: the server's copy once it has been
 *  read this session, the browser's `mesh.*` keys before that. */
export function meshParams(): ThermalMeshRequest {
  return _serverMesh ?? _localMeshParams();
}

/**
 * The mesh block every request must carry, read from the SERVER.
 *
 * WHY (2026-09-07, user: "похоже завис тепловой расчёт"): the first thermal
 * solve on the Ø200 took 683 s because the request said `n_sectors = 1` — the
 * browser's `mesh.nSectors` key was absent (the Mesh tab writes it only while
 * mounted, and a reload had wiped the session), so the default of 1 = the FULL
 * ring went out while the Mesh tab's own setting, persisted on the server, was
 * ½.  The Electromagnetic tab's mesh contract already made the server the source of
 * truth (meshSaveContract.ts); this reads the same block, so the thermal solve
 * meshes what the Mesh tab shows.  The per-part sizes (`component_mesh`) have
 * no server copy and stay the browser's.  Any failure to reach the server falls
 * back to the browser's keys — a stale sector count is still a solve; a thrown
 * error is no solve at all.
 */
export async function fetchMeshParams(): Promise<ThermalMeshRequest> {
  const local = _localMeshParams();
  try {
    const r = await fetch(`${API.replace(/\/$/, '')}/api/mesh/config`, { cache: 'no-store' });
    if (r.ok) {
      const j = await r.json() as Record<string, unknown>;
      const n = (k: string, d: number) => {
        const v = Number(j?.[k]);
        return Number.isFinite(v) && v > 0 ? v : d;
      };
      _serverMesh = {
        mesh_size_mm: n('mesh_size_mm', local.mesh_size_mm),
        min_size_mm: n('min_size_mm', local.min_size_mm),
        outer_air_factor: n('outer_air_factor', local.outer_air_factor),
        n_sectors: Math.max(1, Math.round(n('n_sectors', local.n_sectors))),
        component_mesh: local.component_mesh,
      };
      return _serverMesh;
    }
  } catch { /* fall through to the browser's copy */ }
  return _serverMesh ?? local;
}

/**
 * Write the coil temperature back into the Electromagnetic tab.
 *
 * The converged copper temperature is NEVER written automatically: it is a
 * result of this tab and an INPUT of that one, and silently moving another
 * panel's operating point is the state mutation this project has a standing
 * rule against.  It happens when the user presses the button, and it uses the
 * same key + event the Electromagnetic panel's own persisted fields use, so the
 * field on screen updates without a remount.
 */
export function writeSimCoilTemp(c: number): void {
  if (!Number.isFinite(c)) return;
  try {
    localStorage.setItem('sim.coilTemp', JSON.stringify(Math.round(c * 10) / 10));
    window.dispatchEvent(new Event('sim-settings-restored'));
  } catch { /* private mode — nothing to restore from anyway */ }
}

/** The Electromagnetic tab's operating point, as this tab must send it.  ONE place,
 *  so the field solve, the coupled solve and the context line quote the same
 *  numbers (standing rule: every physics value comes from Electromagnetic). */
export function simOperatingPoint(): {
  rpm: number; gamma_deg: number; I_phase_rms: number;
  coil_temp_c: number; n_steps_per_period: number;
} {
  return {
    rpm: Number(readSimSetting('rpm', 0)) || 0,
    gamma_deg: Number(readSimSetting('gamma', 0)) || 0,
    I_phase_rms: Math.max(0, Number(readSimSetting('current', 0)) || 0),
    coil_temp_c: Number(readSimSetting('coilTemp', 120)) || 120,
    n_steps_per_period: Number(readSimSetting('stepsPP', EDDY_DEFAULT_STEPS)) || EDDY_DEFAULT_STEPS,
  };
}

/* ═══════════════════════════════════════════════════════════════════════════
 * How long things take — the estimate the SolveTimer shows while the next one
 * runs.  Same contract as `mechanical/api`, its own namespace: a thermal solve
 * and a contact solve take wildly different times and must not teach each other.
 * ═══════════════════════════════════════════════════════════════════════════ */

export type ThermKind = 'field' | 'coupled' | 'mesh';

export const readLastSecs = (kind: ThermKind): number | null => {
  const v = readTherm<number | null>(`lastSecs.${kind}`, null);
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null;
};

export const writeLastSecs = (kind: ThermKind, s: number): void => {
  if (Number.isFinite(s) && s > 0) writeTherm(`lastSecs.${kind}`, Math.round(s * 10) / 10);
};

/* ═══════════════════════════════════════════════════════════════════════════
 * Cooling — the film coefficients, and the payload the Configure tab reads
 *
 * Moved here from `simulation/CoolingControls` on 2026-09-07 with the rest of
 * the thermal code.  The localStorage keys are UNCHANGED (`sim.cool.*`): they
 * are the Configure tab's saved cooling system, and renaming them would silently
 * reset every user's numbers to the defaults below.
 * ═══════════════════════════════════════════════════════════════════════════ */

const COOLANTS: Record<string, string> = {
  Water: 'water', 'Water-glycol': 'water_glycol_50', Oil: 'oil',
};

const lsGet = (k: string, d: string): string => {
  try { return localStorage.getItem('sim.cool.' + k) ?? d; } catch { return d; }
};
export const lsSetCool = (k: string, v: string): void => {
  try { localStorage.setItem('sim.cool.' + k, v); } catch { /* quota */ }
};

/** Convection coefficient for air blown across the housing, W/m²K.
 *  Churchill–Bernstein for a cylinder in cross-flow, with a natural-convection
 *  floor: still air is not zero cooling, it is about 7. */
export function airH(v: number, D: number): number {
  const nu = 1.56e-5, ka = 0.0263, Pr = 0.707;
  if (!(v > 0) || !(D > 0)) return 7;
  const Re = v * D / nu;
  const Nu = 0.3 + (0.62 * Math.sqrt(Re) * Math.cbrt(Pr)) / Math.pow(1 + Math.pow(0.4 / Pr, 2 / 3), 0.25)
    * Math.pow(1 + Math.pow(Re / 282000, 5 / 8), 4 / 5);
  return Math.max(Nu * ka / D, 7);
}

/** Jacket coefficient for a liquid loop, W/m²K — a Dittus-Boelter-shaped
 *  q^0.8 law anchored at 1800 W/m²K for 8 L/min, clamped to the range a real
 *  jacket can produce (600…8000). */
export const liqH = (q: number): number =>
  Math.min(8000, Math.max(600, 1800 * Math.pow(Math.max(q, 0.1) / 8, 0.8)));

/** Cooling spec for a thermal / coupled payload, read from localStorage. */
export function getCoolingPayload(): Record<string, number | string> {
  const sys = lsGet('mode', 'Air');
  const isAir = sys === 'Air';
  const t = parseFloat(lsGet('temp', '25')) || 0;
  return {
    cooling_mode: isAir ? 'air' : 'liquid',
    fluid: isAir ? 'water' : (COOLANTS[sys] || 'water'),
    air_speed_mps: parseFloat(lsGet('air', '8')) || 0,
    flow_lpm: parseFloat(lsGet('flow', '8')) || 0,
    ambient_temp: t, fluid_temp_in_c: t,
  };
}

export { COOLANTS, lsGet as readCool };
