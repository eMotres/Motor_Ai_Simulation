/**
 * TransientCharts — torque, losses and phase-voltage waveforms over time.
 *
 * Runs a series of FEM solves at N steps per electrical period
 * (default 60), the same mesh and solver settings as the rest of the
 * Simulation tab.  Plots T(t), P_cu/P_fe/P_total(t) and V_A/B/C(t).
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  Box, Paper, Typography, Tooltip,
} from '@mui/material';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip as RcTooltip, Legend,
} from 'recharts';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

import type { TransientSummary } from './SummaryTable';

interface TransientPayload {
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
  T_avg_Nm: number;
  T_ripple_pct: number;
  P_cu_W: number[];
  P_fe_W: number[];
  P_mag_eddy_W: number[];
  P_loss_total_W: number[];
  P_mech_avg_W: number;
  I_A: number[]; I_B: number[]; I_C: number[];
  V_A: number[]; V_B: number[]; V_C: number[];
  V_peak: number;
  summary?: TransientSummary;
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
}

function readMeshSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`mesh.${key}`);
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

const TransientCharts: React.FC<Props> = ({ gamma_deg = 0, I_phase_rms = 85, onSummary, runNonce = 0, onBusyChange, steps = 12, fresh = false }) => {
  // `steps` (n_steps_per_period) is controlled from the left panel and
  // matches the animation viewer's n_frames so both hit the same backend
  // cache key (one solve, not two).
  const [data,  setData]  = useState<TransientPayload | null>(null);
  const [busy,  setBusy]  = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressInfo | null>(null);

  // Poll the backend /progress endpoint every 500 ms while a run is in
  // flight, so we can show "Frame X/N — Ys elapsed — ETA Zs" instead of
  // an opaque spinning "Running…".
  useEffect(() => {
    if (!busy) { setProgress(null); return; }
    let alive = true;
    const tick = async () => {
      if (!alive) return;
      try {
        const r = await fetch(`${API}/api/simulation/physics/fem_transient/progress`);
        if (r.ok) {
          const p: ProgressInfo = await r.json();
          if (alive) setProgress(p);
        }
      } catch {/* ignore polling errors */}
    };
    tick();   // immediate first read
    const id = window.setInterval(tick, 500);
    return () => { alive = false; window.clearInterval(id); };
  }, [busy]);

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

  const run = () => {
    setBusy(true); setError(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const qs = new URLSearchParams({
      n_steps_per_period: String(steps),
      n_periods:          '1',
      gamma_deg:          String(gamma_deg),
      I_phase_rms:        String(I_phase_rms),
      mesh_size_mm:       String(readMeshSetting('meshSize',    4.0)),
      min_size_mm:        String(readMeshSetting('minSize',     0.3)),
      outer_air_factor:   String(readMeshSetting('outerAir',    1.3)),
      motion_band:        String(readMeshSetting('motionBand',  true)),
      band_thickness_mm:  String(readMeshSetting('bandThickness', 0.4)),
      n_sectors:          String(readMeshSetting('nSectors',    4)),
      stator_fillet_mm:   String(readMeshSetting('statorFillet', 0.0)),
      // Request the SAME include_frames/n_frames as the FemAnimationViewer
      // so both panels hit the exact same backend cache key and the heavy
      // FEM sweep runs ONCE instead of twice (the lock would otherwise
      // serialise two separate computes → 2× wall-clock).  The extra
      // frames payload is ignored here.
      include_frames:     'true',
      n_frames:           String(steps),
      run_id:             String(runNonce),
      fresh:              String(fresh),
    }).toString();
    // Helper: fetch with auto-retry against transient connection drops.
    // The uvicorn supervisor sometimes respawns the worker mid-request when
    // a heavy FEM solve crashes the LLVM JIT; without a retry the user sees
    // a permanent "Failed to fetch" until they click Re-run manually.
    const attempt = async (i = 0): Promise<void> => {
      try {
        const r = await fetch(`${API}/api/simulation/physics/fem_transient?${qs}`,
          { signal: ctrl.signal });
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
        const d: TransientPayload = await r.json();
        setData(d); setBusy(false);
        setError(null);
        if (d.summary && onSummary) onSummary(d.summary);
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

  // Run ONLY when the user presses "Run Simulation" (runNonce ticks).
  // The fetch reads the CURRENT gamma / I / mesh-settings at click time,
  // so several edits batch into one solve.  runNonce starts at 0 → no
  // auto-run on mount.
  useEffect(() => {
    if (runNonce > 0) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runNonce]);

  // Report busy state up to the Run button.
  useEffect(() => { onBusyChange?.(busy);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);

  // Build chart-friendly row arrays
  const rows = React.useMemo(() => {
    if (!data) return [];
    const ms = data.time_s.map(t => t * 1e3);
    return ms.map((t, i) => ({
      t_ms:  t,
      T_em:  data.T_em_Nm[i],
      P_cu:  data.P_cu_W[i],
      P_fe:  data.P_fe_W[i],
      P_mag: data.P_mag_eddy_W[i],
      P_tot: data.P_loss_total_W[i],
      I_A:   data.I_A[i], I_B: data.I_B[i], I_C: data.I_C[i],
      V_A:   data.V_A[i], V_B: data.V_B[i], V_C: data.V_C[i],
    }));
  }, [data]);

  return (
    <Paper sx={{ bgcolor: '#0b1220', border: '1px solid #1e293b', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1.5 }}>
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
          {data && (
            <Typography sx={{ fontSize: 10, color: '#475569' }}>
              {data.n_steps_per_period} steps/period · dt = {(data.dt_s*1e6).toFixed(1)} µs ·
              T_period = {(data.T_period_s*1e3).toFixed(2)} ms ({data.f_elec_Hz.toFixed(1)} Hz electrical) ·
              T_avg = {data.T_avg_Nm.toFixed(2)} N·m · ripple = {data.T_ripple_pct.toFixed(1)} %
            </Typography>
          )}
        </Box>
        {/* Steps/period + Run moved to the left panel's "Run Simulation".
            Just show the live frame counter here while solving. */}
        {busy && progress && progress.total > 0 && (
          <Typography sx={{ fontSize: 11, color: '#60a5fa', fontWeight: 600,
            whiteSpace: 'nowrap' }}>
            Frame {progress.step}/{progress.total}
          </Typography>
        )}
      </Box>

      {/* Live progress strip — only visible while a transient is running */}
      {busy && progress && progress.total > 0 && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.4,
          px: 1, py: 0.6, bgcolor: '#060d17', border: '1px solid #1e293b',
          borderRadius: 1, fontFamily: 'monospace' }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between',
            fontSize: 10, color: '#94a3b8' }}>
            <span>Solving frame <b>{progress.step}</b> of <b>{progress.total}</b>
              {progress.per_step_s ? `   ·   ${progress.per_step_s.toFixed(1)} s/frame` : ''}</span>
            <span>elapsed <b>{progress.elapsed_s.toFixed(1)} s</b>   ·   ETA <b>{progress.eta_s.toFixed(1)} s</b></span>
          </Box>
          <Box sx={{ width: '100%', height: 4, bgcolor: '#0f172a',
            borderRadius: 2, overflow: 'hidden' }}>
            <Box sx={{
              width: `${(100 * progress.step / Math.max(1, progress.total)).toFixed(1)}%`,
              height: '100%', bgcolor: '#3b82f6',
              transition: 'width 0.4s ease' }}/>
          </Box>
        </Box>
      )}

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
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Losses ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Losses (Cu / Fe / Mag / total)
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
                  name="P_Cu" strokeWidth={2}
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_fe" stroke="#f87171"
                  name="P_Fe" strokeWidth={2}
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_mag" stroke="#a78bfa"
                  name="P_Mag" strokeWidth={2}
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_tot" stroke="#cbd5e1"
                  name="P_total" strokeWidth={2}
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Currents ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Phase currents I_A / I_B / I_C
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
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="I_B" stroke="#10b981"
                  name="I_B" strokeWidth={2}
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="I_C" stroke="#60a5fa"
                  name="I_C" strokeWidth={2}
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Voltages ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Phase voltages V_A / V_B / V_C  (V_peak ≈ {data.V_peak.toFixed(1)} V)
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
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="V_B" stroke="#10b981"
                  name="V_B" strokeWidth={2}
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="V_C" stroke="#60a5fa"
                  name="V_C" strokeWidth={2}
                  dot={{ r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>
        </>
      )}
    </Paper>
  );
};

export default TransientCharts;
