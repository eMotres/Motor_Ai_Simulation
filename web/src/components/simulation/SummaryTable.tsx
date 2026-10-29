/**
 * SummaryTable — single overview card at the top of the Simulation tab.
 *
 * Pulls all key metrics from the most recent transient run (T, P, masses,
 * loss breakdown, efficiency, KV).  Numbers come straight from
 * /fem_transient → summary, so every value here is a REAL FEM result
 * (not analytical).
 */
import type { SixPhaseResult } from './sixPhase';
import React from 'react';
import { Box, Button, Chip, Paper, Typography, Tooltip } from '@mui/material';
import AddToCompareButton from '../compare/AddToCompareButton';
import { geoSignature } from '../common/geoSig';
import { geoSigDiffRows, staleBuildKeysText } from '../../lib/dutyGeometryDiff';
import { currentMatJson } from '../../lib/apiAuth';
import { assignmentSignature } from '../../lib/dutyMaterials';
import { fetchBearingLosses } from '../../lib/machineBearings';
import type { BearingLosses } from '../../lib/machineBearings';
import { useMotorStore } from '../../stores/motorStore';
import { couplingLine, couplingTooltip, coupledStateLine,
         coupledStateTip, continuousRatingLine,
         continuousRatingTip, s1ResultsAtLine,
         heldTorqueValues } from './coupledApi';
import type { CouplingBlock } from './coupledApi';
import SineVsInverterTable from './SineVsInverterTable';
import SaveContinuousPointButton from './SaveContinuousPointButton';
import EmResultTiles from './EmResultTiles';
import type { EmMech } from '../../lib/emResultTiles';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';

// UI strings: locales/<lng>/results.json (docs/I18N.md).
const tx = nsT('results');

/** Bench-probe result riding in the summary (backend measures it once per
 *  machine during the run) — small-signal Ld/Lq at the I≈0 iron state, the
 *  LCR-meter measurement simulated. */
interface BenchLdq {
  Ld_mH: number; Lq_mH: number; Lq_over_Ld: number | null;
  I_probe_arms: number; connection: string | null;
}

