/**
 * The Electromagnetic tab's client for the EM ↔ thermal ORCHESTRATOR.
 *
 * The toggle beside Run (`sim.coupled`) switches ONE thing: where the Run
 * request goes.  Off — every run this app has ever made — it goes to the kernel
 * as it always has, and the two temperatures on this tab are exactly what the
 * solve uses.  On, it goes to `POST /api/coupled/run` with the SAME payload, and
 * the backend iterates
 *
 *     EM run → thermal solve → winding / magnet averages → back into the EM run
 *
 * until both settle.  The answer it returns carries the last EM run's own
 * payload (`transient`), so the panel adopts it exactly as it adopts its own
 * Run — same charts, same summary, same localStorage copy.
 *
 * WHY THE COOLING RIDES THE REQUEST.  The backend can load the caller's
 * remembered Thermal-tab fields itself (routes/panel_settings), and it does when
 * this key is absent.  Sending the browser's own `therm.*` block is better when
 * there is one: it is what the Thermal tab is showing RIGHT NOW, including edits
 * the user has not solved with yet, and the promise in the toggle's tooltip is
 * "the Thermal tab's current settings" — not "whatever the server last stored".
 */
import { regimeLine, type RegimeLimits }
  from '../thermal/dutyCycleRegime';
import { DUTY_CYCLE_ENABLED } from '../../lib/dutyCycleFlag';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${API.replace(/\/$/, '')}/api/coupled`;

/** One pass of the loop, as the backend records it. */
export interface CoupledIteration {
  iter: number;
  /** the temperatures THIS pass's electromagnetic run was solved at */
  T_coil_in: number;
  T_magnet_in: number | null;
  /** what the thermal solve came back with — winding / magnet BODY averages */
  T_coil_out: number | null;
  T_magnet_out: number | null;
  /** the hottest magnet element: reported for the demagnetisation check, and
   *  deliberately NOT fed back (a knee check is about the hottest element, a Br
   *  is about the body) */
  T_magnet_max: number | null;
  P_loss_W: number | null;
  T_em_Nm: number | null;
  /** THE MECHANICAL HALF of this pass (2026-09-08): the temperature the bearing
   *  friction was billed at, where that came from, and what it cost.  From pass
   *  2 the temperature is the SHAFT of the previous pass's map, so the friction
   *  in the loop is the friction of a bearing at the temperature the machine
   *  actually reaches.  `null` on a machine that names no bearings — which
   *  means UNKNOWN, never 0 W. */
  bearing_temp_c: number | null;
  bearing_temp_source: string | null;
  P_mech_extra_W: number | null;
}

/** The block the backend writes into the last run's SUMMARY, and returns here. */
export interface CouplingBlock {
  /** the pair the LAST electromagnetic run was solved at — i.e. the machine the
   *  cards on this tab describe */
  coil_temp_c: number;
  magnet_temp_c: number | null;
  magnet_temp_max_c: number | null;
  iterations: number;
  em_runs: number;
  converged: boolean;
  runaway: boolean;
  tol_K: number;
  damping: number;
  max_iter: number;
  /** how far the last thermal answer sat from the temperature it was solved at
   *  — inside `tol_K` when converged, and the honest uncertainty when not */
  residual_coil_K: number | null;
  residual_magnet_K: number | null;
  history: CoupledIteration[];
  /** The LAST run's mechanical numbers, present exactly when that run carried
   *  them.  ABSENT — not zero — on a machine with no bearings: a 0 W bearing
   *  loss on the Electromagnetic card is an efficiency nobody measured. */
  bearing_temp_c?: number;
  bearing_temp_source?: string;
  bearing_temp_note?: string;
  P_bearings_W?: number;
  P_windage_W?: number;
  P_mech_extra_W?: number;
  P_loss_total_incl_mech_W?: number;
  efficiency_shaft?: number;
  /** THE CONTROLLER (Stage 2, 2026-09-22).  Present exactly when this run was
   *  made on `drive: "inverter"` — the machine fed the controller's own
   *  waveform, and the devices solved on the current it produced.  Loosely
   *  typed on purpose, as `mechanical` is: the Controller tab and the report
   *  hold the full answer, this is the line. */
  controller?: {
    device?: string;
    t_j_c?: number;
    losses?: { total_W?: number | null };
    efficiency?: { inverter?: number | null; shaft?: number | null;
                   wall_to_shaft?: number | null };
    thermal?: { t_j_max_c?: number | null; margin_K?: number | null };
    limits_verdict?: string;
    passes?: Array<Record<string, unknown>>;
  } | null;
  /** The mechanical step's verdict (phase 3), or its recorded refusal, or
   *  absent when the caller skipped it.  Loosely typed on purpose: the
   *  Mechanical tab's last result is the full answer, this is the line. */
  mechanical?: {
    ok?: boolean; error?: string; rpm?: number; torque_nm?: number;
    sf_min?: number | null; sf_min_p05?: number | null; note?: string;
    contact_fallback?: { pair?: string; from?: string; to?: string; reason?: string };
    /** The two temperature-free answers the same coupled run leaves
     *  (2026-09-13): the ring modes and the shaft's critical speeds, each
     *  one line's worth — the Mechanical tab holds the full results. */
    modes?: {
      ok?: boolean; error?: string; body?: string; n_modes?: number;
      f1_hz?: number | null; f1_order?: number | null; n_flagged?: number;
      tightest?: { f_hz?: number; order?: number | null; excitation?: string | null;
                   excitation_hz?: number | null; margin_pct?: number; flag?: boolean };
    } | null;
    critical_speeds?: {
      ok?: boolean; error?: string; rated_rpm?: number | null;
      first_forward_rpm?: number | null; first_forward_margin_pct?: number | null;
      n_forward_below_rated?: number; verdict?: string | null;
      bearing_span_mm?: number | null; bearing_k_n_per_m?: number | null;
    } | null;
  } | null;
  note: string | null;
  warning: string | null;
  /** THE REGIME the loop found, on an S2/S3 duty (2026-09-16).  Absent on every
   *  continuous duty — the loop there is what it always was.  Spelled with the
   *  SAME keys the stored duty-cycle record uses, so `dutyCycleRegime`'s own
   *  readers take it as they take the Thermal tab's answer. */
  duty_cycle?: CoupledRegime;
  /** HOW LONG THE POINT MAY BE HELD, when it is past a limit (2026-09-17).
   *  Absent on a machine that states no limit and on a record written before
   *  this existed; present with `within_limits: true` on a point that is inside
   *  every one of them, which is what "nothing to say" looks like here. */
  time_to_limit?: TimeToLimit;
  /** WHICH QUESTION this run was asked (owner 2026-09-18, third option added
   *  2026-09-21) — the selector beside the Coupled thermal switch.  Absent on
   *  every record written before the choice existed, and that is a `steady`
   *  one. */
  solve_to?: 'steady' | 'limits' | 'continuous';
  /** …and WHICH ANSWER came back.  A `limits` (or `continuous`) run of a
   *  machine that is inside every limit — or whose step response never
   *  reaches one — is a `steady` record, because there is no moment to
   *  report. */
  mode?: 'steady' | 'limited';
  limited?: LimitedState;
  /** THE CONTINUOUS (S1) RATING (owner 2026-09-21): the largest current this
   *  machine may hold FOR EVER at THIS duty's own saved cooling, found from
   *  the pass above (the converged steady state, or the machine at the limit)
   *  — `solve_to: 'continuous'` only.  Absent, never null, on every other
   *  answer. */
  continuous_rating?: ContinuousRating;
  /** THE CATALOGUE CONSTANTS (owner 2026-09-18): this machine's KV, Kt, Km and
   *  Km-per-kg with the winding AND the magnets at 20 °C — the datasheet
   *  convention every motor catalogue quotes, so two machines can be compared.
   *  The loop measures them with one extra background pass at the end; absent
   *  when that pass was switched off (`cold_constants: false`) or refused, and
   *  absent is the honest answer — nothing here is extrapolated from the hot
   *  constants.  `POST /api/coupled/constants_20c` fills it in for a duty that
   *  already converged, without re-running the loop. */
  constants_20c?: ColdConstants;
}

/** The 20 °C block.  `two_d` is what the solver produced; `k3d` is the same
 *  quantities under this geometry's 3-D passport, by §4's own conventions —
 *  Kt, Km, Km/mass and Ψ_PM × k_3d, KV ÷ k_3d (rpm per volt goes as 1/flux).
 *  `k3d` is empty and `k_3d` null on a machine with no passport. */
export interface ColdConstants {
  coil_temp_c: number;
  magnet_temp_c: number;
  point?: { rpm?: number; I_phase_rms?: number; gamma_deg?: number;
            drive?: string; star_delta?: 'star' | 'delta' };
  k_3d?: number | null;
  two_d?: Record<string, number | string>;
  k3d?: Record<string, number>;
  /** the four a catalogue prints — corrected where there is a passport */
  kt_line_Nm_per_A?: number | null;
  kv_line_rpm_per_V?: number | null;
  km_Nm_sqrtW?: number | null;
  km_per_mass_Nm_sqrtW_kg?: number | null;
  R_phase_20_ohm?: number | null;
  mass_kg?: number | null;
  computed_at?: string | null;
  note?: string;
}

/** The machine AT the first limit it reaches — the `limits` mode's whole answer.
 *
 *  Owner 2026-09-18: *«состояние мотора в работе 24 секунды при заданной
 *  мощности»* and, the same day, *«при заданной мощности и заданном
 *  охлаждении»*.  Every number in the coupling block beside this one is that
 *  machine: the loop made one more electromagnetic pass at these temperatures
 *  and the thermal map was translated onto them. */
export interface LimitedState {
  /** 'winding' | 'magnet' | 'bearing' — what ends the pull */
  part: string;
  quantity?: string;
  limit_c?: number | null;
  limit_source?: string;
  t_cold_s?: number | null;
  t_cold_words?: string | null;
  t_rated_s?: number | null;
  t_rated_words?: string | null;
  /** the four network NODES at that instant (means, °C) */
  temperatures_at_limit?: Record<string, number>;
  /** …and each judged part's own quantity there — the hot spot, the hottest
   *  element, the seat.  The limiting one is exactly its limit. */
  at_limit_c?: Record<string, number>;
  /** WHAT THE FINAL ELECTROMAGNETIC PASS WAS SOLVED AT (owner 2026-09-18:
   *  *«расчёт должен быть при катушках в 200 градусов, а не 184»*): each part
   *  at the temperature its limit is judged on — the winding hot spot, the
   *  hottest magnet element — and the limiting part exactly AT its limit.
   *  The block's `coil_temp_c` / `magnet_temp_c` ARE these two numbers; the
   *  node means above are what the map was translated onto.  Absent on a
   *  limited record written before that day (solved at the node mean). */
  em_pass_at?: { coil_c?: number | null; magnet_c?: number | null;
                 coil_basis?: string; magnet_basis?: string; rule?: string };
  bearing_seat_at_limit_c?: number | null;
  /** what the pass this was fitted to says each part reaches if held for ever */
  steady_state_would_be?: Record<string, number>;
  /** `false` = the loop was STOPPED at the limit rather than iterated to a
   *  fixed point, so the number above is a reading off the last map */
  steady_state_converged?: boolean;
  steady_state_runaway?: boolean;
  calibration_passes?: number;
  /** the duty's OWN cooling — the boundary this answer is conditional on */
  cooling_words?: string;
  point_error_pct?: number | null;
  drive_held?: 'sine' | 'pwm';
  /** the block's own one sentence, verbatim */
  line?: string;
  note?: string;
}

/** One part's answer inside {@link TimeToLimit}. */
export interface TimeToLimitPart {
  part: string;
  /** "the winding hot spot" / "the hottest magnet element" / "the bearing seat" */
  quantity?: string;
  limit_c?: number;
  at_point_c?: number;
  over_by_K?: number;
  /** `false` = the step response of this network settles BELOW the limit, so no
   *  time may be quoted — the map is over it for a reason four nodes cannot
   *  represent.  The `asymptote_c` beside it says where it settles. */
  reaches?: boolean;
  time_to_limit_s?: number | null;
  asymptote_c?: number | null;
  note?: string;
}

/** The step response of this operating point, from cold and from rated.
 *
 *  The loop answers a question about the STEADY state; when that state is past
 *  a limit the other half of the answer is the TIME, and this is it. */
export interface TimeToLimit {
  within_limits: boolean;
  /** the minimum over the parts, from COLD — `null` when nothing is reached */
  time_to_limit_s?: number | null;
  time_to_limit_from_rated_s?: number | null;
  limiting_part?: string | null;
  limits_c?: Record<string, number>;
  at_point_c?: Record<string, number>;
  over_by_K?: Record<string, number>;
  over_parts?: string[];
  parts?: TimeToLimitPart[];
  starts?: Record<string, { time_to_limit_s?: number | null;
                            limiting_part?: string | null;
                            start_source?: string } >;
  rated_start_note?: string;
  network?: { available?: boolean; note?: string;
              worst_residual_W?: number;
              link_kinds?: Record<string, string> };
  model?: string;
  note?: string;
  calibration_runaway?: boolean;
}

/** ONE part's judged quantity at the continuous rating — the same shape
 *  `POST /api/coupled/continuous_rating` prints per row. */
export interface ContinuousRatingPart {
  part: string;
  node?: string;
  quantity?: string;
  node_c?: number;
  quantity_c?: number;
  limit_c?: number;
  over_by_K?: number;
  limit_source?: string;
}

/** THE CONTINUOUS (S1) RATING — the largest current this machine may hold FOR
 *  EVER at ONE stated cooling (`coupled_continuous_rating.rate`).  On the
 *  coupling block it is always the DUTY'S OWN saved cooling, found from the
 *  pass the loop already made; `POST /api/coupled/continuous_rating` uses the
 *  same shape for a whole table of what-if coolings.
 *
 *  Owner 2026-09-21: *«давай сделаем кнопку, или лучше добавим ещё один
 *  элемент в меню»* — the third `solve_to` option. */
export interface ContinuousRating {
  ok?: boolean;
  feasible?: boolean;
  /** absent = refused; see `refusal` */
  I_cont_A_rms?: number | null;
  I_cont_A_peak?: number | null;
  s?: number | null;
  limiting_part?: string | null;
  limit_residual_K?: number | null;
  capped?: boolean;
  /** each judged part's quantity AT the rating (°C) */
  temperatures_c?: Record<string, number>;
  parts?: ContinuousRatingPart[];
  node_means_c?: Record<string, number>;
  losses_W?: Record<string, number>;
  power?: {
    T_em_Nm?: number | null; P_cu_W?: number; P_other_loss_W?: number;
    P_mech_W?: number; P_rotor_W?: number; P_elec_W?: number;
    P_shaft_W?: number | null; eta_em?: number | null; eta_shaft?: number | null;
    k_flux?: number | null; torque_basis?: string; basis?: string;
    shaft_note?: string; note?: string;
  };
  /** the run this rating is a multiple of — the current, speed, load angle and
   *  provenance the 's' scaling is against */
  reference?: {
    I_phase_rms_A?: number; rpm?: number | null; gamma_deg?: number | null;
    coil_ref_c?: number | null; T_em_avg_Nm?: number | null;
    computed_at?: string | null; geo_fingerprint?: string | null;
    note?: string;
  };
  /** the cooling this rating answers for — machine-readable */
  cooling?: Record<string, unknown>;
  /** …and the same cooling, one line for a human: "forced air 40 m/s + bore
   *  air 10 m/s, 30 °C" */
  cooling_label?: string;
  judged?: string[];
  limits_c?: Record<string, number>;
  n_thermal_fem_solves?: number;
  converged?: boolean;
  /** `false` = the 2-D thermal solve was not monotone under this cooling and
   *  the search was refused to iterate — the row is NOT a rating */
  trustworthy?: boolean;
  model?: string;
  notes?: string[];
  note?: string | null;
  /** the one sentence — `coupled_continuous_rating.headline` */
  headline?: string;
  /** present when `ok: false` (or `feasible: false`) instead of a current */
  refusal?: { error: string; error_code?: string };

  // ── S1 VERIFICATION (owner 2026-09-21, second addendum) ──────────────────
  // *«почему сразу не пересчитывается электромагнитное моделирование для
  // найденного непрерывного режима — токи не совпадают»* — the network's own
  // answer is an ESTIMATE; the loop now CONFIRMS it with a real EM + thermal
  // pass at that current, and the record (em / field / temperatures) becomes
  // that pass.  `I_cont_A_rms` above is the VERIFIED reading once this ran;
  // the network's own first answer survives here.
  I_estimated_A_rms?: number;
  /** `true` = a real EM pass landed within 3 K of the limiting part's card;
   *  `false` = it did not (see `note`), or the pass itself could not be
   *  solved; absent = no card limit to verify against, so none was tried —
   *  `I_cont_A_rms` is then still the network's own estimate. */
  verified?: boolean;
  /** how many real EM + thermal passes the verification made (cap 2) */
  verification_passes?: number;
  /** the verified pass's own reading, minus the limit — 0 is exact, positive
   *  is still over */
  miss_K?: number | null;
  /** the SETPOINT's own question and answer, kept once the record's own
   *  machine moves to the S1 point — "runs 41 s" is this, not the S1 line */
  duty_point?: { I_phase_rms_A?: number | null; T_em_Nm?: number | null;
                verdict?: string | null };
  /** `true` once a real S1 verification pass REPLACED this record's own
   *  em / field / temperatures — i.e. the ONLY time the panel's tiles are
   *  the S1 machine rather than the setpoint's (owner 2026-09-21, third
   *  round: *«опять токи не совпадают»* — the Operating point panel and the
   *  AT-THE-LIMIT line both kept describing the setpoint while the tiles
   *  had already moved to S1, with nothing on screen saying so). */
  record_is_s1?: boolean;
}

/** The found regime, as the coupling block carries it.  It IS a `RegimeLimits`
 *  (same four numbers, same names) plus what only a coupled answer knows: which
 *  kind of cycle it was, whether what the duty ASKED for fits under what the
 *  machine can hold, and the sentence that says so. */
export interface CoupledRegime extends RegimeLimits {
  kind?: 'S2' | 'S3';
  duty?: string;
  cycle_s?: number | null;
  t_on_allowable_s?: number | null;
  requested_t_on_s?: number | null;
  /** `true` it fits, `false` it does not, `null`/absent nothing was asked */
  fits_requested?: boolean | null;
  /** `false` = no duty ratio holds the limits at all — one pull is all there is */
  feasible?: boolean;
  unlimited?: boolean;
  limiting_part?: string | null;
  winding_hot_peak_c?: number | null;
  magnet_peak_c?: number | null;
  note?: string | null;
}

export interface CoupledRunResult {
  ok: boolean;
  coupling: CouplingBlock;
  /** The last electromagnetic run — the same payload a plain Run returns, and
   *  deliberately left loosely typed: `TransientCharts` owns the transient's
   *  shape (90-odd fields) and re-types it at the point of use, and a second
   *  copy of that interface here is a second copy that drifts. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  transient: Record<string, any>;
  /** the converged temperature map (already remembered as the Thermal tab's
   *  last result, so that tab needs no change to show it) */
  thermal: Record<string, unknown>;
  written_to_last_run: boolean;
  run_id: string;
  elapsed_s: number;
  /** LOADED, not solved (2026-09-22): an identical `/run` request was already
   *  in `motor_ai_sim.run_history` (kind `coupled.run`) and was handed back
   *  instead of iterating the loop again — see `run_history.py`'s module
   *  docstring and `routes/coupled.py`'s `_load_coupled_history_entry`.
   *  `computed_at` is the ISO stamp of when it WAS solved; a fresh run
   *  carries neither field.  `history_key` addresses the row for the
   *  History popover's delete — a Recompute instead just re-sends this same
   *  request with `fresh: true`, so it never needs the key. */
  served_from_history?: boolean;
  computed_at?: string;
  history_key?: string;
}

/** Is the toggle on?  Read straight from localStorage so the run builder does
 *  not need a prop threaded through three components — the same way it reads
 *  every other panel setting (`readSimSetting`). */
export function coupledEnabled(): boolean {
  try { return JSON.parse(localStorage.getItem('sim.coupled') || 'false') === true; }
  catch { return false; }
}

/** WHICH QUESTION the loop is asked (owner 2026-09-18, third option added
 *  2026-09-21) — the selector beside the Coupled thermal switch.
 *
 *  `'steady'` is the default and every record written with it is what it always
 *  was; `'limits'` stops at the first limit a part reaches and reports the
 *  machine at that moment; `'continuous'` does the same and adds the largest
 *  current this machine may hold for ever at this duty's own saved cooling
 *  (S1).  Read from the SAME per-duty memory the operating point uses
 *  (`lib/dutySettings`), so switching duty brings its own answer back — a peak
 *  that is a 24-second pull and a continuous duty that is a steady state are
 *  two different questions about one machine. */
export function coupledSolveTo(): 'steady' | 'limits' | 'continuous' {
  try {
    const v = JSON.parse(localStorage.getItem('sim.coupledSolveTo') || '"steady"');
    return v === 'limits' || v === 'continuous' ? v : 'steady';
  } catch { return 'steady'; }
}

/** WHICH DRIVE the loop solves on (2026-09-22 — the Coupled panel's own
 *  selector, closing Stage 2's open item: `drive: "inverter"` used to be
 *  reachable only from the API).  `'sine'` is the default and every record
 *  written with it is what it always was: the Simulation tab's own drive
 *  (sine / voltage / PWM) rides the payload exactly as before this selector
 *  existed.  `'inverter'` asks the CONTROLLER's own bridge to drive this pass
 *  instead — `routes/coupled.py`'s THIRD drive
 *  (`docs/CONTROLLER_MODULE_2026-09-22.md` §7a) — and `SimulationPanel` only
 *  offers it once a controller is saved for the active configuration. */
export function coupledDrive(): 'sine' | 'inverter' {
  try {
    const v = JSON.parse(localStorage.getItem('sim.coupledDrive') || '"sine"');
    return v === 'inverter' ? 'inverter' : 'sine';
  } catch { return 'sine'; }
}

/** The saved controller settings the selector's gate fetched, mirrored here so
 *  `runCoupled` can send `body.controller` BY REFERENCE without
 *  `TransientCharts` threading it through — the same one-browser-side-source
 *  `coupledEnabled` / `coupledSolveTo` already use.  `null` = the selector is
 *  on 'sine', or no controller is saved for the active configuration; either
 *  way an 'inverter' request then leans on the ACTIVE DUTY's own stored
 *  controller solve, exactly as a bare `drive: "inverter"` API call does
 *  (`routes/coupled.py::_duty_controller_record`). */
export function coupledControllerRef(): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem('sim.coupledControllerRef');
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

export function setCoupledControllerRef(v: Record<string, unknown> | null): void {
  try {
    if (v) localStorage.setItem('sim.coupledControllerRef', JSON.stringify(v));
    else localStorage.removeItem('sim.coupledControllerRef');
  } catch { /* private mode — the request falls back to the duty's own record */ }
}

/** The Thermal tab's boundary conditions, as THAT TAB'S STORE holds them —
 *  registered by `stores/thermalStore` at load (it imports this module, so the
 *  dependency runs one way).  The store adopts the user's server-side settings
 *  when the tab first mounts, so its block is complete by construction.
 *
 *  It used to be read straight from the `therm.*` localStorage keys, one by one,
 *  skipping absent ones.  A browser in which the coolant flow had only ever been
 *  typed on ANOTHER browser holds `therm.coolMode = "liquid"` and no
 *  `therm.flowLpm` at all (the value came from the server into the store, never
 *  into localStorage), and the run then shipped a block with the mode and
 *  without the flow: the backend refused "coolant flow must be greater than
 *  0 L/min" while the Thermal tab itself showed 10 L/min (2026-09-09, user:
 *  "опять run не работает").  A partial block is worse than none — `null`
 *  makes the backend load the user's own stored settings, which are whole. */
let _thermalPanelBlock: (() => Record<string, string> | null) | null = null;

/** `stores/thermalStore` hands over the reader once; `null` from it means
 *  "not hydrated yet — let the backend use the stored settings". */
export function registerThermalPanelBlock(read: () => Record<string, string> | null): void {
  _thermalPanelBlock = read;
}

export function thermalSettings(): Record<string, string> | null {
  try { return _thermalPanelBlock ? _thermalPanelBlock() : null; }
  catch { return null; }
}

/** What the CALLER decides about the loop.  Defaulted to the Electromagnetic
 *  tab's toggle, which is the only caller that existed when this module was
 *  written; the Thermal tab's fallback (2026-09-08) asks for something
 *  deliberately smaller — see each field. */
export interface CoupledRunOptions {
  /** The Thermal panel's boundary conditions.  Omitted = read from this
   *  browser's `therm.*` keys; the Thermal tab passes what it is SHOWING,
   *  because it is the tab that asked for the run. */
  thermalSettings?: Record<string, string> | null;
  /** Solve the rotor stress at the converged temperatures too.  The toggle
   *  always asks for it; a Thermal-tab Solve does not — it asked for a
   *  temperature map, and a mechanical solve nobody pressed is minutes nobody
   *  budgeted. */
  mechanical?: boolean;
  /** Cap on the electromagnetic runs.  Omitted = the server reads the Thermal
   *  panel's own `maxIter`.  The Thermal tab's fallback passes 1: it is not
   *  iterating to a fixed point (that is this toggle's job), it is making the
   *  ONE run its map was missing. */
  maxIter?: number;
  /** `'steady'` (the default) iterates until the temperatures stop moving;
   *  `'limits'` stops at the first limit a part reaches and reports the machine
   *  at that moment; `'continuous'` does the same and adds the S1 rating at
   *  this duty's own cooling.  Omitted = whatever the selector beside the
   *  switch holds for the loaded duty. */
  solveTo?: 'steady' | 'limits' | 'continuous';
  /** `'sine'` (the default) leaves the caller's own payload `drive` alone;
   *  `'inverter'` overrides it with the CONTROLLER's bridge.  Omitted =
   *  whatever the selector beside the switch holds — see `coupledDrive`. */
  drive?: 'sine' | 'inverter';
  /** The controller settings to send BY REFERENCE alongside `drive:
   *  "inverter"` (`body.controller`).  Omitted = `coupledControllerRef()`,
   *  which is `null` when nothing is saved — the request then leans on the
   *  active duty's own stored controller solve. */
  controller?: Record<string, unknown> | null;
}

/**
 * Run the loop.  Blocks for its whole duration — minutes, and up to `max_iter`
 * full transients — exactly as the plain Run's fetch does; the strip above the
 * page polls `/api/coupled/progress` meanwhile, and Stop cancels by run-id.
 *
 * A refusal (422) comes back with a sentence an engineer can act on: the loop
 * checks everything that would make it impossible — a single frame, an imposed
 * voltage, the conducting solve switched off, a machine with nothing cooled —
 * BEFORE it spends the first transient on it.
 */
export async function runCoupled(
  payload: Record<string, unknown>,
  signal?: AbortSignal,
  opts: CoupledRunOptions = {},
): Promise<CoupledRunResult> {
  const settings = opts.thermalSettings === undefined
    ? thermalSettings() : opts.thermalSettings;
  const body: Record<string, unknown> = { ...payload };
  if (settings) body.thermal_settings = settings;
  // Phase 3: the loop ends with the rotor stress solved at the converged
  // per-part temperatures, so the Mechanical tab shows the same machine at the
  // same temperatures (user 2026-09-08: "чтобы температуры везде были
  // одинаковы").  Opt-in on the server; the toggle always asks for it.
  body.mechanical = opts.mechanical !== false;
  if (opts.maxIter !== undefined) body.max_iter = opts.maxIter;
  // WHICH QUESTION (owner 2026-09-18).  Always sent, never inferred server-side:
  // the backend's own default is `steady`, and a body that said nothing would
  // make "the user chose the steady state" and "this client is too old to ask"
  // the same request.
  body.solve_to = opts.solveTo ?? coupledSolveTo();
  // THE DRIVE (2026-09-22).  'inverter' OVERRIDES whatever `payload.drive`
  // already carried (the Simulation tab's own ideal reference) with the
  // CONTROLLER's bridge, and rides `body.controller` beside it when one is
  // known — never inferred silently: 'sine' leaves the caller's own payload
  // exactly as it was, which is what "nothing else changed" has to mean for
  // every duty that has never touched this selector.
  const drv = opts.drive ?? coupledDrive();
  if (drv === 'inverter') {
    body.drive = 'inverter';
    const ref = opts.controller !== undefined ? opts.controller : coupledControllerRef();
    if (ref) body.controller = ref;
  }
  const r = await fetch(`${BASE}/run`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal,
  });
  if (!r.ok) {
    let msg = await r.text();
    // The 422 carries {detail: {error, invalid_parameters}} — show the sentence,
    // not the JSON around it (the Thermal tab's fetch does the same).
    try {
      const d = JSON.parse(msg)?.detail;
      msg = typeof d === 'string' ? d : (d?.error ?? msg);
    } catch { /* not JSON — keep the raw text */ }
    throw new Error(msg.slice(0, 400));
  }
  return r.json() as Promise<CoupledRunResult>;
}

/** Stop the loop with this run-id.  Sets BOTH the loop's registry and the
 *  transient's, so a cancel that lands mid-transient is honoured by the frame
 *  march instead of waiting it out. */
export function cancelCoupled(runId: string | number): void {
  fetch(`${BASE}/cancel?run_id=${encodeURIComponent(String(runId))}`,
    { method: 'POST' }).catch(() => { /* the loop's own check still stops it */ });
}

/**
 * Put the converged pair into this tab's own two fields.
 *
 * The standing rule is that a temperature computed on one tab is never written
 * into another tab's inputs by itself (`thermal/api.writeSimCoilTemp` is behind
 * a button for exactly that reason).  This is not that case and the difference
 * is the whole toggle: the user asked THIS tab to solve for these two
 * temperatures, so leaving the fields showing the guess the loop started from
 * would be showing an input the run did not use.  Same key + event the panel's
 * own persisted fields use, so the fields update without a remount.
 */
/** The last coupled run's block, from the server's own memory (survives a
 *  reload and a backend restart) — `null` when the loop never ran or the
 *  lookup failed.  `stale` = it was solved for another geometry. */
export async function fetchCoupledLast(): Promise<{ coupling: CouplingBlock; stale: boolean; computedAt?: string } | null> {
  try {
    const r = await fetch(`${BASE}/last`);
    if (!r.ok) return null;
    const j = await r.json();
    const c = j?.result?.coupling;
    if (!j?.has_result || !c || typeof c.coil_temp_c !== 'number') return null;
    return { coupling: c as CouplingBlock, stale: j.stale_geometry === true,
             computedAt: j?.result?.computed_at };
  } catch { return null; }
}

export function adoptConvergedTemperatures(c: CouplingBlock, stamp?: string): void {
  try {
    localStorage.setItem('sim.coilTemp',
      JSON.stringify(Math.round(c.coil_temp_c * 10) / 10));
    // A string, because the field is "empty = the magnet card as quoted".
    localStorage.setItem('sim.magnetTempC', JSON.stringify(
      c.magnet_temp_c == null ? '' : String(Math.round(c.magnet_temp_c * 10) / 10)));
    // WHICH run these fields now describe (2026-09-09) — see `couplingStamp`.
    if (stamp) localStorage.setItem('sim.coupledAdopted', stamp);
    window.dispatchEvent(new Event('sim-settings-restored'));
  } catch { /* private mode — the answer is still on the card */ }
}

/** Identity of the coupled answer the temperature fields were taken from.
 *
 *  2026-09-09.  User: *"здесь одна температура, а здесь другая — как это
 *  понять?"* — the card showed a loop converged at 200 °C while the Coil
 *  temperature field still read 111, the value the run STARTED from, because
 *  the adopt above runs only on the live response: a page reload (or a coupled
 *  POST whose connection dropped after the server had finished, which happened
 *  the same evening) never reached it, and the next uncoupled Run would have
 *  solved the machine at a temperature nobody's result describes.
 *
 *  A RESTORED run therefore adopts too — but only once, keyed on this stamp, so
 *  a temperature typed by hand AFTER the run survives every later reload. */
export function couplingStamp(c: CouplingBlock, computedAt?: string): string {
  return [c.coil_temp_c, c.magnet_temp_c ?? '', c.iterations ?? '',
          computedAt ?? ''].join('|');
}

/** Has this coupled answer already been written into the panel's fields? */
export function couplingAdopted(stamp: string): boolean {
  try { return localStorage.getItem('sim.coupledAdopted') === stamp; }
  catch { return false; }
}

/** "winding 128 °C · magnets 163 °C · mechanical 63 W · 3 it." — the one line
 *  the summary shows.
 *
 *  The mechanical term joined it on 2026-09-08, when the loop started iterating
 *  the bearing temperature with the rest: a coupled run whose friction is not
 *  on the line is a loss the reader has to go looking for.  Omitted entirely on
 *  a machine that names no bearings, because "0 W" would be a claim. */
export function couplingLine(c: CouplingBlock): string {
  // A LIMITED record's temperatures are the ones the numbers were SOLVED at
  // (owner 2026-09-18): the limiting part sits exactly at its limit, so its
  // term says so — "winding 200 °C (at the limit)" — and the reader knows the
  // torque, the losses and R beside it are those of a 200 °C winding.
  const limPart = c.mode === 'limited' ? String(c.limited?.part ?? '') : '';
  const atLimit = (part: string) => (limPart === part ? ' (at the limit)' : '');
  const m = c.magnet_temp_c == null ? null
    : `magnets ${c.magnet_temp_c.toFixed(0)} °C${atLimit('magnet')}`;
  const w = c.P_mech_extra_W;
  const mech = w == null ? null : `mechanical ${w.toFixed(w < 10 ? 1 : 0)} W`;
  // The mechanical step's own footnotes (2026-09-09): a joint solved bonded
  // because separation left its part unretained, or a refusal.
  const mb = c.mechanical;
  const mechNote = !mb ? null
    : mb.ok === false ? 'mechanics ⚠'
    : mb.contact_fallback ? `${String(mb.contact_fallback.pair ?? 'joint').replace('_', '–')} solved ${mb.contact_fallback.to ?? 'bonded'}`
    : null;
  // The modes and the critical speeds the same run left (2026-09-13) used to
  // print here too (f₁ …, crit … rpm) — owner, 2026-09-21: *«не надо их
  // выводить сюда»*.  They stay everywhere else that already carries them:
  // the Mechanical tab, this record, the catalog row and the report — only
  // this ONE dashboard line drops them.  `couplingTooltip` → `mechanicalRows`
  // (below) still prints the full sentences on hover.
  // THE REGIME, on an impulse duty (2026-09-16): the ratio the machine can hold
  // and, when the duty asked about one, whether it fits.  One term — the
  // sentence is in the tooltip — and nothing at all on a continuous duty.
  return [`winding ${c.coil_temp_c.toFixed(0)} °C${atLimit('winding')}`, m, mech,
    // …and a LIMITED run did not fail to settle, it was asked to stop (owner
    // 2026-09-18), so there is no ⚠.  The temperature term already says
    // "(at the limit)" when the winding or the magnets limit; only a record
    // limited by another part (the bearing seat, or one with no part named)
    // still needs the words on the iteration term.
    c.mode === 'limited'
      ? (limPart === 'winding' || limPart === 'magnet'
          ? `${c.iterations} it.` : `${c.iterations} it. · at the limit`)
      : `${c.iterations} it.${c.converged ? '' : ' ⚠'}`,
    regimeTerm(c.duty_cycle),
    // THE CONTROLLER's two numbers, on a run that had one (Stage 2).  The
    // machine keeps ONE efficiency and it is the shaft's — the drive's second
    // one is a DIFFERENT quantity, so it is NAMED in full rather than shown
    // beside the first under the same word.  Absent on every sine and ideal-PWM
    // run, which is what "nothing else changed" looks like on this line.
    controllerTerm(c.controller),
    mechNote]
    .filter(Boolean).join(' · ');
}


/** "T_j 132 °C · η wall-to-shaft 96.24 %" — the drive's own two numbers. */
export function controllerTerm(
  ctl: CouplingBlock['controller']): string | null {
  if (!ctl) return null;
  const tj = ctl.t_j_c ?? ctl.thermal?.t_j_max_c;
  const wts = ctl.efficiency?.wall_to_shaft;
  const bits: string[] = [];
  if (tj != null) bits.push(`T_j ${Number(tj).toFixed(0)} °C`);
  if (wts != null) bits.push(`η wall-to-shaft ${(Number(wts) * 100).toFixed(2)} %`);
  if (ctl.limits_verdict === 'fail') bits.push('datasheet limits ⚠');
  return bits.length ? bits.join(' · ') : null;
}

/** "ED 21.6 % of 60 s ⚠" — the found regime, short enough for the card. */
export function regimeTerm(r: CoupledRegime | null | undefined): string | null {
  // …and nothing at all while the duty-cycle feature is off (owner 2026-09-17).
  // With the backend flag off no coupled answer carries a regime anyway; this
  // guard is for the RESTORED one — a record filed before the feature was put
  // away must not print an ED on a build that has no duty cycle in it.
  if (!r || !DUTY_CYCLE_ENABLED) return null;
  const flag = r.feasible === false || r.fits_requested === false ? ' ⚠' : '';
  if (r.kind === 'S2') {
    const t = r.t_on_allowable_s;
    return t == null ? null : `pull ${g1(t)} s${flag}`;
  }
  const ed = r.ed_allowable_pct;
  if (ed == null) return null;
  const cyc = r.ed_cycle_s ?? r.cycle_s;
  return `ED ${g1(ed)} %${cyc == null ? '' : ` of ${g1(cyc)} s`}${flag}`;
}

/** THE SENTENCE the inline run notice shows when the duty does not fit — and
 *  `null` when it does, which is what "no news" looks like.
 *
 *  A ratio that does not fit is an ANSWER, not a failure: the loop solved the
 *  machine and the machine cannot hold what the duty asks of it.  So it is
 *  prefixed rather than thrown, and `lib/runNotice` reads that prefix as the
 *  INFO kind — blue, no "not solved" in front of it. */
export function coupledRegimeNotice(c: CouplingBlock | null | undefined):
    string | null {
  const r = c?.duty_cycle;
  if (!r || !DUTY_CYCLE_ENABLED) return null;
  if (r.feasible !== false && r.fits_requested !== false) return null;
  return `Duty cycle: ${r.note || regimeLine(r) || 'the duty does not fit'}`;
}

/* ── HOW LONG MAY IT RUN (owner 2026-09-17) ─────────────────────────────────
 * *«если где-то выходим за лимиты, нужно посчитать время, за какое мотор
 * проработает до этого лимита»*.  A temperature past its class is half an
 * answer; the loop now computes the other half and it gets ONE line.
 *
 * NOT gated by the duty-cycle flag: this is not a duty cycle.  It reads no
 * cycle block, needs no duty ratio and runs on every coupled loop. */

/** A duration a human reads at a glance: "0.8 s", "48 s", "2 m 40 s".
 *
 *  The same rule `coupled_time_to_limit.fmt_seconds` and `report._secs_words`
 *  apply, so one number is called one thing in the panel, the log and the PDF. */
export function fmtSecs(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s < 0) return '—';
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m} m ${String(Math.round(s - m * 60)).padStart(2, '0')} s`;
}

