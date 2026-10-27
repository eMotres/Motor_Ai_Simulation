import { Box, Typography, useTheme } from '@mui/material';
import { CONTINUOUS } from '../../keyPoints';
import { formatNumber, type ChartModel } from './operatingPoints';

/** Live torque-speed chart of the entered operating points: the continuous point, timed (peak) points, the mission-profile
 *  points as small grey markers and the maximum speed as a dashed line.  Updates as the user types. */
export default function OperatingChart({ model, t, generator }: { model: ChartModel; t: (key: string, args?: Record<string, unknown>) => string; generator?: boolean }) {
  const theme = useTheme();
  const blue = theme.palette.primary.main, amber = theme.palette.warning.main, grey = theme.palette.text.secondary;
  const { plot } = model;
  const dur = (d: unknown) => d === CONTINUOUS ? t('keyPoints.continuous') : typeof d === 'number' ? `${formatNumber(d)} s` : '';
  const empty = model.points.length === 0;
  // no legend (owner 2026-10-27): the points are listed right above the chart; each marker names its point on hover
  const label = (p: ChartModel['points'][number]) => `${p.name || t('keyPoints.name')} · ${formatNumber(p.torque)} N·m · ${formatNumber(p.speed)} rpm${dur(p.duration) ? ` · ${dur(p.duration)}` : ''}`;
  return <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
    <Typography component="h2" sx={{ fontSize: 15, fontWeight: 600, color: 'var(--text-0)' }}>{t('wizard.chart.title')}</Typography>
    <Box component="svg" role="img" aria-label={t('wizard.chart.aria')} viewBox={`0 0 ${model.width} ${model.height}`} sx={{ flex: 1, minWidth: 0, width: '100%', height: 'auto', maxHeight: 320 }}>
      <rect x={plot.left} y={plot.top} width={plot.right - plot.left} height={plot.bottom - plot.top} fill="var(--app-bg)" />
      {model.yTicks.map((tick) => <g key={`y${tick.value}`}><line x1={plot.left} x2={plot.right} y1={tick.y} y2={tick.y} stroke="var(--line-soft)" /><text x={plot.left - 8} y={tick.y + 4} textAnchor="end" fontSize="11" fill="var(--text-3)">{formatNumber(tick.value)}</text></g>)}
      {model.xTicks.map((tick) => <g key={`x${tick.value}`}><line x1={tick.x} x2={tick.x} y1={plot.top} y2={plot.bottom} stroke="var(--line-soft)" /><text x={tick.x} y={plot.bottom + 16} textAnchor="middle" fontSize="11" fill="var(--text-3)">{formatNumber(tick.value)}</text></g>)}
      <line x1={plot.left} x2={plot.right} y1={plot.bottom} y2={plot.bottom} stroke="var(--line)" />
      <line x1={plot.left} x2={plot.left} y1={plot.top} y2={plot.bottom} stroke="var(--line)" />
      <text x={(plot.left + plot.right) / 2} y={model.height - 6} textAnchor="middle" fontSize="12" fill="var(--text-3)">{t('wizard.chart.speedAxis')}</text>
      <text x={14} y={(plot.top + plot.bottom) / 2} textAnchor="middle" fontSize="12" fill="var(--text-3)" transform={`rotate(-90 14 ${(plot.top + plot.bottom) / 2})`}>{generator ? t('wizard.chart.torqueAxisGenerator') : t('wizard.chart.torqueAxis')}</text>
      {model.maxSpeed && <g><line x1={model.maxSpeed.x} x2={model.maxSpeed.x} y1={plot.top} y2={plot.bottom} stroke={grey} strokeDasharray="5 4" /><text x={model.maxSpeed.x - 4} y={plot.top + 12} textAnchor="end" fontSize="11" fill={grey}>{formatNumber(model.maxSpeed.value)} rpm</text></g>}
      {model.mission.map((m) => <circle key={m.id} cx={m.x} cy={m.y} r={3.5} fill="none" stroke={grey} strokeWidth="1.2"><title>{t('wizard.chart.mission')}</title></circle>)}
      {model.points.map((p) => {
        const left = p.x > plot.right - 120;
        return <g key={p.id}>
          <circle cx={p.x} cy={p.y} r={8} fill={p.duration === CONTINUOUS ? blue : amber} stroke="var(--panel)" strokeWidth="2"><title>{label(p)}</title></circle>
          <text x={left ? p.x - 13 : p.x + 13} y={p.y + 4} textAnchor={left ? 'end' : 'start'} fontSize="12" fill="var(--text-0)">{formatNumber(p.torque)} N·m{dur(p.duration) && p.duration !== CONTINUOUS ? ` · ${dur(p.duration)}` : ''}</text>
        </g>;
      })}
      {empty && <text x={(plot.left + plot.right) / 2} y={(plot.top + plot.bottom) / 2} textAnchor="middle" fontSize="13" fill="var(--text-3)">{t('wizard.chart.empty')}</text>}
    </Box>
  </Box>;
}
