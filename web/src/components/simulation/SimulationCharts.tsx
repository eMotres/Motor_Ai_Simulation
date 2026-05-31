/**
 * Motor waveform charts over one electrical period.
 *
 * Always available (analytical):
 *   – Phase currents  i_A, i_B, i_C  vs θ_mech
 *   – Phase voltages  V_A, V_B, V_C  (resistive + estimated back-EMF)
 *   – Copper losses   P_cu(θ)  — constant for balanced 3-phase
 *
 * Requires NVIDIA Modulus (PINN field):
 *   – Torque          T(θ)
 *   – Iron losses     P_fe(θ)
 *   – Magnet losses   P_mag(θ)
 */
import React, { useMemo } from 'react';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip as RcTooltip, Legend, ReferenceLine,
} from 'recharts';
import { Box, Paper, Typography, Chip } from '@mui/material';
import TorqueWaveformChart from './TorqueWaveformChart';

// ── types ─────────────────────────────────────────────────────────────────────
interface ChartProps {
  /** Mechanical degrees per electrical period (e.g. 25.71°) */
  elecPeriod_deg:    number;
  /** Cogging period [deg mechanical] */
  coggingPeriod_deg: number;
  /** Number of pole pairs */
  polePairs:         number;
  /** Peak coil current [A] (per slot) */
  I_coil_peak:       number;
  /** Parallel branches (n_parallel) — used to compute I_phase_peak */
  nParallel:         number;
  /** Phase offset γ [deg] */
  phaseOffset_deg:   number;
  /** Phase resistance [Ω] */
  R_phase:           number;
  /** Rated speed [rpm] */
  rpm:               number;
  /** Electrical frequency [Hz] */
  frequency_hz:      number;
  /** Phase inductance estimate [H] — optional, used for V estimate */
  L_phase_est_H?:    number;
  /** Torque data from PINN — array of {theta: deg, T: Nm} */
  torqueData?:       { theta: number; T: number }[];
}

// ── helpers ───────────────────────────────────────────────────────────────────
const CHART_STYLE = {
  bgcolor: '#060d17',
  border: '1px solid #1e293b',
  borderRadius: 2,
  p: 2,
};

const AXIS_STYLE = { stroke: '#334155', fontSize: 10 };
const GRID_STYLE = { stroke: '#1e293b', strokeDasharray: '3 3' };

function sectionLabel(title: string, note?: string) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
      <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6',
        textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        {title}
      </Typography>
      {note && (
        <Chip label={note} size="small"
          sx={{ fontSize: 9, height: 16, bgcolor: '#1e293b', color: '#64748b' }}/>
      )}
    </Box>
  );
}

