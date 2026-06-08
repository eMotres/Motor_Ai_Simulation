/**
 * Model Comparison — runs the three torque models (analytical Green's-function
 * torque_sweep, FEM field2d single-angle, FEM sliding-band transient) across a
 * γ sweep and plots them together. Makes the inter-model discrepancy and the
 * transient's γ-independence visible in the UI instead of only in the terminal.
 *
 * Pure frontend: it just orchestrates the existing endpoints and charts the
 * result, streaming each γ point in as it lands so the process is watchable.
 */
import React, { useState } from 'react';
import {
  Box, Paper, Typography, Button, Chip, CircularProgress, Checkbox,
  FormControlLabel, Table, TableBody, TableCell, TableHead, TableRow, Tooltip,
} from '@mui/material';
import ScienceIcon from '@mui/icons-material/Science';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip as RcTooltip, Legend, ReferenceLine,
} from 'recharts';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';
const GAMMAS = [0, -5, -10, -15, -20];

const C = { an: '#a78bfa', f2: '#38bdf8', tr: '#4ade80', bg: '#060d17', border: '#1e293b', axis: '#475569' };

interface Row {
  gamma: number;
  analytical: number | null; an_ripple: number | null;
  field2d: number | null;
  transient: number | null; tr_ripple: number | null;
}

const jget = (url: string) => fetch(url).then((r) => r.json());

