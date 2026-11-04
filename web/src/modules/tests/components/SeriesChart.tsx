/** The chart to the right of a comparison group whose rows form a series over speed (owner 2026-10-27): x = rpm, two
 *  curves - "Measured" (dots + line) and "Model" (dashed line) - y = the group's unit, a tooltip with both values and
 *  Δ %.  Pure presentation of `GroupSeries` (tableModel.seriesOf); no data work here. */
import React from 'react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip as RcTooltip, XAxis, YAxis } from 'recharts';
import { useTranslation } from 'react-i18next';
import { fmtDelta, fmtValue, speedChartData, unitLabel, type GroupSeries, type SeriesPoint, type SpeedPlot } from '../tableModel';

export const MEASURED_COLOR = '#38bdf8';
export const MODEL_COLOR = '#f59e0b';

function SeriesTooltip({ active, payload, unit }: { active?: boolean; payload?: Array<{ payload: SeriesPoint }>; unit: string }) {
  const { t } = useTranslation('production');
  const p = active && payload && payload.length ? payload[0].payload : null;
  if (!p) return null;
  const u = unitLabel(unit);
  return (
    <div style={{ background: 'var(--panel)', border: '1px solid var(--line-soft)', borderRadius: 4, padding: '4px 8px',
      fontSize: 11.5, color: 'var(--text-1)', lineHeight: 1.5 }}>
      <div style={{ fontWeight: 700 }}>{fmtValue(p.rpm)} {t('tests.chart.rpm')}{p.current != null ? ` · ${fmtValue(p.current)} A` : ''}</div>
      {p.measured !== null && <div><span style={{ color: MEASURED_COLOR }}>●</span> {t('tests.chart.measured')}: {fmtValue(p.measured)} {u}</div>}
      {p.model !== null && <div><span style={{ color: MODEL_COLOR }}>●</span> {t('tests.chart.model')}: {fmtValue(p.model)} {u}</div>}
      {p.model !== null && p.measured !== null && <div>Δ {fmtDelta(p.delta)} %</div>}
    </div>
  );
}

export const SeriesChart: React.FC<{ series: GroupSeries; height?: number }> = ({ series, height = 190 }) => {
  const { t } = useTranslation('production');
  const u = unitLabel(series.unit);
  // a series with no model value draws no model curve (no empty legend entry); a lone model point still shows as a dot
  const modelN = series.points.filter((p) => p.model !== null).length;
  const measuredN = series.points.filter((p) => p.measured !== null).length;
  return (
    <div style={{ width: '100%', height, minWidth: 0 }} data-testid="tests-series-chart">
      <ResponsiveContainer>
        <LineChart data={series.points} margin={{ top: 6, right: 12, bottom: 2, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line-soft)" />
          <XAxis dataKey="rpm" type="number" domain={['dataMin', 'dataMax']} tick={{ fontSize: 10 }}
            label={{ value: t('tests.chart.rpm'), position: 'insideBottomRight', offset: -2, fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} width={52} domain={['auto', 'auto']}
            label={{ value: u, angle: -90, position: 'insideLeft', fontSize: 10, offset: 12 }} />
          <RcTooltip content={<SeriesTooltip unit={series.unit} />} />
          <Legend verticalAlign="top" height={20} iconSize={10} wrapperStyle={{ fontSize: 11 }} />
          {measuredN > 0 && (
            <Line type="monotone" dataKey="measured" name={t('tests.chart.measured')} stroke={MEASURED_COLOR} strokeWidth={1.6}
              dot={{ r: 2.5, fill: MEASURED_COLOR }} connectNulls isAnimationActive={false} />
          )}
          {modelN > 0 && (
            <Line type="monotone" dataKey="model" name={t('tests.chart.model')} stroke={MODEL_COLOR} strokeWidth={1.6}
              strokeDasharray="5 3" dot={modelN === 1 ? { r: 2.5, fill: MODEL_COLOR } : false} connectNulls isAnimationActive={false} />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

/** One colour per speed (the model line of a speed shares its hue and is dashed). */
export const SPEED_COLORS = ['#38bdf8', '#f59e0b', '#34d399', '#f472b6', '#a78bfa', '#f87171', '#facc15', '#2dd4bf'];

/** A load test's plot with one line per speed: straight segments, x and y from the chosen plot; a one-line tooltip. */
export const SpeedChart: React.FC<{ plot: SpeedPlot; xLabel: string; height?: number }> = ({ plot, xLabel, height = 190 }) => {
  const { t } = useTranslation('production');
  const { data, lines } = React.useMemo(() => speedChartData(plot), [plot]);
  const yu = unitLabel(plot.yUnit);
  const name = (l: { rpm: number; model: boolean }) =>
    t('tests.series.rpm', { rpm: l.rpm }) + (l.model ? `, ${t('tests.series.modelSuffix')}` : '');
  const tip = ({ active, payload }: { active?: boolean; payload?: ReadonlyArray<{ dataKey?: unknown; value?: unknown; payload?: { x?: number } }> }) => {
    const hit = active && payload ? payload.find((e) => typeof e.value === 'number' && typeof e.payload?.x === 'number') : undefined;
    const l = hit ? lines.find((q) => q.id === hit.dataKey) : undefined;
    if (!hit || !l) return null;
    return (
      <div style={{ background: 'var(--panel)', border: '1px solid var(--line-soft)', borderRadius: 4, padding: '3px 8px',
        fontSize: 11.5, color: 'var(--text-1)', whiteSpace: 'nowrap' }}>
        {name(l)}: {fmtValue(hit.value as number)} {yu} · {fmtValue(hit.payload?.x)} {unitLabel(plot.xUnit)}
      </div>
    );
  };
  return (
    <div style={{ width: '100%', height, minWidth: 0 }} data-testid="tests-speed-chart">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 6, right: 12, bottom: 14, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line-soft)" />
          <XAxis dataKey="x" type="number" domain={['dataMin', 'dataMax']} tick={{ fontSize: 10 }}
            label={{ value: xLabel, position: 'insideBottom', offset: -8, fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} width={52} domain={['auto', 'auto']}
            label={{ value: yu, angle: -90, position: 'insideLeft', fontSize: 10, offset: 12 }} />
          <RcTooltip content={tip} />
          <Legend verticalAlign="top" height={20} iconSize={10} wrapperStyle={{ fontSize: 11 }} />
          {lines.map((l) => (
            <Line key={l.id} type="linear" dataKey={l.id} name={name(l)} stroke={SPEED_COLORS[l.color % SPEED_COLORS.length]}
              strokeWidth={1.6} strokeDasharray={l.model ? '5 3' : undefined}
              dot={{ r: 2.5, fill: l.model ? 'var(--panel)' : SPEED_COLORS[l.color % SPEED_COLORS.length],
                stroke: SPEED_COLORS[l.color % SPEED_COLORS.length] }}
              connectNulls isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

export default SeriesChart;