// ── main component ─────────────────────────────────────────────────────────────
const SimulationCharts: React.FC<ChartProps> = ({
  elecPeriod_deg,
  coggingPeriod_deg,
  polePairs,
  I_coil_peak,
  nParallel,
  phaseOffset_deg,
  R_phase,
  rpm,
  frequency_hz,
  L_phase_est_H = 0.0002,
  torqueData,
}) => {
  const N_POINTS = 72;
  // Phase peak current = coil peak × parallel branches
  const I_phase_peak = I_coil_peak * nParallel;

  // ── compute waveform data ──────────────────────────────────────────────────
  const data = useMemo(() => {
    const pts = [];
    for (let i = 0; i <= N_POINTS; i++) {
      // θ_mech in [0, elecPeriod_deg]
      const theta_mech = (i / N_POINTS) * elecPeriod_deg;
      // Electrical angle — q-axis convention (γ=0 ⇒ pure q-axis), +90° shift
      const theta_elec_deg = theta_mech * polePairs + phaseOffset_deg + 90;
      const theta_elec = theta_elec_deg * Math.PI / 180;

      // ── Phase currents [A] (at motor terminals = I_coil × n_parallel) ──
      const iA = I_phase_peak * Math.cos(theta_elec);
      const iB = I_phase_peak * Math.cos(theta_elec - 2 * Math.PI / 3);
      const iC = I_phase_peak * Math.cos(theta_elec + 2 * Math.PI / 3);

      // ── Estimated voltages [V] ────────────────────────────────────────
      // v_phase = R·i + L·di/dt + e_back
      //   i        = I·cos(θ_elec)    →   di/dt = -ω_elec·I·sin(θ_elec)
      //   e_back   = E_peak·sin(θ_elec_rotor)   (PMSM back-EMF leads PM axis 90°)
      // The rotor PM axis is θ_rotor_elec = p·θ_mech (gamma=0). For γ≠0 the
      // current is shifted by γ from the rotor d-axis, so back-EMF uses
      // θ_rotor_elec = θ_elec − γ_rad.
      const omega_elec    = 2 * Math.PI * frequency_hz;
      const X_L           = omega_elec * L_phase_est_H;
      const gamma_rad_chart = phaseOffset_deg * Math.PI / 180;
      const theta_rot_elec_A = theta_elec - gamma_rad_chart;
      // E_peak ≈ Ke·ω_elec; Ke ≈ Ψ_pm·p ≈ R·I_peak·6 as a coarse default
      // (true Ke is computed in backend physics endpoint).
      const E_back_peak   = R_phase * I_phase_peak * 6;
      const vA = R_phase * iA - X_L * I_phase_peak * Math.sin(theta_elec)
                 + E_back_peak * Math.sin(theta_rot_elec_A);
      const vB = R_phase * iB - X_L * I_phase_peak * Math.sin(theta_elec - 2 * Math.PI / 3)
                 + E_back_peak * Math.sin(theta_rot_elec_A - 2 * Math.PI / 3);
      const vC = R_phase * iC - X_L * I_phase_peak * Math.sin(theta_elec + 2 * Math.PI / 3)
                 + E_back_peak * Math.sin(theta_rot_elec_A + 2 * Math.PI / 3);

      // ── Copper losses [W] ─────────────────────────────────────────────
      // P_cu = R_phase × (iA² + iB² + iC²) — balanced 3-phase = constant
      // = (3/2) × R_phase × I_phase_peak² = 3 × R_phase × I_phase_rms²
      const Pcu = R_phase * (iA * iA + iB * iB + iC * iC);

      // ── Time [ms] for this angle (at current rpm) ─────────────────────
      const t_ms = (theta_mech / 360) * (60 / rpm) * 1000;

      pts.push({
        theta: parseFloat(theta_mech.toFixed(3)),
        t_ms:  parseFloat(t_ms.toFixed(4)),
        iA: parseFloat(iA.toFixed(2)),
        iB: parseFloat(iB.toFixed(2)),
        iC: parseFloat(iC.toFixed(2)),
        vA: parseFloat(vA.toFixed(2)),
        vB: parseFloat(vB.toFixed(2)),
        vC: parseFloat(vC.toFixed(2)),
        Pcu: parseFloat(Pcu.toFixed(1)),
      });
    }
    return pts;
  }, [elecPeriod_deg, polePairs, phaseOffset_deg, I_coil_peak,
      nParallel, R_phase, frequency_hz, L_phase_est_H, rpm]);

  // Cogging reference lines (vertical)
  const coggingLines = useMemo(() => {
    const lines = [];
    for (let d = coggingPeriod_deg; d < elecPeriod_deg - 0.01; d += coggingPeriod_deg) {
      lines.push(parseFloat(d.toFixed(3)));
    }
    return lines;
  }, [coggingPeriod_deg, elecPeriod_deg]);

  const n_cogging = Math.round(elecPeriod_deg / coggingPeriod_deg);
  const T_period_ms = parseFloat(((elecPeriod_deg / 360) * (60 / rpm) * 1000).toFixed(4));

  const tooltipStyle = {
    contentStyle: { backgroundColor: '#1e293b', border: '1px solid #334155',
      borderRadius: 6, fontSize: 11 },
    labelStyle: { color: '#94a3b8' },
  };

  // X-axis tick formatter
  const xFmt = (v: number) => `${v.toFixed(1)}°`;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>

      {/* ── Header ── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <Typography variant="h6" sx={{ color: '#e2e8f0', fontWeight: 700 }}>
          Waveforms — One Electrical Period
        </Typography>
        <Chip label={`${elecPeriod_deg.toFixed(2)}° mech`} size="small"
          sx={{ fontSize: 10, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
        <Chip label={`${T_period_ms.toFixed(3)} ms`} size="small"
          sx={{ fontSize: 10, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
        <Chip label={`${n_cogging} cogging sub-periods`} size="small"
          sx={{ fontSize: 10, bgcolor: '#2d1e0f', color: '#fb923c' }}/>
      </Box>
      <Typography sx={{ fontSize: 11, color: '#475569', mt: -1 }}>
        Vertical dashed lines = cogging boundaries ({coggingPeriod_deg.toFixed(3)}° each).
        Torque T(θ) and iron losses require NVIDIA Modulus.
      </Typography>

      {/* ── Chart 1: Phase Currents ── */}
      <Paper sx={CHART_STYLE}>
        {sectionLabel('Phase Currents', 'analytical')}
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={data} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <CartesianGrid {...GRID_STYLE} />
            <XAxis dataKey="theta" tickFormatter={xFmt} tick={AXIS_STYLE}
              label={{ value: 'θ_mech [°]', position: 'insideBottomRight',
                       offset: -5, style: { fontSize: 10, fill: '#475569' } }}/>
            <YAxis tick={AXIS_STYLE}
              label={{ value: 'i [A]', angle: -90, position: 'insideLeft',
                       style: { fontSize: 10, fill: '#475569' } }}/>
            <RcTooltip {...tooltipStyle}
              formatter={(v: number, name: string) => [`${v.toFixed(1)} A`, name]}
              labelFormatter={(l: number) => `θ = ${l.toFixed(2)}°`}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {coggingLines.map(d => (
              <ReferenceLine key={d} x={d} stroke="#f97316" strokeDasharray="2 4"
                strokeWidth={0.8} strokeOpacity={0.6}/>
            ))}
            <Line dataKey="iA" name="i_A" stroke="#f87171" dot={false} strokeWidth={2}/>
            <Line dataKey="iB" name="i_B" stroke="#4ade80" dot={false} strokeWidth={2}/>
            <Line dataKey="iC" name="i_C" stroke="#60a5fa" dot={false} strokeWidth={2}/>
          </LineChart>
        </ResponsiveContainer>
      </Paper>

      {/* ── Chart 2: Phase Voltages ── */}
      <Paper sx={CHART_STYLE}>
        {sectionLabel('Phase Voltages', `R·i + X_L·i  (L_est = ${(L_phase_est_H*1000).toFixed(2)} mH)`)}
        <Typography sx={{ fontSize: 10, color: '#475569', mb: 0.75 }}>
          ⚠ Approximate — back-EMF from flux linkage requires PINN
        </Typography>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={data} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <CartesianGrid {...GRID_STYLE} />
            <XAxis dataKey="theta" tickFormatter={xFmt} tick={AXIS_STYLE}/>
            <YAxis tick={AXIS_STYLE}
              label={{ value: 'V [V]', angle: -90, position: 'insideLeft',
                       style: { fontSize: 10, fill: '#475569' } }}/>
            <RcTooltip {...tooltipStyle}
              formatter={(v: number, name: string) => [`${v.toFixed(1)} V`, name]}
              labelFormatter={(l: number) => `θ = ${l.toFixed(2)}°`}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {coggingLines.map(d => (
              <ReferenceLine key={d} x={d} stroke="#f97316" strokeDasharray="2 4"
                strokeWidth={0.8} strokeOpacity={0.6}/>
            ))}
            <Line dataKey="vA" name="V_A" stroke="#f87171" dot={false} strokeWidth={2}/>
            <Line dataKey="vB" name="V_B" stroke="#4ade80" dot={false} strokeWidth={2}/>
            <Line dataKey="vC" name="V_C" stroke="#60a5fa" dot={false} strokeWidth={2}/>
          </LineChart>
        </ResponsiveContainer>
      </Paper>

      {/* ── Chart 3: Losses ── */}
      <Paper sx={CHART_STYLE}>
        {sectionLabel('Losses', 'Cu analytical · Fe/Mag need Modulus')}
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={data} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <CartesianGrid {...GRID_STYLE} />
            <XAxis dataKey="theta" tickFormatter={xFmt} tick={AXIS_STYLE}/>
            <YAxis tick={AXIS_STYLE}
              label={{ value: 'P [W]', angle: -90, position: 'insideLeft',
                       style: { fontSize: 10, fill: '#475569' } }}/>
            <RcTooltip {...tooltipStyle}
              formatter={(v: number, name: string) => [`${v.toFixed(1)} W`, name]}
              labelFormatter={(l: number) => `θ = ${l.toFixed(2)}°`}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {coggingLines.map(d => (
              <ReferenceLine key={d} x={d} stroke="#f97316" strokeDasharray="2 4"
                strokeWidth={0.8} strokeOpacity={0.6}/>
            ))}
            <Line dataKey="Pcu" name="P_cu (Cu)" stroke="#fbbf24" dot={false} strokeWidth={2}/>
          </LineChart>
        </ResponsiveContainer>
        <Typography sx={{ fontSize: 10, color: '#334155', mt: 0.5 }}>
          P_cu is constant for balanced 3-phase: P = (3/2) × R_phase × I_peak²
          = {((1.5 * R_phase * I_phase_peak ** 2)).toFixed(1)} W
        </Typography>
      </Paper>

      {/* ── Chart 4: Torque — Maxwell stress tensor ── */}
      <Paper sx={CHART_STYLE}>
        <TorqueWaveformChart
          gamma_deg={phaseOffset_deg}
          n_points={60}
          pinnTorqueData={torqueData ?? null}
        />
      </Paper>

    </Box>
  );
};

export default SimulationCharts;