/** "Runs 2 m 40 s from cold, 1 m 05 s from rated, then the winding reaches
 *  200 °C" — and `null` when there is nothing to say.
 *
 *  Nothing to say means one of two things, and both are answers: the point is
 *  inside every limit it has (so there is no time to a limit, and printing one
 *  would invite planning around a number that is not a constraint), or the
 *  record predates this feature. */
export function timeToLimitLine(t: TimeToLimit | null | undefined):
    string | null {
  if (!t || t.within_limits) return null;
  const part = t.limiting_part ?? 'a part';
  const lim = t.limits_c?.[part];
  const ends = `then the ${part} reaches${lim == null ? ' its limit'
    : ` ${Math.round(lim)} °C`}`;
  const runs: string[] = [];
  const cold = t.starts?.cold?.time_to_limit_s;
  const rated = t.starts?.rated?.time_to_limit_s;
  if (cold != null) runs.push(`${fmtSecs(cold)} from cold`);
  if (rated != null) runs.push(`${fmtSecs(rated)} from rated`);
  if (!runs.length) {
    return `No time to the ${part} limit: this network settles below it`;
  }
  return `Runs ${runs.join(', ')}, ${ends}`;
}

/* ── SOLVE TO THE STEADY STATE, OR TO THE LIMITS (owner 2026-09-18) ─────────
 * *«надо сделать выбор — или считать до конца стабилизации температуры, или
 * считать до лимитов и находить время работы при заданных условиях»*.  The
 * selector beside the Coupled thermal switch asks the question; these two read
 * the answer back off the record, so the panel, the catalog chip and the PDF
 * print ONE sentence about one state. */

