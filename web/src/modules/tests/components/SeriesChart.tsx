/** The chart to the right of a comparison group whose rows form a series over speed (owner 2026-10-27): x = rpm, two
 *  curves - "Measured" (dots + line) and "Model" (dashed line) - y = the group's unit, a tooltip with both values and
 *  Δ %.  Pure presentation of `GroupSeries` (tableModel.seriesOf); no data work here. */
import React from 'react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip as RcTooltip, XAxis, YAxis } from 'recharts';
import { useTranslation } from 'react-i18next';
import { fmtDelta, fmtValue, unitLabel, type GroupSeries, type SeriesPoint } from '../tableModel';

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
      <div style={{ fontWeight: 700 }}>{fmtValue(p.rpm)} {t('tests.chart.rpm')}</div>
      <div><span style={{ color: MEASURED_COLOR }}>●</span> {t('tests.chart.measured')}: {fmtValue(p.measured)} {u}</div>
      <div><span style={{ color: MODEL_COLOR }}>●</span> {t('tests.chart.model')}: {fmtValue(p.model)} {u}</div>
      <div>Δ {fmtDelta(p.delta)} %</div>
    </div>
  );
}

export const SeriesChart: React.FC<{ series: GroupSeries; height?: number }> = ({ series, height = 190 }) => {
  const { t } = useTranslation('production');
  const u = unitLabel(series.unit);
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
          <Line type="monotone" dataKey="measured" name={t('tests.chart.measured')} stroke={MEASURED_COLOR} strokeWidth={1.6}
            dot={{ r: 2.5, fill: MEASURED_COLOR }} connectNulls isAnimationActive={false} />
          <Line type="monotone" dataKey="model" name={t('tests.chart.model')} stroke={MODEL_COLOR} strokeWidth={1.6}
            strokeDasharray="5 3" dot={false} connectNulls isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

export default SeriesChart;
