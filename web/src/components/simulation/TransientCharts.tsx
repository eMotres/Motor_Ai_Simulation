/**
 * TransientCharts — torque, losses and phase-voltage waveforms over time.
 *
 * Runs a series of FEM solves at N steps per electrical period
 * (default 60), the same mesh and solver settings as the rest of the
 * Simulation tab.  Plots T(t), P_cu/P_fe/P_total(t) and V_A/B/C(t).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { whenVisible } from '../../lib/pageVisible';
import {
  Box, Paper, Typography, Tooltip, CircularProgress, TextField,
} from '@mui/material';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip as RcTooltip, Legend, BarChart, Bar, Cell,
} from 'recharts';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

import type { TransientSummary } from './SummaryTable';
import { useMotorStore } from '../../stores/motorStore';
import { geoSignature } from '../common/geoSig';
import { assignmentSignature } from '../../lib/dutyMaterials';
// THE REQUEST BODY of an Electromagnetic run — shared with the Thermal tab,
// which builds the same one when it has to make the run its own solve was
// missing.  See lib/emRunPayload.
import { buildEmRunPayload } from '../../lib/emRunPayload';
import { historyNoticeFor, formatHistoryComputedAt } from '../../lib/historyNotice';
import HistoryPopover, { type HistoryRow } from '../common/HistoryPopover';
// The EM<->thermal orchestrator.  It changes exactly ONE thing about a Run:
// where the request goes.  See ./coupledApi.
import {
  adoptConvergedTemperatures, cancelCoupled, coupledRegimeNotice,
  couplingAdopted, couplingStamp, coupledEnabled, runCoupled,
  applyLoadedOperatingPoint,
  type CouplingBlock, type CoupledRunResult,
} from './coupledApi';
import HelpTip from '../common/HelpTip';

interface TransientPayload {
  // Frontend-only stamp: the geometry signature this run was computed for.
  // Lets us flag the shown result stale when the live geometry changes (the
  // backend transient cache key omits geometry, so it can't detect this).
  _geoSig?: string;
  // Frontend-only stamp: the MATERIAL ASSIGNMENT this run was computed with
  // (lib/dutyMaterials.assignmentSignature), machine + active-duty overrides
  // together — the same string the request carried.  Undefined = unknown (a run
  // from before the stamp, or a restored one), which never flags staleness.
  _matSig?: string;
  // The BACKEND's own verdict, returned by the restore path: it stamps every
  // solve with a fingerprint of the machine (`geo_fingerprint`) and compares it
  // against the live one when handing the saved run back.  true = the saved run
  // belongs to a different motor; null/undefined = the run predates the stamp,
  // i.e. UNKNOWN — which is reported as unknown, never as "fine".
  geo_fingerprint?: string;
  stale_geometry?: boolean | null;
  stale_reason?: 'geometry' | 'inputs' | null;
  n_steps: number;
  n_steps_per_period: number;
  // What the caller ASKED for, and whether the solver snapped it onto the
  // slip-node divisor grid.  When it did, the run is NOT at the requested time
  // resolution and the header says so instead of showing only what ran.
  n_steps_per_period_requested?: number;
  steps_snapped?: boolean;
  slip_nodes_per_period?: number;
  // What the run actually COST.  n_steps above is the REPORTED window; the
  // solver also solves a full extra settling period when demagnetisation is on
  // and however many warm-up frames at θ<0 the coupled eddy solve needed to
  // settle its σ·∂A/∂t history, and those frames are stripped before the result
  // is returned — so `n_steps` understated the work by up to 2× and nothing on
  // screen said so.  n_frames_solved is the honest count and solve_wall_s the
  // honest wall time; together they give the s/frame the Run panel uses for its
  // pre-run estimate.  eddy_warmup_frames is how many of them were warm-up, so
  // the estimate can quote a count this machine actually needed instead of a
  // constant (the reported series NEVER contain those frames — every chart
  // below indexes data.time_s, which starts at the first REPORTED frame).
  n_frames_solved?: number;
  solve_wall_s?: number;
  eddy_warmup_frames?: number;
  n_periods: number;
  dt_s: number;
  T_period_s: number;
  f_elec_Hz: number;
  rpm: number;
  time_s: number[];
  rotor_angle_deg: number[];
  T_em_Nm: number[];
  // Raw torque is authoritative. Legacy filtered fields are not displayed.
  T_em_raw_Nm?: number[];
  T_em_filt_Nm?: number[];
  T_avg_Nm: number;
  T_ripple_pct: number;
  T_ripple_raw_pct?: number;
  T_ripple_filt_pct?: number;
  P_cu_W: number[];
  P_cu_dc_W?: number;      // flat DC (I²R) part — chart shows it vs DC+AC so the eddy share is visible
  P_fe_W: number[];
  P_mag_eddy_W: number[];
  P_shaft_eddy_W?: number[];
  P_loss_total_W: number[];
  P_mech_avg_W: number;
  I_A: number[]; I_B: number[]; I_C: number[];
  V_A: number[]; V_B: number[]; V_C: number[];
  V_peak: number;
  T_harm_order?: number[];
  T_harm_amp?: number[];
  summary?: TransientSummary;
  // ISO timestamp of the solve (backend stamp) — shown in the header so a
  // stale view is recognisable at a glance.
  computed_at?: string;
  // LOADED, not solved: a stored result whose parameters are byte-identical to
  // this request was found in the backend's results ledger (user, 2026-09-05:
  // he came back to 667.4 A after trying another current and had to re-solve a
  // run that already existed).  Always labelled on the card — a Run that did
  // not solve must never look like one that did — with a Recompute beside it.
  ledger_hit?: boolean;
  ledger_computed_at?: string;
  // Vocabulary shared with the other three panels (2026-09-22, cbe1de8) —
  // `historyNoticeFor` reads these two, not `ledger_hit`/`ledger_computed_at`
  // above (which stay for the wall-time-skip check in `run()`).
  served_from_history?: boolean;
  history_key?: string;
  // Set (instead of `summary`) when the backend solved the waveforms but the
  // summary-block build threw — lets the UI surface the error rather than freeze
  // the cards on the previous run's numbers.
  summary_error?: string;
  // Imposed-VOLTAGE sources (drive="voltage" | "pwm_voltage"): applied V,
  // circuit diagnostics + the matched-fundamental current-drive reference →
  // ΔP_harm.  `pwm` / `bldc` / `custom_current` carry each source's own
  // description of what it actually applied.
  drive?: 'current' | 'voltage' | 'pwm_voltage' | 'custom_current' | 'bldc_current';
  v_phase_peak_V?: number | null;
  v_delta_deg?: number | null;
  pwm?: {
    v_bus_V: number; modulation_index: number; reference_delta_deg: number;
    v1_applied_V: number; v1_applied_delta_deg: number;
    f_switch_requested_Hz: number; f_switch_eff_Hz: number;
    carriers_per_period: number; steps_per_switching_period: number;
    modulator: string;
    // The real pulse train as EDGES (two per carrier), for the carrier-
    // resolution chart.  Analytic, tiny (~1-4 kB), persists with the result.
    wave_A?: {
      t_s: number[]; v: number[]; duty: number[]; n_edges: number;
      v_bus_V: number; carriers_per_period: number; f_elec_Hz: number;
      t_end_s: number; v1_peak_V: number; v1_phase_deg: number; note: string;
    } | null;
    // LINE voltage V_AB — three-level, full bus swing.  What the chart draws.
    wave_AB?: {
      t_s: number[]; v: number[]; duty_A: number[]; duty_B: number[];
      n_edges: number; v_bus_V: number; carriers_per_period: number;
      f_elec_Hz: number; t_end_s: number; levels_V: number[];
      v1_ll_peak_V: number; v1_ll_phase_deg: number; v1_phase_peak_V: number;
      note: string;
    } | null;
    // ── DC-LINK SIDE ─────────────────────────────────────────────────
    // Σ_phase s_phase(t)·i_phase(t), one MEAN per solve step, off the same
    // comparator the circuit integrated.  Positive = out of the pack
    // (motoring); negative = into it (charging).  This is the boost-mode
    // oscillogram, and it is on the same time grid as the torque and the
    // phase currents so the ripple lines up edge for edge.
    dc_link?: {
      I_dc_A: number[]; I_dc_mean_A: number; I_dc_rms_A: number;
      I_dc_ripple_pp_A: number; note: string;
    } | null;
  } | null;
  bldc?: {
    i_block_A: number; I_phase_rms_A: number; I1_phase_rms_A: number;
    gamma1_deg: number; commutation_ramp_deg: number; note: string;
  } | null;
  custom_current?: {
    n_samples: number; I_phase_rms_A: number; I1_phase_rms_A: number;
    gamma1_deg: number;
  } | null;
  // What the source APPLIED, one value per solved step (A/B/C present only for
  // the imposed-VOLTAGE sources; the imposed-current ones are already I_A/…).
  excitation?: {
    kind: string; series: 'V' | 'I'; quantity: string;
    A?: number[]; B?: number[]; C?: number[];
  } | null;
  v_dc_residual_A?: number | null;
  dP_harm_W?: number | null;
  harm_ref?: {
    I1_phase_rms_A: number; gamma1_deg: number;
    P_loss_ref_W: number; P_loss_v_W: number; T_ref_Nm: number;
  } | null;
  // Demagnetisation (present only when demag=true): per-magnet worst-cell
  // report + the full-mesh per-element Br factor for the %-map.
  demag_report?: Array<{
    magnet_index: number; H_min_kA_per_m: number; H_knee_kA_per_m: number;
    knee_proximity: number; demagnetised: boolean; Br_factor: number;
  }>;
  demag_field?: {
    vertices: [number, number][];
    triangles: [number, number, number][];
    domain_per_tri: number[];
    demag_coef_per_tri: number[];
    extent: [number, number, number, number];
  } | null;
}

interface Props {
  gamma_deg?: number;
  I_phase_rms?: number;
  onSummary?: (s: TransientSummary) => void;
  // Incremented by the "Run Simulation" button.  The transient only
  // (re)computes when this changes — never on raw gamma/current edits —
  // so the user can tweak several parameters and launch one solve.
  runNonce?: number;
  onBusyChange?: (busy: boolean) => void;
  // Steps per electrical period — now lives in the left panel.
  steps?: number;
  // "Start fresh" → backend discards cached frames before recomputing.
  fresh?: boolean;
  // Field-based magnet/shaft eddy losses (J = σ(−∂A/∂t + U) magnetodynamic
  // solve, per-magnet ∫J=0, library σ) instead of the slab d²/12 estimate.
  fieldLosses?: boolean;
  // Legacy callers may still pass this prop; raw torque is always displayed.
  torqueFilter?: boolean; // deprecated compatibility prop, ignored
  // Per-element irreversible demagnetisation — de-rates Br → torque/EMF + %-map.
  demag?: boolean;
  // Coupled σ·∂A/∂t eddy-current solve (P2): the induced currents in copper /
  // magnets / shaft become part of the Newton system, so the run reports the
  // SOLVED copper loss and its field snapshot carries the real eddy J⟳ — which
  // is what makes the J⟳ field view instant instead of a second transient.
  eddyCoupled?: boolean;
  // A design was just applied from the Sweep tab (summary numbers reused) — the
  // shown waveforms are still the PREVIOUS design's, so flag them stale.
  appliedFromSweep?: boolean;
  // EXCITATION SOURCE: imposed sinusoidal current (default), imposed
  // sinusoidal voltage (FOC verification — currents are the machine's own
  // response), a PWM inverter's chopped voltage, a 120° block, or an arbitrary
  // sampled current waveform.
  drive?: 'current' | 'voltage' | 'pwm_voltage' | 'custom_current' | 'bldc_current';
  vPeak?: number;   // voltage drives: FUNDAMENTAL phase-voltage amplitude [V, peak]
  vDelta?: number;  // voltage drives: voltage angle δ [°el] in the γ frame
  vBus?: number;    // pwm_voltage: DC link [V]
  fSwitch?: number; // pwm_voltage: carrier [Hz]
  iBlock?: number;  // bldc_current: flat-top block amplitude [A terminal]
  waveform?: string;// custom_current: JSON [[θe_deg, i_A], …] over one period
  // ── GENERATOR → BATTERY (boost mode) ─────────────────────────────────
  // The pack on the DC link, as the plain payload the backend takes.  Sent
  // ONLY on an imposed-voltage run of a machine that has a battery; a request
  // without it is byte-identical to what this component has always sent.
  battery?: {
    v_oc?: number | null; v_nom?: number | null;
    v_min?: number | null; v_max?: number | null;
    cells?: number | null; n_parallel?: number | null;
    r_int_mohm?: number | null; capacity_ah?: number | null;
    i_charge_max_a?: number | null; chemistry?: string | null;
  } | null;
  // Iterate V_bus = V_oc + I_charge·R_pack around the solve (each pass is a
  // full transient) instead of assuming an infinitely stiff supply.
  busCouple?: boolean;
  // One-shot: search (V₁, δ) for the maximum charge power at this rpm, then
  // confirm the winner at the requested resolution.  Consumed by the parent
  // after the run so the button does not latch.
  chargeMax?: boolean;
}

/* `readMeshSetting` / `readSimSetting` lived here until 2026-09-08 and read the
   panel settings into the request literal.  Both moved into `lib/emRunPayload`
   with the literal itself — one place builds the body now. */

