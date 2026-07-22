/**
 * Loss Waveforms — one electrical period (0–360°)
 *
 * 5 curves on one chart:
 *   DC coils   — constant I²R  (analytical)
 *   AC coils   — proximity eddy  (analytical Dowell)
 *   AC core    — stator Fe Bertotti  (analytical)
 *   AC magnets — NdFeB eddy  (analytical classical)
 *   AC shaft   — Al6061 surface impedance  (analytical)
 *
 * X axis: electrical degrees [0, 360]
 * Y axis: power [W]
 * All curves are analytical estimates — PINN provides exact values.
 *
 * Export to CSV included for Ansys comparison.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip as RcTooltip, Legend, ReferenceLine,
} from 'recharts';
import {
  Box, Paper, Typography, Chip, CircularProgress,
  IconButton, Tooltip, ToggleButtonGroup, ToggleButton,
} from '@mui/material';
import DownloadIcon    from '@mui/icons-material/Download';
import RefreshIcon     from '@mui/icons-material/Refresh';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// ── Colour scheme ─────────────────────────────────────────────────────────────
const COLORS = {
  P_cu_dc:   '#fbbf24',   // amber   — DC coils
  P_cu_ac:   '#fb923c',   // orange  — AC coils (proximity)
  P_fe_stat: '#f87171',   // red     — AC core stator
  P_fe_rot:  '#c084fc',   // purple  — AC core rotor
  P_mag:     '#38bdf8',   // sky     — AC magnets
  P_shaft:   '#4ade80',   // green   — AC shaft
};
const LABELS: Record<string, string> = {
  P_cu_dc:   'DC coils (I²R)',
  P_cu_ac:   'AC coils (proximity)',
  P_fe_stat: 'AC core — stator',
  P_fe_rot:  'AC core — rotor',
  P_mag:     'AC magnets (eddy)',
  P_shaft:   'AC shaft (Al6061)',
};

// ── types ─────────────────────────────────────────────────────────────────────
interface SweepPoint {
  theta_elec_deg: number;
  theta_mech_deg: number;
  P_cu_dc:   number;
  P_cu_ac:   number;
  P_fe_stat: number;
  P_fe_rot:  number;
  P_mag:     number;
  P_shaft:   number;
  P_total:   number;
}

interface SweepData {
  n_points:        number;
  elec_period_deg: number;
  mech_period_deg: number;
  gamma_deg:       number;
  freq_hz:         number;
  rpm:             number;
  pole_pairs:      number;
  points:          SweepPoint[];
  constants: {
    P_cu_dc_W:       number;
    R_phase_mOhm:    number;
    d_over_delta_cu: number;
    regime:          string;
  };
}

interface Props {
  gamma_deg:  number;
  n_points?:  number;
  pinnPoints?: { theta_elec_deg: number; P_total?: number }[] | null;
}

// ── helpers ───────────────────────────────────────────────────────────────────
function exportCSV(filename: string, data: SweepPoint[]) {
  if (!data.length) return;
  const header = Object.keys(data[0]).join(',');
  const body   = data.map(r => Object.values(r).join(',')).join('\n');
  const blob   = new Blob([header + '\n' + body], { type: 'text/csv' });
  const a      = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}

const TOOLTIP_STYLE = {
  contentStyle: { backgroundColor: 'var(--panel)', border: '1px solid var(--line-soft)',
    borderRadius: 6, fontSize: 11 },
  labelStyle: { color: 'var(--text-2)' },
};
const AXIS_STYLE = { stroke: 'var(--text-4)', fontSize: 10 };
const GRID_STYLE = { stroke: 'var(--panel)', strokeDasharray: '3 3' };

// ── main component ─────────────────────────────────────────────────────────────
const LossWaveformChart: React.FC<Props> = ({
  gamma_deg,
  n_points = 60,
  pinnPoints,
}) => {
  const [data,    setData]    = useState<SweepData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [scale,   setScale]   = useState<'linear' | 'log'>('linear');
  const [visible, setVisible] = useState<Record<string, boolean>>({
    P_cu_dc: true, P_cu_ac: true,
    P_fe_stat: true, P_fe_rot: true,
    P_mag: true, P_shaft: true,
  });

  const fetch_ = useCallback(() => {
    setLoading(true); setError(null);
    fetch(`${API}/api/simulation/physics/sweep?gamma_deg=${gamma_deg}&n_points=${n_points}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, [gamma_deg, n_points]);

  useEffect(() => { fetch_(); }, [fetch_]);

  const pts = data?.points ?? [];

  // ── summary row: average & range per loss ───────────────────────────────
  const summary = useMemo(() => {
    if (!pts.length) return [];
    return (Object.keys(COLORS) as (keyof typeof COLORS)[]).map(key => {
      const vals = pts.map(p => p[key as keyof SweepPoint] as number);
      const avg  = vals.reduce((a, b) => a + b, 0) / vals.length;
      const min  = Math.min(...vals);
      const max  = Math.max(...vals);
      return { key, avg, min, max, ripple: avg > 0 ? (max - min) / avg * 100 : 0 };
    });
  }, [pts]);

  const totalAvg = useMemo(() => {
    if (!pts.length) return 0;
    return pts.reduce((s, p) => s + p.P_total, 0) / pts.length;
  }, [pts]);

  if (loading) return (
    <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
      <CircularProgress size={24} sx={{ color: '#3b82f6' }}/>
    </Box>
  );
  if (error) return (
    <Box sx={{ color: '#ef4444', fontSize: 11, p: 1 }}>Sweep error: {error}</Box>
  );
  if (!data) return null;

  const { constants, mech_period_deg, elec_period_deg, freq_hz, rpm, pole_pairs } = data;
  const cogging_period = elec_period_deg / (data.n_points > 0 ? 12 : 12);  // 12 cogging per elec

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>

      {/* ── Header ── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#3b82f6',
          textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Loss Waveforms — One Electrical Period
        </Typography>
        <Chip label={`${elec_period_deg}° elec`} size="small"
          sx={{ fontSize: 9, height: 16, bgcolor: 'var(--line-accent)', color: '#93c5fd' }}/>
        <Chip label={`${mech_period_deg.toFixed(2)}° mech`} size="small"
          sx={{ fontSize: 9, height: 16, bgcolor: 'var(--panel)', color: 'var(--text-3)' }}/>
        <Chip label={`${n_points} pts`} size="small"
          sx={{ fontSize: 9, height: 16, bgcolor: 'var(--panel)', color: 'var(--text-3)' }}/>
        <Chip label="analytical estimates" size="small"
          sx={{ fontSize: 9, height: 16, bgcolor: '#2d1e0f', color: '#fb923c' }}/>
        <Box sx={{ flex: 1 }}/>
        <Tooltip title="Log scale shows small losses (Cu prox, shaft) better">
          <ToggleButtonGroup size="small" value={scale}
            exclusive onChange={(_, v) => v && setScale(v)}
            sx={{ '& .MuiToggleButton-root': { py: 0, px: 1, fontSize: 10, color: 'var(--text-4)',
              borderColor: 'var(--panel)', '&.Mui-selected': { color: '#3b82f6', bgcolor: 'var(--line-accent)' } } }}>
            <ToggleButton value="linear">Lin</ToggleButton>
            <ToggleButton value="log">Log</ToggleButton>
          </ToggleButtonGroup>
        </Tooltip>
        <IconButton size="small" onClick={fetch_} sx={{ color: 'var(--text-4)' }}>
          <RefreshIcon fontSize="small"/>
        </IconButton>
        <IconButton size="small" onClick={() => exportCSV('loss_sweep.csv', pts)}
          sx={{ color: 'var(--text-4)' }}>
          <DownloadIcon fontSize="small"/>
        </IconButton>
      </Box>

      <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>
        X = electrical degrees 0–360° (= {mech_period_deg.toFixed(2)}° mech, 1/{pole_pairs} revolution).
        {' '}freq = {freq_hz} Hz · {rpm} rpm · gamma = {gamma_deg}°.
        Vertical dashed = cogging boundaries (every 30° elec = {(30/pole_pairs).toFixed(2)}° mech).
      </Typography>

      {/* ── Toggle visibility ── */}
      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
        {(Object.keys(COLORS) as (keyof typeof COLORS)[]).map(key => (
          <Box key={key} onClick={() => setVisible(v => ({...v, [key]: !v[key]}))}
            sx={{
              display: 'flex', alignItems: 'center', gap: 0.5, cursor: 'pointer',
              opacity: visible[key] ? 1 : 0.35, transition: 'opacity .15s',
              bgcolor: 'var(--panel-2)', border: `1px solid ${visible[key] ? COLORS[key] : 'var(--panel)'}`,
              px: 1, py: 0.3, borderRadius: 1,
            }}>
            <Box sx={{ width: 10, height: 3, bgcolor: COLORS[key], borderRadius: 1 }}/>
            <Typography sx={{ fontSize: 10, color: 'var(--text-2)' }}>{LABELS[key]}</Typography>
          </Box>
        ))}
      </Box>

      {/* ── Main chart ── */}
      <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 2, p: 1.5 }}>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={pts} margin={{ top: 10, right: 30, left: 15, bottom: 10 }}>
            <CartesianGrid {...GRID_STYLE}/>
            <XAxis dataKey="theta_elec_deg"
              tickFormatter={v => `${v}°`} tick={AXIS_STYLE}
              label={{ value: 'θ_elec [°]', position: 'insideBottomRight',
                offset: -8, style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
            <YAxis scale={scale} tick={AXIS_STYLE}
              domain={scale === 'log' ? ['auto', 'auto'] : [0, 'auto']}
              tickFormatter={v => v >= 1000 ? `${(v/1000).toFixed(1)}k` : `${v}`}
              label={{ value: 'P [W]', angle: -90, position: 'insideLeft',
                style: { fontSize: 10, fill: 'var(--text-4)' } }}/>
            <RcTooltip {...TOOLTIP_STYLE}
              formatter={(v: number, name: string) => {
                const label = LABELS[name as keyof typeof LABELS] ?? name;
                return [`${v.toFixed(1)} W`, label];
              }}
              labelFormatter={(l: number) => `θ_elec = ${l}°  (${(l/pole_pairs).toFixed(2)}° mech)`}/>

            {/* Cogging reference lines every 30° electrical */}
            {Array.from({ length: 11 }, (_, i) => (i + 1) * 30).map(deg => (
              <ReferenceLine key={deg} x={deg} stroke="#f97316"
                strokeDasharray="2 4" strokeWidth={0.7} strokeOpacity={0.5}/>
            ))}

            {(Object.keys(COLORS) as (keyof typeof COLORS)[]).map(key => (
              visible[key] && (
                <Line key={key} dataKey={key}
                  stroke={COLORS[key]} dot={false}
                  strokeWidth={key === 'P_cu_dc' ? 2 : 1.5}
                  strokeDasharray={key === 'P_cu_ac' || key === 'P_shaft' ? '4 2' : undefined}
                  name={key}/>
              )
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Paper>

      {/* ── Summary table ── */}
      <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 2, p: 1.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <Typography sx={{ fontSize: 10, fontWeight: 700, color: '#3b82f6',
            textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Loss Summary
          </Typography>
          <Tooltip title="Ripple = (max-min)/avg × 100%. Near-zero ripple = symmetric machine.">
            <InfoOutlinedIcon sx={{ fontSize: 12, color: 'var(--line)' }}/>
          </Tooltip>
        </Box>
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr auto auto auto auto', gap: '1px',
          bgcolor: 'var(--panel)', border: '1px solid var(--line-soft)', borderRadius: 1, overflow: 'hidden' }}>
          {/* Header */}
          {['Loss type', 'Avg [W]', 'Min [W]', 'Max [W]', 'Ripple %'].map(h => (
            <Box key={h} sx={{ bgcolor: 'var(--panel-2)', px: 1, py: 0.5 }}>
              <Typography sx={{ fontSize: 9, color: 'var(--text-4)', fontWeight: 700, textTransform: 'uppercase' }}>{h}</Typography>
            </Box>
          ))}
          {/* Rows */}
          {summary.map(({ key, avg, min, max, ripple }) => (
            <React.Fragment key={key}>
              <Box sx={{ bgcolor: 'var(--panel-2)', px: 1, py: 0.4, display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: COLORS[key as keyof typeof COLORS], flexShrink: 0 }}/>
                <Typography sx={{ fontSize: 10, color: 'var(--text-2)' }}>{LABELS[key as keyof typeof LABELS]}</Typography>
              </Box>
              <Box sx={{ bgcolor: 'var(--panel-2)', px: 1, py: 0.4, textAlign: 'right' }}>
                <Typography sx={{ fontSize: 10, fontWeight: 700, color: COLORS[key as keyof typeof COLORS] }}>
                  {avg >= 1000 ? `${(avg/1000).toFixed(2)}k` : avg.toFixed(1)}
                </Typography>
              </Box>
              <Box sx={{ bgcolor: 'var(--panel-2)', px: 1, py: 0.4, textAlign: 'right' }}>
                <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{min.toFixed(1)}</Typography>
              </Box>
              <Box sx={{ bgcolor: 'var(--panel-2)', px: 1, py: 0.4, textAlign: 'right' }}>
                <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{max.toFixed(1)}</Typography>
              </Box>
              <Box sx={{ bgcolor: 'var(--panel-2)', px: 1, py: 0.4, textAlign: 'right' }}>
                <Typography sx={{ fontSize: 10, color: ripple > 5 ? '#fbbf24' : '#4ade80' }}>
                  {ripple.toFixed(2)}%
                </Typography>
              </Box>
            </React.Fragment>
          ))}
          {/* Total */}
          <Box sx={{ bgcolor: 'var(--panel-2)', px: 1, py: 0.5, borderTop: '1px solid var(--line-soft)' }}>
            <Typography sx={{ fontSize: 10, color: 'var(--text-0)', fontWeight: 700 }}>TOTAL</Typography>
          </Box>
          <Box sx={{ bgcolor: 'var(--panel-2)', px: 1, py: 0.5, textAlign: 'right', borderTop: '1px solid var(--line-soft)' }}>
            <Typography sx={{ fontSize: 11, fontWeight: 800, color: '#fbbf24' }}>
              {totalAvg >= 1000 ? `${(totalAvg/1000).toFixed(2)}k` : totalAvg.toFixed(1)}
            </Typography>
          </Box>
          <Box sx={{ bgcolor: 'var(--panel-2)', gridColumn: 'span 3', borderTop: '1px solid var(--line-soft)', px: 1, py: 0.5 }}>
            <Typography sx={{ fontSize: 9, color: 'var(--line)' }}>
              ⚠ Analytical estimates. Fe & Mag use linear B model (no saturation). PINN gives exact values.
            </Typography>
          </Box>
        </Box>

        {/* DC copper detail */}
        <Box sx={{ mt: 1, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
          <Box sx={{ display: 'flex', gap: 0.5 }}>
            <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>R_phase =</Typography>
            <Typography sx={{ fontSize: 10, fontWeight: 700, color: '#fbbf24' }}>
              {constants.R_phase_mOhm} mΩ
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', gap: 0.5 }}>
            <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>d/δ_cu =</Typography>
            <Typography sx={{ fontSize: 10, fontWeight: 700, color: '#fbbf24' }}>
              {constants.d_over_delta_cu} ({constants.regime})
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', gap: 0.5 }}>
            <Typography sx={{ fontSize: 10, color: 'var(--text-3)' }}>DC baseline =</Typography>
            <Typography sx={{ fontSize: 10, fontWeight: 700, color: '#fbbf24' }}>
              {constants.P_cu_dc_W} W
            </Typography>
          </Box>
        </Box>
      </Paper>

    </Box>
  );
};

export default LossWaveformChart;
