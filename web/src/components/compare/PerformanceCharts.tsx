/**
 * PerformanceCharts — motor behaviour across the speed range, for the Configurator.
 *
 * Sweeps the analytical passport model (scaleMotor) over rpm at the current knobs
 * and plots power + efficiency vs speed (with the battery-voltage-limited top
 * speed marked) and the loss breakdown (copper / iron / magnet) vs speed.
 *
 * NOTE: losses here use the analytical model (copper I²R, iron ~f^1.5, magnet
 * ~f²).  The FEM speed-sweep curves (see docs/roadmap) will replace those with
 * measured curves once a reference passport carries them.
 */
import React, { useMemo } from 'react';
import { Box, Typography, Paper } from '@mui/material';
import {
  ResponsiveContainer, ComposedChart, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip as RcTooltip, Legend, ReferenceLine,
} from 'recharts';
import { scaleMotor, type Passport, type Knobs } from '../../lib/motorScaling';
import EfficiencyMap from './EfficiencyMap';
import { nsT } from '../../i18n/nsT';

const tx = nsT('controller');   // EN source, ZH mirror (docs/I18N.md)

const SQRT3 = Math.sqrt(3);
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, p: 2 } as const;
const LABEL = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const CARD = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, px: 1.5, py: 1, flex: 1, minWidth: 120, textAlign: 'center' } as const;
const TT = { contentStyle: { backgroundColor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 6, fontSize: 11 }, labelStyle: { color: 'var(--text-2)' } };
const AX = { stroke: 'var(--text-4)', fontSize: 10 } as const;

