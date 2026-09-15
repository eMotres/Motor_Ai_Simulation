/**
 * The thermal solve's payload — its own type, in its own file.
 *
 * It used to be the second half of `simulation/fem-types.FemPayload`: one
 * interface describing both an electromagnetic field (A_z, |B|, J, losses) and
 * a temperature map, with every thermal field optional so the EM half kept
 * compiling.  That is how the Electromagnetic tab ended up hosting a thermal solve
 * it never asked for — the type said the two were the same thing.
 *
 * They are not.  The thermal solve carries its OWN sub-mesh, a nodal
 * temperature, a per-element heat-flux VECTOR, and the losses that drove it.
 * Nothing here is optional because the EM payload might not have it; a field is
 * optional only when the backend can honestly omit it.
 *
 * That sub-mesh grew on 2026-09-07.  It used to be the solids alone — the whole
 * of the air was dropped, which drew the slot white around the wire bars and
 * the clearance white around the rotor.  The user's answer to that ("надо
 * рисовать изоляцию и покрытие провода … и воздух тоже показывать — он же
 * входит в расчёт") is the shape of this file now: the insulation, the wire
 * enamel, the wire coating, the air gap and the rotor's pocket air are domains
 * with tags, names, conductivities whose SOURCE is stated, and temperatures of
 * their own.  Only the far field, the slip band and the bore air are still
 * dropped, and each for a reason the backend spells out.
 */

/** Peak and mean temperature of one component, °C. */
export interface ThermalComponent { max: number; avg: number }

/**
 * What ONE cooled surface actually did — echoed back by the solver, never
 * re-derived here: printing a second copy of it computed in the browser is how
 * two numbers for one thing start to disagree.
 *
 * 2026-09-07 the outlet temperature changed sides.  It used to be an INPUT (the
 * jacket was held between an inlet and an outlet the engineer typed, and the
 * flow was derived from that ΔT), which is backwards — a cooling loop is a
 * coolant, an inlet temperature and a PUMP, and how hot it comes back out is
 * what the machine does to it.  `t_out_c` is now a result like `h_conv` is.
 */
export interface ThermalCoolingSurface {
  /** 'air' | 'liquid' | 'manual' | 'none' */
  mode?: string;
  fluid?: string;
  h_conv?: number;
  air_speed_mps?: number;
  /** litres per minute — an INPUT for a liquid loop, echoed back */
  flow_lpm?: number;
  /** coolant inlet, °C (input) and outlet, °C (RESULT of the flow) */
  t_in_c?: number;
  t_out_c?: number;
  /** the temperature this film pulls its wall toward */
  t_sink_c?: number;
  re?: number;
  nu?: number;
  /** wetted area of this surface, m² */
  area_m2?: number;
  /** power actually leaving through this surface, W */
  heat_removed_W?: number;
  /** inner bore only: the radius the film is applied at */
  r_bore_mm?: number;
  /** liquid jacket: the groove's OWN film (Dittus–Boelter on the helical
   *  channel) — reported for the drawing; the solver pins the housing at the
   *  coolant outlet instead (`model`), which is what the 1e5 `h_conv` is */
  h_jacket?: number;
  model?: string;
  channel_velocity_mps?: number;
  channel_width_mm?: number;
  channel_height_mm?: number;
  hydraulic_diameter_mm?: number;
  note?: string;
  /** the backend may add descriptive fields; they are printed, never computed on */
  [k: string]: unknown;
}

/**
 * The air gap's effective conductivity — a RESULT, never an input.
 *
 * It is computed for every machine from the gap width and the rotor speed
 * (turbulent Taylor–Couette: the rotating film carries far more than still air),
 * so there is no box to type it into.  A number typed there would be a boundary
 * condition nobody measured, silently deciding how hot the rotor gets.
 */
export interface ThermalGapInfo {
  /** effective conductivity actually used, W/m·K */
  k_eff?: number;
  /** the still-air conductivity it is a multiple of */
  k_air?: number;
  /** Taylor number, and the Nusselt number that follows from it */
  Ta?: number;
  Nu?: number;
  /** gap width and mean gap radius, mm */
  delta_mm?: number;
  r_mean_mm?: number;
  regime?: 'conduction' | 'transitional' | 'turbulent' | string;
  note?: string;
  [k: string]: unknown;
}

