/**
 * Simulation tab — 2D Magnetostatics PINN (NVIDIA Modulus)
 *
 * Layout:
 *   Left  — operating-point controls + run button
 *   Right — status / results / log
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box, Typography, TextField, Button, Chip, Divider,
  LinearProgress, Alert, Tooltip, IconButton, Paper,
  CircularProgress, Dialog, DialogTitle, DialogContent, DialogActions,
  Checkbox, FormControlLabel,
} from '@mui/material';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import { useMotorStore } from '../../stores/motorStore';
import { windingConnections } from '../../lib/referencePassports';
import PlayArrowIcon    from '@mui/icons-material/PlayArrow';
import StopIcon         from '@mui/icons-material/Stop';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import CheckCircleIcon  from '@mui/icons-material/CheckCircle';
import ErrorIcon        from '@mui/icons-material/Error';
import BoltIcon         from '@mui/icons-material/Bolt';
import SimulationCharts from './SimulationCharts';
import PhysicsDashboard from './PhysicsDashboard';
import CoupledEmThermal from './CoupledEmThermal';
import HelpTip from '../common/HelpTip';
import { syncActiveMotor } from '../common/motorSettings';

// NOTE: using port 8001 (new backend with loss calculations)
// Change back to 8000 after restarting the main backend
const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// ── types ─────────────────────────────────────────────────────────────────────
interface SimStatus {
  modulus_available: boolean;
  operating_point: {
    max_current: number;
    frequency_hz: number;
    rpm: number;
    Br_magnet_T: number;
  };
  solver: string;
}

interface JobStatus {
  job_id: string;
  status: 'queued' | 'running' | 'done' | 'error';
  progress: number;
  result?: {
    torque_Nm: number;
    B_max_T: number;
    B_mean_T: number;
    training_steps: number;
    output_dir: string;
    status?: string;
    modulus_available?: boolean;
    // Copper losses (always available)
    P_cu_total_W?: number;
    R_phase_ohm?: number;
    R_coil_ohm?: number;
    L_turn_mm?: number;
    I_coil_rms_A?: number;
    // Iron / magnet losses (require PINN)
    P_fe_stator_W?: number | null;
    P_fe_rotor_W?: number | null;
    P_mag_eddy_W?: number | null;
    // Power & efficiency
    P_mech_W?: number | null;
    P_input_W?: number | null;
    P_loss_total_W?: number;
    efficiency_pct?: number | null;
    note?: string;
  };
  error?: string;
  elapsed_s?: number;
}

// ── small helpers ─────────────────────────────────────────────────────────────
const Row: React.FC<{ label: string; value: string; unit?: string; highlight?: boolean }> = ({
  label, value, unit, highlight,
}) => (
  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', py: 0.4 }}>
    <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{label}</Typography>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <Typography sx={{ fontSize: 12, fontWeight: 600, color: highlight ? '#4ade80' : 'var(--text-0)' }}>
        {value}
      </Typography>
      {unit && <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{unit}</Typography>}
    </Box>
  </Box>
);

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────
// ── helpers ───────────────────────────────────────────────────────────────────
function gcd(a: number, b: number): number { return b === 0 ? a : gcd(b, a % b); }
function lcm(a: number, b: number): number { return (a * b) / gcd(a, b); }

// Winding connection options come from the backend (/api/winding/config), which
// derives them from the slot count: coils/phase C = slots/6, every factor pair
// (nS series × nP parallel = C) is an option.  See api._winding_connections.
interface WindConn { label: string; n_parallel: number; n_series: number; }

const SimulationPanel: React.FC<{ active?: boolean }> = ({ active = false }) => {
  // localStorage-backed state so the whole left column survives reloads.
  const usePersisted = <T,>(key: string, def: T) => {
    const [v, setV] = useState<T>(() => {
      try {
        const raw = localStorage.getItem(`sim.${key}`);
        return raw == null ? def : (JSON.parse(raw) as T);
      } catch { return def; }
    });
    useEffect(() => {
      try { localStorage.setItem(`sim.${key}`, JSON.stringify(v)); } catch {}
    }, [key, v]);
    return [v, setV] as const;
  };

  // ── server status ─────────────────────────────────────────────────────────
  const [srvStatus, setSrvStatus] = useState<SimStatus | null>(null);
  const [srvErr, setSrvErr]       = useState<string | null>(null);

  // ── geometry (for period + winding calc) ─────────────────────────────────
  const [numPoles,      setNumPoles]      = useState<number>(28);
  const [numSlots,      setNumSlots]      = useState<number>(24);
  const [nWiresPerSlot, setNWiresPerSlot] = useState<number>(14);
  const [nCoilsPerPhase, setNCoilsPerPhase] = useState<number>(4);

  // ── winding connection ────────────────────────────────────────────────────
  const [connection, setConnection] = usePersisted<string>('connection', '4S');

  // ── winding LAYOUT (per-slot phase + sign = coil currents) ─────────────────
  const [windCfg, setWindCfg]       = useState<any>(null);     // /api/winding/config
  // connection options + the selected connection's parallel-path count come from
  // the backend (slot-derived). I_coil = I_phase / n_parallel.
  // Prefer the backend's slot-derived options; fall back to deriving them locally
  // from the slot count so the UI still works against an older backend that
  // doesn't yet return `connections`.
  const windConns: WindConn[] = windCfg?.connections
    ?? windingConnections(windCfg?.num_slots ?? 0).map((c) => ({ label: c.label, n_parallel: c.nP, n_series: c.nS }));
  const connDef = windConns.find((c) => c.label === connection);
  const nParallel = connDef?.n_parallel ?? windCfg?.n_parallel ?? 1;

  const loadWinding = useCallback(() => {
    fetch(`${API}/api/winding/config`)
      .then(r => r.json())
      .then(d => {
        setWindCfg(d);
        // config.yaml is the persistent, cross-browser source for the connection;
        // adopt it on load so a fresh browser / other tab reflects the real value.
        if (d.connection) setConnection(d.connection);
      })
      .catch(() => {});
  }, []);
  useEffect(() => { loadWinding(); }, [loadWinding]);

  const applyWinding = useCallback((patch: Record<string, any>) => {
    fetch(`${API}/api/winding/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
      .then(async r => {
        if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `HTTP ${r.status}`); }
      })
      .then(() => loadWinding())
      .catch(() => {});
  }, [loadWinding]);

  // Phase → colour for the slot map (A=red, B=green, C=blue); +full, −faded.
  // self-heal: if the persisted connection isn't valid for THIS motor's slot count
  // (e.g. a 4-coil connection inherited on a 12-slot motor), switch to the first valid one.
  useEffect(() => {
    if (windConns.length && !windConns.some((c) => c.label === connection)) {
      setConnection(windConns[0].label);
      applyWinding({ connection: windConns[0].label });
    }
  }, [windCfg]); // eslint-disable-line react-hooks/exhaustive-deps

  const PHASE_COLOR: Record<string, string> = { A: '#ef4444', B: '#22c55e', C: '#3b82f6' };

  // ── derived periodicity ───────────────────────────────────────────────────
  const polePairs         = Math.round(numPoles / 2);
  const elecPeriod_deg    = 360 / polePairs;
  const coggingPeriod_deg = 360 / lcm(numSlots, numPoles);

  // ── form state (current = I_phase_rms) ───────────────────────────────────
  const [current,       setCurrent]       = usePersisted('current',   85.0);
  const [frequency,     setFrequency]     = usePersisted('frequency', 921.67);
  const [rpm,           setRpm]           = usePersisted('rpm',       3950.0);
  const [phaseOffset,   setPhaseOffset]   = usePersisted('gamma',     0.0);   // γ [deg]
  // Frequency is DERIVED (f = rpm·pp/60) — recompute whenever rpm OR the pole
  // count changes.  It used to be seeded once from the config's saved operating
  // point and only refreshed on manual rpm edits, so a pole-count change left
  // the stale value (e.g. 921.67 Hz shown for a 20-pole motor instead of 658.33).
  useEffect(() => {
    const f = +(rpm * Math.round(numPoles / 2) / 60).toFixed(2);
    if (Number.isFinite(f) && f > 0 && Math.abs(f - frequency) > 0.01) setFrequency(f);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rpm, numPoles]);
  // Drive mode for the transient: imposed sinusoidal CURRENT (design work) or
  // imposed sinusoidal VOLTAGE (FOC-drive verification — the currents become
  // the machine's own response, incl. the parasitic harmonics a distorted
  // back-EMF forces through the winding + their real extra losses).
  const [drive,   setDrive]   = usePersisted<'current' | 'voltage'>('drive', 'current');
  const [vPeak,   setVPeak]   = usePersisted('vPeak',  30.0);  // phase-voltage amplitude [V]
  const [vDelta,  setVDelta]  = usePersisted('vDelta',  0.0);  // voltage angle δ [°el], same frame as γ

  // γ-as-optimization-variable: a checkbox here marks the load angle γ for the
  // Sweep/Optimize grid (like the chart icon on geometry params).  When checked,
  // gamma_deg becomes a sweep variation; the user sets its min/max/step on the
  // Sweep tab card.  γ is an operating variable — it rotates the current vector,
  // not the mesh — so it never rebuilds geometry.
  const gammaIsVar    = useMotorStore(s => (s.sweepConfig.variations['gamma_deg']?.mode ?? 'fixed') !== 'fixed');
  const updateVariation = useMotorStore(s => s.updateVariation);
  const toggleGammaVar = (on: boolean) =>
    updateVariation('gamma_deg', on
      ? { mode: 'sweep', min: phaseOffset, max: phaseOffset + 30, step: 5 }
      : { mode: 'fixed' });
  const [coilTemp,      setCoilTemp]      = usePersisted('coilTemp',  120.0); // °C
  const [endWinding,    setEndWinding]    = usePersisted('endWinding', 0.0);  // k_end (editable)
  // Last geometry-derived k_end we seeded the cell with — lets us re-seed on a
  // geometry change without clobbering a manual override on an unchanged geom.
  const [endWindingGeo, setEndWindingGeo] = usePersisted('endWindingGeo', 0.0);

  // ── Run-Simulation gating ──────────────────────────────────────────────
  // The FEM transient + field animation only (re)compute when runNonce
  // ticks, i.e. when the user presses "Run Simulation".  This lets them
  // change several parameters (γ, current, mesh settings) and launch ONE
  // solve instead of re-running on every keystroke.
  // Persisted so a computed simulation SURVIVES an F5: on reload runNonce is
  // restored >0, the FEM viewers refetch (a fast backend cache hit) and the
  // result reappears WITHOUT recomputing. It only changes when the user presses
  // Run for a new simulation. (Was useState(0) → every reload wiped it.)
  const [runNonce, setRunNonce] = usePersisted('runNonce', 0);
  const [simBusy,  setSimBusy]  = useState(false);
  // "fresh" tells the backend to discard any frames cached from a Stopped
  // run and recompute everything; cancelledRun remembers that the last run
  // was Stopped so the next Run offers Continue / Start-fresh.
  const [freshRun,     setFreshRun]     = useState(false);
  const [cancelledRun, setCancelledRun] = useState(false);
  const [askResume,    setAskResume]    = useState(false);
  const launchRun = (fresh: boolean) => {
    setFreshRun(fresh);
    setCancelledRun(false);
    setAskResume(false);
    // Persist the operating point to config.yaml on every Run — so it's
    // permanent across sessions/browsers (same principle as Rebuild mesh).
    fetch(`${API}/api/simulation/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        max_current: current, frequency, rpm, phase_offset_deg: phaseOffset,
      }),
    }).catch(() => {});
    setRunNonce(n => n + 1);
  };

  // Persist the operating point to config.yaml on ANY change (debounced), not
  // only on Run — config wins on mount (so presets / Reset apply), so it must
  // stay current or a change made without pressing Run would be lost on reload.
  const simReady = useRef(false);   // gate: persist only AFTER the mount load populated state
  useEffect(() => {
    if (!simReady.current) return;   // skip until the operating point is loaded from config
    const id = setTimeout(() => {
      fetch(`${API}/api/simulation/config`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_current: current, frequency, rpm, phase_offset_deg: phaseOffset }),
      }).catch(() => {});
    }, 700);
    return () => clearTimeout(id);
  }, [current, frequency, rpm, phaseOffset]);

  // Auto-run ONCE when the Simulation tab first becomes visible (on the first
  // open or after an F5). The runNonce===0 guard means it computes exactly one
  // time per page load — the panel stays mounted, so switching to another tab
  // and back keeps the result without recomputing. Without this the right pane
  // is a black void after every reload until you click Run. The backend caches
  // the transient, so the auto-run is a fast cache hit on subsequent reloads.
  // NO auto-run on open.  The transient charts restore the LAST result on mount
  // (from localStorage, or the backend's persisted last via ?restore=true), so a
  // reload SHOWS the previous simulation instead of silently recomputing it.  A
  // fresh state with nothing saved shows the "press Run" prompt; only the Run
  // button (or a settings change + Re-run) computes.  (Was: auto-launchRun when
  // runNonce===0 — that recomputed on every reload where runNonce hadn't been
  // persisted or the backend cache key missed.)
  useEffect(() => { void active; void simBusy; }, [active]);
  // Steps per electrical period (transient time resolution).  Persisted.
  // The text field edits a free string (stepsStr) and only commits a
  // clamped integer on blur / Enter, so typing "12" over "6" works
  // naturally instead of producing "62".
  const [steps,    setSteps]    = usePersisted('stepsPP', 24);   // transient frames/period — single source (optimizer reads this too)
  // Magnet/shaft eddy losses ALWAYS come from the real field solve
  // (J = σ(−∂A/∂t + U), per-magnet ∫J=0, assigned-material σ — the Ansys way),
  // never the classical slab d²/12 estimate.  No toggle: real fields only.
  const fieldLosses = true;
  // Per-element irreversible demagnetisation: a pre-pass sweeps the period at
  // full Br, finds the worst demagnetising field at every magnet element, and
  // de-rates Br along the recoil line (Ansys-style) so the transient torque /
  // back-EMF reflect the weakened magnets.  Opt-in (adds a pre-pass sweep).
  const [demag, setDemag] = usePersisted('demag', false);
  // Band-limit the transient torque to the physical 6·k electrical orders
  // (drops the broadband slip-node noise a balanced 3-phase machine cannot
  // produce).  ON by default; turn off to inspect the raw per-frame torque.
  const [torqueFilter, setTorqueFilter] = usePersisted('torqueFilter', false);
  // Auto-save EVERY simulation change into the active motor ("my copy").
  // syncActiveMotor is internally debounced, so firing on each change is fine.
  useEffect(() => {
    if (!simReady.current) return;
    syncActiveMotor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, frequency, rpm, phaseOffset, steps, coilTemp, endWinding, demag, torqueFilter, connection]);
  const [stepsStr, setStepsStr] = useState(String(steps));
  useEffect(() => { setStepsStr(String(steps)); }, [steps]);
  // HARD upper bound: the sliding-band rotor can only sit on slip-ring nodes,
  // so steps/period must DIVIDE the nodes-per-electrical-period count.  The
  // backend (fem_solver_2d.fem_transient_sliding_band) makes that count adaptive:
  //   _slip_base       = round(1008·(gap_layers+2)/3)        // scales with air-gap layers
  //   _slip_per_period = 24·max(5, ceil(_slip_base/(24·pp))) // multiple of 24, pole-pair-divisible
  // Mirror the SAME formula (incl. the gap_layers term — previously this used a
  // fixed 1008 ≙ gap_layers=1, so it predicted 120 while the solver used 144 at
  // gap_layers=2: the field kept 60 but the solver snapped it to 72).
  const gapLayers = (() => {
    try { return Number(JSON.parse(localStorage.getItem('mesh.gapLayers') ?? '2')) || 2; }
    catch { return 2; }
  })();
  const _slipBase = Math.round(1008 * (Math.max(1, gapLayers) + 2) / 3);
  const SLIP_PER_PERIOD = 24 * Math.max(5, Math.ceil(_slipBase / (24 * Math.max(polePairs, 1))));
  const stepsMax = SLIP_PER_PERIOD;         // nodes per electrical period
  // valid step counts shown in the helper (divisors of stepsMax, ≥12)
  const validSteps = Array.from({ length: stepsMax }, (_, i) => i + 1)
    .filter(d => stepsMax % d === 0 && d >= 12);
  const snapSteps = (v: number) => {
    if (stepsMax % v === 0) return v;       // already a divisor
    let best = stepsMax;
    for (let d = 1; d <= stepsMax; d++)
      if (stepsMax % d === 0 && (Math.abs(d - v) < Math.abs(best - v)
          || (Math.abs(d - v) === Math.abs(best - v) && d > best))) best = d;
    return best;
  };
  // Snap the committed steps onto the valid grid whenever it changes (motor /
  // gap_layers change) or on mount, so the FIELD shows exactly what the solver
  // runs — no "typed 60, ran 72" surprise.
  useEffect(() => {
    const s = snapSteps(steps);
    if (s !== steps) { setSteps(s); setStepsStr(String(s)); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepsMax]);
  const commitSteps = () => {
    const v = Math.round(Number(stepsStr));
    const clamped = Number.isFinite(v)
      ? snapSteps(Math.max(6, Math.min(stepsMax, v))) : steps;
    setSteps(clamped);
    setStepsStr(String(clamped));
  };
  // ── rotor angle / PINN training settings removed ──────────────────────
  // FEM auto-run now sweeps the rotor through the full electrical period,
  // and the PINN run button is gone (no Modulus dependency).  Kept as
  // placeholders so the legacy fetch payload below still type-checks.
  const rotorAngle = 0;
  const maxSteps   = 10000;
  const device: 'cpu' | 'cuda' = 'cpu';

  // ── derived winding values ────────────────────────────────────────────────
  const I_coil_rms  = current / nParallel;                   // Arms per coil
  const I_coil_peak = I_coil_rms * Math.sqrt(2);             // A peak per coil

  // ── job state ─────────────────────────────────────────────────────────────
  const [jobId,   setJobId]   = useState<string | null>(null);
  const [job,     setJob]     = useState<JobStatus | null>(null);
  const [polling, setPolling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── "Apply best design" (Sweep) pushes the optimizer's operating point here ──
  // so simulating the saved geometry reproduces the optimizer's result instead of
  // running at the panel's idle current.  This panel is ALWAYS mounted (hidden
  // when inactive), so it won't re-read on a tab switch — update live via event.
  useEffect(() => {
    const onOp = (e: Event) => {
      const d = (e as CustomEvent).detail || {};
      if (typeof d.current === 'number') setCurrent(d.current);
      if (typeof d.gamma === 'number') setPhaseOffset(d.gamma);
    };
    window.addEventListener('sim-operating-point', onOp as EventListener);
    return () => window.removeEventListener('sim-operating-point', onOp as EventListener);
  }, []);

  // Pin: when a descent design is applied, adopt the run's eval params so re-running
  // the Simulation reproduces the picked point (mesh params are read fresh from
  // localStorage; these persisted-state fields need a live nudge, like the op-point).
  useEffect(() => {
    const onEval = (e: Event) => {
      const p = (e as CustomEvent).detail || {};
      if (typeof p.steps_per_period === 'number') { setSteps(p.steps_per_period); setStepsStr(String(p.steps_per_period)); }
      if (typeof p.coil_temp_c === 'number') setCoilTemp(p.coil_temp_c);
      if (typeof p.end_winding_factor === 'number') setEndWinding(p.end_winding_factor);
      if (typeof p.demag === 'boolean') setDemag(p.demag);
      if (typeof p.torque_filter === 'boolean') setTorqueFilter(p.torque_filter);
    };
    window.addEventListener('descent-eval-params', onEval as EventListener);
    return () => window.removeEventListener('descent-eval-params', onEval as EventListener);
  }, []);

  // ── load server status + geometry ─────────────────────────────────────────
  useEffect(() => {
    fetch(`${API}/api/simulation/status`)
      .then(r => r.json())
      .then(d => {
        setSrvStatus(d);
        if (d.operating_point) {
          setCurrent(d.operating_point.max_current ?? 85);
          // frequency is NOT seeded from the saved operating point — it is
          // fully derived (rpm·pp/60, effect above); seeding it here raced the
          // geometry load and pinned the stale value after a pole-count change.
          setRpm(d.operating_point.rpm ?? 3950);
        }
        simReady.current = true;     // operating point loaded → debounced saves allowed
      })
      .catch(e => setSrvErr(String(e)));

    // Get geometry for periodicity + winding
    fetch(`${API}/api/config`)
      .then(r => r.json())
      .then(d => {
        const g = d.geometry ?? {};
        if (g.num_poles)        setNumPoles(g.num_poles);
        if (g.num_slots)        setNumSlots(g.num_slots);
        if (g.num_wires_per_slot) setNWiresPerSlot(g.num_wires_per_slot);
        // End-winding factor k_end = (π·tooth_w/2 + L)/L, derived from the
        // CURRENT geometry.  Re-seed the (editable) cell whenever the geometry-
        // derived value CHANGES — like the other geometry-derived parameters —
        // but leave a manual override untouched if the geometry is the same.
        const kAuto = Number(d.end_winding_factor);
        if (Number.isFinite(kAuto) && kAuto > 0) {
          // Show the CURRENT geometry's auto k_end (end-turn length / stack).
          // Seed the cell when it's at 0 ("auto"/unset) OR when the geometry
          // changed (kAuto differs from the last seeded value) — so it never
          // sits at a misleading 0 and always tracks the geometry; a manual
          // override is left untouched only while the geometry is unchanged.
          if (!(endWinding > 0) || kAuto !== endWindingGeo) {
            setEndWinding(+kAuto.toFixed(2));
          }
          setEndWindingGeo(kAuto);
        }
      })
      .catch(() => {});
  }, []);

  // ── polling ───────────────────────────────────────────────────────────────
  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setPolling(false);
  }, []);

  useEffect(() => {
    if (!jobId || !polling) return;
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/simulation/result/${jobId}`);
        const d: JobStatus = await r.json();
        setJob(d);
        if (d.status === 'done' || d.status === 'error') stopPolling();
      } catch { /* ignore transient */ }
    }, 1500);
    return stopPolling;
  }, [jobId, polling, stopPolling]);

  // ── run ───────────────────────────────────────────────────────────────────
  const handleRun = async () => {
    setJob(null);
    try {
      const r = await fetch(`${API}/api/simulation/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          max_current:      parseFloat(I_coil_peak.toFixed(2)),  // peak A per coil
          frequency:        frequency,
          rpm:              rpm,
          rotor_angle:      rotorAngle,
          phase_offset_deg: phaseOffset,
          max_steps:        maxSteps,
          device:           device,
        }),
      });
      const d: JobStatus = await r.json();
      setJobId(d.job_id);
      setJob(d);
      setPolling(true);
    } catch (e) {
      setSrvErr(String(e));
    }
  };

  const isRunning = job?.status === 'queued' || job?.status === 'running';

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden', bgcolor: 'var(--panel-2)' }}>

      {/* ── LEFT: controls ── */}
      <Box sx={{
        width: 320, flexShrink: 0, overflowY: 'auto',
        borderRight: '1px solid var(--line-soft)', p: 2,
        display: 'flex', flexDirection: 'column', gap: 2,
      }}>

        {/* Winding connection */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-4)',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Winding Connection
          </Typography>
          <Typography sx={{ fontSize: 10, color: 'var(--line)', mb: 1.2 }}>
            {nCoilsPerPhase} coils/phase · {nWiresPerSlot} wires/slot
          </Typography>

          {/* Connection buttons */}
          <Box sx={{ display: 'flex', gap: 0.75, mb: 1.5, flexWrap: 'wrap' }}>
            {windConns.map(c => (
              <Tooltip key={c.label} title={`${c.n_series} series × ${c.n_parallel} parallel`} placement="top">
                <Button
                  size="small"
                  variant={connection === c.label ? 'contained' : 'outlined'}
                  onClick={() => { setConnection(c.label); applyWinding({ connection: c.label }); }}
                  disabled={isRunning}
                  sx={{ flex: 1, minWidth: 52, fontSize: 11, fontWeight: 700, py: 0.5,
                    textTransform: 'none',
                    ...(connection === c.label ? {} : { color: 'var(--text-3)', borderColor: 'var(--line)' })
                  }}
                >
                  {c.label}
                </Button>
              </Tooltip>
            ))}
          </Box>

        </Box>

        <Divider sx={{ borderColor: 'var(--panel)' }}/>

        {/* ── Coil layout — currents (phase + sign) per slot ── */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-4)',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Coil Layout — currents per slot
          </Typography>

          {/* single-layer winding (this machine has no double-layer variant) */}
          <Typography sx={{ fontSize: 10, color: 'var(--text-3)', mb: 1 }}>
            Single-layer winding · {windCfg?.num_slots ?? 24} slots
          </Typography>

          {/* phase map: one cell per slot (A=red B=green C=blue, +full −faded) */}
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: '2px', mb: 1 }}>
            {(windCfg?.layout_slots || []).map(([ph, d]: [string, number], i: number) => (
              <Tooltip key={i} title={`slot ${i}: ${ph}${d > 0 ? '+' : '−'}`}>
                <Box sx={{ width: 16, height: 18, borderRadius: '2px',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 9, fontWeight: 700, color: '#fff',
                  bgcolor: PHASE_COLOR[ph] || 'var(--text-3)',
                  opacity: d > 0 ? 1 : 0.4,
                  border: d > 0 ? '1px solid rgba(255,255,255,0.45)' : '1px solid transparent' }}>
                  {d > 0 ? ph : ph.toLowerCase()}
                </Box>
              </Tooltip>
            ))}
          </Box>

        </Box>

        <Divider sx={{ borderColor: 'var(--panel)' }}/>

        {/* Operating point */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-4)',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1.5 }}>
            Operating Point
          </Typography>

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            {/* Drive mode: imposed sinusoidal current (design work) vs imposed
                sinusoidal voltage (FOC-drive verification — currents become the
                machine's own response incl. back-EMF-harmonic parasitics). */}
            <Box sx={{ display: 'flex', gap: 0.5 }}>
              {(['current', 'voltage'] as const).map(m => (
                <Button key={m} size="small" fullWidth disabled={isRunning}
                  variant={drive === m ? 'contained' : 'outlined'}
                  onClick={() => setDrive(m)}
                  sx={{ fontSize: '0.7rem', textTransform: 'none', py: 0.3 }}>
                  {m === 'current' ? 'Current drive' : 'Voltage drive'}
                </Button>
              ))}
              <HelpTip title={'Current drive: imposed sinusoidal phase currents (design work). ' +
                'Voltage drive: imposed sinusoidal LINE voltages (floating neutral, as a real ' +
                'wye FOC inverter) — the currents are the machine’s own response, including the ' +
                'parasitic harmonics a distorted back-EMF forces through the winding; ΔP_harm ' +
                'compares against a current-drive reference at the same fundamental current. ' +
                'Needs ≥48 steps/period. Runs without rotor-eddy dynamics (magnet/shaft eddy ' +
                'losses excluded on both sides of the comparison). Near no-load (V ≈ back-EMF) ' +
                'the current is extremely sensitive to V — like the real machine open-loop.'} />
            </Box>
            {drive === 'current' ? (
            <TextField label="I phase RMS (Arms)" type="number" size="small" fullWidth
              value={current} onChange={e => setCurrent(+e.target.value)}
              inputProps={{ step: 5, min: 0, max: 500 }} disabled={isRunning}
              InputProps={{ endAdornment: <HelpTip title={`I coil peak = ${I_coil_peak.toFixed(1)} A → sent to solver`} /> }}/>
            ) : (<>
            <TextField label="V phase peak (V)" type="number" size="small" fullWidth
              value={vPeak} onChange={e => setVPeak(+e.target.value)}
              inputProps={{ step: 1, min: 0, max: 2000 }} disabled={isRunning}
              InputProps={{ endAdornment: <HelpTip title={'Amplitude of the imposed sinusoidal phase voltage. ' +
                'Tip: run a current-drive simulation first — its V₁ (fundamental phase voltage) ' +
                'is the natural starting value.'} /> }}/>
            <TextField label="δ — voltage angle (°el)" type="number" size="small" fullWidth
              value={vDelta} onChange={e => setVDelta(+e.target.value)}
              inputProps={{ step: 5, min: -180, max: 180 }} disabled={isRunning}
              InputProps={{ endAdornment: <HelpTip title={'Voltage-vector angle in the same electrical frame as γ ' +
                '(0° = q-axis). The load angle: more δ → more torque until pull-out.'} /> }}/>
            </>)}
            {/* Frequency ↔ Speed are mutually locked:
                  f_elec [Hz]  =  rpm × pole_pairs / 60
                  rpm          =  f_elec × 60 / pole_pairs
                Editing one immediately recomputes the other. */}
            <TextField label="Speed (rpm)" type="number" size="small" fullWidth
              value={Number(rpm.toFixed(0))}
              onChange={e => {
                const r = +e.target.value;
                setRpm(r);
                setFrequency(+(r * polePairs / 60).toFixed(2));
              }}
              inputProps={{ step: 100, min: 0 }} disabled={isRunning}
              InputProps={{ endAdornment: <HelpTip title={`electrical f = ${Number(frequency.toFixed(1))} Hz`} /> }}/>
            {/* Frequency is DERIVED from rpm (f = rpm × pole_pairs / 60) — read-only,
                single source is the speed above.  Editing rpm recomputes it. */}
            <TextField label="Frequency (Hz) — derived" type="number" size="small" fullWidth
              value={Number(frequency.toFixed(2))}
              disabled
              InputProps={{ endAdornment: <HelpTip title={`f = rpm × ${polePairs} / 60 — derived from speed`} /> }}/>
            {/* The "Rotor Angle (°)" initial-position field was removed —
                in the auto-run architecture the Field Animation sweeps the
                whole 25.71° electrical period itself, so picking a single
                starting angle adds nothing.  Only the current-vector
                load-angle γ is user-facing now. */}

            {/* Current-vector angle (γ) — the only operating-point control
                left.  Convention:  I_total points at (90° + γ) electrical
                relative to the rotor d-axis.  γ = 0 keeps I purely on the
                q-axis (max torque); γ < 0 advances the vector for field
                weakening at high speed; γ > 0 retards (used to flatten
                cogging-torque ripple in some control schemes). */}
            <TextField
              label="γ — current-vector offset from q-axis (°)"
              type="number" size="small" fullWidth
              value={phaseOffset}
              onChange={e => setPhaseOffset(+e.target.value)}
              inputProps={{ step: 5, min: -90, max: 90 }}
              InputProps={{ endAdornment: <HelpTip title={`I direction = 90° + γ elec from d-axis.  ` +
                          `γ=0 → q-axis (max torque),  γ=±90 → d-axis (field weakening)`} /> }}
              disabled={isRunning}
            />

            {/* ── Copper-loss physics: temperature + end-winding ──
                The 2-D field only sees the in-slot (active) copper.  ρ_Cu rises
                with coil temperature, and the end-turns that loop outside the
                stack add series resistance the 2-D model can't see. */}
            <TextField
              label="Coil temperature (°C)"
              type="number" size="small" fullWidth
              value={coilTemp}
              onChange={e => setCoilTemp(+e.target.value)}
              inputProps={{ step: 10, min: -40, max: 220 }}
              InputProps={{ endAdornment: <HelpTip title={`ρ_Cu(T): +0.393 %/°C from 20 °C → higher copper loss`} /> }}
              disabled={isRunning}
            />
            <TextField
              label="End-winding factor k_end (editable)"
              type="number" size="small" fullWidth
              value={endWinding}
              onChange={e => setEndWinding(+e.target.value)}
              inputProps={{ step: 0.05, min: 0, max: 6 }}
              InputProps={{ endAdornment: <HelpTip title={`End-winding copper: scales the coil resistance/loss by the end-turn length.` +
                          ` k_end = (π·tooth_w/2 + L_stack)/L_stack = ${endWindingGeo ? endWindingGeo.toFixed(3) : '—'} for THIS geometry.` +
                          ` AUTO by default — recomputed for every geometry and used in all simulations AND optimizations` +
                          ` (0 in the field also means auto). Type a value only to override for this run.`} /> }}
              disabled={isRunning}
            />

            {/* Cooling inputs now live in the Thermal (Temp) field view's toolbar
                (Air blow-speed / Liquid inlet+outlet).  The coupled EM↔thermal solve
                reads its cooling via getCoolingPayload() (localStorage `sim.cool.*`,
                still editable from the Configure → Thermal panel). */}

            {/* Slot currents bar / PINN Training settings / RUN SIMULATION
                button removed.  The Simulation tab now uses real FEM via the
                Physics Dashboard auto-runs on the right — no PINN training
                step is needed.  The instantaneous phase currents are still
                visible inside the Transient I(t) chart with full per-step
                detail. */}
          </Box>
        </Box>

        {srvErr && (
          <Alert severity="error" sx={{ fontSize: 11 }}>{srvErr}</Alert>
        )}

        {/* ── Run Simulation — launches ONE FEM solve with the current
              operating point + mesh settings.  Flows directly under the fields
              above (the panel scrolls if needed); no bottom-pin, which used to
              leave a big empty gap now that the cooling/PINN sections are gone. ── */}
        <Box sx={{ pt: 1 }}>
          <TextField
            label="Steps per electrical period"
            type="text" size="small" fullWidth
            value={stepsStr}
            onChange={e => setStepsStr(e.target.value.replace(/[^0-9]/g, ''))}
            onBlur={commitSteps}
            onKeyDown={e => { if (e.key === 'Enter') { commitSteps(); (e.target as HTMLInputElement).blur(); } }}
            inputProps={{ inputMode: 'numeric', pattern: '[0-9]*' }}
            disabled={simBusy}
            InputProps={{ endAdornment: <HelpTip title={`Transient time resolution. Valid = divisors of ${stepsMax} (slip nodes per electrical period): ${validSteps.join(', ')}. Other values snap to the nearest so the rotor lands on whole mesh nodes.`} /> }}
            sx={{ mb: 1.25 }}
          />
          {/* Per-element irreversible demagnetisation (Ansys-style).  A pre-pass
              sweeps the period at full Br, finds the worst demag field at every
              magnet element, and de-rates Br on the recoil line → the torque /
              back-EMF reflect the weakened magnets, plus a Demag-% map. */}
          <Tooltip title="Account for irreversible magnet demagnetisation. A pre-pass sweeps the whole period at full strength, finds the worst demagnetising field H at EVERY magnet element, and permanently de-rates Br along the recoil line where H crosses the BH-curve knee (per element, like Ansys). The torque and back-EMF then reflect the weakened magnets, and a Demag-% map is produced. Adds a pre-pass sweep (the one-time mesh build dominates, so overhead is modest)." placement="right">
            <FormControlLabel
              sx={{ mt: -0.5, mb: 0.75, ml: 0.25 }}
              control={
                <Checkbox size="small" checked={demag}
                  onChange={e => setDemag(e.target.checked)}
                  disabled={simBusy}
                  sx={{ p: 0.5, color: 'var(--text-4)', '&.Mui-checked': { color: '#c084fc' } }} />
              }
              label={
                <Typography variant="caption" sx={{ color: demag ? '#c084fc' : 'var(--text-2)' }}>
                  Demagnetisation — de-rate torque (FEM, per element)
                </Typography>
              }
            />
          </Tooltip>
          {/* Torque band-limit filter: keep only the physical 6·k electrical
              orders.  The sliding band steps the rotor across discrete slip
              nodes, injecting broadband ripple a balanced 3-phase machine
              cannot produce — ON drops it (mean preserved), OFF shows raw. */}
          <Tooltip title="Band-limit the torque waveform to the physical 6·k electrical orders (6, 12, 18…). A balanced 3-phase machine can only produce torque ripple at these orders; everything else is numerical slip-node noise that does not converge with mesh refinement. The average torque is preserved exactly. Turn off to inspect the raw per-frame solver torque." placement="right">
            <FormControlLabel
              sx={{ mt: -0.5, mb: 0.75, ml: 0.25 }}
              control={
                <Checkbox size="small" checked={torqueFilter}
                  onChange={e => setTorqueFilter(e.target.checked)}
                  disabled={simBusy}
                  sx={{ p: 0.5, color: 'var(--text-4)', '&.Mui-checked': { color: '#34d399' } }} />
              }
              label={
                <Typography variant="caption" sx={{ color: torqueFilter ? '#34d399' : 'var(--text-2)' }}>
                  Torque filter — physical 6·k orders only
                </Typography>
              }
            />
          </Tooltip>
          {simBusy ? (
            <Button
              fullWidth
              variant="contained"
              onClick={() => { window.dispatchEvent(new CustomEvent('sim:stop')); setCancelledRun(true); }}
              startIcon={<StopIcon />}
              sx={{
                py: 1.2, fontWeight: 700, fontSize: 13, letterSpacing: 0.5,
                textTransform: 'none', borderRadius: 2,
                bgcolor: '#dc2626', '&:hover': { bgcolor: '#b91c1c' },
                boxShadow: '0 2px 12px rgba(220,38,38,0.4)',
              }}
            >
              Stop Simulation
            </Button>
          ) : (
            <Button
              fullWidth
              variant="contained"
              onClick={() => { commitSteps(); if (cancelledRun) setAskResume(true); else launchRun(true); }}
              startIcon={<PlayArrowIcon />}
              sx={{
                py: 1.2, fontWeight: 700, fontSize: 13, letterSpacing: 0.5,
                textTransform: 'none', borderRadius: 2,
                bgcolor: '#2563eb', '&:hover': { bgcolor: '#1d4ed8' },
                boxShadow: '0 2px 12px rgba(37,99,235,0.4)',
              }}
            >
              {runNonce === 0 ? 'Run Simulation' : 'Re-run Simulation'}
            </Button>
          )}
          <Typography sx={{ fontSize: 10, color: 'var(--text-4)', textAlign: 'center', mt: 0.75 }}>
            {simBusy
              ? 'Solving the transient — press Stop to cancel'
              : cancelledRun
                ? 'Stopped — Run to resume the finished frames or start fresh'
                : 'Edit γ / current / mesh settings, then launch one solve'}
          </Typography>
        </Box>

        {/* ── Resume / fresh dialog (after a Stop) ── */}
        <Dialog open={askResume} onClose={() => setAskResume(false)}
          PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 2 } }}>
          <DialogTitle sx={{ fontSize: 15, color: 'var(--text-0)' }}>Resume the stopped run?</DialogTitle>
          <DialogContent>
            <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }}>
              The previous transient was stopped part-way.  <b>Continue</b> keeps the
              frames already solved and only computes the missing ones.
              <b> Start fresh</b> discards them and recomputes the whole period.
            </Typography>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2, gap: 1 }}>
            <Button onClick={() => launchRun(true)} sx={{ textTransform: 'none', color: 'var(--text-2)' }}>
              Start fresh
            </Button>
            <Button onClick={() => launchRun(false)} variant="contained"
              sx={{ textTransform: 'none', bgcolor: '#2563eb', '&:hover': { bgcolor: '#1d4ed8' } }}>
              Continue
            </Button>
          </DialogActions>
        </Dialog>
      </Box>

      {/* ── RIGHT: results ── */}
      <Box sx={{ flex: 1, overflowY: 'auto', p: 3, display: 'flex', flexDirection: 'column', gap: 3 }}>

        {/* Header + Physics overview card removed by user request.
            • The "2D Magnetostatics / Governing equation / Rotor
              periodicity / Domains" block is dropped entirely.
            • Rotor-periodicity info now lives in the LEFT control
              panel, in a compact 2×2 grid right under the Solver
              badge.  See <Box>{Rotor Periodicity}</Box> above. */}
        <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
          borderRadius: 2, display: 'none' }}>
          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1.5,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Governing Equation
          </Typography>
          <Box sx={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-2)', lineHeight: 2 }}>
            <Box>∂/∂x(ν ∂A_z/∂x) + ∂/∂y(ν ∂A_z/∂y) = −J_z</Box>
            <Box sx={{ color: 'var(--text-4)', fontSize: 10, mt: 0.5 }}>
              ν = reluctivity = 1/(μ₀ μᵣ) &nbsp;|&nbsp;
              B_x = ∂A_z/∂y &nbsp;|&nbsp;
              B_y = −∂A_z/∂x
            </Box>
          </Box>

          <Divider sx={{ borderColor: 'var(--panel)', my: 1.5 }}/>

          {/* Periodicity info */}
          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Rotor Periodicity
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, mb: 1.5 }}>
            {[
              { label: 'Pole pairs',       value: polePairs.toString(),                    sub: `${numPoles} poles / 2` },
              { label: 'Electrical period', value: `${elecPeriod_deg.toFixed(2)}°`,        sub: `360° / ${polePairs}` },
              { label: 'Cogging period',    value: `${coggingPeriod_deg.toFixed(3)}°`,     sub: `360° / LCM(${numSlots},${numPoles})` },
              { label: 'Cogging per elec', value: Math.round(elecPeriod_deg / coggingPeriod_deg).toString(), sub: 'samples for full curve' },
            ].map(item => (
              <Box key={item.label} sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, p: 1,
                border: '1px solid var(--line-soft)' }}>
                <Typography sx={{ fontSize: 9, color: 'var(--text-4)', textTransform: 'uppercase',
                  letterSpacing: '0.08em' }}>{item.label}</Typography>
                <Typography sx={{ fontSize: 14, fontWeight: 700, color: '#93c5fd',
                  fontVariantNumeric: 'tabular-nums' }}>{item.value}</Typography>
                <Typography sx={{ fontSize: 9, color: 'var(--line)' }}>{item.sub}</Typography>
              </Box>
            ))}
          </Box>
          <Alert severity="info" sx={{ fontSize: 10, py: 0.5, mb: 1.5,
            '& .MuiAlert-message': { py: 0 } }}>
            Full T(θ) curve needs {Math.round(elecPeriod_deg / coggingPeriod_deg)} points × one simulation each,
            or one parametric PINN with θ as input.
          </Alert>

          <Divider sx={{ borderColor: 'var(--panel)', my: 1.5 }}/>

          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Domains
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
            {[
              { name: 'Stator Core', color: '#3b82f6',  pde: 'μᵣ = 5000' },
              { name: 'Air Gap',     color: 'var(--text-2)',  pde: 'μᵣ = 1' },
              { name: 'Rotor Core',  color: '#2563eb',  pde: 'μᵣ = 5000' },
              { name: 'Magnets',     color: '#ef4444',  pde: 'Br = 1.2 T' },
              { name: 'Windings',    color: '#f59e0b',  pde: 'J = ±J_peak' },
              { name: 'Shaft',       color: 'var(--text-3)',  pde: 'μᵣ = 1000' },
            ].map(d => (
              <Tooltip key={d.name} title={`PDE: ${d.pde}`} placement="top">
                <Chip label={d.name} size="small" sx={{
                  fontSize: 10, height: 20,
                  bgcolor: `${d.color}18`, color: d.color,
                  border: `1px solid ${d.color}44`,
                  cursor: 'help',
                }}/>
              </Tooltip>
            ))}
          </Box>
        </Paper>

        {/* Job progress */}
        {job && (
          <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2, borderRadius: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
              <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-4)',
                textTransform: 'uppercase', letterSpacing: 1 }}>
                Job {job.job_id}
              </Typography>
              {job.status === 'done'  && <CheckCircleIcon sx={{ fontSize: 16, color: '#4ade80' }}/>}
              {job.status === 'error' && <ErrorIcon       sx={{ fontSize: 16, color: '#f87171' }}/>}
              {isRunning && <CircularProgress size={14} sx={{ color: '#3b82f6' }}/>}
            </Box>

            <Box sx={{ display: 'flex', gap: 1, mb: 1.5 }}>
              <Chip
                label={job.status.toUpperCase()}
                size="small"
                sx={{ fontSize: 10,
                  bgcolor: job.status === 'done' ? 'var(--ok-bg)' : job.status === 'error' ? '#7f1d1d' : 'var(--line-accent)',
                  color:   job.status === 'done' ? '#4ade80' : job.status === 'error' ? '#f87171' : '#93c5fd',
                }}
              />
              {job.elapsed_s != null && (
                <Chip label={`${job.elapsed_s.toFixed(1)}s`} size="small"
                  sx={{ fontSize: 10, bgcolor: 'var(--panel)', color: 'var(--text-3)' }}/>
              )}
            </Box>

            <LinearProgress
              variant="determinate"
              value={job.progress * 100}
              sx={{ borderRadius: 1, height: 4, bgcolor: 'var(--panel)',
                '& .MuiLinearProgress-bar': {
                  bgcolor: job.status === 'error' ? '#ef4444' : '#3b82f6',
                }
              }}
            />

            {job.error && (
              <Alert severity="error" sx={{ mt: 1.5, fontSize: 11 }}>{job.error}</Alert>
            )}
          </Paper>
        )}

        {/* Results */}
        {job?.result && (
          <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2, borderRadius: 2 }}>
            {job.result.status === 'dry_run' && (
              <Alert severity="info" sx={{ fontSize: 10, mb: 1.5, py: 0.5,
                '& .MuiAlert-message': { py: 0 } }}>
                Dry-run: copper losses computed. Install NVIDIA Modulus for torque, iron &amp; magnet losses, η.
              </Alert>
            )}

            {/* ── Efficiency banner ── */}
            {job.result.efficiency_pct != null && (
              <Box sx={{ textAlign: 'center', py: 1.5, mb: 1.5,
                bgcolor: 'var(--ok-bg)', borderRadius: 1, border: '1px solid var(--ok-bg)' }}>
                <Typography sx={{ fontSize: 28, fontWeight: 800,
                  color: job.result.efficiency_pct > 90 ? '#4ade80' : '#fbbf24' }}>
                  {job.result.efficiency_pct.toFixed(1)} %
                </Typography>
                <Typography sx={{ fontSize: 10, color: '#16a34a' }}>efficiency η</Typography>
              </Box>
            )}

            {/* ── Power balance ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#3b82f6',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Power Balance
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 1.5 }}>
              <Row label="Torque"    value={job.result.torque_Nm.toFixed(4)} unit="N·m"
                   highlight={job.result.torque_Nm !== 0}/>
              <Row label="P mech"   value={job.result.P_mech_W != null ? job.result.P_mech_W.toFixed(0) : '—'} unit="W"
                   highlight={(job.result.P_mech_W ?? 0) > 0}/>
              <Row label="P input"  value={job.result.P_input_W != null ? job.result.P_input_W.toFixed(0) : '—'} unit="W"/>
            </Box>

            {/* ── Losses breakdown ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#ef4444',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Losses
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 1.5 }}>
              <Row label="Cu (winding)"   value={job.result.P_cu_total_W != null ? job.result.P_cu_total_W.toFixed(1) : '—'} unit="W"/>
              <Row label="Fe stator"      value={job.result.P_fe_stator_W != null ? job.result.P_fe_stator_W.toFixed(1) : '— (need Modulus)'} unit={job.result.P_fe_stator_W != null ? 'W' : ''}/>
              <Row label="Fe rotor"       value={job.result.P_fe_rotor_W  != null ? job.result.P_fe_rotor_W.toFixed(1)  : '— (need Modulus)'} unit={job.result.P_fe_rotor_W  != null ? 'W' : ''}/>
              <Row label="Mag eddy"       value={job.result.P_mag_eddy_W  != null ? job.result.P_mag_eddy_W.toFixed(1)  : '— (need Modulus)'} unit={job.result.P_mag_eddy_W  != null ? 'W' : ''}/>
              <Row label="Total losses"   value={job.result.P_loss_total_W != null ? job.result.P_loss_total_W.toFixed(1) : '—'} unit="W"/>
            </Box>

            {/* ── Winding params ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: 'var(--text-4)',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Winding (computed)
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 1.5 }}>
              <Row label="R phase"    value={job.result.R_phase_ohm != null ? (job.result.R_phase_ohm * 1000).toFixed(2) : '—'} unit="mΩ"/>
              <Row label="L turn"     value={job.result.L_turn_mm != null ? job.result.L_turn_mm.toFixed(1) : '—'} unit="mm"/>
              <Row label="I coil rms" value={job.result.I_coil_rms_A != null ? job.result.I_coil_rms_A.toFixed(1) : '—'} unit="A"/>
            </Box>

            {/* ── Field ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: 'var(--text-4)',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Magnetic Field
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
              <Row label="B max"  value={job.result.B_max_T.toFixed(4)}  unit="T"/>
              <Row label="B mean" value={job.result.B_mean_T.toFixed(4)} unit="T"/>
              <Row label="Steps"  value={job.result.training_steps.toString()}/>
            </Box>

            <Divider sx={{ borderColor: 'var(--panel)', my: 1.5 }}/>

            <Typography sx={{ fontSize: 10, color: 'var(--line)' }}>
              Next steps: open output_dir in ParaView to visualise A_z, B field,
              and H field maps.
            </Typography>
          </Paper>
        )}

        {/* No-job empty state ("Set operating point and press Run") removed
            — the right panel now goes straight from the operating-point
            controls to the Physics Dashboard, which auto-runs the FEM
            transient on mount.  No manual Run button needed. */}

        {/* Analytical SimulationCharts (currents / voltages / losses) removed —
            the FEM transient panel inside PhysicsDashboard below shows all
            three waveforms computed from the actual mesh solve. */}

        {/* ── Multiphysics: coupled EM↔thermal (on/off, slow — runs the
            solver.em_thermal module: losses ↔ temperature to equilibrium). ── */}
        <CoupledEmThermal I_phase_rms={current} gamma_deg={phaseOffset} steps={steps} />

        {/* ── Physics dashboard (the standard FEM interface) — FIRST so the
            FEM results + fields + transient are the prominent view ── */}
        <PhysicsDashboard
          rotorAngle_deg={rotorAngle}
          gamma_deg={phaseOffset}
          I_phase_rms={current}
          pinnLosses={job?.result ?? null}
          runNonce={runNonce}
          fresh={freshRun}
          onBusyChange={setSimBusy}
          steps={steps}
          fieldLosses={fieldLosses}
          demag={demag}
          torqueFilter={torqueFilter}
          drive={drive}
          vPeak={vPeak}
          vDelta={vDelta}
        />

      </Box>
    </Box>
  );
};

export default SimulationPanel;
