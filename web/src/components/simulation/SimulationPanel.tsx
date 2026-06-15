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
import PlayArrowIcon    from '@mui/icons-material/PlayArrow';
import StopIcon         from '@mui/icons-material/Stop';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import CheckCircleIcon  from '@mui/icons-material/CheckCircle';
import ErrorIcon        from '@mui/icons-material/Error';
import BoltIcon         from '@mui/icons-material/Bolt';
import SimulationCharts from './SimulationCharts';
import PhysicsDashboard from './PhysicsDashboard';
import ModelCompare from './ModelCompare';
import SaveToMotorButton from '../common/SaveToMotorButton';
import { syncActiveMotor } from '../common/motorSettings';
import SaveIcon from '@mui/icons-material/Save';
import type { TransientSummary } from './SummaryTable';

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
    <Typography sx={{ fontSize: 11, color: '#64748b' }}>{label}</Typography>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <Typography sx={{ fontSize: 12, fontWeight: 600, color: highlight ? '#4ade80' : '#e2e8f0' }}>
        {value}
      </Typography>
      {unit && <Typography sx={{ fontSize: 10, color: '#475569' }}>{unit}</Typography>}
    </Box>
  </Box>
);

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────
// ── helpers ───────────────────────────────────────────────────────────────────
function gcd(a: number, b: number): number { return b === 0 ? a : gcd(b, a % b); }
function lcm(a: number, b: number): number { return (a * b) / gcd(a, b); }