/** The line the panels print for a coupled answer — `null` when there is none.
 *
 *  A LIMITED record's own sentence ("Runs 24 s from cold … — the numbers below
 *  are the machine at that moment"), else the steady record's time-to-limit
 *  line, else nothing. */
export function coupledStateLine(c: CouplingBlock | null | undefined):
    string | null {
  if (!c) return null;
  if (c.mode === 'limited' && c.limited?.line) return c.limited.line;
  return timeToLimitLine(c.time_to_limit);
}

/** The HelpTip behind it: the model, and — on a limited answer — the two things
 *  it is conditional on, the operating point and the COOLING (owner's addendum
 *  of the same day: *«при заданной мощности и заданном охлаждении»*). */
export function coupledStateTip(c: CouplingBlock | null | undefined): string {
  if (!c) return '';
  const l = c.mode === 'limited' ? c.limited : undefined;
  if (!l) return timeToLimitTip(c.time_to_limit);
  const steady = l.steady_state_would_be?.[l.part];
  return [
    l.note ?? l.line ?? '',
    l.cooling_words ? `Cooling: ${l.cooling_words}.` : '',
    steady == null ? ''
      : `Held here for ever the ${l.part} would reach ${Math.round(steady)} °C`
        + `${l.steady_state_converged ? '' : ' (the pass this was fitted to — '
          + 'the loop was stopped at the limit, not iterated to a fixed point)'}.`,
    timeToLimitTip(c.time_to_limit),
  ].filter(Boolean).join('\n');
}