/**
 * The shaft that sticks OUT of the housing — the rotor's third heat path.
 *
 * User 2026-09-07: *"торцы и лобовые части — только для вала, всё остальное
 * вращается внутри мотора"*.  The rotor's end faces and the end windings spin
 * inside a closed housing and have nowhere else to send their heat, so nothing
 * is modelled there on purpose.  The shaft comes out through the bearings, and
 * the exposed stubs lose heat to the room's air while turning in it.
 *
 * It is NOT a `ThermalCoolingSurface`: the area it acts on is out along the
 * axis, which the cross-section does not have, so there is no h·A on any facet
 * of this mesh — it is one lumped conductance `G_W_per_K` between the shaft's
 * mean temperature and the ambient, and the watts it removes are
 * `G · (t_shaft_mean_c − t_sink_c)`.
 */
export interface ThermalShaftEnds {
  /** 'rotating shaft in air' when the path is on, 'off' when nothing sticks out */
  mode?: string;
  /** exposed length on EACH side, mm — 0 = off */
  length_each_side_mm?: number;
  /** OD of the exposed shaft, mm, and where that number came from */
  diameter_mm?: number;
  diameter_source?: string;
  bore_diameter_mm?: number;
  /** how many ends come out of the housing: 2 = through shaft, 1 = one capped */
  sides?: number;
  /** film on a cylinder spinning in still air, W/m²K, and its Reynolds number */
  h_conv?: number;
  re_omega?: number;
  nu?: number;
  regime?: string;
  k_shaft?: number;
  /** the lumped conductance the solve used, W/K */
  G_W_per_K?: number;
  t_sink_c?: number;
  /** the shaft's own mean temperature — the other half of P = G·ΔT */
  t_shaft_mean_c?: number | null;
  heat_removed_W?: number;
  /** tanh(mL)/(mL): how much of the stub is actually working.  Well under 1 on
   *  a long one — which is why this is a FIN and not a wetted area. */
  fin_efficiency?: number;
  mL?: number;
  n_elements?: number;
  note?: string;
  [k: string]: unknown;
}

/** The END WINDINGS of an OPEN machine, in the airflow (2026-09-09).
 *
 *  `mode: 'housed'` when the machine has a housing — the end turns are then
 *  inside it, turning in their own air, and there is nothing to report but the
 *  fact.  On an open frame every number the conductance is built from is here,
 *  so `G = h·A_ew` and `end_windings_W = G·(t_winding_mean_c − t_sink_c)` are
 *  both checkable from the payload rather than trusted. */
export interface ThermalEndWindings {
  /** 'end turns in the airflow' when open, 'housed' when not */
  mode?: string;
  air_speed_mps?: number;
  /** 'given' | 'the housing air speed (…)' | 'still air (…)' */
  air_speed_source?: string;
  /** the end-winding factor the LOSSES were billed at, and where it came from */
  k_end?: number;
  k_end_source?: string;
  n_coils?: number;
  n_sides?: number;
  /** (k_end − 1)·L_stack/2 — one side's end turn */
  end_turn_length_mm?: number;
  bundle_thickness_mm?: number;
  bundle_width_mm?: number;
  /** 2·thickness + width: the tooth-facing face is not in the wash */
  perimeter_mm?: number;
  d_equiv_mm?: number;
  area_m2?: number;
  h_conv?: number;
  re?: number;
  nu?: number;
  regime?: string;
  /** 1 by construction: copper, short and thick — stated, not computed */
  fin_efficiency?: number;
  G_W_per_K?: number;
  t_sink_c?: number;
  t_winding_mean_c?: number | null;
  heat_removed_W?: number;
  n_elements?: number;
  note?: string;
  notes?: string[];
  [k: string]: unknown;
}

/** The ventilated axial channels between neighbouring coils (2026-09-09).
 *
 *  The duct's free cross-section and its wetted perimeter are MEASURED on the
 *  mesh — what is left of a slot once the wires are in it is not a number
 *  anybody types — and both come back so the hydraulic diameter can be checked
 *  against them. */
