/** Types and fetches for the Mechanical tab.
 *
 * Mirrors `routes/mechanical.py` one-for-one.  The fields that look like
 * decoration are the ones the panel is obliged to show: `lift_off`, `cached`,
 * `mesh.rigid_residual` and the p99.5 stresses all say how much the headline
 * number is worth, and a sleeve sized off a number whose provenance is hidden
 * is a sleeve sized off nothing.
 */
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${API.replace(/\/$/, '')}/api/mechanical`;

/** A load case's name.
 *
 *  `string`, not a union, since 2026-09-06: in single-speed mode the backend
 *  names the one case by its SPEED ("23,000 rpm") — user, "давай будем
 *  рассчитывать только на 23 000 оборотов ... проще будет считать только одну
 *  величину" — so the case names are data, and the panel reads them off the
 *  result instead of assuming the three below. */
export type CaseName = string;
/** The three-case table, when that is what was solved. */
export const CASES: CaseName[] = ['standstill', 'rated', 'overspeed'];

/** `single` = one case at the rpm asked for; `three` = standstill / rated /
 *  overspeed.  `three` is the API default so nothing that predates the
 *  parameter changes. */
export type CasesMode = 'single' | 'three';

/** Which forces act.
 *
 *  User 2026-09-07: "добавь ещё и момент на ротор, пусть действуют все силы;
 *  сделай меню, чтобы можно было выбрать центробежную, момент и обе."  `both` is
 *  the API default — the machine is loaded by both — and it falls back to
 *  `centrifugal` when there is no run to take the torque from. */
export type LoadsMode = 'centrifugal' | 'torque' | 'both';

/** The temperature the material library is quoted at, i.e. the state in which
 *  the geometry as drawn is stress-free.  Mirrors `rotor_stress.REF_TEMP_C`;
 *  both temperature fields default to it, so a panel nobody has touched solves
 *  exactly the machine it always did (2026-09-07). */
export const REF_TEMP_C = 20;

export const LOADS_LABEL: Record<LoadsMode, string> = {
  centrifugal: 'Centrifugal',
  torque: 'Torque',
  both: 'Both',
};

/** The cases THIS result actually has, in the order the backend solved them. */
export const caseKeys = (res: { cases: Record<string, unknown> }): CaseName[] =>
  Object.keys(res.cases ?? {});

/** `want` if this result has it, otherwise its first case.
 *
 *  The remembered view choice ("rated") outlives the result it was made on: a
 *  single-speed answer has no "rated" column, and indexing `cases` with it
 *  would blank the whole picture instead of showing the one case there is. */
export const pickCase = (res: { cases: Record<string, unknown> },
                         want: CaseName): CaseName => {
  const keys = caseKeys(res);
  return keys.includes(want) ? want : (keys[0] ?? want);
};

export interface PartResult {
  material: string;
  von_mises_max_mpa: number;
  von_mises_p995_mpa: number;
  principal_max_mpa: number;
  principal_max_p995_mpa: number;
  principal_min_mpa: number;
  hoop_max_mpa: number;
  radial_min_mpa: number;
  radial_max_mpa: number;
  /** The UNAVERAGED element peaks — the other half of the ANSYS averaging
   *  toggle, kept so a user can reproduce either number.  Optional: a result
   *  cached before 2026-09-10 has none. */
  von_mises_max_unaveraged_mpa?: number;
  principal_max_unaveraged_mpa?: number;
  hoop_max_unaveraged_mpa?: number;
  radial_min_unaveraged_mpa?: number;
  /** 'averaged' on every result solved since 2026-09-10. */
  stress_convention?: string;
  /** the stress the safety factor was divided into: strength / this === SF */
  governing_stress_mpa?: number | null;
  strength_mpa: number;
  strength_kind: 'yield' | 'tensile';
  safety_factor: number | null;
  mass_kg: number | null;
}

/** Fusion-360 terminology, and the user's default (2026-09-05): every
 *  interface is Separation unless it is a press fit. */
export type ContactType = 'separation' | 'bonded' | 'sliding';
export interface ContactSpec { type: ContactType; mu: number }

export const CONTACT_PAIRS = [
  'magnet_rotor', 'sleeve_rotor', 'sleeve_magnet', 'shaft_rotor',
] as const;
export type ContactPair = typeof CONTACT_PAIRS[number];

/** µ = 0.2 on the three separation pairs since 2026-09-07.
 *
 *  It used to be 0, which is the conservative choice for the question the tab
 *  was built for ("how thick must the band be to hold the magnets DOWN" — a
 *  radial question friction does not help with).  The torque load asks the
 *  opposite question: whether the poles can be held TANGENTIALLY once the
 *  bridges have yielded, and there µ = 0 is not conservative, it is degenerate —
 *  a frictionless separation joint transmits no shear at all, so the answer is
 *  "no" before the solver starts.  0.2 is the usual dry steel-on-composite /
 *  steel-on-magnet figure.  This is the PANEL's default only: the API still
 *  defaults to 0, and a µ the user has already set (including 0) wins. */
export const DEFAULT_CONTACTS: Record<ContactPair, ContactSpec> = {
  magnet_rotor: { type: 'separation', mu: 0.2 },
  sleeve_rotor: { type: 'separation', mu: 0.2 },
  sleeve_magnet: { type: 'separation', mu: 0.2 },
  // A press-fit / keyed hub is a bonded joint by construction; letting it
  // rattle inside the bore would be modelling a machine nobody built.
  shaft_rotor: { type: 'bonded', mu: 0 },
};

export const CONTACT_LABEL: Record<ContactPair, string> = {
  magnet_rotor: 'Magnet ↔ rotor iron',
  sleeve_rotor: 'Sleeve ↔ rotor iron',
  sleeve_magnet: 'Sleeve ↔ magnet',
  shaft_rotor: 'Shaft ↔ rotor iron',
};

/** What a seated part came to rest ON, in words (2026-09-09). The pair label is
 *  the honest identifier and rides in `contact.seated`; the panel's one-liner
 *  says what the surface IS — the same wording the solver's retention verdict
 *  uses, so the panel and the tooltip cannot drift apart. */
export const SEAT_SURFACE: Record<string, string> = {
  magnet_rotor: 'the pocket tab',
  sleeve_magnet: 'the sleeve',
  sleeve_rotor: 'the sleeve',
  shaft_rotor: 'the shaft',
};

/** Rotor part names as the panel writes them — bare nouns, so the caller can
 *  put "the" in front of one and a count in front of several. */
export const PART_LABEL: Record<string, string> = {
  rotor: 'rotor iron',
  magnet: 'magnet',
  sleeve: 'sleeve',
  shaft: 'shaft',
};

export interface InterfaceResult {
  n_facets: number;
  type: ContactType;
  mu: number;
  length_mm: number;
  /** most COMPRESSIVE normal traction — the clamp holding the part on */
  normal_min_mpa: number;
  /** most TENSILE normal traction — positive means the joint is opening */
  normal_max_mpa: number;
  /** 95th percentile of the traction, recovered from the element stresses */
  normal_p95_mpa: number;
  /** contact pressure: >= 0 for a separation pair, negative for a bonded tie
   *  that is holding TENSION */
  pressure_min_mpa: number;
  pressure_max_mpa: number;
  pressure_mean_mpa: number;
  /** fraction of the interface LENGTH that has opened */
  open_fraction: number;
  gap_max_um: number;
  radial_force_kn_per_m: number;
  lift_off: boolean;
  /* ── the TANGENTIAL half, 2026-09-07 (the torque load) ────────────────────
     open + slip + stick = 1. `stick` is the only one that transmits shear: an
     open facet and a sliding one both pass nothing the joint was asked for. */
  slip_fraction?: number;
  stick_fraction?: number;
  /** largest tangential relative motion on a CLOSED facet of this pair */
  slip_max_um?: number;
  tangential_force_kn_per_m?: number;
  shear_mean_mpa?: number;
  /** moment this interface actually carried, N·m over the whole stack */
  torque_transmitted_nm?: number;
  /** µ·∫p·r·dA — the most Coulomb could ever pass at this clamp */
  friction_capacity_nm?: number;
}

/** Can the torque get out of the poles?  One line, and the numbers behind it. */
export interface TorquePath {
  verdict: string;
  worst_pair: string | null;
  slip_fraction: number;
  stick_fraction: number;
  capacity_nm: number | null;
  applied_nm: number;
  /** capacity / applied of the weakest clamped joint; null = no verdict */
  margin: number | null;
  slip_max_um: number;
  /** null = NO verdict (the solve ran away or a part came loose) */
  held: boolean | null;
}

export interface RetentionResult {
  magnet_centrifugal_kn_per_m: number;
  carried_kn_per_m: Record<string, number>;
  share: Record<string, number | null>;
  verdict: string;
}

/** Minimum safety factor of one part, with the criterion it was divided by.
 *  `p05` is the same field with the singular corner elements removed — the
 *  number to quote — and `min` is the raw element minimum beside it. */
export interface PartSafety {
  /** THE safety factor: the part's strength over the governing AVERAGED stress
   *  — the stress printed beside it on every tile, table and map since
   *  2026-09-10.  Optional: a result cached before that date has none, and the
   *  readers fall back to `PartResult.safety_factor`. */
  averaged?: number;
  /** raw element minimum, and its 5th percentile — diagnostics now, not
   *  verdicts.  The gap between these and `averaged` is how singular the
   *  part's worst corner is. */
  min: number;
  p05: number;
  criterion: string;
}

export interface CaseResult {
  rpm: number;
  parts: Record<string, PartResult>;
  sf_min_per_part: Record<string, PartSafety>;
  sf_min: number | null;
  sf_min_part: string | null;
  sf_min_p05: number | null;
  sf_min_p05_part: string | null;
  interfaces: Record<string, InterfaceResult | null>;
  interface_open_frac: Record<string, number | null>;
  magnet_retention: RetentionResult;
  contact: {
    converged: boolean; iterations: number; max_penetration_um: number;
    n_frozen_pairs: number; n_bodies: number; unretained_parts: string[];
    /** Parts that came loose at temperature and were TRAVELLED onto the surface
     *  that retains them, instead of being pinned where they floated
     *  (2026-09-09, "магнит должен сесть на язычок, как в Fusion"). Empty on
     *  every solve where nothing floats — which is most of them. `travel_um` is
     *  the number to compare with Fusion's, `landed_on` the contact pair the
     *  part came to rest on. Optional: an older cached result has no key. */
    seated?: {
      part: string; component_id: number; travel_um: number;
      direction: number[]; landed_on: string; n_pairs_closed: number;
    }[];
  };
  rotor_od_growth_um: number;
  /** The same outer surface, spelled out: the band's outside when the rotor has
   *  one, the iron's when it has not.  `max_um` is what eats the MECHANICAL
   *  clearance (air gap minus the band) and so decides whether the rotor rubs;
   *  `mean_um` is the ring growing as a whole, and the gap between the two is
   *  the lobing the magnets push into the band between the poles.  Optional: a
   *  result cached before 2026-09-10 has no key. */
  od_growth?: {
    max_um: number; mean_um: number; min_um: number;
    r_mm: number; part: string; n_nodes: number;
  } | null;
  /** What is left of the air gap once the rotor has grown into it. The
   *  clearance is measured off the DRAWN section (smallest stator radius
   *  against the rotor's largest), so a chamfer or a stepped pole counts. It
   *  knows nothing of manufacturing tolerance, bearing clearance or whirl.
   *  Optional: absent on a result cached before 2026-09-10, and null on a
   *  section whose stator was not handed to the solve. */
  air_gap?: {
    clearance_um: number; closed_um: number; remaining_um: number;
    closed_pct: number; bore_r_mm: number; rotor_r_mm: number;
  } | null;
  max_displacement_um: number;
  /** torque integrated at the SHAFT BORE, which is held tangentially. It must
   *  equal the applied torque — that identity is the check that the traction,
   *  the contact and the reaction all agree. `null` with no torque applied. */
  torque_reaction_nm?: number | null;
  /** reaction / applied. Anything but 1 means the solve ran away. */
  torque_balance?: number | null;
  torque_path?: TorquePath | null;
}

export interface MechMaterial {
  material: string;
  density: number;
  youngs_modulus_gpa: number;
  youngs_modulus_transverse_gpa: number | null;
  shear_modulus_gpa: number | null;
  poisson_ratio: number;
  strength_mpa: number;
  strength_kind: string;
  compressive_strength_mpa: number | null;
  tensile_strength_transverse_mpa: number | null;
  /* ── thermal expansion, ppm/K (2026-09-07) ────────────────────────────────
     Axis 1 is the same axis E1 is quoted in: the fibre = hoop direction of a
     wound sleeve, the MAGNETISATION direction of a magnet.  `null` means the
     card carries none — the part then does not expand, and `thermal_notes`
     says so rather than the solve refusing. */
  cte_ppm_k_1?: number | null;
  cte_ppm_k_2?: number | null;
  cte_anisotropic?: boolean;
  /** the sentence the safety-factor map divides by, e.g. "yield 350 MPa /
   *  von Mises" — built on the backend so the map never re-implements it */
  sf_criterion: string;
  orthotropic: boolean;
  note: string;
}

export interface StressField {
  vm_per_tri: number[];
  s_hoop_per_tri: number[];
  s_rad_per_tri: number[];
  s_p1_per_tri: number[];
  /** per-element safety factor, already divided by the right strength for the
   *  part each element belongs to; clamped to 1000 where nothing is stressed */
  sf_per_tri: number[];
  /** micrometres, [ux, uy] per vertex */
  u_per_node: [number, number][];
  u_mag_per_node: number[];
  /** MPa per contact facet, per pair — positive is compression */
  contact_pressure_per_pair: Record<string, number[]>;
  /** 0 = closed, 1 = open, per contact facet (a facet can be part-open
   *  because its three node pairs vote separately) */
  contact_open_per_pair: Record<string, number[]>;
}

export interface FieldPayload {
  vertices: [number, number][];
  triangles: [number, number, number][];
  domain_per_tri: number[];
  part_names: Record<string, string>;
  outlines: [number, number][][];
  extent: number;
  n_sectors: number;
  symmetry_mult: number;
  /** [x0, y0, x1, y1] in mm, one per contact facet, per pair */
  contact_segments_per_pair: Record<string, [number, number, number, number][]>;
  cases: Record<CaseName, StressField>;
}

/** The rotor's structural limit — the speed at which the minimum averaged
 *  safety factor reaches `target_sf` (1.0 = SF 1), everything else held as in
 *  the case this rode in on: same torque, contacts, interference,
 *  temperatures, mesh, order.  Owner 2026-09-21: "нужно искать ещё
 *  максимальную скорость вращения ... она будет, когда достигает SF = 1".
 *
 *  `rpm_sf1` is null when the search did not bracket a crossing within
 *  `max_factor` of the analysed speed (`reached: false`) — a real answer
 *  ("not within the range searched"), not a failed request.
 *  `omega2_extrapolation_rpm` is a pure ω² cross-check only (SF ∝ 1/rpm² if
 *  every load were centrifugal) — the real answer is `rpm_sf1`, which is
 *  interpolated between two actual solves, never extrapolated. */
export interface LimitSpeedResult {
  rpm_sf1: number | null;
  reached: boolean;
  bracket: [number, number] | null;
  limiting_part: string | null;
  sf_at_rpm0: number;
  n_solves: number;
  log: [number, number, string | null][];
  omega2_extrapolation_rpm: number;
  note: string;
  target_sf: number;
  loads: LoadsMode;
  torque_nm: number;
  interference_mm: number;
  rotor_temp_c: number;
  sleeve_temp_c: number;
  geo_fingerprint: string | null;
  /** the rpm this search started from — the case `sf_at_rpm0` describes */
  analysed_rpm: number;
}

export interface RotorStress {
  rpm: number;
  /** which case table this is — `single` means `cases` has one entry, named by
   *  its speed, and `overspeed_rpm` is that same speed (nothing above it was
   *  solved, so nothing above it is quoted).  Absent on results solved before
   *  2026-09-06, which are always three-case. */
  case_mode?: CasesMode;
  /** the case the report is ABOUT: `rated`, or the single speed */
  primary_case?: CaseName;
  overspeed_factor: number;
  overspeed_rpm: number;
  interference_mm: number;
  /** the fit the sleeve ACTUALLY feels once the rotor is hot: the geometric
   *  oversize plus the CTE mismatch under the band.  Equal to
   *  `interference_mm` whenever nothing is heated (2026-09-07). */
  interference_effective_mm?: number;
  has_sleeve: boolean;
  stack_length_mm: number;
  /** which rotor was solved (2026-09-09): the whole 360° (`n_sectors` 1) or
   *  one periodic sector replicated `n_sectors` times for the map */
  symmetry?: {
    mode: 'full' | 'sector'; n_sectors: number; angle_deg: number;
    cut_angle_deg?: number; note?: string; notes?: string[];
  };
  /* ── which forces acted (2026-09-07) ──────────────────────────────────────
     `loads` is what was SOLVED, `loads_requested` what was asked for: `both` on
     a machine that has never been run downgrades to `centrifugal`, and the
     panel says so instead of showing an empty torque column. */
  loads?: LoadsMode;
  loads_requested?: LoadsMode;
  /** 0 when no torque acted */
  torque_nm?: number;
  /** 'given' | 'last run' | 'last run (another geometry)' | 'none' */
  torque_source?: string;
  /** how the torque was turned into a traction — null with no torque */
  torque_load?: {
    torque_nm: number;
    r_gap_mm: number;
    /** the traction actually applied, spread over the surface that is there */
    traction_mpa: number;
    /** T / (2πr²L), i.e. what it would be on a full circle at r_gap */
    traction_nominal_mpa: number;
    surface_mm: number;
    coverage: number | null;
    n_facets: number;
    direction: string;
    reaction_at: string;
    reaction_rows: number;
  } | null;
  mesh: {
    n_nodes: number; n_triangles: number; element_order: number;
    mesh_size_mm: number; rigid_residual: number;
    n_contact_pairs: number; n_contact_facets: number;
    /** seconds this mesh cost to BUILD — kept when the mesh is reused */
    mesh_s?: number;
    /** true when the mesh came from the mesher's memo, i.e. this solve did not
     *  pay those seconds (a Build mesh press had already paid them) */
    mesh_reused?: boolean;
  };
  /* ── the rotor temperature (2026-09-07) ───────────────────────────────────
     User: "нужно универсально добавить температуру ротора, чтобы можно было
     задавать; для моторов без бандажа этот эффект вообще минимальный".
     `active` is false for a 20/20 °C request — the machine as drawn. */
  thermal?: {
    /** the FALLBACK for the core, the magnets and the shaft — see
     *  `part_temps_c` for what each of them was actually solved at */
    rotor_temp_c: number;
    sleeve_temp_c: number;
    ref_temp_c: number;
    /* ── one temperature per solid (2026-09-08) ─────────────────────────────
       User: "в механический расчёт тоже нужно делать каплинг, чтобы температуры
       везде были одинаковы".  `part_temps_c` is ALWAYS all four, whether they
       were named or inherited — the line the panel and the Compare row print —
       and `part_temps_given` is only what the caller named, so "coupled to
       Thermal" and "two fields typed by hand" stay distinguishable. */
    part_temps_c?: { rotor_core: number; magnet: number; shaft: number;
                     sleeve: number };
    part_temps_given?: Partial<Record<'rotor_core' | 'magnet' | 'shaft' | 'sleeve',
                                      number>>;
    /** the temperatures CHANGED the answer.  Under `band_fit` (the only model
     *  the routes use, user 2026-09-09: "температура только как изменение
     *  давления на бандаж, если он есть") that means "there is a band and its
     *  fit moved"; a sleeveless rotor at 150 °C is `active: false`. */
    active: boolean;
    /** `band_fit` in production; `free_expansion` is the solver's own
     *  verification model and never comes back from a route */
    model?: 'band_fit' | 'free_expansion';
    /** one sentence: what the temperatures were applied as, or why nothing */
    applied_as?: string;
    parts: Record<string, {
      temp_c: number; delta_t_c: number;
      /** 'part' = named for this solid; otherwise the scalar it inherited */
      temp_source?: 'part' | 'rotor_temp_c' | 'sleeve_temp_c';
      cte_ppm_k_1: number; cte_ppm_k_2: number;
      cte_source: string; anisotropic: boolean;
      /** free strain of this part at its temperature, ppm (axis 1) */
      thermal_strain_ppm: number;
      thermal_strain_ppm_1: number;
      thermal_strain_ppm_2: number;
      axis_1: string;
    }>;
    /** how the fit moved — null with no sleeve or no temperature */
    fit: {
      sleeve_bore_radius_mm: number;
      free_growth_under_sleeve_um: number;
      free_growth_sleeve_bore_um: number;
      delta_interference_mm: number;
      n_bore_nodes_rotor: number;
      n_bore_nodes_sleeve: number;
    } | null;
    notes: string[];
  };
  /** a material with no CTE, a sleeve temperature on a machine with no sleeve,
   *  a fit that has opened — one line each, never a wall of text */
  thermal_notes?: string[];
  /** The route solved one joint BONDED because the requested separation
   *  contact left its part unretained (2026-09-09: glued magnets in a pocket
   *  with a gap above them — the G2, the 40 mm).  Said, never silent. */
  contact_fallback?: {
    pair: string; from: string; to: string;
    open_fraction?: number | null; reason: string; refusal?: string;
    /** every joint bonded in turn, first to last (`pair` is the first) */
    pairs?: string[];
  };
  contacts: Record<string, ContactSpec & { n_facets: number }>;
  materials: Record<string, MechMaterial>;
  cases: Record<CaseName, CaseResult>;
  lift_off_rpm: Record<string, number | null>;
  field?: FieldPayload;
  solve_time_s?: number;
  /** seconds the BACKEND spent on this answer, measured around the geometry
   *  build and the solve — the number the panel prints as "solved in 48 s".
   *  A cached hit carries the ORIGINAL solve's value, not its own microseconds
   *  (user 2026-09-06: "нужно добавить ещё индикатор времени расчёта"). */
  elapsed_s?: number;
  cached?: boolean;
  geo_fingerprint?: string | null;
  /** present only on a result the **Limit speed** button produced — see
   *  `LimitSpeedResult`. Absent on a plain Solve. */
  limit_speed?: LimitSpeedResult;
}

export interface MechMaterialsReport {
  has_sleeve: boolean;
  parts: Record<string, Partial<MechMaterial> & {
    assignment_key: string; material: string | null;
    error?: string; missing_key?: string;
  }>;
}

async function get<T>(path: string,
                      params: Record<string, string | number | boolean | null | undefined>): Promise<T> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined) qs.set(k, String(v));
  });
  const r = await fetch(`${BASE}${path}?${qs.toString()}`, { cache: 'no-store' });
  if (!r.ok) {
    let msg = await r.text();
    // The 422 carries {detail: {error, invalid_parameters}} — show the sentence
    // an engineer can act on, not the JSON around it.
    try {
      const j = JSON.parse(msg);
      const d = j?.detail;
      msg = typeof d === 'string' ? d : (d?.error ?? msg);
    } catch { /* not JSON — keep the raw text */ }
    throw new Error(msg.slice(0, 400));
  }
  return r.json() as Promise<T>;
}

/** `full` = the whole 360° rotor; `sector` = ONE periodic pole sector with
 *  cyclic-symmetry ties on its two cut faces (user 2026-09-09: "используй
 *  периодичность, как я во Fusion") — every pole identical by construction,
 *  ~40× faster on the G2, the field replicated for the map. */
export type SymmetryMode = 'full' | 'sector';

export const fetchRotorStress = (p: {
  rpm: number; overspeed_factor: number; interference_mm: number;
  mesh_size_mm: number; order: number;
  /** omitted = `full`, the answer this app has always asked for */
  symmetry?: SymmetryMode;
  /** `single` solves ONE case at `rpm` — three times fewer nonlinear solves */
  cases?: CasesMode;
  /** which forces act; omitted = the API's `both` */
  loads?: LoadsMode;
  /** electromagnetic torque, N·m. OMIT to let the backend take the mean torque
   *  of the last Electromagnetic run — the operating point comes from there. */
  torque_nm?: number;
  /** rotor core / magnet / shaft temperature, °C. 20 = no thermal load.
   *  Since 2026-09-08 this is the FALLBACK: the three fields below override it
   *  per part when the Thermal tab has an answer for them. */
  rotor_temp_c?: number;
  /** sleeve temperature, °C — separate, because on a real machine it is not
   *  the rotor's number and the DIFFERENCE is what changes the fit. */
  sleeve_temp_c?: number;
  /* ── one temperature per solid (2026-09-08) ───────────────────────────────
     User: "в механический расчёт тоже нужно делать каплинг, чтобы температуры
     везде были одинаковы".  OMITTED unless the Thermal result carries that
     part: an absent field keeps `rotor_temp_c`, and a request that sends none
     of them is byte for byte the request this app has always sent (they enter
     the backend's cache key only when they are given). */
  magnet_temp_c?: number;
  rotor_core_temp_c?: number;
  shaft_temp_c?: number;
  contacts?: Record<string, ContactSpec>;
}) => {
  const { contacts, ...rest } = p;
  return get<RotorStress>('/rotor_stress', {
    ...rest, field: true,
    // The contact settings ARE the model — they ride in the query string and
    // are part of the server's cache key, so a Solve after changing one is a
    // fresh solve, not a cache hit.
    contacts: contacts ? JSON.stringify(contacts) : undefined,
  });
};

/** Same contract as `get`, POST instead — everything still rides the query
 *  string (there is no body), matching the route's own signature. */
async function post<T>(path: string,
                       params: Record<string, string | number | boolean | null | undefined>): Promise<T> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined) qs.set(k, String(v));
  });
  const r = await fetch(`${BASE}${path}?${qs.toString()}`, { method: 'POST', cache: 'no-store' });
  if (!r.ok) {
    let msg = await r.text();
    try {
      const j = JSON.parse(msg);
      const d = j?.detail;
      msg = typeof d === 'string' ? d : (d?.error ?? msg);
    } catch { /* not JSON — keep the raw text */ }
    throw new Error(msg.slice(0, 400));
  }
  return r.json() as Promise<T>;
}

/** The **Limit speed** button: the same case `fetchRotorStress` would solve
 *  (rpm, loads, torque, contacts, interference, temperatures, mesh, order —
 *  `cases` and `overspeed_factor` are not sent, because the search is
 *  inherently about ONE speed at a time), plus `target_sf` and the search's
 *  own knobs.  Returns a genuine single-speed `RotorStress`, with the search
 *  riding on it as `.limit_speed` — the backend's `/rotor_stress` shape is
 *  unchanged, so every existing reader of the result (the tiles, the maps,
 *  the compare row) keeps working on this answer exactly as it does on a
 *  plain Solve's. */
export const fetchLimitSpeed = (p: {
  rpm: number; interference_mm: number; mesh_size_mm: number; order: number;
  symmetry?: SymmetryMode;
  loads?: LoadsMode;
  torque_nm?: number;
  rotor_temp_c?: number;
  sleeve_temp_c?: number;
  magnet_temp_c?: number;
  rotor_core_temp_c?: number;
  shaft_temp_c?: number;
  contacts?: Record<string, ContactSpec>;
  /** 1.0 = the rotor's structural limit; omitted = the API's own 1.0 */
  target_sf?: number;
}) => {
  const { contacts, ...rest } = p;
  return post<RotorStress>('/limit_speed', {
    ...rest,
    contacts: contacts ? JSON.stringify(contacts) : undefined,
  });
};

export const fetchMechMaterials = () => get<MechMaterialsReport>('/materials', {});

/* ═══════════════════════════════════════════════════════════════════════════
 * The last result, and the bare cross-section
 *
 * User 2026-09-06: "когда я захожу и выхожу в Mechanical, графики пропадают.
 * Нужно, чтобы по умолчанию: если нет расчётов — рисуется просто геометрия;
 * если есть — подгружается последний расчёт; если были изменения текущей
 * геометрии — нужно подсвечивать неактуальность текущего расчёта."
 *
 * Neither call solves anything: `/last` is a lookup of what the tab was showing
 * before (it survives a backend restart — the server persists it beside the
 * config, the same way the Electromagnetic tab's last transient is persisted) and
 * `/mesh` is the mesher alone.  Solve stays the only way to compute.
 * ═══════════════════════════════════════════════════════════════════════════ */

/** One remembered answer, with the machine it was solved for. */
export interface LastEntry<T> {
  result: T;
  /** the query that produced it — so re-entering the tab restores the inputs */
  params: Record<string, unknown>;
  geometry_fingerprint: string | null;
  computed_at: string | null;
  /** the BACKEND's own verdict: it compares the stored fingerprint against the
   *  live one. `null` = UNKNOWN (a fingerprint is missing), never "fine" — a
   *  staleness check that cannot prove a mismatch must not claim one. */
  stale_geometry: boolean | null;
}

export interface LastMechanical {
  has_result: boolean;
  live_geometry_fingerprint: string | null;
  rotor_stress: LastEntry<RotorStress> | null;
  modes: LastEntry<ModalResult> | null;
  critical_speeds: LastEntry<RotordynamicsResult> | null;
}

/** The rotor cross-section as triangles — no fields, no solve. */
export interface MechMeshPayload {
  vertices: [number, number][];
  triangles: [number, number, number][];
  domain_per_tri: number[];
  part_names: Record<string, string>;
  outlines: [number, number][][];
  extent: number;
  n_nodes: number;
  /** the same count as `n_nodes`, under the name the mesh line prints */
  n_vertices?: number;
  n_triangles: number;
  mesh_size_mm: number;
  /** the order the SOLVE will run on these triangles (P2 by default) */
  element_order?: number;
  /** seconds of gmsh this mesh cost to build */
  mesh_s?: number;
  /** true when the mesher never ran because this mesh already existed */
  mesh_reused?: boolean;
  /** the edge extremes gmsh actually produced — what the size setting bought */
  min_edge_mm?: number;
  max_edge_mm?: number;
  geo_fingerprint: string | null;
  cached?: boolean;
  elapsed_s?: number;
}

export const fetchLastMechanical = () =>
  get<LastMechanical>('/last', { field: true });

/** Build (or fetch) the rotor mesh alone.
 *
 * User 2026-09-06: "по поводу сетки — как я понял, она строится отдельно, и ей
 * тоже нужно как-то управлять".  This is the Build mesh button.  It never
 * solves, and the solve that follows reuses what it built (the backend memoises
 * the mesh on the geometry itself), so pressing it costs the gmsh seconds ONCE.
 */
export const fetchMechMesh = (mesh_size_mm: number, order = 2) =>
  get<MechMeshPayload>('/mesh', { mesh_size_mm, order });

/** Settings the Electromagnetic panel persists — the operating point is read from
 *  there, never from a default in this file (standing project rule). */
export function readSimSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`sim.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