const AXIS = { fontSize: 10, fill: 'var(--text-2)' };
// recharts types its formatter callbacks over the ValueType/NameType unions
// (which include undefined), so a `(v: number)` signature does not satisfy
// them and every `<RcTooltip {...TOOLTIP}/>` in this file was a type error.
// The bodies already coerce with Number(), so widening the parameter is the
// honest signature rather than a cast at nine call sites.
const TOOLTIP = {
  contentStyle: { background: 'var(--app-bg)', border: '1px solid var(--line-soft)',
    fontSize: 11, color: 'var(--text-1)' },
  labelFormatter: (v: unknown) => `t = ${Number(v).toFixed(3)} ms`,
  formatter: (v: unknown) => Number(v).toFixed(3),
};
const GRID = { stroke: 'var(--panel)', strokeDasharray: '2 4' };

// Round the x-axis tick label to 3 decimal places (ms).  Without this
// recharts displays the raw floating-point time values with full
// double-precision noise (e.g. "0.07233273056057866").
const fmtMs = (v: number) => Number(v).toFixed(3);

interface ProgressInfo {
  running:   boolean;
  step:      number;
  total:     number;
  elapsed_s: number;
  eta_s:     number;
  per_step_s?: number;
  phase:     string;
}

// ── Persist the last transient run so a page/back-end reload SHOWS it instead
// of recomputing.  runNonce is persisted in localStorage, so the old code re-ran
// the whole FEM solve on every mount; now we load the cached result and only
// compute when the user actually presses Run (runNonce increments post-mount).
const LAST_KEY = 'sim.lastTransient';
// The per-element MAPS, and nothing else, are what makes this payload big:
// measured on the live Ø200 run of 2026-09-13 (config/.last_transient.json),
// 1 125 354 chars of which demag_field + demag_coef_per_tri are 1 091 571 —
// 97 %.  The waveforms every chart and every stored duty run reads are ~33 kB.
const HEAVY_KEYS = ['frames', 'field', 'demag_field', 'demag_coef_per_tri'];
function persistLastTransient(d: TransientPayload) {
  try { localStorage.setItem(LAST_KEY, JSON.stringify(d)); return; }
  catch { /* no room for the maps — fall through */ }
  // A SILENT drop left `sim.lastTransient` holding the PREVIOUS run, and
  // "Save to duty" (ActiveFamilyStrip) refuses to file waveforms it cannot
  // prove belong to the summary it is saving — so the duty kept its numbers
  // and lost its torque / current / voltage charts (user 2026-09-13, twice).
  // Keeping the run WITHOUT its maps costs only the demag view (FemFieldChart
  // re-solves it); keeping a foreign run costs the report.
  try {
    const lean: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(d)) if (!HEAVY_KEYS.includes(k)) lean[k] = v;
    localStorage.setItem(LAST_KEY, JSON.stringify(lean));
    console.warn('[sim] last transient stored WITHOUT its per-element maps '
      + '(localStorage full) — the demagnetisation view will re-solve');
  } catch {
    // Still no room: REMOVE the stale run rather than leave another run's
    // waveforms behind this run's numbers.  The save then takes the run from
    // the backend (which keeps its own copy) instead of from here.
    try { localStorage.removeItem(LAST_KEY); } catch { /* nothing left to do */ }
    console.warn('[sim] last transient could NOT be stored (localStorage full) '
      + '— Save to duty will fetch the run from the backend');
  }
}
function loadLastTransient(): TransientPayload | null {
  try { const s = localStorage.getItem(LAST_KEY); return s ? JSON.parse(s) : null; }
  catch { return null; }
}