export interface ThermalSlotChannels {
  /** 'ventilated slot channels' when open, 'housed' when not */
  mode?: string;
  air_speed_mps?: number;
  air_speed_source?: string;
  n_channels?: number;
  /** whole machine, and per slot */
  wetted_perimeter_mm?: number;
  wetted_perimeter_per_slot_mm?: number;
  cross_section_mm2?: number;
  hydraulic_diameter_mm?: number;
  area_m2?: number;
  h_conv?: number;
  re?: number;
  nu?: number;
  regime?: string;
  G_W_per_K?: number;
  t_sink_c?: number;
  t_air_mean_c?: number | null;
  heat_removed_W?: number;
  n_elements?: number;
  note?: string;
  notes?: string[];
  [k: string]: unknown;
}

/** The BOLTED MOUNT (2026-09-14) — the flange a robot joint hangs on.
 *
 *  Not a `ThermalCoolingSurface` either, and for the shaft ends' reason: it
 *  leaves along the axis, which this cross-section does not have.  It is one
 *  lumped conductance between the stator's mean temperature and a HELD mount
 *  temperature, and because both numbers are in the block the identity
 *  `heat_removed_W === G_W_per_K · (t_housing_mean_c − t_sink_c)` is checkable
 *  rather than trusted.  `mode: 'off'` is an ANSWER: this machine was modelled
 *  as bolted to nothing. */
export interface ThermalMount {
  /** 'conduction' when there is a mount, 'off' when G = 0 */
  mode?: string;
  /** the conductance that was GIVEN, W/K */
  G_W_per_K?: number;
  /** the temperature the mount is held at, and whether that was typed */
  t_sink_c?: number;
  t_sink_source?: string;
  /** the stator's own mean temperature — the other half of P = G·ΔT */
  t_housing_mean_c?: number | null;
  heat_removed_W?: number;
  n_elements?: number;
  attached_to?: string;
  note?: string;
  [k: string]: unknown;
}

/** ONE of the four AXIAL end faces (2026-09-14): the end turns, the stator core
 *  end annulus, the rotor core's and the magnets'.  `mode: 'off'` on a housed
 *  machine, where whatever they hand to the air inside the housing comes
 *  straight back through it. */
export interface ThermalEndFace {
  mode?: string;
  /** both ends together (per face × sides × symmetry), and one face */
  area_m2?: number;
  area_per_face_m2?: number;
  char_len_mm?: number;
  orientation?: string;
  /** the still-air film and its radiation half — h_conv + h_rad = h_total */
  h_conv?: number;
  h_rad?: number;
  h_total?: number;
  ra?: number;
  nu?: number;
  regime?: string;
  n_faces?: number;
  emissivity?: number;
  G_W_per_K?: number;
  t_sink_c?: number;
  t_wall_c?: number;
  t_mean_c?: number | null;
  heat_removed_W?: number;
  n_elements?: number;
  /** where the AREA came from — the run's own k_end for the end turns, the
   *  mesh's section area for the cores and the magnets */
  area_source?: string;
  note?: string;
  [k: string]: unknown;
}

/** The four end faces together, with the totals the strip line quotes. */
export interface ThermalEndFaces {
  /** 'still' when the ends are exposed, 'off' when they are not */
  mode?: string;
  /** how many ends: 1 or 2 (0 when off) */
  sides?: number;
  emissivity?: number;
  /** all four, summed */
  heat_removed_W?: number;
  G_W_per_K?: number;
  /** the end-winding factor the LOSSES were billed at — the winding face's
   *  area is derived from it, never from a second estimate */
  k_end?: number;
  k_end_source?: string;
  winding?: ThermalEndFace;
  stator?: ThermalEndFace;
  rotor?: ThermalEndFace;
  magnet?: ThermalEndFace;
  notes?: string[];
  [k: string]: unknown;
}

/** A retaining sleeve in the gap heat path, when the machine has one. */
export interface ThermalSleeveInfo {
  present?: boolean;
  /** conductivity, W/m·K */
  k?: number;
  thickness_mm?: number;
  [k: string]: unknown;
}