/** Mean torque of the last transient the Electromagnetic tab ran, |T| in N·m.
 *
 *  Same rule as `readSimSetting`: the torque is a RESULT of that tab, never a
 *  number invented here.  `null` when nothing has been run — the field is then
 *  left empty and the backend resolves the default itself (it keeps its own
 *  copy of the last run, fingerprinted against the loaded machine). */
export function readSimTorqueNm(): number | null {
  try {
    const raw = localStorage.getItem('sim.lastTransient');
    if (!raw) return null;
    const v = (JSON.parse(raw) as { T_avg_Nm?: number })?.T_avg_Nm;
    return typeof v === 'number' && Number.isFinite(v) && v !== 0
      ? Math.abs(v) : null;
  } catch { return null; }
}

/** The COUPLED operating point (2026-09-09).
 *
 *  With the Electromagnetic tab's "Coupled thermal" switch ON, the loop solves
 *  the rotor stress at the point of the run it has just made — that tab's rpm
 *  and the torque the run produced — and never at this tab's boxes, which hold
 *  whatever machine they were typed for (the Ø200's 23 000 rpm and 245.5 N·m
 *  sat on the 13 000 rpm, 0.6 N·m 40 mm this morning; user: "момент должен
 *  быть правильным, и электромагнитного, и обороты, и температуры").  A manual
 *  Solve on this tab follows the same rule while the switch is on, so the two
 *  cannot disagree.  `null` fields: not coupled, or nothing run yet — the boxes
 *  apply as before. */
