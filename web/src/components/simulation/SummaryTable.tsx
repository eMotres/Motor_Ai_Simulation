/**
 * SummaryTable — single overview card at the top of the Simulation tab.
 *
 * Pulls all key metrics from the most recent transient run (T, P, masses,
 * loss breakdown, efficiency, KV).  Numbers come straight from
 * /fem_transient → summary, so every value here is a REAL FEM result
 * (not analytical).
 */
import React from 'react';
import { Box, Button, Paper, Typography, Tooltip } from '@mui/material';
import AddToCompareButton from '../compare/AddToCompareButton';
import { geoSignature } from '../common/geoSig';
import { currentMatJson } from '../../lib/apiAuth';
import { assignmentSignature } from '../../lib/dutyMaterials';
import { pickCable } from '../../lib/cableTable';
import { fetchBearingLosses } from '../../lib/machineBearings';
import type { BearingLosses } from '../../lib/machineBearings';
import { useMotorStore } from '../../stores/motorStore';
import { couplingLine, couplingTooltip, coupledStateLine,
         coupledStateTip } from './coupledApi';
import type { CouplingBlock } from './coupledApi';

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
  // R includes the end-winding; Ld/Lq are the INCREMENTAL (frozen-permeability)
  // inductances of THIS operating point — ∂ψ/∂i at the loaded iron state.  The
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
  Ld_chord_mH?:         number | null;
  Lq_chord_mH?:         number | null;
  ldq_method?:          string | null;
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
  P_mech_W:            number;
  V_phase_peak_V:      number;
  V_phase_rms_V:       number;
  V_line_peak_V:       number;
  // Terminal connection of the three phases — decides what "line" means.
  star_delta?:         string;          // "star" | "delta"
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
  eddy_capped?: boolean | null;
  eddy_settle_residual?: number | null;
  eddy_settle_tol?: number | null;
  efficiency:          number;
  // TOTAL = iron + copper + magnets + shaft (the divisor of every density below);
  // ACTIVE = the EM-active mass without the shaft — the basis an ANSYS active-mass
  // expression quotes, so it is the tile a user cross-checks against Ansys.
  mass_total_kg:       number;
  mass_active_kg?:     number;
  mass_area_source?:   string;
  rotor_inertia?: { J_kg_m2: number; J_kg_cm2: number; rotor_iron: number;
                    magnet: number; shaft: number; sleeve?: number;
                    source: string } | null;
  Km_Nm_sqrtW?: number | null;
  Km_per_mass_Nm_sqrtW_kg?: number | null;
  demag?: { bh_kept_vol_pct?: number; bh_loss_pct?: number;
            br_kept_vol_pct: number; loss_pct: number; br_worst_pct: number;
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
            V_line_peak_corrected_V?: number | null } | null;
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

/** "  stator 67.3 W (hyst 33.9 / eddy 17.6 / excess 15.8, k_f 0.92); rotor …"
 *  — the split behind the Core tile, appended to its HelpTip.  Empty string
 *  when the run predates the split, so old stored summaries still read. */
function coreSplit(t?: TransientSummary['P_core_terms']): string {
  const parts = Object.entries(t ?? {}).map(([half, v]) => {
    const tot = v.hysteresis_W + v.eddy_W + v.excess_W;
    return `${half} ${tot.toFixed(1)} W (hyst ${v.hysteresis_W.toFixed(1)}`
         + ` / eddy ${v.eddy_W.toFixed(1)} / excess ${v.excess_W.toFixed(1)},`
         + ` k_f ${v.k_f.toFixed(3)})`;
  });
  return parts.length ? `  Split: ${parts.join('; ')}.` : '';
}

/** True when any half of this run went through the measured P(B, f) surface.
 *  The tile must not describe the model it did not use. */
function usesSurface(t?: TransientSummary['P_core_terms']): boolean {
  return Object.values(t ?? {}).some(v => v.model === 'measured_surface');
}

/** The Core tile's HelpTip — one text per loss model, because the two differ in
 *  what they claim: the surface path IS the manufacturer's measurement inside
 *  the measured (B, f) envelope, the Bertotti path is three coefficients fitted
 *  to it.  Both carry the same stated under-reads (alternating-axis basis,
 *  no manufacturing degradation, no correction coefficient anywhere). */
function coreTooltip(t?: TransientSummary['P_core_terms']): string {
  const head = usesSurface(t)
    ? 'Core loss in stator + rotor steel from the manufacturer\'s MEASURED '
      + 'P(B, f) table, interpolated directly and summed over the harmonics of '
      + 'each element\'s flux locus — no B² law, so the saturation upturn is '
      + 'followed.  Past the table\'s edge it blends into the fitted Bertotti '
      + 'extrapolation; the run log says how much of the answer came from '
      + 'there.  The steel carries B/k_f over k_f of the section (lamination).  '
      + 'In the split below only eddy is separately computed (from the true '
      + 'dB/dt series) — hysteresis/excess are the fit\'s proportion of the '
      + 'measured total.'
    : 'Bertotti loss in stator + rotor steel — kh·f·B² + kc·(f·B)² + '
      + 'ke·(f·B)^1.5, period mean.  Coefficients are fitted to the material\'s '
      + 'measured loss curves at every frequency; the steel carries B/k_f over '
      + 'k_f of the section (lamination).';
  return head + coreSplit(t)
       + '  Alternating-axis basis: a rotating B locus (tooth roots) reads low, '
       + 'and manufacturing degradation (punching, stacking, welding — '
       + 'typically 1.5-2×) is NOT included.  This is the raw calculation, with '
       + 'no correction coefficient.';
}

/** The sentence the Solid (magnets) tile appends when the magnets are SLICED
 *  axially.  Both eddy routes are 2-D (axially infinite magnet), so the slicing
 *  is a correction applied on top — and it is a MODEL, which the text says. */
function magnetSegNote(m?: TransientSummary['magnet_segmentation']): string {
  const f = m?.factor;
  if (f == null || !(m?.slice_mm)) return '';
  if (f >= 0.999) return '';
  return `  Magnets are SLICED axially: ${m.slice_mm} mm slices of a `
       + `${m.stack_mm} mm stack, eddy loop width ${m.width_mm} mm → the 2-D magnet `
       + `eddy loss (which assumes an axially infinite magnet) is multiplied by `
       + `${f.toPrecision(3)}. Russell-Norsworthy resistance-limited end effect, `
       + `normalised to the solid stack so a solid magnet is unchanged. This is a `
       + `MODEL pending 3-D validation and a LOWER bracket — inter-slice coupling `
       + `and the return current's own reaction field are not in it.`;
}

/** One card row: its cells share the row width equally and never wrap into
 *  the next row (grid auto-flow by column), so a row stays the row the user
 *  defined however many optional cells this run carries. */