/**
 * The whole cooling block: one entry per cooled surface, plus the gap.
 *
 * The FLAT fields are the pre-2026-09-07 single-surface shape.  They stay
 * optional so a payload cached before the two-surface split still renders
 * through `outerCooling` — not because the backend may omit the nested ones.
 */
/** Where the heat went, in machine watts, integrated on the solved mesh:
 *  the two cooled surfaces, the exposed shaft ends and the gap bridge
 *  (rotor → stator).  The rotor's own budget is `bore_W + gap_W +
 *  shaft_ends_W` = everything the rotor side makes; how it splits between the
 *  shaft and the gap is the number the user asked for ("ротор придётся
 *  охлаждать в основном через вал"). */
/** The ROTOR's own balance on the 2-D section: out across the AIR GAP into the
 *  stator, in off the BORE surface, and — beside them, not inside either — the
 *  axial path down any exposed shaft stubs.  `closure_W` says how closely the
 *  three come back to `rotor_W`.  Added 2026-09-10; absent on an older result. */
export interface ThermalRotorHeatSplit {
  /** '2D' — both paths are surface integrals on the solved cross-section. */
  dimensionality?: string;
  rotor_W?: number;
  /** out through the OUTER diameter, across the air gap into the stator */
  gap_W?: number;
  gap_pct?: number | null;
  /** in through the INNER diameter, off the bore surface */
  bore_W?: number;
  bore_pct?: number | null;
  /** the lumped OUT-OF-PLANE path down the exposed shaft stubs — reported
   *  beside the two, never folded into either. 0 when nothing sticks out. */
  axial_shaft_ends_W?: number;
  axial_shaft_ends_pct?: number | null;
  /** the rotor's OTHER out-of-plane path (2026-09-14): the rotor core's and the
   *  magnets' own end faces.  0 on every housed machine, and 0 is a statement. */
  axial_end_faces_W?: number;
  axial_end_faces_pct?: number | null;
  closure_W?: number;
  note?: string;
}

/** The mirror of `ThermalRotorHeatSplit` (2026-09-14): everything the stator
 *  side makes plus what the rotor hands it across the gap, against the doors
 *  out.  It exists because the robotics mode made the question a design
 *  decision — on a joint in still air the housing gives the room ~3 W of 64 and
 *  the BOLTS take the rest, so "how much leaves through the mount" is the number
 *  the flange is designed from. */
export interface ThermalStatorHeatSplit {
  dimensionality?: string;
  /** what the stator side makes (copper + stator iron + its mechanical heat) */
  stator_W?: number;
  /** …and what crosses the air gap into it */
  gap_in_W?: number;
  total_in_W?: number;
  housing_W?: number;
  housing_pct?: number | null;
  mount_W?: number;
  mount_pct?: number | null;
  end_faces_W?: number;
  end_faces_pct?: number | null;
  end_windings_W?: number;
  slot_channels_W?: number;
  closure_W?: number;
  note?: string;
}