/** The HelpTip behind that line: what the model IS, in one short paragraph
 *  plus the per-part detail the answer is a minimum over. */
export function timeToLimitTip(t: TimeToLimit | null | undefined): string {
  if (!t) return '';
  const rows = (t.parts ?? []).map(p => `· ${p.note ?? p.part}`);
  return [
    'The four-node lumped network (winding, stator, rotor, magnet) fitted to '
    + "THIS run's own converged thermal map — its conductances are divided out "
    + 'of that map, its heat capacities are the machine\'s own masses — switched '
    + 'on at the start state and integrated at this operating point. The time is '
    + 'the first crossing; only the copper loss moves with temperature.',
    t.starts?.cold?.start_source ? `From cold: ${t.starts.cold.start_source}`
                                 : '',
    t.starts?.rated?.start_source ? `From rated: ${t.starts.rated.start_source}`
                                  : (t.rated_start_note ?? ''),
    ...rows,
    t.network?.note ?? '',
    t.calibration_runaway
      ? 'The map behind this network RAN AWAY — it has no equilibrium, so the '
        + 'conductances come from a state the machine cannot actually hold.'
      : '',
  ].filter(Boolean).join('\n');
}

/* ── THE CONTINUOUS (S1) RATING (owner 2026-09-21) ───────────────────────────
 * *«давай сделаем кнопку, или лучше добавим ещё один элемент в меню»* — the
 * third `solve_to` option, beside `steady` and `limits`: the largest current
 * this machine may hold FOR EVER at this duty's own saved cooling, found from
 * the pass the loop already made. */