const PerformanceCharts: React.FC<{ p: Passport; knobs: Knobs; packMin: number; packMax: number }> = ({ p, knobs, packMin, packMax }) => {
  const { data, maxSpeed, peakEff, peakEffRpm, iBatOp, vdcOp } = useMemo(() => {
    const RPM_MAX = 8000, STEP = 200;
    const data: { rpm: number; kW: number; eff: number; cu: number; fe: number; mag: number; vdc: number; ibat: number }[] = [];
    let maxSpeed: number | null = null;
    let peakEff = 0, peakEffRpm = 0;
    for (let r = 0; r <= RPM_MAX; r += STEP) {
      const s = scaleMotor(p, { ...knobs, rpm: r });
      const vdc = s.Vphase_peak_V * SQRT3;
      const pin = s.P_mech_W + s.P_loss_W;
      const row = { rpm: r, kW: s.P_mech_W / 1000, eff: s.efficiency * 100, cu: s.P_cu_W, fe: s.P_fe_W, mag: s.P_mag_W, vdc, ibat: vdc > 1 ? pin / vdc : 0 };
      data.push(row);
      if (maxSpeed === null && vdc > packMax) {
        const prev = data[data.length - 2];
        maxSpeed = prev ? Math.max(0, prev.rpm + ((packMax - prev.vdc) / (vdc - prev.vdc)) * STEP) : r;
      }
      if (r > 0 && row.eff > peakEff) { peakEff = row.eff; peakEffRpm = r; }
    }
    const sOp = scaleMotor(p, knobs);
    const vdcOp = sOp.Vphase_peak_V * SQRT3;
    const iBatOp = vdcOp > 1 ? (sOp.P_mech_W + sOp.P_loss_W) / vdcOp : 0;
    return { data, maxSpeed, peakEff, peakEffRpm, iBatOp, vdcOp };
  }, [p, knobs, packMax]);

  const battOk = vdcOp <= packMax;

  return (
    <Box sx={PANEL}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 13, fontWeight: 800, color: 'var(--text-0)' }} title={tx('configure.perfSub')}>{tx('configure.perfTitle')}</Typography>
      </Box>

      {/* summary */}
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 1.5 }}>
        <Box sx={CARD}>
          <Typography sx={{ fontSize: 9, color: 'var(--text-3)' }}>{tx('configure.maxSpeedBattery')}</Typography>
          <Typography sx={{ fontSize: 16, fontWeight: 800, color: maxSpeed === null ? '#4ade80' : '#fbbf24' }}>
            {maxSpeed === null ? '> 8000' : maxSpeed.toFixed(0)}
          </Typography>
          <Typography sx={{ fontSize: 9, color: 'var(--text-4)' }}>{tx('configure.rpmAtV', { v: packMax.toFixed(0) })}</Typography>
        </Box>
        <Box sx={CARD}>
          <Typography sx={{ fontSize: 9, color: 'var(--text-3)' }}>{tx('configure.battCurrentNow')}</Typography>
          <Typography sx={{ fontSize: 16, fontWeight: 800, color: battOk ? '#60a5fa' : '#f87171' }}>{iBatOp.toFixed(0)}</Typography>
          <Typography sx={{ fontSize: 9, color: 'var(--text-4)' }}>{tx('configure.ampsFromV', { min: packMin.toFixed(0), max: packMax.toFixed(0) })}</Typography>
        </Box>
        <Box sx={CARD}>
          <Typography sx={{ fontSize: 9, color: 'var(--text-3)' }}>{tx('configure.peakEfficiency')}</Typography>
          <Typography sx={{ fontSize: 16, fontWeight: 800, color: '#4ade80' }}>{peakEff.toFixed(1)}%</Typography>
          <Typography sx={{ fontSize: 9, color: 'var(--text-4)' }}>{tx('configure.atRpm', { rpm: peakEffRpm })}</Typography>
        </Box>
      </Box>

      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
        {/* power + efficiency */}
        <Paper sx={{ flex: '1 1 360px', minWidth: 320, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1 }}>
          <Typography sx={{ ...LABEL, mb: 0.5 }}>{tx('configure.powerEffTitle')}</Typography>
          <ResponsiveContainer width="100%" height={230}>
            <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid stroke="var(--panel)" strokeDasharray="3 3" />
              <XAxis dataKey="rpm" tick={AX} tickFormatter={(v) => `${v / 1000}k`} />
              <YAxis yAxisId="kW" tick={AX} tickFormatter={(v) => `${v.toFixed(0)}`} label={{ value: 'kW', angle: -90, position: 'insideLeft', style: { fontSize: 10, fill: '#a78bfa' } }} />
              <YAxis yAxisId="eff" orientation="right" domain={[0, 100]} tick={AX} tickFormatter={(v) => `${v}`} label={{ value: '%', angle: 90, position: 'insideRight', style: { fontSize: 10, fill: '#4ade80' } }} />
              <RcTooltip {...TT} formatter={(v: number, _n: string, item: { dataKey?: unknown }) => [item?.dataKey === 'eff' ? `${v.toFixed(1)} %` : `${v.toFixed(2)} kW`, item?.dataKey === 'eff' ? tx('configure.seriesEff') : tx('configure.seriesPower')]} labelFormatter={(l: number) => `${l} rpm`} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              {maxSpeed !== null && <ReferenceLine yAxisId="kW" x={Math.round(maxSpeed / 200) * 200} stroke="#f87171" strokeDasharray="5 3" label={{ value: tx('configure.batteryLimit'), fill: '#f87171', fontSize: 9, position: 'insideTopRight' }} />}
              <Line yAxisId="kW" dataKey="kW" name={tx('configure.seriesPower')} stroke="#a78bfa" dot={false} strokeWidth={1.25} />
              <Line yAxisId="eff" dataKey="eff" name={tx('configure.seriesEff')} stroke="#4ade80" dot={false} strokeWidth={1.25} />
            </ComposedChart>
          </ResponsiveContainer>
        </Paper>

        {/* losses */}
        <Paper sx={{ flex: '1 1 360px', minWidth: 320, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1 }}>
          <Typography sx={{ ...LABEL, mb: 0.5 }}>{tx('configure.lossesTitle')}</Typography>
          <ResponsiveContainer width="100%" height={230}>
            <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid stroke="var(--panel)" strokeDasharray="3 3" />
              <XAxis dataKey="rpm" tick={AX} tickFormatter={(v) => `${v / 1000}k`} />
              <YAxis tick={AX} tickFormatter={(v) => `${v.toFixed(0)}`} />
              <RcTooltip {...TT} formatter={(v: number, n: string) => [`${v.toFixed(0)} W`, n]} labelFormatter={(l: number) => `${l} rpm`} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              {maxSpeed !== null && <ReferenceLine x={Math.round(maxSpeed / 200) * 200} stroke="#f87171" strokeDasharray="5 3" />}
              <Line dataKey="cu" name={tx('configure.seriesCopper')} stroke="#f59e0b" dot={false} strokeWidth={1.25} />
              <Line dataKey="fe" name={tx('configure.seriesIron')} stroke="#60a5fa" dot={false} strokeWidth={1.25} />
              <Line dataKey="mag" name={tx('configure.seriesMagnet')} stroke="#ef4444" dot={false} strokeWidth={1.25} />
            </LineChart>
          </ResponsiveContainer>
        </Paper>
      </Box>

      <EfficiencyMap p={p} knobs={knobs} packMax={packMax} />
    </Box>
  );
};

export default PerformanceCharts;
