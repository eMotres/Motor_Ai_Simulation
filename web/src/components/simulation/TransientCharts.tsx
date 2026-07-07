/**
 * TransientCharts — torque, losses and phase-voltage waveforms over time.
 *
 * Runs a series of FEM solves at N steps per electrical period
 * (default 60), the same mesh and solver settings as the rest of the
 * Simulation tab.  Plots T(t), P_cu/P_fe/P_total(t) and V_A/B/C(t).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Box, Paper, Typography, Tooltip, CircularProgress,
} from '@mui/material';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip as RcTooltip, Legend, BarChart, Bar, Cell,
} from 'recharts';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

import type { TransientSummary } from './SummaryTable';
import DemagMap from './DemagMap';
import { useMotorStore } from '../../stores/motorStore';

interface TransientPayload {
  // Frontend-only stamp: the geometry signature this run was computed for.
  // Lets us flag the shown result stale when the live geometry changes (the
  // backend transient cache key omits geometry, so it can't detect this).
  _geoSig?: string;
  n_steps: number;
  n_steps_per_period: number;
  n_periods: number;
  dt_s: number;
  T_period_s: number;
  f_elec_Hz: number;
  rpm: number;
  time_s: number[];
  rotor_angle_deg: number[];
  T_em_Nm: number[];
  // Raw per-frame torque + the band-limited (6·k) reconstruction.  Both are
  // ALWAYS returned so the "Torque filter" toggle flips between them client-
  // side (instant — band-limiting is post-processing, no re-solve needed).
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
  // Set (instead of `summary`) when the backend solved the waveforms but the
  // summary-block build threw — lets the UI surface the error rather than freeze
  // the cards on the previous run's numbers.
  summary_error?: string;
  // Voltage drive (drive="voltage"): applied V, circuit diagnostics + the
  // matched-fundamental current-drive reference → ΔP_harm.
  drive?: 'current' | 'voltage';
  v_phase_peak_V?: number | null;
  v_delta_deg?: number | null;
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
  // Band-limit T(t) to the physical 6·k orders (default ON; off = raw torque).
  torqueFilter?: boolean;
  // Per-element irreversible demagnetisation — de-rates Br → torque/EMF + %-map.
  demag?: boolean;
  // A design was just applied from the Sweep tab (summary numbers reused) — the
  // shown waveforms are still the PREVIOUS design's, so flag them stale.
  appliedFromSweep?: boolean;
  // Drive mode: imposed sinusoidal current (default) or imposed sinusoidal
  // voltage (FOC verification — currents are the machine's own response).
  drive?: 'current' | 'voltage';
  vPeak?: number;   // voltage drive: phase-voltage amplitude [V, peak]
  vDelta?: number;  // voltage drive: voltage angle δ [°el] in the γ frame
}

function readMeshSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`mesh.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

function readSimSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`sim.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

const AXIS = { fontSize: 10, fill: '#94a3b8' };
const TOOLTIP = {
  contentStyle: { background: '#0f172a', border: '1px solid #1e293b',
    fontSize: 11, color: '#cbd5e1' },
  labelFormatter: (v: number) => `t = ${Number(v).toFixed(3)} ms`,
  formatter: (v: number) => Number(v).toFixed(3),
};
const GRID = { stroke: '#1e293b', strokeDasharray: '2 4' };

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
function persistLastTransient(d: TransientPayload) {
  try { localStorage.setItem(LAST_KEY, JSON.stringify(d)); }
  catch { /* quota — drop silently, recompute path still works */ }
}
function loadLastTransient(): TransientPayload | null {
  try { const s = localStorage.getItem(LAST_KEY); return s ? JSON.parse(s) : null; }
  catch { return null; }
}