/** "S1: 34.4 A rms · limited by winding 200 °C" — and `null` when it was not
 *  asked for.  A rating that could not be found still prints a line (there is
 *  always something to say about why), the way a refused `limits` pass still
 *  warns instead of going silent.
 *
 *  Owner addendum, 2026-09-21: *«не пиши уже мощность и момент — его и так
 *  видно»* — the tiles already show the machine's numbers, and the S1 torque
 *  is a linear estimate anyway, so the line names only the current and what
 *  limits it.  Both are still in the stored block for the API/CLI
 *  (`power.T_em_Nm` / `power.P_shaft_W`) and in the tooltip's per-part table. */
export function continuousRatingLine(c: CouplingBlock | null | undefined):
    string | null {
  const r = c?.continuous_rating;
  if (!r) return null;
  if (r.trustworthy === false) {
    // The specific reason, when the block names one (a contradiction with
    // this run's own time_to_limit verdict, a re-solve that never converged,
    // the non-monotone-map case) — never only ever the one this line used to
    // hard-code.
    const why = r.notes?.find(n => n.startsWith('THE 2-D')
                                  || n.startsWith('CONTRADICTS'))
      ?? r.note ?? 'the map could not be iterated';
    return `S1: NOT A RATING — ${why}`;
  }
  if (!r.ok || r.feasible === false || r.I_cont_A_rms == null) {
    return `S1: ${r.refusal?.error ?? r.note ?? 'no continuous rating under '
      + 'this cooling'}`;
  }
  const parts: string[] = [`${r.I_cont_A_rms.toFixed(1)} A rms`];
  if (r.limiting_part) {
    const lim = r.limits_c?.[r.limiting_part];
    parts.push(`limited by ${r.limiting_part}`
      + (lim == null ? '' : ` ${Math.round(lim)} °C`));
    // THE VERIFICATION STATUS (owner 2026-09-21, second addendum) — the whole
    // reason this pass exists: a reader must see whether the current beside
    // it was CONFIRMED by a real electromagnetic pass or is still the
    // network's own estimate.
    if (r.verified === true) {
      const actual = r.temperatures_c?.[r.limiting_part];
      parts.push(`FEM-verified${actual == null ? ''
        : ` (${r.limiting_part} ${actual.toFixed(1)} °C)`}`);
    } else if (r.verified === false) {
      parts.push(`estimate, not verified (${r.note ?? 'see the tooltip'})`);
    }
  }
  return `S1: ${parts.join(' · ')}`;
}