export function coupledPoint(): { on: boolean; rpm: number | null; torque: number | null } {
  const on = readSimSetting<unknown>('coupled', false) === true;
  if (!on) return { on: false, rpm: null, torque: null };
  const rpm = Number(readSimSetting('rpm', 0));
  return { on: true, rpm: Number.isFinite(rpm) && rpm > 0 ? rpm : null,
           torque: readShownTorqueNm() ?? readSimTorqueNm() };
}

/** The torque the Electromagnetic tab SHOWS, |T| in N·m — its summary card
 *  as displayed, i.e. with the machine's 3-D end-effect factor applied when
 *  that toggle is on (`sim.lastSummary` is written by the card itself).  The
 *  user reads 0.59 N·m there and expects the rotor solved at 0.59, not at the
 *  2-D 0.623 the raw run carries (2026-09-09).  `null` when nothing is shown. */
export function readShownTorqueNm(): number | null {
  try {
    const raw = localStorage.getItem('sim.lastSummary');
    if (!raw) return null;
    const v = (JSON.parse(raw) as { T_em_avg_Nm?: number })?.T_em_avg_Nm;
    return typeof v === 'number' && Number.isFinite(v) && v !== 0
      ? Math.abs(v) : null;
  } catch { return null; }
}

export const fmt = (v: number | null | undefined, d = 1): string =>
  (v === null || v === undefined || !Number.isFinite(v)) ? '—' : v.toFixed(d);

