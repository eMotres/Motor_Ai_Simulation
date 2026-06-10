/**
 * PhysicsDashboard — comprehensive analytical physics display.
 *
 * Charts (all analytical, no PINN required):
 *   1. Spatial MMF distribution around airgap (360°)
 *   2. Air-gap radial B — winding + PM + total
 *   3. Harmonic spectrum of B (bar chart, orders 1–28)
 *   4. Slot current J_z map (colour-coded by phase)
 *   5. Loss breakdown (stacked bar: Cu_DC / Cu_prox / Fe_stat / Fe_rot / Mag_eddy)
 *   6. Key scalars table (skin depth, B peaks, R_phase, …)
 *
 * Designed for Ansys comparison: export each dataset to CSV.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ResponsiveContainer, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip as RcTooltip,
  Legend, ReferenceLine, Cell,
} from 'recharts';
import {
  Box, Paper, Typography, Chip, Divider, CircularProgress,
  Button, IconButton, Tooltip,
} from '@mui/material';
import DownloadIcon    from '@mui/icons-material/Download';
import RefreshIcon     from '@mui/icons-material/Refresh';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import LossWaveformChart from './LossWaveformChart';
import MotorField2D      from './MotorField2D';
import FemFieldChart       from './FemFieldChart';
import FemAnimationViewer  from './FemAnimationViewer';
import TransientCharts     from './TransientCharts';
import SummaryTable        from './SummaryTable';
import type { TransientSummary } from './SummaryTable';
import type { FemPayload } from './fem-types';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// ── palette ───────────────────────────────────────────────────────────────────
const C = {
  A: '#f87171', B: '#4ade80', C: '#60a5fa',
  bg: '#060d17', paper: '#0a1628', border: '#1e293b',
  grid: '#1e293b', axis: '#475569',
  MMF: '#a78bfa', B_total: '#fbbf24', B_winding: '#38bdf8', B_pm: '#f97316',
  Cu: '#fbbf24', Fe_s: '#f87171', Fe_r: '#fb923c', Mag: '#a78bfa',
};

// ── types ─────────────────────────────────────────────────────────────────────
interface PhysicsData {
  winding_layout: { slot: number; phase: string; direction: number }[];
  slot_currents:  {
    slot: number; phase: string; direction: number;
    phi_center_deg: number; I_slot_A: number; J_z_MA_m2: number;
  }[];
  theta_deg: number[];
  MMF:       number[];
  B_winding: number[];
  B_pm:      number[];
  B_total:   number[];
  harmonics: { order: number; amplitude: number; phase_deg: number }[];
  losses: {
    P_cu_dc_W: number; P_cu_prox_W: number; P_cu_total_W: number;
    P_fe_stator_W: number; P_fe_rotor_W: number;
    P_mag_eddy_W: number; P_shaft_W: number; P_total_W: number;
    B_tooth_T?: number; B_harm_in_mag_T?: number; f_slot_Hz?: number;
    model_note?: string;
  };
  torque: {
    T_em_Nm: number; T_max_Nm: number;
    P_mech_W: number; P_mech_max_W: number;
    I_d_A: number; I_q_A: number;
    psi_pm_Wb: number; Kt_Nm_A: number; Ke_Vs_rad: number;
    gamma_deg: number; note: string; k_w: number;
  };
  masses: {
    components: { name: string; material: string; density_kg_m3: number;
                  volume_cm3: number; mass_kg: number; note?: string }[];
    total_active_kg: number;
    note: string;
    estimated_total_with_frame_kg: number;
  };
  scalars: Record<string, number>;
}

interface Props {
  rotorAngle_deg: number;
  gamma_deg:      number;
  I_phase_rms?:   number;
  pinnLosses?: {
    P_cu_total_W?: number | null;
    P_fe_stator_W?: number | null;
    P_fe_rotor_W?: number | null;
    P_mag_eddy_W?: number | null;
    torque_Nm?: number;
    B_max_T?: number;
  } | null;
  // Ticks on each "Run Simulation" press; forwarded to the FEM panels
  // which only (re)solve on that signal.
  runNonce?:      number;
  onBusyChange?:  (busy: boolean) => void;
  // Steps per electrical period (set in the left panel).  Drives both the
  // transient n_steps_per_period and the animation n_frames so they share
  // one backend cache key.
  steps?:         number;
  // When true the next run tells the backend to discard cached frames and
  // recompute the whole period ("Start fresh"); false resumes them.
  fresh?:         boolean;
  // Forwards the latest transient-run summary up to the parent (SimulationPanel)
  // so it can be snapshotted by the "Save simulation" card.
  onSummary?:     (s: TransientSummary | null) => void;
}

// ── helpers ───────────────────────────────────────────────────────────────────
const CHART_STYLE = {
  bgcolor: C.bg, border: `1px solid ${C.border}`, borderRadius: 2, p: 2,
};
const AXIS_STYLE   = { stroke: C.axis, fontSize: 10 };
const GRID_STYLE   = { stroke: C.grid, strokeDasharray: '3 3' };
const TOOLTIP_STYLE = {
  contentStyle: { backgroundColor: '#1e293b', border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 11 },
  labelStyle:   { color: '#94a3b8' },
};

function SectionLabel({ title, note, tooltip }: { title: string; note?: string; tooltip?: string }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
      <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6',
        textTransform: 'uppercase', letterSpacing: '0.08em' }}>{title}</Typography>
      {note && <Chip label={note} size="small"
        sx={{ fontSize: 9, height: 16, bgcolor: '#1e293b', color: '#64748b' }}/>}
      {tooltip && (
        <Tooltip title={tooltip} placement="right">
          <InfoOutlinedIcon sx={{ fontSize: 13, color: '#334155', cursor: 'help' }}/>
        </Tooltip>
      )}
    </Box>
  );
}

function exportCSV(filename: string, rows: Record<string, number | string>[]) {
  if (!rows.length) return;
  const header = Object.keys(rows[0]).join(',');
  const body   = rows.map(r => Object.values(r).join(',')).join('\n');
  const blob   = new Blob([header + '\n' + body], { type: 'text/csv' });
  const url    = URL.createObjectURL(blob);
  const a      = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// ── main component ────────────────────────────────────────────────────────────
const PhysicsDashboard: React.FC<Props> = ({ rotorAngle_deg, gamma_deg, I_phase_rms, pinnLosses, runNonce = 0, onBusyChange, steps = 12, fresh = false, onSummary }) => {
  // Latest FEM solve payload — kept around so future siblings can reuse it.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [_femPayload, setFemPayload] = React.useState<FemPayload | null>(null);
  // Most recent transient-run summary — drives the top-of-tab overview card.
  const [transientSummary, setTransientSummary] =
    React.useState<TransientSummary | null>(null);
  // Forward the latest summary to the parent (for the Save-simulation snapshot).
  React.useEffect(() => { onSummary?.(transientSummary); }, [transientSummary]);  // eslint-disable-line react-hooks/exhaustive-deps
  const [data, setData]       = useState<PhysicsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const fetchData = useCallback(() => {
    setLoading(true); setError(null);
    fetch(`${API}/api/simulation/physics?rotor_angle_deg=${rotorAngle_deg}&gamma_deg=${gamma_deg}&n_points=360`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, [rotorAngle_deg, gamma_deg]);

  // No auto-run: the analytical torque/field overview also waits for the
  // user to press "Run Simulation" (runNonce ticks).  Editing γ / current
  // never kicks off any compute on its own.
  useEffect(() => {
    if (runNonce > 0) fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runNonce]);

  // ── merged spatial data for charts ───────────────────────────────────────
  const spatialData = useMemo(() => {
    if (!data) return [];
    return data.theta_deg.map((th, i) => ({
      theta: parseFloat(th.toFixed(1)),
      MMF:       parseFloat(data.MMF[i].toFixed(1)),
      B_total:   parseFloat(data.B_total[i].toFixed(4)),
      B_winding: parseFloat(data.B_winding[i].toFixed(4)),
      B_pm:      parseFloat(data.B_pm[i].toFixed(4)),
    }));
  }, [data]);

  // ── harmonic data (orders 1–28 only, relevant) ───────────────────────────
  const harmonicData = useMemo(() => {
    if (!data) return [];
    return data.harmonics
      .filter(h => h.order >= 1 && h.order <= 30)
      .map(h => ({ order: h.order, amp: parseFloat(h.amplitude.toFixed(4)) }));
  }, [data]);

  // ── slot map data for polar display ──────────────────────────────────────
  const slotBarData = useMemo(() => {
    if (!data) return [];
    return data.slot_currents.map(s => ({
      slot:   s.slot,
      phi:    parseFloat(s.phi_center_deg.toFixed(1)),
      J:      parseFloat((s.J_z_MA_m2 / 1e6).toFixed(3)),   // MA/m2
      phase:  s.phase,
      dir:    s.direction,
    }));
  }, [data]);

  // ── loss comparison data ─────────────────────────────────────────────────
  const lossData = useMemo(() => {
    if (!data) return [];
    const est = data.losses;
    const rows = [
      { name: 'Cu DC',    analytical: est.P_cu_dc_W,    pinn: pinnLosses?.P_cu_total_W ?? null },
      { name: 'Cu prox.', analytical: est.P_cu_prox_W,  pinn: null },
      { name: 'Fe stator',analytical: est.P_fe_stator_W,pinn: pinnLosses?.P_fe_stator_W ?? null },
      { name: 'Fe rotor', analytical: est.P_fe_rotor_W, pinn: pinnLosses?.P_fe_rotor_W ?? null },
      { name: 'Mag eddy', analytical: est.P_mag_eddy_W, pinn: pinnLosses?.P_mag_eddy_W ?? null },
    ];
    return rows.map(r => ({
      ...r,
      analytical: parseFloat(r.analytical.toFixed(1)),
      pinn: r.pinn != null ? parseFloat(r.pinn.toFixed(1)) : null,
    }));
  }, [data, pinnLosses]);

  if (loading) return (
    <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
      <CircularProgress size={28} sx={{ color: '#3b82f6' }}/>
    </Box>
  );
  if (error) return (
    <Box sx={{ color: '#ef4444', fontSize: 12, p: 2 }}>Physics API error: {error}</Box>
  );
  if (!data) return null;

  const sc = data.scalars;
  const lossTotal = data.losses.P_total_W;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>

      {/* ── Header ── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <Typography variant="h6" sx={{ color: '#e2e8f0', fontWeight: 700 }}>
          Physics Dashboard
        </Typography>
        <Chip label="real FEM" size="small" sx={{ fontSize: 10, bgcolor: '#1a2e1a', color: '#4ade80' }}/>
        <Box sx={{ flex: 1 }}/>
        <IconButton size="small" onClick={fetchData} sx={{ color: '#475569' }}>
          <RefreshIcon fontSize="small"/>
        </IconButton>
      </Box>

      {/* ── Top-of-tab summary card — populated by TransientCharts ── */}
      <SummaryTable summary={transientSummary}/>

      {/* Analytical "Key Scalars" table removed — all key quantities
          (T_em, P_cu/Fe/eddy, |B|_max, A_z range) come from the FEM
          solve and are shown in the FemFieldChart sidebar. */}

      {/* Analytical TORQUE summary card removed — FemFieldChart sidebar
          shows the FEM-computed T_em / P_mech / efficiency. */}
      <Paper sx={{ ...CHART_STYLE,
        borderColor: data.torque.T_em_Nm > 0.5 ? '#4ade80' : '#f97316',
        bgcolor: '#060d17', display: 'none' }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2, flexWrap: 'wrap' }}>
          {/* Main torque value */}
          <Box sx={{ flex: '0 0 auto', textAlign: 'center',
            bgcolor: '#0a2010', border: '1px solid #14532d',
            borderRadius: 2, px: 3, py: 2, minWidth: 140 }}>
            <Typography sx={{ fontSize: 11, color: '#4ade80', textTransform: 'uppercase',
              letterSpacing: '0.1em', mb: 0.5 }}>Torque T_em</Typography>
            <Typography sx={{ fontSize: 36, fontWeight: 900,
              color: data.torque.T_em_Nm > 0.5 ? '#4ade80' : '#f97316' }}>
              {data.torque.T_em_Nm.toFixed(1)}
            </Typography>
            <Typography sx={{ fontSize: 14, color: '#64748b' }}>N·m</Typography>
            <Typography sx={{ fontSize: 9, color: '#475569', mt: 0.5 }}>
              γ = {data.torque.gamma_deg}°
            </Typography>
          </Box>

          {/* Max torque at gamma=90 */}
          <Box sx={{ flex: '0 0 auto', textAlign: 'center',
            bgcolor: '#0a1020', border: '1px solid #1e293b',
            borderRadius: 2, px: 2, py: 2, minWidth: 120 }}>
            <Typography sx={{ fontSize: 10, color: '#3b82f6', textTransform: 'uppercase',
              letterSpacing: '0.08em', mb: 0.5 }}>T_max (γ=90°)</Typography>
            <Typography sx={{ fontSize: 28, fontWeight: 800, color: '#3b82f6' }}>
              {data.torque.T_max_Nm.toFixed(1)}
            </Typography>
            <Typography sx={{ fontSize: 13, color: '#64748b' }}>N·m</Typography>
          </Box>

          {/* Power */}
          <Box sx={{ flex: '0 0 auto', textAlign: 'center',
            bgcolor: '#0a1020', border: '1px solid #1e293b',
            borderRadius: 2, px: 2, py: 2, minWidth: 120 }}>
            <Typography sx={{ fontSize: 10, color: '#a78bfa', textTransform: 'uppercase',
              letterSpacing: '0.08em', mb: 0.5 }}>P_mech (γ=90°)</Typography>
            <Typography sx={{ fontSize: 28, fontWeight: 800, color: '#a78bfa' }}>
              {(data.torque.P_mech_max_W / 1000).toFixed(1)}
            </Typography>
            <Typography sx={{ fontSize: 13, color: '#64748b' }}>kW</Typography>
          </Box>

          {/* Efficiency at gamma=90 */}
          {(() => {
            const P_m = data.torque.P_mech_max_W;
            const P_l = data.losses.P_total_W;
            const eta = P_m / (P_m + P_l) * 100;
            return (
              <Box sx={{ flex: '0 0 auto', textAlign: 'center',
                bgcolor: eta > 93 ? '#0a2010' : '#1a1000',
                border: `1px solid ${eta > 93 ? '#14532d' : '#78350f'}`,
                borderRadius: 2, px: 2, py: 2, minWidth: 100 }}>
                <Typography sx={{ fontSize: 10, color: eta > 93 ? '#4ade80' : '#fbbf24',
                  textTransform: 'uppercase', letterSpacing: '0.08em', mb: 0.5 }}>η (est.)</Typography>
                <Typography sx={{ fontSize: 28, fontWeight: 800,
                  color: eta > 93 ? '#4ade80' : '#fbbf24' }}>
                  {eta.toFixed(1)}
                </Typography>
                <Typography sx={{ fontSize: 13, color: '#64748b' }}>%</Typography>
              </Box>
            );
          })()}

          {/* Motor constants */}
          <Box sx={{ flex: 1, minWidth: 200 }}>
            <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#3b82f6',
              textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>Motor constants</Typography>
            {[
              ['Kt (torque const.)', `${data.torque.Kt_Nm_A.toFixed(4)} N·m/A`],
              ['Ke (back-EMF)', `${data.torque.Ke_Vs_rad.toFixed(5)} V·s/rad`],
              ['Ψ_pm (flux link.)', `${data.torque.psi_pm_Wb.toFixed(5)} Wb`],
              ['I_d (d-axis)',  `${data.torque.I_d_A.toFixed(1)} A`],
              ['I_q (q-axis)',  `${data.torque.I_q_A.toFixed(1)} A`],
              ['k_w (winding factor)', `${data.torque.k_w}`],
            ].map(([k, v]) => (
              <Box key={k as string} sx={{ display: 'flex', justifyContent: 'space-between', py: 0.2 }}>
                <Typography sx={{ fontSize: 10, color: '#64748b' }}>{k}</Typography>
                <Typography sx={{ fontSize: 10, fontWeight: 600, color: '#e2e8f0' }}>{v}</Typography>
              </Box>
            ))}
          </Box>
        </Box>

        {data.torque.T_em_Nm < 0.5 && (
          <Box sx={{ mt: 1.5, p: 1, bgcolor: '#1a0e00', borderRadius: 1,
            border: '1px solid #78350f' }}>
            <Typography sx={{ fontSize: 11, color: '#fbbf24' }}>
              ⚠ {data.torque.note}
            </Typography>
            <Typography sx={{ fontSize: 10, color: '#92400e', mt: 0.5 }}>
              Set Phase Offset γ = 0° in the left panel for maximum torque (pure q-axis operation).
            </Typography>
          </Box>
        )}
      </Paper>

      {/* ── MASS TABLE (hidden — duplicates the SummaryTable card on top) ── */}
      <Paper sx={{ ...CHART_STYLE, display: 'none' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
          <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6',
            textTransform: 'uppercase', letterSpacing: '0.08em' }}>Component Masses</Typography>
          <Chip label={`${data.masses.total_active_kg} kg active`} size="small"
            sx={{ fontSize: 10, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
          <Chip label={`~${data.masses.estimated_total_with_frame_kg} kg with frame`}
            size="small" sx={{ fontSize: 10, bgcolor: '#1e293b', color: '#64748b' }}/>
        </Box>
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr auto auto auto', gap: '1px',
          bgcolor: '#1e293b', borderRadius: 1, overflow: 'hidden' }}>
          {['Component', 'Material', 'Vol [cm³]', 'Mass [kg]'].map(h => (
            <Box key={h} sx={{ bgcolor: '#0a1628', px: 1.5, py: 0.75 }}>
              <Typography sx={{ fontSize: 9, color: '#475569', fontWeight: 700,
                textTransform: 'uppercase' }}>{h}</Typography>
            </Box>
          ))}
          {data.masses.components.map((c, i) => {
            const colors = ['#f87171','#fbbf24','#38bdf8','#f87171','#4ade80'];
            return (
              <React.Fragment key={i}>
                <Box sx={{ bgcolor: '#060d17', px: 1.5, py: 0.6 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                    <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: colors[i], flexShrink: 0 }}/>
                    <Typography sx={{ fontSize: 10, color: '#94a3b8' }}>{c.name}</Typography>
                  </Box>
                </Box>
                <Box sx={{ bgcolor: '#060d17', px: 1.5, py: 0.6 }}>
                  <Typography sx={{ fontSize: 10, color: '#475569' }}>{c.material}</Typography>
                </Box>
                <Box sx={{ bgcolor: '#060d17', px: 1.5, py: 0.6, textAlign: 'right' }}>
                  <Typography sx={{ fontSize: 10, color: '#64748b' }}>{c.volume_cm3}</Typography>
                </Box>
                <Box sx={{ bgcolor: '#060d17', px: 1.5, py: 0.6, textAlign: 'right' }}>
                  <Typography sx={{ fontSize: 11, fontWeight: 700, color: colors[i] }}>
                    {c.mass_kg}
                  </Typography>
                </Box>
              </React.Fragment>
            );
          })}
          <Box sx={{ bgcolor: '#0a1628', px: 1.5, py: 0.75, gridColumn: 'span 3',
            borderTop: '1px solid #1e293b' }}>
            <Typography sx={{ fontSize: 10, fontWeight: 700, color: '#e2e8f0' }}>
              Total active components
            </Typography>
          </Box>
          <Box sx={{ bgcolor: '#0a1628', px: 1.5, py: 0.75, textAlign: 'right',
            borderTop: '1px solid #1e293b' }}>
            <Typography sx={{ fontSize: 13, fontWeight: 800, color: '#fbbf24' }}>
              {data.masses.total_active_kg} kg
            </Typography>
          </Box>
        </Box>
        <Typography sx={{ fontSize: 9, color: '#334155', mt: 1 }}>
          {data.masses.note}
          {` Power density: ${(data.torque.P_mech_max_W / data.masses.total_active_kg / 1000).toFixed(1)} kW/kg active
            — ${(data.torque.P_mech_max_W / data.masses.estimated_total_with_frame_kg / 1000).toFixed(1)} kW/kg with frame.`}
          {` Torque density: ${(data.torque.T_max_Nm / data.masses.total_active_kg).toFixed(1)} N·m/kg.`}
        </Typography>
      </Paper>

      {/* Analytical Loss Budget hidden — FEM transient/field charts above
          show the real per-domain losses. */}
      <Paper sx={{ ...CHART_STYLE, display: 'none' }}>
        <SectionLabel title="Loss Budget (corrected model)"
          note="specific-loss [W/kg] + slot harmonics"
          tooltip="Fe: Steinmetz with k calibrated to 20SW1200 datasheet (1.2 W/kg at 50Hz/1T). Magnets: slot harmonic AC B only (fundamental = DC in rotor frame for SPMSM)."/>
        <Typography sx={{ fontSize: 10, color: '#4ade80', mb: 1.5 }}>
          ✓ Saturation + synchronous-machine physics included. Total = {data.losses.P_total_W.toFixed(0)} W
          vs naive linear model ~38 kW. PINN will compute exact values from solved A_z field.
        </Typography>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          {[
            { label: 'Cu DC',   val: data.losses.P_cu_dc_W,    color: '#fbbf24',
              note: `I²R, R=${sc.R_phase_mOhm}mΩ` },
            { label: 'Cu AC',   val: data.losses.P_cu_prox_W,  color: '#fb923c',
              note: `proximity, d/δ=${sc.d_wire_over_delta}` },
            { label: 'Fe stat', val: data.losses.P_fe_stator_W, color: '#f87171',
              note: `B_tooth≈${data.losses.B_tooth_T?.toFixed(2)}T` },
            { label: 'Fe rot',  val: data.losses.P_fe_rotor_W, color: '#c084fc',
              note: `f=${data.losses.f_slot_Hz?.toFixed(0)}Hz` },
            { label: 'Mag',     val: data.losses.P_mag_eddy_W, color: '#38bdf8',
              note: `B_ac=${((data.losses.B_harm_in_mag_T ?? 0)*1000).toFixed(1)}mT` },
            { label: 'Shaft',   val: data.losses.P_shaft_W ?? 0, color: '#4ade80',
              note: 'Al, surface Z' },
          ].map(({ label, val, color, note }) => (
            <Box key={label} sx={{
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              bgcolor: '#060d17', border: `1px solid ${color}33`,
              borderRadius: 1.5, px: 2, py: 1.5, minWidth: 90, flex: 1,
            }}>
              <Typography sx={{ fontSize: 9, color: '#64748b', mb: 0.25 }}>{label}</Typography>
              <Typography sx={{ fontSize: 20, fontWeight: 800, color, lineHeight: 1.1 }}>
                {val >= 1000 ? `${(val/1000).toFixed(1)}k` : val.toFixed(0)}
              </Typography>
              <Typography sx={{ fontSize: 9, color: '#475569' }}>W</Typography>
              <Typography sx={{ fontSize: 8, color: '#334155', mt: 0.25, textAlign: 'center' }}>
                {note}
              </Typography>
            </Box>
          ))}
        </Box>
        <Box sx={{ mt: 1, p: 1, bgcolor: '#060d17', borderRadius: 1 }}>
          <Typography sx={{ fontSize: 9, color: '#475569' }}>
            {data.losses.model_note}
            {` f_slot = ${data.losses.f_slot_Hz?.toFixed(0)} Hz (slot harmonic in rotor frame = f_e × N_slots / p).`}
            {` eta_analytical = ${(data.torque.P_mech_max_W / (data.torque.P_mech_max_W + data.losses.P_total_W) * 100).toFixed(1)}%`}
          </Typography>
        </Box>
      </Paper>

      {/* ── Field viewer / animation — one widget covers both the static
            initial snapshot (frame[0] at rotor_angle = 0) AND the full
            playback across one electrical period.  Slider lets you scrub
            through the rotor positions; play button auto-advances. ── */}
      <FemAnimationViewer gamma_deg={gamma_deg} I_phase_rms={I_phase_rms}
        n_frames={Math.min(steps, 24)} runNonce={runNonce} fresh={fresh} onPayload={setFemPayload}/>

      {/* ── Field inspector: Magnetostatic ↔ Eddy-current solve ──────────
          Standalone FemFieldChart (fetches its own data, no payloadOverride)
          with the Magneto/Eddy toggle — switch to "Eddy" to run the σ·∂A/∂t
          solve and inspect A_z / |B| / J (real current density in the copper). */}
      <FemFieldChart gamma_deg={gamma_deg} I_phase_rms={I_phase_rms}/>

      {/* ── Transient: T(t), P(t), V(t) — one FEM solve per time step ── */}
      <TransientCharts gamma_deg={gamma_deg} I_phase_rms={I_phase_rms}
        steps={steps} runNonce={runNonce} fresh={fresh} onBusyChange={onBusyChange}
        onSummary={setTransientSummary}/>

      {/* Analytical MMF(θ) hidden. */}
      <Paper sx={{ ...CHART_STYLE, display: 'none' }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <SectionLabel title="Magnetomotive Force MMF(θ)"
            note="analytical"
            tooltip="Spatial distribution of winding MMF around the airgap. Rectangular steps from concentrated slots. Ansys shows the same from FEA current density."/>
          <IconButton size="small" sx={{ color: '#334155' }}
            onClick={() => exportCSV('MMF.csv', spatialData.map(p => ({ theta: p.theta, MMF: p.MMF })))}>
            <DownloadIcon fontSize="small"/>
          </IconButton>
        </Box>
        <Typography sx={{ fontSize: 10, color: '#475569', mb: 1 }}>
          Peak = {sc.MMF_peak_At} At · 24 slots · 14 turns/slot · I_coil_peak = {sc.I_coil_peak_A} A
        </Typography>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={spatialData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <CartesianGrid {...GRID_STYLE}/>
            <XAxis dataKey="theta" tickFormatter={v => `${v}°`} tick={AXIS_STYLE}
              label={{ value: 'θ_mech [°]', position: 'insideBottomRight', offset: -5,
                style: { fontSize: 10, fill: '#475569' }}}/>
            <YAxis tick={AXIS_STYLE} label={{ value: 'MMF [At]', angle: -90,
              position: 'insideLeft', style: { fontSize: 10, fill: '#475569' }}}/>
            <RcTooltip {...TOOLTIP_STYLE}
              formatter={(v: number) => [`${v.toFixed(0)} At`, 'MMF']}
              labelFormatter={(l: number) => `θ = ${l}°`}/>
            <ReferenceLine y={0} stroke={C.border}/>
            <Line dataKey="MMF" stroke={C.MMF} dot={false} strokeWidth={1.5} name="MMF"/>
          </LineChart>
        </ResponsiveContainer>
      </Paper>

      {/* Analytical B_r(θ) hidden. */}
      <Paper sx={{ ...CHART_STYLE, display: 'none' }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <SectionLabel title="Air-Gap Radial Flux Density B_r(θ)"
            note="analytical (linear model)"
            tooltip="B_winding = μ₀·MMF/g. B_pm = rectangular PM approximation at Br=1.19T. B_total = sum. Compare with Ansys B_r on airgap path."/>
          <IconButton size="small" sx={{ color: '#334155' }}
            onClick={() => exportCSV('B_airgap.csv', spatialData)}>
            <DownloadIcon fontSize="small"/>
          </IconButton>
        </Box>
        <Typography sx={{ fontSize: 10, color: '#f97316', mb: 1 }}>
          ⚠ Linear model only (no saturation). PINN will give exact B from solved A_z field.
          B_total_peak = {sc.B_total_peak_T} T (over-estimated — steel saturation not modelled).
        </Typography>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={spatialData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <CartesianGrid {...GRID_STYLE}/>
            <XAxis dataKey="theta" tickFormatter={v => `${v}°`} tick={AXIS_STYLE}/>
            <YAxis tick={AXIS_STYLE}
              label={{ value: 'B [T]', angle: -90, position: 'insideLeft',
                style: { fontSize: 10, fill: '#475569' }}}/>
            <RcTooltip {...TOOLTIP_STYLE}
              formatter={(v: number, name: string) => [`${v.toFixed(3)} T`, name]}
              labelFormatter={(l: number) => `θ = ${l}°`}/>
            <Legend wrapperStyle={{ fontSize: 11 }}/>
            <ReferenceLine y={0} stroke={C.border}/>
            <Line dataKey="B_pm"      stroke={C.B_pm}      dot={false} strokeWidth={1.5} name="B_pm"      strokeDasharray="4 2"/>
            <Line dataKey="B_winding" stroke={C.B_winding} dot={false} strokeWidth={1.5} name="B_winding" strokeDasharray="2 3"/>
            <Line dataKey="B_total"   stroke={C.B_total}   dot={false} strokeWidth={2}   name="B_total"/>
          </LineChart>
        </ResponsiveContainer>
      </Paper>

      {/* Analytical Spatial Harmonics hidden. */}
      <Paper sx={{ ...CHART_STYLE, display: 'none' }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <SectionLabel title="Spatial Harmonic Spectrum of B"
            note="FFT of B_total"
            tooltip="Spatial orders of the air-gap flux density. Dominant order = pole-pair number (14). Sub-harmonics cause additional iron losses. Compare Ansys FFT of B_r."/>
          <IconButton size="small" sx={{ color: '#334155' }}
            onClick={() => exportCSV('harmonics.csv', harmonicData)}>
            <DownloadIcon fontSize="small"/>
          </IconButton>
        </Box>
        <Typography sx={{ fontSize: 10, color: '#475569', mb: 1 }}>
          Fundamental = order {14} (pole pairs). Sub/super harmonics cause extra Fe losses &amp; noise.
        </Typography>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={harmonicData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <CartesianGrid {...GRID_STYLE}/>
            <XAxis dataKey="order" tick={AXIS_STYLE}
              label={{ value: 'Harmonic order', position: 'insideBottomRight', offset: -5,
                style: { fontSize: 10, fill: '#475569' }}}/>
            <YAxis tick={AXIS_STYLE}
              label={{ value: '|B_n| [T]', angle: -90, position: 'insideLeft',
                style: { fontSize: 10, fill: '#475569' }}}/>
            <RcTooltip {...TOOLTIP_STYLE}
              formatter={(v: number) => [`${v.toFixed(4)} T`, 'Amplitude']}
              labelFormatter={(l: number) => `Order ${l}`}/>
            <Bar dataKey="amp" name="Amplitude">
              {harmonicData.map((entry) => (
                <Cell key={entry.order}
                  fill={entry.order === 14 ? '#fbbf24' : entry.order % 2 === 0 ? '#3b82f6' : '#475569'}/>
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <Typography sx={{ fontSize: 9, color: '#334155', mt: 0.5 }}>
          Yellow = fundamental (p=14). Blue = even orders. Grey = odd. Sub-harmonics below 14 indicate winding asymmetry.
        </Typography>
      </Paper>

      {/* Analytical Slot Current Density hidden — FEM transient I(t) chart
          replaces it with the actual per-phase currents. */}
      <Paper sx={{ ...CHART_STYLE, display: 'none' }}>
        <SectionLabel title="Slot Current Density J_z(slot)"
          note="at current rotor angle"
          tooltip="J_z per slot in MA/m². Colour = phase (A/B/C). Sign = direction (+into page, -out). Compare Ansys current density map."/>
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={slotBarData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid {...GRID_STYLE}/>
            <XAxis dataKey="slot" tick={AXIS_STYLE}
              label={{ value: 'Slot #', position: 'insideBottomRight', offset: -5,
                style: { fontSize: 10, fill: '#475569' }}}/>
            <YAxis tick={AXIS_STYLE}
              label={{ value: 'J_z [MA/m²]', angle: -90, position: 'insideLeft',
                style: { fontSize: 10, fill: '#475569' }}}/>
            <RcTooltip {...TOOLTIP_STYLE}
              formatter={(v: number, _: string, props: { payload: typeof slotBarData[0] }) =>
                [`${v.toFixed(3)} MA/m²`, `Phase ${props.payload.phase}`]}
              labelFormatter={(l: number) => `Slot ${l} (φ=${slotBarData[l]?.phi}°)`}/>
            <ReferenceLine y={0} stroke={C.border}/>
            <Bar dataKey="J" name="J_z">
              {slotBarData.map((entry) => (
                <Cell key={entry.slot}
                  fill={entry.phase === 'A' ? C.A : entry.phase === 'B' ? C.B : C.C}
                  opacity={Math.abs(entry.J) > 0.01 ? 1 : 0.3}/>
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <Box sx={{ display: 'flex', gap: 2, mt: 0.5 }}>
          {['A','B','C'].map(ph => (
            <Box key={ph} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Box sx={{ width: 10, height: 10, borderRadius: '50%',
                bgcolor: ph === 'A' ? C.A : ph === 'B' ? C.B : C.C }}/>
              <Typography sx={{ fontSize: 10, color: '#64748b' }}>Phase {ph}</Typography>
            </Box>
          ))}
        </Box>
      </Paper>

      {/* Analytical Loss Breakdown hidden — FEM losses in TransientCharts. */}
      <Paper sx={{ ...CHART_STYLE, display: 'none' }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <SectionLabel title="Loss Breakdown"
            note="analytical estimates vs PINN"
            tooltip="Analytical = classical formulas. PINN = from solved A_z field (AC redistribution included). Compare Ansys loss report."/>
          <IconButton size="small" sx={{ color: '#334155' }}
            onClick={() => exportCSV('losses.csv', lossData)}>
            <DownloadIcon fontSize="small"/>
          </IconButton>
        </Box>
        <Typography sx={{ fontSize: 10, color: '#f97316', mb: 1 }}>
          ⚠ Analytical Fe &amp; Mag estimates use linear B model — expect 3–10× overestimate vs PINN.
          Total analytical = {lossTotal.toFixed(0)} W.
        </Typography>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={lossData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <CartesianGrid {...GRID_STYLE}/>
            <XAxis dataKey="name" tick={{ ...AXIS_STYLE, fontSize: 10 }}/>
            <YAxis tick={AXIS_STYLE}
              label={{ value: 'P [W]', angle: -90, position: 'insideLeft',
                style: { fontSize: 10, fill: '#475569' }}}/>
            <RcTooltip {...TOOLTIP_STYLE}
              formatter={(v: number | null, name: string) =>
                [v != null ? `${v.toFixed(1)} W` : '—', name]}/>
            <Legend wrapperStyle={{ fontSize: 11 }}/>
            <Bar dataKey="analytical" name="Analytical (classical)" fill="#475569" radius={[2,2,0,0]}/>
            {lossData.some(d => d.pinn != null) && (
              <Bar dataKey="pinn" name="PINN (from A_z)" fill="#4ade80" radius={[2,2,0,0]}/>
            )}
          </BarChart>
        </ResponsiveContainer>

        {/* Loss pie summary */}
        <Divider sx={{ borderColor: C.border, my: 1.5 }}/>
        <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#475569',
          textTransform: 'uppercase', letterSpacing: 1, mb: 0.75 }}>
          Analytical breakdown (% of total)
        </Typography>
        <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
          {[
            { label: 'Cu DC',    val: data.losses.P_cu_dc_W,    color: C.Cu },
            { label: 'Cu prox', val: data.losses.P_cu_prox_W,  color: '#d97706' },
            { label: 'Fe stat', val: data.losses.P_fe_stator_W,color: C.Fe_s },
            { label: 'Fe rot',  val: data.losses.P_fe_rotor_W, color: C.Fe_r },
            { label: 'Mag eddy',val: data.losses.P_mag_eddy_W, color: C.Mag },
          ].map(({ label, val, color }) => (
            <Box key={label} sx={{ display: 'flex', alignItems: 'center', gap: 0.5,
              bgcolor: '#060d17', px: 1, py: 0.4, borderRadius: 1 }}>
              <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: color }}/>
              <Typography sx={{ fontSize: 10, color: '#94a3b8' }}>{label}</Typography>
              <Typography sx={{ fontSize: 10, fontWeight: 700, color }}>
                {(val / lossTotal * 100).toFixed(1)}%
              </Typography>
              <Typography sx={{ fontSize: 9, color: '#475569' }}>
                {val.toFixed(0)}W
              </Typography>
            </Box>
          ))}
        </Box>
      </Paper>

      {/* Analytical loss waveform removed — TransientCharts above already
          shows the FEM-computed losses vs time. */}

{/* Magnet segmentation guidance hidden. */}
      <Paper sx={{ ...CHART_STYLE, borderColor: '#7c3aed', display: 'none' }}>
        <SectionLabel title="Magnet Eddy — Segmentation Impact"
          note="classical formula P ~ d²"
          tooltip="Eddy losses scale as d². Segmentation is the key lever. PINN will compute exact from A_z."/>
        <Typography sx={{ fontSize: 10, color: '#94a3b8', mb: 1 }}>
          Current design: unsegmented (d_tang = {(2*Math.PI*(39.1+56.3)/2/1000/28*0.9*1000).toFixed(1)} mm).
          P_mag scales as number_of_segments⁻².
        </Typography>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          {[1, 2, 4, 7, 14].map(k => {
            const P = data.losses.P_mag_eddy_W / (k * k);
            const isCurrent = k === 1;
            return (
              <Box key={k} sx={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                bgcolor: isCurrent ? '#2d1657' : '#060d17',
                border: `1px solid ${isCurrent ? '#7c3aed' : C.border}`,
                borderRadius: 1, px: 1.5, py: 1, minWidth: 80,
              }}>
                <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#a78bfa' }}>
                  {k === 1 ? 'current' : `×${k}`}
                </Typography>
                <Typography sx={{ fontSize: 14, fontWeight: 800,
                  color: P > 1000 ? '#ef4444' : P > 300 ? '#fbbf24' : '#4ade80' }}>
                  {P.toFixed(0)} W
                </Typography>
                <Typography sx={{ fontSize: 9, color: '#475569' }}>segments={k}</Typography>
              </Box>
            );
          })}
        </Box>
      </Paper>

    </Box>
  );
};

export default PhysicsDashboard;