export interface ThermalHeatBudget {
  rotor_heat_split?: ThermalRotorHeatSplit;
  /** where the STATOR side's heat went (2026-09-14) — absent on an older result */
  stator_heat_split?: ThermalStatorHeatSplit;
  losses_W?: number;
  housing_W?: number;
  /** which half of the housing's watts left as convection and which as light.
   *  Only the still-air film has two mechanisms; every other mode reports its
   *  whole heat as convection and 0 W of radiation, which is what those models
   *  say.  The split is exact — both films act on the same area and the same ΔT
   *  — so it is the ratio of the two coefficients, not an apportionment. */
  housing_convection_W?: number;
  housing_radiation_W?: number;
  /** the bolted flange: G·(T_stator − T_mount).  0 on a machine bolted to
   *  nothing — which is what every answer before 2026-09-14 assumed silently. */
  mount_W?: number;
  /** the four AXIAL end faces summed (end turns, both cores, the magnets).
   *  0 on a housed machine. */
  end_faces_W?: number;
  bore_W?: number;
  gap_W?: number;
  /** watts leaving down the shaft stubs that come out of the housing — a rotor
   *  outflow like `bore_W`, but along the AXIS rather than across a facet of
   *  this cross-section (see `ThermalShaftEnds`).  0 when nothing sticks out. */
  shaft_ends_W?: number;
  /** watts leaving off the END TURNS and out of the SLOT CHANNELS of an OPEN
   *  frame (2026-09-09).  Both 0 on a housed machine — and 0 there is a
   *  statement, not a gap: the end turns and the slot air are inside the
   *  housing.  Every watt that leaves the model is on one of these lines, so
   *  `housing_W + bore_W + shaft_ends_W + end_windings_W + slot_channels_W`
   *  is what has to come back equal to `losses_W`. */
  end_windings_W?: number;
  slot_channels_W?: number;
  /** 'housed' | 'open' — which model the numbers above belong to */
  frame?: string;
  /** ∫q dV inside the slip radius — what the ROTOR side makes.  In steady state
   *  it leaves across the gap or through the bore and nowhere else, so
   *  `gap_W + bore_W === rotor_W`.  Reported since 2026-09-07; before that the
   *  panel had to add the two outflows up itself, which is the same number only
   *  while nothing is wrong. */
  rotor_W?: number;
  /** coil → slot: ∮q·n over the winding's own boundary, W.  Since the liner and
   *  the wire coating are meshed domains this is a measurement, not a lumped link
   *  — in steady state it returns the copper loss. */
  slot_liner_W?: number;
  coil_W?: number;
  /** the legacy lumped coil-island bridge; 0 whenever the slot resolves */
  slot_bridge_W?: number;
  /** node pairs tying the two halves of the sliding band at the slip radius */
  n_slip_ties?: number;
  n_islands_rotor?: number;
  n_islands_stator?: number;
  residual_W?: number;
  residual_pct?: number;
  em_loss_total_W?: number;
  note?: string;
}

/** One non-metal domain's conductivity AND where the number came from.
 *
 *  The source matters as much as the value: a liner solved with the assigned
 *  Nomex card and one solved with our documented default are two different
 *  claims, and only the payload can tell them apart. */
export interface ThermalMaterialUse {
  /** W/m·K.  The air gap reports `k_eff` (Taylor–Couette) instead. */
  k?: number;
  k_eff?: number;
  k_air?: number;
  material?: string | null;
  /** 'library' | 'default' | 'taylor_couette' | a sentence */
  source?: string;
  /** how many elements of the solved sub-mesh this domain got */
  n_elements?: number;
  model?: string;
  note?: string;
}

/** What the air re-tagger did: counts per named domain, and why. */
export interface ThermalAirDomains {
  counts?: Record<string, number>;
  n_air?: number;
  n_liner?: number;
  n_enamel?: number;
  /** false when the insulation polygons could not be built — the liner and the
   *  enamel are then inside `wire coating` and `note` says so */
  polygons?: boolean;
  note?: string;
}

export interface ThermalCooling extends ThermalCoolingSurface {
  /** the outer stator / housing surface */
  outer?: ThermalCoolingSurface;
  /** the inner rotor bore; `mode: 'none'` when it is not cooled */
  inner?: ThermalCoolingSurface;
  /** the shaft outside the housing; `mode: 'off'` when nothing sticks out */
  shaft_ends?: ThermalShaftEnds;
  /** the bolted MOUNT (2026-09-14); `mode: 'off'` = bolted to nothing */
  mount?: ThermalMount;
  /** the four AXIAL end faces (2026-09-14); `mode: 'off'` when housed */
  end_faces?: ThermalEndFaces;
  /** 'housed' | 'open' — how the machine is built (2026-09-09) */
  frame?: string;
  /** the two paths an OPEN frame has; `mode: 'housed'` when it does not */
  end_windings?: ThermalEndWindings;
  slot_channels?: ThermalSlotChannels;
  gap?: ThermalGapInfo;
  sleeve?: ThermalSleeveInfo | null;
  heat_budget?: ThermalHeatBudget;
  /** legacy flat spelling of the inlet / outlet */
  fluid_temp_in_c?: number;
  fluid_temp_out_c?: number;
}

/** The OUTER surface of any payload: the nested block when it is there, the old
 *  flat one folded into the same shape when it is not.  ONE reader, so a result
 *  restored from yesterday prints in exactly the tiles today's does. */
