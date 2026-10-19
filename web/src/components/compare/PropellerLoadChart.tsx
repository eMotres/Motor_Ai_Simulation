/**
 * Load chart (owner 2026-10-18, after the X13-18S datasheet chart): speed [rpm] on x and ONE parameter on y, switched
 * with the chips above it (thrust, torque, battery / phase current, efficiency, g/W, shaft / battery power,
 * winding / magnet temperature), one line per propeller — the propeller Configure has selected plus any other this
 * motor may drive.  Each point is a steady continuous operating point from the backend's propeller S1 rows; the
 * part above the continuous limit is dashed.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Chip, CircularProgress, Paper, Tooltip, Typography } from '@mui/material';
import { CartesianGrid, Legend, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip as RcTooltip, XAxis, YAxis } from 'recharts';
import type { PropSummary } from '../../lib/configuratorPropeller';
import { modelLabel, vendorLabel } from '../../lib/configuratorPropeller';
import { fetchPropellerS1 } from '../../lib/propellerApi';
import { s1CacheKey } from '../../lib/configuratorPropellerS1';
import { LOAD_COLORS, LOAD_METRICS, METRIC_UNIT, chartState, loadLine, rpmAtChartX, valueAt, withPointAt, type LoadMetric, type S1ChartRow } from '../../lib/propellerLoadChart';
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
  /** the continuous limit with this propeller (green dashed); the chip sets the speed to it */
  s1Rpm?: number | null;
  /** what stops the continuous line there (controller current, winding, demagnetisation ...), shown on the S1 line */
  s1Reason?: string | null;
  /** the controller / ESC / pack current limit (orange dashed); absent with a sine drive */
  supplyRpm?: number | null;
  supplyReason?: string | null;
  /** a side that holds to the top of its scan (a BOUND): the whole label of its line, e.g. "S1: holds up to 9 090 rpm
   *  (end of propeller data)" - the line is still drawn (owner 2026-10-19) */
  s1Label?: string | null;
  supplyLabel?: string | null;
  onRpmChange?: (rpm: number) => void;
}

const propName = (p: PropSummary | undefined, id: string) =>
  p ? `${vendorLabel(p.vendor)} ${modelLabel(p.model)}` : id;

