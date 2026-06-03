/**
 * ParetoResults — scatter of all evaluated designs with the non-dominated
 * (Pareto) front highlighted, plus a table of the front designs.  X axis is
 * efficiency, Y axis is torque density (N·m/kg); both are maximized, so the
 * front is the up-right envelope.  The baseline (current motor) is marked.
 */
import React from 'react';
import {
  Box, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  Button, Chip, Tooltip,
} from '@mui/material';
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid,
  Tooltip as RcTooltip, ResponsiveContainer, Legend,
} from 'recharts';
import type { OptimizationResult, OptDesignPoint } from '../../types/motor';
import { useMotorStore } from '../../stores/motorStore';

const OPERATING_KEYS = new Set(['gamma_deg', 'current_a', 'rpm']);

interface Pt { x: number; y: number; d: OptDesignPoint; }

const fmt = (v: number, n = 2) => (Number.isFinite(v) ? v.toFixed(n) : '–');

const ParetoTooltip: React.FC<any> = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const d: OptDesignPoint = payload[0].payload.d;
  const ov = Object.entries(d.overrides || {});
  return (
    <Box sx={{ bgcolor: '#0f172a', border: '1px solid #1e293b', p: 1, fontSize: 11, color: '#cbd5e1', borderRadius: 1 }}>
      <div><b>η = {(d.efficiency * 100).toFixed(2)} %</b> · T/mass = {fmt(d.torque_per_mass_Nm_kg)} N·m/kg</div>
      <div>T = {fmt(d.T_em_Nm)} N·m · mass = {fmt(d.mass_total_kg)} kg</div>
      <div style={{ color: '#94a3b8' }}>
        loss {fmt(d.P_loss_total_W, 0)} W (Cu {fmt(d.P_cu_W, 0)} / Fe {fmt(d.P_fe_W, 0)} / Mg {fmt(d.P_mag_W, 0)})
      </div>
      {ov.length > 0 && (
        <div style={{ marginTop: 4, color: '#fbbf24' }}>
          {ov.map(([k, v]) => `${k}=${(v as number).toFixed(2)}`).join('  ')}
        </div>
      )}
    </Box>
  );
};