export function outerCooling(c?: ThermalCooling | null): ThermalCoolingSurface {
  if (!c) return {};
  if (c.outer) return c.outer;
  return {
    mode: c.mode, fluid: c.fluid, h_conv: c.h_conv, t_sink_c: c.t_sink_c,
    air_speed_mps: c.air_speed_mps, flow_lpm: c.flow_lpm,
    t_in_c: c.fluid_temp_in_c, t_out_c: c.fluid_temp_out_c, note: c.note,
  };
}

export interface ThermalPayload {
  /* ── the solid sub-mesh (metres, like every solver payload in this app) ── */
  vertices: [number, number][];
  triangles: [number, number, number][];
  domain_per_tri: number[];
  outlines?: { domain: number; loops: [number, number][][] }[];
  /** [xmin, xmax, ymin, ymax] — informational; the adapter derives its own
   *  extent from the vertices it actually draws */
  extent?: [number, number, number, number];
  n_vertices?: number;
  n_triangles?: number;
  part_names?: Record<string, string>;

  /* ── the answer ───────────────────────────────────────────────────────── */
  /** °C per mesh vertex — a conduction solve's natural (continuous) output */
  temperature_per_node?: number[];
  /** q = −k∇T per element, W/m², as a real vector (it is drawn as arrows) */
  heat_flux_per_tri?: [number, number][];
  /** |q| per element, W/m² */
  flux_mag_per_tri?: number[];
  /** |∇T| per element, K/m — where the heat path is worst */
  grad_T_mag_per_tri?: number[];
  T_min?: number;
  T_max?: number;
  /** Peak / mean per part.  The five air entries joined the four metals on
   *  2026-09-07: the liner, the enamel, the wire coating, the gap and the rotor's
   *  pocket air are solved domains now, and a part that conducts has a
   *  temperature worth reading.  Any of them is `null` on a mesh too coarse to
   *  resolve it — `null` means "no elements", not "not modelled". */
  components?: {
    winding?: ThermalComponent | null;
    magnet?: ThermalComponent | null;
    stator?: ThermalComponent | null;
    rotor?: ThermalComponent | null;
    shaft?: ThermalComponent | null;
    sleeve?: ThermalComponent | null;
    liner?: ThermalComponent | null;
    enamel?: ThermalComponent | null;
    slot_fill?: ThermalComponent | null;
    gap_air?: ThermalComponent | null;
    pocket_air?: ThermalComponent | null;
  };
  /** k and its provenance for every non-metal domain the solve used. */
  materials_used?: {
    liner?: ThermalMaterialUse;
    enamel?: ThermalMaterialUse;
    slot_fill?: ThermalMaterialUse;
    gap_air?: ThermalMaterialUse;
    pocket_air?: ThermalMaterialUse;
    winding?: ThermalMaterialUse;
  };
  /** what the air classifier found, and whether it could place all of it */
  air_domains?: ThermalAirDomains;

  /* ── the boundary it was solved against ───────────────────────────────── */
  ambient_temp?: number;
  h_conv?: number;
  /** the sink temperature the housing is pulled toward — for a liquid loop this
   *  is NOT the ambient (it sits between inlet and outlet) */
  t_sink_c?: number;
  cooling?: ThermalCooling;
  /** effective in-slot and air-gap conductivities actually used, W/m·K.  The
   *  gap one is COMPUTED by the solver (Taylor–Couette, see `cooling.gap`) and
   *  echoed here for compatibility; it is never sent as an input. */
  slot_k?: number;
  gap_k?: number;
  /** the Taylor and Nusselt numbers behind `gap_k` — top-level duplicates of
   *  `cooling.gap`, kept because older cached payloads carry only these */
  gap_Ta?: number;
  gap_Nu?: number;
  k_steel?: number;
  k_magnet?: number;
  k_shaft?: number;
  k_copper?: number;

  /* ── the heat that drove it ───────────────────────────────────────────── */
  P_cu_W?: number;
  P_fe_W?: number;
  P_mag_eddy_W?: number;
  P_loss_total_W?: number;

  /* ── sector solve → full ring ─────────────────────────────────────────── */
  n_sectors?: number;
  symmetry_mult?: number;
  anti_periodic?: boolean;