const ROW = {
  display: 'grid', gridAutoFlow: 'column', gridAutoColumns: 'minmax(0, 1fr)', gap: 1,
} as const;

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
  // card's one efficiency (user: "КПД должен быть один и потери разные — все
  // потери суммируются, ищется КПД на валу"): the η tile is the shaft's, the
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
            Simulation summary — real FEM results
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
  const s = (() => {
    if (!apply3d || k3d == null) return summary;
    const k = k3d;
    const Pm = summary.P_mech_W * k;
    return {
      ...summary,
      T_em_avg_Nm: summary.T_em_avg_Nm * k,
      P_mech_W: Pm,
      torque_per_mass_Nm_kg: summary.torque_per_mass_Nm_kg * k,
      power_per_mass_W_kg: summary.power_per_mass_W_kg * k,
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
      Km_Nm_sqrtW: summary.Km_Nm_sqrtW != null ? summary.Km_Nm_sqrtW * k : summary.Km_Nm_sqrtW,
      Km_per_mass_Nm_sqrtW_kg: summary.Km_per_mass_Nm_sqrtW_kg != null
        ? summary.Km_per_mass_Nm_sqrtW_kg * k : summary.Km_per_mass_Nm_sqrtW_kg,
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
        const Pin = b.P_mech_in_W * k;
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
  const fmtK = (n: number) => n >= 1000 ? `${fmt(n / 1000, 2)}k` : fmt(n, 0);
  // The mechanical cells read an OPTIONAL analytic payload, so their tooltips
  // need a formatter that survives a missing field instead of writing "NaN"
  // into the middle of a sentence.
  const fmtN = (n: number | null | undefined, d = 2) =>
    (n == null || !Number.isFinite(Number(n))) ? '—' : Number(n).toFixed(d);
  // Share of the total loss, for the heat-split cells' unit line ("W · 71 %").
  const pctOfLoss = (n: number, tot: number) =>
    tot > 0 ? `${fmt(100 * n / tot, 0)} %` : '—';

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
  const lossAll = pExtra != null ? s.P_loss_total_W + pExtra : null;
  const brgTempC = fromRun ? s.bearing_temp_c : mech?.temp_c;
  const brgWind = fromRun ? runMech?.windage : mech?.windage;
  const brgMoment = fromRun ? runMech?.M_bearings_Nm : mech?.M_bearings_Nm;
  // ONE energy balance, three tiles (user 2026-09-09: "нужно добавить
  // электрическую мощность рядом с Mech power: механическая + все потери для
  // мотора, механическая − все потери для генератора"; "все потери
  // суммируются, ищется КПД на валу").  The mechanical losses sit BETWEEN the
  // rotor and the coupling, so
  //   shaft power  = rotor T·ω − (bearings + windage)   motoring
  //                = rotor T·ω + (bearings + windage)   generating (what the
  //                                                     coupling must supply)
  //   elec power   = shaft + ALL losses  (motor)  = rotor T·ω + EM losses
  //                = shaft − ALL losses  (generator) = rotor T·ω − EM losses
  //   efficiency   = shaft / elec (motor);  elec / shaft (generator)
  // — exactly `efficiency_shaft` of routes/simulation.py, computed HERE from
  // the numbers on the card so the three tiles can never disagree (under the
  // 3D toggle the rotor power has moved while the stored value has not).  The
  // direction comes from the run's own `op_mode`: generator numbers are quoted
  // POSITIVE on this card, so the sign of P_mech says nothing.  No bearings on
  // the machine = the mechanical term is UNKNOWN, not zero: the tiles then
  // fall back to the rotor power and the electromagnetic η, and say so.
  const pMechAbs = Math.abs(s.P_mech_W || 0);
  const xMech = (pExtra != null && pMechAbs > 0) ? pExtra / pMechAbs : null;
  const genMode = s.op_mode === 'generator';
  const mechKnown = pExtra != null;
  const pShaft = mechKnown
    ? (genMode ? pMechAbs + pExtra : Math.max(0, pMechAbs - pExtra))
    : pMechAbs;
  const pElec = genMode ? Math.max(0, pMechAbs - s.P_loss_total_W)
                        : pMechAbs + s.P_loss_total_W;
  const etaEm = pMechAbs > 0
    ? (genMode ? Math.max(0, pMechAbs - s.P_loss_total_W) / pMechAbs
               : pMechAbs / (pMechAbs + s.P_loss_total_W))
    : s.efficiency;
  const etaShaft = !mechKnown ? null
    : (genMode ? (pShaft > 0 ? pElec / pShaft : 0)
               : (pElec > 0 ? pShaft / pElec : 0));
  const etaOne = etaShaft ?? etaEm;
  const accentOne: 'green' | 'amber' | 'red' = etaOne >= 0.92 ? 'green'
                                             : etaOne >= 0.85 ? 'amber' : 'red';
  const brgNames = fromRun ? (runMech?.cards ?? [])
                           : (mech?.bearings ?? []).map(b => b.bearing);
  const brgLabel = brgNames.length === 2 && brgNames[0] === brgNames[1]
    ? `2 × ${brgNames[0]}` : brgNames.join(' / ');
  const NO_BRG = 'No bearings on this machine, so the mechanical loss is UNKNOWN '
    + '— not zero. Assign them in Mechanical → Shaft & bearings and this card '
    + 'fills in.'
    + (pWind != null ? `  (Windage alone, which needs no bearings, would be `
                       + `${fmtN(pWind, 2)} W at this speed.)` : '');
  // WHERE the bearing temperature came from — M_rr goes as ν^0.6, so a grease
  // quoted at 40 °C running at 90 °C is a factor of two on the rolling term.
  const brgTempFrom = ({ thermal: 'from Thermal', coupled: 'converged by the '
    + 'coupled run', assigned: 'set on this machine',
    default: 'ASSUMED — nothing on this machine says' } as Record<string, string>)
    [s.bearing_temp_source ?? ''] ?? '';
  const MECH_SRC = fromRun
    ? ` SKF analytic${brgTempC != null ? `, bearing at ${fmtN(brgTempC, 0)} °C`
        + `${brgTempFrom ? ` ${brgTempFrom}` : ''}` : ''} — stored WITH this run, `
      + 'so the datasheet, the report and the coupled loop quote the same watts. '
      + 'NOT FEM, and not part of the solved efficiency beside it.'
    : ' Analytic SKF frictional-moment model + windage, ESTIMATED IN THE BROWSER '
      + 'at this card\'s speed and current bearing assignment — this run predates '
      + 'the server-side mechanical block (2026-09-08), so re-run to store it '
      + 'with the result. NOT FEM, and not part of the solved efficiency beside '
      + 'it.';
  // Narrowed local for the demag cell: TS cannot narrow s.demag through the
  // optional chains inside JSX, and the headline falls back to the Br-based
  // loss for pre-energy-coefficient payloads.
  const dm = s.demag ?? null;
  // Headline = the TORQUE criterion (user's call 2026-08-23): loss_pct is the
  // volume-weighted Br deficit, the first-order bound on the torque/EMF drop
  // (T ∝ ∫Br·dA) — verified against an exact two-solve measurement on the
  // 85 mm (bound 0.55 % vs measured 0.10 %, correctly conservative).  The
  // energy/(BH) view and the effective grade stay in the tooltip.
  const dmLoss = dm ? dm.loss_pct : null;
  // Displayed as the KEPT coefficient (user's call: "должен быть 99.74, а не
  // 0.26") — the retention reads naturally as a health figure: 100 % = intact.
  const dmKept = dmLoss != null ? 100 - dmLoss : null;
  const accentRipple: 'green' | 'amber' | 'red' = s.T_ripple_pct <= 5 ? 'green'
                                                : s.T_ripple_pct <= 15 ? 'amber'
                                                : 'red';

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
  // current after the assignment changed — "цифры совпадают с железом,
  // которое было до этого").  The run's grades are in its own mass rows
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
      // 2026-09-09: "после каждого расчёта мне нужно перегружать страницу").
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
  // Terminal connection this run was SOLVED with (the summary stamps it) —
  // the line cells below change meaning with it, not just their factor.
  const isDelta = String(s.star_delta ?? 'star').toLowerCase().startsWith('d');

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1 }}>
      {/* The shouting red STALE banner is gone (user: "выкинь").  Staleness
          still shows two quieter ways that survive: the whole card dims to
          55 % opacity, and the ⚠ chip in the header line carries the why in
          its tooltip.  Do not resurrect the banner. */}
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2,
        flexWrap: 'wrap', opacity: stale ? 0.55 : 1 }}>
        <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
          Simulation summary — real FEM results
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
        {/* R at 25 °C — bench-check view (user: "чтобы можно было проверить").
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
        {/* Snapshot THIS design as a comparison point (Compare tab). Sits next to
            the numbers it captures, so it is obvious what gets saved. */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, ml: 'auto' }}>
          <AddToCompareButton />
        </Box>
        {/* Operating-point text removed (user: "убери — без неё всё понятно",
            the panel inputs above already say it).  The line SURVIVES as the
            carrier of the warnings: stale ⚠, non-converged, snapped steps,
            generator chip — those must never disappear with it. */}
        <Typography sx={{ fontSize: 10, color: stale ? '#f59e0b' : 'var(--text-4)' }}>
          {s.op_mode === 'generator' && (
            <span style={{ color: '#fbbf24', fontWeight: 700 }}> · GENERATOR</span>
          )}
          {stale && (
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

      {/* ── SEVEN FIXED ROWS (user 2026-09-04: "упорядочить вывод по строкам"):
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
          Directly under the card's heading (user 2026-09-09: "перенеси
          это сразу после Physics Dashboard").  It is the sentence that
          says at WHICH temperatures everything below was computed, so
          it is read before the numbers it qualifies, not after them.
          Its own row, one cell wide: the value is a sentence, and a
          sentence in a nine-column row is an ellipsis.  The pass-by-pass
          trace stays in the tooltip (UI rule). */}
      {s.coupling && (
        <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
          <Cell label="Coupled EM ↔ thermal" value={couplingLine(s.coupling)}
            accent={s.coupling.runaway ? 'red'
                    : s.coupling.converged ? 'green' : 'amber'}
            tooltip={couplingTooltip(s.coupling)}/>
        </Box>
      )}
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

      {/* ── Row 1 — torque, power, mass, efficiency, ripple ───────────────── */}
      <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
        <Cell label="Torque T_em" value={fmt(s.T_em_avg_Nm, 2)} unit="N·m"
          accent="blue"
          tooltip="Average electromagnetic torque from Maxwell stress integral over one electrical period"/>
        <Cell label="Mech power" value={`${fmt(pShaft / 1000, 3)}`} unit="kW"
          accent="blue"
          tooltip={mechKnown
            ? `At the COUPLING — ${genMode ? 'what the shaft must supply' : 'what leaves the shaft'}: `
              + `rotor T·ω ${fmt(pMechAbs / 1000, 3)} kW ${genMode ? 'plus' : 'minus'} `
              + `${fmtN(pExtra, 1)} W of bearing friction and windage (${brgLabel || 'the assigned pair'}), `
              + `which sit between the rotor and the coupling.` + MECH_SRC
            : `Rotor power T·ω from the energy balance. No bearings on this machine, so the `
              + `shaft's own friction is UNKNOWN, not zero — assign them in Mechanical → `
              + `Shaft & bearings and this becomes the coupling's number.`}/>
        <Cell label="Elec power" value={`${fmt(pElec / 1000, 3)}`} unit="kW"
          accent="blue"
          tooltip={(genMode
            ? `At the TERMINALS (output) = mechanical power ${fmt(pShaft / 1000, 3)} kW − all losses `
            : `At the TERMINALS (input) = mechanical power ${fmt(pShaft / 1000, 3)} kW + all losses `)
            + `${fmtN(lossAll ?? s.P_loss_total_W, 1)} W (electromagnetic ${fmtN(s.P_loss_total_W, 1)}`
            + (mechKnown ? ` + bearings and windage ${fmtN(pExtra, 1)}` : '') + ` W). `
            + `Equivalently rotor T·ω ${genMode ? '−' : '+'} the electromagnetic losses`
            + (s.P_elec_in_solved_W != null
                ? `; the solved circuit's own terminal power ${fmt(Number(s.P_elec_in_solved_W) / 1000, 3)} kW `
                  + `carries only the losses inside the field solve and is a balance diagnostic, not this number`
                : '') + '.'}/>
        {/* 3 decimals: a 30 mm machine weighs ~0.05 kg, so 2 decimals showed a
            single significant digit and hid every change during optimization.
            Matches the per-component breakdown below, which already uses 3. */}
        {/* ONE mass (user 2026-09-10: "масса у нас только одна") — the total,
            because it is what every N·m/kg and kW/kg in this app divides by.
            The electromagnetic subset moved into the tooltip: it is no longer
            the same quantity Ansys prints under "active mass" either, since
            the band was folded into it. */}
        <Cell label="Mass"
          value={fmt(s.mass_total_kg ?? s.mass_active_kg, 3)} unit="kg"
          tooltip={"IN: stator iron + rotor iron (× lamination k_f) + copper (× k_end) + magnets "
                 + "+ retaining band + shaft. OUT: housing, bearings. CAD sections × stack × the "
                 + "assigned material's density; this is the divisor of every N·m/kg and kW/kg here"
                 + `${s.mass_active_kg ? `. Electromagnetically active part alone: ${fmt(s.mass_active_kg, 3)} kg` : ''}.`}/>
        {/* ONE efficiency, at the shaft (user 2026-09-09).  The electromagnetic
            figure the optimizer and Compare use is in the tooltip, not a
            second tile. */}
        <Cell label="Efficiency η"
          value={fmt(etaOne * 100, 2) + (s.solid_loss_not_solved || !mechKnown ? '*' : '')} unit="%"
          accent={s.solid_loss_not_solved || !mechKnown ? 'amber' : accentOne}
          tooltip={(s.solid_loss_not_solved
            ? '* PARTIAL: magnet/shaft eddy losses are not solvable on an imposed-voltage '
              + 'run yet and are missing from the denominator — this η is optimistic and '
              + 'must not be compared against a current-drive run. '
            : '')
            + (mechKnown
              ? `${genMode ? 'Electrical out / mechanical in, both at the SHAFT' : 'Shaft out / electrical in'}: `
                + `${fmt(pShaft / 1000, 3)} and ${fmt(pElec / 1000, 3)} kW — what a dynamometer reads. `
                + `ALL losses are in it: electromagnetic ${fmtN(s.P_loss_total_W, 1)} W `
                + `+ bearings and windage ${fmtN(pExtra, 1)} W (${brgLabel || 'the assigned pair'}, `
                + `${fmtN((xMech ?? 0) * 100, 2)} % of the rotor power). `
                + `Electromagnetic only it would read ${fmt(etaEm * 100, 2)} % — the figure the `
                + `optimizer's metric and the Compare row use.` + MECH_SRC
              : `* ELECTROMAGNETIC only: rotor T·ω against T·ω ${genMode ? '−' : '+'} every solved loss `
                + `(copper, iron, eddy). ${NO_BRG} The shaft efficiency is LOWER than this by an amount `
                + `nobody can see until they are.`)}/>
        <Cell label="T ripple" value={fmt(s.T_ripple_pct, 1)} unit="%"
          accent={accentRipple}
          tooltip={`Physical torque ripple (T_max − T_min)/|T_avg| over one electrical period, ` +
                   `reconstructed from the 6·k electrical orders a balanced 3-phase machine can produce ` +
                   `(6th/12th ripple + cogging).` +
                   (s.T_ripple_raw_pct != null
                     ? `  Raw FEM pk-pk = ${s.T_ripple_raw_pct.toFixed(1)}% — the difference is sliding-band ` +
                       `stair-step noise (forbidden orders), not real ripple.`
                     : '')}/>
        <Cell label="Torque density" value={fmt(s.torque_per_mass_Nm_kg, 3)} unit="N·m/kg"
          tooltip="T_em / total mass (EM-active + shaft) — figure of merit for motor compactness"/>
        <Cell label="Power density" value={fmt(s.power_per_mass_W_kg / 1000, 3)} unit="kW/kg"
          tooltip="P_mech / total mass (EM-active + shaft)"/>
      </Box>

      {/* ── Row 2 — losses + heat per side ────────────────────────────────── */}
      <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
        <Cell label="EM losses" value={fmtK(s.P_loss_total_W)} unit="W"
          accent="amber"
          tooltip={"The ELECTROMAGNETIC losses — Cu + Fe (Bertotti) + magnet eddy + shaft eddy"
                 + (s.P_sleeve_W != null ? " + sleeve eddy" : "") + " — period means. "
                 + "Bearings and windage are their own cells to the right; All losses is the sum."}/>
        <Cell label="Core (lamination)" value={fmtK(s.P_core_W)} unit="W"
          tooltip={coreTooltip(s.P_core_terms)}/>
        <Cell label="Stranded (copper)" value={fmtK(s.P_stranded_W)} unit="W"
          tooltip={"I²R (DC) + AC eddy/proximity share in the coil windings, incl. "
                 + "end-winding resistance (k_end) and ρ_Cu(T)"
                 + ((s.wire_split ?? 1) > 1
                    ? `  wire_split = ${s.wire_split}: each wire row is ${s.wire_split} `
                      + `strips of wire_width laid side by side, 2×wire_spacing_x apart. `
                      + `They are drawn, meshed and solved as separate conductors, so the `
                      + `AC share is the honest loss of the narrow strips. The strips are `
                      + `wired in SERIES — each one is its own turn, at the full branch `
                      + `current — so the DC share carries ${s.wire_split}× the copper.`
                    : '')
                 + (s.cu_ac_solved_ignores_wire_split
                    ? `  THIS STORED RUN PREDATES THE DRAWN SPLIT: its AC copper was solved `
                      + `on the whole bar while the loss model described strips, so it is an `
                      + `OVER-read (up to wire_split² on the width-direction term). Re-run to `
                      + `replace it.`
                    : '')}/>
        {/* UNSETTLED (2026-09-07): the eddy warm-up ended at its cap with the
            start-up transient still running, so THIS number and the efficiency
            beside it are start-up values, not physics.  One marker + the
            residual in the tooltip — the tile stays one tile. */}
        {/* No ⚠ on the number (user 2026-09-09: "восклицательный знак надо
            убрать"): an unsettled eddy start-up is said in the tooltip and by
            the amber colour, not by a mark inside the value. */}
        <Cell label="Solid (magnets)"
          value={s.solid_loss_not_solved ? '—' : fmtK(s.P_solid_W)} unit="W"
          accent={(s.solid_loss_not_solved || s.eddy_settled === false)
                  ? 'amber' : 'default'}
          tooltip={s.solid_loss_not_solved
            ? 'NOT SOLVED on this run: rotor eddy was disabled (checkbox off, or the '
              + 'SB_VDRIVE_ROTOR_EDDY=0 hatch), so magnet and shaft eddy losses are missing '
              + 'from this card — the efficiency beside it is optimistic by exactly those '
              + 'watts. Voltage and PWM drives DO solve the conducting rotor now; rerun with '
              + 'field losses on for the full picture.'
            : ("Magnet + shaft eddy from the coupled conducting-rotor field solve "
                 + "(with per-magnet ∫J=0 and lamination factor); the d²/12 slab estimate "
                 + "is used only when field losses are off.")
                 + magnetSegNote(s.magnet_segmentation)
                 + (s.eddy_warmup_frames
                    // "over a SETTLED window" is a CLAIM, and until 2026-09-07
                    // it was made unconditionally off the frame count alone —
                    // which says what the warm-up cost, not whether it worked.
                    ? (s.eddy_settled === false
                       ? `  ${s.eddy_warmup_frames} warm-up frame(s) were solved and `
                         + `discarded before the reported window — and were NOT enough:`
                       : `  Cycle mean over a SETTLED window: ${s.eddy_warmup_frames} warm-up `
                         + `frame(s) were solved and discarded first (a probe march at θ<0 on `
                         + `current drive; the settling-prefix periods on voltage/PWM), so no `
                         + `σ·∂A/∂t start-up transient is averaged into it.`)
                      + (s.demag_prepass_frames
                         ? `  ${s.demag_prepass_frames} of them are the demag pre-pass — one `
                           + `further period on the settled field, where the irreversible Br `
                           + `ratchet is allowed to look (it is frozen through the warm-up, `
                           + `whose transient is not a field the machine was ever in).`
                         : '')
                    : '')
                 + (s.eddy_settled === false
                    ? `  ⚠ NOT SETTLED: `
                      + (s.eddy_capped
                         ? `the warm-up march ended at its cap (one electrical period) `
                         : `the handoff was taken `)
                      + `with the σ·∂A/∂t start-up transient still running — `
                      + (s.eddy_settle_residual != null
                         ? `${(s.eddy_settle_residual * 100).toFixed(1)} % of this `
                           + `number was still decaying start-up transient at the handoff `
                           + `(tolerance ${((s.eddy_settle_tol ?? 0.02) * 100).toFixed(1)} %). `
                         : 'the residual could not even be measured (the march was too short). ')
                      + `Magnet/shaft eddy watts and the efficiency are over-read by roughly `
                      + `that much; raise steps/period or re-run without the sweep's warm seed.`
                    : '')}/>
        {s.P_shaft_W != null && (
          <Cell label="Shaft loss" value={fmt(s.P_shaft_W, 1)} unit="W"
            tooltip={"Eddy-current loss in the SHAFT over the cycle (period mean) — the solid conductor inside the rotor yoke that the slot harmonics reach. On a sleeved surface-PM machine it is the rotor's largest single heat source, and it has to leave through the shaft bore or across the gap; the Thermal tab reads it from this run. Part of P_solid_W above."} />
        )}
        {s.P_mag_W != null && (
          <Cell label="Magnet loss" value={fmt(s.P_mag_W, 1)} unit="W"
            tooltip={"Eddy-current loss in the magnets over the cycle (period mean), with the axial segmentation model applied. Heats the magnets directly — the number behind the magnet temperature on the Thermal tab. Part of P_solid_W above."} />
        )}
        {s.P_sleeve_W != null && (
          <Cell label="Sleeve loss" value={fmt(s.P_sleeve_W, 4)} unit="W"
            tooltip={"Eddy loss solved in the carbon-fibre retaining ring, inside the same coupled "
                   + "σ·∂A/∂t system as the magnets. It is milliwatts on purpose: a hoop-wound UD "
                   + "sleeve conducts ~3e4 S/m ALONG the fibres but only ~80 S/m ACROSS them, and "
                   + "the 2-D induced current is AXIAL — transverse to every fibre. It is inside "
                   + "the Solid (magnets) total and the efficiency above."}/>
        )}
        <Cell label="Loss density" value={fmt(s.loss_density_W_kg, 1)} unit="W/kg"
          tooltip="P_loss / mass — thermal stress indicator. Electromagnetic only: the bearings are not in the active mass."/>
      </Box>

      {/* ── Row 2b — heat per side + the mechanical half ─────────────────
          Its own row (user 2026-09-08: "перенеси на другую строку, а то
          намельчил с потерями"): thirteen cells on one grid row squeezed the
          labels to three letters. */}
      <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
        {/* ── HEAT TO REMOVE, per side ─────────────────────────────────
            Losses stay 2-D under the 3D toggle (same rule as the tiles
            above), so these read off `s` unchanged. */}
        {s.P_loss_stator_W != null && (
          <Cell label="Stator heat" value={fmtK(s.P_loss_stator_W)}
            unit={`W · ${pctOfLoss(s.P_loss_stator_W, s.P_loss_total_W)}`}
            tooltip={"Heat the STATOR has to shed: stator iron loss "
              + `(${fmt(s.P_core_stator_W ?? s.P_core_W, 1)} W) + all copper `
              + `(${fmt(s.P_stranded_W, 1)} W — I²R incl. end-winding + AC/proximity). `
              + "It leaves through the housing, the jacket or the fan."
              + (s.P_loss_split_measured === false
                 ? "  NB this run carries no per-half iron breakdown, so the WHOLE "
                   + "core loss is billed here — rerun to get the solved split."
                 : "")}/>
        )}
        {s.P_loss_rotor_W != null && (
          <Cell label="Rotor heat" value={fmtK(s.P_loss_rotor_W)}
            unit={`W · ${pctOfLoss(s.P_loss_rotor_W, s.P_loss_total_W)}`}
            accent={s.P_loss_split_measured === false ? 'amber' : 'default'}
            tooltip={"Heat the ROTOR has to shed: rotor back-iron loss "
              + `(${fmt(s.P_core_rotor_W ?? 0, 1)} W) + magnet, shaft and sleeve `
              + `eddy (${fmt(s.P_solid_W, 1)} W). It can only leave across the air `
              + "gap, through the shaft, or by windage. Stator + rotor = the Total "
              + "loss tile, exactly."
              + (s.P_loss_split_measured === false
                 ? "  NB this run carries no per-half iron breakdown, so the rotor "
                   + "IRON share is missing here and sits on the stator side."
                 : "")}/>
        )}
        {/* ── THE MECHANICAL HALF (analytic, never from the field solve) ──
            Three cells so the card finally shows ONE full loss picture:
            electromagnetic + mechanical. They are '—' when the machine has no
            bearings, because an unknown loss must not be printed as zero. */}
        <Cell label="Bearings" value={pBrg == null ? '—' : fmtK(pBrg)} unit="W"
          accent={pBrg == null ? 'default' : 'amber'}
          tooltip={pBrg == null ? NO_BRG
            : `${brgLabel} by the SKF frictional-moment model: rolling + sliding + `
              + `seal drag at ${fmtN(brgTempC, 0)} °C, carrying the rotor's own `
              + `weight plus any preload. Torque ${fmtN(brgMoment, 4)} N·m `
              + `for the pair.`
              + ` Unbalanced magnetic pull, coupling side loads and any shaft seal `
              + `are NOT modelled.` + MECH_SRC}/>
        <Cell label="Windage"
          value={pWind != null ? fmtN(pWind, 2) : '—'} unit="W"
          tooltip={pWind != null
            ? `Air drag on the rotor: gap Couette friction `
              + `${fmtN(brgWind?.P_gap_W, 2)} W (${brgWind?.gap_regime}) `
              + `plus the two end faces as rotating discs `
              + `${fmtN(brgWind?.P_faces_W, 2)} W (${brgWind?.face_regime}), `
              + `at the MECHANICAL clearance ${fmtN(brgWind?.delta_mm, 2)} mm `
              + `(stator bore − rotor OD including any sleeve).`
              + (fromRun ? `  The gap-shear half is a heat SOURCE in the Thermal `
                           + `tab's map; the end faces are out along the axis and `
                           + `are reported there as not modelled.` : '')
              + MECH_SRC
            : NO_BRG}/>
        <Cell label="All losses"
          value={lossAll == null ? '—' : fmtK(lossAll)} unit="W"
          accent={lossAll == null ? 'default' : 'amber'}
          tooltip={lossAll == null ? NO_BRG
            : `EVERYTHING: the ${fmt(s.P_loss_total_W, 1)} W of solved `
              + `electromagnetic loss in the EM losses cell plus `
              + `${fmtN(pExtra, 1)} W of bearings and windage. This is the loss of `
              + `the assembled machine — the number between Mech power and Elec `
              + `power, and the one the Efficiency η tile is computed from.` + MECH_SRC}/>
      </Box>

      {/* ── Row 3 — voltages + coil current density ───────────────────────── */}
      {/* (isDelta is derived at the top of the component from s.star_delta) */}
      <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
        <Cell label="V_phase peak" value={fmt(s.V_phase_peak_V, 1)} unit="V"
          tooltip={isDelta
            ? "DELTA: the winding's own EMF peak (R·I + dψ/dt), triplen INCLUDED — what you would measure with the delta opened at one corner. It is LARGER than the line peak beside it: the three windings' triplen EMFs are in phase and add round the closed loop, the circulating current they drive drops exactly that part across each winding, and the terminals (whose three line-line voltages sum to zero identically) never see it."
            : "Max |V_A|, |V_B|, |V_C| of the actual waveform (R·I + dψ/dt) — includes harmonics"}/>
        {/* The solver's own per-connection number wins when the run carries
            it: a result restored from before the route learned about delta
            still holds the star mapping in V_line_peak_V. */}
        <Cell label={`V_line peak ${isDelta ? 'Δ' : 'Y'}`}
          value={fmt(s.V_line_peak_solved_V ?? s.V_line_peak_V, 1)} unit="V"
          tooltip={isDelta
            ? "DELTA: the winding IS the line, so this is the phase waveform minus its zero-sequence part (the triplen EMF drives the circulating current round the closed loop and never reaches the terminals). This is what the DC bus / battery must cover — NOT √3×phase and NOT the raw phase peak."
            : "STAR: max of the ACTUAL |V_A−V_B|, |V_B−V_C|, |V_C−V_A| waveforms (triplens cancel line-to-line, so this is LESS than √3×phase peak). This is what the DC bus / battery must cover."}/>
        <Cell label="V_phase RMS" value={fmt(s.V_phase_rms_V, 1)} unit="V"
          tooltip="True RMS of the phase waveform (not peak/√2 — harmonics included)"/>
        <Cell label="V_line RMS"
          value={s.V_line_rms_solved_V != null ? fmt(s.V_line_rms_solved_V, 1)
                 : isDelta ? '—' : fmt(s.V_line_rms_V, 1)} unit="V"
          tooltip={isDelta
            ? (s.V_line_rms_solved_V != null
               ? "True RMS of the winding voltage minus its zero-sequence part — in delta that is the line-to-line RMS"
               : "This result predates the delta mapping and carries only the star line RMS, which is wrong here — press Run.")
            : "True RMS of the line-to-line waveforms (mean of the 3 pairs)"}/>
        {isDelta && s.I_winding_rms_A != null && (
          <Cell label="I_winding RMS Δ" value={fmt(s.I_winding_rms_A, 1)} unit="A"
            accent="amber"
            tooltip={`DELTA: the current in ONE winding = the line setpoint (${fmt(s.I_phase_rms_A, 1)} A on the three leads) ÷ √3. This is what the field was driven with and what J coil is billed on. The inverter never sees it.`}/>
        )}
        <Cell label="J coil" value={s.J_coil_A_per_mm2 != null ? fmt(s.J_coil_A_per_mm2, 1) : '—'} unit="A/mm²"
          accent={s.J_coil_A_per_mm2 == null ? 'default'
                  : s.J_coil_A_per_mm2 <= 20 ? 'green'
                  : s.J_coil_A_per_mm2 <= 40 ? 'amber' : 'red'}
          tooltip="Coil current density = I_phase RMS / (a_parallel × strand copper section, wire_width × wire_height — one STRIP when the wire is split). a_parallel counts the connection's paths TIMES the strands in hand (wire_parallel), because those are the conductors sharing one turn's current. wire_split is NOT in it: a row's strips are consecutive SERIES turns and each carries the branch current whole, so splitting a bar into N narrower strips leaves J where the unsplit row had it. The thermal-loading figure of merit: ~5–15 A/mm² continuous (natural/liquid cooling), 20–40+ for short peak / forced cooling."/>
      </Box>

      {/* ── Row 4 — winding: section, fill, lead, resistance ──────────────── */}
      <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
        {s.A_phase_mm2 != null && (
          <Cell label="Phase section" value={fmt(s.A_phase_mm2, 2)} unit="mm²"
            tooltip={"Copper cross-section the PHASE current flows through: one strand (wire_width × wire_height — one STRIP when the wire is split) × the parallel paths × the strands in hand. A split row's strips are SERIES turns, not extra paths, so they are not in it. I_phase / this area is exactly the J coil cell above — the two cross-check each other. Nearest lead cable: "
              + (pickCable(s.A_phase_mm2)?.awg ?? '—')}/>
        )}
        {s.slot_fill_pct != null && (
          <Cell label="Fill factor" value={fmt(s.slot_fill_pct, 1)} unit="%"
            accent={s.slot_fill_pct <= 60 ? 'green' : s.slot_fill_pct <= 75 ? 'amber' : 'red'}
            tooltip={`Measured conductor area over the winding window the teeth leave — both taken from the CAD polygons the mesher receives, not from a nominal slot rectangle. ${s.A_copper_slotted_mm2?.toFixed(0) ?? '—'} mm² of copper in a ${s.A_slot_mm2?.toFixed(0) ?? '—'} mm² window. Rectangular wire wound by hand reaches ~45–60 %; above ~75 % the winding stops being buildable, and the remainder is insulation, wire spacing and the space the winder needs.`}/>
        )}
        {s.A_phase_mm2 != null && (() => {
          const c = pickCable(s.A_phase_mm2);
          return c ? (
            <Cell label="Lead cable" value={c.awg.replace('awg', ' AWG')} unit={`Ø${c.od_mm.toFixed(1)} mm`}
              tooltip={`Catalogue silicone lead (${c.strands}): ${c.area_mm2} mm² copper — the first size at or above the winding's ${s.A_phase_mm2!.toFixed(2)} mm² (a conductor is never rounded down). Conductor Ø${c.d_mm} mm, insulation O.D. Ø${c.od_mm}±0.1 mm (wall ${c.thk_mm} mm), ${c.r_ohm_km} Ω/km, rated ${c.i_rated_A} A continuous / ${c.i_max_A} A peak, ${c.roll_m} m per roll.`
                + (c.suspect ? ` ⚠ supplier sheet: ${c.suspect}.` : '')}/>
          ) : null;
        })()}
        {/* R with end-winding — shown when the run carries it (old stored
            runs simply lack the cells). */}
        {s.R_phase_ohm != null && (
          <Cell label="R phase" value={fmt(s.R_phase_ohm * 1000 * rf, 2)} unit="mΩ"
            tooltip={(r25 && solveT != null
              ? `At 25 °C (bench-check view; solved at ${fmt(solveT, 0)} °C — copper ρ scaled by 0.393 %/°C). `
              : 'At the coil temperature of this solve. ')
              + 'END-WINDING INCLUDED: R = P_cu/(3·I²) with P_cu = ρ_Cu(T)·J²·V_cu·k_end. The same R the copper-loss cell is billed from.'}/>
        )}
        {s.R_line_line_ohm != null && (
          <Cell label="R line-line" value={fmt(s.R_line_line_ohm * 1000 * rf, 2)} unit="mΩ"
            tooltip={(r25 && solveT != null ? `At 25 °C (bench-check view). ` : '')
              + (isDelta
                 ? '⅔ × R_winding — one winding in parallel with the other two in series. What an ohmmeter across two leads of the delta reads. Star-equivalent per-phase R = R_winding / 3.'
                 : '2 × R_phase — the terminal-to-terminal resistance of the isolated-neutral star this machine is driven as (the voltage circuit is line-to-line for the same reason). What an ohmmeter across two leads reads.')}/>
        )}
        {((s.wire_parallel ?? 1) > 1 || (s.wire_split ?? 1) > 1) && (
          <Cell label="Turns/coil" value={fmt(s.turns_per_coil ?? 0, 0)}
            unit={[(s.wire_parallel ?? 1) > 1 ? `${s.wire_parallel} in hand` : '',
                   (s.wire_split ?? 1) > 1
                     ? `${s.wire_split} strips in series`
                     : ''].filter(Boolean).join(' · ')}
            tooltip={((s.wire_parallel ?? 1) > 1
                      ? `Wound ${s.wire_parallel} wires in hand: the slot still holds all its physical wire rows (same copper, same wire coating, same mass), but ${s.wire_parallel} of them make each turn. Against one wire in hand: ψ, back-EMF, V and Kt divide by ${s.wire_parallel}, KV multiplies by ${s.wire_parallel}, and R_phase, Ld and Lq divide by ${(s.wire_parallel ?? 1) ** 2}. `
                      : '')
                   + ((s.wire_split ?? 1) > 1
                      ? `wire_split = ${s.wire_split}: every wire row is ${s.wire_split} strips of wire_width side by side, wired in SERIES — each strip is its own turn, so the coil has ${s.wire_split}× the turns of the same rows unsplit: ψ, back-EMF and V ×${s.wire_split}, KV ÷${s.wire_split}, R_phase, Ld and Lq ×${(s.wire_split ?? 1) ** 2}, and every strip carries the full branch current. Set the phase current yourself: the same torque comes back at I ÷ ${s.wire_split}. `
                      : '')
                   + `The coil is ${s.turns_per_coil} series turns and the phase current splits over ${s.n_parallel_eff ?? '—'} conductors.`}/>
        )}
      </Box>

      {/* ── Row 5 — dq inductances ────────────────────────────────────────── */}
      {(s.Ld_mH != null || s.Lq_mH != null || s.bench_ldq != null || s.psi_pm_Wb != null) && (
      <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
        {/* Ld / Lq / Lq/Ld — ALWAYS a number when one exists, never advice
            text in the value slot (user's call).  Since 2026-09-20 the value
            is the INCREMENTAL (frozen-permeability) inductance of this point:
            the per-element ν of the converged loaded field held fixed and a
            unit d/q current solved on it. The chord it replaces divided by a
            NO-LOAD ψ_PM and read Ld > Lq on a machine whose Lq is the larger
            (client review of the L180 report). Fallback order is unchanged:
            this run's value, else the bench small-signal probe. */}
        <Cell label="Ld"
          value={s.Ld_mH != null ? fmt(s.Ld_mH, 3)
            : (s.bench_ldq ? fmt(s.bench_ldq.Ld_mH, 3) : '—')} unit="mH"
          accent="blue"
          tooltip={(s.Ld_mH != null
            ? 'd-axis INCREMENTAL inductance at THIS operating point, ∂ψd/∂i_d by frozen permeability: the iron\'s ν is held at the loaded field\'s own state and a unit d-axis current solved on it, so this is flux-per-amp and carries none of the magnet flux the load moved.'
            : 'BENCH value — small-signal Ld at the I≈0 iron state, rotor locked on a magnet: what an LCR meter on the terminals reads. This run predates the incremental measurement; re-run to measure it at the point.')
            + (s.Ldq_inc_mH != null ? ` Cross term ∂ψd/∂i_q = ${fmt(s.Ldq_inc_mH, 3)} mH.` : '')
            + (s.bench_ldq && s.Ld_mH != null ? ` Bench (small-signal): ${fmt(s.bench_ldq.Ld_mH, 3)} mH.` : '')
            + (s.Ld_chord_mH != null ? ` Chord (ψd − ψ_PM)/i_d for comparison: ${fmt(s.Ld_chord_mH, 3)} mH — not an inductance under saturation.` : '')
            + (s.dq_note ? ' — ' + s.dq_note : '')}/>
        <Cell label="Lq" value={s.Lq_mH != null ? fmt(s.Lq_mH, 3) : '—'} unit="mH"
          accent="blue"
          tooltip={'q-axis INCREMENTAL inductance under load, ∂ψq/∂i_q by frozen permeability (same solve as Ld). The q-axis saturates under load, so this sits below the no-load value.'
            + (s.bench_ldq ? ` Bench (small-signal, rotor between magnets): ${fmt(s.bench_ldq.Lq_mH, 3)} mH.` : '')
            + (s.Lq_chord_mH != null ? ` Chord ψq/i_q for comparison: ${fmt(s.Lq_chord_mH, 3)} mH.` : '')
            + (s.dq_note ? ' — ' + s.dq_note : '')}/>
        <Cell label="ψ_PM" value={s.psi_pm_Wb != null ? fmt(s.psi_pm_Wb * 1000, 2) : '—'} unit="mWb"
          tooltip={'Magnet flux linkage (phase, peak) measured at I = 0 — one cheap no-load solve, cached per geometry. The PM term of ψd = ψ_PM + Ld·i_d.'
            + (s.psi_pm_frozen_Wb != null
              ? ` In the LOADED iron the magnets link ${fmt(s.psi_pm_frozen_Wb * 1000, 2)} mWb (${fmt(-(s.psi_pm_sag_pct ?? 0), 1)}%) — measured on the same frozen-permeability solve as Ld/Lq. That difference is what the old chord Ld divided by i_d and called an inductance.`
              : '')}/>
        {s.B_gap_mean_T != null && (
          <Cell label="B gap mean" value={fmt(s.B_gap_mean_T, 3)} unit="T"
            tooltip={'Mean |B| over the AIR-GAP clearance, averaged over the electrical period. Area-weighted over the elements between the outermost rotating metal and the stator bore — a mean of the field, not of the mesh. It is the whole gap under load, magnet flux and armature reaction together, so it is not the no-load fundamental B_g1 a sizing formula asks for.'}/>
        )}
        <Cell label="Lq/Ld"
          value={s.saliency_Lq_over_Ld != null ? fmt(s.saliency_Lq_over_Ld, 2)
            : (s.bench_ldq?.Lq_over_Ld != null ? fmt(s.bench_ldq.Lq_over_Ld, 2) : '—')}
          tooltip={(s.saliency_Lq_over_Ld != null
            ? 'Saliency at this load point, from the two INCREMENTAL inductances (frozen permeability). The dq frame behind these cells is self-checked every run: T = 1.5·p·(ψd·iq − ψq·id) must reproduce the energy-method torque, or the inductances are withheld rather than shown wrong.'
            : 'Saliency from the BENCH (small-signal) frame — both axes measured the LCR way at I≈0, so the ratio is cross-saturation-free.')}/>
      </Box>
      )}

      {/* ── Row 6 — machine constants + rotor mechanics ───────────────────── */}
      <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
        <Cell label={kvNl && s.KV_noload_rpm_per_V_line != null ? 'KV no-load' : 'KV (line)'}
          value={kvNl && s.KV_noload_rpm_per_V_line != null
            ? fmt(s.KV_noload_rpm_per_V_line, 1) : fmt(s.KV_rpm_per_V_line, 1)} unit="rpm/V"
          tooltip={kvNl && s.KV_noload_rpm_per_V_line != null
            ? 'NO-LOAD KV: rpm / (√3·ω_e·ψ_PM) — back-EMF fundamental from the run\'s cached I=0 probe; what spinning the motor on the bench reads. Harmonics excluded (fundamental convention). Loaded KV: '
              + fmt(s.KV_rpm_per_V_line, 1) + ' rpm/V.'
            : 'rpm / V_LINE PEAK — the max/max convention, the same peak shown in the voltage row above (and the one an Ansys induced-voltage table reports). Loaded voltage: at field-weakening γ it differs from the no-load back-EMF KV'
              + (s.KV_noload_rpm_per_V_line != null ? ` (${fmt(s.KV_noload_rpm_per_V_line, 1)} rpm/V — toggle in the header).` : '.')}/>
        {(() => {
          // Kt beside KV — the other controller-facing constant.  Measured at
          // THIS point (T/I_rms), so load saturation is inside it; the k3d
          // toggle scales it with the torque automatically (T is taken off the
          // rescaled copy `s`).  No cell at I=0 — T/0 is not a constant.
          const kt = (s.T_em_avg_Nm != null && s.I_phase_rms_A > 0)
            ? s.T_em_avg_Nm / s.I_phase_rms_A : null;
          return kt != null && Number.isFinite(kt) ? (
            <Cell label="Kt" value={fmt(kt, kt < 0.1 ? 4 : 3)} unit="N·m/A"
              tooltip={(isDelta ? 'DELTA: per LINE amp — the current on the three leads, what the inverter rating is set against. Per WINDING amp it is √3 larger: '
                                  + fmt(kt * Math.sqrt(3), 4) + ' N·m/A. ' : '')
                + 'Torque constant at THIS operating point: T / I_phase_rms = '
                + fmt(s.T_em_avg_Nm, 3) + ' / ' + fmt(s.I_phase_rms_A, 1)
                + '. Measured, so iron saturation is included (the Saturation koef '
                + 'tile says how much) — the low-current bench Kt sits above this. '
                + 'RMS convention: multiply by √2 for N·m per A peak.'}/>
          ) : null;
        })()}
        {s.Km_Nm_sqrtW != null && (
          <Cell label="Km" value={fmt(s.Km_Nm_sqrtW, 3)} unit="N·m/√W"
            tooltip={"Motor constant Km = T_em / √P_cu_DC — torque per square root of the ohmic loss paid for it. "
                   + "Operating-point-independent while the iron is unsaturated (T ∝ I, P_cu ∝ I²): the robotics "
                   + "sizing constant. Uses the DC (I²R) copper loss only — the AC add-on is speed-dependent and "
                   + "would make Km a function of rpm. Computed at the solved coil temperature: R grows ≈39 %/100 °C, "
                   + "so a cold-datasheet Km reads higher than the same machine hot."}/>
        )}
        {s.Km_per_mass_Nm_sqrtW_kg != null && (
          <Cell label="Km / mass" value={fmt(s.Km_per_mass_Nm_sqrtW_kg, 3)} unit="N·m/(√W·kg)"
            tooltip={"Specific motor constant Km / total mass (EM-active + shaft) — the actuator figure of merit "
                   + "that survives scaling: torque density says how much torque per kg, Km/m says how much of it "
                   + "you can HOLD continuously per kg for a given copper heat budget."}/>
        )}
        {s.rotor_inertia?.J_kg_m2 != null && (
          <Cell label="Rotor inertia" value={fmt(s.rotor_inertia.J_kg_cm2, 2)} unit="kg·cm²"
            tooltip={"Rotor moment of inertia J about the shaft axis — ∬r²·dA over the same CAD polygons the mass uses "
                   + "(rotor iron × lamination k_f, magnets and shaft solid) × stack × density. "
                   + `= ${Number(s.rotor_inertia.J_kg_m2).toExponential(3)} kg·m². Breakdown: iron ${fmt(s.rotor_inertia.rotor_iron * 1e4, 2)}, `
                   + `magnets ${fmt(s.rotor_inertia.magnet * 1e4, 2)}, shaft ${fmt(s.rotor_inertia.shaft * 1e4, 2)}`
                   + (s.rotor_inertia.sleeve ? `, sleeve ${fmt(s.rotor_inertia.sleeve * 1e4, 3)}` : '')
                   + ` kg·cm². `
                   + "Drive sizing: t_accel = J·Δω/T; mechanical time constant τ = J·ω/T at the operating point."}/>
        )}
        {/* The analytic sleeve hoop σ (ρω²r², own mass only) left this card on
            2026-09-07 — the Mechanical tab solves the real one, with the magnet
            pressure the ring exists to carry; a lower bound that gates nothing
            beside it only invited comparison with the wrong number. */}
      </Box>

      {/* ── Row 7 — retention coefficients ────────────────────────────────── */}
      {(dmKept != null || s.saturation?.droop_pct != null || s.end3d?.k_flux != null) && (
      <Box sx={{ ...ROW, opacity: stale ? 0.55 : 1 }}>
        {dm != null && dmLoss != null && dmKept != null && (
          <Cell label="Demag koef" value={`≥ ${fmt(dmKept, 2)}`} unit="%"
            accent={dmLoss < 1 ? 'green' : dmLoss < 5 ? 'amber' : 'red'}
            tooltip={`Torque RETAINED after irreversible demag: 100 % = magnets intact (loss ≤ ${fmt(dmLoss, 2)} %). `
                   + "Demagnetisation coefficient — the TORQUE the motor loses to irreversible demag: the "
                   + "volume-weighted Br deficit of all magnets, and T ∝ ∫Br·dA, so this bounds the torque/EMF "
                   + "drop from above (checked against an exact double-solve: the bound is conservative because "
                   + "most de-rated area sits in weakly-coupled leakage corners). "
                   + (dm.bh_loss_pct != null
                       ? `Energy view ((BH)max ∝ Br²): −${fmt(dm.bh_loss_pct, 2)} %`
                         + (dm.energy_lost_J != null
                             ? ` = ${fmt(dm.energy_lost_J, 2)} J of ${fmt(dm.energy_total_J ?? 0, 1)} J`
                             : '')
                         + (dm.grade_effective != null
                             ? `; ${dm.magnet_name ?? 'magnet'} is effectively grade ${fmt(dm.grade_effective, 1)} of nominal ${fmt(dm.grade_nominal ?? 0, 0)}`
                             : '')
                         + '. '
                       : '')
                   + `Worst single element ${fmt(dm.br_worst_pct, 1)} % Br (corner statistic); `
                   + `${fmt(dm.area_derated_pct, 1)} % of magnet area de-rated. `
                   + "Knee from the ASSIGNED grade at its record temperature — pick the _30C/_80C/_120C variant "
                   + "matching the real magnet temperature. Shown only when the run modelled demag."}/>
        )}
        {s.saturation?.droop_pct != null && (
          <Cell label="Saturation koef"
            value={fmt(Math.min(100, 100 - s.saturation.droop_pct), 1)} unit="%"
            accent={s.saturation.droop_pct < 5 ? 'green' : s.saturation.droop_pct < 15 ? 'amber' : 'red'}
            tooltip={`Torque RETAINED against iron SATURATION at this operating point: 100 % = linear iron `
                   + `(loss ${fmt(s.saturation.droop_pct, 1)} %). Same retention convention as Demag koef beside it — `
                   + "that one is the magnets' doing, this one is the iron's, so the two torque-eaters never conflate. "
                   + `Reference: the unsaturated linear torque = ${fmt(s.saturation.T_linear_Nm, 2)} N·m — `
                   + "1.5·p·ψ_PM·i_q (ψ_PM from the measured no-load solve, i_q from this run's dq frame)"
                   + (s.saturation.T_reluctance_Nm != null
                       ? ` PLUS the reluctance term 1.5·p·(Ld−Lq)·i_d·i_q = ${fmt(s.saturation.T_reluctance_Nm, 3)} N·m `
                         + 'off the bench (unsaturated) Ld/Lq, so at γ ≠ 0 on this salient rotor the ratio still measures '
                         + 'IRON SATURATION alone — never above 100 %. '
                       : ' (no bench Ld/Lq for this machine yet, so the reference is PM-only and the value is clamped at 100 %). ')
                   + "Saturation is REVERSIBLE (drop the current, it returns) and grows with current — compare operating "
                   + "points, not machines."}/>
        )}
        {s.end3d?.k_flux != null && (
          <Cell label={s.end3d.inherited ? '3D end-effect ⚠' : '3D end-effect'} value={`×${fmt(s.end3d.k_flux, 3)}`}
            accent={s.end3d.inherited ? 'amber' : s.end3d.k_flux > 0.97 ? 'green' : s.end3d.k_flux > 0.93 ? 'amber' : 'red'}
            tooltip={"Axial end-effect correction from the 3D static Stage A solve of THIS machine (matched by geometry "
                   + "fingerprint — it can never show another motor's number). A 2D solve assumes the field fills the "
                   + "stack uniformly; in reality it spills past the laminations, and the shorter the stack the worse. "
                   + `Multiply 2D flux/EMF/torque by ${fmt(s.end3d.k_flux, 3)}: corrected T ≈ ${fmt(s.end3d.T_corrected_Nm, 2)} N·m`
                   + (s.end3d.V_line_peak_corrected_V != null
                       ? `, V_line peak ≈ ${fmt(s.end3d.V_line_peak_corrected_V, 1)} V` : '')
                   + `. Pure axial spill ×${fmt(s.end3d.k_flux_self, 3)}; the rest is 3D-vs-2D model disagreement. `
                   + `Solve rung: ${s.end3d.fidelity}.`}/>
        )}
        {dmKept != null && s.saturation?.droop_pct != null && (() => {
          // Product of the two retentions: how much of the IDEAL machine —
          // linear iron, fresh magnets — this operating point delivers.
          const total = dmKept / 100 * (100 - s.saturation.droop_pct);
          return (
            <Cell label="Total koef" value={fmt(total, 1)} unit="%"
              accent={total > 95 ? 'green' : total > 85 ? 'amber' : 'red'}
              tooltip={"Overall torque retention vs the IDEAL machine — linear iron AND fresh magnets: "
                     + `Demag koef × Saturation koef = ${fmt(dmKept, 2)} % × ${fmt(100 - s.saturation.droop_pct, 1)} % `
                     + `= ${fmt(total, 1)} %. The single figure for how far this operating point sits from the `
                     + "linear no-loss ideal; the two neighbouring cells say who is to blame — the magnets "
                     + "(irreversible) or the iron (reversible, current-dependent)."}/>
          );
        })()}
      </Box>
      )}

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
          <Cell label="P charge" value={fmt(b.P_charge_W, 1)} unit="W"
            accent={chg ? 'green' : 'red'}
            tooltip={"Watts reaching the pack, from the ENERGY BALANCE: mechanical power in ("
                   + `${fmt(b.P_mech_in_W, 1)} W) minus every loss this card reports (${fmt(b.P_loss_machine_W, 1)} W). `
                   + "This is the headline rather than the DC-link integral because the 2-D circuit does not "
                   + "carry the iron loss or the end-winding copper — both are post-processed and never flow "
                   + "through its terminals. " + (b.bridge_model || '')}/>
          <Cell label="I charge" value={fmt(b.I_charge_A, 2)} unit="A DC"
            accent={b.over_i_charge_max ? 'red' : chg ? 'green' : 'default'}
            tooltip={`P_charge / V_bus = ${fmt(b.P_charge_W, 1)} W / ${fmt(b.V_bus_V, 2)} V. `
                   + (b.i_charge_max_A != null
                       ? `Pack limit ${fmt(b.i_charge_max_A, 1)} A${phTip('i_charge_max_a')}; `
                         + `headroom ${fmt(b.i_charge_headroom_A ?? 0, 1)} A. ` : '')
                   + "Negative = this operating point EMPTIES the pack."}/>
          {b.C_rate != null && (
            <Cell label="C-rate" value={fmt(b.C_rate, 2)} unit="C"
              accent={b.C_rate > 2 ? 'amber' : 'default'}
              tooltip={`I_charge over the pack capacity (${fmt(b.capacity_ah ?? 0, 1)} Ah)`
                     + `${phTip('capacity_ah')}.`}/>
          )}
          <Cell label="V bus" value={fmt(b.V_bus_V, 2)} unit="V"
            tooltip={`Under load. Open circuit ${fmt(b.V_oc_V, 2)} V${phTip('v_oc')}, `
                   + `pack R ${fmt(b.R_pack_ohm * 1000, 1)} mΩ `
                   + `(${fmt(b.r_int_mohm_per_cell ?? 0, 1)} mΩ/cell${phTip('r_int_mohm')}), `
                   + `so charging lifts it by ${fmt(b.V_bus_rise_V, 2)} V. `
                   + "No state-of-charge model: V_oc is the pack nominal, not a point on a charge curve."}/>
          {b.eta_charge != null && (
            <Cell label="η charge" value={fmt(b.eta_charge * 100, 1)} unit="%"
              accent={b.eta_charge > 0.85 ? 'green' : b.eta_charge > 0.6 ? 'amber' : 'red'}
              tooltip={"Shaft in → pack in. P_charge / P_mech_in = "
                     + `${fmt(b.P_charge_W, 1)} / ${fmt(b.P_mech_in_W, 1)} W. `
                     + "The bridge is modelled lossless, so this is the MACHINE's charge efficiency; "
                     + "a real ESC subtracts its own conduction and switching loss on top."}/>
          )}
          {b.I_dc_mean_A != null && (
            <Cell label="I dc (circuit)" value={fmt(-b.I_dc_mean_A, 2)} unit="A"
              tooltip={"DC-link current straight off the modulator's switch states: "
                     + "⟨Σ s_phase·i_phase⟩ integrated exactly across the switching edges inside every solve "
                     + `step. rms ${fmt(b.I_dc_rms_A ?? 0, 2)} A, ripple ${fmt(b.I_dc_ripple_pp_A ?? 0, 2)} A p-p. `
                     + "Sign flipped here so positive means INTO the pack."}/>
          )}
          {b.balance_gap_W != null && (
            <Cell label="Balance gap" value={fmt(b.balance_gap_W, 1)} unit="W"
              accent={Math.abs(b.balance_gap_pct ?? 0) < 25 ? 'default' : 'amber'}
              tooltip={`Switched (${fmt(b.P_charge_circuit_W ?? 0, 1)} W) minus balance `
                     + `(${fmt(b.P_charge_W, 1)} W) = ${fmt(b.balance_gap_pct ?? 0, 1)} %. `
                     + (b.balance_gap_note || '')
                     + " It is the model's own cross-check: two independent routes to the same watts."}/>
          )}
          {b.P_pack_r_loss_W != null && (
            <Cell label="Pack I²R" value={fmt(b.P_pack_r_loss_W, 1)} unit="W"
              tooltip={"Burnt in the pack's own internal resistance while charging. NOT a machine loss — "
                     + "it is deliberately outside the motor's efficiency so the two are never conflated."}/>
          )}
          {b.bus_coupling && (
            <Cell label="Bus coupling"
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
            <Cell label="Max charge"
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

      {/* ── Mass component breakdown ───────────────────────────────────── */}
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 0.5 }}>
        {s.mass_components.map((c, i) => {
          const pct = (c.mass_kg / s.mass_total_kg * 100);
          // A `reference` part is the CUSTOMER's (a frameless motor's shaft):
          // it is in the field and its losses are in P_loss, but it is not in
          // this mass, this inertia or any N·m/kg.  The row stays — dropping it
          // silently would leave the reader to wonder where the shaft went —
          // and says so, with what it was modelled at.
          const isRef = c.state === 'reference';
          return (
            <Box key={i} title={c.note} sx={{
              flex: '1 1 0', minWidth: 0,
              px: 1, py: 0.4,
              bgcolor: 'var(--panel-2)',
              border: `1px solid ${isRef ? 'rgba(56,189,248,0.35)' : 'var(--app-bg)'}`,
              borderRadius: 1 }}>
              <Typography sx={{ fontSize: 9, color: isRef ? '#38bdf8' : 'var(--text-3)',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {c.name}
              </Typography>
              <Typography sx={{ fontSize: 11, fontFamily: 'monospace',
                color: isRef ? 'var(--text-3)' : 'var(--text-1)' }}>
                {isRef
                  ? <>not billed · <b>{fmt(c.mass_modelled_kg ?? 0, 3)} kg</b> modelled</>
                  : <><b>{fmt(c.mass_kg, 3)} kg</b> · {fmt(pct, 1)}%</>}
              </Typography>
            </Box>
          );
        })}
      </Box>
    </Paper>
  );
};

export default SummaryTable;