const ParetoResults: React.FC<{ result: OptimizationResult }> = ({ result }) => {
  const updateGeometryViaApi = useMotorStore(s => s.updateGeometryViaApi);
  const updateOperatingPoint = useMotorStore(s => s.updateOperatingPoint);

  const frontSet = new Set(result.pareto_indices);
  const cloudAll: Pt[] = [];
  const front: Pt[] = [];
  result.points.forEach((d, i) => {
    if (!d.feasible) return;
    const pt: Pt = { x: d.efficiency * 100, y: d.torque_per_mass_Nm_kg, d };
    (frontSet.has(i) ? front : cloudAll).push(pt);
  });
  front.sort((a, b) => a.x - b.x);
  // Downsample the background cloud — rendering thousands of SVG dots makes the
  // chart sluggish; ~600 is plenty to show the feasible design space.
  const MAX_CLOUD = 600;
  const stride = Math.max(1, Math.ceil(cloudAll.length / MAX_CLOUD));
  const cloud = stride > 1 ? cloudAll.filter((_, i) => i % stride === 0) : cloudAll;
  const base = result.baseline;
  const basePt: Pt[] = base?.feasible
    ? [{ x: base.efficiency * 100, y: base.torque_per_mass_Nm_kg, d: base }]
    : [];

  const varNames = result.variables.map(v => v.name);

  const applyDesign = (d: OptDesignPoint) => {
    const geoOverrides: Record<string, number> = {};
    Object.entries(d.overrides || {}).forEach(([k, v]) => {
      if (!OPERATING_KEYS.has(k)) geoOverrides[k] = v as number;
    });
    if (Object.keys(geoOverrides).length) {
      updateGeometryViaApi(geoOverrides as any);
    }
    // operating overrides → push into operating point 1 so Simulation can use it
    const op: any = {};
    if ('current_a' in (d.overrides || {})) op.current_a = d.overrides.current_a;
    if ('rpm' in (d.overrides || {})) op.rpm = d.overrides.rpm;
    if (Object.keys(op).length) updateOperatingPoint(0, op);
  };

  return (
    <Box sx={{ mt: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <Typography variant="overline" sx={{ fontSize: 10, letterSpacing: 1, color: 'text.secondary' }}>
          Pareto Front — Torque density vs Efficiency
        </Typography>
        <Chip size="small" label={`${result.n_feasible}/${result.n_total} feasible`} variant="outlined" sx={{ height: 18, fontSize: 10 }} />
        <Chip size="small" color="warning" label={`${result.pareto_indices.length} on front`} sx={{ height: 18, fontSize: 10 }} />
        <Tooltip title="Analytical surrogate calibrated to the validated sliding-band FEM at the baseline. Use it to narrow the design space; confirm a chosen point in the Simulation tab." placement="top">
          <span style={{ color: '#475569', fontSize: 11, cursor: 'help' }}>ⓘ</span>
        </Tooltip>
      </Box>

      <Box sx={{ height: 320, bgcolor: 'rgba(255,255,255,0.02)', borderRadius: 1, p: 1 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="2 4" />
            <XAxis type="number" dataKey="x" name="Efficiency" unit="%"
              domain={['dataMin - 0.3', 'dataMax + 0.3']}
              tickFormatter={(v: number) => v.toFixed(1)}
              tick={{ fontSize: 10, fill: '#94a3b8' }}
              label={{ value: 'Efficiency [%]', position: 'insideBottom', offset: -12, style: { fontSize: 10, fill: '#64748b' } }} />
            <YAxis type="number" dataKey="y" name="Torque density"
              domain={['dataMin - 0.2', 'dataMax + 0.2']}
              tickFormatter={(v: number) => v.toFixed(1)}
              tick={{ fontSize: 10, fill: '#94a3b8' }}
              label={{ value: 'Torque/mass [N·m/kg]', angle: -90, position: 'insideLeft', style: { fontSize: 10, fill: '#64748b' } }} />
            <ZAxis range={[24, 24]} />
            <RcTooltip content={<ParetoTooltip />} cursor={{ strokeDasharray: '3 3' }} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <Scatter name="evaluated" data={cloud} fill="#334155" fillOpacity={0.5} />
            <Scatter name="Pareto front" data={front} fill="#f59e0b" line={{ stroke: '#f59e0b', strokeWidth: 1 }} shape="circle" />
            <Scatter name="baseline" data={basePt} fill="#3b82f6" shape="diamond" />
          </ScatterChart>
        </ResponsiveContainer>
      </Box>

      {/* Front designs table */}
      <Box sx={{ mt: 2, overflowX: 'auto' }}>
        <Table size="small" sx={{ '& td, & th': { fontSize: 11, py: 0.5, borderColor: '#1e293b' } }}>
          <TableHead>
            <TableRow>
              <TableCell>η&nbsp;%</TableCell>
              <TableCell align="right">T/mass</TableCell>
              <TableCell align="right">T&nbsp;[N·m]</TableCell>
              <TableCell align="right">mass&nbsp;[kg]</TableCell>
              <TableCell align="right">loss&nbsp;[W]</TableCell>
              {varNames.map(n => <TableCell key={n} align="right">{n}</TableCell>)}
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {front.map((p, i) => (
              <TableRow key={i} hover>
                <TableCell sx={{ color: '#fbbf24', fontWeight: 700 }}>{(p.d.efficiency * 100).toFixed(2)}</TableCell>
                <TableCell align="right">{fmt(p.d.torque_per_mass_Nm_kg)}</TableCell>
                <TableCell align="right">{fmt(p.d.T_em_Nm, 1)}</TableCell>
                <TableCell align="right">{fmt(p.d.mass_total_kg)}</TableCell>
                <TableCell align="right">{fmt(p.d.P_loss_total_W, 0)}</TableCell>
                {varNames.map(n => (
                  <TableCell key={n} align="right">
                    {p.d.overrides && n in p.d.overrides ? (p.d.overrides[n] as number).toFixed(2) : '–'}
                  </TableCell>
                ))}
                <TableCell align="right">
                  <Button size="small" variant="outlined" sx={{ fontSize: 9, py: 0, px: 0.75, minWidth: 0 }}
                    onClick={() => applyDesign(p.d)}>Apply</Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 1 }}>
        Diamond = baseline (current motor). <b>Apply</b> writes a front design's geometry to the model — confirm it in the Simulation tab (full FEM).
      </Typography>
    </Box>
  );
};

export default ParetoResults;
