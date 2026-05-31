/**
 * TorqueWaveformChart  —  T(θ) over one electrical period.
 *
 * Method: Maxwell stress tensor on analytical A_z field.
 *   T = (L/μ₀) r² ∮ B_r(φ) · B_φ(φ) dφ
 *
 * The waveform SHAPE captures:
 *   - Average electromagnetic torque T_avg
 *   - Cogging ripple (12 periods per electrical period for 24s/28p)
 *   - Load-angle variation
 *
 * Amplitude is scaled from the analytical flux-linkage formula
 * (free-space Green's function underestimates by ~8-10× without iron).
 *
 * PINN replaces this with the exact T from nonlinear A_z.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip as RcTooltip, Legend, ReferenceLine,
} from 'recharts';
import { Box, Paper, Typography, Chip, CircularProgress, IconButton } from '@mui/material';
import RefreshIcon   from '@mui/icons-material/Refresh';
import DownloadIcon  from '@mui/icons-material/Download';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

interface TorquePoint {
  theta_elec_deg: number;
  theta_mech_deg: number;
  T_Nm:           number;
  T_Nm_scaled:    number;
}

interface TorqueData {
  n_points:       number;
  elec_period_deg: number;
  mech_period_deg: number;
  gamma_deg:      number;
  pole_pairs:     number;
  T_avg_Nm:       number;
  T_max_Nm:       number;
  T_min_Nm:       number;
  T_ripple_Nm:    number;
  T_ripple_pct:   number;
  P_mech_W:       number;
  scale_factor:   number;
  points:         TorquePoint[];
  note:           string;
}

interface Props {
  gamma_deg: number;
  n_points?: number;
  pinnTorqueData?: { theta: number; T: number }[] | null;
}

const TOOLTIP_STYLE = {
  contentStyle: { backgroundColor: '#1e293b', border: '1px solid #1e293b',
    borderRadius: 6, fontSize: 11 },
  labelStyle: { color: '#94a3b8' },
};

function exportCSV(data: TorqueData) {
  const rows = data.points.map(p =>
    `${p.theta_elec_deg},${p.theta_mech_deg},${p.T_Nm_scaled},${p.T_Nm}`
  );
  const csv  = 'theta_elec_deg,theta_mech_deg,T_Nm_scaled,T_Nm_raw\n' + rows.join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const a    = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = `torque_gamma${data.gamma_deg}.csv`; a.click();
}

const TorqueWaveformChart: React.FC<Props> = ({ gamma_deg, n_points = 60, pinnTorqueData }) => {
  const [data,    setData]    = useState<TorqueData | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(() => {
    setLoading(true);
    fetch(`${API}/api/simulation/physics/torque_sweep?gamma_deg=${gamma_deg}&n_rotor=${n_points}&n_ag=720`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [gamma_deg, n_points]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) return (
    <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
      <CircularProgress size={24} sx={{ color: '#3b82f6' }}/>
    </Box>
  );
  if (!data) return null;

  // Merge PINN data if available
  const chartData = data.points.map(p => {
    const row: Record<string, number> = {
      theta: p.theta_elec_deg,
      T_analytical: p.T_Nm_scaled,
    };
    if (pinnTorqueData) {
      const nearest = pinnTorqueData.reduce((a, b) =>
        Math.abs(b.theta - p.theta_elec_deg) < Math.abs(a.theta - p.theta_elec_deg) ? b : a
      );
      row.T_pinn = nearest.T;
    }
    return row;
  });

  const coggingLines = Array.from({ length: 11 }, (_, i) => (i + 1) * 30);
  const { T_avg_Nm, T_max_Nm, T_min_Nm, T_ripple_Nm, T_ripple_pct, P_mech_W,
          mech_period_deg, scale_factor, note } = data;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>

      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#4ade80',
          textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Torque T(θ) — One Electrical Period
        </Typography>
        <Chip label={`${data.elec_period_deg}° elec = ${mech_period_deg.toFixed(2)}° mech`}
          size="small" sx={{ fontSize: 9, height: 16, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
        <Chip label="Maxwell stress tensor" size="small"
          sx={{ fontSize: 9, height: 16, bgcolor: '#1a2e1a', color: '#4ade80' }}/>
        <Chip label={`γ = ${gamma_deg}°`} size="small"
          sx={{ fontSize: 9, height: 16, bgcolor: '#1e293b', color: '#64748b' }}/>
        <Box sx={{ flex: 1 }}/>
        <IconButton size="small" onClick={fetchData} sx={{ color: '#475569' }}>
          <RefreshIcon fontSize="small"/>
        </IconButton>
        <IconButton size="small" onClick={() => data && exportCSV(data)} sx={{ color: '#475569' }}>
          <DownloadIcon fontSize="small"/>
        </IconButton>
      </Box>

      {/* Summary cards */}
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
        {[
          { label: 'T_avg',    val: T_avg_Nm.toFixed(2),  unit: 'N·m', color: '#4ade80' },
          { label: 'T_max',    val: T_max_Nm.toFixed(2),  unit: 'N·m', color: '#fbbf24' },
          { label: 'T_min',    val: T_min_Nm.toFixed(2),  unit: 'N·m', color: '#60a5fa' },
          { label: 'ΔT ripple',val: T_ripple_Nm.toFixed(3), unit: `N·m (${T_ripple_pct.toFixed(1)}%)`, color: '#f87171' },
          { label: 'P_mech',   val: (P_mech_W/1000).toFixed(2), unit: 'kW', color: '#a78bfa' },
        ].map(({ label, val, unit, color }) => (
          <Box key={label} sx={{
            bgcolor: '#060d17', border: '1px solid #1e293b',
            borderRadius: 1.5, px: 1.5, py: 1, flex: 1, minWidth: 80, textAlign: 'center',
          }}>
            <Typography sx={{ fontSize: 9, color: '#64748b' }}>{label}</Typography>
            <Typography sx={{ fontSize: 16, fontWeight: 800, color }}>{val}</Typography>
            <Typography sx={{ fontSize: 9, color: '#475569' }}>{unit}</Typography>
          </Box>
        ))}
      </Box>

      {/* Chart */}
      <Paper sx={{ bgcolor: '#060d17', border: '1px solid #1e293b', borderRadius: 2, p: 1.5 }}>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={chartData} margin={{ top: 10, right: 30, left: 15, bottom: 10 }}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3"/>
            <XAxis dataKey="theta"
              tickFormatter={v => `${v}°`}
              tick={{ stroke: '#475569', fontSize: 10 }}
              label={{ value: 'θ_elec [°]', position: 'insideBottomRight',
                offset: -8, style: { fontSize: 10, fill: '#475569' }}}/>
            <YAxis tick={{ stroke: '#475569', fontSize: 10 }}
              tickFormatter={v => `${v.toFixed(1)}`}
              label={{ value: 'T [N·m]', angle: -90, position: 'insideLeft',
                style: { fontSize: 10, fill: '#475569' }}}/>
            <RcTooltip {...TOOLTIP_STYLE}
              formatter={(v: number, name: string) => [
                `${v.toFixed(4)} N·m`, name === 'T_analytical' ? 'T (analytical)' : 'T (PINN)',
              ]}
              labelFormatter={(l: number) => `θ_elec = ${l}°  (${(l/data.pole_pairs).toFixed(3)}° mech)`}/>
            <Legend wrapperStyle={{ fontSize: 11 }}/>

            {/* Average line */}
            <ReferenceLine y={T_avg_Nm} stroke="#4ade80" strokeDasharray="6 3"
              strokeWidth={1.5} label={{ value: `T_avg=${T_avg_Nm.toFixed(2)}`, fill:'#4ade80', fontSize:9, position:'right' }}/>

            {/* Cogging boundaries every 30° elec */}
            {coggingLines.map(deg => (
              <ReferenceLine key={deg} x={deg} stroke="#f97316"
                strokeDasharray="2 4" strokeWidth={0.7} strokeOpacity={0.5}/>
            ))}

            <Line dataKey="T_analytical" name="T_analytical"
              stroke="#4ade80" dot={false} strokeWidth={2}/>
            {pinnTorqueData && (
              <Line dataKey="T_pinn" name="T_pinn"
                stroke="#fbbf24" dot={false} strokeWidth={2.5}
                strokeDasharray="6 2"/>
            )}
          </LineChart>
        </ResponsiveContainer>
      </Paper>

      {/* Cogging analysis */}
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
        <Box sx={{ flex: 1, bgcolor: '#060d17', p: 1.5, borderRadius: 1,
          border: '1px solid #1e293b' }}>
          <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#475569',
            textTransform: 'uppercase', letterSpacing: 1, mb: 0.5 }}>
            Cogging Analysis
          </Typography>
          {[
            ['Cogging periods/elec', '12  (= LCM(24,28)/28)'],
            ['Cogging period',       '30° elec = 2.143° mech'],
            ['Ripple ΔT',           `${T_ripple_Nm.toFixed(3)} N·m (${T_ripple_pct.toFixed(1)}%)`],
            ['Scale factor',        `×${scale_factor}  (iron μ_r correction)`],
          ].map(([k, v]) => (
            <Box key={k} sx={{ display: 'flex', justifyContent: 'space-between', py: 0.2 }}>
              <Typography sx={{ fontSize: 10, color: '#64748b' }}>{k}</Typography>
              <Typography sx={{ fontSize: 10, color: '#e2e8f0', fontWeight: 600 }}>{v}</Typography>
            </Box>
          ))}
        </Box>
        <Box sx={{ flex: 2, bgcolor: '#060d17', p: 1.5, borderRadius: 1,
          border: '1px solid #1e293b' }}>
          <Typography sx={{ fontSize: 9, fontWeight: 700, color: '#f97316',
            textTransform: 'uppercase', letterSpacing: 1, mb: 0.5 }}>
            Method Note
          </Typography>
          <Typography sx={{ fontSize: 10, color: '#475569', lineHeight: 1.5 }}>
            {note}
          </Typography>
        </Box>
      </Box>

    </Box>
  );
};

export default TorqueWaveformChart;