  /* ── provenance and cost ──────────────────────────────────────────────── */
  /** seconds the BACKEND spent, measured around the solve — a cache hit carries
   *  the ORIGINAL solve's value, not its own microseconds */
  elapsed_s?: number;
  solve_time_s?: number;
  cached?: boolean;
  geometry_fingerprint?: string | null;
  source_label?: string;
}

/**
 * Expand a SECTOR solve into the full ring for display.
 *
 * The mechanics are the EM tiler's (rotate a copy of the sector mesh to each of
 * the N positions), minus everything that only an EM field needs: there is no
 * signed potential here and therefore no anti-periodic sign flip, because
 * temperature is a scalar with no symmetry of that kind — every sector of a
 * balanced machine is exactly as hot as the next.  The heat flux IS a vector, so
 * it is ROTATED with the sector rather than copied; copying it would draw every
 * arrow pointing the way the first sector's did.
 *
 * `n_sectors ≤ 1` (already the full disk) is returned untouched.
 *
 * Generic in the payload so the MESH preview (`ThermalMeshPayload`, which is a
 * thermal payload with no field on it plus the mesher's own bookkeeping) can be
 * tiled by the same function and stay its own type: a sector mesh drawn
 * untiled is a quarter of a motor on an empty tab.
 */
export function tileFullRing<T extends ThermalPayload>(p: T): T {
  const N = (p?.n_sectors as number) | 0;
  if (!p || !p.vertices || N <= 1) return p;
  const nv = p.vertices.length;

  const vertices: [number, number][] = [];
  const triangles: [number, number, number][] = [];
  const domain: number[] = [];
  const mk = <T,>(a?: T[]) => (a && a.length ? ([] as T[]) : undefined);
  const temp = mk(p.temperature_per_node);
  const fmag = mk(p.flux_mag_per_tri);
  const gmag = mk(p.grad_T_mag_per_tri);
  const hflux = mk(p.heat_flux_per_tri);
  const outlines: NonNullable<ThermalPayload['outlines']> = [];

  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;

  for (let k = 0; k < N; k++) {
    const th = (2 * Math.PI / N) * k, ca = Math.cos(th), sa = Math.sin(th);
    const off = k * nv;
    for (const [x, y] of p.vertices) {
      const X = x * ca - y * sa, Y = x * sa + y * ca;
      vertices.push([X, Y]);
      if (X < xmin) xmin = X; if (X > xmax) xmax = X;
      if (Y < ymin) ymin = Y; if (Y > ymax) ymax = Y;
    }
    for (const t of p.triangles) triangles.push([t[0] + off, t[1] + off, t[2] + off]);
    for (const v of p.domain_per_tri) domain.push(v);
    if (temp) for (const v of p.temperature_per_node!) temp.push(v);
    if (fmag) for (const v of p.flux_mag_per_tri!) fmag.push(v);
    if (gmag) for (const v of p.grad_T_mag_per_tri!) gmag.push(v);
    if (hflux) for (const q of p.heat_flux_per_tri!) {
      hflux.push([q[0] * ca - q[1] * sa, q[0] * sa + q[1] * ca]);
    }
    for (const o of (p.outlines || [])) {
      outlines.push({
        domain: o.domain,
        loops: o.loops.map(lp => lp.map(([x, y]) =>
          [x * ca - y * sa, x * sa + y * ca] as [number, number])),
      });
    }
  }

  return {
    ...p,
    n_sectors: 1, symmetry_mult: 1,
    n_vertices: vertices.length, n_triangles: triangles.length,
    vertices, triangles, domain_per_tri: domain,
    temperature_per_node: temp ?? p.temperature_per_node,
    flux_mag_per_tri: fmag ?? p.flux_mag_per_tri,
    grad_T_mag_per_tri: gmag ?? p.grad_T_mag_per_tri,
    heat_flux_per_tri: hflux ?? p.heat_flux_per_tri,
    outlines,
    extent: [xmin, xmax, ymin, ymax],
    // The spread of a generic is a fresh object type to the compiler, not `T`
    // itself; every field it carries either came from `p` or is replaced above,
    // so the cast asserts what the body has just built.
  } as unknown as T;
}