export interface TransientSummary {
  /** Backend summary-shape version — lets the restore path adopt a rebuilt
   *  summary when the code gained new derived cells (see _SUMMARY_SHAPE_V). */
  summary_shape_v?: number;
  rpm:                 number;
  I_phase_rms_A:       number;
  gamma_deg:           number;
  // The winding the run was solved with.  Torque, voltage and R_phase all scale
  // with it, so it is part of the operating point, not a setting on the side.
  // Absent on runs saved before it was stamped — unknown, never assumed.
  connection?:         string;
  n_parallel?:         number;
  // STRANDS IN HAND: k wires wound together as one turn.  The slot keeps its
  // num_wires_per_slot physical wire rows (same copper, same fill, same mass),
  // but the coil has turns_per_coil = (rows/k)×wire_split SERIES turns — so EMF
  // and Kt divide by k, R by k², and the phase current splits over
  // n_parallel_eff = paths×k conductors (the split divides no current: its
  // strips are turns).  Absent on runs saved before it was stamped.
  wire_parallel?:      number;
  turns_per_coil?:     number;
  n_parallel_eff?:     number;
  // Which way the power flowed — decided by the backend from the shaft-power
  // sign, so a hand-typed generator angle is labelled too.
  op_mode?:            'motor' | 'generator' | string;
  // Terminal parameters (persisted with the summary like everything else).
  // R includes the end-winding; Ld/Lq are the INCREMENTAL inductances of THIS
  // operating point — ∂ψ/∂i at the loaded iron state, DIFFERENTIAL (tangent
  // reluctivity, small-signal) since 2026-10-11; `ldq_method` says which, and
  // the frozen-permeability secant values ride under *_fp_secant_mH.  The
  // chord they replaced is kept under *_chord_mH for comparison only; it is
  // not an inductance under saturation.  Absent on runs from before the dq
  // stamp, and Ld/Lq absent on runs before the incremental measurement
  // (2026-09-20); dq_note says why a value was withheld.
  R_phase_ohm?:         number | null;
  R_line_line_ohm?:     number | null;
  Ld_mH?:               number | null;
  Lq_mH?:               number | null;
  Ld_inc_mH?:           number | null;
  Lq_inc_mH?:           number | null;
  /** cross term ∂ψd/∂i_q of the same 2×2 matrix */
  Ldq_inc_mH?:          number | null;
  /** frozen-permeability SECANT values of the same frames (not small-signal) */
  Ld_fp_secant_mH?:     number | null;
  Lq_fp_secant_mH?:     number | null;
  Ld_chord_mH?:        number | null;
  Lq_chord_mH?:         number | null;
  ldq_method?:          string | null;
  /** true when a non-monotone B-H table forced a regularized tangent: Ld/Lq withheld */
  ldq_tangent_clamped?: boolean | null;
  psi_pm_Wb?:           number | null;
  /** the magnets' own flux linkage in the LOADED iron (frozen permeability) */
  psi_pm_frozen_Wb?:    number | null;
  /** 100·(1 − ψ*_PM/ψ_PM): positive = the load sagged the magnet flux */
  psi_pm_sag_pct?:      number | null;
  /** area-weighted mean |B| over the air-gap clearance, period-averaged */
  B_gap_mean_T?:        number | null;
  saliency_Lq_over_Ld?: number | null;
  dq_note?:             string;
  T_em_avg_Nm:         number;
  T_ripple_pct:        number;
  T_ripple_raw_pct?:   number;
  T_ripple_filt_pct?:  number;
  // The run's OWN torque-method label ("coulomb_virtual_work" since
  // 2026-09-30; older runs keep theirs, e.g. "energy_mean+maxwell_ripple").
  torque_method?:      string | null;
  // Coulomb two-ring self-check: true = the air-gap mesh limits the ripple.
  ripple_mesh_limited?: boolean | null;
  ripple_self_check_rel?: number | null;
  gap_layers_note?:    string | null;
  // Set only when a Mesh setting fell back to the last-resort default
  // because the machine has no saved Mesh setting for it.
  mesh_settings_note?: string | null;
  // The measured gap rule re-solved this run with more layers per side.
  gap_refinement?: { applied?: boolean; gap_layers_per_side?: number;
                     persist_gap_layers?: number | null;
                     first?: { gap_layers_per_side?: number } } | null;
  P_mech_W:            number;
  V_phase_peak_V:      number;
  V_phase_rms_V:       number;
  V_line_peak_V:       number;
  // Terminal connection of the three phases — decides what "line" means.
  star_delta?:         string;          // "star" | "delta"
  // six-phase winding (two in-phase sets): per-set values + the FEM L_xy
  six_phase?:          SixPhaseResult;
  I_line_rms_A?:       number;          // delta: sqrt(3) x phase; star: = phase
  I_terminal_rms_A?:   number;          // the SETPOINT (three leads to the inverter)
  I_winding_rms_A?:    number;          // delta: setpoint / sqrt(3); star: = setpoint
  V_line_peak_solved_V?: number | null; // the SOLVER's line peak for the connection
  V_line_rms_solved_V?:  number | null;
  Kt_Nm_per_A_line?:     number;
  R_phase_eq_star_ohm?:  number | null;
  Ld_eq_star_mH?:        number | null;
  Lq_eq_star_mH?:        number | null;
  P_cu_circulating_W?: number;          // delta only: triplen loop current loss
  L0_mH?:              number | null;
  V_line_rms_V:        number;
  KV_rpm_per_V_phase:  number;
  KV_rpm_per_V_line:   number;
  /** No-load KV computed from the run's cached ψ_PM (fundamental back-EMF)
   *  — no extra solve; what a spun-motor bench measurement reads. */
  KV_noload_rpm_per_V_line?: number | null;
  V1_phase_V?:         number;
  THD_pct?:            number;
  THD_LL_pct?:         number;
  I1_A?:               number;   // fundamental current amplitude (branch, from FFT)
  THD_I_pct?:          number;   // ≈0 in current drive; real parasitics in voltage drive
  V1_LL_V?:            number;   // fundamental of the ACTUAL line-to-line waveform
  Kt_Nm_per_Arms?:     number;
  J_coil_A_per_mm2?:   number;   // coil current density = I_rms/parallel over one strand's copper section
  A_phase_mm2?:        number;   // copper section the phase current flows through (strand × parallel paths)
  slot_fill_pct?:      number;   // measured copper / winding window (CAD polygons)
  A_slot_mm2?:         number;   // the winding window itself
  A_copper_slotted_mm2?: number; // conductor area sitting in it
  P_loss_total_W:      number;
  P_core_W:            number;
  // Split behind P_core_W, per half — what the core loss is MADE of.  `model`
  // says WHICH loss model produced it: 'measured_surface' (the manufacturer's
  // P(B, f) table interpolated directly, summed over the locus's harmonics) or
  // 'bertotti' (the three fitted coefficients).  Absent on runs that predate
  // the split.
  P_core_terms?: Record<string, { hysteresis_W: number; eddy_W: number;
                                  excess_W: number; k_f: number;
                                  model?: string }>;
  /** The core loss per half [W] — the two halves of P_core_W the heat split
   *  below is built on. Absent on runs that predate the split. */
  P_core_stator_W?:    number;
  P_core_rotor_W?:     number;
  /** HEAT TO REMOVE, per side [W].  stator = stator iron + all copper;
   *  rotor = rotor iron + magnet + shaft + sleeve eddy.  They sum to
   *  P_loss_total_W by construction.  Absent on runs that predate them. */
  P_loss_stator_W?:    number;
  P_loss_rotor_W?:     number;
  /** false = this run carried no per-half iron breakdown, so the WHOLE core
   *  loss was billed to the stator side (an assumption, not a solve). */
  P_loss_split_measured?: boolean;
  P_stranded_W:        number;
  // wire_split = N lays N strips of wire_width side by side across the slot,
  // 2×wire_spacing_x apart.  They are REAL GEOMETRY since 2026-09-08: drawn,
  // meshed and solved as separate conductors, so the coupled σ·∂A/∂t solve
  // reports the AC copper of the narrow strips.
  // cu_ac_solved_ignores_wire_split is the retired flag of the electrical-only
  // split (strips of wire_width/N with no CAD, assumed transposed, the mesher
  // handed the whole bar).  It is always false now; older stored summaries can
  // still carry true, and those runs really were over-reads.
  wire_split?:                       number;
  cu_ac_solved_ignores_wire_split?:  boolean;
  P_solid_W:           number;
  /** Eddy loss solved in the carbon-fibre retaining sleeve [W]. Broken out of
   *  P_solid_W because a hoop-wound CFRP ring conducts ~80 S/m ACROSS the
   *  fibres (the direction the axial induced current must take), so it is
   *  milliwatts and would round to nothing inside P_solid_W. Absent when the
   *  machine has no sleeve. */
  P_sleeve_W?:         number | null;
  /** magnet eddy and shaft eddy on their own (2026-09-07) — the sum is P_solid_W */
  P_mag_W?:            number | null;
  P_shaft_W?:          number | null;
  /** Retaining-sleeve burst check at this run's speed. Thin-ring, OWN MASS
   *  ONLY (sigma = rho*omega^2*r_mean^2) — the magnet pressure the sleeve is
   *  fitted to retain is NOT in it, so this is a lower bound. Reported, never
   *  gating. Absent when there is no sleeve. */
  sleeve_hoop?: { sigma_hoop_MPa: number; r_mean_mm: number; rpm: number;
                  material: string; density_kg_m3: number; thickness_mm: number;
                  strength_MPa?: number; utilisation_pct?: number } | null;
  solid_loss_not_solved?: boolean;  // imposed-voltage runs: rotor eddy not solvable yet
  // What `magnet_lamination` (axial slice length, mm) did to the 2-D magnet
  // eddy loss.  factor 1.0 = solid magnet; {} = a run from before the model.
  magnet_segmentation?: { slice_mm?: number; stack_mm?: number; width_mm?: number;
                          factor?: number; n_bodies?: number; model?: string };
  // Discarded frames at θ<0 the coupled eddy solve needed before its σ·∂A/∂t
  // start-up transient was quiet — 0 when the solve did not run.  Since
  // 2026-09-05 it INCLUDES the demag pre-pass (one more discarded period, Br
  // ratchet active on the settled field); demag_prepass_frames is that part.
  eddy_warmup_frames?: number;
  demag_prepass_frames?: number;
  // Did that warm-up actually SETTLE (2026-09-07)?  The frame count above is
  // what it cost, not whether it worked: the march can end at its one-shot cap
  // with the σ·∂A/∂t start-up transient still running, and then the magnet /
  // shaft eddy watts on this card — and the efficiency beside them — are
  // START-UP VALUES.  eddy_settle_residual is what was left at the handoff, as
  // a fraction of the settled solid loss; eddy_capped says the march ran out of
  // budget rather than passing the test.  Absent on older runs → treated as
  // settled, which is how those runs already read.
  eddy_settled?: boolean | null;
  eddy_settled_via_accelerator?: boolean | null;
  eddy_capped?: boolean | null;
  eddy_settle_residual?: number | null;
  eddy_settle_tol?: number | null;
  efficiency:          number;
  // TOTAL = iron + copper + magnets + shaft (the divisor of every density below);
  // ACTIVE = the EM-active mass without the shaft — the basis a commercial FEM active-mass
  // expression quotes, so it is the tile a user cross-checks against commercial FEM.
  mass_total_kg:       number;
  mass_active_kg?:     number;
  // THE FULL MOTOR MASS of the simulated geometry (owner 2026-10-20), stamped by the server from the loaded configuration's
  // mass_summary: mass_full_kg = mass_total_kg + mass_non_active_kg (holders, rings, bearings, screws, board).  Absent /
  // mass_partial = no CAD build: the tiles then print the active mass labelled so.  See lib/fullMass.ts.
  mass_full_kg?:       number;
  mass_non_active_kg?: number;
  mass_partial?:       boolean;
  mass_partial_reason?: string;
  mass_basis?:         string;
  mass_scope?:         string;
  torque_per_mass_full_Nm_kg?: number;
  power_per_mass_full_W_kg?:   number;
  loss_density_full_W_kg?:     number;
  Km_per_mass_full_Nm_sqrtW_kg?: number | null;
  mass_area_source?:   string;
  rotor_inertia?: { J_kg_m2: number; J_kg_cm2: number; rotor_iron: number;
                    magnet: number; shaft: number; sleeve?: number;
                    source: string } | null;
  Km_Nm_sqrtW?: number | null;
  Km_per_mass_Nm_sqrtW_kg?: number | null;
  demag?: { bh_kept_vol_pct?: number; bh_loss_pct?: number;
            br_kept_vol_pct: number; loss_pct: number;
            // Worst single element: a flagged CORNER diagnostic (sharp-corner
            // value, mesh-dependent) with its location — not the magnet's
            // figure.  br_worst_pct is the same number on pre-2026-09-26 results.
            br_corner?: BrCorner | null; br_worst_pct?: number;
            area_derated_pct: number; grade_nominal?: number;
            grade_effective?: number; magnet_name?: string;
            energy_total_J?: number; energy_lost_J?: number } | null;
  saturation?: { T_linear_Nm: number; droop_pct: number;
                 T_reluctance_Nm?: number | null } | null;
  end3d?: { k_flux: number; k_flux_self: number; fidelity: string;
            /** no passport for THIS geometry — the last one measured on the
             *  same machine (slots/poles/OD) is served, flagged, until Stage A
             *  is re-run (user 2026-09-03: keep it, highlight it) */
            inherited?: boolean; inherited_from?: string; inherited_note?: string;
            machine?: string; T_corrected_Nm: number;
            V_line_peak_corrected_V?: number | null;
            /** MEASURED 3-D torque factor (Stage B), when a passport has one —
             *  the only factor Kt / Km may carry (owner 2026-09-30) */
            k_T?: number | null;
            /** the k_T was measured on an earlier geometry of this machine */
            k_T_inherited?: boolean } | null;
  bench_ldq?: BenchLdq | null;
  /** Coil temperature the run was solved at [°C] — R and copper loss are
   *  quoted at it; the R@25°C view rescales the R cells from it. */
  coil_temp_C?: number;
  /** THE MECHANICAL HALF OF THE LOSS PICTURE, SERVER-SIDE (2026-09-08).
   *
   *  User: "when the coupled run runs, the WHOLE model must be solved, and ALL
   *  the losses must be carried into the electromagnetic calculation".  Until
   *  that date these four numbers were computed HERE, in the browser, from
   *  /api/bearings/losses — so the stored run, the datasheet, the report and
   *  the coupled loop all described a machine whose largest sub-3000-rpm loss
   *  was missing.  The physics is unchanged and still ANALYTIC (SKF frictional
   *  moment + Couette/disc windage); what changed is that the RUN carries it.
   *
   *  ABSENT — never zero — on a machine that names no bearings, on an
   *  optimizer candidate, and on any run solved before this date.  Absent means
   *  UNKNOWN, and the cells fall back to the browser estimate and say so. */
  P_bearings_W?:       number;
  P_windage_W?:        number;
  P_mech_extra_W?:     number;
  /** The temperature the grease viscosity was interpolated at [°C] and where
   *  it came from: 'thermal' (this machine's last map), 'coupled' (converged by
   *  the loop), 'assigned' (set on the machine), 'default' (nothing said).
   *  M_rr goes as ν^0.6, so the source belongs beside the number. */
  bearing_temp_c?:     number;
  bearing_temp_source?: string;
  bearing_temp_note?:  string;
  P_loss_total_incl_mech_W?: number;
  P_shaft_net_W?:      number | null;
  /** the solved circuit's own terminal power — carries only the losses inside
   *  the field solve, so it is a balance diagnostic, never the Elec power tile */
  P_elec_in_solved_W?: number | null;
  P_shaft_convention?: string;
  efficiency_shaft?:   number | null;
  /** The SKF model's own answer, in short: which cards, at what speed, split
   *  into rolling/sliding/seal per end and gap/faces for the windage. */
  mech_losses?: {
    rpm: number;
    cards?: string[];
    lubrication?: string;
    preload_n?: number;
    rotor_mass_kg?: number;
    rotor_mass_source?: string;
    M_bearings_Nm?: number;
    bearings?: Array<{ end: string; bearing: string; P_W: number;
                       M_total_Nm: number; M_rr_Nm?: number; M_sl_Nm?: number;
                       M_seal_Nm?: number; nu_mm2_s?: number;
                       speed_ok?: boolean | null; speed_verdict?: string;
                       limit_rpm?: number | null }>;
    windage?: { P_W?: number; P_gap_W?: number; P_faces_W?: number;
                M_total_Nm?: number; gap_regime?: string; face_regime?: string;
                delta_mm?: number };
    model?: string;
    notes?: string[];
  };
  /** THE EM<->THERMAL LOOP (2026-09-08), present ONLY on a run made with the
   *  Coupled thermal toggle on.  Absent on every ordinary run — which is what
   *  makes "off = today's behaviour" visible in the payload rather than only
   *  claimed — and its presence says the cards on this card set were computed at
   *  temperatures the machine actually reaches, not at temperatures typed in. */
  coupling?: CouplingBlock;
  /** Per-part accounting the mass was computed under: {part: 'reference'|'excluded'};
   *  absent/empty on an ordinary machine (every part included). */
  part_states?:        Record<string, string>;
  mass_components:     Array<{ name: string; mass_kg: number; volume_cm3?: number;
                               material?: string; note?: string;
                               /** 'reference' — customer-supplied; the row is
                                *  kept but contributes nothing to any total. */
                               state?: string; counted?: boolean;
                               mass_modelled_kg?: number }>;
  torque_per_mass_Nm_kg: number;
  power_per_mass_W_kg:   number;
  loss_density_W_kg:     number;
  // Nonlinear-solve honesty: false = at least one frame of the reported window
  // was accepted without meeting its solver's convergence test, so everything
  // above is an average that includes an unconverged field.
  nonlinear_converged?:  boolean;
  // Imposed-voltage / PWM settling honesty (B5, PWM study 2026-09-13): the DC
  // left in the phase currents over the REPORTED electrical period, worst
  // phase.  Zero on a settled orbit; above the tolerance the reported torque
  // ripple and current ripple ARE that offset.  Null on an imposed-current run.
  pwm_dc_residual_A?:    number | null;
  pwm_dc_residual_phase?: string | null;
  pwm_dc_tol_A?:         number | null;
  pwm_dc_unconverged?:   boolean | null;
  // Time resolution the run ACTUALLY used vs the one that was asked for: the
  // solver snaps steps/period onto the slip-node divisor grid.
  steps_snapped?:              boolean;
  n_steps_per_period?:         number;
  n_steps_per_period_requested?: number;
  slip_nodes_per_period?:      number;
  nonlinear_resid_max?:  number;
  nonlinear_tol?:        number;
  nonlinear_unconverged_frames?: number[];
  // WHICH MACHINE these numbers describe.  Stamped by TransientCharts from the
  // run that produced them and carried through PhysicsDashboard into
  // `sim.lastSummary`, so a summary that outlives its motor (preset switch,
  // page reload, applied Sweep design) can be recognised as belonging to
  // another machine instead of being read as the current one.  Absent = a run
  // from before the stamp existed: unknown, and reported as unknown.
  _geoSig?:              string;
  // WHICH RUN produced them — the payload's `computed_at` (user 2026-09-13).
  // `sim.lastSummary` and `sim.lastTransient` are written by two different
  // components, so "Save to duty" needs an identity, not a same-point guess,
  // before it may file one run's waveforms under another run's numbers.
  _runAt?:               string;
  // The backend's independent verdict on a RESTORED run (its own fingerprint of
  // the machine it solved vs the machine loaded now).  Either witness is enough.
  _geoStaleBackend?:     boolean;
  /** rms TERMINAL phase current the run SOLVED — present only where the
   *  current was the answer (imposed voltage / PWM / imposed waveform), absent
   *  on a current-drive run where the request already states it. */
  I_phase_rms_solved_A?: number;
  /** GENERATOR → BATTERY.  Present only on a run that carried a `battery`
   *  payload AND drove the machine from an imposed voltage — a current-drive
   *  run has no bridge and no DC link, so it has no charging card. */
  battery_charge?: {
    V_oc_V: number; R_pack_ohm: number; V_bus_V: number; V_bus_rise_V: number;
    capacity_ah?: number | null; i_charge_max_A?: number | null;
    cells_series?: number | null; cells_parallel?: number | null;
    chemistry?: string | null; r_int_mohm_per_cell?: number | null;
    /** Which of the numbers above nobody measured, field → why. */
    placeholders?: Record<string, string>;
    /** The honest headline: mechanical in minus every loss the card reports. */
    P_charge_W: number;
    P_mech_in_W: number;
    P_loss_machine_W: number;
    /** Off the modulator's own switch states — the 2-D circuit's terminal
     *  power, which does NOT carry the iron loss or the end-winding copper. */
    P_charge_circuit_W?: number;
    I_charge_circuit_A?: number;
    I_dc_mean_A?: number; I_dc_rms_A?: number; I_dc_ripple_pp_A?: number;
    balance_gap_W?: number; balance_gap_pct?: number; balance_gap_note?: string;
    I_charge_A: number;
    C_rate?: number;
    i_charge_headroom_A?: number; over_i_charge_max?: boolean;
    eta_charge?: number | null;
    charging?: boolean;
    verdict?: string;
    P_pack_r_loss_W?: number;
    method?: string; bridge_model?: string;
    bus_coupling?: {
      enabled: boolean; converged: boolean; iterations: number;
      max_iterations: number; tol_pct: number; V_oc_V: number;
      R_pack_ohm: number; note?: string;
      trace: Array<{ iteration: number; V_bus_V: number; I_charge_A: number;
                     V_bus_next_V: number; delta_V: number }>;
    } | null;
    charge_search?: {
      objective: string;
      seed: { V1_peak_V: number; delta_deg: number };
      best: { V1_peak_V: number; delta_deg: number };
      constraints: { I_phase_rms_max_A?: number | null;
                     i_charge_max_A?: number | null };
      coarse_steps_per_period: number; fine_steps_per_period: number;
      n_coarse_solves: number;
      P_charge_coarse_W?: number | null; P_charge_fine_W?: number | null;
      coarse_to_fine_shift_W?: number | null;
      method: string; caveat: string;
      evals: Array<{ V1_peak_V: number; delta_deg: number;
                     P_charge_W?: number; I_phase_rms_A?: number;
                     I_charge_A?: number; feasible: boolean;
                     violation?: number; refused?: string }>;
    };
  };
  /** Stamp of the MATERIAL ASSIGNMENT this run was solved with (machine +
   *  active-duty overrides; lib/dutyMaterials.assignmentSignature).  Absent =
   *  unknown, which never flags staleness on its own. */
  _matSig?:              string;
}