/** The HelpTip behind it: the model in one line, the cooling it is
 *  conditional on, and the table of every judged part's temperature. */
export function continuousRatingTip(c: CouplingBlock | null | undefined): string {
  const r = c?.continuous_rating;
  if (!r) return '';
  const rows = (r.parts ?? []).map(p => {
    const q = p.quantity_c != null ? `${p.quantity_c.toFixed(1)} °C` : '—';
    const lim = p.limit_c != null ? ` / ${p.limit_c} °C` : '';
    return `· ${p.quantity ?? p.part}: ${q}${lim}`;
  });
  return [
    r.headline ?? '',
    r.cooling_label ? `Cooling: ${r.cooling_label}.` : '',
    'Largest current the machine holds for ever at this cooling: torque '
    + 'scaled linearly with current, iron and magnet losses held at the '
    + 'solved point.',
    ...rows,
    r.trustworthy === false
      ? (r.notes?.find(n => n.startsWith('THE 2-D')) ?? '') : '',
  ].filter(Boolean).join('\n');
}

/* ── THE RECORD MOVED TO S1 — say so, and let the panel catch up ────────────
 * Owner, 2026-09-21, third round (screenshot: tiles at the S1 machine, the
 * Operating point panel and the AT-THE-LIMIT line both still describing the
 * setpoint): *«опять токи не совпадают»*. */

/** "Results at the continuous current 48.6 A rms (setpoint 63.64 A rms)" —
 *  `null` unless a real S1 verification pass actually REPLACED this record
 *  (`record_is_s1`), which is the only time the tiles are not the setpoint's
 *  own numbers and a reader needs telling which current they are looking at. */
