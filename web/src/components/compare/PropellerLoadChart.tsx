/**
 * Load chart (owner 2026-10-18, after the X13-18S datasheet chart): speed [rpm] on x and ONE parameter on y, switched
 * with the chips above it (thrust, torque, battery / phase current, efficiency, g/W, shaft / battery power,
 * winding / magnet temperature), one line per propeller — the propeller Configure has selected plus any other this
 * motor may drive.  Each point is a steady continuous operating point from the backend's propeller S1 rows; the
 * part above the continuous limit is dashed.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Chip, Paper, Tooltip, Typography } from '@mui/material';
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip as RcTooltip, XAxis, YAxis } from 'recharts';
import type { PropSummary } from '../../lib/configuratorPropeller';
import { modelLabel, vendorLabel } from '../../lib/configuratorPropeller';
import { fetchPropellerS1 } from '../../lib/propellerApi';
import { LOAD_COLORS, LOAD_METRICS, METRIC_UNIT, loadLine, type LoadMetric, type S1ChartRow } from '../../lib/propellerLoadChart';
import { nsT } from '../../i18n/nsT';

const tx = nsT('controller');
const MAX_PROPS = LOAD_COLORS.length;

interface Props {
  die: string;
  config: string;
  ambient: number;
  drive: string;
  pack: { min: number; max: number } | null;
  escBusA: number | null;
  allowed: PropSummary[];
  selectedId: string | null;
  /** the speed Configure is set to (a vertical line) and the continuous limit (green dashed); a click sets the speed */
  rpm?: number;
  s1Rpm?: number | null;
  onRpmChange?: (rpm: number) => void;
}

const propName = (p: PropSummary | undefined, id: string) =>
  p ? `${vendorLabel(p.vendor)} ${modelLabel(p.model)}` : id;

const PropellerLoadChart: React.FC<Props> = ({ die, config, ambient, drive, pack, escBusA, allowed, selectedId, rpm, s1Rpm, onRpmChange }) => {
  const [metric, setMetric] = useState<LoadMetric>('thrust');
  const [extra, setExtra] = useState<string[]>([]);
  const [rows, setRows] = useState<Record<string, S1ChartRow[] | null>>({});
  const ids = useMemo(() => {
    const out: string[] = [];
    for (const id of [selectedId, ...extra]) if (id && !out.includes(id) && allowed.some((p) => p.id === id)) out.push(id);
    return out.slice(0, MAX_PROPS);
  }, [selectedId, extra, allowed]);
  const packMin = pack?.min ?? null;
  const packMax = pack?.max ?? null;

  useEffect(() => {
    let dead = false;
    const pk = packMin != null && packMax != null ? { min: packMin, max: packMax } : null;
    for (const id of ids) {
      void fetchPropellerS1(id, die, config, ambient, drive, pk, escBusA).then((r) => {
        if (dead) return;
        const rr = (r as unknown as { rows?: S1ChartRow[] } | null)?.rows ?? null;
        setRows((cur) => ({ ...cur, [id]: rr }));
      });
    }
    return () => { dead = true; };
  }, [ids, die, config, ambient, drive, packMin, packMax, escBusA]);

  const lines = ids.map((id, i) => ({ id, color: LOAD_COLORS[i], name: propName(allowed.find((p) => p.id === id), id),
    ...loadLine(rows[id], metric) }));
  const anyData = lines.some((l) => l.ok.length || l.over.length);
  const unit = METRIC_UNIT[metric];
  const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2));
  const toggle = (id: string) => setExtra((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  return (
    <Paper variant="outlined" sx={{ p: 1.5, bgcolor: 'var(--panel-2)' }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 0.75, flexWrap: 'wrap' }}>
        <Tooltip title={tx('propLoad.tip')}>
          <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>{tx('propLoad.title')}</Typography>
        </Tooltip>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{tx('propLoad.subtitle', { ambient: ambient.toFixed(0) })}</Typography>
      </Box>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: 1 }}>
        {LOAD_METRICS.map((m) => (
          <Chip key={m} size="small" label={tx(`propLoad.m.${m}`)} onClick={() => setMetric(m)}
            color={m === metric ? 'primary' : 'default'} variant={m === metric ? 'filled' : 'outlined'}
            sx={{ fontSize: 11, height: 24 }} />
        ))}
      </Box>
      <Box sx={{ height: 390, cursor: onRpmChange ? 'crosshair' : undefined }}>
        {anyData ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart margin={{ top: 6, right: 12, bottom: 18, left: 4 }}
              onClick={(e: { activeLabel?: unknown } | null) => {
                const x = Number(e?.activeLabel);
                if (onRpmChange && Number.isFinite(x) && x > 0) onRpmChange(Math.round(x));
              }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.25)" />
              <XAxis type="number" dataKey="x" domain={['dataMin', 'dataMax']} tick={{ fontSize: 11 }} tickFormatter={(v: number) => v.toFixed(0)}
                label={{ value: tx('propLoad.x'), position: 'insideBottom', offset: -10, fontSize: 11 }} />
              <YAxis type="number" dataKey="y" width={46} tick={{ fontSize: 11 }} domain={['auto', 'auto']} tickFormatter={(v: number) => fmt(v)}
                label={{ value: unit, angle: -90, position: 'insideLeft', fontSize: 11 }} />
              <RcTooltip formatter={(v: unknown) => [`${fmt(Number(v))} ${unit}`, tx(`propLoad.m.${metric}`)]}
                labelFormatter={(x: unknown) => `${Number(x).toFixed(0)} rpm`}
                contentStyle={{ fontSize: 11 }} />
              <Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 11 }} />
              {s1Rpm != null && <ReferenceLine x={s1Rpm} stroke="#16a34a" strokeDasharray="4 3" label={{ value: 'S1', fill: '#16a34a', fontSize: 10, position: 'top' }} />}
              {rpm != null && rpm > 0 && <ReferenceLine x={rpm} stroke="var(--text-2)" strokeWidth={1.5} />}
              {lines.flatMap((l) => [
                <Line key={`${l.id}-ok`} data={l.ok} dataKey="y" name={l.name} stroke={l.color} strokeWidth={2} dot={false} isAnimationActive={false} />,
                <Line key={`${l.id}-over`} data={l.over} dataKey="y" name={`${l.name} — ${tx('propLoad.over')}`} legendType="none"
                  stroke={l.color} strokeWidth={1.5} strokeDasharray="5 4" dot={false} isAnimationActive={false} />,
              ])}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <Typography sx={{ fontSize: 12, color: 'var(--text-3)', pt: 4, textAlign: 'center' }}>{tx('propLoad.noData')}</Typography>
        )}
      </Box>
      {allowed.length > 1 && (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.5, alignItems: 'center' }}>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mr: 0.5 }}>{tx('propLoad.compare')}</Typography>
          {allowed.filter((p) => p.selectable !== false).map((p) => {
            const i = ids.indexOf(p.id);
            const on = i >= 0;
            return (
              <Chip key={p.id} size="small" label={propName(p, p.id)} disabled={p.id === selectedId || (!on && ids.length >= MAX_PROPS)}
                onClick={() => toggle(p.id)} variant={on ? 'filled' : 'outlined'}
                sx={{ fontSize: 11, height: 22, ...(on ? { bgcolor: LOAD_COLORS[i], color: '#fff' } : {}) }} />
            );
          })}
        </Box>
      )}
      <Typography sx={{ fontSize: 10.5, color: 'var(--text-3)', mt: 0.5 }}>{tx('propLoad.note')}</Typography>
    </Paper>
  );
};

export default PropellerLoadChart;