interface Props {
  summary: TransientSummary | null;
  loading?: boolean;
  fromSweep?: boolean;   // numbers reused from an applied Sweep design (no re-run)
  // Set once a Run has produced its OWN numbers for a design that was applied
  // from Sweep/Compare: how far this solve landed from the point's numbers.
  // Currently-set operating point (the Simulation inputs). If the shown summary was
  // computed at a DIFFERENT current / γ (e.g. the user changed it after the run, and
  // the Optimize tab already uses the new point), flag the result as stale so the
  // Sim numbers aren't mistaken for the current point.
  liveOp?: { current?: number; gamma?: number; connection?: string; rpm?: number };
}

/** Short label of a run's torque method (its own stored label). */
function torqueMethodShort(m: string): string {
  if (m === 'coulomb_virtual_work') return 'Torque: Coulomb';
  if (m === 'maxwell_stress') return 'Torque: Maxwell';
  return 'Torque: hybrid';
}

function torqueMethodTooltip(m: string): string {
  if (m === 'coulomb_virtual_work') {
    return 'Coulomb virtual work: mean and ripple from the co-energy derivative on the air-gap rings.';
  }
  if (m === 'maxwell_stress') return 'Raw Maxwell stress (older run, near-zero current).';
  return `Older method (${m}): energy / terminal-work mean plus raw Maxwell ripple.`;
}

/** One card row: its cells share the row width equally and never wrap into
 *  the next row (grid auto-flow by column), so a row stays the row the user
 *  defined however many optional cells this run carries. */
const ROW = {
  display: 'grid', gridAutoFlow: 'column', gridAutoColumns: 'minmax(0, 1fr)', gap: 1,
} as const;

interface BrCorner {
  flag: 'corner'; br_pct: number; x_mm?: number; y_mm?: number;
  r_mm?: number; theta_deg?: number; magnet_tag?: number | null;
  element_area_pct?: number; note?: string;
}