export function s1ResultsAtLine(c: CouplingBlock | null | undefined):
    string | null {
  const r = c?.continuous_rating;
  if (!r || r.record_is_s1 !== true) return null;
  const i = r.I_cont_A_rms;
  const iSet = r.duty_point?.I_phase_rms_A;
  if (i == null || iSet == null) return null;
  return `Results at the continuous current ${i.toFixed(1)} A rms `
    + `(setpoint ${iSet.toFixed(2)} A rms)`;
}

/** Writes the S1 current into the Operating point panel's OWN `current`
 *  field (I phase rms) — the same `sim.current` localStorage key and the
 *  same `sim-settings-restored` re-read event `SimulationPanel`'s
 *  `usePersisted('current', …)` already uses for a duty load, so the peak
 *  reading (derived from it) and the panel's own state stay consistent and
 *  the value persists exactly like one the user typed.
 *
 *  NEVER SILENT (project rule): called only by the auto-set below for a
 *  verified S1 run, which always pairs the call with a visible notice + undo
 *  (`s1AutoSetPlan` / PhysicsDashboard) — never a bare side effect with
 *  nothing said on screen.  The manual "Use N A as the operating point"
 *  button that used to call this on click is GONE (owner, fourth round:
 *  *«ты что не можешь сам записать этот ток и прогнать солвер с ним
 *  автоматом?»* — the auto-set below is the answer).
 *
 *  ALSO PATCHES THE BACKEND DIRECTLY (owner, 2026-09-21, fifth round: field
 *  still read the old setpoint after an F5, live-verified in a sandbox).
 *  `SimulationPanel` mounts by ADOPTING THE SERVER'S OWN `max_current` —
 *  "the SERVER config is the single source of truth" (its own mount-effect
 *  comment) — and only afterwards starts a 700 ms DEBOUNCED patch back from
 *  whatever the panel is showing.  Writing only `localStorage` + the restore
 *  event wins the RACE inside one already-open tab, but loses it across a
 *  reload that lands before that debounce fires: the mount effect re-reads
 *  the server's still-old current and calls `setCurrent` with it, which then
 *  writes that old value back over `sim.current` too (`usePersisted`'s own
 *  write-back effect), so the browser forgets the S1 value ever arrived.
 *  Sending the SAME field the debounced sync PATCHes (`max_current`) right
 *  here removes the race instead of trying to win it: by the time anyone
 *  reloads, the server already agrees.
 *
 *  A SECOND race, found live in a sandbox (owner, sixth round — the field
 *  DID move on screen, but the backend read back the OLD current a moment
 *  later): the mount-time "adopt the server's own operating point" fetch
 *  (`SimulationPanel`'s `/api/simulation/status` + `/api/simulation/config`
 *  GET, issued once on page load) can still be IN FLIGHT — with the SETPOINT,
 *  not the S1 current, since it was sent before this function ever ran — when
 *  a finished continuous run restores and this function fires.  That GET then
 *  resolves and calls `setCurrent(server_value)` unconditionally, undoing
 *  the S1 write in local state; its OWN 700 ms debounced sync then PATCHes
 *  the undone value back to the server too (observed: `max_current: 45.962`
 *  overwriting the `30.0` this function had just written).  The stamp below
 *  is the other half of the fix — `SimulationPanel`'s mount effect skips its
 *  `setCurrent` when this stamp is newer than the moment ITS OWN fetch was
 *  issued, i.e. an S1 write that happened while that GET was in flight or
 *  after it. */