const PropellerLoadChart: React.FC<Props> = ({ die, config, ambient, drive, pack, escBusA, allowed, selectedId, s1Rpm, s1Reason, supplyRpm, supplyReason, s1Label, supplyLabel, onRpmChange }) => {
  const [metric, setMetric] = useState<LoadMetric>('thrust');
  const [extra, setExtra] = useState<string[]>([]);
  // the answers kept WITH the request they answer (propeller, drive, pack, air): a line never shows another request's rows,
  // and a request still in flight is "loading", never "no data" (owner 2026-10-19)
  const [rows, setRows] = useState<Record<string, S1ChartRow[] | null>>({});
  const ids = useMemo(() => {
    const out: string[] = [];
    for (const id of [selectedId, ...extra]) if (id && !out.includes(id) && allowed.some((p) => p.id === id)) out.push(id);
    return out.slice(0, MAX_PROPS);
  }, [selectedId, extra, allowed]);
  const packMin = pack?.min ?? null;
  const packMax = pack?.max ?? null;
  const reqKey = (id: string) => s1CacheKey(id, die, config, ambient, drive,
    packMin != null && packMax != null ? { min: packMin, max: packMax } : null, escBusA);

  useEffect(() => {
    let dead = false;
    const pk = packMin != null && packMax != null ? { min: packMin, max: packMax } : null;
    for (const id of ids) {
      const k = reqKey(id);
      void fetchPropellerS1(id, die, config, ambient, drive, pk, escBusA).then((r) => {
        if (dead) return;
        const rr = (r as unknown as { rows?: S1ChartRow[] } | null)?.rows ?? null;
        setRows((cur) => ({ ...cur, [k]: rr }));
      });
    }
    return () => { dead = true; };
  }, [ids, die, config, ambient, drive, packMin, packMax, escBusA]); // eslint-disable-line react-hooks/exhaustive-deps
  const answers = Object.fromEntries(ids.map((id) => [id, rows[reqKey(id)]]));

  // the selected propeller's line carries points AT the two limit speeds, so the tooltip stops exactly there
  const lines = ids.map((id, i) => {
    let ln = loadLine(answers[id], metric);
    if (id === selectedId) ln = withPointAt(withPointAt(ln, s1Rpm), supplyRpm);
    return { id, color: LOAD_COLORS[i], name: propName(allowed.find((p) => p.id === id), id), ...ln };
  });
  const sel = lines.find((l) => l.id === selectedId);
  const s1Y = sel && s1Rpm != null ? valueAt(sel, s1Rpm) : null;
  const supplyY = sel && supplyRpm != null ? valueAt(sel, supplyRpm) : null;
  const anyData = lines.some((l) => l.ok.length || l.over.length);
  const state = anyData ? 'data' : chartState(ids, answers);
  // A click on the chart sets the motor's speed there, dragging moves it (owner 2026-10-19: "it used to recompute the
  // motor") - the current then follows the propeller, no loading gate: an interactive move recomputes at once.
  const box = useRef<HTMLDivElement | null>(null);
  const dragging = useRef(false);
  const xs = lines.flatMap((l) => [...l.ok, ...l.over].map((q) => q.x));
  const xMin = xs.length ? Math.min(...xs) : 0;
  const xMax = xs.length ? Math.max(...xs) : 0;
  const setFrom = (ev: { clientX?: number } | undefined) => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect || ev?.clientX == null || !onRpmChange) return;
    const r = rpmAtChartX(ev.clientX - rect.left, rect.width, xMin, xMax);
    if (r != null) onRpmChange(r);
  };
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
      {onRpmChange && s1Rpm != null && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
          <Chip size="small" variant="outlined" onClick={() => onRpmChange(Math.round(s1Rpm))}
            label={tx('propLoad.toS1', { rpm: Math.round(s1Rpm) })}
            sx={{ fontSize: 11, height: 22, borderColor: '#16a34a', color: '#16a34a' }} />
        </Box>
      )}
      <Box ref={box} sx={{ height: 390, cursor: onRpmChange && anyData ? 'crosshair' : undefined, userSelect: 'none' }}
        onMouseUp={() => { dragging.current = false; }} onMouseLeave={() => { dragging.current = false; }}>
        {anyData ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart margin={{ top: 6, right: 12, bottom: 18, left: 4 }}
              onMouseDown={(_s: unknown, ev: { clientX?: number }) => { dragging.current = true; setFrom(ev); }}
              onMouseMove={(_s: unknown, ev: { clientX?: number }) => { if (dragging.current) setFrom(ev); }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.25)" />
              <XAxis type="number" dataKey="x" domain={['dataMin', 'dataMax']} tick={{ fontSize: 11 }} tickFormatter={(v: number) => v.toFixed(0)}
                label={{ value: tx('propLoad.x'), position: 'insideBottom', offset: -10, fontSize: 11 }} />
              <YAxis type="number" dataKey="y" width={46} tick={{ fontSize: 11 }} domain={['auto', 'auto']} tickFormatter={(v: number) => fmt(v)}
                label={{ value: unit, angle: -90, position: 'insideLeft', fontSize: 11 }} />
              <RcTooltip formatter={(v: unknown) => [`${fmt(Number(v))} ${unit}`, tx(`propLoad.m.${metric}`)]}
                labelFormatter={(x: unknown) => `${Number(x).toFixed(0)} rpm`}
                contentStyle={{ fontSize: 11 }} />
              <Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 11 }} />
              {s1Rpm != null && <ReferenceLine x={s1Rpm} stroke="#16a34a" strokeDasharray="4 3"
                label={{ value: s1Label ?? (tx('propLoad.motorLabel', { rpm: Math.round(s1Rpm) }) + (s1Reason ? ` - ${s1Reason}` : '')), fill: '#16a34a', fontSize: 10.5, position: 'insideTopRight' }} />}
              {supplyRpm != null && <ReferenceLine x={supplyRpm} stroke="#ea580c" strokeDasharray="6 3"
                label={{ value: supplyLabel ?? (tx('propLoad.supplyLabel', { rpm: Math.round(supplyRpm) }) + (supplyReason ? ` - ${supplyReason}` : '')),
                  fill: '#ea580c', fontSize: 10.5, position: 'insideBottomLeft' }} />}
              {s1Rpm != null && s1Y != null && <ReferenceDot x={s1Rpm} y={s1Y} r={5} fill="#16a34a" stroke="#fff"
                label={{ value: `${fmt(s1Y)} ${unit}`, position: 'top', offset: 12, fill: '#16a34a', fontSize: 11, fontWeight: 700 }} />}
              {supplyRpm != null && supplyY != null && <ReferenceDot x={supplyRpm} y={supplyY} r={5} fill="#ea580c" stroke="#fff"
                label={{ value: `${fmt(supplyY)} ${unit}`, position: 'bottom', offset: 12, fill: '#ea580c', fontSize: 11, fontWeight: 700 }} />}
              {lines.flatMap((l) => [
                <Line key={`${l.id}-ok`} data={l.ok} dataKey="y" name={l.name} stroke={l.color} strokeWidth={2} dot={false} isAnimationActive={false} />,
                <Line key={`${l.id}-over`} data={l.over} dataKey="y" name={`${l.name} — ${tx('propLoad.over')}`} legendType="none"
                  stroke={l.color} strokeWidth={1.5} strokeDasharray="5 4" dot={false} isAnimationActive={false} />,
              ])}
            </LineChart>
          </ResponsiveContainer>
        ) : state === 'loading' ? (
          <Box role="status" sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1, pt: 4 }}>
            <CircularProgress size={14} sx={{ color: '#60a5fa' }} />
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tx('propLoad.loading')}</Typography>
          </Box>
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
      <Typography sx={{ fontSize: 10.5, color: 'var(--text-3)', mt: 0.5 }}>{tx('propLoad.note')} {tx('propLoad.linesNote2')}</Typography>
    </Paper>
  );
};

export default PropellerLoadChart;
