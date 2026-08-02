/**
 * BHCurveChart — magnet demagnetisation curve with per-magnet operating
 * points overlaid.
 *
 * The black line is the measured B-H demag curve of the assigned magnet
 * grade (read from materials_library.yaml).  Coloured dots = each
 * magnet's WORST cell (most-demagnetised, most-negative H projected
 * along +M̂) at the FEM-solved operating point.  Dots that fall BELOW
 * the knee (where B crosses zero on the recoil line) are flagged in red
 * — those magnets have crossed into irreversible demagnetisation.
 */
import React, { useMemo } from 'react';
import { Box, Paper, Typography } from '@mui/material';
import HelpTip from '../common/HelpTip';
import {
  ResponsiveContainer, ComposedChart, Line, Scatter, XAxis, YAxis,
  CartesianGrid, ReferenceLine, Tooltip, Cell,
} from 'recharts';

interface BHPoint { H_kA_per_m: number; B_T: number; }
interface OpPoint {
  magnet_index: number;
  H_op_kA_per_m: number;
  H_mean_kA_per_m: number;
  B_op_T: number;
}
interface DemagEntry {
  magnet_index: number;
  knee_proximity: number;
  demagnetised: boolean;
}

interface Props {
  bh_curve_magnet:  BHPoint[];
  magnet_op_points: OpPoint[];
  demag_report:     DemagEntry[];
}

const BHCurveChart: React.FC<Props> = ({
  bh_curve_magnet, magnet_op_points, demag_report,
}) => {
  // Look up demag status per magnet index
  const demagByIdx = useMemo(() => {
    const m = new Map<number, DemagEntry>();
    for (const r of demag_report ?? []) m.set(r.magnet_index, r);
    return m;
  }, [demag_report]);

  // Knee H = where B first crosses zero in the curve.
  const H_knee = useMemo(() => {
    if (!bh_curve_magnet || bh_curve_magnet.length < 2) return null;
    for (let i = 0; i < bh_curve_magnet.length - 1; i++) {
      const a = bh_curve_magnet[i], b = bh_curve_magnet[i + 1];
      if (a.B_T <= 0 && b.B_T > 0) {
        const f = -a.B_T / (b.B_T - a.B_T);
        return a.H_kA_per_m + f * (b.H_kA_per_m - a.H_kA_per_m);
      }
      if (a.B_T >= 0 && b.B_T < 0) {
        const f = a.B_T / (a.B_T - b.B_T);
        return a.H_kA_per_m + f * (b.H_kA_per_m - a.H_kA_per_m);
      }
    }
    // Otherwise just the H at the most-negative B
    let minB = +Infinity, hKnee = bh_curve_magnet[0].H_kA_per_m;
    for (const p of bh_curve_magnet) {
      if (p.B_T < minB) { minB = p.B_T; hKnee = p.H_kA_per_m; }
    }
    return hKnee;
  }, [bh_curve_magnet]);

  // Build merged data array for ComposedChart (curve sampled + op points).
  // Recharts has trouble overlaying Line and Scatter with different X axes,
  // so we render them as two series on a single shared <XAxis>.
  const curveData = bh_curve_magnet ?? [];
  const opData = (magnet_op_points ?? []).map(p => ({
    H_kA_per_m: p.H_op_kA_per_m,
    B_op:       p.B_op_T,
    magnet_index: p.magnet_index,
  }));

  const has_demag = (demag_report ?? []).some(r => r.demagnetised);
  const has_near  = (demag_report ?? []).some(r => !r.demagnetised);

  if (!curveData.length) return null;

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', p: 2,
      display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
        <Typography sx={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
          Magnet B–H demagnetisation curve · per-magnet operating points
        </Typography>
        <HelpTip title={"Black line: measured BH curve of the magnet grade assigned in the Materials tab. "
          + "Each dot = one magnet's worst-cell operating point (H projected on +M̂, B along the same axis). "
          + 'Dots below the knee are in irreversible demag.'} />
      </Box>
      <Box sx={{ height: 320 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart margin={{ top: 8, right: 10, left: 0, bottom: 26 }}>
            <CartesianGrid stroke="var(--panel)" strokeDasharray="2 4"/>
            <XAxis
              type="number"
              dataKey="H_kA_per_m"
              tick={{ fontSize: 10, fill: 'var(--text-2)' }}
              label={{ value: 'H [kA/m]', position: 'insideBottom',
                offset: -8, style: { fontSize: 10, fill: 'var(--text-4)' } }}
              domain={['dataMin', 'dataMax']}
            />
            <YAxis
              type="number"
              dataKey="B_T"
              tick={{ fontSize: 10, fill: 'var(--text-2)' }}
              label={{ value: 'B [T]', angle: -90, position: 'insideLeft',
                offset: 12, style: { fontSize: 10, fill: 'var(--text-4)' } }}
              domain={[-1.2, 1.5]}
            />
            <Tooltip
              contentStyle={{ background: 'var(--app-bg)', border: '1px solid var(--line-soft)',
                fontSize: 11, color: 'var(--text-1)' }}
              labelFormatter={(v) => `H = ${(v as number).toFixed(0)} kA/m`}
            />
            <ReferenceLine y={0} stroke="var(--text-4)" strokeDasharray="3 3"/>
            <ReferenceLine x={0} stroke="var(--text-4)" strokeDasharray="3 3"/>
            {H_knee != null && (
              <ReferenceLine x={H_knee} stroke="#fbbf24" strokeDasharray="2 4"
                label={{ value: `knee ${H_knee.toFixed(0)} kA/m`,
                  position: 'top', style: { fontSize: 9, fill: '#fbbf24' } }}/>
            )}
            <Line
              data={curveData}
              dataKey="B_T"
              type="monotone"
              stroke="var(--text-1)"
              strokeWidth={1.25}
              dot={{ r: 0.5, fill: 'var(--text-3)' }}
              isAnimationActive={false}
              name="BH curve"
            />
            <Scatter
              data={opData}
              dataKey="B_op"
              isAnimationActive={false}
              name="Magnets"
            >
              {opData.map((p, i) => {
                const d = demagByIdx.get(p.magnet_index);
                const colour = d?.demagnetised ? '#ef4444'
                              : d ? '#fbbf24' : '#10b981';
                return <Cell key={i} fill={colour} stroke="var(--panel-2)"/>;
              })}
            </Scatter>
          </ComposedChart>
        </ResponsiveContainer>
      </Box>
      <Box sx={{ display: 'flex', gap: 2, fontSize: 10, color: 'var(--text-3)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box sx={{ width: 10, height: 10, bgcolor: '#10b981', borderRadius: '50%' }}/>
          safe
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box sx={{ width: 10, height: 10, bgcolor: '#fbbf24', borderRadius: '50%' }}/>
          near knee
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box sx={{ width: 10, height: 10, bgcolor: '#ef4444', borderRadius: '50%' }}/>
          demagnetised
        </Box>
        <Box sx={{ ml: 'auto', fontFamily: 'monospace', color: 'var(--text-2)' }}>
          {opData.length} magnets · {(demag_report ?? []).length} flagged
          {has_demag && ' · ⛔ irreversible loss'}
          {!has_demag && has_near && ' · ⚠ near-knee'}
        </Box>
      </Box>
    </Paper>
  );
};

export default BHCurveChart;