export function applyS1AsOperatingPoint(i_A_rms: number): void {
  try { localStorage.setItem('sim.current', JSON.stringify(i_A_rms)); }
  catch { /* best effort — the field simply is not updated */ }
  // The race-guard stamp SimulationPanel's mount effect reads — see above.
  try { localStorage.setItem('sim.current.s1AppliedAt', String(Date.now())); }
  catch { /* best effort — the guard simply does not arm */ }
  try { window.dispatchEvent(new Event('sim-settings-restored')); }
  catch { /* best effort */ }
  try {
    fetch(`${API}/api/simulation/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_current: i_A_rms }),
    }).catch(() => { /* the debounced sync will retry this on the next edit */ });
  } catch { /* best effort — SSR / no fetch */ }
}

/**
 * Sync the panel's OWN operating-point fields to a LOADED result (the
 * History popover, 2026-09-22) — the same setter path as
 * `applyS1AsOperatingPoint` above (the same `sim.*` keys, the same
 * `sim-settings-restored` re-read event), generalised to every coordinate
 * `SummaryTable`'s `opStale` guard compares: current, γ, rpm.
 *
 * WHY THIS EXISTS: a History row is the same point ITS OWN run was solved
 * at, by construction — loading one and then showing the dashboard's
 * "⚠ STALE — DIFFERENT MACHINE" banner over it, only because the operating-
 * point FIELDS still hold whatever was typed before the click, would be
 * exactly the false alarm the S1 auto-set feature above exists to prevent
 * (PhysicsDashboard's 2026-09-21 note: *"почему замыленный экран … опять
 * токи не совпадают"*). `current` is `I_terminal_rms_A ?? I_phase_rms_A` —
 * the SAME preference `opStale`'s own comparison uses (SummaryTable.tsx),
 * so a loaded entry that predates one of the two fields still clears the
 * guard. Each field is skipped independently when the loaded summary does
 * not carry it (`undefined`/non-finite) rather than writing a bad value in.
 *
 * CONNECTION IS NOT TOUCHED: star/delta is a machine SETTING, never solved
 * for, so a mismatch between the loaded entry's winding and the panel's
 * current selection is real and must keep flagging (`connStale`).
 *
 * KNOWN GAP, same class as the PATCH note above: only `current` is echoed to
 * the server (`max_current`, the field a page reload re-adopts from); γ and
 * rpm stay LOCAL-only. The dashboard banner this function exists to fix is
 * corrected the instant it runs (the localStorage write is synchronous and
 * every persisted field re-reads on the SAME event) — a reload before the
 * next real solve is the only case where γ/rpm could revert, scoped out for
 * time rather than adding two more debounced PATCH targets the backend does
 * not yet expose.
 */
export function applyLoadedOperatingPoint(point: {
  current?: number | null; gamma_deg?: number | null; rpm?: number | null;
}): void {
  const finite = (v: number | null | undefined): v is number =>
    typeof v === 'number' && Number.isFinite(v);
  try {
    if (finite(point.current)) {
      localStorage.setItem('sim.current', JSON.stringify(point.current));
      localStorage.setItem('sim.current.s1AppliedAt', String(Date.now()));
    }
    if (finite(point.gamma_deg)) localStorage.setItem('sim.gamma', JSON.stringify(point.gamma_deg));
    if (finite(point.rpm)) localStorage.setItem('sim.rpm', JSON.stringify(point.rpm));
  } catch { /* best effort — the fields simply are not updated */ }
  try { window.dispatchEvent(new Event('sim-settings-restored')); }
  catch { /* best effort */ }
  if (finite(point.current)) {
    try {
      fetch(`${API}/api/simulation/config`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_current: point.current }),
      }).catch(() => { /* the debounced sync will retry this on the next edit */ });
    } catch { /* best effort — SSR / no fetch */ }
  }
}

/* ── AUTO-SET on a VERIFIED S1 run (owner 2026-09-21, fourth round) ─────────
 * Screenshot after a `continuous` coupled run: the dashboard DIMMED (the
 * stale/"different point" verdict) and the Operating point panel still read
 * the setpoint (63.64 A) under tiles at the S1 machine (48.6 A) — *«почему
 * замыленный экран после окончания каплинга и почему опять токи не
 * совпадают»*.  A manual "Use N A as the operating point" button briefly
 * fixed this on click, but sat at the far right of a row and went unnoticed —
 * removed (owner, fourth round: *«ты что не можешь сам записать этот ток и
 * прогнать солвер с ним автоматом?»*).  This is the same setter, called once
 * by the run itself — but ONLY for a REAL S1
 * verification pass that replaced the record (`record_is_s1` AND
 * `verified === true`); an estimate or a contradiction must never move the
 * setpoint (rule 2 of the brief) — `continuousRatingLine` already says why
 * in that case. */

/** Whether THIS record should move the panel, and what to say if so.  `prevA`
 *  is the panel's OWN current at the moment the record arrived (captured
 *  before the write, so the notice can name what it was).  `null` when
 *  nothing should move: not S1, not verified, no number to apply, or the
 *  panel already reads within the same 0.05 A tolerance SummaryTable's own
 *  staleness check uses (nothing to announce). */
export function s1AutoSetPlan(c: CouplingBlock | null | undefined,
    prevA: number | null | undefined): { from: number; to: number } | null {
  const r = c?.continuous_rating;
  if (!r || r.record_is_s1 !== true || r.verified !== true
      || r.I_cont_A_rms == null) return null;
  if (prevA == null || !Number.isFinite(prevA)) return null;
  const to = r.I_cont_A_rms;
  if (Math.abs(prevA - to) <= 0.05) return null;
  return { from: prevA, to };
}

/** "Operating point set to the continuous current 48.6 A rms (was 63.64 A) —
 *  undo" — the one visible line the brief asks for.  Ends in the literal word
 *  "undo" so a caller that needs it clickable can slice it off the end and
 *  render its own control in its place (`PhysicsDashboard` does this) without
 *  duplicating the number formatting. */
export function s1AutoSetNoticeText(plan: { from: number; to: number }): string {
  return `Operating point set to the continuous current ${plan.to.toFixed(1)} A rms `
       + `(was ${plan.from.toFixed(2)} A) — undo`;
}

/** One decimal, and none when it is a whole number. */
function g1(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

/** 2104 → "2104 Hz", 12 430 → "12.4 kHz": the card line has no room for five digits. */
function fmtHz(f: number): string {
  return f >= 10000 ? `${(f / 1000).toFixed(1)} kHz` : `${Math.round(f)} Hz`;
}

/** 48 312 → "48 300 rpm" (three significant figures, thin-space grouped, as the report prints speeds). */
function fmtRpm(v: number): string {
  const r = v >= 10000 ? Math.round(v / 100) * 100 : Math.round(v / 10) * 10;
  return `${r.toLocaleString('en-US').replace(/,/g, ' ')} rpm`;
}

/** The tooltip: every iteration, in the order they ran. */
export function couplingTooltip(c: CouplingBlock): string {
  const rows = (c.history || []).map(h => {
    const inn = `${h.T_coil_in.toFixed(1)} °C`
      + (h.T_magnet_in == null ? '' : ` / ${h.T_magnet_in.toFixed(1)} °C`);
    const out = h.T_coil_out == null ? '—'
      : `${h.T_coil_out.toFixed(1)} °C`
        + (h.T_magnet_out == null ? '' : ` / ${h.T_magnet_out.toFixed(1)} °C`);
    const hot = h.T_magnet_max == null ? ''
      : `, hottest magnet ${h.T_magnet_max.toFixed(1)} °C`;
    const mech = h.P_mech_extra_W == null ? ''
      : `, bearings + windage ${h.P_mech_extra_W.toFixed(1)} W`
        + (h.bearing_temp_c == null ? ''
           : ` at ${h.bearing_temp_c.toFixed(0)} °C`);
    return `${h.iter}. solved at ${inn} → ${out}${hot}${mech}`;
  });
  return [
    'EM ↔ thermal loop: each pass solves the electromagnetic run at a winding '
    + 'and magnet temperature, then the thermal map from ITS loss field, and '
    + 'feeds the winding / magnet BODY AVERAGES back (the hottest magnet is the '
    + 'demagnetisation check, not the Br the solver uses).',
    `Boundary conditions: the Thermal tab's. Tolerance ${c.tol_K} K on both, `
    + `damping ${c.damping}, max ${c.max_iter} passes.`,
    c.P_mech_extra_W == null ? ''
      : 'The bearings and the rotor windage are ANALYTIC (SKF frictional moment '
        + '+ Couette/disc drag) and travel round the loop with the rest: each '
        + "pass takes the bearing temperature off the previous pass's map (the "
        + 'shaft at its exposed ends, else the shaft average), bills the '
        + 'friction at it, and puts that heat back into the shaft. It is NOT '
        + 'part of the convergence test — that stays on the winding and the '
        + 'magnet, the two temperatures that change the field.',
    ...rows,
    c.residual_coil_K == null ? ''
      : `Residual at the reported point: winding ${c.residual_coil_K} K`
        + (c.residual_magnet_K == null ? '' : `, magnets ${c.residual_magnet_K} K`),
    c.converged
      ? 'The cards above were computed at the temperatures on the left of the '
        + 'last line — the run stored for this machine IS that run.'
      : (c.warning || 'did not settle'),
    c.note || '',
    ...regimeRows(c),
    ...mechanicalRows(c),
  ].filter(Boolean).join('\n');
}

/** The regime, in the tooltip: what the loop was closing on besides the two
 *  temperatures, and what the duty asked for.  Nothing on a continuous duty. */
function regimeRows(c: CouplingBlock): string[] {
  const r = c.duty_cycle;
  if (!r || !DUTY_CYCLE_ENABLED) return [];
  const rows: string[] = [
    `Duty cycle (${r.kind ?? 'S3'}${r.duty ? ` · ${r.duty}` : ''}): this point `
    + 'is an impulse, so the loop did not iterate to the temperature it would '
    + 'reach if the pull never ended — each pass FOUND the regime the limits '
    + 'allow and fed back the temperatures at it.',
  ];
  const line = regimeLine(r);
  if (line) rows.push(line);
  if (r.note) rows.push(r.note);
  return rows;
}

/** The mechanical answers the run left, one line each: rotor stress, ring
 *  modes, critical speeds.  Nothing when the run did not ask for them. */
function mechanicalRows(c: CouplingBlock): string[] {
  const mb = c.mechanical;
  if (!mb) return [];
  const rows: string[] = [];
  if (mb.ok === false) rows.push(`Rotor stress: not solved — ${mb.error ?? 'refused'}.`);
  else if (mb.sf_min != null) {
    rows.push(`Rotor stress at ${mb.rpm != null ? fmtRpm(mb.rpm) : 'the run speed'}: `
      + `SF_min ${Number(mb.sf_min).toFixed(2)}${mb.note ? ` (${mb.note})` : ''}.`);
  }
  const mo = mb.modes;
  if (mo) {
    if (mo.ok === false) rows.push(`Ring modes: not solved — ${mo.error ?? 'refused'}.`);
    else {
      const t = mo.tightest;
      rows.push(`Ring modes (${mo.body ?? 'rotor'}, ${mo.n_modes ?? '?'} solved): `
        + `f₁ ${mo.f1_hz != null ? fmtHz(mo.f1_hz) : '—'}`
        + (mo.f1_order != null ? ` order ${mo.f1_order}` : '')
        + (t && t.margin_pct != null
            ? `; closest to an excitation: ${fmtHz(t.f_hz ?? 0)} vs ${t.excitation ?? '?'}`
              + ` (${t.margin_pct > 0 ? '+' : ''}${t.margin_pct.toFixed(1)} %${t.flag ? ' ⚠ inside 10 %' : ''})`
            : '')
        + '. Bonded, no prestress, no temperature — an upper bound.');
    }
  }
  const cr = mb.critical_speeds;
  if (cr) {
    if (cr.ok === false) rows.push(`Critical speeds: not solved — ${cr.error ?? 'refused'}.`);
    else {
      rows.push('Critical speeds: '
        + (cr.first_forward_rpm != null
            ? `first forward ${fmtRpm(cr.first_forward_rpm)}`
              + (cr.first_forward_margin_pct != null
                  ? `, ${cr.first_forward_margin_pct >= 0 ? '+' : ''}${cr.first_forward_margin_pct.toFixed(1)} % vs rated`
                    + (cr.rated_rpm != null ? ` ${fmtRpm(cr.rated_rpm)}` : '')
                  : '')
            : 'no forward critical in range')
        + (cr.verdict ? ` — ${cr.verdict}` : '')
        + (cr.bearing_span_mm != null
            ? `. Shaft line ASSUMED: span ${cr.bearing_span_mm} mm, bearing k `
              + `${cr.bearing_k_n_per_m != null ? Number(cr.bearing_k_n_per_m).toExponential(1) : '?'} N/m.`
            : '.'));
    }
  }
  return rows;
}
