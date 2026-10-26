/**
 * StepsChart - how the main values changed over the OPTIMIZATION STEPS of a project (owner 2026-10-26).
 *
 * x = the steps 1..N (labelled like the step chips, one colour per geometry), y = the value chosen in the one-line switch for
 * the mode (duty) that is selected.  Only values stored for at least two steps are offered; a step that does not store the value
 * is a GAP (never 0).  The step on screen is highlighted; a click on a point opens that step.
 */
import React, { useMemo } from 'react';
import { Box, MenuItem, Select, Tooltip, Typography } from '@mui/material';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip as RcTooltip, XAxis, YAxis } from 'recharts';
import { useTranslation } from 'react-i18next';
import { PANEL, LABEL, fmt } from '../common/ResultTiles';
import { nsT } from '../../i18n/nsT';
import { oneOf } from '../../lib/viewMemory';
import { useRememberedView } from '../../lib/useRememberedView';
import { motorColor } from '../../modules/development/motorGroups';
import { SERIES, availableSeries, pickSeries, stepSeries } from '../../modules/development/stepSeries';
import { configureTileKey } from '../../modules/development/pointsLimited';
import type { MotorPointVariant } from '../../modules/development/types';

const tc = nsT('controller');
const SERIES_IDS = SERIES.map((s) => s.id);

interface Props {
  variants: MotorPointVariant[];
  duty: string | null;
  current: number;
  onPick: (index: number) => void;
  height?: number | string;
}

const StepsChart: React.FC<Props> = ({ variants, duty, current, onPick, height = '100%' }) => {
  const { t } = useTranslation('development');
  const [wanted, setWanted] = useRememberedView<string>('configure.points.series', 'torque', oneOf(SERIES_IDS));
  const available = useMemo(() => availableSeries(variants, duty), [variants, duty]);
  const spec = pickSeries(available, wanted);
  const data = useMemo(() => (spec ? stepSeries(variants, duty, spec) : []), [variants, duty, spec]);
  const point = variants[current]?.points.find((p) => p.duty === duty) ?? variants.flatMap((v) => v.points)[0];
  const nameOf = (id: string) => {
    const key = configureTileKey(id, point);
    return key ? tc(key) : t(`points.series.${id}`);
  };
  if (!spec || data.length < 2) return null;

  const colorOf = (index: number) => motorColor(variants[index]);
  const Dot = (props: { cx?: number; cy?: number; index?: number; value?: unknown }) => {
    const { cx, cy, index = 0, value } = props;
    if (cx === undefined || cy === undefined || value === null || value === undefined) return <g />;
    const here = index === current;
    return (
      <g onClick={() => onPick(index)} style={{ cursor: 'pointer' }} data-testid={here ? 'steps-chart-current' : 'steps-chart-point'}>
        {here && <circle cx={cx} cy={cy} r={9} fill="none" stroke={colorOf(index)} strokeWidth={2} />}
        <circle cx={cx} cy={cy} r={here ? 5 : 4} fill={colorOf(index)} />
      </g>
    );
  };

  return (
    <Box sx={{ ...PANEL, p: 1, display: 'flex', flexDirection: 'column', gap: 0.5, height, minHeight: 260, boxSizing: 'border-box' }} data-testid="steps-chart">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={LABEL}>{t('points.chart.title')}</Typography>
        <Tooltip title={t('points.chart.switchTip')}>
          <Select size="small" value={spec.id} onChange={(e) => setWanted(String(e.target.value))} aria-label={t('points.chart.title')}
            sx={{ fontSize: 12, '& .MuiSelect-select': { py: 0.3 } }}>
            {available.map((s) => <MenuItem key={s.id} value={s.id} sx={{ fontSize: 12 }}>{nameOf(s.id)}{s.unit ? `, ${s.unit}` : ''}</MenuItem>)}
          </Select>
        </Tooltip>
      </Box>
      <Box sx={{ flex: 1, minHeight: 200 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 10, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="var(--line-soft)" strokeDasharray="3 3" />
            <XAxis dataKey="step" type="number" domain={[1, variants.length]} allowDecimals={false} ticks={data.map((d) => d.step)}
              tickFormatter={(s) => String(data.find((d) => d.step === s)?.label ?? s)} interval={0} angle={-20} textAnchor="end" height={54}
              tick={{ fontSize: 10, fill: 'var(--text-3)' }} />
            <YAxis tick={{ fontSize: 10, fill: 'var(--text-3)' }} width={52} domain={['auto', 'auto']} tickFormatter={(v) => fmt(Number(v), spec.d)} />
            <RcTooltip
              formatter={(v) => [`${fmt(Number(v), spec.d)} ${spec.unit}`.trim(), nameOf(spec.id)]}
              labelFormatter={(s) => String(data.find((d) => d.step === s)?.label ?? s)}
              contentStyle={{ background: 'var(--panel)', border: '1px solid var(--line-soft)', fontSize: 12 }} />
            <Line type="linear" dataKey="value" stroke="var(--text-3)" strokeWidth={1.5} connectNulls={false} isAnimationActive={false}
              dot={<Dot />} activeDot={false} />
          </LineChart>
        </ResponsiveContainer>
      </Box>
    </Box>
  );
};

export default StepsChart;