// ── winding connection definitions ───────────────────────────────────────────
type ConnectionKey = '4S' | '2P2S' | '4P';
const CONNECTIONS: { key: ConnectionKey; label: string; nP: number; nS: number; desc: string }[] = [
  { key: '4S',   label: '4S',   nP: 1, nS: 4, desc: '4 series — max voltage' },
  { key: '2P2S', label: '2P·2S', nP: 2, nS: 2, desc: '2 parallel × 2 series' },
  { key: '4P',   label: '4P',   nP: 4, nS: 1, desc: '4 parallel — max current' },
];

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
  const [connection, setConnection] = usePersisted<ConnectionKey>('connection', '2P2S');
  const connDef = CONNECTIONS.find(c => c.key === connection)!;

  // ── winding LAYOUT (per-slot phase + sign = coil currents) ─────────────────
  const [windCfg, setWindCfg]       = useState<any>(null);     // /api/winding/config
  const [layoutDraft, setLayoutDraft] = useState<string>('');
  const [layoutBusy, setLayoutBusy] = useState<boolean>(false);
  const [layoutMsg, setLayoutMsg]   = useState<string | null>(null);

  const loadWinding = useCallback(() => {
    fetch(`${API}/api/winding/config`)
      .then(r => r.json())
      .then(d => {
        setWindCfg(d); setLayoutDraft(d.layout || '');
        // config.yaml is the persistent, cross-browser source for the connection;
        // adopt it on load so a fresh browser / other tab reflects the real value.
        if (d.connection) setConnection(d.connection);
      })
      .catch(() => {});
  }, []);
  useEffect(() => { loadWinding(); }, [loadWinding]);

  const applyWinding = useCallback((patch: Record<string, any>) => {
    setLayoutBusy(true); setLayoutMsg(null);
    fetch(`${API}/api/winding/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
      .then(async r => {
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
        return j;
      })
      .then(() => { setLayoutMsg('✓ applied — re-run the simulation'); loadWinding(); })
      .catch(e => setLayoutMsg('✗ ' + String(e.message || e)))
      .finally(() => setLayoutBusy(false));
  }, [loadWinding]);

  // Phase → colour for the slot map (A=red, B=green, C=blue); +full, −faded.
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

  // ── d-axis angle optimisation (sweep γ ∈ [−30,30], find max torque) ────────
  // NOTE: must be declared AFTER `current` — its dependency array reads it.
  const [daxisSweep, setDaxisSweep] = useState<any>(null);
  const [daxisBusy,  setDaxisBusy]  = useState<boolean>(false);
  const runDaxisSweep = useCallback(() => {
    setDaxisBusy(true);
    fetch(`${API}/api/simulation/physics/daxis_sweep?lo=-30&hi=30&step=2`
          + `&I_phase_rms=${current}&mesh_size_mm=4`)
      .then(r => r.json())
      .then(d => { setDaxisSweep(d); setDaxisBusy(false); })
      .catch(() => setDaxisBusy(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

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
  const [steps,    setSteps]    = usePersisted('stepsPP', 72);   // transient pts/period (matches slip-node grid)
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
  const [torqueFilter, setTorqueFilter] = usePersisted('torqueFilter', true);
  // Auto-save EVERY simulation change into the active motor ("my copy").
  // syncActiveMotor is internally debounced, so firing on each change is fine.
  useEffect(() => {
    if (!simReady.current) return;
    syncActiveMotor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, frequency, rpm, phaseOffset, steps, coilTemp, endWinding, demag, torqueFilter, connection]);
  const [stepsStr, setStepsStr] = useState(String(steps));
  useEffect(() => { setStepsStr(String(steps)); }, [steps]);
  // HARD upper bound: the sliding-band rotor can only sit on slip-ring nodes —
  // 1008 ring nodes / pole_pairs positions per electrical period (72 for this
  // 28-pole motor).  The backend silently snaps any request to a DIVISOR of
  // that (144→72, 100→72, 50→36), which looked like "the step won't change".
  // Clamp + snap in the UI so what you type is what actually runs.
  const N_SLIP = 1008;                      // backend slip-ring node count (360°)
  const stepsMax = Math.max(6, Math.floor(N_SLIP / Math.max(polePairs, 1)));
  const snapSteps = (v: number) => {
    if (stepsMax % v === 0) return v;       // already a divisor
    let best = stepsMax;
    for (let d = 1; d <= stepsMax; d++)
      if (stepsMax % d === 0 && (Math.abs(d - v) < Math.abs(best - v)
          || (Math.abs(d - v) === Math.abs(best - v) && d > best))) best = d;
    return best;
  };
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
  const I_coil_rms  = current / connDef.nP;                  // Arms per coil
  const I_coil_peak = I_coil_rms * Math.sqrt(2);             // A peak per coil
  const ampTurns    = nWiresPerSlot * I_coil_rms;            // A·turns per slot

  // ── job state ─────────────────────────────────────────────────────────────
  const [jobId,   setJobId]   = useState<string | null>(null);
  const [job,     setJob]     = useState<JobStatus | null>(null);
  const [polling, setPolling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── load server status + geometry ─────────────────────────────────────────
  useEffect(() => {
    fetch(`${API}/api/simulation/status`)
      .then(r => r.json())
      .then(d => {
        setSrvStatus(d);
        if (d.operating_point) {
          setCurrent(d.operating_point.max_current ?? 85);
          setFrequency(d.operating_point.frequency_hz ?? 921.67);
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
        if (Number.isFinite(kAuto) && kAuto > 0 && kAuto !== endWindingGeo) {
          setEndWinding(+kAuto.toFixed(2));
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

  // ── Save-simulation snapshot (for the Compare tab) ──────────────────────
  // The rich FEM result summary is produced by the transient panel; we lift it
  // here via PhysicsDashboard.onSummary, bundle it with the input parameters
  // (operating point + mesh + geometry), and POST it to the saved-sims store.
  const [lastSummary, setLastSummary] = useState<TransientSummary | null>(null);
  const [saveName, setSaveName] = useState('');
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveMsg,  setSaveMsg]  = useState<string | null>(null);
  const [runSig,   setRunSig]   = useState<string | null>(null);
  const readMesh = <T,>(k: string, def: T): T => {
    try { const r = localStorage.getItem(`mesh.${k}`); return r == null ? def : (JSON.parse(r) as T); }
    catch { return def; }
  };
  // Signature of every input that changes the FEM result.  Re-read each render
  // (incl. localStorage mesh.* which the Mesh tab edits) so we can tell when the
  // displayed result no longer matches the current settings.
  // NB: torqueFilter is NOT here — band-limiting is a client-side display
  // toggle (the backend always returns both raw + filtered series), so flipping
  // it must NOT mark the result stale or require a re-run.
  const computeSig = () => JSON.stringify({
    I: current, g: phaseOffset, rpm, steps, coilTemp, endWinding, connection,
    fl: fieldLosses, dm: demag,
    ns: readMesh('nSectors', 4), ms: readMesh('meshSize', 4.0), mn: readMesh('minSize', 0.3),
    gl: readMesh('gapLayers', 2), oa: readMesh('outerAir', 1.3), nd: readMesh('normalDev', 6),
  });
  // Snapshot the run's inputs the moment a run is launched (runNonce ticks).
  useEffect(() => { setRunSig(computeSig()); }, [runNonce]);  // eslint-disable-line react-hooks/exhaustive-deps
  // The shown summary is STALE if any sim input changed since that run — then
  // saving would store the NEW params against the OLD result (the "all rows
  // identical" bug).  Block Save until the user re-runs.
  const settingsChanged = !!lastSummary && runSig != null && computeSig() !== runSig;
  const saveSimulation = async () => {
    if (settingsChanged) { setSaveMsg('Settings changed — press Re-run Simulation first'); return; }
    if (!lastSummary) { setSaveMsg('Run a simulation first'); return; }
    setSaveBusy(true); setSaveMsg(null);
    try {
      // Snapshot all numeric geometry params so any geometry change is diffable.
      let geo: Record<string, number> = {};
      try {
        const cfg = await fetch(`${API}/api/config`).then(r => r.json());
        const g = cfg.geometry ?? {};
        geo = Object.fromEntries(
          Object.entries(g).filter(([, v]) => typeof v === 'number')
        ) as Record<string, number>;
      } catch { /* geometry optional */ }
      const params = {
        ...geo,
        I_phase_rms: current,
        gamma_deg: phaseOffset,
        rpm,
        frequency_hz: frequency,
        coil_temp_c: coilTemp,
        end_winding_factor: endWinding,
        connection,
        steps_per_period: steps,
        field_losses: fieldLosses,
        demag,
        torque_filter: torqueFilter,
        n_sectors: readMesh('nSectors', 4),
        mesh_size_mm: readMesh('meshSize', 4.0),
        min_size_mm: readMesh('minSize', 0.3),
        num_poles: numPoles,
        num_slots: numSlots,
        num_wires_per_slot: nWiresPerSlot,
      };
      const name = saveName.trim() || `sim ${new Date().toLocaleString()}`;
      const r = await fetch(`${API}/api/sims/saved`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, params, results: lastSummary }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setSaveMsg('✓ saved — open the Compare tab');
      setSaveName('');
    } catch (e: any) {
      setSaveMsg('✗ ' + String(e.message || e));
    } finally {
      setSaveBusy(false);
    }
  };

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden', bgcolor: '#060d17' }}>

      {/* ── LEFT: controls ── */}
      <Box sx={{
        width: 320, flexShrink: 0, overflowY: 'auto',
        borderRight: '1px solid #1e293b', p: 2,
        display: 'flex', flexDirection: 'column', gap: 2,
      }}>

        {/* Solver badge */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Solver
          </Typography>
          {srvStatus ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Chip icon={<BoltIcon sx={{ fontSize: 13 }}/>} label="2-D FEM"
                size="small" color="success" sx={{ fontSize: 10 }} />
              <Tooltip title={srvStatus.solver}>
                <InfoOutlinedIcon sx={{ fontSize: 14, color: '#475569', cursor: 'help' }}/>
              </Tooltip>
            </Box>
          ) : (
            <Chip label="Connecting…" size="small" sx={{ fontSize: 10 }}/>
          )}
          {srvStatus && (
            <Typography sx={{ fontSize: 10, color: '#334155', mt: 0.5 }}>
              Br = {srvStatus.operating_point.Br_magnet_T.toFixed(2)} T
              &nbsp;(from materials config)
            </Typography>
          )}
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* Rotor periodicity (moved from right panel) */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Rotor Periodicity
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0.6 }}>
            {[
              { label: 'Pole pairs',        value: polePairs.toString(),                                       sub: `${numPoles} poles / 2` },
              { label: 'Electrical period', value: `${elecPeriod_deg.toFixed(2)}°`,                            sub: `360° / ${polePairs}` },
              { label: 'Cogging period',    value: `${coggingPeriod_deg.toFixed(3)}°`,                         sub: `360° / LCM(${numSlots},${numPoles})` },
              { label: 'Cogging / elec',    value: Math.round(elecPeriod_deg / coggingPeriod_deg).toString(),  sub: 'samples' },
            ].map(item => (
              <Box key={item.label} sx={{ bgcolor: '#0a1628', borderRadius: 1,
                px: 0.8, py: 0.6, border: '1px solid #1e293b' }}>
                <Typography sx={{ fontSize: 8.5, color: '#475569',
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {item.label}
                </Typography>
                <Typography sx={{ fontSize: 12, fontWeight: 700, color: '#93c5fd',
                  fontVariantNumeric: 'tabular-nums', lineHeight: 1.1 }}>
                  {item.value}
                </Typography>
                <Typography sx={{ fontSize: 8.5, color: '#334155',
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {item.sub}
                </Typography>
              </Box>
            ))}
          </Box>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* Winding connection */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Winding Connection
          </Typography>
          <Typography sx={{ fontSize: 10, color: '#334155', mb: 1.2 }}>
            {nCoilsPerPhase} coils/phase · {nWiresPerSlot} wires/slot
          </Typography>

          {/* Connection buttons */}
          <Box sx={{ display: 'flex', gap: 0.75, mb: 1.5 }}>
            {CONNECTIONS.map(c => (
              <Tooltip key={c.key} title={c.desc} placement="top">
                <Button
                  size="small"
                  variant={connection === c.key ? 'contained' : 'outlined'}
                  onClick={() => { setConnection(c.key); applyWinding({ connection: c.key }); }}
                  disabled={isRunning}
                  sx={{ flex: 1, fontSize: 11, fontWeight: 700, py: 0.5,
                    textTransform: 'none',
                    ...(connection === c.key ? {} : { color: '#64748b', borderColor: '#334155' })
                  }}
                >
                  {c.label}
                </Button>
              </Tooltip>
            ))}
          </Box>

          {/* Derived values */}
          <Box sx={{ bgcolor: '#0a1628', borderRadius: 1, p: 1.2,
            border: '1px solid #1e293b', display: 'flex', flexDirection: 'column', gap: 0.4 }}>
            {[
              { label: 'Parallel branches',  value: `${connDef.nP}` },
              { label: 'Series coils/branch', value: `${connDef.nS}` },
              { label: 'I coil (RMS)',        value: `${I_coil_rms.toFixed(1)} A` },
              { label: 'I coil (peak) →sim',  value: `${I_coil_peak.toFixed(1)} A`, hi: true },
              { label: 'A·turns / slot',      value: `${ampTurns.toFixed(0)} At` },
            ].map(row => (
              <Box key={row.label} sx={{ display: 'flex', justifyContent: 'space-between' }}>
                <Typography sx={{ fontSize: 10, color: '#475569' }}>{row.label}</Typography>
                <Typography sx={{ fontSize: 11, fontWeight: 600,
                  color: (row as any).hi ? '#4ade80' : '#94a3b8',
                  fontVariantNumeric: 'tabular-nums' }}>
                  {row.value}
                </Typography>
              </Box>
            ))}
          </Box>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* ── Coil layout — currents (phase + sign) per slot ── */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Coil Layout — currents per slot
          </Typography>

          {/* single-layer winding (this machine has no double-layer variant) */}
          <Typography sx={{ fontSize: 10, color: '#64748b', mb: 1 }}>
            Single-layer winding · {windCfg?.num_slots ?? 24} slots
          </Typography>

          {/* phase map: one cell per slot (A=red B=green C=blue, +full −faded) */}
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: '2px', mb: 1 }}>
            {(windCfg?.layout_slots || []).map(([ph, d]: [string, number], i: number) => (
              <Tooltip key={i} title={`slot ${i}: ${ph}${d > 0 ? '+' : '−'}`}>
                <Box sx={{ width: 16, height: 18, borderRadius: '2px',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 9, fontWeight: 700, color: '#fff',
                  bgcolor: PHASE_COLOR[ph] || '#64748b',
                  opacity: d > 0 ? 1 : 0.4,
                  border: d > 0 ? '1px solid rgba(255,255,255,0.45)' : '1px solid transparent' }}>
                  {d > 0 ? ph : ph.toLowerCase()}
                </Box>
              </Tooltip>
            ))}
          </Box>

          {/* editable layout string (paste from winding tool) */}
          <TextField label="Layout string" size="small" fullWidth multiline minRows={2}
            value={layoutDraft} onChange={e => setLayoutDraft(e.target.value)}
            disabled={layoutBusy}
            inputProps={{ style: { fontSize: 11, fontFamily: 'monospace' } }}
            helperText={`${windCfg?.num_slots ?? 24} slots · A|a|c|… (UPPER=+, lower=−) · paste from winding tool`}
            FormHelperTextProps={{ sx: { fontSize: 9, color: '#475569', mx: 0 } }}/>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.75 }}>
            <Button size="small" variant="contained"
              onClick={() => applyWinding({ layout: layoutDraft })}
              disabled={layoutBusy}
              sx={{ fontSize: 10, py: 0.4, textTransform: 'none' }}>
              {layoutBusy ? 'Applying…' : 'Apply layout'}
            </Button>
            {layoutMsg && (
              <Typography sx={{ fontSize: 10,
                color: layoutMsg.startsWith('✓') ? '#4ade80' : '#fca5a5' }}>
                {layoutMsg}
              </Typography>
            )}
          </Box>

          {/* ── d-axis angle optimisation: sweep γ∈[−30,30] step 2, all values ── */}
          <Box sx={{ mt: 1.5 }}>
            <Button size="small" variant="outlined" fullWidth
              onClick={runDaxisSweep} disabled={daxisBusy}
              sx={{ fontSize: 10, py: 0.4, textTransform: 'none',
                color: '#93c5fd', borderColor: '#334155' }}>
              {daxisBusy ? 'Optimizing… (~2 min)' : 'Optimize d-axis angle (−30…30°, step 2)'}
            </Button>
            {daxisSweep && (
              <Box sx={{ mt: 1, bgcolor: '#0a1628', border: '1px solid #1e293b',
                borderRadius: 1, p: 1 }}>
                <Typography sx={{ fontSize: 11, color: '#4ade80', fontWeight: 700, mb: 0.5 }}>
                  Optimal: γ = {daxisSweep.optimal_angle}° → T = {daxisSweep.optimal_torque} N·m
                </Typography>
                <Box sx={{ maxHeight: 180, overflowY: 'auto', display: 'grid',
                  gridTemplateColumns: '1fr 1fr', columnGap: 1, rowGap: '1px' }}>
                  {(daxisSweep.points || []).map((p: any) => (
                    <Box key={p.angle} sx={{ display: 'flex', justifyContent: 'space-between',
                      px: 0.5, borderRadius: '2px',
                      bgcolor: p.angle === daxisSweep.optimal_angle ? '#14532d' : 'transparent' }}>
                      <Typography sx={{ fontSize: 10, color: '#64748b',
                        fontVariantNumeric: 'tabular-nums' }}>γ={p.angle}°</Typography>
                      <Typography sx={{ fontSize: 10,
                        color: p.angle === daxisSweep.optimal_angle ? '#4ade80' : '#cbd5e1',
                        fontVariantNumeric: 'tabular-nums' }}>
                        {p.torque == null ? '—' : p.torque.toFixed(2)}
                      </Typography>
                    </Box>
                  ))}
                </Box>
              </Box>
            )}
          </Box>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* Operating point */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1.5 }}>
            Operating Point
          </Typography>

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            <TextField label="I phase RMS (Arms)" type="number" size="small" fullWidth
              value={current} onChange={e => setCurrent(+e.target.value)}
              inputProps={{ step: 5, min: 0, max: 500 }} disabled={isRunning}
              helperText={`I coil peak = ${I_coil_peak.toFixed(1)} A → sent to solver`}
              FormHelperTextProps={{ sx: { fontSize: 10, color: '#3b82f6', mx: 0 } }}/>
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
              helperText={`electrical f = ${Number(frequency.toFixed(1))} Hz`}
              FormHelperTextProps={{ sx: { fontSize: 10, color: '#475569', mx: 0 } }}/>
            {/* Frequency is DERIVED from rpm (f = rpm × pole_pairs / 60) — read-only,
                single source is the speed above.  Editing rpm recomputes it. */}
            <TextField label="Frequency (Hz) — derived" type="number" size="small" fullWidth
              value={Number(frequency.toFixed(2))}
              disabled
              helperText={`f = rpm × ${polePairs} / 60`}
              FormHelperTextProps={{ sx: { fontSize: 10, color: '#475569', mx: 0 } }}/>
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
              helperText={`I direction = 90° + γ elec from d-axis.  ` +
                          `γ=0 → q-axis (max torque),  γ=±90 → d-axis (field weakening)`}
              disabled={isRunning}
              FormHelperTextProps={{ sx: { fontSize: 10, color: '#475569', mx: 0 } }}
            />
            {/* Mark γ as a Sweep/Optimize variable (like the chart icon on a
                geometry param).  Checked → gamma_deg joins the Sweep grid; set
                its range on the Sweep tab card. */}
            <Tooltip title="Add the load angle γ to the Sweep/Optimize grid. Range (min/max/step) is set on the Sweep tab. γ rotates the current vector only — the mesh is unchanged." placement="right">
              <FormControlLabel
                sx={{ mt: -0.5, ml: 0.25 }}
                control={
                  <Checkbox
                    size="small"
                    checked={gammaIsVar}
                    onChange={e => toggleGammaVar(e.target.checked)}
                    icon={<ShowChartIcon sx={{ fontSize: 18, color: '#475569' }} />}
                    checkedIcon={<ShowChartIcon sx={{ fontSize: 18, color: '#60a5fa' }} />}
                  />
                }
                label={
                  <Typography variant="caption" sx={{ color: gammaIsVar ? '#60a5fa' : '#94a3b8' }}>
                    Sweep γ (optimization variable)
                  </Typography>
                }
              />
            </Tooltip>

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
              helperText={`ρ_Cu(T): +0.393 %/°C from 20 °C → higher copper loss`}
              disabled={isRunning}
              FormHelperTextProps={{ sx: { fontSize: 10, color: '#475569', mx: 0 } }}
            />
            <TextField
              label="End-winding factor k_end (editable)"
              type="number" size="small" fullWidth
              value={endWinding}
              onChange={e => setEndWinding(+e.target.value)}
              inputProps={{ step: 0.05, min: 0, max: 6 }}
              helperText={`k_end = (π·tooth_w/2 + L_stack)/L_stack` +
                          ` = ${endWindingGeo ? endWindingGeo.toFixed(3) : '—'}` +
                          ` from geometry · auto-recomputed on geometry change`}
              disabled={isRunning}
              FormHelperTextProps={{ sx: { fontSize: 10, color: '#475569', mx: 0 } }}
            />

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
              operating point + mesh settings.  Pinned at the bottom of the
              left panel so the user can edit several fields, then run. ── */}
        <Box sx={{ mt: 'auto', pt: 1 }}>
          <TextField
            label="Steps per electrical period"
            type="text" size="small" fullWidth
            value={stepsStr}
            onChange={e => setStepsStr(e.target.value.replace(/[^0-9]/g, ''))}
            onBlur={commitSteps}
            onKeyDown={e => { if (e.key === 'Enter') { commitSteps(); (e.target as HTMLInputElement).blur(); } }}
            inputProps={{ inputMode: 'numeric', pattern: '[0-9]*' }}
            disabled={simBusy}
            helperText={`Transient time resolution. Max ${stepsMax} = slip-ring positions per electrical period (1008/${polePairs}); snapped to a divisor (e.g. 36, 24) so the rotor lands on whole mesh nodes.`}
            FormHelperTextProps={{ sx: { fontSize: 10, color: '#475569', mx: 0 } }}
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
                  sx={{ p: 0.5, color: '#475569', '&.Mui-checked': { color: '#c084fc' } }} />
              }
              label={
                <Typography variant="caption" sx={{ color: demag ? '#c084fc' : '#94a3b8' }}>
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
                  sx={{ p: 0.5, color: '#475569', '&.Mui-checked': { color: '#34d399' } }} />
              }
              label={
                <Typography variant="caption" sx={{ color: torqueFilter ? '#34d399' : '#94a3b8' }}>
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
              onClick={() => { if (cancelledRun) setAskResume(true); else launchRun(false); }}
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
          <Typography sx={{ fontSize: 10, color: '#475569', textAlign: 'center', mt: 0.75 }}>
            {simBusy
              ? 'Solving the transient — press Stop to cancel'
              : cancelledRun
                ? 'Stopped — Run to resume the finished frames or start fresh'
                : 'Edit γ / current / mesh settings, then launch one solve'}
          </Typography>
          <Box sx={{ mt: 1 }}>
            <SaveToMotorButton disabled={simBusy} />
          </Box>
        </Box>

        {/* ── Resume / fresh dialog (after a Stop) ── */}
        <Dialog open={askResume} onClose={() => setAskResume(false)}
          PaperProps={{ sx: { bgcolor: '#0b1220', border: '1px solid #1e293b', borderRadius: 2 } }}>
          <DialogTitle sx={{ fontSize: 15, color: '#e2e8f0' }}>Resume the stopped run?</DialogTitle>
          <DialogContent>
            <Typography sx={{ fontSize: 13, color: '#94a3b8' }}>
              The previous transient was stopped part-way.  <b>Continue</b> keeps the
              frames already solved and only computes the missing ones.
              <b> Start fresh</b> discards them and recomputes the whole period.
            </Typography>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2, gap: 1 }}>
            <Button onClick={() => launchRun(true)} sx={{ textTransform: 'none', color: '#94a3b8' }}>
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

        {/* ── Save-simulation card → snapshots the run for the Compare tab ── */}
        <Paper sx={{ bgcolor: '#0a1628', border: '1px solid #1e3a5f', p: 1.5, borderRadius: 2,
          display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
          <SaveIcon sx={{ color: '#60a5fa' }} />
          <Box sx={{ flex: 1, minWidth: 200 }}>
            <Typography sx={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0' }}>
              Save this simulation
            </Typography>
            <Typography sx={{ fontSize: 10, color: (simBusy || settingsChanged) ? '#fbbf24' : '#64748b' }}>
              {simBusy
                ? 'Simulation running — Save enables when it finishes'
                : settingsChanged
                  ? '⚠ Settings changed since the last run — press “Re-run Simulation” to update before saving'
                  : lastSummary
                    ? `T_avg = ${lastSummary.T_em_avg_Nm.toFixed(1)} N·m · ripple = ${lastSummary.T_ripple_pct.toFixed(1)} % · η = ${(lastSummary.efficiency * 100).toFixed(1)} % → snapshot for the Compare tab`
                    : 'Run a simulation first, then snapshot it for side-by-side comparison'}
            </Typography>
          </Box>
          <TextField size="small" placeholder="name (e.g. baseline 1/2)"
            value={saveName} onChange={e => setSaveName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !simBusy && !settingsChanged) saveSimulation(); }}
            disabled={simBusy || settingsChanged}
            sx={{ width: 220 }} inputProps={{ style: { fontSize: 12 } }} />
          <Button variant="contained" onClick={saveSimulation}
            disabled={!lastSummary || saveBusy || simBusy || settingsChanged} startIcon={<SaveIcon />}
            sx={{ textTransform: 'none', bgcolor: '#2563eb', '&:hover': { bgcolor: '#1d4ed8' } }}>
            {saveBusy ? 'Saving…' : simBusy ? 'Running…' : settingsChanged ? 'Re-run first' : 'Save'}
          </Button>
          {saveMsg && (
            <Typography sx={{ fontSize: 11, color: saveMsg.startsWith('✓') ? '#4ade80' : '#fca5a5' }}>
              {saveMsg}
            </Typography>
          )}
        </Paper>

        {/* Header + Physics overview card removed by user request.
            • The "2D Magnetostatics / Governing equation / Rotor
              periodicity / Domains" block is dropped entirely.
            • Rotor-periodicity info now lives in the LEFT control
              panel, in a compact 2×2 grid right under the Solver
              badge.  See <Box>{Rotor Periodicity}</Box> above. */}
        <Paper sx={{ bgcolor: '#0a1628', border: '1px solid #1e293b', p: 2,
          borderRadius: 2, display: 'none' }}>
          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1.5,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Governing Equation
          </Typography>
          <Box sx={{ fontFamily: 'monospace', fontSize: 12, color: '#94a3b8', lineHeight: 2 }}>
            <Box>∂/∂x(ν ∂A_z/∂x) + ∂/∂y(ν ∂A_z/∂y) = −J_z</Box>
            <Box sx={{ color: '#475569', fontSize: 10, mt: 0.5 }}>
              ν = reluctivity = 1/(μ₀ μᵣ) &nbsp;|&nbsp;
              B_x = ∂A_z/∂y &nbsp;|&nbsp;
              B_y = −∂A_z/∂x
            </Box>
          </Box>

          <Divider sx={{ borderColor: '#1e293b', my: 1.5 }}/>

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
              <Box key={item.label} sx={{ bgcolor: '#0f1e35', borderRadius: 1, p: 1,
                border: '1px solid #1e293b' }}>
                <Typography sx={{ fontSize: 9, color: '#475569', textTransform: 'uppercase',
                  letterSpacing: '0.08em' }}>{item.label}</Typography>
                <Typography sx={{ fontSize: 14, fontWeight: 700, color: '#93c5fd',
                  fontVariantNumeric: 'tabular-nums' }}>{item.value}</Typography>
                <Typography sx={{ fontSize: 9, color: '#334155' }}>{item.sub}</Typography>
              </Box>
            ))}
          </Box>
          <Alert severity="info" sx={{ fontSize: 10, py: 0.5, mb: 1.5,
            '& .MuiAlert-message': { py: 0 } }}>
            Full T(θ) curve needs {Math.round(elecPeriod_deg / coggingPeriod_deg)} points × one simulation each,
            or one parametric PINN with θ as input.
          </Alert>

          <Divider sx={{ borderColor: '#1e293b', my: 1.5 }}/>

          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', mb: 1,
            textTransform: 'uppercase', letterSpacing: 1 }}>
            Domains
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
            {[
              { name: 'Stator Core', color: '#3b82f6',  pde: 'μᵣ = 5000' },
              { name: 'Air Gap',     color: '#94a3b8',  pde: 'μᵣ = 1' },
              { name: 'Rotor Core',  color: '#2563eb',  pde: 'μᵣ = 5000' },
              { name: 'Magnets',     color: '#ef4444',  pde: 'Br = 1.2 T' },
              { name: 'Windings',    color: '#f59e0b',  pde: 'J = ±J_peak' },
              { name: 'Shaft',       color: '#64748b',  pde: 'μᵣ = 1000' },
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
          <Paper sx={{ bgcolor: '#0a1628', border: '1px solid #1e293b', p: 2, borderRadius: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
              <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#475569',
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
                  bgcolor: job.status === 'done' ? '#14532d' : job.status === 'error' ? '#7f1d1d' : '#1e3a5f',
                  color:   job.status === 'done' ? '#4ade80' : job.status === 'error' ? '#f87171' : '#93c5fd',
                }}
              />
              {job.elapsed_s != null && (
                <Chip label={`${job.elapsed_s.toFixed(1)}s`} size="small"
                  sx={{ fontSize: 10, bgcolor: '#1e293b', color: '#64748b' }}/>
              )}
            </Box>

            <LinearProgress
              variant="determinate"
              value={job.progress * 100}
              sx={{ borderRadius: 1, height: 4, bgcolor: '#1e293b',
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
          <Paper sx={{ bgcolor: '#0a1628', border: '1px solid #1e293b', p: 2, borderRadius: 2 }}>
            {job.result.status === 'dry_run' && (
              <Alert severity="info" sx={{ fontSize: 10, mb: 1.5, py: 0.5,
                '& .MuiAlert-message': { py: 0 } }}>
                Dry-run: copper losses computed. Install NVIDIA Modulus for torque, iron &amp; magnet losses, η.
              </Alert>
            )}

            {/* ── Efficiency banner ── */}
            {job.result.efficiency_pct != null && (
              <Box sx={{ textAlign: 'center', py: 1.5, mb: 1.5,
                bgcolor: '#0a2010', borderRadius: 1, border: '1px solid #14532d' }}>
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
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#475569',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Winding (computed)
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 1.5 }}>
              <Row label="R phase"    value={job.result.R_phase_ohm != null ? (job.result.R_phase_ohm * 1000).toFixed(2) : '—'} unit="mΩ"/>
              <Row label="L turn"     value={job.result.L_turn_mm != null ? job.result.L_turn_mm.toFixed(1) : '—'} unit="mm"/>
              <Row label="I coil rms" value={job.result.I_coil_rms_A != null ? job.result.I_coil_rms_A.toFixed(1) : '—'} unit="A"/>
            </Box>

            {/* ── Field ── */}
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#475569',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
              Magnetic Field
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
              <Row label="B max"  value={job.result.B_max_T.toFixed(4)}  unit="T"/>
              <Row label="B mean" value={job.result.B_mean_T.toFixed(4)} unit="T"/>
              <Row label="Steps"  value={job.result.training_steps.toString()}/>
            </Box>

            <Divider sx={{ borderColor: '#1e293b', my: 1.5 }}/>

            <Typography sx={{ fontSize: 10, color: '#334155' }}>
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

        {/* ── Model comparison (diagnostics) — runs the three torque models
            across a γ sweep so the inter-model discrepancy is visible. ── */}
        <ModelCompare I_phase_rms={current} />

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
          onSummary={setLastSummary}
          fieldLosses={fieldLosses}
          demag={demag}
          torqueFilter={torqueFilter}
        />

      </Box>
    </Box>
  );
};

export default SimulationPanel;