// (live recompute progress strip: elapsed + points, driven by busy + /progress)
const TransientCharts: React.FC<Props> = ({ gamma_deg = 0, I_phase_rms = 85, onSummary, runNonce = 0, onBusyChange, steps = 12, fresh = false, fieldLosses = true, demag = false, torqueFilter = true, appliedFromSweep = false, drive = 'current', vPeak = 0, vDelta = 0 }) => {
  // `steps` (n_steps_per_period) is controlled from the left panel and
  // matches the animation viewer's n_frames so both hit the same backend
  // cache key (one solve, not two).
  const [data,  setData]  = useState<TransientPayload | null>(null);
  const [busy,  setBusy]  = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressInfo | null>(null);
  // True when the shown result was RESTORED on open but its params differ from
  // the current inputs (the backend flagged it stale) — a hint to press Run.
  const [stale, setStale] = useState<boolean>(false);

  // GEOMETRY staleness.  The shown result is stamped (in run()) with the
  // geometry it was solved for.  When the live geometry differs — e.g. after
  // applying a design from the Sweep/Optimization tab — the result is stale
  // even though the operating point is unchanged.  The backend can't catch
  // this (its transient cache key omits geometry), so we detect it here.
  const geometry = useMotorStore(s => s.geometry);
  const geoSig = useMemo(() => {
    try {
      return Object.entries(geometry || {})
        .filter(([, v]) => typeof v === 'number')
        .sort(([a], [b]) => (a < b ? -1 : 1))
        .map(([k, v]) => `${k}:${v}`).join('|');
    } catch { return ''; }
  }, [geometry]);
  const geoStale = !!data && data._geoSig != null && data._geoSig !== geoSig;

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
      try {
        const r = await fetch(`${API}/api/simulation/physics/fem_transient/progress`);
        if (r.ok) {
          const p: ProgressInfo = await r.json();
          if (alive) { setProgress(p); misses = p.running ? 0 : Math.min(misses + 1, 99); }
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
      setBusy(false);
      setError('Cancelled.');
    };
    window.addEventListener('sim:stop', onStop);
    return () => window.removeEventListener('sim:stop', onStop);
  }, [runNonce]);

  const run = (restoreOnly = false) => {
    setBusy(true); setError(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    // Typed inputs — ONE source for both transports.  The direct GET goes through
    // FastAPI (which coerces query strings to the typed signature); the kernel
    // POST calls get_fem_transient DIRECTLY, so the JSON types must be REAL here
    // (a string "false" would read truthy → e.g. a spurious restore).  Build typed
    // values, then stringify only when assembling the GET query string.
    const p: Record<string, unknown> = {
      // restore=true → on open, return the LAST saved transient (stale-flagged if
      // params differ) instead of recomputing.  Only the Run button omits it.
      restore:            restoreOnly,
      n_steps_per_period: steps,
      n_periods:          1,
      gamma_deg, I_phase_rms,
      // Drive mode: "voltage" imposes sinusoidal phase voltages — the currents
      // become the machine's own response (incl. back-EMF-harmonic parasitics)
      // and the backend also runs a matched-fundamental current-drive reference
      // (harm_ref) so ΔP_harm = the watt cost of those harmonic currents.
      drive,
      v_phase_peak:       drive === 'voltage' ? vPeak  : 0,
      v_delta_deg:        drive === 'voltage' ? vDelta : 0,
      harm_ref:           drive === 'voltage',
      mesh_size_mm:       readMeshSetting('meshSize',    4.0),
      min_size_mm:        readMeshSetting('minSize',     0.3),
      outer_air_factor:   readMeshSetting('outerAir',    1.3),
      motion_band:        readMeshSetting('motionBand',  true),
      band_thickness_mm:  readMeshSetting('bandThickness', 0.4),
      gap_layers:         readMeshSetting('gapLayers',   2),
      n_sectors:          readMeshSetting('nSectors',    1),
      stator_fillet_mm:   0,   // native geometry — extra smoothing removed
      // ALWAYS use the sliding band for the transient torque/back-EMF — meshes
      // ONCE and rotates the rotor through a moving band (clean, physical T(t)),
      // vs remesh-per-frame which injects huge numerical ripple.  Decoupled from
      // the Mesh-tab toggle (that now only controls mesh VISUALISATION).
      sliding_band:       true,
      // Field-based magnet/shaft eddy losses (σ·∂A/∂t solve) vs the slab estimate.
      rotor_eddy:         fieldLosses,
      // Per-element irreversible demagnetisation: de-rates Br → torque/EMF + %-map.
      demag,
      // Band-limit T(t) to the physical 6·k orders (UI toggle, default ON).
      torque_filter:      torqueFilter,
      // Bit-identical pole/slot mesh (Mesh-tab "Periodic" toggle).
      pole_copy:          readMeshSetting('poleCopy', false),
      // ANSYS-style concentric-ring air-gap mesh (Mesh-tab "Air-gap mesh" toggle).
      structured_gap:     readMeshSetting('structuredGap', false),
      // Copper-loss physics: coil temperature → ρ_Cu(T); end-winding factor
      // (0 = auto-estimate from geometry) for the copper the 2-D field misses.
      coil_temp_c:        readSimSetting('coilTemp',   120.0),
      end_winding_factor: readSimSetting('endWinding',   0.0),
      // Per-part mesh size from the Mesh tab (same localStorage key).
      component_mesh:     JSON.stringify(readMeshSetting<Record<string, number>>('componentMesh', {})),
      // SAME include_frames/n_frames as the FemAnimationViewer so both panels hit
      // the exact same backend cache key (one solve, not two).  Frames ignored here.
      include_frames:     true,
      n_frames:           steps,
      run_id:             String(runNonce),
      fresh,
    };
    // Helper: fetch with auto-retry against transient connection drops.
    // The uvicorn supervisor sometimes respawns the worker mid-request when
    // a heavy FEM solve crashes the LLVM JIT; without a retry the user sees
    // a permanent "Failed to fetch" until they click Re-run manually.
    const attempt = async (i = 0): Promise<void> => {
      try {
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
        const d: TransientPayload & { restored?: boolean; stale?: boolean } =
          (j.result && j.result.raw) || {};
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
        // Stamp a FRESH run with the geometry it was computed for, so a later
        // geometry change (e.g. applying a Sweep design) flags it stale.  A
        // RESTORED result keeps whatever stamp it was saved with.
        const stamped: TransientPayload = restoreOnly ? d : { ...d, _geoSig: geoSig };
        setStale(!!d.stale);
        setData(stamped); setBusy(false);
        setError(null);
        persistLastTransient(stamped);      // remember it (+ stamp) across reloads
        // summary is emitted by the effect below (so its ripple matches the
        // current filter toggle, and flips with it without a re-solve).
      } catch (e: any) {
        const msg = String(e);
        // User pressed Stop → don't retry, don't surface as an error.
        if (ctrl.signal.aborted || /abort/i.test(msg)) { setBusy(false); return; }
        const isNetwork = /Failed to fetch|NetworkError|TypeError/i.test(msg);
        if (isNetwork && i < 4) {
          // wait 2 s for the supervisor to bring uvicorn back up, then retry
          setError(`Backend hiccup — retrying (attempt ${i+2}/5)…`);
          setTimeout(() => attempt(i+1), 2000);
        } else {
          setError(msg); setBusy(false);
        }
      }
    };
    attempt();
  };

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
      if (last) { setData(last); return; }   // localStorage copy → show it, no compute
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

  // Build chart-friendly row arrays
  // Torque series shown: band-limited (6·k) when the filter is ON, raw per-
  // frame otherwise.  Both arrays come from the backend, so flipping the
  // checkbox switches the curve INSTANTLY — no re-solve.  Falls back to the
  // legacy single T_em_Nm for cached runs from before this field existed.
  const Tshown = React.useMemo(() => {
    if (!data) return [] as number[];
    const raw  = data.T_em_raw_Nm  ?? data.T_em_Nm;
    const filt = data.T_em_filt_Nm ?? data.T_em_Nm;
    return torqueFilter ? filt : raw;
  }, [data, torqueFilter]);

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
      // Line-to-line voltage — what a wye-connected inverter actually applies.
      // Zero-sequence (triplen) content cancels in the difference by physics.
      V_LL:  (Number.isFinite(data.V_A[i]) && Number.isFinite(data.V_B[i]))
               ? data.V_A[i] - data.V_B[i] : 0,
    }));
  }, [data, Tshown]);

  // Ripple % computed from the DISPLAYED curve (pk-pk / |T_avg|), so it
  // recomputes the instant the 6·k filter is toggled and always matches the
  // shown torque + spectrum — no dependence on which ripple field the backend
  // happened to cache.  (Near no-load |T_avg|→0 makes % meaningless; the header
  // shows the absolute cogging pk-pk there instead.)
  const ripplePct = React.useMemo(() => {
    if (!data || !Tshown.length) return 0;
    const avg = Math.abs(data.T_avg_Nm);
    if (avg < 1e-9) return 0;
    return 100 * (Math.max(...Tshown) - Math.min(...Tshown)) / avg;
  }, [data, Tshown]);

  // Emit the summary to the parent with the T-ripple of the DISPLAYED curve, so
  // the summary cards flip together with the torque curve + spectrum on toggle —
  // instantly, no re-solve.  (Declared AFTER ripplePct so its dep array doesn't
  // hit the temporal-dead-zone.)  Fires on data change + whenever the toggle does.
  // NB: a run that lacks a summary never reaches setData — the fetch surfaces it
  // as an error first (see the run() guards) — so `data && !data.summary` here
  // means only the empty-panel state (data null): correctly a no-op, not a freeze.
  useEffect(() => {
    if (!data?.summary || !onSummary) return;
    onSummary({ ...data.summary, T_ripple_pct: ripplePct });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, torqueFilter, ripplePct]);

  // Torque harmonic spectrum (over one electrical period).  6·k orders are the
  // physical 3-phase torque ripple; a clean ripple = a few discrete bars, broad
  // noise = energy in every order.  When the 6·k filter is ON the spectrum MUST
  // match the displayed (filtered) torque curve, so the parasitic non-6·k bars
  // are zeroed out — they visibly disappear, exactly what the filter removes.
  // Toggle OFF to see the full raw spectrum again.
  const harmRows = React.useMemo(() => {
    if (!data?.T_harm_order || !data?.T_harm_amp) return [];
    return data.T_harm_order.map((n, i) => {
      const raw = data.T_harm_amp![i];
      const amp = (torqueFilter && n % 6 !== 0) ? 0 : raw;
      return {
        order: n, amp,
        pct: data.T_avg_Nm ? (100 * amp / Math.abs(data.T_avg_Nm)) : 0,
      };
    });
  }, [data, torqueFilter]);

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

  // Line-to-line voltage harmonic spectrum — DFT of the ACTUAL V_AB = V_A − V_B
  // waveform.  Triplen (3/9/15…) orders cancel in the difference by physics, so
  // their bars sit at ≈0 here — the visual proof of why THD_LL excludes them.
  // The THD of this curve IS the line-to-line THD a sinusoidal FOC drive fights
  // (matches the summary THD_LL up to phase unbalance), and V₁_LL ≈ √3·V₁_phase.
  const vllHarm = React.useMemo(() => {
    if (!data?.V_A?.length || !data?.V_B?.length
        || data.V_B.length !== data.V_A.length) return null;
    const N = data.V_A.length;
    const P = Math.max(1, Math.round(data.n_periods || 1));
    const hMax = Math.min(25, Math.floor(N / (2 * P)) - 1);
    if (hMax < 1) return null;
    const vll = data.V_A.map((a, i) => {
      const b = data.V_B[i];
      return (Number.isFinite(a) && Number.isFinite(b)) ? a - b : 0;
    });
    const rows = [];
    for (let h = 1; h <= hMax; h++) {
      let re = 0, im = 0;
      const w = (2 * Math.PI * h * P) / N;
      for (let n = 0; n < N; n++) {
        re += vll[n] * Math.cos(w * n); im -= vll[n] * Math.sin(w * n);
      }
      rows.push({ order: h, amp: (2 / N) * Math.hypot(re, im) });
    }
    const v1 = rows[0].amp;
    const thd = v1 > 1e-9
      ? 100 * Math.sqrt(rows.slice(1).reduce((s, r) => s + r.amp * r.amp, 0)) / v1
      : 0;
    return { rows: rows.map(r => ({ ...r, pct: v1 > 1e-9 ? (100 * r.amp / v1) : 0 })), v1, thd };
  }, [data]);

  return (
    <Paper sx={{ bgcolor: '#0b1220', border: '1px solid #1e293b', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      {/* Stale-graph warning — the shown waveforms were computed for different
          inputs/geometry (e.g. a design was just applied from Sweep).  The
          summary numbers update instantly, but the GRAPHS need a re-solve. */}
      {(stale || geoStale || appliedFromSweep) && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 1,
          bgcolor: 'rgba(251,191,36,0.12)', border: '1px solid #b45309', borderRadius: 1 }}>
          <span style={{ fontSize: 16, lineHeight: 1 }}>⚠️</span>
          <Typography sx={{ fontSize: 12, color: '#fbbf24', fontWeight: 600 }}>
            These graphs are from the previous design{geoStale || appliedFromSweep ? ' — the geometry has changed' : ' — inputs changed'}. Press “Run Simulation” to recompute the correct waveforms.
          </Typography>
        </Box>
      )}
      {/* ── header ────────────────────────────────────────────────────── */}
      <Box sx={{ display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', gap: 2 }}>
        <Box>
          <Typography sx={{ fontSize: 13, color: '#cbd5e1', fontWeight: 700 }}>
            Transient analysis — T(t), P(t), V(t)
            <Tooltip title="Runs one FEM solve per time step over one electrical period. Each step uses the current rotor angle and instantaneous phase currents." placement="top">
              <span style={{ color: '#475569', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
            </Tooltip>
          </Typography>
          {data && (() => {
            const tpp = Tshown.length
              ? Math.max(...Tshown) - Math.min(...Tshown) : 0;
            // Ripple = pk-pk of the DISPLAYED curve / |T_avg| — recomputed on
            // every filter toggle so it always matches the shown torque + spectrum.
            const ripPct = ripplePct;
            // ripple % = pk-pk / |T_avg| is meaningless near no-load (T_avg≈0 →
            // it blows up to 1000s of %).  There, report the absolute cogging
            // pk-pk in N·m instead; show the % only when there's real average torque.
            const loaded = Math.abs(data.T_avg_Nm) >= 1.0;
            return (
              <Typography sx={{ fontSize: 10, color: '#475569' }}>
                {data.n_steps_per_period} steps/period · dt = {(data.dt_s*1e6).toFixed(1)} µs ·
                T_period = {(data.T_period_s*1e3).toFixed(2)} ms ({data.f_elec_Hz.toFixed(1)} Hz electrical) ·
                T_avg = {data.T_avg_Nm.toFixed(2)} N·m · {loaded
                  ? `ripple = ${ripPct.toFixed(1)} %`
                  : `cogging pk-pk = ${tpp.toFixed(2)} N·m`}
                {' · '}<span style={{ color: torqueFilter ? '#34d399' : '#fbbf24' }}>
                  {torqueFilter ? '6·k filtered' : 'raw'}</span>
                {data.computed_at &&
                  <> · <span style={{ color: '#60a5fa' }}>
                    solved {new Date(data.computed_at).toLocaleTimeString()}</span></>}
              </Typography>
            );
          })()}
          {/* Voltage-drive result strip: what was applied + what the machine
              answered with (harmonic currents = the FOC controller's real
              disturbance) + the watt cost vs a clean sinusoidal current. */}
          {data?.drive === 'voltage' && (
            <Typography sx={{ fontSize: 10, color: '#94a3b8', mt: 0.25 }}>
              <span style={{ color: '#a78bfa', fontWeight: 700 }}>voltage drive</span>
              {' '}V = {Number(data.v_phase_peak_V ?? 0).toFixed(1)} V @ δ {Number(data.v_delta_deg ?? 0).toFixed(1)}°
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
                <> · DC resid {Number(data.v_dc_residual_A).toFixed(2)} A</>}
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
      </Box>

      {/* Live progress strip — appears the INSTANT a recompute starts (not
          gated on the first backend poll), so the user always sees the solve
          running: a live-ticking elapsed clock, the number of points computed
          so far, and a fill bar. */}
      {solving && (() => {
        const total = (progress && progress.total > 0) ? progress.total : steps;
        const step  = progress ? Math.min(progress.step, total) : 0;
        const pct   = Math.min(100, 100 * step / Math.max(1, total));
        const eta   = (progress && progress.eta_s) ? progress.eta_s : 0;
        const perPt = (progress && progress.per_step_s) ? progress.per_step_s
                    : (step > 0 ? solveElapsed / step : 0);
        return (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.6,
          px: 1.25, py: 0.9, bgcolor: '#0a1424', border: '1px solid #1d4ed8',
          borderRadius: 1, fontFamily: 'monospace',
          boxShadow: '0 0 10px rgba(37,99,235,0.25)' }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between',
            alignItems: 'baseline', fontSize: 12, color: '#bfdbfe' }}>
            <span>
              <CircularProgress size={11} thickness={6}
                sx={{ color: '#3b82f6', mr: 0.8, verticalAlign: 'middle' }}/>
              Computing&nbsp;<b style={{ color: '#e0f2fe' }}>{step}</b>&nbsp;/&nbsp;<b>{total}</b>&nbsp;points
              {perPt ? `   ·   ${perPt.toFixed(2)} s/pt` : ''}
            </span>
            <span style={{ color: '#93c5fd' }}>
              elapsed&nbsp;<b style={{ color: '#e0f2fe' }}>{solveElapsed.toFixed(1)} s</b>
              {eta ? `   ·   ETA ${eta.toFixed(0)} s` : ''}
            </span>
          </Box>
          <Box sx={{ width: '100%', height: 6, bgcolor: '#0f172a',
            borderRadius: 3, overflow: 'hidden' }}>
            <Box sx={{ width: `${pct.toFixed(1)}%`, height: '100%',
              bgcolor: '#3b82f6', transition: 'width 0.3s ease' }}/>
          </Box>
        </Box>
        );
      })()}

      {error && (
        <Typography sx={{ fontSize: 11, color: '#fca5a5', p: 1,
          border: '1px solid #7f1d1d', borderRadius: 1 }}>
          {error}
        </Typography>
      )}

      {!data && !busy && !error && (
        <Typography sx={{ fontSize: 11, color: '#64748b', textAlign: 'center',
          p: 3, border: '1px dashed #1e293b', borderRadius: 1 }}>
          Press <b>Run Simulation</b> (left panel) to launch a transient FEM
          sweep over one electrical period.<br/>
          {steps} steps/period.
        </Typography>
      )}

      {data && (
        <>
          {/* ── Torque ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Torque T_em(t)
              <span style={{ color: '#475569', fontWeight: 400 }}>  ·  {rows.length} points</span>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: '#475569' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'T [N·m]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: '#475569' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Line type="monotone" dataKey="T_em" stroke="#34d399"
                  strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Torque harmonic spectrum ── */}
          {harmRows.length > 0 && (
          <Box sx={{ height: 200 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Torque harmonics (order = ×electrical freq)
              <Tooltip title="FFT of T(t) over one electrical period. A clean PERIODIC ripple shows a few discrete bars — the 6th/12th/18th (3-phase) and slot-cogging orders. Energy spread across every order = broadband (chaotic) noise. Orange = the physical 6·k 3-phase orders." placement="top">
                <span style={{ color: '#475569', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={harmRows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="order" tick={AXIS} interval={0}
                  label={{ value: 'harmonic order (n × f_elec)', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: '#475569' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'amp [N·m]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: '#475569' } }}/>
                <RcTooltip {...TOOLTIP}
                  labelFormatter={(v: number) => `harmonic n = ${v}`}
                  formatter={(val: number, _n: string, p: any) =>
                    [`${Number(val).toFixed(2)} N·m  (${p?.payload?.pct?.toFixed(1)} % of T_avg)`, 'amplitude']}/>
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
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Losses (Cu / Fe / Mag / total)
              <span style={{ color: '#475569', fontWeight: 400 }}>  ·  {rows.length} points</span>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: '#475569' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'P [W]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: '#475569' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                <Line type="monotone" dataKey="P_cu" stroke="#fbbf24"
                  name="P_Cu (DC+AC)" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                {/* Flat DC-only copper reference: the vertical gap to P_Cu (DC+AC)
                    IS the eddy/proximity loss share in the winding. */}
                <Line type="monotone" dataKey="P_cu_dc" stroke="#b45309"
                  name="P_Cu DC only" strokeWidth={2} strokeDasharray="6 3"
                  dot={false} activeDot={{ r: 4 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_fe" stroke="#f87171"
                  name="P_Fe" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_mag" stroke="#a78bfa"
                  name="P_Mag" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_shaft" stroke="#4ade80"
                  name="P_shaft (Al)" strokeWidth={2} strokeDasharray="4 2"
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_tot" stroke="#cbd5e1"
                  name="P_total" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Currents ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Phase currents I_A / I_B / I_C
              <span style={{ color: '#475569', fontWeight: 400 }}>  ·  {rows.length} points</span>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: '#475569' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'I [A]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: '#475569' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                <Line type="monotone" dataKey="I_A" stroke="#ef4444"
                  name="I_A" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="I_B" stroke="#10b981"
                  name="I_B" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="I_C" stroke="#60a5fa"
                  name="I_C" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Voltages ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Phase voltages V_A / V_B / V_C  (V_peak ≈ {data.V_peak.toFixed(1)} V)
              <span style={{ color: '#475569', fontWeight: 400 }}>  ·  {rows.length} points</span>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: '#475569' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'V [V]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: '#475569' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                <Line type="monotone" dataKey="V_A" stroke="#ef4444"
                  name="V_A" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="V_B" stroke="#10b981"
                  name="V_B" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="V_C" stroke="#60a5fa"
                  name="V_C" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Voltage harmonic spectrum ── */}
          {vharm && (
          <Box sx={{ height: 200 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Voltage harmonics (order = ×f_elec)
              <span style={{ color: '#475569', fontWeight: 400 }}>
                {'  ·  '}V₁ ≈ {vharm.v1.toFixed(1)} V · THD ≈ {vharm.thd.toFixed(1)} %
                {' · '}
              </span>
              <span style={{ color: '#22d3ee', fontWeight: 600 }}>
                THD_LL ≈ {vharm.thdLL.toFixed(1)} %
              </span>
              <Tooltip title="DFT of the phase voltage V = R·I + dψ/dt (3-phase magnitude average). With sinusoidal imposed currents everything above order 1 is the machine itself: back-EMF shape + slotting. Green = fundamental (the useful component). Blue = 5/7/11/13… — these pair into the 6·k torque-ripple orders and load the inverter. Grey = triplen (3/9/15…) zero-sequence — visible phase-to-neutral but cancels line-to-line in a wye winding, drives no current. No PWM here — an inverter adds its own switching harmonics on top." placement="top">
                <span style={{ color: '#475569', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={vharm.rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="order" tick={AXIS} interval={0}
                  label={{ value: 'harmonic order (n × f_elec)', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: '#475569' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'amp [V]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: '#475569' } }}/>
                <RcTooltip {...TOOLTIP}
                  labelFormatter={(v: number) => `harmonic n = ${v}`}
                  formatter={(val: number, _n: string, p: any) =>
                    [`${Number(val).toFixed(2)} V  (${p?.payload?.pct?.toFixed(1)} % of V₁)`, 'amplitude']}/>
                <Bar dataKey="amp" isAnimationActive={false}>
                  {vharm.rows.map((r, i) => (
                    <Cell key={i} fill={r.order === 1 ? '#34d399'
                      : r.order % 3 === 0 ? '#64748b' : '#3b82f6'}/>
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Box>
          )}

          {/* ── Line-to-line voltage V_AB = V_A − V_B ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Line-to-line voltage V_LL = V_A − V_B
              {vllHarm && <span style={{ color: '#475569', fontWeight: 400 }}>
                {'  ·  '}V₁_LL ≈ {vllHarm.v1.toFixed(1)} V</span>}
              <Tooltip title="The voltage a wye-connected inverter actually applies between two terminals. Zero-sequence (triplen) harmonics of the phase voltage cancel in the difference, so this waveform is cleaner than the phase one. V₁_LL ≈ √3 × V₁_phase." placement="top">
                <span style={{ color: '#475569', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="t_ms" tick={AXIS} tickFormatter={fmtMs}
                  label={{ value: 't [ms]', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: '#475569' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'V [V]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: '#475569' } }}/>
                <RcTooltip {...TOOLTIP}/>
                <Line type="monotone" dataKey="V_LL" stroke="#a78bfa"
                  name="V_LL" strokeWidth={2}
                  dot={(d: any) => <circle key={d.index} cx={d.cx} cy={d.cy}
                    r={3} fill={d.stroke} stroke="none"/>}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Line-to-line harmonic spectrum ── */}
          {vllHarm && (
          <Box sx={{ height: 200 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Line-to-line harmonics (order = ×f_elec)
              <span style={{ color: '#475569', fontWeight: 400 }}>
                {'  ·  '}V₁_LL ≈ {vllHarm.v1.toFixed(1)} V ·{' '}
              </span>
              <span style={{ color: '#22d3ee', fontWeight: 600 }}>
                THD ≈ {vllHarm.thd.toFixed(1)} %
              </span>
              <Tooltip title="DFT of the ACTUAL V_AB waveform. Triplen bars (3/9/15…, grey) sit at ≈0 because zero-sequence cancels between two phases of a wye winding — the physical reason THD_LL excludes them. The THD of this curve is what a sinusoidal FOC supply fights (CIANO-S target < 5%); any residual triplen content here indicates phase unbalance." placement="top">
                <span style={{ color: '#475569', marginLeft: 6, fontSize: 11, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={vllHarm.rows} margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                <CartesianGrid {...GRID}/>
                <XAxis dataKey="order" tick={AXIS} interval={0}
                  label={{ value: 'harmonic order (n × f_elec)', position: 'insideBottom',
                    offset: -4, style: { fontSize: 10, fill: '#475569' } }}/>
                <YAxis tick={AXIS}
                  label={{ value: 'amp [V]', angle: -90,
                    position: 'insideLeft', offset: 12,
                    style: { fontSize: 10, fill: '#475569' } }}/>
                <RcTooltip {...TOOLTIP}
                  labelFormatter={(v: number) => `harmonic n = ${v}`}
                  formatter={(val: number, _n: string, p: any) =>
                    [`${Number(val).toFixed(2)} V  (${p?.payload?.pct?.toFixed(1)} % of V₁_LL)`, 'amplitude']}/>
                <Bar dataKey="amp" isAnimationActive={false}>
                  {vllHarm.rows.map((r, i) => (
                    <Cell key={i} fill={r.order === 1 ? '#34d399'
                      : r.order % 3 === 0 ? '#64748b' : '#8b5cf6'}/>
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Box>
          )}

          {/* ── Demagnetisation: per-magnet report (%) + per-element %-map ── */}
          {data.demag_report && data.demag_report.length > 0 && (
            <Box sx={{ p: 1, border: '1px solid',
              borderColor: data.demag_report.some(r => r.demagnetised) ? '#7f1d1d' : '#78350f',
              borderRadius: 1, bgcolor: '#160e0e' }}>
              <Typography sx={{ fontSize: 12, fontWeight: 700, mb: 0.5,
                color: data.demag_report.some(r => r.demagnetised) ? '#fca5a5' : '#fbbf24' }}>
                {data.demag_report.some(r => r.demagnetised)
                  ? '⛔ MAGNET DEMAGNETISATION — torque & back-EMF de-rated'
                  : '⚠ Magnets approaching demag knee'}
              </Typography>
              {data.demag_report.map((r, i) => (
                <Typography key={i} sx={{ fontSize: 11, fontFamily: 'monospace',
                  color: r.demagnetised ? '#fca5a5' : '#cbd5e1' }}>
                  mag[{r.magnet_index}]: H_min = {r.H_min_kA_per_m} kA/m
                  {' '}(knee {r.H_knee_kA_per_m} kA/m, {(r.knee_proximity * 100).toFixed(0)}%)
                  {r.demagnetised && `  →  Br ×${r.Br_factor}  (−${((1 - r.Br_factor) * 100).toFixed(0)}%)`}
                </Typography>
              ))}
            </Box>
          )}
          {data.demag_field && <DemagMap field={data.demag_field as any} />}
        </>
      )}
    </Paper>
  );
};

export default TransientCharts;