/** Colour for a safety factor: this is the whole point of the tab. */
export function sfAccent(sf: number | null | undefined): string {
  if (sf === null || sf === undefined || !Number.isFinite(sf)) return 'var(--text-0)';
  if (sf < 1.0) return '#f87171';
  if (sf < 1.5) return '#fbbf24';
  return '#4ade80';
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Modal — 2-D in-plane modes, and the shaft's critical speeds
 *
 * Added 2026-09-05 for "нам нужно сделать ещё модальный анализ, чтобы понять
 * все частоты — это очень важно для 20000 rpm".  Two models, two endpoints, and
 * they answer different questions: /modes is the ring frequencies of the iron
 * (per unit length, no axial half-waves), /critical_speeds is the bending of
 * the shaft LINE, which is the one that decides whether 20 000 rpm is allowed.
 * ═══════════════════════════════════════════════════════════════════════════ */

export type ModalBody = 'rotor' | 'stator';
export type ModalSupport = 'free' | 'pinned';

export interface Excitation {
  name: string;
  hz: number;
  /** cycles per MECHANICAL revolution — the slope of this line on a Campbell
   *  plot. `null` for the PWM carrier, which does not scale with speed. */
  order: number | null;
  note: string;
}

export interface NearestExcitation {
  name: string | null;
  hz: number | null;
  /** signed, relative to the EXCITATION — the number a separation requirement
   *  is written against ("keep every mode 10 % clear of 2·f_e") */
  margin_pct: number | null;
  flag: boolean;
}

export interface ModeRow {
  index: number;
  f_hz: number;
  /** circumferential order: 0 breathing, 2 the ovalisation every ring machine
   *  hears first. `null` when the mode has no radial component at all. */
  order: number | null;
  nearest?: NearestExcitation;
}

export interface ModeField {
  vertices: [number, number][];
  triangles: [number, number, number][];
  domain_per_tri: number[];
  part_names: Record<string, string>;
  outlines: [number, number][][];
  extent: number;
  /** per mode, per vertex [ux, uy], normalised so the peak component is 1 —
   *  a mode shape has no units, only a shape */
  modes: [number, number][][];
}

export interface ModalResult {
  body: ModalBody;
  support: ModalSupport;
  n_modes: number;
  rpm: number;
  modes: ModeRow[];
  excitations: Excitation[];
  mesh: {
    n_nodes: number; n_triangles: number; n_dof: number;
    element_order: number; mesh_size_mm: number;
    /** seconds this mesh cost to build, out of `elapsed_s` */
    mesh_s?: number;
    n_rigid_modes_dropped: number; n_rigid_modes_expected: number;
    n_pinned_locations: number; boundary_nodes_counted: number;
    order_counted_on: string;
  };
  winding_mass_kg_per_m: number;
  materials: Record<string, {
    material: string; density: number; youngs_modulus_gpa: number;
    poisson_ratio: number; orthotropic: boolean; note: string;
  }>;
  assumptions: string;
  field?: ModeField;
  solve_time_s?: number;
  /** backend-measured seconds — see `RotorStress.elapsed_s` */
  elapsed_s?: number;
  cached?: boolean;
}

export interface CriticalSpeed {
  rpm: number;
  hz: number;
  whirl: 'forward' | 'backward';
  mode: number;
  /** unbalance is a synchronous FORWARD force, so on an isotropic rotor it
   *  finds the forward crossings and not the backward ones */
  excited_by_unbalance: boolean;
  margin_vs_rated_pct: number | null;
  beyond_plot: boolean;
}

export interface RotordynamicsResult {
  rated_rpm: number;
  overspeed_rpm: number;
  rpm_plot_max: number;
  rpm_hunt_max: number;
  natural_hz_at_rest: number[];
  critical_speeds: CriticalSpeed[];
  verdict: string;
  campbell: { rpm: number[]; backward: number[][]; forward: number[][] };
  excitations: Excitation[];
  layout: {
    total_length_mm: number; bearing_a_mm: number; bearing_b_mm: number;
    stack_from_mm: number; stack_to_mm: number;
    n_nodes: number; n_elements: number;
  };
  shaft: {
    material: string; od_mm: number; id_mm: number;
    youngs_modulus_gpa: number; poisson_ratio: number; density: number;
    mass_kg_per_m: number; EI_n_m2: number; kGA_n: number;
  };
  stack: {
    length_mm: number; added_mass_kg_per_m: number; added_mass_kg: number;
    polar_inertia_kg_m2: number; diametral_inertia_kg_m2: number;
    shaft_tube_mass_kg_per_m: number; lamination_EI_n_m2: number;
    stiffness_fraction_used: number;
    part_mass_kg_per_m: Record<string, number>;
  };
  inputs: Record<string, number | string[]> & { assumed: string[] };
  assumptions: string;
  solve_time_s?: number;
  /** backend-measured seconds — see `RotorStress.elapsed_s` */
  elapsed_s?: number;
  cached?: boolean;
}

/** The shaft-line dimensions the motor config does NOT carry. Every one of
 *  them is an assumption, and the backend echoes them back under
 *  `inputs.assumed` so a critical speed is never quoted without them. */
export interface BeamInputs {
  bearing_span_mm: number;
  stack_offset_mm: number;
  overhang_a_mm: number;
  overhang_b_mm: number;
  /** 0 = twice the geometry's rotor_inner_radius */
  shaft_od_mm: number;
  /** -1 = twice the geometry's shaft_inner_radius */
  shaft_id_mm: number;
  bearing_k_n_per_m: number;
  stack_stiffness_fraction: number;
}

export const DEFAULT_BEAM: BeamInputs = {
  bearing_span_mm: 250,
  stack_offset_mm: 0,
  overhang_a_mm: 30,
  overhang_b_mm: 30,
  shaft_od_mm: 0,
  shaft_id_mm: -1,
  // a mid-size preloaded angular-contact pair; a real bearing is 1e8…1e9 and
  // it is speed and load dependent
  bearing_k_n_per_m: 2e8,
  // laminations are not bonded axially, so the conservative first pass gives
  // the stack its mass and none of its bending stiffness
  stack_stiffness_fraction: 0,
};

export const fetchModes = (p: {
  body: ModalBody; n: number; support: ModalSupport;
  mesh_size_mm: number; order: number; winding_mass: boolean; rpm: number;
}) => get<ModalResult>('/modes', { ...p, shapes: true });

export const fetchCriticalSpeeds = (
  p: BeamInputs & { rpm: number; n_modes: number; mesh_size_mm: number },
) => get<RotordynamicsResult>('/critical_speeds', { ...p });

/** Panel settings persisted under `mech.*`. The operating point is NOT one of
 *  them — that always comes from the Electromagnetic tab. */
export function readMech<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`mech.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

export function writeMech(key: string, v: unknown): void {
  try { localStorage.setItem(`mech.${key}`, JSON.stringify(v)); } catch { /* private mode */ }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * How long things take
 *
 * User 2026-09-06: "нужно добавить ещё индикатор времени расчёта".  Two numbers,
 * and they are not the same number:
 *
 *   • the ESTIMATE shown while a solve is running — the last measured duration
 *     of that kind of solve on this machine, remembered in localStorage so it
 *     survives a reload and is there BEFORE the first solve of the session;
 *   • the MEASURED duration of the answer on screen, which comes from the
 *     backend (`elapsed_s`) because a client stopwatch also times the network
 *     and the JSON of a 40 MB field payload.
 * ═══════════════════════════════════════════════════════════════════════════ */

export type MechKind = 'stress' | 'modal' | 'rotordyn' | 'mesh';

export const readLastSecs = (kind: MechKind): number | null => {
  const v = readMech<number | null>(`lastSecs.${kind}`, null);
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null;
};

export const writeLastSecs = (kind: MechKind, s: number): void => {
  if (Number.isFinite(s) && s > 0) writeMech(`lastSecs.${kind}`, Math.round(s * 10) / 10);
};

/** A duration a human reads at a glance: "0.8 s", "48 s", "2 m 05 s". */
export function fmtSecs(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s) || s < 0) return '—';
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m} m ${String(Math.round(s - m * 60)).padStart(2, '0')} s`;
}

/** The running clock, m:ss — a timer, so it counts rather than rounds. */
export function fmtClock(ms: number): string {
  const t = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
}