const Cell: React.FC<{
  label: string; value: string; unit?: string;
  tooltip?: string; accent?: 'green' | 'amber' | 'red' | 'blue' | 'default';
}> = ({ label, value, unit, tooltip, accent = 'default' }) => {
  const colour = {
    green:   '#4ade80',
    amber:   '#fbbf24',
    red:     '#f87171',
    blue:    '#60a5fa',
    default: 'var(--text-0)',
  }[accent];
  const cell = (
    <Box sx={{
      display: 'flex', flexDirection: 'column',
      px: 1.25, py: 0.8,
      bgcolor: 'var(--panel-2)', border: '1px solid var(--app-bg)',
      borderRadius: 1, minWidth: 0,
    }}>
      <Typography sx={{ fontSize: 9, color: 'var(--text-3)',
        letterSpacing: '0.04em', textTransform: 'uppercase',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {label}
      </Typography>
      <Typography sx={{ fontSize: 16, fontWeight: 700, color: colour,
        fontFamily: 'monospace', lineHeight: 1.2 }}>
        {value}
        {unit && (
          <Typography component="span" sx={{ fontSize: 10, color: 'var(--text-4)',
            ml: 0.5, fontWeight: 400 }}>{unit}</Typography>
        )}
      </Typography>
    </Box>
  );
  return tooltip ? <Tooltip title={tooltip} placement="top">{cell}</Tooltip> : cell;
};

const SummaryTable: React.FC<Props> = ({ summary, loading, fromSweep, liveOp }) => {
  useTranslation('results'); // re-render on language change; lazy-loads the namespace
  // Hook FIRST — the empty-state early-return below must not sit between the
  // component entry and a hook call.
  const geometry = useMotorStore(s => s.geometry);
  const liveSig = React.useMemo(
    () => geoSignature(geometry as Record<string, unknown>), [geometry]);
  // The materials-staleness check below reads currentMatJson() during render,
  // so it only speaks when this card re-renders.  A material CHANGE is exactly
  // the moment it must speak: a duty's own material picks ('duty-materials-
  // changed' — the ?mat= payload carries them over the machine's) and an
  // assignment change now both bump it.  Cheap: one integer, no fetch.
  const [, bumpMat] = React.useState(0);
  React.useEffect(() => {
    const on = () => bumpMat(n => n + 1);
    const EV = ['duty-materials-changed', 'mat-assign-changed',
                'mat-assign-local-changed', 'sim-settings-restored'];
    for (const ev of EV) window.addEventListener(ev, on);
    return () => { for (const ev of EV) window.removeEventListener(ev, on); };
  }, []);
  // "3D corrections" toggle (user's design, 2026-08-24): ON → every
  // flux-proportional tile is rescaled by the machine's Stage-A k_flux;
  // OFF → pure 2D; no passport → the toggle is disabled and says so.
  // DISPLAY-level only: Compare snapshots and saved duties keep pure 2D.
  // R at 25 °C (bench-check view): display-level rescale of the two R cells
  // by the copper ρ(T) ratio.  Losses/η stay at the solve temperature — a
  // true cold loss set needs a cold re-solve (set coil temperature, re-run).
  const [r25, setR25State] = React.useState<boolean>(() => {
    try { return localStorage.getItem('sim.r25') === '1'; } catch { return false; }
  });
  const setR25 = (v: boolean) => {
    setR25State(v);
    try { localStorage.setItem('sim.r25', v ? '1' : '0'); } catch { /* quota */ }
  };

  // KV view: loaded (terminal voltage of THIS run) vs no-load (from ψ_PM —
  // the spun-motor bench figure).  Both are in the summary; the button only
  // picks which one the KV cell shows.
  const [kvNl, setKvNlState] = React.useState<boolean>(() => {
    try { return localStorage.getItem('sim.kvNoload') === '1'; } catch { return false; }
  });
  const setKvNl = (v: boolean) => {
    setKvNlState(v);
    try { localStorage.setItem('sim.kvNoload', v ? '1' : '0'); } catch { /* quota */ }
  };

  // ── THE MECHANICAL HALF OF THE LOSS PICTURE ──────────────────────────────
  // The transient solve knows nothing about bearings — they are not in the
  // field — so these come from GET /api/bearings/losses, an ANALYTIC model over
  // the machine's own bearing cards (SKF frictional moment + windage). Fetched
  // whenever the summary's speed changes.  Since 2026-09-09 they ARE in the
  // card's one efficiency (user: "there should be one efficiency and different
  // losses — all losses are summed, efficiency is sought at the shaft"): the η tile is the shaft's, the
  // electromagnetic-only figure lives in its tooltip, and the losses stay as
  // separate cells (EM / bearings / windage / all).  The stored `efficiency`
  // (electromagnetic) is untouched — the optimizer's metric and the Compare
  // row still read it.
  //
  // Why it belongs on THIS card: the user asked for ONE place showing the full
  // loss picture. On the measured 150 mm free run the bearings were 43-170 W
  // against 83-319 W total — leaving them off this card made every efficiency
  // on it optimistic by an amount nobody could see.
  const [mech, setMech] = React.useState<BearingLosses | null>(null);
  const sumRpm = summary?.rpm ?? 0;
  const sumMass = React.useMemo(() => {
    const rows = summary?.mass_components ?? [];
    let t = 0;
    for (const c of rows) {
      if (!/^(Rotor back-iron|Magnets|Shaft|Sleeve)/.test(c.name ?? '')) continue;
      const v = (c.mass_kg || (c as { mass_modelled_kg?: number }).mass_modelled_kg);
      if (Number.isFinite(v as number)) t += Number(v);
    }
    return t > 0 ? t : null;
  }, [summary?.mass_components]);
  const runCarriesMech = summary?.P_mech_extra_W != null;
  React.useEffect(() => {
    // A run that carries its OWN mechanical block needs no fetch: those watts
    // describe THE RUN, at the bearing temperature it was billed at, and a
    // second answer computed from today's assignment would contradict the
    // efficiency stored beside them.  The fetch stays for runs solved before
    // 2026-09-08 — see the cells' tooltip, which says which one is on screen.
    if (!(sumRpm > 0) || runCarriesMech) { setMech(null); return; }
    const ac = new AbortController();
    void fetchBearingLosses({ rpm: sumRpm, rotorMassKg: sumMass,
                              signal: ac.signal })
      .then(r => { if (!ac.signal.aborted) setMech(r); });
    // A machine change must re-ask: another motor has other bearings.
    const on = () => {
      void fetchBearingLosses({ rpm: sumRpm, rotorMassKg: sumMass })
        .then(setMech);
    };
    window.addEventListener('family-changed', on);
    window.addEventListener('family-context-changed', on);
    return () => {
      ac.abort();
      window.removeEventListener('family-changed', on);
      window.removeEventListener('family-context-changed', on);
    };
  }, [sumRpm, sumMass, runCarriesMech]);

  const [apply3d, setApply3dState] = React.useState<boolean>(() => {
    // ON unless the user switched it off (user 2026-09-03): the machine's
    // stored Stage-A coefficient is the default view; a fresh profile shows
    // corrected numbers, not raw 2-D ones.
    try { return localStorage.getItem('sim.apply3d') !== '0'; } catch { return true; }
  });
  const setApply3d = (v: boolean) => {
    setApply3dState(v);
    try { localStorage.setItem('sim.apply3d', v ? '1' : '0'); } catch { /* quota */ }
  };

  if (!summary) {
    // Keep the header (and the disabled "+ Compare") in place even with nothing to
    // show: the button vanishing entirely before the first run made it impossible
    // to find. Its tooltip explains what is missing.
    return (
      <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
        display: 'flex', flexDirection: 'column', gap: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
          <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
            {tx('simulationSummaryRealFemResults')}
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, ml: 'auto' }}>
            <AddToCompareButton />
          </Box>
        </Box>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
          {loading
            ? 'Computing transient FEM — summary will appear when the run completes…'
            : 'No transient data yet — press “Run Simulation” in the left panel.'}
        </Typography>
      </Paper>
    );
  }

  // The view summary: with the toggle ON and a passport present, every
  // flux-proportional number is rescaled by k_flux.  Losses are kept 2D
  // (conservative: iron loss would really drop ~k², so the true efficiency
  // sits at or above the shown one); ratios (ripple, koefs, saturation droop)
  // are k-invariant and stay untouched.  KV = rpm/V rises as V falls.
  // (plain computation, NOT a hook — this sits below the empty-state early
  // return, where a hook would violate the rules-of-hooks)
  const k3d = summary.end3d?.k_flux ?? null;
  // THE TORQUE FACTOR (owner 2026-09-30, «если были старые расчёты 3D —
  // применяй пока их»): torque, rotor power, the densities, Kt and Km carry
  // the MEASURED k_T when this machine has one, else the flux factor k_flux;
  // voltages, KV and ψ_PM always carry k_flux.  One factor for all torque
  // numbers, so torque and Kt stay consistent.
  const kTraw = summary.end3d?.k_T;
  const kTmeas = kTraw != null && kTraw > 0.5 && kTraw <= 1.2 ? kTraw : null;
  const kTq = kTmeas ?? k3d;
  const ktBasis = kTmeas != null ? '3-D' : '3-D flux';
  const s = (() => {
    if (!apply3d || k3d == null) return summary;
    const k = k3d;
    const kq = kTq ?? k;
    const Pm = summary.P_mech_W * kq;
    return {
      ...summary,
      T_em_avg_Nm: summary.T_em_avg_Nm * kq,
      P_mech_W: Pm,
      torque_per_mass_Nm_kg: summary.torque_per_mass_Nm_kg * kq,
      power_per_mass_W_kg: summary.power_per_mass_W_kg * kq,
      // the same torque factor on the per-mass figures over the FULL mass (the server's divisor, not ours)
      torque_per_mass_full_Nm_kg: summary.torque_per_mass_full_Nm_kg != null
        ? summary.torque_per_mass_full_Nm_kg * kq : summary.torque_per_mass_full_Nm_kg,
      power_per_mass_full_W_kg: summary.power_per_mass_full_W_kg != null
        ? summary.power_per_mass_full_W_kg * kq : summary.power_per_mass_full_W_kg,
      V_phase_peak_V: summary.V_phase_peak_V * k,
      V_phase_rms_V: summary.V_phase_rms_V * k,
      V_line_peak_V: summary.V_line_peak_V * k,
      V_line_rms_V: summary.V_line_rms_V * k,
      // The delta tiles read the SOLVER's line values — flux-proportional
      // like every voltage above, and the catalog row already stores them
      // ×k (user 2026-09-13: card 722 V beside a saved 702 V).
      V_line_peak_solved_V: summary.V_line_peak_solved_V != null
        ? summary.V_line_peak_solved_V * k : summary.V_line_peak_solved_V,
      V_line_rms_solved_V: summary.V_line_rms_solved_V != null
        ? summary.V_line_rms_solved_V * k : summary.V_line_rms_solved_V,
      V1_LL_V: summary.V1_LL_V != null ? summary.V1_LL_V * k : summary.V1_LL_V,
      KV_rpm_per_V_line: summary.KV_rpm_per_V_line / k,
      KV_noload_rpm_per_V_line: summary.KV_noload_rpm_per_V_line != null
        ? summary.KV_noload_rpm_per_V_line / k : summary.KV_noload_rpm_per_V_line,
      psi_pm_Wb: summary.psi_pm_Wb != null ? summary.psi_pm_Wb * k : summary.psi_pm_Wb,
      // Km and Km/mass are NOT flux-scaled (owner 2026-09-30): they carry
      // the torque factor, like the torque (see ktBasis)
      Km_Nm_sqrtW: summary.Km_Nm_sqrtW != null
        ? summary.Km_Nm_sqrtW * kq : summary.Km_Nm_sqrtW,
      Km_per_mass_Nm_sqrtW_kg: summary.Km_per_mass_Nm_sqrtW_kg != null
        ? summary.Km_per_mass_Nm_sqrtW_kg * kq : summary.Km_per_mass_Nm_sqrtW_kg,
      Km_per_mass_full_Nm_sqrtW_kg: summary.Km_per_mass_full_Nm_sqrtW_kg != null
        ? summary.Km_per_mass_full_Nm_sqrtW_kg * kq : summary.Km_per_mass_full_Nm_sqrtW_kg,
      efficiency: (Pm > 0 && summary.P_loss_total_W > 0)
        ? Pm / (Pm + summary.P_loss_total_W) : summary.efficiency,
      // The charging card is an energy balance on the shaft power, so it moves
      // with k too: P_charge = k·P_mech_in − losses (losses kept 2D, same rule
      // as the efficiency tile), then the bus re-rooted on the pack
      // (V·(V−V_oc) = P·R) and the current/C-rate/headroom re-derived.  The
      // circuit-side numbers (switch states, i_dc) are the 2-D circuit's own
      // and stay — the balance gap is restated against the corrected headline.
      battery_charge: summary.battery_charge ? (() => {
        const b = summary.battery_charge;
        const Pin = b.P_mech_in_W * kq;
        const Pch = Pin - b.P_loss_machine_W;
        const R = b.R_pack_ohm > 0 ? b.R_pack_ohm : 0;
        const Voc = b.V_oc_V > 0 ? b.V_oc_V : 0;
        // Bus moved from the SOLVE's own fixed point by R·ΔI (the solver may
        // have stopped within its coupling tolerance — keep its convention
        // rather than re-rooting from V_oc and showing a jump that is not 3D).
        let V = b.V_bus_V > 0 ? b.V_bus_V : Voc;
        let I = V > 0 ? Pch / V : b.I_charge_A;
        for (let n = 0; n < 6 && V > 0; n++) {
          V = b.V_bus_V + R * (I - b.I_charge_A);
          I = Pch / V;
        }
        const circ = b.P_charge_circuit_W;
        const gap = circ != null ? circ - Pch : b.balance_gap_W;
        return {
          ...b,
          P_mech_in_W: Pin, P_charge_W: Pch, V_bus_V: V,
          V_bus_rise_V: Voc > 0 ? V - Voc : b.V_bus_rise_V,
          I_charge_A: I,
          C_rate: b.capacity_ah != null && b.capacity_ah > 0 ? Math.abs(I) / b.capacity_ah : b.C_rate,
          i_charge_headroom_A: b.i_charge_max_A != null ? b.i_charge_max_A - I : b.i_charge_headroom_A,
          over_i_charge_max: b.i_charge_max_A != null ? I > b.i_charge_max_A : b.over_i_charge_max,
          eta_charge: Pin > 0 ? Pch / Pin : b.eta_charge,
          P_pack_r_loss_W: I * I * R,
          charging: Pch > 0,
          balance_gap_W: gap,
          balance_gap_pct: (gap != null && circ != null)
            ? 100 * gap / Math.max(Math.abs(circ), Math.abs(Pch), 1e-9) : b.balance_gap_pct,
        };
      })() : summary.battery_charge,
    } as TransientSummary;
  })();
  const fmt = (n: number, d = 2) => Number(n).toFixed(d);

  // Copper ρ(T) ratio for the R@25°C view (0.393 %/°C, referenced to 20 °C).
  const solveT = s.coil_temp_C ?? null;
  const rf = (r25 && solveT != null)
    ? (1 + 0.00393 * (25 - 20)) / (1 + 0.00393 * (solveT - 20))
    : 1;

  // ── The summary AS DISPLAYED, for Compare snapshots ──────────────────────
  // (User 2026-08-25: a Compare record must carry the SAME values the card
  // shows, pressed buttons included.)  `s` already carries the 3D toggle;
  // fold in the R@25 rescale, the KV choice and the bench-Ld fallback the
  // cells apply, stamp WHICH buttons were pressed, and publish.  A plain
  // localStorage write keyed to render values — deterministic, no effect
  // ordering games around the early return above.
  try {
    const view: any = {
      ...s,
      R_phase_ohm: s.R_phase_ohm != null ? s.R_phase_ohm * rf : s.R_phase_ohm,
      R_line_line_ohm: s.R_line_line_ohm != null
        ? s.R_line_line_ohm * rf : s.R_line_line_ohm,
      Ld_mH: s.Ld_mH ?? s.bench_ldq?.Ld_mH ?? null,
      saliency_Lq_over_Ld: s.saliency_Lq_over_Ld
        ?? s.bench_ldq?.Lq_over_Ld ?? null,
      KV_rpm_per_V_line: (kvNl && s.KV_noload_rpm_per_V_line != null)
        ? s.KV_noload_rpm_per_V_line : s.KV_rpm_per_V_line,
      view_flags: {
        end3d_k: apply3d && k3d != null ? k3d : null,
        r_temp_C: r25 && solveT != null ? 25 : solveT,
        kv: (kvNl && s.KV_noload_rpm_per_V_line != null) ? 'no-load' : 'loaded',
        ld_source: s.Ld_mH != null ? 'incremental'
          : (s.bench_ldq ? 'bench' : 'none'),
      },
    };
    localStorage.setItem('sim.viewSummary', JSON.stringify(view));
  } catch { /* quota — Compare falls back to the raw summary */ }


  // ── the mechanical loss, as this card shows it ───────────────────────────
  // THE RUN FIRST (2026-09-08).  Every stored run of a machine with bearings
  // now carries its own mechanical block — the same analytic physics, computed
  // where the run is stored, so this card, the datasheet, the report and the
  // coupled loop quote ONE number instead of four callers each doing the
  // arithmetic.  `mech` (the /api/bearings/losses fetch) is the fallback for
  // runs solved before that date, and the tooltip says which is on screen.
  //
  // Losses stay 2-D under the 3D toggle (the same rule the loss tiles follow),
  // so these read untouched either way.
  const runMech = s.mech_losses;
  const fromRun = s.P_mech_extra_W != null;
  const pBrg = fromRun ? Number(s.P_bearings_W ?? 0)
             : (mech?.has_bearings ? Number(mech?.P_bearings_W ?? 0) : null);
  const pWind = fromRun ? s.P_windage_W : mech?.P_windage_W;
  const pExtra = fromRun ? Number(s.P_mech_extra_W ?? 0)
               : (mech?.has_bearings ? Number(mech?.P_mech_extra_W ?? 0) : null);
  const brgTempC = fromRun ? s.bearing_temp_c : mech?.temp_c;
  const brgWind = fromRun ? runMech?.windage : mech?.windage;
  const brgNames = fromRun ? (runMech?.cards ?? [])
                           : (mech?.bearings ?? []).map(b => b.bearing);
  const brgLabel = brgNames.length === 2 && brgNames[0] === brgNames[1]
    ? `2 × ${brgNames[0]}` : brgNames.join(' / ');
  // ONE energy balance, at the SHAFT (lib/shaftBalance.ts = report.shaft_view), built inside the
  // tile panel from this block: POWER = rotor T·ω ∓ (bearings + windage), TOTAL LOSS = every loss,
  // EFFICIENCY = the shaft's.  `extra_W: null` = the machine has no bearings, so the friction is
  // UNKNOWN (not zero) and the tiles say so.  The block is analytic (SKF + windage), never FEM.
  const mechView: EmMech = {
    bearings_W: pBrg, windage_W: pWind ?? null, extra_W: pExtra,
    cards: brgLabel || null, temp_C: brgTempC ?? null,
    gap_W: brgWind?.P_gap_W ?? null, faces_W: brgWind?.P_faces_W ?? null,
  };

  // Stale-operating-point guard: these numbers were computed at the summary's own
  // current / γ.  If the inputs have since changed (the optimizer already uses the
  // new point), the Sim shows an OLD point — flag it so it isn't compared 1:1.
  const liveI = liveOp?.current, liveG = liveOp?.gamma;
  // The panel holds the TERMINAL current, so that is what the comparison must
  // use; a result written between 2026-09-12 08:58 and 09:41 carries the
  // WINDING current in I_phase_rms_A (delta: setpoint/√3) and the setpoint in
  // I_terminal_rms_A — prefer the latter so such a result is not flagged
  // stale for ever.
  const dI = Number.isFinite(liveI as number)
    ? Math.abs((liveI as number) - (s.I_terminal_rms_A ?? s.I_phase_rms_A)) : 0;
  const dG = Number.isFinite(liveG as number) ? Math.abs((liveG as number) - s.gamma_deg)     : 0;
  // The winding is the third half of the operating point.  Switching 4S → 4P
  // divides the coil current by four: torque, V_peak and R_phase all move, and
  // until the run is repeated the card shows the OTHER winding's numbers with
  // nothing saying so — which reads as "I changed the connection and nothing
  // happened".  Only compare when BOTH labels are known (a run saved before the
  // stamp existed cannot raise this flag).
  const liveC = String(liveOp?.connection || '');
  const ranC  = String(s.connection || '');
  const connStale = !!liveC && !!ranC && liveC !== ranC;
  // SPEED is the third coordinate of the point (2026-09-13): the card of the
  // 22 900 rpm peak sat unflagged under a panel set to 20 900, and the user
  // read its 538 kW / 784 V as "rated changed by itself".
  const liveN = liveOp?.rpm;
  const dN = (Number.isFinite(liveN as number) && Number.isFinite(Number(s.rpm)))
    ? Math.abs((liveN as number) - Number(s.rpm)) : 0;
  const opStale = dI > 0.05 || dG > 0.05 || dN > 0.5 || connStale;
  // …and the OTHER way a card goes stale, which used to slip through entirely:
  // the machine changed under it.  Loading another preset (or reloading with a
  // persisted `sim.lastSummary` from the previous session) leaves I and γ
  // untouched, so the operating-point check above says "current" about torque
  // and mass measured on a motor that is no longer loaded — precisely the
  // "0.02 N·m from a previous machine" reading this banner exists to stop.
  // Two independent witnesses: this client's stamp, and the backend's own
  // fingerprint verdict on a restored run.  Unknown (unstamped legacy run) is
  // NOT treated as fresh — it simply cannot raise this flag on its own.
  const geoStale = (!!s._geoSig && !!liveSig && s._geoSig !== liveSig)
                   || s._geoStaleBackend === true;
  // MATERIALS staleness (user 2026-08-25: a run with the old steel showed as
  // current after the assignment changed — "the numbers match the hardware
  // that was there before").  The run's grades are in its own mass rows
  // ("Stator core (B15AHV950M)"); the live ones are what the next run will
  // use (currentMatJson — the same payload the solve carries).
  const matDiffs: string[] = (() => {
    try {
      const j = currentMatJson();
      if (!j) return [];
      const live = (JSON.parse(j).assignment || {}) as Record<string, string>;
      const gradeOf = (prefix: string) => {
        const mc = (s.mass_components || []).find(m => m.name?.startsWith(prefix));
        const mm = mc?.name?.match(/\(([^)]+)\)/);
        return mm ? mm[1] : null;
      };
      const out: string[] = [];
      // The MAGNET is compared by GRADE, not by temperature record: the
      // coupled loop picks the grade's library record for the temperature it
      // solved (140 °C → N52UH_150C) while the machine is assigned N52UH_20C,
      // and that is the same magnet, not a changed one.  Comparing the record
      // names dimmed every coupled result until the page was reloaded (user
      // 2026-09-09: "after every computation I need to reload the page").
      const grade = (name: string) => name.replace(/_\d+C$/i, '');
      for (const [prefix, key] of [['Stator core', 'stator_core'],
                                   ['Rotor back-iron', 'rotor_core'],
                                   ['Magnets', 'magnet'],
                                   // The shaft carries its grade in the mass row
                                   // the same way; the copper row does not (it
                                   // is labelled "(Cu)" whatever conductor is
                                   // assigned), and the two insulators have no
                                   // mass row at all — those three are covered
                                   // by the _matSig witness below instead.
                                   ['Shaft', 'shaft']] as const) {
        const ran = gradeOf(prefix);
        if (!ran || !live[key]) continue;
        const same = key === 'magnet' ? grade(ran) === grade(live[key]) : ran === live[key];
        if (!same) out.push(`${ran} → ${live[key]}`);
      }
      // The WHOLE assignment, named parts and unnamed alike: the stamp the run
      // was made with vs the one the next run would carry.  This is what makes a
      // change to ANY part — including one the active duty made for itself
      // (lib/dutySettings.ts) — dim a result solved with something else.
      // Unknown on either side (a legacy or restored run) raises nothing.
      const normSig = (sig: string) => sig.replace(/(magnet=[A-Za-z0-9]+?)_\d+C(?=\||$)/i, '$1');
      const ranSig = s._matSig ? normSig(s._matSig) : s._matSig;
      const nowSig = normSig(assignmentSignature(j));
      if (!out.length && ranSig && nowSig && ranSig !== nowSig) {
        out.push('assignment changed');
      }
      return out;
    } catch { return []; }
  })();
  const matStale = matDiffs.length > 0;
  const stale = opStale || geoStale || matStale;
  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1 }}>
      {/* The shouting red STALE banner is gone (user: "drop it").  Staleness
          still shows two quieter ways that survive: the whole card dims to
          55 % opacity, and the ⚠ chip in the header line carries the why in
          its tooltip.  Do not resurrect the banner. */}
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2,
        flexWrap: 'wrap', opacity: stale ? 0.55 : 1 }}>
        <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
          {tx('simulationSummaryRealFemResults')}
        </Typography>
        {/* 3D-corrections toggle (user's design): ON → flux-proportional tiles
            rescaled by this machine's Stage-A k_flux; OFF → pure 2D; without a
            passport the switch is disabled and says so honestly.  DISPLAY-level
            only — Compare snapshots and saved duties stay pure 2D. */}
        <Tooltip title={k3d != null
          ? ((summary.end3d?.inherited
                ? `⚠ ${summary.end3d.inherited_note ?? 'inherited from an earlier geometry of this machine — recompute Stage A'}. `
                : '')
             + (apply3d
              ? `3D end-effect ×${k3d.toFixed(3)} APPLIED to torque, power, voltages, KV, ψ_PM, Km, efficiency and the charging card (P_charge = k·P_mech − losses, bus re-rooted on the pack; losses kept 2D — conservative). Compare/saves stay pure 2D. Click to show raw 2D.`
              : `Apply this machine's 3D end-effect (×${k3d.toFixed(3)}) to the flux-proportional tiles. Click to enable.`))
          : '3D correction: not measured for this machine — run Stage A first; toggling changes nothing.'}>
          <span>
            {/* INHERITED (user 2026-09-03): a geometry edit no longer drops the
                coefficient — the last one measured on this machine stays,
                amber, until Stage A is re-run. */}
            <Button size="small" variant={apply3d && k3d != null ? 'contained' : 'outlined'}
              disabled={k3d == null}
              color={summary.end3d?.inherited ? 'warning' : 'primary'}
              onClick={() => setApply3d(!apply3d)}
              sx={{ fontSize: 10, py: 0, px: 1, minWidth: 0, textTransform: 'none' }}>
              {k3d != null ? `3D ×${k3d.toFixed(3)}${summary.end3d?.inherited ? ' ⚠ recompute' : ''}` : '3D: n/a'}
            </Button>
          </span>
        </Tooltip>
        {/* R at 25 °C — bench-check view (user: "so it can be checked").
            Rescales ONLY the two R cells by the copper ρ(T) ratio; losses and
            η stay at the solve temperature — a cold loss set needs a cold
            re-solve, not display math. */}
        <Tooltip title={solveT == null
          ? 'This run predates the coil-temperature stamp — re-run to enable.'
          : (r25
              ? `Showing R at 25 °C for checking against an ohmmeter (solved at ${fmt(solveT, 0)} °C). Losses and η stay at ${fmt(solveT, 0)} °C — for a true cold loss set, set coil temperature to 25 and re-run. Click to show solve-temperature R.`
              : `Show R phase / R line-line converted to 25 °C (copper 0.393 %/°C) to check against a bench ohmmeter. Losses stay at ${fmt(solveT, 0)} °C. Click to enable.`)}>
          <span>
            <Button size="small" variant={r25 && solveT != null ? 'contained' : 'outlined'}
              disabled={solveT == null}
              onClick={() => setR25(!r25)}
              sx={{ fontSize: 10, py: 0, px: 1, minWidth: 0, textTransform: 'none' }}>
              {r25 && solveT != null ? 'R @ 25 °C' : `R @ ${solveT != null ? fmt(solveT, 0) : '—'} °C`}
            </Button>
          </span>
        </Tooltip>
        {/* KV: loaded ↔ no-load.  No-load comes free from the run's cached
            ψ_PM (fundamental back-EMF) — no separate I=0 simulation. */}
        <Tooltip title={s.KV_noload_rpm_per_V_line == null
          ? 'This run predates the no-load-KV stamp — re-run to enable.'
          : (kvNl
              ? 'Showing NO-LOAD KV — rpm per volt of back-EMF (fundamental, from the run\'s cached ψ_PM probe): what spinning the motor on the bench reads. Free of the IR/IL drop and load saturation inside the loaded KV. Click for loaded KV.'
              : 'Showing LOADED KV — rpm per volt of this run\'s terminal voltage (includes IR/IL drop and saturation). Click for the no-load (bench) KV, computed from ψ_PM with no extra solve.')}>
          <span>
            <Button size="small" variant={kvNl && s.KV_noload_rpm_per_V_line != null ? 'contained' : 'outlined'}
              disabled={s.KV_noload_rpm_per_V_line == null}
              onClick={() => setKvNl(!kvNl)}
              sx={{ fontSize: 10, py: 0, px: 1, minWidth: 0, textTransform: 'none' }}>
              {kvNl && s.KV_noload_rpm_per_V_line != null ? 'KV no-load' : 'KV loaded'}
            </Button>
          </span>
        </Tooltip>
        {/* Torque method of THIS run (its own label — an older run keeps its
            hybrid label) and the ripple self-check badge. */}
        {s.torque_method && (
          <Tooltip title={torqueMethodTooltip(s.torque_method)}>
            <Typography sx={{ fontSize: 10, color: 'var(--text-3)', cursor: 'help' }}>
              {torqueMethodShort(s.torque_method)}
            </Typography>
          </Tooltip>
        )}
        {s.mesh_settings_note && (
          <Tooltip title={`${s.mesh_settings_note}. Save the Mesh tab for this machine to set it.`}>
            <Typography sx={{ fontSize: 10, color: '#fbbf24', cursor: 'help' }}>
              Mesh: fallback
            </Typography>
          </Tooltip>
        )}
        {s.gap_refinement?.applied && (
          <Tooltip title={(s.gap_layers_note ?? 'Re-solved with more air-gap layers per side.')
            + (s.gap_refinement?.persist_gap_layers ? " — now this machine's default." : '')}>
            <Typography sx={{ fontSize: 10, color: 'var(--text-3)', cursor: 'help' }}>
              {`Gap ${s.gap_refinement.first?.gap_layers_per_side ?? '?'}→${s.gap_refinement.gap_layers_per_side ?? '?'}/side`}
            </Typography>
          </Tooltip>
        )}
        {s.ripple_mesh_limited && (
          <Tooltip title={'The torque on the rotor-side and on the stator-side air-gap ring '
            + 'differs by '
            + (s.ripple_self_check_rel != null
              ? `${fmt(s.ripple_self_check_rel * 100, 0)} % of the ripple`
              : 'more than 5 % of the ripple')
            + ': the air-gap mesh, not the machine, sets the ripple. '
            + 'Raise Air-gap layers/side on the Mesh tab and re-run.'
            + (s.gap_layers_note ? ` ${s.gap_layers_note}.` : '')}>
            <Chip size="small" color="warning" variant="outlined"
              label={tx('rippleMeshLimited')}
              sx={{ fontSize: 10, height: 18 }}/>
          </Tooltip>
        )}
        {/* Snapshot THIS design as a comparison point (Compare tab). Sits next to
            the numbers it captures, so it is obvious what gets saved. */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, ml: 'auto' }}>
          <AddToCompareButton />
        </Box>
        {/* Operating-point text removed (user: "remove it — it's clear without it",
            the panel inputs above already say it).  The line SURVIVES as the
            carrier of the warnings: stale ⚠, non-converged, snapped steps,
            generator chip — those must never disappear with it. */}
        <Typography sx={{ fontSize: 10, color: stale ? '#f59e0b' : 'var(--text-4)' }}>
          {s.op_mode === 'generator' && (
            <span style={{ color: '#fbbf24', fontWeight: 700 }}> · GENERATOR</span>
          )}
          {/* Computed on ANOTHER BUILD (owner 2026-10-29): a duty always opens
              on the configuration's current build, so its older saved numbers
              say so here — one short chip, what moved in the tooltip, and the
              one click that refreshes them (`sim-rerun`, SimulationPanel). */}
          {geoStale && !matStale && (
            <>
              <Tooltip title={tx('staleBuildTip', { keys: staleBuildKeysText(
                geoSigDiffRows(s._geoSig, geometry as Record<string, unknown>)) || '…' })}
                placement="top">
                <span style={{ marginLeft: 6, cursor: 'help' }}>{tx('staleBuild')}</span>
              </Tooltip>
              <Button size="small" variant="outlined" color="warning"
                onClick={() => { try { window.dispatchEvent(new CustomEvent('sim-rerun')); } catch { /* SSR */ } }}
                sx={{ ml: 0.75, fontSize: 10, py: 0, px: 1, minWidth: 0, lineHeight: 1.6,
                      textTransform: 'none' }}>
                {tx('recompute')}
              </Button>
            </>
          )}
          {stale && !(geoStale && !matStale) && (
            <Tooltip title={(matStale
                ? `Solved with OTHER MATERIALS than now assigned (${matDiffs.join('; ')}). `
                : `Computed at I = ${s.I_phase_rms_A} A, γ = ${s.gamma_deg}°, ${fmt(Number(s.rpm), 0)} rpm — the panel is now set to `
                  + `${Number.isFinite(liveI as number) ? `I = ${fmt(liveI as number, 2)} A` : ''}`
                  + `${dG > 0.05 && Number.isFinite(liveG as number) ? `, γ = ${fmt(liveG as number, 0)}°` : ''}`
                  + `${dN > 0.5 && Number.isFinite(liveN as number) ? `, ${fmt(liveN as number, 0)} rpm` : ''}. `)
              + 'Run Simulation to recompute at the current point.'} placement="top">
              <span style={{ marginLeft: 6, cursor: 'help' }}>
                ⚠{matStale ? ' materials changed' : ''}
              </span>
            </Tooltip>
          )}
          {s.nonlinear_converged === false && (
            <Tooltip title={
              'Nonlinear solve did NOT converge on '
              + `${s.nonlinear_unconverged_frames?.length
                    ? `frame(s) ${s.nonlinear_unconverged_frames.join(', ')}`
                    : 'at least one frame'}`
              + ` — worst residual ${(s.nonlinear_resid_max ?? 0).toExponential(2)}`
              + ` against tol ${(s.nonlinear_tol ?? 0).toExponential(0)}. `
              + 'Every number on this card is an average that includes that frame, '
              + 'so treat it as indicative, not as a result. Torque ripple and the '
              + 'dB/dt losses (core, AC copper) are the most affected.'} placement="top">
              <span style={{ marginLeft: 6, color: '#f87171', cursor: 'help',
                fontWeight: 700 }}>⚠ not converged</span>
            </Tooltip>
          )}
          {s.steps_snapped && (
            <Tooltip title={
              `Requested ${s.n_steps_per_period_requested} steps per electrical period; `
              + `the rotor has to land on whole slip-ring nodes `
              + `(${s.slip_nodes_per_period ?? '?'} per period), so the solver snapped to the `
              + `nearest divisor and ran ${s.n_steps_per_period}. Every number on this card is `
              + 'at the SNAPPED resolution. Pick a divisor of the slip-node count to get '
              + 'exactly what you asked for.'} placement="top">
              <span style={{ marginLeft: 6, color: '#fbbf24', cursor: 'help' }}>
                ⚠ steps {s.n_steps_per_period_requested} → {s.n_steps_per_period}
              </span>
            </Tooltip>
          )}
          {fromSweep && (
            <Tooltip title="These are the applied design's OWN FEM numbers, computed when the point was evaluated — nothing was re-solved here. Press Run for waveforms, field maps, or an independent check." placement="top">
              <span style={{ marginLeft: 6, color: '#fbbf24', cursor: 'help' }}>← applied point</span>
            </Tooltip>
          )}
          {/* "vs applied point" delta chip removed (user request 2026-08-20):
              too small to read in the header line and its η delta was
              routinely nonsense when the applied point carried no results. */}
        </Typography>
      </Box>
      {/* WHICH CURRENT THE TILES BELOW ARE AT (owner 2026-09-21, third round:
          screenshot showing the tiles at the S1 machine while the Operating
          point panel still read the setpoint — *«the currents don't match again»*).
          Only once a real S1 verification pass has REPLACED this record
          (`record_is_s1`): every other run's tiles are simply the setpoint's,
          which is what "no line here" already means on every card today. */}
      {s.coupling && s1ResultsAtLine(s.coupling) && (
        <Typography sx={{ fontSize: 11, color: '#4ade80', opacity: stale ? 0.55 : 1 }}>
          {s1ResultsAtLine(s.coupling)}
        </Typography>
      )}

      {/* ── SEVEN FIXED ROWS (user 2026-09-04: "order the output by rows"):
            1 torque · power · mass · efficiency · ripple  (+ the two densities)
            2 total loss · core · stranded · solid · sleeve · stator/rotor heat
            3 voltages + J coil (unchanged)
            4 phase section · wire coating · lead cable · R phase · R line-line
            5 Ld · Lq · ψ_PM · Lq/Ld
            6 KV · Kt · Km · Km/mass · rotor inertia
            7 demag koef · saturation koef · 3D coef · total koef
          Every row is its own grid: the cells of a row share its width and
          NEVER wrap into the row below, so a row stays a row whatever the
          window width and whichever optional cells this run carries. */}

      {/* ── THE COUPLED RUN, FIRST ────────────────────────────────────
          Directly under the card's heading (user 2026-09-09: "move it
          right after Physics Dashboard").  It is the sentence that
          says at WHICH temperatures everything below was computed, so
          it is read before the numbers it qualifies, not after them.
          Its own row, one cell wide: the value is a sentence, and a
          sentence in a nine-column row is an ellipsis.  The pass-by-pass
          trace stays in the tooltip (UI rule). */}
      {s.coupling && (
        <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
          <Cell label={tx('coupledEmThermal')} value={couplingLine(s.coupling)}
            accent={s.coupling.runaway ? 'red'
                    : s.coupling.converged ? 'green' : 'amber'}
            tooltip={couplingTooltip(s.coupling)}/>
        </Box>
      )}
      {/* …AND THE CURRENT THE DUTY'S TORQUE WAS HELD AT (owner 2026-10-27):
          one line, the basis in the tooltip; nothing on a fixed-current record. */}
      {s.coupling && heldTorqueValues(s.coupling) && (() => {
        const h = heldTorqueValues(s.coupling) as NonNullable<ReturnType<typeof heldTorqueValues>>;
        return (
          <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
            <Cell label={tx('torqueHeld')}
              value={tx(h.held ? 'torqueHeldLine' : 'torqueNotHeldLine', h)}
              accent={h.held ? 'green' : 'amber'}
              tooltip={tx('torqueHeldTip', h)}/>
          </Box>
        );
      })()}
      {/* …AND HOW LONG IT MAY RUN (owner 2026-09-17).  Directly under the
          temperatures it qualifies: a winding past its class is half an
          answer, and this is the other half.  One short line, the model
          behind it in the tooltip (no-walls-of-text rule); nothing at all
          on a point that is inside every limit it states. */}
      {/* …and on a `limits` run (owner 2026-09-18) the same line says what the
          numbers ABOVE it are: the machine at that moment, not a state it
          holds.  `coupledStateLine` picks whichever sentence this record's own
          mode calls for — there is never more than one. */}
      {s.coupling && coupledStateLine(s.coupling) && (
        <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
          <Cell label={s.coupling.mode === 'limited' ? 'At the limit'
                                                     : 'Time to the limit'}
            value={coupledStateLine(s.coupling) as string}
            accent="amber"
            tooltip={coupledStateTip(s.coupling)}/>
        </Box>
      )}
      {/* ── THE CONTINUOUS (S1) RATING (owner 2026-09-21) ───────────────────
          "let's make a button, or better add one more item to the menu" —
          the third `solve_to` option's own line, beside the limit line above
          it: the largest current this machine holds for ever at this duty's
          own saved cooling.  Green when a current came back, amber when the
          search refused (no capacities, a non-monotone map, …) — the same
          "something is always said" rule as the limit line. */}
      {s.coupling?.continuous_rating && (
        <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1, alignItems: 'center' }}>
          <Cell label={tx('continuousRating')}
            value={continuousRatingLine(s.coupling) as string}
            accent={s.coupling.continuous_rating.ok
                    && s.coupling.continuous_rating.verified === true
                    && s.coupling.continuous_rating.feasible !== false
                    && s.coupling.continuous_rating.trustworthy !== false
                    ? 'green' : 'amber'}
            tooltip={continuousRatingTip(s.coupling)}/>
          {/* No rating at the duty's speed → save the speed that holds
              as a duty of its own (owner 2026-10-19), behind a dialog. */}
          <SaveContinuousPointButton coupling={s.coupling}/>
          {/* The manual "Use N A as the operating point" button (owner
              2026-09-21, third round) is GONE (owner, fourth round, looking at
              it: *«can't you just record this current yourself and run the
              solver with it automatically?»*) — a verified S1 run now writes the panel by
              itself (`PhysicsDashboard`'s `s1AutoSetPlan` effect, still the
              SAME `applyS1AsOperatingPoint` setter), with the one visible
              notice + undo that rule requires instead of a click nobody
              noticed at the end of this row. */}
        </Box>
      )}
      {/* ── SINE | INVERTER | Δ (owner 2026-09-25) ─────────────────────────
          On a drive=inverter coupled run: the same point on the ideal sine
          current beside the controller's PWM.  One caption + HelpTip. */}
      {s.coupling?.sine_comparison?.rows?.length ? (
        <Box sx={{ opacity: stale ? 0.55 : 1 }}>
          <SineVsInverterTable sc={s.coupling.sine_comparison}/>
        </Box>
      ) : null}

      {/* ── THE RESULT TILES — the ONE results panel of this tab ─────────────
          Configure's compact tile block, fed from this run (owner 2026-10-12).
          The old card of small cells that used to sit below it is GONE (owner
          2026-10-13: two panels showed the same results one above the other);
          everything it carried lives in these tiles, down to the part-mass row.
          A tile whose value this run lacks is omitted (never an empty tile,
          never a zero).  ONE efficiency, at the shaft: the bearings + windage
          block is passed in so POWER, TOTAL LOSS and EFFICIENCY are the shaft's. */}
      <Box sx={{ opacity: stale ? 0.55 : 1 }}>
        <EmResultTiles summary={s} rf={rf} k3dBasis={apply3d && k3d != null ? ktBasis : null} mech={mechView}/>
      </Box>

      {/* ── CHARGING: generator → battery through the PWM bridge ─────────
           Present only when the run carried a battery AND was driven from an
           imposed voltage — the key simply does not exist otherwise, so every
           current-drive card renders exactly as it always has.
           The headline P_charge is the ENERGY BALANCE (shaft in minus every
           loss the card above reports); the switched number beside it is the
           2-D circuit's own terminal power, which does not carry the iron loss
           or the end-winding copper.  The gap between them is stated, not
           averaged away. */}
      {s.battery_charge && (() => {
        const b = s.battery_charge;
        const ph = b.placeholders || {};
        const phTip = (k: string) => (ph[k] ? ` — ${ph[k]}` : '');
        const chg = b.P_charge_W > 0;
        return (
      <Box sx={{ mt: 0.75, opacity: stale ? 0.55 : 1 }}>
        <Typography sx={{ fontSize: 10, fontWeight: 700, color: 'var(--text-3)',
          mb: 0.4, display: 'flex', alignItems: 'center', gap: 0.75 }}>
          <span>{chg ? 'CHARGING THE PACK' : 'NOT CHARGING'}</span>
          <span style={{ fontWeight: 400, color: 'var(--text-4)' }}>
            {b.chemistry ? `${b.chemistry} · ` : ''}
            {b.cells_series ? `${b.cells_series}S` : ''}
            {b.cells_parallel && b.cells_parallel > 1 ? `${b.cells_parallel}P` : ''}
            {` · ${b.method || ''}`}
          </span>
        </Typography>
        {b.verdict && (
          <Typography sx={{ fontSize: 10, color: chg ? 'var(--text-3)' : '#f59e0b',
            mb: 0.4 }}>{b.verdict}</Typography>
        )}
        <Box sx={{ display: 'grid', gap: 0.75,
          gridTemplateColumns: 'repeat(auto-fit, minmax(118px, 1fr))' }}>
          <Cell label={tx('pCharge')} value={fmt(b.P_charge_W, 1)} unit="W"
            accent={chg ? 'green' : 'red'}
            tooltip={"Watts reaching the pack, from the ENERGY BALANCE: mechanical power in ("
                   + `${fmt(b.P_mech_in_W, 1)} W) minus every loss this card reports (${fmt(b.P_loss_machine_W, 1)} W). `
                   + "This is the headline rather than the DC-link integral because the 2-D circuit does not "
                   + "carry the iron loss or the end-winding copper — both are post-processed and never flow "
                   + "through its terminals. " + (b.bridge_model || '')}/>
          <Cell label={tx('iCharge')} value={fmt(b.I_charge_A, 2)} unit="A DC"
            accent={b.over_i_charge_max ? 'red' : chg ? 'green' : 'default'}
            tooltip={`P_charge / V_bus = ${fmt(b.P_charge_W, 1)} W / ${fmt(b.V_bus_V, 2)} V. `
                   + (b.i_charge_max_A != null
                       ? `Pack limit ${fmt(b.i_charge_max_A, 1)} A${phTip('i_charge_max_a')}; `
                         + `headroom ${fmt(b.i_charge_headroom_A ?? 0, 1)} A. ` : '')
                   + "Negative = this operating point EMPTIES the pack."}/>
          {b.C_rate != null && (
            <Cell label={tx('cRate')} value={fmt(b.C_rate, 2)} unit="C"
              accent={b.C_rate > 2 ? 'amber' : 'default'}
              tooltip={`I_charge over the pack capacity (${fmt(b.capacity_ah ?? 0, 1)} Ah)`
                     + `${phTip('capacity_ah')}.`}/>
          )}
          <Cell label={tx('vBus')} value={fmt(b.V_bus_V, 2)} unit="V"
            tooltip={`Under load. Open circuit ${fmt(b.V_oc_V, 2)} V${phTip('v_oc')}, `
                   + `pack R ${fmt(b.R_pack_ohm * 1000, 1)} mΩ `
                   + `(${fmt(b.r_int_mohm_per_cell ?? 0, 1)} mΩ/cell${phTip('r_int_mohm')}), `
                   + `so charging lifts it by ${fmt(b.V_bus_rise_V, 2)} V. `
                   + "No state-of-charge model: V_oc is the pack nominal, not a point on a charge curve."}/>
          {b.eta_charge != null && (
            <Cell label={tx('charge')} value={fmt(b.eta_charge * 100, 1)} unit="%"
              accent={b.eta_charge > 0.85 ? 'green' : b.eta_charge > 0.6 ? 'amber' : 'red'}
              tooltip={"Shaft in → pack in. P_charge / P_mech_in = "
                     + `${fmt(b.P_charge_W, 1)} / ${fmt(b.P_mech_in_W, 1)} W. `
                     + "The bridge is modelled lossless, so this is the MACHINE's charge efficiency; "
                     + "a real ESC subtracts its own conduction and switching loss on top."}/>
          )}
          {b.I_dc_mean_A != null && (
            <Cell label={tx('iDcCircuit')} value={fmt(-b.I_dc_mean_A, 2)} unit="A"
              tooltip={"DC-link current straight off the modulator's switch states: "
                     + "⟨Σ s_phase·i_phase⟩ integrated exactly across the switching edges inside every solve "
                     + `step. rms ${fmt(b.I_dc_rms_A ?? 0, 2)} A, ripple ${fmt(b.I_dc_ripple_pp_A ?? 0, 2)} A p-p. `
                     + "Sign flipped here so positive means INTO the pack."}/>
          )}
          {b.balance_gap_W != null && (
            <Cell label={tx('balanceGap')} value={fmt(b.balance_gap_W, 1)} unit="W"
              accent={Math.abs(b.balance_gap_pct ?? 0) < 25 ? 'default' : 'amber'}
              tooltip={`Switched (${fmt(b.P_charge_circuit_W ?? 0, 1)} W) minus balance `
                     + `(${fmt(b.P_charge_W, 1)} W) = ${fmt(b.balance_gap_pct ?? 0, 1)} %. `
                     + (b.balance_gap_note || '')
                     + " It is the model's own cross-check: two independent routes to the same watts."}/>
          )}
          {b.P_pack_r_loss_W != null && (
            <Cell label={tx('packIR')} value={fmt(b.P_pack_r_loss_W, 1)} unit="W"
              tooltip={"Burnt in the pack's own internal resistance while charging. NOT a machine loss — "
                     + "it is deliberately outside the motor's efficiency so the two are never conflated."}/>
          )}
          {b.bus_coupling && (
            <Cell label={tx('busCoupling')}
              value={b.bus_coupling.converged ? `${b.bus_coupling.iterations} it` : 'not conv.'}
              accent={b.bus_coupling.converged ? 'green' : 'red'}
              tooltip={"Fixed point V_bus = V_oc + I_charge·R_pack around the whole transient — charging raises "
                     + "the terminal it charges into, which moves the modulation index. "
                     + `${b.bus_coupling.iterations} of at most ${b.bus_coupling.max_iterations} full solves, `
                     + `tolerance ${b.bus_coupling.tol_pct} %. `
                     + b.bus_coupling.trace.map(t => `#${t.iteration}: ${fmt(t.V_bus_V, 3)} V → `
                         + `${fmt(t.I_charge_A, 2)} A → ${fmt(t.V_bus_next_V, 3)} V`).join(' · ')
                     + (b.bus_coupling.converged ? '' : ` — ${b.bus_coupling.note || ''}`)}/>
          )}
          {b.charge_search && (
            <Cell label={tx('maxCharge')}
              value={`${fmt(b.charge_search.best.V1_peak_V, 2)} V`}
              accent="blue"
              tooltip={`Best (V₁, δ) found: ${fmt(b.charge_search.best.V1_peak_V, 3)} V peak at `
                     + `${fmt(b.charge_search.best.delta_deg, 2)}°, from the seed `
                     + `(${fmt(b.charge_search.seed.V1_peak_V, 3)} V, ${fmt(b.charge_search.seed.delta_deg, 2)}°). `
                     + `${b.charge_search.n_coarse_solves} coarse solves at `
                     + `${b.charge_search.coarse_steps_per_period} steps/period, confirmed at `
                     + `${b.charge_search.fine_steps_per_period}`
                     + (b.charge_search.coarse_to_fine_shift_W != null
                         ? ` (coarse→fine moved P_charge by ${fmt(b.charge_search.coarse_to_fine_shift_W, 1)} W)` : '')
                     + `. ${b.charge_search.method}. ${b.charge_search.caveat}.`}/>
          )}
        </Box>
      </Box>
        );
      })()}

    </Paper>
  );
};

export default SummaryTable;
