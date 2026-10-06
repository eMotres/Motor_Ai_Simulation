import React, { useMemo, useRef } from 'react';
import { Box, Paper, Typography } from '@mui/material';
import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Tooltip as RcTooltip, XAxis, YAxis,
} from 'recharts';
import type { PropSeries } from '../../lib/configuratorPropeller';
import { propellerCurveData, rpmAtPlotPointer } from '../../lib/configuratorPropellerCurves';
import { nsT } from '../../i18n/nsT';

const tx = nsT('controller');
const MARGIN = { top: 8, right: 10, bottom: 20, left: 4 };
const Y_AXIS_WIDTH = 38;
const CURVE_COLORS = { tested: '#60a5fa', modeled: '#fbbf24', cursor: 'var(--text-2)' };

interface Props {
  series: PropSeries | null;
  rpm: number;
  rpmMin: number;
  rpmMax: number;
  powerEstimated: boolean;
  onRpmChange: (rpm: number) => void;
}

const PropellerCurves: React.FC<Props> = ({ series, rpm, rpmMin, rpmMax, powerEstimated, onRpmChange }) => {
  const pointerBox = useRef<Record<'thrust' | 'power', HTMLDivElement | null>>({ thrust: null, power: null });
  const dragging = useRef(false);
  const curve = useMemo(() => series ? propellerCurveData(series, rpmMin, rpmMax) : null,
    [series, rpmMin, rpmMax]);
  const domain = curve?.domain ?? null;

  const updateFromPointer = (clientX: number, kind: 'thrust' | 'power') => {
    const el = pointerBox.current[kind];
    if (!el || !domain) return;
    const rect = el.getBoundingClientRect();
    const plotLeft = MARGIN.left + Y_AXIS_WIDTH;
    const plotRight = MARGIN.right;
    const plotWidth = rect.width - plotLeft - plotRight;
    const x = clientX - rect.left - plotLeft;
    onRpmChange(rpmAtPlotPointer(x, plotWidth, domain));
  };
  const startDrag = (e: React.PointerEvent<HTMLDivElement>, kind: 'thrust' | 'power') => {
    if (e.button !== 0 || !domain) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const plotLeft = MARGIN.left + Y_AXIS_WIDTH;
    const plotRight = MARGIN.right;
    if (e.clientX - rect.left < plotLeft || e.clientX - rect.left > rect.width - plotRight) return;
    dragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    updateFromPointer(e.clientX, kind);
  };
  const title = tx('configurePropeller.curvesTitle');
  const chart = (kind: 'thrust' | 'power') => {
    const thrust = kind === 'thrust';
    const testedKey = thrust ? 'thrustTested' : 'powerTested';
    const modeledKey = thrust ? 'thrustBeyondTested' : 'powerBeyondTested';
    const unit = thrust ? 'kgf' : 'W';
    const hasData = curve?.points.some((p) => p[thrust ? 'thrustTested' : 'powerTested'] != null
      || p[thrust ? 'thrustBeyondTested' : 'powerBeyondTested'] != null) ?? false;
    const noData = !series
      ? tx('configurePropeller.curvesLoading')
      : !domain || !hasData ? tx('configurePropeller.curvesUnavailable') : null;
    const titleText = tx(thrust ? 'configurePropeller.thrustRpm' : 'configurePropeller.powerRpm')
      + (!thrust && powerEstimated ? ` · ${tx('configurePropeller.estimated')}` : '');
    return (
      <Paper key={kind} variant="outlined" sx={{ flex: '1 1 320px', minWidth: 0, bgcolor: 'var(--panel-2)', borderColor: 'var(--line-soft)', p: 1 }}>
        <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)', mb: 0.25 }}>{titleText}</Typography>
        {noData ? <Typography role="status" sx={{ height: 184, display: 'grid', placeItems: 'center', color: 'var(--text-4)', fontSize: 11 }}>{noData}</Typography> : (
          <Box ref={(el: HTMLDivElement | null) => { pointerBox.current[kind] = el; }} sx={{ height: 184, position: 'relative', touchAction: 'none', cursor: 'crosshair', outlineOffset: 2 }}
            role="group" tabIndex={0} aria-label={tx(thrust ? 'configurePropeller.thrustCursor' : 'configurePropeller.powerCursor')}
            aria-valuetext={tx('configurePropeller.cursorAt', { rpm: Math.round(rpm) })}
            onKeyDown={(e) => {
              if (!domain) return;
              const direction = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? 1
                : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -1 : 0;
              if (!direction) return;
              e.preventDefault();
              onRpmChange(Math.max(domain[0], Math.min(domain[1], rpm + direction * 50)));
            }}
            onPointerDown={(e) => startDrag(e, kind)}
            onPointerMove={(e) => { if (dragging.current && (e.buttons & 1)) updateFromPointer(e.clientX, kind); }}
            onPointerUp={(e) => { dragging.current = false; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId); }}
            onPointerCancel={() => { dragging.current = false; }}
            onLostPointerCapture={() => { dragging.current = false; }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={curve!.points} margin={MARGIN}>
                <CartesianGrid stroke="var(--panel)" strokeDasharray="3 3" />
                <XAxis dataKey="rpm" type="number" domain={domain!} tick={{ fill: 'var(--text-4)', fontSize: 9 }} tickFormatter={(v) => Number(v).toLocaleString()} label={{ value: 'rpm', position: 'insideBottom', offset: -12, fill: 'var(--text-4)', fontSize: 9 }} />
                <YAxis width={Y_AXIS_WIDTH} tick={{ fill: 'var(--text-4)', fontSize: 9 }} tickFormatter={(v) => Number(v).toLocaleString()} label={{ value: unit, angle: -90, position: 'insideLeft', fill: 'var(--text-4)', fontSize: 9 }} />
                <RcTooltip formatter={(value, name) => [value == null ? '—' : `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${unit}`, name]} labelFormatter={(v) => `${Number(v).toLocaleString()} rpm`} />
                <Line type="monotone" dataKey={testedKey} name={tx('configurePropeller.tested')} stroke={CURVE_COLORS.tested} dot={false} connectNulls={false} isAnimationActive={false} strokeWidth={2} />
                <Line type="monotone" dataKey={modeledKey} name={tx('configurePropeller.beyondTested')} stroke={CURVE_COLORS.modeled} strokeDasharray="5 3" dot={false} connectNulls={false} isAnimationActive={false} strokeWidth={2} />
                {rpm >= domain![0] && rpm <= domain![1] && <ReferenceLine x={rpm} stroke={CURVE_COLORS.cursor} strokeWidth={1.5} />}
              </LineChart>
            </ResponsiveContainer>
          </Box>
        )}
      </Paper>
    );
  };

  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography sx={{ fontSize: 12, color: 'var(--text-2)', fontWeight: 800, mb: 0.75 }}>{title}</Typography>
      <Typography sx={{ fontSize: 10, color: 'var(--text-4)', mb: 0.75 }}>{tx('configurePropeller.curvesTip')}</Typography>
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>{chart('thrust')}{chart('power')}</Box>
    </Box>
  );
};

export default PropellerCurves;