const ModelCompare: React.FC<{ I_phase_rms?: number }> = ({ I_phase_rms = 168 }) => {
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [prog, setProg] = useState('');
  const [withTransient, setWithTransient] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setBusy(true); setErr(null); setRows([]);
    const out: Row[] = [];
    try {
      for (const g of GAMMAS) {
        setProg(`analytical + FEM field2d @ γ=${g}°`);
        const [an, fe] = await Promise.all([
          jget(`${API}/api/simulation/physics/torque_sweep?gamma_deg=${g}&n_rotor=24&n_ag=360`),
          jget(`${API}/api/simulation/physics/fem_field2d?rotor_angle_deg=0&gamma_deg=${g}&I_phase_rms=${I_phase_rms}`),
        ]);
        let tr: number | null = null, trr: number | null = null;
        if (withTransient) {
          setProg(`FEM transient @ γ=${g}° (slow)…`);
          const t = await jget(
            `${API}/api/simulation/physics/fem_transient?n_steps_per_period=12&n_periods=1`
            + `&gamma_deg=${g}&I_phase_rms=${I_phase_rms}&mesh_size_mm=4&min_size_mm=0.3`
            + `&n_sectors=4&sliding_band=true&motion_band=true&band_thickness_mm=0.4`
            + `&outer_air_factor=1.3&coil_temp_c=120&end_winding_factor=1.5`
            + `&include_frames=false&fresh=false&run_id=7${Math.abs(g)}`);
          tr = typeof t.T_avg_Nm === 'number' ? t.T_avg_Nm : null;
          trr = typeof t.T_ripple_pct === 'number' ? t.T_ripple_pct : null;
        }
        out.push({
          gamma: g,
          analytical: an?.T_avg_Nm ?? null, an_ripple: an?.T_ripple_pct ?? null,
          field2d: typeof fe?.T_em_Nm === 'number' ? fe.T_em_Nm : null,
          transient: tr, tr_ripple: trr,
        });
        setRows([...out]);
      }
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); setProg(''); }
  };

  const chartData = rows.map((r) => ({
    gamma: r.gamma,
    analytical: r.analytical != null ? +r.analytical.toFixed(1) : null,
    field2d: r.field2d != null ? +r.field2d.toFixed(1) : null,
    transient: r.transient != null ? +r.transient.toFixed(1) : null,
  }));

  return (
    <Paper sx={{ bgcolor: C.bg, border: `1px solid ${C.border}`, borderRadius: 2, p: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1, flexWrap: 'wrap' }}>
        <ScienceIcon sx={{ fontSize: 18, color: '#a78bfa' }} />
        <Typography sx={{ fontWeight: 700, color: '#e2e8f0', fontSize: '0.95rem' }}>
          Model Comparison — torque vs load angle γ
        </Typography>
        <Chip label="diagnostics" size="small" sx={{ fontSize: 9, height: 16, bgcolor: '#1e293b', color: '#a78bfa' }} />
        <Box sx={{ flex: 1 }} />
        <FormControlLabel
          control={<Checkbox size="small" checked={withTransient} disabled={busy}
            onChange={(e) => setWithTransient(e.target.checked)} sx={{ color: '#475569', py: 0 }} />}
          label={<Typography sx={{ fontSize: '0.72rem', color: '#94a3b8' }}>+ FEM transient (slow)</Typography>}
        />
        <Button size="small" variant="contained" onClick={run} disabled={busy}
          startIcon={busy ? <CircularProgress size={12} /> : <ScienceIcon sx={{ fontSize: 15 }} />}
          sx={{ textTransform: 'none', fontSize: '0.75rem' }}>
          {busy ? 'Running…' : 'Compare models'}
        </Button>
      </Box>

      <Typography sx={{ fontSize: '0.72rem', color: '#64748b', mb: 1 }}>
        Same geometry &amp; current ({I_phase_rms} A). If the curves don't overlap, the models disagree;
        a flat transient line means γ isn't reaching the transient currents.
      </Typography>

      {busy && prog && (
        <Typography sx={{ fontSize: '0.72rem', color: '#38bdf8', mb: 1 }}>● {prog}</Typography>
      )}
      {err && <Typography sx={{ fontSize: '0.72rem', color: '#f87171', mb: 1 }}>{err}</Typography>}

      {chartData.length > 0 && (
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={chartData} margin={{ top: 8, right: 20, left: 6, bottom: 6 }}>
            <CartesianGrid stroke={C.border} strokeDasharray="3 3" />
            <XAxis dataKey="gamma" tick={{ fill: C.axis, fontSize: 10 }}
              label={{ value: 'γ [°]', position: 'insideBottomRight', offset: -4, style: { fontSize: 10, fill: C.axis } }} />
            <YAxis tick={{ fill: C.axis, fontSize: 10 }}
              label={{ value: 'T_avg [N·m]', angle: -90, position: 'insideLeft', style: { fontSize: 10, fill: C.axis } }} />
            <RcTooltip contentStyle={{ backgroundColor: '#1e293b', border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 11 }} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <ReferenceLine y={96} stroke="#f59e0b" strokeDasharray="4 3"
              label={{ value: 'rated 96', fill: '#f59e0b', fontSize: 9, position: 'right' }} />
            <Line dataKey="analytical" name="analytical (Green)" stroke={C.an} strokeWidth={2} dot={{ r: 3 }} connectNulls />
            <Line dataKey="field2d" name="FEM field2d" stroke={C.f2} strokeWidth={2} dot={{ r: 3 }} connectNulls />
            {withTransient && <Line dataKey="transient" name="FEM transient" stroke={C.tr} strokeWidth={2} dot={{ r: 3 }} connectNulls />}
          </LineChart>
        </ResponsiveContainer>
      )}

      {rows.length > 0 && (
        <Table size="small" sx={{
          mt: 1,
          '& td, & th': { borderColor: C.border, py: 0.4, fontSize: '0.74rem', color: '#cbd5e1' },
          '& th': { color: '#64748b', fontWeight: 600, fontSize: '0.62rem', textTransform: 'uppercase' },
        }}>
          <TableHead>
            <TableRow>
              <TableCell>γ [°]</TableCell>
              <TableCell align="center">analytical T / ripple</TableCell>
              <TableCell align="center">field2d T</TableCell>
              {withTransient && <TableCell align="center">transient T / ripple</TableCell>}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.gamma}>
                <TableCell>{r.gamma}</TableCell>
                <TableCell align="center" sx={{ color: C.an }}>
                  {r.analytical?.toFixed(1) ?? '—'} N·m / {r.an_ripple?.toFixed(2) ?? '—'}%
                </TableCell>
                <TableCell align="center" sx={{ color: C.f2 }}>{r.field2d?.toFixed(1) ?? '—'} N·m</TableCell>
                {withTransient && (
                  <TableCell align="center" sx={{ color: C.tr }}>
                    {r.transient != null ? `${r.transient.toFixed(1)} N·m / ${r.tr_ripple?.toFixed(2) ?? '—'}%` : '—'}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Paper>
  );
};

export default ModelCompare;
