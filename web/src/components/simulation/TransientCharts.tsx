/**
 * TransientCharts — torque, losses and phase-voltage waveforms over time.
 *
 * Runs a series of FEM solves at N steps per electrical period
 * (default 60), the same mesh and solver settings as the rest of the
 * Simulation tab.  Plots T(t), P_cu/P_fe/P_total(t) and V_A/B/C(t).
 */
import React, { useEffect, useState } from 'react';
import {
  Box, Paper, Typography, CircularProgress, Button, Slider, Tooltip,
} from '@mui/material';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip as RcTooltip, Legend,
} from 'recharts';
import RefreshIcon from '@mui/icons-material/Refresh';

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

const TransientCharts: React.FC<Props> = ({ gamma_deg = 0, I_phase_rms = 85, onSummary }) => {
  const [steps, setSteps] = useState<number>(30);          // faster default
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

  const run = () => {
    setBusy(true); setError(null);
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
    }).toString();
    fetch(`${API}/api/simulation/physics/fem_transient?${qs}`)
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
        return r.json();
      })
      .then((d: TransientPayload) => {
        setData(d); setBusy(false);
        if (d.summary && onSummary) onSummary(d.summary);
      })
      .catch(e => { setError(String(e)); setBusy(false); });
  };

  // Auto-run on mount + when operating-point inputs change.  30 steps
  // ≈ 12 seconds at the default mesh density.
  useEffect(() => { run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gamma_deg, I_phase_rms]);

  // Build chart-friendly row arrays
  const rows = React.useMemo(() => {
    if (!data) return [];
    const ms = data.time_s.map(t => t * 1e3);
    return ms.map((t, i) => ({
      t_ms:  t,
      T_em:  data.T_em_Nm[i],
      P_cu:  data.P_cu_W[i],
      P_fe:  data.P_fe_W[i],
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
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5,
          minWidth: 320 }}>
          <Box sx={{ flex: 1 }}>
            <Typography sx={{ fontSize: 10, color: '#94a3b8' }}>
              Steps per electrical period: <b>{steps}</b>
            </Typography>
            <Slider value={steps} min={12} max={180} step={6}
              onChange={(_, v) => setSteps(v as number)}
              sx={{ color: '#3b82f6' }}/>
          </Box>
          <Button size="small" variant="contained"
            startIcon={busy ? <CircularProgress size={14} sx={{ color: '#fff' }}/>
                            : <RefreshIcon fontSize="small"/>}
            disabled={busy} onClick={run}
            sx={{ bgcolor: '#1e3a5f', '&:hover': { bgcolor: '#1e40af' },
              textTransform: 'none', minWidth: 170 }}>
            {busy && progress && progress.total > 0
              ? `Frame ${progress.step}/${progress.total}`
              : (busy ? 'Running…' : (data ? 'Re-run' : 'Run'))}
          </Button>
        </Box>
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
          Press <b>Run</b> to launch a transient FEM sweep over one electrical period.<br/>
          Estimated time: {(steps * 0.4).toFixed(0)} seconds at the current mesh density.
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
                  strokeWidth={2} dot={false} isAnimationActive={false}/>
              </LineChart>
            </ResponsiveContainer>
          </Box>

          {/* ── Losses ── */}
          <Box sx={{ height: 220 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#94a3b8' }}>
              Losses (Cu / Fe / total)
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
                  name="P_Cu" strokeWidth={2} dot={false}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_fe" stroke="#f87171"
                  name="P_Fe" strokeWidth={2} dot={false}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="P_tot" stroke="#cbd5e1"
                  name="P_total" strokeWidth={2} dot={false}
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
                  name="I_A" strokeWidth={2} dot={false}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="I_B" stroke="#10b981"
                  name="I_B" strokeWidth={2} dot={false}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="I_C" stroke="#60a5fa"
                  name="I_C" strokeWidth={2} dot={false}
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
                  name="V_A" strokeWidth={2} dot={false}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="V_B" stroke="#10b981"
                  name="V_B" strokeWidth={2} dot={false}
                  isAnimationActive={false}/>
                <Line type="monotone" dataKey="V_C" stroke="#60a5fa"
                  name="V_C" strokeWidth={2} dot={false}
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