// (live recompute progress strip: elapsed + points, driven by busy + /progress)
const TransientCharts: React.FC<Props> = ({ gamma_deg = 0, I_phase_rms = 85, onSummary, runNonce = 0, onBusyChange, steps = 12, fresh = false, fieldLosses = true, demag = false, appliedFromSweep = false, drive = 'current', vPeak = 0, vDelta = 0, vBus = 0, fSwitch = 0, iBlock = 0, waveform = '', eddyCoupled = true, battery = null, busCouple = false, chargeMax = false }) => {
  // `steps` (n_steps_per_period) is controlled from the left panel and
  // matches the animation viewer's n_frames so both hit the same backend
  // cache key (one solve, not two).
  const [data,  setData]  = useState<TransientPayload | null>(null);
  const [busy,  setBusy]  = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  // …and publish every change of it to the Run button.  This panel renders
  // `error` where the waveforms are — thousands of pixels BELOW the button that
  // launched the run, so a refused solve (live 2026-09-16: the coupled loop's
  // 422 on an S3 duty) looked on screen like Run doing nothing at all.  One
  // event, fired from the single place the state lives, so every path that sets
  // it — a refusal, a dead backend, a retry, a clear at the start of a run —
  // reaches the rail without a second set of call sites to keep in sync.
  // SimulationPanel renders `lib/runNotice.runNoticeFor(message)`.
  // …and the same rail carries the coupled loop's OUTCOME when there is one to
  // report (2026-09-16): on an S2/S3 duty the loop finds the regime the machine
  // can hold, and "the 25 % this duty asks for does not fit under 21.6 %" is an
  // answer the user must see where they clicked — not a failure (an error wins
  // the rail when there is one), and not a line buried in a tooltip.
  const [regimeNotice, setRegimeNotice] = useState<string | null>(null);
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('sim:run-notice',
      { detail: { message: error ?? regimeNotice } }));
  }, [error, regimeNotice]);
  const [progress, setProgress] = useState<ProgressInfo | null>(null);
  // True when the shown result was RESTORED on open but its params differ from
  // the current inputs (the backend flagged it stale) — a hint to press Run.
  const [stale, setStale] = useState<boolean>(false);
  // "Loaded from history — computed …" for the COUPLED half of a run
  // (2026-09-22): `POST /api/coupled/run`'s own `served_from_history` /
  // `computed_at` live on the TOP-LEVEL response (`res`, below), not inside
  // `res.coupling` — the coupling block written into the summary is the same
  // object whether solved or loaded, so it carries no stamp of its own.  Kept
  // here, next to `stale`, rather than folded into `data`: it describes the
  // COUPLED call this run made, and a plain (uncoupled) run must not show it.
  const [coupledHistory, setCoupledHistory] =
    useState<{ computed_at: string } | null>(null);

  // GEOMETRY staleness.  The shown result is stamped (in run()) with the
  // geometry it was solved for.  When the live geometry differs — e.g. after
  // applying a design from the Sweep/Optimization tab, or after loading another
  // preset and reloading the page — the result is stale even though the
  // operating point is unchanged.  TWO independent witnesses now, because the
  // restore path has two mouths: the localStorage copy carries `_geoSig` (this
  // client's own stamp) and the backend's persisted last transient carries
  // `stale_geometry` (its own fingerprint of the machine it solved).  Either one
  // saying "different motor" is enough — a stale machine must never need both.
  const geometry = useMotorStore(s => s.geometry);
  const geoSig = useMemo(() => geoSignature(geometry as Record<string, unknown>),
                         [geometry]);
  const geoStale = !!data && (
    (data._geoSig != null && data._geoSig !== geoSig)
    || data.stale_geometry === true);

  // Poll the backend /progress endpoint so we can show a live "Computing X/N
  // points — Ys elapsed" strip.  Polled CONTINUOUSLY while mounted — NOT gated
  // on the frontend `busy` flag.  The transient solve can be launched by THIS
  // panel OR by the field/animation viewer (they share one backend solve), and
  // the backend's `running` flag is the single source of truth for whether a
  // solve is in flight.  Cadence backs off to 1.5 s when idle to stay cheap;
  // tightens to 350 ms while a solve is running so the counter advances live.
  useEffect(() => {
    let alive = true;
    let misses = 0;
    let timer = 0;
    const tick = async () => {
      if (!alive) return;
      await whenVisible();              // a hidden tab polls nothing (lib/pageVisible)
      if (!alive) return;
      try {
        const r = await fetch(`${API}/api/simulation/physics/fem_transient/progress`);
        if (r.ok) {
          const p: ProgressInfo = await r.json();
          if (alive) {
            // Keep the previous object when nothing the strip shows has
            // changed: an idle backend answers the same `running: false`
            // record every 1.5 s, and a fresh object each time re-rendered
            // this whole panel — ten recharts plots, collapsed or not — for
            // as long as the tab was open (2026-09-13: ~80 000 "width(0)"
            // chart warnings in one afternoon's console, one per idle
            // re-render of a hidden chart).  Same identity → React bails out.
            setProgress(prev => (prev
              && prev.running === p.running && prev.step === p.step
              && prev.total === p.total && prev.phase === p.phase
              && prev.elapsed_s === p.elapsed_s && prev.eta_s === p.eta_s)
              ? prev : p);
            misses = p.running ? 0 : Math.min(misses + 1, 99);
          }
        }
      } catch {/* ignore polling errors */}
      if (alive) timer = window.setTimeout(tick, misses > 3 ? 1500 : 350);
    };
    tick();   // immediate first read
    return () => { alive = false; window.clearTimeout(timer); };
  }, []);

  // A solve is in flight if EITHER this panel's own fetch is busy OR the backend
  // reports a transient running (covers the field/animation-viewer-triggered
  // solve, and survives the frontend busy flag being flaky in dev StrictMode).
  const solving = busy || !!progress?.running;

  // Local wall-clock so "elapsed" advances SMOOTHLY in real time (every 200 ms)
  // — the backend /progress poll only refreshes every 500 ms and not until the
  // solve loop starts, so on its own it can't show a live ticking timer.  This
  // is what makes the recompute visibly "running" the instant Run is pressed.
  const [solveElapsed, setSolveElapsed] = useState(0);
  const solveStartRef = useRef(0);
  useEffect(() => {
    if (!solving) { setSolveElapsed(0); return; }
    solveStartRef.current = performance.now();
    setSolveElapsed(0);
    const id = window.setInterval(
      () => setSolveElapsed((performance.now() - solveStartRef.current) / 1000), 200);
    return () => window.clearInterval(id);
  }, [solving]);

  const abortRef = useRef<AbortController | null>(null);

  // "Stop Simulation" (left panel) dispatches a window event — abort the
  // in-flight fetch and tell the backend to cancel THIS run_id (so its
  // animation twin sharing the run_id is cancelled too, but the next run
  // isn't).
  useEffect(() => {
    const onStop = () => {
      abortRef.current?.abort();
      fetch(`${API}/api/simulation/physics/fem_transient/cancel?run_id=${runNonce}`,
        { method: 'POST' }).catch(() => {});
      // …and the LOOP, when one is running: aborting this fetch stops the
      // browser waiting, not the six transients the orchestrator still intends
      // to solve.  Its cancel is keyed by the same run-id and sets the
      // transient's registry too, so the frame march in flight stops as well.
      cancelCoupled(runNonce);
      setBusy(false);
      setError('Cancelled.');
    };
    window.addEventListener('sim:stop', onStop);
    return () => window.removeEventListener('sim:stop', onStop);
  }, [runNonce]);

  // `freshOnce` = the card's "Recompute" link: force ONE solve past the results
  // ledger without turning the panel's "Start fresh" state on (that one belongs
  // to the Stop/Continue dialog and would then stick to every later Run).
  const run = (restoreOnly = false, freshOnce = false) => {
    setBusy(true); setError(null); setRegimeNotice(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    // THE REQUEST BODY — built in `lib/emRunPayload`, not here.  It lived in
    // this function as a 130-line literal until 2026-09-08, when the Thermal tab
    // was given the right to make the missing Electromagnetic run for itself
    // (through the orchestrator): two places building "the same" payload is two
    // places for the run that gets MADE to stop being the run that gets LOOKED
    // FOR.  Everything it reads from `mesh.*` / `sim.*` it reads exactly as this
    // function did, so the body is unchanged.
    const p: Record<string, unknown> = buildEmRunPayload({
      restore: restoreOnly,
      steps, gamma_deg, I_phase_rms,
      drive, vPeak, vDelta, vBus, fSwitch, iBlock, waveform,
      battery, busCouple, chargeMax,
      fieldLosses, eddyCoupled, demag, torqueFilter: false,
      fresh: fresh || freshOnce,
      run_id: String(runNonce),
    });
    // Helper: fetch with auto-retry against transient connection drops.
    // The uvicorn supervisor sometimes respawns the worker mid-request when
    // a heavy FEM solve crashes the LLVM JIT; without a retry the user sees
    // a permanent "Failed to fetch" until they click Re-run manually.
    // THE COUPLED TOGGLE, and the only thing it changes: where this request
    // goes.  Never on a RESTORE — that path must not solve anything at all, and
    // a mount is not a Run.
    const useCoupled = !restoreOnly && coupledEnabled();
    const attempt = async (i = 0): Promise<void> => {
      try {
        let d: TransientPayload & { restored?: boolean; stale?: boolean };
        if (useCoupled) {
          // POST /api/coupled/run — the SAME payload, iterated: EM run ->
          // thermal solve -> winding / magnet averages -> back into the EM run's
          // temperatures, until both settle.  What comes back carries the LAST
          // electromagnetic run's own payload, so everything below this branch
          // treats it exactly as it treats a plain Run: same charts, same
          // summary (which now also carries the `coupling` block), same
          // localStorage copy, same "field snapshot moved" event.
          const res = await runCoupled(p, ctrl.signal);
          d = (res.transient || {}) as typeof d;
          // "Loaded from history — computed …" for the COUPLED call itself
          // (2026-09-22): an IDENTICAL coupled request — same machine, same
          // operating point, same cooling — was already in `run_history` and
          // was handed back rather than re-iterated.  `fresh || freshOnce` is
          // already inside `p`, so the SAME Recompute link the EM notice uses
          // (below) forces this fresh too; no second control needed.
          setCoupledHistory(res.served_from_history && res.computed_at
            ? { computed_at: res.computed_at } : null);
          // What the loop found about this duty's CYCLE, when it is one: `null`
          // on a continuous duty and on a ratio that fits, which is what "no
          // news" looks like on the rail.
          setRegimeNotice(coupledRegimeNotice(res.coupling));
          // The two temperatures this run SOLVED for go back into the fields
          // they came from — leaving them showing the guess the loop started
          // from would leave an input on screen that the run did not use.
          adoptConvergedTemperatures(
            res.coupling, couplingStamp(res.coupling, d.computed_at));
        } else {
          if (!restoreOnly) setCoupledHistory(null);   // plain run — no coupled call made
          // ALWAYS through the modular kernel (POST /api/kernel/run, capability
          // solver.em_transient). The kernel -> get_fem_transient -> em_transient_eval
          // (the same solver), so results are identical to the old direct route.
          // result.raw is the transient payload (frames are dropped by the IR —
          // ignored here; the animation viewer fetches frames directly). Progress /
          // cancel / cache / restore are shared global backend state.
          const r = await fetch(`${API}/api/kernel/run`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ capability: 'solver.em_transient', payload: p }),
            signal: ctrl.signal });
          if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
          const j = await r.json();
          if (!j.ok) throw new Error(j.error || 'kernel solve failed');
          // TWO envelopes, and only the outer one was checked.  A refused solve —
          // a 422 from the buildability gate, a solver exception — comes back as
          // HTTP 200 with j.ok TRUE and the failure inside j.result.ok/.error.
          // The panel then read `raw` (null), fell through to the "no waveform"
          // branch or simply left the old numbers on screen: pressing Run looked
          // like nothing happened at all.  The reason has to reach the user.
          if (j.result && j.result.ok === false) {
            throw new Error(String(j.result.error || 'solve refused').replace(/^HTTPException: \d+: /, ''));
          }
          d = (j.result && j.result.raw) || {};
        }
        // restore=true with nothing ever saved → backend returns {restored:false}.
        // Leave the panel empty (the "press Run" prompt) — do NOT recompute, NOT
        // an error.  This is the ONLY legitimate empty payload.
        if (d.restored === false) { setBusy(false); setError(null); return; }
        // A real Run (or a restore of a saved run) that came back 200 OK but with
        // no usable waveform is a FAILED solve, not an empty state — surface it
        // instead of silently keeping the previous (stale) numbers.
        if (!d.time_s || !d.time_s.length) {
          setBusy(false);
          setError('Solve returned no data — see backend logs');
          return;
        }
        // Backend built the waveforms but the summary-block build threw: the cards
        // would otherwise freeze on the previous run's values with no warning.
        if (d.summary_error) {
          setBusy(false);
          setError(`Summary unavailable: ${d.summary_error}`);
          return;
        }
        // Stamp a FRESH run with the geometry AND the material assignment it
        // was computed for, so a later change (applying a Sweep design; picking
        // another steel, another magnet temperature, another insulation —
        // whether on the machine or on the active duty) flags it stale.  The
        // material stamp is taken from the request that was actually sent
        // (`p.mat`), not re-read afterwards, so it describes THIS solve.  It
        // covers the parts that have no mass row of their own (the liner, the
        // enamel), which name-matching on the mass rows cannot see.  A RESTORED
        // result keeps whatever stamp it was saved with.
        const stamped: TransientPayload = restoreOnly ? d : {
          ...d, _geoSig: geoSig,
          _matSig: assignmentSignature((p as { mat?: string }).mat) || undefined,
        };
        setStale(!!d.stale);
        if (restoreOnly && d.stale_geometry === true) {
          // Loud in the console too: a restored run from another machine is the
          // exact situation where the user reads the numbers before the banner.
          console.warn('[stale] restored transient was solved on a DIFFERENT '
            + 'machine (backend fingerprint mismatch) — shown flagged, press Run '
            + 'to recompute on the current geometry');
        }
        setData(stamped); setBusy(false);
        setError(null);
        persistLastTransient(stamped);      // remember it (+ stamp) across reloads
        // A RESTORED coupled run must leave the temperature FIELDS describing
        // what the card shows (2026-09-09) — the live path above adopts them,
        // a reload never went through it.  Once per answer: `couplingStamp`.
        if (restoreOnly) {
          const cpl = (stamped.summary as { coupling?: CouplingBlock } | undefined)?.coupling;
          if (cpl && Number.isFinite(Number(cpl.coil_temp_c))) {
            const st = couplingStamp(cpl, stamped.computed_at);
            if (!couplingAdopted(st)) adoptConvergedTemperatures(cpl, st);
          }
        }
        // ── What this run COST, measured, for the Run panel's estimate ──────
        // Only from a FRESH solve: a restored/cached result reports the wall
        // time of the run it was saved from, and reusing that as "seconds per
        // frame" would quote the user a rate no solve on this machine produced.
        // …and a LEDGER hit is exactly that case: it carries the wall time of
        // the run it was stored from, and no solve happened just now.
        if (!restoreOnly && !d.ledger_hit
            && (d.n_frames_solved ?? 0) > 0 && (d.solve_wall_s ?? 0) > 0) {
          const cost = { frames: d.n_frames_solved as number,
                         wall_s: d.solve_wall_s as number,
                         // Warm-up is ADAPTIVE (settle-until-quiet), so the
                         // pre-run estimate cannot compute it — it quotes the
                         // count this machine last needed.
                         warm: d.eddy_warmup_frames ?? 0,
                         at: Date.now() };
          try { localStorage.setItem('sim.lastSolveCost', JSON.stringify(cost)); }
          catch { /* quota — the estimate just falls back to "no history yet" */ }
          window.dispatchEvent(new CustomEvent('sim:solve-cost', { detail: cost }));
        }
        // This run kept its last frame's field server-side (field_snapshot), so
        // the J⟳ / Loss views can now render THIS run's field instead of solving
        // their own.  Tell them the snapshot moved; whatever they are holding is
        // from an older operating point.  `field_snapshot` says whether the
        // backend actually kept one (a RESTORED result carries no fresh field).
        if (!restoreOnly) {
          window.dispatchEvent(new CustomEvent('sim-transient-done', {
            detail: { field_snapshot: (d as any).field_snapshot === true,
                      eddy: (d as any).field_snapshot_eddy === true } }));
        }
        // The effect below emits the summary with the raw curve's ripple.
      } catch (e: any) {
        const msg = String(e);
        // User pressed Stop → don't retry, don't surface as an error.
        if (ctrl.signal.aborted || /abort/i.test(msg)) { setBusy(false); return; }
        const isNetwork = /Failed to fetch|NetworkError|TypeError/i.test(msg);
        // A backend restart takes ~15-20 s to come back (watchdog task), and a
        // solve that died with it is NOT recoverable — the retry re-submits
        // and the backend re-solves from scratch (cache miss), which is the
        // honest recovery.  The old 4×2 s retry window was SHORTER than a
        // restart, so the user got a permanent "Failed to fetch" and a summary
        // silently frozen on the previous run — reported many times.
        if (isNetwork && i < 8) {
          setError(`Backend connection lost — the running solve died with it; `
                 + `reconnecting and re-solving (attempt ${i + 2}/9)…`);
          setTimeout(() => attempt(i + 1), Math.min(15000, 2000 + i * 2000));
        } else {
          setError(isNetwork
            ? 'Backend connection lost mid-solve — the run DIED with a server '
            + 'restart and nothing was updated. Press Re-run Simulation.'
            : msg);
          setBusy(false);
        }
      }
    };
    attempt();
  };

  // ── History popover: EM ledger (2026-09-22) ─────────────────────────────
  // Lists/loads/deletes rows of the SAME `.run_ledger` store the "Loaded from
  // history" notice above already reads (`served_from_history`/`computed_at`
  // on `data`) — see the module note on `routes/simulation.py`'s
  // `/ledger/recent` for why this is a second listing rather than the
  // generic `/api/history` one every other panel's popover would use.
  const listLedgerHistory = useCallback(async (): Promise<HistoryRow[]> => {
    const r = await fetch(`${API}/api/simulation/ledger/recent?limit=10`);
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    return (j.entries || []) as HistoryRow[];
  }, []);
  // A load is shaped exactly like a ledger-hit `attempt()` would have
  // produced (`ledger_hit`/`served_from_history`/`computed_at`/`stale:
  // false`, already set by the backend) — applied the same way a restore
  // adopts a persisted result, minus the geometry/material stamp: an entry
  // picked from history may not describe what is live NOW, so `_geoSig` /
  // `_matSig` are left unset, exactly as an old (pre-stamp) restored result
  // is — "unknown", which `geoStale`/`stale` above read as "say nothing",
  // never as "fine" (the backend's own `stale_geometry` on the payload is
  // the one witness that CAN speak here, and it does: see `/ledger/{key}
  // /load`'s docstring).
  const loadLedgerHistory = useCallback(async (key: string) => {
    const r = await fetch(
      `${API}/api/simulation/ledger/${encodeURIComponent(key)}/load`,
      { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json() as TransientPayload;
    if (!d.time_s || !d.time_s.length) {
      throw new Error('stored entry has no waveform data');
    }
    setCoupledHistory(null);      // a ledger row is an EM-only record
    setStale(false);
    setData(d); setBusy(false); setError(null);
    persistLastTransient(d);
    // The dashboard's "different point" verdict compares the PANEL's own
    // current/γ/rpm fields against the shown summary (SummaryTable's
    // `opStale`) — sync them to what this row was actually solved at, same
    // setter path the S1 auto-set uses, so a loaded point never reads as
    // stale against itself.
    applyLoadedOperatingPoint({
      current: d.summary?.I_terminal_rms_A ?? d.summary?.I_phase_rms_A,
      gamma_deg: d.summary?.gamma_deg, rpm: d.summary?.rpm,
    });
  }, []);
  const deleteLedgerHistory = useCallback(async (key: string) => {
    const r = await fetch(`${API}/api/simulation/ledger/${encodeURIComponent(key)}`,
      { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
  }, []);

  // ── History popover: the COUPLED result (2026-09-22) ────────────────────
  // The generic, `run_history`-backed store every other panel's history
  // already lives in (`GET/POST/DELETE /api/history`, kind `coupled.run`,
  // `routes/coupled.py`'s `_COUPLED_HISTORY`/`_load_coupled_history_entry`) —
  // unlike the EM ledger above, this one needs NO dedicated endpoints.
  const listCoupledHistory = useCallback(async (): Promise<HistoryRow[]> => {
    const r = await fetch(`${API}/api/history?kind=coupled.run&limit=10`);
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    return (j.kinds?.['coupled.run'] || []) as HistoryRow[];
  }, []);
  // The loaded response is the WHOLE `/run` payload — applied exactly as the
  // live coupled branch of `attempt()` applies one (same regime notice, same
  // converged-temperature adoption, same charts) — minus the geometry stamp,
  // for the same "unknown, not fine" reason `loadLedgerHistory` leaves it
  // unset above.
  const loadCoupledHistory = useCallback(async (key: string) => {
    const r = await fetch(
      `${API}/api/history/${encodeURIComponent(key)}/load?kind=coupled.run`,
      { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const res = await r.json() as CoupledRunResult;
    const d = (res.transient || {}) as TransientPayload;
    if (!d.time_s || !d.time_s.length) {
      throw new Error('stored entry has no waveform data');
    }
    setCoupledHistory(res.served_from_history && res.computed_at
      ? { computed_at: res.computed_at } : null);
    setRegimeNotice(coupledRegimeNotice(res.coupling));
    adoptConvergedTemperatures(
      res.coupling, couplingStamp(res.coupling, d.computed_at));
    setStale(false);
    setData(d); setBusy(false); setError(null);
    persistLastTransient(d);
    // Same sync as the ledger load above — see its comment.
    applyLoadedOperatingPoint({
      current: d.summary?.I_terminal_rms_A ?? d.summary?.I_phase_rms_A,
      gamma_deg: d.summary?.gamma_deg, rpm: d.summary?.rpm,
    });
  }, []);
  const deleteCoupledHistory = useCallback(async (key: string) => {
    const r = await fetch(
      `${API}/api/history/${encodeURIComponent(key)}?kind=coupled.run`,
      { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
  }, []);

  // On MOUNT (page/back-end reload): show the last run from localStorage rather
  // than recomputing.  After mount, a runNonce CHANGE means the user pressed Run
  // → recompute (and overwrite the cache).  runNonce is persisted, so without
  // this guard every reload re-ran the whole FEM solve.
  const mountedRef = useRef(false);
  const handledNonceRef = useRef(runNonce);   // the runNonce we've already acted on
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      handledNonceRef.current = runNonce;      // remember the mount nonce — never recompute it
      const last = loadLastTransient();
      if (last) {
        setData(last);                       // localStorage copy → show it, no compute
        // …but the SUMMARY may be stale in SHAPE: the backend rebuilds stored
        // summaries when the code gains new derived cells (Km, inertia, demag,
        // saturation — summary_shape_v), and the local copy freezes whatever
        // shape existed when the run was made.  Ask the backend's restore path
        // (no compute, milliseconds) and adopt ITS summary when it is newer —
        // waveforms, _geoSig and stale flags stay local.  Without this, new
        // summary cells only ever appeared after a full re-run ("не вижу его
        // после Rotor inertia", 2026-08-23).
        (async () => {
          try {
            const q = new URLSearchParams({ restore: 'true' });
            const r = await fetch(`${API}/api/simulation/physics/fem_transient?${q}`);
            if (!r.ok) return;
            const d = await r.json();
            const vLocal = Number(last.summary?.summary_shape_v ?? 1);
            const vBack = Number(d?.summary?.summary_shape_v ?? 1);
            // Adopt ONLY when the backend is describing THE SAME solve (its
            // rebuilt summary can then be richer: newer shape, or a Stage-A
            // passport measured after the run).  Without the same-run guard a
            // server that regressed to an OLDER persisted run (measured live:
            // a restart lost the in-memory last) overwrote a newer local
            // run's summary — the user's γ=2 Ld vanished under a γ=0 card.
            const sameRun = !!d?.computed_at && d.computed_at === (last as any)?.computed_at;
            const richer = sameRun
              && (vBack > vLocal
                  || (vBack === vLocal && d?.summary?.end3d != null
                      && last.summary?.end3d == null));
            // Backend holds a NEWER run than this browser copy (solved in
            // another tab, or the response was lost to a mid-run reload):
            // adopt it WHOLE — waveforms and summary together, never a
            // frankenstein of one run's charts under another run's cards.
            const backNewer = !!d?.computed_at && !!(last as any)?.computed_at
              && !sameRun && String(d.computed_at) > String((last as any).computed_at)
              && !!d?.time_s?.length && !!d?.summary;
            if (backNewer) {
              setData(prev => (prev === last || prev == null) ? d : prev);
              // …and REMEMBER it: the local copy is what "Save to duty" files
              // as the duty's waveforms (ActiveFamilyStrip's same-run check
              // reads sim.lastTransient).  Adopting a newer backend run on
              // screen without persisting it left the old run in localStorage,
              // the save found no matching waveforms, and the backend kept the
              // duty's previous sidecar under today's summary (2026-09-13: a
              // 2-day-old STAR run's line voltages printed in the report of
              // today's delta duty).
              persistLastTransient(d);
            } else if (d?.summary && richer) {
              setData(prev => (prev === last || prev == null)
                ? { ...last, summary: d.summary } : prev);
              persistLastTransient({ ...last, summary: d.summary });
            }
          } catch { /* offline — the local copy stands */ }
        })();
        return;
      }
      run(true);   // none locally → ask the backend for its persisted last (restore=true, no compute)
      return;
    }
    // Recompute ONLY when the user actually presses Run (runNonce INCREMENTS).
    // Guard on "changed" (not ">0"): React StrictMode double-invokes the mount
    // effect, and on the 2nd invoke mountedRef is already true — a ">0" guard
    // there recomputed on every reload (the bug: reload silently re-ran the FEM).
    if (runNonce !== handledNonceRef.current) {
      handledNonceRef.current = runNonce;
      setStale(false); run();                  // user pressed Run → recompute fresh
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runNonce]);

  // Report busy state up to the Run button.
  useEffect(() => { onBusyChange?.(busy);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);

  // CROSS-TAB sync: another tab's finished run writes sim.lastTransient — adopt
  // it live.  Without this a background tab silently keeps showing its old
  // result ("пересчиталось, но не обновилось" when two app tabs are open: the
  // solve lands only in the tab whose Run button was pressed).  The `storage`
  // event fires only in OTHER tabs (never the writer), so there is no loop; a
  // tab that is mid-solve keeps its own run (busyRef guard).
  const busyRef = useRef(false);
  useEffect(() => { busyRef.current = busy; }, [busy]);

  // A backend "DIFFERENT MACHINE" verdict on a RESTORED run is re-asked, never
  // frozen.  2026-09-13: a page reload landed in a ~5-minute window after an
  // API restart in which the server's live fingerprint transiently differed;
  // the restore came back `stale_geometry: true`, the dashboard dimmed to 35 %
  // and STAYED so for a quarter of an hour although the very next lookup would
  // have said "same machine" ("если каплинг завершился, почему у меня экран
  // замыленный?").  The verdict is a function of the geometry the client sends
  // and of the server's state at that instant — both move — so while it says
  // "different", ask again: 3 s, 15 s, 60 s, then every 2 min; at once when
  // the loaded geometry changes.  A restore is a lookup (milliseconds, no
  // solve), and a legitimately foreign run just keeps its banner.
  const staleRecheckRef = useRef<{ n: number; sig: string }>({ n: 0, sig: '' });
  useEffect(() => {
    const restored = !!data && (data as { restored?: boolean }).restored === true;
    if (!restored || data?.stale_geometry !== true) {
      staleRecheckRef.current = { n: 0, sig: geoSig };
      return;
    }
    if (staleRecheckRef.current.sig !== geoSig) staleRecheckRef.current = { n: 0, sig: geoSig };
    const n = staleRecheckRef.current.n;
    const delay = [3000, 15000, 60000][n] ?? 120000;
    const t = setTimeout(() => {
      if (busyRef.current) return;              // a solve in flight owns the panel
      staleRecheckRef.current = { n: n + 1, sig: geoSig };
      run(true);
    }, delay);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, geoSig]);
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== LAST_KEY || !e.newValue || busyRef.current) return;
      try {
        const d = JSON.parse(e.newValue) as TransientPayload;
        if (d?.time_s?.length) { setData(d); setStale(false); setError(null); }
      } catch { /* corrupt entry — ignore */ }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  // SAME-TAB restore: a duty's STORED RUN was selected (▶ in the catalog, or
  // the run selector switching sine → PWM).  The `storage` event above fires
  // only in OTHER tabs, so without this the charts kept the previous run while
  // the summary card had already switched — the exact frankenstein the restore
  // path everywhere else is careful to avoid.  Nothing is solved: the payload
  // is a run that finished minutes or days ago (lib/dutyRuns.ts).
  useEffect(() => {
    const onRestored = (e: Event) => {
      if (busyRef.current) return;            // a solve in flight owns the panel
      const d = (e as CustomEvent).detail?.payload as TransientPayload | undefined;
      if (!d?.time_s?.length) return;
      setData(d); setStale(false); setError(null);
    };
    window.addEventListener('sim-transient-restored', onRestored as EventListener);
    return () => window.removeEventListener('sim-transient-restored', onRestored as EventListener);
  }, []);

  // Build chart-friendly row arrays
  // Always show raw samples, including when restoring a legacy filtered run.
  const Tshown = React.useMemo(() => {
    if (!data) return [] as number[];
    return data.T_em_raw_Nm ?? data.T_em_Nm ?? [];
  }, [data]);

  const rows = React.useMemo(() => {
    if (!data) return [];
    const ms = data.time_s.map(t => t * 1e3);
    return ms.map((t, i) => ({
      t_ms:  t,
      T_em:  Tshown[i],
      P_cu:  data.P_cu_W[i],
      // Flat DC (I²R) copper line — the gap up to P_cu is the AC eddy/proximity share.
      P_cu_dc: data.P_cu_dc_W ?? undefined,
      P_fe:  data.P_fe_W[i],
      P_mag: data.P_mag_eddy_W[i],
      P_shaft: (data.P_shaft_eddy_W ?? [])[i] ?? 0,
      P_tot: data.P_loss_total_W[i],
      I_A:   data.I_A[i], I_B: data.I_B[i], I_C: data.I_C[i],
      V_A:   data.V_A[i], V_B: data.V_B[i], V_C: data.V_C[i],
      // Line-to-line voltages — what a wye-connected inverter actually applies.
      // Zero-sequence (triplen) content cancels in the differences by physics;
      // any SHAPE difference between the three pairs is phase unbalance.
      V_AB:  (Number.isFinite(data.V_A[i]) && Number.isFinite(data.V_B[i]))
               ? data.V_A[i] - data.V_B[i] : 0,
      V_BC:  (Number.isFinite(data.V_B[i]) && Number.isFinite(data.V_C[i]))
               ? data.V_B[i] - data.V_C[i] : 0,
      V_CA:  (Number.isFinite(data.V_C[i]) && Number.isFinite(data.V_A[i]))
               ? data.V_C[i] - data.V_A[i] : 0,
      // What the SOURCE applied at this step — the inverter's pole voltage
      // under PWM, the commanded current for the imposed-current sources
      // (where the solver ships no arrays because the current IS the applied
      // quantity).  Same index as everything else in the row, so the ripple in
      // the torque line above sits under the switching edge that caused it.
      exc_A: data.excitation?.A?.[i] ?? data.I_A[i],
      exc_B: data.excitation?.B?.[i] ?? data.I_B[i],
      exc_C: data.excitation?.C?.[i] ?? data.I_C[i],
    }));
  }, [data, Tshown]);

  // The excitation chart's labelling — and whether it earns a chart at all.
  // The two IDEAL sinusoids do not: their applied waveform is already the
  // current chart (current drive) or the voltage chart (voltage drive), and a
  // third copy of a sine is not information.
  const exc = React.useMemo(() => {
    const k = data?.drive;
    if (k === 'pwm_voltage') {
      const p = data?.pwm;
      return {
        title: 'Applied inverter pole voltages (what the circuit integrated)',
        sub: p ? `  ·  ${(p.f_switch_eff_Hz / 1000).toFixed(1)} kHz carrier · `
                 + `${p.steps_per_switching_period.toFixed(1)} steps/switching period` : '',
        unit: 'V', prefix: 'V_', stepped: true,
        help: 'The pole voltage each leg applied, averaged EXACTLY over each solve step — '
          + 'that is the volt-seconds the line-to-line circuit integrated, so the ripple it '
          + 'causes in the current and torque charts above lines up with these edges by '
          + 'construction. It is not the carrier-resolution waveform: drawing pulses the run '
          + 'never solved would be a picture of a different simulation. The common mode (large '
          + 'on two-level PWM) falls on the floating neutral and drives no current.',
      };
    }
    if (k === 'bldc_current' || k === 'custom_current') {
      return {
        title: k === 'bldc_current'
          ? 'Commanded block current (what the source imposed)'
          : 'Commanded waveform current (what the source imposed)',
        sub: k === 'bldc_current' && data?.bldc
          ? `  ·  ${data.bldc.i_block_A.toFixed(1)} A flat top · `
            + `${data.bldc.commutation_ramp_deg.toFixed(1)}°el commutation ramp` : '',
        unit: 'I [A per branch]', prefix: 'I_', stepped: false,
        help: k === 'bldc_current'
          ? 'The 120° block as the solver sampled it, one value per solve step. Each '
            + 'commutation is ramped over exactly one time step — an ideal block has infinite '
            + 'di/dt, which is not a source a time-marched solve can accept — so a finer run '
            + 'gives a sharper edge rather than a different machine. The three phases sum to '
            + 'zero throughout, ramps included.'
          : 'The imposed waveform as the solver sampled it, one value per solve step: the '
            + 'linear interpolation of the pasted samples at the angles actually solved. If '
            + 'this looks coarser than what you pasted, the run has fewer steps than your '
            + 'waveform has detail.',
      };
    }
    return null;
  }, [data]);

  // ── carrier-resolution pulse train, expanded for drawing ──────────────
  // The payload is EDGES; a step line needs a point at every edge, and the
  // dashed fundamental needs points between them.  So: the union of the edge
  // instants and a uniform grid, each row carrying the step value in force at
  // that instant and the fundamental evaluated there.  The expansion is
  // client-side and disposable — nothing dense is ever persisted.
  const waveA = React.useMemo(() => {
    const w = data?.pwm?.wave_AB;
    if (!w || !w.t_s?.length) return null;
    const times = new Set<number>(w.t_s);
    const GRID = 720;
    for (let i = 0; i <= GRID; i++) times.add((w.t_end_s * i) / GRID);
    const xs = Array.from(times).sort((a, b) => a - b);
    let j = -1;
    const rows = xs.map(t => {
      while (j + 1 < w.t_s.length && w.t_s[j + 1] <= t + 1e-15) j += 1;
      const ang = (360 * w.f_elec_Hz * t + w.v1_ll_phase_deg) * Math.PI / 180;
      return {
        t_ms: t * 1e3,
        v_pwm: j >= 0 ? w.v[j] : 0,
        v1: w.v1_ll_peak_V * Math.cos(ang),
      };
    });
    return { rows, w };
  }, [data]);

  // ── DC-link current, per solve step ───────────────────────────────────
  // Straight from the payload: one value per solved step, already on the same
  // grid as `rows`, so it is zipped onto the run's own time axis rather than
  // resampled.  The mean line is drawn beside it because the mean IS the
  // charge current — the ripple around it is what the pack's capacitor sees.
  const dcRows = React.useMemo(() => {
    const d = data?.pwm?.dc_link;
    const ts = data?.time_s;
    if (!d?.I_dc_A?.length || !ts?.length) return null;
    const n = Math.min(d.I_dc_A.length, ts.length);
    const t0 = ts[0] ?? 0;
    const out = [];
    for (let i = 0; i < n; i++) {
      out.push({ t_ms: (ts[i] - t0) * 1e3, i_dc: d.I_dc_A[i],
                 i_mean: d.I_dc_mean_A });
    }
    return { rows: out, d };
  }, [data]);

  // ── DC-link VOLTAGE, boost mode (user 2026-09-02) ─────────────────────
  // The pack is the bus: v_dc(t) = V_oc − R_pack·i_dc(t) with the run's own
  // V_oc and R_pack (summary.battery_charge), on the same per-step grid as
  // i_dc.  i_dc > 0 draws from the pack (terminal sags), < 0 charges it
  // (terminal rises) — the mean of this curve IS the V_bus the charging card
  // reports.  An optional bus capacitor C_dc (a property of the inverter, not
  // of the machine — typed here, remembered per browser) filters the ripple:
  // R·C·dv/dt + (v − V_oc) = −R·i_dc(t), solved exactly step by step (i_dc is
  // a per-step mean) and closed to its periodic steady state.  The mean does
  // not move with C; only the ripple does.
  // Empty field = the TYPICAL capacitor for this bus and power (user
  // 2026-09-02): film-cap rules of thumb per voltage class — ≈4 µF/kW for the
  // 800 V SiC class, ≈10 µF/kW at 400 V, ≈60 µF/kW electrolytic below 100 V
  // (ESC) — floored at 50 µF and snapped to the 1-2-5 series.  Type 0 for the
  // bare pack, any other number for a known inverter.
  const [cdcUF, setCdcUF] = useState<string>(() => {
    try { return localStorage.getItem('sim.dcLinkCapUF') ?? ''; } catch { return ''; }
  });
  useEffect(() => {
    try { localStorage.setItem('sim.dcLinkCapUF', cdcUF); } catch { /* ignore */ }
  }, [cdcUF]);
  // Cable + pack inductance between the pack and the bus capacitor (user
  // 2026-09-02: "the ripple only shifted" — with zero inductance a 25 mΩ pack
  // is stiffer than any capacitor, which is the unrealisable case).  Empty =
  // typical 2 µH (≈1 µH per metre of twisted DC harness plus busbar/pack
  // inductance); 0 = ideal, pack directly on the capacitor.
  const [lcUH, setLcUH] = useState<string>(() => {
    try { return localStorage.getItem('sim.dcLinkCableUH') ?? ''; } catch { return ''; }
  });
  useEffect(() => {
    try { localStorage.setItem('sim.dcLinkCableUH', lcUH); } catch { /* ignore */ }
  }, [lcUH]);
  const TYPICAL_L_UH = 2;
  // Excitation chart: one phase at a time to read the pole voltage against the
  // winding's own phase voltage (user 2026-09-02), or all three.
  const [excOnlyA, setExcOnlyA] = useState<boolean>(() => {
    try { return localStorage.getItem('sim.excOnlyA') === '1'; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem('sim.excOnlyA', excOnlyA ? '1' : '0'); } catch { /* ignore */ }
  }, [excOnlyA]);
  const typicalCdcUF = (vBusV: number, pKW: number): number => {
    const perKW = vBusV >= 550 ? 4 : vBusV >= 200 ? 10 : 60;
    const raw = Math.max(50, perKW * Math.max(pKW, 1));
    const dec = Math.pow(10, Math.floor(Math.log10(raw)));
    const m = raw / dec;
    const snap = m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10;
    return snap * dec;
  };
  const vdcRows = React.useMemo(() => {
    const d = data?.pwm?.dc_link;
    const ts = data?.time_s;
    const bc = data?.summary?.battery_charge;
    if (!d?.I_dc_A?.length || !ts?.length || !bc || !(bc.V_oc_V > 0)) return null;
    const R = bc.R_pack_ohm > 0 ? bc.R_pack_ohm : 0;
    const Voc = bc.V_oc_V;
    const n = Math.min(d.I_dc_A.length, ts.length);
    const t0 = ts[0] ?? 0;
    const dt = n > 1 ? (ts[n - 1] - ts[0]) / (n - 1) : 0;
    const pKW = Math.max(Math.abs(bc.P_mech_in_W ?? 0), Math.abs(bc.P_charge_W ?? 0)) / 1000;
    const typUF = typicalCdcUF(bc.V_bus_V > 0 ? bc.V_bus_V : Voc, pKW);
    const typical = cdcUF.trim() === '';
    const cUF = typical ? typUF : Math.max(0, Number(cdcUF) || 0);
    const C = cUF > 0 ? cUF * 1e-6 : 0;
    const lTypical = lcUH.trim() === '';
    const lUH = lTypical ? TYPICAL_L_UH : Math.max(0, Number(lcUH) || 0);
    const L = lUH * 1e-6;
    // Bus node with the pack behind R (+L) and the capacitor across it; the
    // bridge draws i_dc (a per-step MEAN, held constant inside each step).
    //   L·di_L/dt = V_oc − R·i_L − v ,   C·dv/dt = i_L − i_dc
    // (L = 0 collapses to  R·C·dv/dt + v − V_oc = −R·i_dc).  Linear with
    // periodic forcing, so the steady state is closed exactly: x(T) = M·x(0) + c
    // from unit-vector passes, then (I − M)·x0 = c.
    let vSeries: number[] | null = null;
    let iBat: number[] | null = null;
    if (C > 0 && R > 0 && dt > 0) {
      if (L > 0) {
        const T0 = 2 * Math.PI * Math.sqrt(L * C);
        const nsub = Math.min(400, Math.max(4, Math.ceil(dt / (0.02 * T0))));
        const h = dt / nsub;
        const f = (iL: number, v: number, idc: number): [number, number] =>
          [(Voc - R * iL - v) / L, (iL - idc) / C];
        const pass = (iL0: number, v0: number) => {
          let iL = iL0, v = v0;
          const vs: number[] = [], is: number[] = [];
          for (let i = 0; i < n; i++) {
            const idc = d.I_dc_A[i];
            vs.push(v); is.push(iL);
            for (let s = 0; s < nsub; s++) {
              const k1 = f(iL, v, idc);
              const k2 = f(iL + 0.5 * h * k1[0], v + 0.5 * h * k1[1], idc);
              const k3 = f(iL + 0.5 * h * k2[0], v + 0.5 * h * k2[1], idc);
              const k4 = f(iL + h * k3[0], v + h * k3[1], idc);
              iL += (h / 6) * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]);
              v += (h / 6) * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]);
            }
          }
          return { vs, is, iT: iL, vT: v };
        };
        const p0 = pass(0, 0), p1 = pass(1, 0), p2 = pass(0, 1);
        // M = [[m11, m12],[m21, m22]] on (iL, v); c = (p0.iT, p0.vT)
        const m11 = p1.iT - p0.iT, m21 = p1.vT - p0.vT;
        const m12 = p2.iT - p0.iT, m22 = p2.vT - p0.vT;
        const a11 = 1 - m11, a12 = -m12, a21 = -m21, a22 = 1 - m22;
        const det = a11 * a22 - a12 * a21;
        if (Math.abs(det) > 1e-14) {
          const iL0 = (p0.iT * a22 - a12 * p0.vT) / det;
          const v0 = (a11 * p0.vT - a21 * p0.iT) / det;
          const ps = pass(iL0, v0);
          vSeries = ps.vs; iBat = ps.is;
        }
      } else {
        const a1 = Math.exp(-dt / (R * C));
        const pass = (v0: number) => {
          let v = v0; const arr: number[] = [];
          for (let i = 0; i < n; i++) {
            const vInf = Voc - R * d.I_dc_A[i];
            arr.push(v);
            v = vInf + (v - vInf) * a1;
          }
          return { arr, vT: v };
        };
        const p0 = pass(0), p1 = pass(1);
        const a = p1.vT - p0.vT, b = p0.vT;
        const v0 = Math.abs(1 - a) > 1e-12 ? b / (1 - a) : Voc;
        vSeries = pass(v0).arr;
        iBat = vSeries.map((v) => (Voc - v) / R);
      }
    }
    const rows = [];
    let vMin = Infinity, vMax = -Infinity, vSum = 0;
    let cMin = Infinity, cMax = -Infinity, cSum = 0;
    let bMin = Infinity, bMax = -Infinity;
    let dMin = Infinity, dMax = -Infinity;
    for (let i = 0; i < n; i++) {
      const v = Voc - R * d.I_dc_A[i];
      vMin = Math.min(vMin, v); vMax = Math.max(vMax, v); vSum += v;
      dMin = Math.min(dMin, d.I_dc_A[i]); dMax = Math.max(dMax, d.I_dc_A[i]);
      const row: Record<string, number> = { t_ms: (ts[i] - t0) * 1e3, v_dc: v, v_oc: Voc };
      if (vSeries) { row.v_cap = vSeries[i]; cMin = Math.min(cMin, vSeries[i]); cMax = Math.max(cMax, vSeries[i]); cSum += vSeries[i]; }
      if (iBat) { bMin = Math.min(bMin, iBat[i]); bMax = Math.max(bMax, iBat[i]); }
      rows.push(row);
    }
    const f0 = (C > 0 && L > 0) ? 1 / (2 * Math.PI * Math.sqrt(L * C)) : null;
    const q = (C > 0 && L > 0 && R > 0) ? Math.sqrt(L / C) / R : null;
    return { rows, Voc, R, mean: vSum / n, pp: vMax - vMin,
             ppCap: vSeries ? cMax - cMin : null, meanCap: vSeries ? cSum / n : null,
             ppBat: iBat ? bMax - bMin : null, ppDc: dMax - dMin,
             C, cUF, typUF, typical, pKW, lUH, lTypical, f0, q };
  }, [data, cdcUF, lcUH]);

  // Ripple % computed from the DISPLAYED curve (pk-pk / |T_avg|), so it
  // matches the raw torque — no dependence on a cached filtered ripple.
  // (Near no-load |T_avg|→0 makes % meaningless; the header
  // shows the absolute cogging pk-pk there instead.)
  const ripplePct = React.useMemo(() => {
    if (!data || !Tshown.length) return 0;
    const avg = Math.abs(data.T_avg_Nm);
    if (avg < 1e-9) return 0;
    return 100 * (Math.max(...Tshown) - Math.min(...Tshown)) / avg;
  }, [data, Tshown]);

  // Emit the summary with the raw curve's ripple when data changes.
  // NB: a run that lacks a summary never reaches setData — the fetch surfaces it
  // as an error first (see the run() guards) — so `data && !data.summary` here
  // means only the empty-panel state (data null): correctly a no-op, not a freeze.
  useEffect(() => {
    if (!data?.summary || !onSummary) return;
    // The summary travels FURTHER than this panel: PhysicsDashboard persists it
    // to `sim.lastSummary`, from where it becomes a motor card's metrics and a
    // Compare point's results.  So it carries the machine stamp with it — the
    // number that says which motor these values describe.  `_geoSig` is the
    // stamp of the run itself (restored runs keep theirs; a run from before the
    // stamp existed carries none and is reported as unknown downstream).
    onSummary({ ...data.summary, T_ripple_pct: ripplePct,
                // WHICH RUN these numbers are (user 2026-09-13).  The summary
                // and the waveforms live under two different localStorage keys,
                // written by two different components, and "Save to duty" could
                // only ask whether they DESCRIBE the same point — a coincidence
                // test that says nothing when one of the two is stale at the
                // same point.  `computed_at` is the run's identity; carrying it
                // on the summary turns that test into an identity check.
                _runAt: (data as { computed_at?: string }).computed_at ?? undefined,
                _geoSig: data._geoSig ?? undefined,
                // …and which MATERIALS, for the same reason: the summary card
                // dims itself when the assignment moved under a finished run.
                _matSig: data._matSig ?? undefined,
                _geoStaleBackend: data.stale_geometry === true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, ripplePct]);

  // Display every resolved harmonic of the raw torque without suppressing bars.
  const harmRows = React.useMemo(() => {
    if (!data?.T_harm_order || !data?.T_harm_amp) return [];
    return data.T_harm_order.map((n, i) => {
      const amp = data.T_harm_amp![i];
      return {
        order: n, amp,
        pct: data.T_avg_Nm ? (100 * amp / Math.abs(data.T_avg_Nm)) : 0,
      };
    });
  }, [data]);

  // Phase-voltage harmonic spectrum — client-side DFT of V(t) over the stored
  // window (N samples = n_periods electrical periods, so harmonic h lives in
  // bin h·n_periods).  Magnitudes of the three phases are averaged: a balanced
  // machine has identical per-phase spectra, so averaging only suppresses
  // numerical asymmetry.  THD = √(ΣV_h², h≥2) / V₁.
  const vharm = React.useMemo(() => {
    if (!data?.V_A?.length) return null;
    const N = data.V_A.length;
    const P = Math.max(1, Math.round(data.n_periods || 1));
    const hMax = Math.min(25, Math.floor(N / (2 * P)) - 1);
    if (hMax < 1) return null;
    const phases = [data.V_A, data.V_B, data.V_C].filter(a => a?.length === N);
    const mag = (h: number) => {
      let sum = 0;
      for (const v of phases) {
        let re = 0, im = 0;
        const w = (2 * Math.PI * h * P) / N;
        for (let n = 0; n < N; n++) {
          // Edge samples of the dψ/dt central difference can be NaN/null in
          // older stored runs — treat them as 0 instead of poisoning the DFT.
          const x = Number.isFinite(v[n]) ? v[n] : 0;
          re += x * Math.cos(w * n); im -= x * Math.sin(w * n);
        }
        sum += (2 / N) * Math.hypot(re, im);
      }
      return sum / phases.length;
    };
    const rows = [];
    for (let h = 1; h <= hMax; h++) rows.push({ order: h, amp: mag(h) });
    const v1 = rows[0].amp;
    const thd = v1 > 1e-9
      ? 100 * Math.sqrt(rows.slice(1).reduce((s, r) => s + r.amp * r.amp, 0)) / v1
      : 0;
    // Line-to-line THD: triplens (3/9/15…) are zero-sequence and cancel in the
    // line voltage of a wye winding, so only non-triplen orders count — this is
    // the number a sinusoidal FOC drive actually fights (CIANO-S target < 5%).
    const thdLL = v1 > 1e-9
      ? 100 * Math.sqrt(rows.slice(1).reduce((s, r) => s + (r.order % 3 !== 0 ? r.amp * r.amp : 0), 0)) / v1
      : 0;
    return { rows: rows.map(r => ({ ...r, pct: v1 > 1e-9 ? (100 * r.amp / v1) : 0 })), v1, thd, thdLL };
  }, [data]);

  // Line-to-line voltage harmonic spectrum — DFT of the ACTUAL differences
  // V_A−V_B / V_B−V_C / V_C−V_A (3-pair magnitude average, same convention as
  // the backend summary THD_LL).  Triplen (3/9/15…) orders cancel in the
  // differences by physics, so their bars sit at ≈0 here — the visual proof of
  // why THD_LL excludes them; residual triplen bars expose phase unbalance.
  // The THD of this spectrum IS the line-to-line THD a sinusoidal FOC drive
  // fights, and V₁_LL ≈ √3·V₁_phase.
  const vllHarm = React.useMemo(() => {
    if (!data?.V_A?.length || !data?.V_B?.length || !data?.V_C?.length
        || data.V_B.length !== data.V_A.length
        || data.V_C.length !== data.V_A.length) return null;
    const N = data.V_A.length;
    const P = Math.max(1, Math.round(data.n_periods || 1));
    const hMax = Math.min(25, Math.floor(N / (2 * P)) - 1);
    if (hMax < 1) return null;
    const f = (x: number) => (Number.isFinite(x) ? x : 0);
    const pairs = [
      data.V_A.map((a, i) => f(a) - f(data.V_B[i])),
      data.V_B.map((b, i) => f(b) - f(data.V_C[i])),
      data.V_C.map((c, i) => f(c) - f(data.V_A[i])),
    ];
    const rows = [];
    for (let h = 1; h <= hMax; h++) {
      const w = (2 * Math.PI * h * P) / N;
      let m = 0;
      for (const vll of pairs) {
        let re = 0, im = 0;
        for (let n = 0; n < N; n++) {
          re += vll[n] * Math.cos(w * n); im -= vll[n] * Math.sin(w * n);
        }
        m += (2 / N) * Math.hypot(re, im);
      }
      rows.push({ order: h, amp: m / pairs.length });
    }
    const v1 = rows[0].amp;
    const thd = v1 > 1e-9
      ? 100 * Math.sqrt(rows.slice(1).reduce((s, r) => s + r.amp * r.amp, 0)) / v1
      : 0;
    return { rows: rows.map(r => ({ ...r, pct: v1 > 1e-9 ? (100 * r.amp / v1) : 0 })), v1, thd };
  }, [data]);

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      {/* Stale-graph marker.  TWO severities, because they are not the same
          mistake.  A different OPERATING POINT is a hint: same motor, other
          conditions — an amber chip is proportionate.  A different GEOMETRY
          means these curves belong to a motor that is no longer loaded, and a
          quiet chip is how a 30 mm machine's torque got read as the 40 mm's.
          That one shouts, in the same red as the summary card's banner
          (a174253) — one visual language for "these numbers are not yours". */}
      {geoStale ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5,
          px: 1.25, py: 0.75, borderRadius: 1, bgcolor: 'rgba(239,68,68,0.10)',
          border: '1px solid #b91c1c', color: '#f87171', fontSize: 12, fontWeight: 700 }}>
          ⚠ STALE — DIFFERENT MACHINE · Run Simulation
          <HelpTip title={'These waveforms were solved on a different machine than the one now loaded'
            + (data?.computed_at ? ` (run of ${data.computed_at})` : '')
            + '. Nothing below describes the current geometry. Press Run Simulation.'} />
        </Box>
      ) : (stale || appliedFromSweep) && (
        <Tooltip title={`The waveforms below were computed ${appliedFromSweep
            ? 'before the Sweep design was applied' : 'for different inputs'} — run Simulation to recompute.`}
          placement="top">
          <Box sx={{ alignSelf: 'flex-start', px: 1, py: 0.25, borderRadius: 1,
            bgcolor: 'rgba(251,191,36,0.12)', border: '1px solid #b45309',
            fontSize: 11, color: '#fbbf24', fontWeight: 700, cursor: 'help' }}>
            ⚠ stale
          </Box>
        </Tooltip>
      )}
      {/* ── header ────────────────────────────────────────────────────── */}
      <Box sx={{ display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', gap: 2 }}>
        <Box>
          <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
            Transient analysis — T(t), P(t), V(t)
            <Tooltip title="Runs one FEM solve per time step over one electrical period. Each step uses the current rotor angle and instantaneous phase currents." placement="top">
              <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
            </Tooltip>
          </Typography>
          {data && (() => {
            const tpp = Tshown.length
              ? Math.max(...Tshown) - Math.min(...Tshown) : 0;
            // Ripple is the raw curve's pk-pk / |T_avg|.
            const ripPct = ripplePct;
            // ripple % = pk-pk / |T_avg| is meaningless near no-load (T_avg≈0 →
            // it blows up to 1000s of %).  There, report the absolute cogging
            // pk-pk in N·m instead; show the % only when there's real average torque.
            const loaded = Math.abs(data.T_avg_Nm) >= 1.0;
            return (
              <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>
                {data.steps_snapped && data.n_steps_per_period_requested
                  ? <span style={{ color: '#fbbf24' }}
                      title={`Requested ${data.n_steps_per_period_requested} steps/period; the rotor must land on whole slip-ring nodes (${data.slip_nodes_per_period ?? '?'} per period), so the solver snapped to the nearest divisor. The numbers below are at ${data.n_steps_per_period} steps/period.`}>
                      requested {data.n_steps_per_period_requested} → ran {data.n_steps_per_period} steps/period
                    </span>
                  : <>{data.n_steps_per_period} steps/period</>} · dt = {(data.dt_s*1e6).toFixed(1)} µs ·
                T_period = {(data.T_period_s*1e3).toFixed(2)} ms ({data.f_elec_Hz.toFixed(1)} Hz electrical) ·
                T_avg = {data.T_avg_Nm.toFixed(2)} N·m · {loaded
                  ? `ripple = ${ripPct.toFixed(1)} %`
                  : `cogging pk-pk = ${tpp.toFixed(2)} N·m`}
                {' · '}<span style={{ color: '#fbbf24' }}>raw</span>
                {data.computed_at &&
                  <> · <span style={{ color: '#60a5fa' }}>
                    solved {new Date(data.computed_at).toLocaleTimeString()}</span></>}
              </Typography>
            );
          })()}
          {/* "Loaded from history — computed …" (2026-09-22): the SAME notice
              Mechanical/Thermal/Coupled show, not the bespoke "result from
              HH:MM" line this used to be — one wording across every panel
              that can answer from a store instead of solving.  The condition
              is unchanged (a stored run whose parameters are byte-identical
              to this one was found — user, 2026-09-05), only the words. */}
          {!geoStale && historyNoticeFor(data) && !busy && (
            <Typography sx={{ fontSize: 10, color: '#93c5fd', mt: 0.25 }}>
              {historyNoticeFor(data)!.text}
              {' · '}
              <Box component="span" role="button" tabIndex={0}
                onClick={() => { setStale(false); run(false, true); }}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { setStale(false); run(false, true); } }}
                sx={{ color: '#60a5fa', cursor: 'pointer', fontWeight: 700,
                  textDecoration: 'underline' }}>
                Recompute
              </Box>
              <HelpTip title="Nothing was solved: a run with exactly these inputs — geometry, materials, current, γ, rpm, mesh and drive — is already stored, so it was loaded. Recompute solves it again and replaces the stored one." />
            </Typography>
          )}
          {/* The COUPLED half of the same notice (2026-09-22) — a separate
              line because it answers a separate question ("was the EM/thermal
              LOOP re-iterated, or handed back") that can disagree with the one
              above (e.g. a Start-fresh EM solve whose coupled call still hit
              its own history). Same Recompute: `fresh` already rides the one
              request both calls share. */}
          {coupledHistory && !busy && (
            <Typography sx={{ fontSize: 10, color: '#93c5fd', mt: 0.25 }}>
              Coupled run — loaded from history — computed{' '}
              {formatHistoryComputedAt(coupledHistory.computed_at)}
              {' · '}
              <Box component="span" role="button" tabIndex={0}
                onClick={() => { setStale(false); run(false, true); }}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { setStale(false); run(false, true); } }}
                sx={{ color: '#60a5fa', cursor: 'pointer', fontWeight: 700,
                  textDecoration: 'underline' }}>
                Recompute
              </Box>
              <HelpTip title="Nothing was iterated: an EM ↔ thermal coupled loop with exactly these inputs — geometry, materials, operating point, cooling — is already stored, so it was loaded. Recompute runs the loop again and replaces the stored one." />
            </Typography>
          )}
          {/* Voltage-drive result strip: what was applied + what the machine
              answered with (harmonic currents = the FOC controller's real
              disturbance) + the watt cost vs a clean sinusoidal current. */}
          {(data?.drive === 'voltage' || data?.drive === 'pwm_voltage') && (
            <Typography sx={{ fontSize: 10, color: 'var(--text-2)', mt: 0.25 }}>
              <span style={{ color: '#a78bfa', fontWeight: 700 }}>
                {data.drive === 'pwm_voltage' ? 'PWM inverter' : 'voltage drive'}</span>
              {' '}V = {Number(data.v_phase_peak_V ?? 0).toFixed(1)} V @ δ {Number(data.v_delta_deg ?? 0).toFixed(1)}°
              {data.pwm && <> · {Number(data.pwm.v_bus_V).toFixed(0)} V bus · f_sw{' '}
                <b>{(data.pwm.f_switch_eff_Hz / 1000).toFixed(1)} kHz</b>{' '}
                (m {data.pwm.modulation_index.toFixed(2)},{' '}
                <b style={{ color: data.pwm.steps_per_switching_period >= 10 ? '#34d399' : '#fbbf24' }}>
                  {data.pwm.steps_per_switching_period.toFixed(1)} steps/switch</b>)
                <HelpTip title={`${data.pwm.modulator}. The carrier is snapped to `
                  + `${data.pwm.carriers_per_period} whole periods per electrical period `
                  + `(requested ${(data.pwm.f_switch_requested_Hz / 1000).toFixed(2)} kHz), so the `
                  + `reported electrical period repeats. Applied fundamental `
                  + `${data.pwm.v1_applied_V.toFixed(2)} V @ ${data.pwm.v1_applied_delta_deg.toFixed(1)}° `
                  + `from a reference at ${data.pwm.reference_delta_deg.toFixed(1)}° — the `
                  + `modulator's sampled-reference delay and gain, compensated. Below ~10 steps per `
                  + `switching period the ripple is averaged out and the losses read LOW.`} /></>}
              {data.summary?.THD_I_pct != null &&
                <> · THD_I = <b style={{ color: (data.summary.THD_I_pct <= 5 ? '#34d399' : data.summary.THD_I_pct <= 15 ? '#fbbf24' : '#f87171') }}>
                  {data.summary.THD_I_pct.toFixed(1)} %</b></>}
              {data.harm_ref &&
                <> · I₁ = {data.harm_ref.I1_phase_rms_A.toFixed(1)} A @ γ₁ {data.harm_ref.gamma1_deg.toFixed(1)}°</>}
              {data.dP_harm_W != null &&
                <> · ΔP_harm = <b style={{ color: (data.dP_harm_W as number) > 0 ? '#fbbf24' : '#34d399' }}>
                  {(data.dP_harm_W as number) >= 0 ? '+' : ''}{Number(data.dP_harm_W).toFixed(1)} W</b>
                  <HelpTip title={'Extra loss caused by the parasitic harmonic currents: this voltage-drive run minus a current-drive reference at the SAME fundamental current (I₁, γ₁). Positive = the distorted back-EMF costs real watts under a sinusoidal FOC supply.'} /></>}
              {data.v_dc_residual_A != null &&
                <> · DC resid <b style={{ color: (data.summary?.pwm_dc_unconverged ? '#f87171' : '#34d399') }}>
                  {Number(data.v_dc_residual_A).toFixed(2)} A</b>
                  <HelpTip title={'Mean phase current over the reported electrical period, worst phase. '
                    + 'Zero on a settled orbit. Above ' + (data.summary?.pwm_dc_tol_A ?? 0.5) + ' A the reported '
                    + 'TORQUE RIPPLE and current ripple ARE this offset, not the machine (the loss terms barely move).'} /></>}
            </Typography>
          )}
          {/* Imposed-current sources that are NOT the sinusoid: say what
              current actually went in, because "I rms" on the panel is not it. */}
          {data?.drive === 'bldc_current' && data.bldc && (
            <Typography sx={{ fontSize: 10, color: 'var(--text-2)', mt: 0.25 }}>
              <span style={{ color: '#a78bfa', fontWeight: 700 }}>BLDC 120° block</span>
              {' '}I = {data.bldc.i_block_A.toFixed(1)} A flat top ·{' '}
              {data.bldc.I_phase_rms_A.toFixed(1)} A rms · I₁ ={' '}
              {data.bldc.I1_phase_rms_A.toFixed(1)} A rms
              <HelpTip title={`${data.bldc.note}. The flat top is the controller's current `
                + `limit; the rms (I·√(2/3)) is what heats the winding and the fundamental `
                + `(I·2√3/π) is what makes the mean torque — compare against a sinusoidal run `
                + `at the same rms, not at the same peak.`} />
            </Typography>
          )}
          {data?.drive === 'custom_current' && data.custom_current && (
            <Typography sx={{ fontSize: 10, color: 'var(--text-2)', mt: 0.25 }}>
              <span style={{ color: '#a78bfa', fontWeight: 700 }}>imposed waveform</span>
              {' '}{data.custom_current.n_samples} pts ·{' '}
              {data.custom_current.I_phase_rms_A.toFixed(1)} A rms · I₁ ={' '}
              {data.custom_current.I1_phase_rms_A.toFixed(1)} A rms @ γ₁{' '}
              {data.custom_current.gamma1_deg.toFixed(1)}°
            </Typography>
          )}
        </Box>
        {/* Steps/period + Run moved to the left panel's "Run Simulation".
            Just show the live point counter here while solving. */}
        {solving && (
          <Typography sx={{ fontSize: 11, color: '#60a5fa', fontWeight: 600,
            whiteSpace: 'nowrap' }}>
            {progress && progress.total > 0
              ? `Point ${progress.step}/${progress.total}`
              : `Computing ${steps} points…`}
          </Typography>
        )}
        {/* History (2026-09-22): the last 10 stored runs of each kind, a click
            away from being shown with nothing solved — see HistoryPopover.
            Two buttons because they are two different stores (the EM ledger
            predates `run_history`; the coupled kind is `run_history`-backed)
            and two different questions ("which EM waveform" vs "which
            EM+thermal loop"). */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
          <HistoryPopover label="EM transient runs" disabled={busy}
            list={listLedgerHistory} onLoad={loadLedgerHistory}
            onDelete={deleteLedgerHistory} />
          <HistoryPopover label="coupled runs" disabled={busy}
            list={listCoupledHistory} onLoad={loadCoupledHistory}
            onDelete={deleteCoupledHistory} />
        </Box>
      </Box>

      {/* Live progress strip — appears the INSTANT a recompute starts (not
          gated on the first backend poll), so the user always sees the solve
          running: a live-ticking elapsed clock, the number of points computed
          so far, and a fill bar. */}
      {/* The strip itself moved to SolveProgressStrip at the TOP of the page
          (user request: a running solve must be visible without scrolling).
          The "Point X/N" counter in this panel's header stays. */}

      {error && (
        <Typography sx={{ fontSize: 11, color: '#fca5a5', p: 1,
          border: '1px solid #7f1d1d', borderRadius: 1 }}>
          {error}
        </Typography>
      )}

      {!data && !busy && !error && (
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)', textAlign: 'center',
          p: 3, border: '1px dashed var(--panel)', borderRadius: 1 }}>
          Press <b>Run Simulation</b> (left panel) to launch a transient FEM
          sweep over one electrical period.<br/>
          {steps} steps/period.
        </Typography>
      )}

      {data && (
        <>
          {/* ── Torque ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
              Torque T_em(t)
              <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>  ·  {rows.length} points</span>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'T [N·m]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Line type="monotone" dataKey="T_em" stroke="#34d399"
                  strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Torque harmonic spectrum ── */}
          {harmRows.length > 0 && (
          <Box sx={{ height: 200 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
              Torque harmonics (order = ×electrical freq)
              <Tooltip title="FFT of the entire raw Maxwell torque waveform. Every resolved bin is shown; finite windows can have fractional electrical orders. Orange marks multiples of six; an order alone does not establish whether its amplitude is physical or a numerical artifact." placement="top">
                <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={harmRows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="order" tick={AXIS} interval={0}
                  label={{ value: 'harmonic order (n × f_elec)', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'amp [N·m]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}
                  labelFormatter={(v: number) => `harmonic n = ${v}`}
                  formatter={(val: number, _n: string, p: any) =>
                    [`${Number(val).toPrecision(4)} N·m  (${p?.payload?.pct?.toPrecision(3)} % of T_avg)`, 'amplitude']}/>
                <Bar dataKey="amp" isAnimationActive={false}>
                  {harmRows.map((r, i) => (
                    <Cell key={i} fill={r.order % 6 === 0 ? '#f59e0b' : '#3b82f6'}/>
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Box>
          )}

          {/* ── Losses ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
              Losses (Cu / Fe / Mag / total)
              <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>  ·  {rows.length} points</span>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'P [W]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                <Line type="monotone" dataKey="P_cu" stroke="#fbbf24"
                  name="P_Cu (DC+AC)" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
                {/* Flat DC-only copper reference: the vertical gap to P_Cu (DC+AC)
                    IS the eddy/proximity loss share in the winding. */}
                <Line type="monotone" dataKey="P_cu_dc" stroke="#b45309"
                  name="P_Cu DC only" strokeWidth={1.25} strokeDasharray="6 3"
                  dot={false} activeDot={{ r: 1.5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_fe" stroke="#f87171"
                  name="P_Fe" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_mag" stroke="#a78bfa"
                  name="P_Mag" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_shaft" stroke="#4ade80"
                  name="P_shaft (Al)" strokeWidth={1.25} strokeDasharray="4 2"
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_tot" stroke="var(--text-1)"
                  name="P_total" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Currents ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
              Phase currents I_A / I_B / I_C
              <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>  ·  {rows.length} points</span>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'I [A]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                <Line type="monotone" dataKey="I_A" stroke="#ef4444"
                  name="I_A" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="I_B" stroke="#10b981"
                  name="I_B" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="I_C" stroke="#60a5fa"
                  name="I_C" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Voltages ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
              Phase voltages V_A / V_B / V_C  (V_peak ≈ {data.V_peak.toFixed(1)} V)
              <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>  ·  {rows.length} points{
                (data.drive === 'pwm_voltage')
                  ? ' · per-step mean — the real terminal voltage is rectangular pulses swinging the full bus; each point is the exact volt-second average of one solve step (what the winding integrated)'
                  : ''}</span>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'V [V]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                <Line type="monotone" dataKey="V_A" stroke="#ef4444"
                  name="V_A" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="V_B" stroke="#10b981"
                  name="V_B" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="V_C" stroke="#60a5fa"
                  name="V_C" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Voltage harmonic spectrum ── */}
          {vharm && (
          <Box sx={{ height: 200 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
              Voltage harmonics (order = ×f_elec)
              <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>
                {'  ·  '}V₁ ≈ {vharm.v1.toFixed(1)} V · THD ≈ {vharm.thd.toFixed(1)} %
                {' · '}
              </span>
              <span style={{ color: '#22d3ee', fontWeight: 600 }}>
                THD_LL ≈ {vharm.thdLL.toFixed(1)} %
              </span>
              <Tooltip title="DFT of the phase voltage V = R·I + dψ/dt (3-phase magnitude average). With sinusoidal imposed currents everything above order 1 is the machine itself: back-EMF shape + slotting. Green = fundamental (the useful component). Blue = 5/7/11/13… — these pair into the 6·k torque-ripple orders and load the inverter. Grey = triplen (3/9/15…) zero-sequence — visible phase-to-neutral but cancels line-to-line in a wye winding, drives no current. No PWM here — an inverter adds its own switching harmonics on top." placement="top">
                <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={vharm.rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="order" tick={AXIS} interval={0}
                  label={{ value: 'harmonic order (n × f_elec)', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'amp [V]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}
                  labelFormatter={(v: number) => `harmonic n = ${v}`}
                  formatter={(val: number, _n: string, p: any) =>
                    [`${Number(val).toFixed(2)} V  (${p?.payload?.pct?.toFixed(1)} % of V₁)`, 'amplitude']}/>
                <Bar dataKey="amp" isAnimationActive={false}>
                  {vharm.rows.map((r, i) => (
                    <Cell key={i} fill={r.order === 1 ? '#34d399'
                      : r.order % 3 === 0 ? 'var(--text-3)' : '#3b82f6'}/>
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Box>
          )}

          {/* ── Line-to-line voltages V_AB / V_BC / V_CA ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
              Line-to-line voltages V_AB / V_BC / V_CA
              {vllHarm && <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>
                {'  ·  '}V₁_LL ≈ {vllHarm.v1.toFixed(1)} V</span>}
              <Tooltip title="The voltages a wye-connected inverter actually applies between terminal pairs. Zero-sequence (triplen) harmonics cancel in the differences, so these waveforms are cleaner than the phase ones. In a perfectly balanced model the three curves are identical time-shifted copies — visible shape/amplitude differences between them are phase unbalance (e.g. sector-mesh seams)." placement="top">
                <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'V [V]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                <Line type="monotone" dataKey="V_AB" stroke="#a78bfa"
                  name="V_AB" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="V_BC" stroke="#f472b6"
                  name="V_BC" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="V_CA" stroke="#fbbf24"
                  name="V_CA" strokeWidth={1.25}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={1.5} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 2 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Line-to-line harmonic spectrum ── */}
          {vllHarm && (
          <Box sx={{ height: 200 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
              Line-to-line harmonics (order = ×f_elec)
              <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>
                {'  ·  '}V₁_LL ≈ {vllHarm.v1.toFixed(1)} V ·{' '}
              </span>
              <span style={{ color: '#22d3ee', fontWeight: 600 }}>
                THD ≈ {vllHarm.thd.toFixed(1)} %
              </span>
              <Tooltip title="DFT of the ACTUAL V_AB waveform. Triplen bars (3/9/15…, grey) sit at ≈0 because zero-sequence cancels between two phases of a wye winding — the physical reason THD_LL excludes them. The THD of this curve is what a sinusoidal FOC supply fights (CIANO-S target < 5%); any residual triplen content here indicates phase unbalance." placement="top">
                <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={vllHarm.rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="order" tick={AXIS} interval={0}
                  label={{ value: 'harmonic order (n × f_elec)', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'amp [V]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}
                  labelFormatter={(v: number) => `harmonic n = ${v}`}
                  formatter={(val: number, _n: string, p: any) =>
                    [`${Number(val).toFixed(2)} V  (${p?.payload?.pct?.toFixed(1)} % of V₁_LL)`, 'amplitude']}/>
                <Bar dataKey="amp" isAnimationActive={false}>
                  {vllHarm.rows.map((r, i) => (
                    <Cell key={i} fill={r.order === 1 ? '#34d399'
                      : r.order % 3 === 0 ? 'var(--text-3)' : '#8b5cf6'}/>
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Box>
          )}

          {/* ── EXCITATION: what the source actually applied ──────────────
              Last, and on the SAME t axis as everything above, so switching
              edges line up with the ripple they cause in the torque and
              current charts.  Sampled AT the solve steps — that is what the
              circuit integrated; the carrier-resolution waveform would be a
              picture of something this run did not solve.  Only for the
              sources where it is not already on screen: the two sinusoids need
              no such chart, and for the imposed-current sources the applied
              waveform IS the current chart above. */}
          {exc && (
          <Box sx={{ height: 230 }}>
            <Typography component="div" sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)',
              display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.5 }}>
              <span>{exc.title}</span>
              <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>
                {'  ·  '}{rows.length} points{exc.sub}
              </span>
              <Tooltip title={exc.help + (excOnlyA && exc.stepped
                ? ' Phase A only: the stepped line is the POLE voltage (bridge output vs the bus mid-point, '
                  + 'a per-step mean of ±V_bus/2); the dashed line is the same phase\'s PHASE voltage vs the '
                  + 'winding neutral. Their difference is the common mode, which the floating neutral absorbs.'
                : '')} placement="top">
                <span style={{ color: 'var(--text-4)', marginLeft: 2, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
              <span style={{ marginLeft: 6, display: 'inline-flex', gap: 2 }}>
                {([['A', true], ['ABC', false]] as const).map(([lbl, only]) => (
                  <span key={lbl} onClick={() => setExcOnlyA(only)}
                    title={only ? 'Phase A only, with its phase-to-neutral voltage' : 'All three phases'}
                    style={{ cursor: 'pointer', padding: '0 6px', borderRadius: 3, fontSize: 10,
                      border: '1px solid var(--line)',
                      background: excOnlyA === only ? 'var(--panel-3)' : 'transparent',
                      color: excOnlyA === only ? 'var(--text-1)' : 'var(--text-4)' }}>{lbl}</span>
                ))}
              </span>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: exc.unit, angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                {(excOnlyA ? (['A'] as const) : (['A', 'B', 'C'] as const)).map((ph) => (
                  // stepBEFORE, not after: the frame loop integrates
                  // [theta_prev, theta_k] and stores that mean at index k, so
                  // the value belongs to the step ENDING at this timestamp.
                  // (Measured: aligning it forward instead disagrees with the
                  // pulse train by 23 V, the full bus swing — see the wave_A
                  // self-check.)
                  <Line key={ph} type={exc.stepped ? 'stepBefore' : 'monotone'}
                    dataKey={`exc_${ph}`} stroke={{ A: '#ef4444', B: '#10b981', C: '#60a5fa' }[ph]}
                    name={`${exc.prefix}${ph}${excOnlyA && exc.stepped ? ' pole (vs bus mid-point)' : ''}`}
                    strokeWidth={1.25} dot={false}
                    activeDot={{ r: 2 }} isAnimationActive={false}/>
                ))}
                {excOnlyA && exc.stepped && (
                  <Line type="stepBefore" dataKey="V_A" stroke="#fbbf24"
                    name="V_A phase (vs winding neutral)" strokeWidth={1.25}
                    strokeDasharray="5 4" dot={false} activeDot={{ r: 2 }}
                    isAnimationActive={false}/>
                )}
              </LineChart>
            </ResponsiveContainer>
          </Box>
          )}

          {/* ── LINE VOLTAGE AT CARRIER RESOLUTION — the scope view ─────
              The excitation chart above plots what the SOLVE integrated (one
              mean per step).  This one plots what the CONTROLLER put across
              the motor: V_AB, three-level, the full bus.  Pole voltages are
              referenced to the DC-link mid-point — a construction, not a
              terminal — so the phase-to-mid-point view understates the swing
              by 2x and shows two levels where the machine sees three.
              Reconstructed analytically from the modulator, and it IS the same
              pulse train: integrating these edges over each solve step
              reproduces the line-to-line volt-second means the circuit used to
              1.4e-12 V (3e-12 worst case over the studied carriers). */}
          {waveA && (
          <Box sx={{ height: 250 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
              Line voltage V_AB — carrier resolution
              <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>
                {'  ·  '}±{waveA.w.v_bus_V.toFixed(1)} V bus ·{' '}
                {waveA.w.carriers_per_period} carriers/period ·{' '}
                {waveA.w.n_edges} edges · duty A{' '}
                {(100 * Math.min(...waveA.w.duty_A)).toFixed(0)}–
                {(100 * Math.max(...waveA.w.duty_A)).toFixed(0)} %, B{' '}
                {(100 * Math.min(...waveA.w.duty_B)).toFixed(0)}–
                {(100 * Math.max(...waveA.w.duty_B)).toFixed(0)} %
              </span>
              <Tooltip placement="top" title={
                'The voltage the inverter puts ACROSS two motor terminals: pole_A − pole_B, '
                + 'three-level (0, ±V_bus), because the two legs switch at different instants '
                + 'inside the same carrier period. Reconstructed analytically from the modulator '
                + '— the exact pulse train whose per-step volt-second means the solve integrated '
                + '(verified to 1.4e-12 V), NOT a solver output sampled coarser. The dashed sine '
                + 'is the applied LINE fundamental: with v_A = V₁cos(x) and v_B = V₁cos(x−120°), '
                + 'cosX−cosY = −2·sin((X+Y)/2)·sin((X−Y)/2) gives v_AB = √3·V₁·cos(x+30°) — so '
                + '√3 in amplitude and +30°el in phase, both measured back off these edges '
                + '(ratio 1.73205, delta +30.04°). Sent as switching EDGES (~4 per carrier, '
                + '1–7 kB) and expanded here for drawing.'}>
                <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={waveA.rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs} type="number"
                  domain={['dataMin', 'dataMax']}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'V_AB [V]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                <Line type="stepAfter" dataKey="v_pwm" stroke="#22d3ee"
                  name="V_AB (line)" strokeWidth={1.1} dot={false}
                  activeDot={false} isAnimationActive={false}/>
                <Line type="monotone" dataKey="v1" stroke="#fbbf24"
                  name="applied line fundamental" strokeWidth={1.4}
                  strokeDasharray="5 4" dot={false} activeDot={false}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>
          )}

          {/* ── DC-link current — the boost-mode oscillogram ─────────────
              Not a second view of the phase currents: it is what the BUS
              carries, Σ_phase s_phase·i_phase, and its MEAN is the charge
              current.  Drawn under the carrier chart because the two are the
              same bridge seen from its two sides. */}
          {dcRows && (
          <Box sx={{ height: 230 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
              DC-link current i_dc(t) — battery side of the bridge
              <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>
                {'  ·  '}mean {dcRows.d.I_dc_mean_A.toFixed(2)} A
                {'  ·  '}rms {dcRows.d.I_dc_rms_A.toFixed(2)} A
                {'  ·  '}ripple {dcRows.d.I_dc_ripple_pp_A.toFixed(2)} A p-p
                {'  ·  '}
                {dcRows.d.I_dc_mean_A < 0 ? 'INTO the pack' : 'OUT of the pack'}
              </span>
              <Tooltip placement="top" title={
                'i_dc = Σ_phase s_phase(t)·i_phase(t), one mean per solved step, integrated exactly across '
                + "the modulator's own switching edges inside each step (the phase current taken linear between "
                + 'solved steps). POSITIVE draws from the pack, NEGATIVE charges it. With pole voltages ±V_bus/2 '
                + 'and a floating neutral, V_bus·i_dc equals Σ v_pole·i identically, so this is the terminal '
                + 'power the run already solved, read on the other side of the switches — not a converter model '
                + 'bolted on afterwards. Ideal bridge: no dead time, no device drops, no bus ripple. '
                + dcRows.d.note}>
                <span style={{ color: 'var(--text-4)', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={dcRows.rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'i_dc [A]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                <Line type="monotone" dataKey="i_dc" stroke="#f59e0b"
                  name="i_dc (per step)" strokeWidth={1.25} dot={false}
                  activeDot={{ r: 2 }} isAnimationActive={false}/>
                <Line type="monotone" dataKey="i_mean" stroke="#38bdf8"
                  name="mean = charge current" strokeWidth={1.4}
                  strokeDasharray="5 4" dot={false} activeDot={false}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>
          )}

          {/* ── DC-link voltage — boost mode: the pack terminal under i_dc ── */}
          {vdcRows && (
          <Box sx={{ height: 250 }}>
            <Typography component="div" sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)',
              display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.5 }}>
              <span>DC-link voltage v_dc(t) — pack terminal, V_oc − R_pack·i_dc</span>
              <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>
                {'  ·  '}V_oc {vdcRows.Voc.toFixed(1)} V
                {'  ·  '}R_pack {(vdcRows.R * 1e3).toFixed(1)} mΩ
                {'  ·  '}mean {vdcRows.mean.toFixed(2)} V
                {'  ·  '}ripple {vdcRows.pp.toFixed(2)} V p-p
                {vdcRows.ppCap != null
                  ? `  ·  bus with C_dc ${vdcRows.cUF} µF${vdcRows.lUH > 0 ? ` + L ${vdcRows.lUH} µH` : ''}: ${vdcRows.ppCap.toFixed(3)} V p-p`
                    + (vdcRows.ppBat != null ? `  ·  pack current ${vdcRows.ppDc.toFixed(1)} → ${vdcRows.ppBat.toFixed(2)} A p-p` : '')
                    + (vdcRows.f0 != null ? `  ·  f₀ ${(vdcRows.f0 / 1e3).toFixed(1)} kHz` : '')
                  : '  ·  bare pack'}
                {vdcRows.typical && vdcRows.ppCap != null ? ` (typical for ${vdcRows.Voc.toFixed(0)} V · ${vdcRows.pKW.toFixed(0)} kW)` : ''}
              </span>
              <Tooltip placement="top" title={
                'v_dc = V_oc − R_pack·i_dc(t): the pack terminal seen by the bridge, per solved step, with the '
                + "run's own V_oc and R_pack (NS·r_cell/NP). Charging current raises the terminal, so the mean of "
                + 'this curve is the V_bus the charging card reports. No state-of-charge model: V_oc is the pack '
                + 'nominal. The second curve is the BUS with the inverter capacitor C_dc across it and the pack '
                + 'behind R_pack and the cable inductance L: L·di/dt = V_oc − R·i − v, C·dv/dt = i − i_dc, closed '
                + 'exactly to its periodic steady state. With L the switching ripple has to flow through the '
                + 'capacitor (the cable blocks it from the pack), so the bus ripple is ≈ ΔQ/C_dc — halve it by '
                + 'doubling C_dc — and the pack current is smoothed; with L = 0 a 25 mΩ pack is stiffer than any '
                + `capacitor and the ripple only shifts in phase. Resonance f₀ = 1/(2π√LC)${vdcRows.f0 != null ? ` = ${(vdcRows.f0 / 1e3).toFixed(1)} kHz, Q = ${vdcRows.q?.toFixed(1)}` : ''}; `
                + 'keep it away from 6·f_e and from the carrier. Means do not move with C or L, only ripple. '
                + 'Empty C_dc = typical film capacitor for this class: ≈4 µF/kW at 800 V (SiC), ≈10 µF/kW at 400 V, '
                + `≈60 µF/kW electrolytic below 100 V, floor 50 µF, 1-2-5 series → ${vdcRows.typUF} µF here; `
                + `empty L = ${TYPICAL_L_UH} µH (≈1 µH per metre of twisted DC harness + busbar). Type 0 for the bare pack. `
                + 'i_dc is a per-step mean: a coarse pass (few steps per carrier) under-resolves the switching ripple. '
                + 'Ideal bridge: no dead time, no device drops.'}>
                <span style={{ color: 'var(--text-4)', marginLeft: 2, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
              <TextField size="small" label="C_dc (µF)" type="number" value={cdcUF}
                placeholder={String(vdcRows.typUF)}
                onChange={(e) => setCdcUF(e.target.value)}
                inputProps={{ min: 0, step: 10, style: { fontSize: 11, padding: '2px 6px', width: 64 } }}
                InputLabelProps={{ sx: { fontSize: 10 } }}
                sx={{ ml: 1, '& .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--line)' } }}/>
              <TextField size="small" label="L cable (µH)" type="number" value={lcUH}
                placeholder={String(TYPICAL_L_UH)}
                onChange={(e) => setLcUH(e.target.value)}
                inputProps={{ min: 0, step: 0.5, style: { fontSize: 11, padding: '2px 6px', width: 56 } }}
                InputLabelProps={{ sx: { fontSize: 10 } }}
                sx={{ ml: 0.5, '& .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--line)' } }}/>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={vdcRows.rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <YAxis tick={AXIS} domain={['auto', 'auto']}
                  tickFormatter={(v: number) => v.toFixed(2)}
                  label={{ value: 'v_dc [V]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
                <RcTooltip {...TOOLTIP} formatter={((v: unknown) => `${Number(v).toFixed(3)} V`) as any}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                <Line type="monotone" dataKey="v_dc" stroke="#a78bfa"
                  name="v_dc (bare pack)" strokeWidth={1.25} dot={false}
                  activeDot={{ r: 2 }} isAnimationActive={false}/>
                {vdcRows.ppCap != null && (
                  <Line type="monotone" dataKey="v_cap" stroke="#34d399"
                    name={`bus: C_dc ${vdcRows.cUF} µF${vdcRows.lUH > 0 ? ` + L ${vdcRows.lUH} µH` : ''}${vdcRows.typical ? ' (typical)' : ''}`} strokeWidth={1.4} dot={false}
                    activeDot={{ r: 2 }} isAnimationActive={false}/>
                )}
                <Line type="monotone" dataKey="v_oc" stroke="#38bdf8"
                  name="V_oc (open circuit)" strokeWidth={1.2}
                  strokeDasharray="5 4" dot={false} activeDot={false}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>
          )}

          {/* The small per-element demag map that used to render here was
              removed at the user's request (2026-07-29): the Field view's
              Demag tab shows the same data on the full mesh with the
              Ansys-style colour map — one honest view instead of two. */}
        </>
      )}
    </Paper>
  );
};

export default TransientCharts;
