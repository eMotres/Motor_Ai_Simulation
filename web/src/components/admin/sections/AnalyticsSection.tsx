// Admin · Analytics — site statistics from the self-hosted Umami (GET /api/admin/analytics).
// Admin tab only; the backend asks Umami server-side, so no analytics credential reaches the browser.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Paper, Typography, ToggleButton, ToggleButtonGroup, Table, TableBody, TableCell,
  TableRow, CircularProgress, Alert, useTheme,
} from '@mui/material';
import {
  ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip as RcTooltip,
} from 'recharts';
import { useTranslation } from 'react-i18next';
import {
  ANALYTICS_RANGES, analyticsErrorKind, chartData, countryName, formatDuration, formatPercent,
  withShare, type AnalyticsPayload, type AnalyticsRange, type AnalyticsRow,
} from '../analyticsPanel';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5 } as const;
const LS_KEY = 'admin.analytics.range';

function savedRange(): AnalyticsRange {
  try {
    const v = window.localStorage.getItem(LS_KEY);
    if (v && (ANALYTICS_RANGES as readonly string[]).includes(v)) return v as AnalyticsRange;
  } catch { /* blocked storage */ }
  return '7d';
}

const Tile: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <Paper elevation={0} sx={{ ...PANEL, minWidth: 130, flex: '1 1 130px' }}>
    <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>{label}</Typography>
    <Typography sx={{ fontSize: 22, fontWeight: 800, color: 'var(--text-0)' }}>{value}</Typography>
  </Paper>
);

const TopList: React.FC<{ title: string; rows: AnalyticsRow[]; empty: string; map?: (n: string) => string }> =
  ({ title, rows, empty, map }) => (
  <Paper elevation={0} sx={{ ...PANEL, flex: '1 1 280px', minWidth: 260 }}>
    <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)', mb: 0.5 }}>{title}</Typography>
    {rows.length === 0 ? (
      <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{empty}</Typography>
    ) : (
      <Table size="small">
        <TableBody>
          {withShare(rows).map((r) => (
            <TableRow key={r.name}>
              <TableCell sx={{ fontSize: 12, py: 0.25, border: 0, wordBreak: 'break-all' }}>
                {map ? map(r.name) : r.name}
              </TableCell>
              <TableCell align="right" sx={{ fontSize: 12, py: 0.25, border: 0 }}>{r.count}</TableCell>
              <TableCell align="right" sx={{ fontSize: 11, py: 0.25, border: 0, color: 'var(--text-2)' }}>
                {r.share.toFixed(1)} %
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )}
  </Paper>
);

const AnalyticsSection: React.FC = () => {
  const { t, i18n } = useTranslation('admin');
  const theme = useTheme();
  const [range, setRange] = useState<AnalyticsRange>(savedRange);
  const [data, setData] = useState<AnalyticsPayload | null>(null);
  const [state, setState] = useState<'loading' | 'ok' | 'unavailable' | 'forbidden' | 'error'>('loading');

  const load = useCallback(async (r: AnalyticsRange) => {
    setState('loading');
    try {
      const res = await fetch(`${API}/api/admin/analytics?range=${r}`);
      if (!res.ok) { setData(null); setState(analyticsErrorKind(res.status)); return; }
      setData(await res.json() as AnalyticsPayload);
      setState('ok');
    } catch { setData(null); setState('error'); }
  }, []);
  useEffect(() => { void load(range); }, [range, load]);

  const pick = (_e: unknown, v: AnalyticsRange | null) => {
    if (!v) return;
    setRange(v);
    try { window.localStorage.setItem(LS_KEY, v); } catch { /* not persisted this time */ }
  };

  const bars = theme.palette.primary.main;
  const line = theme.palette.secondary.main;
  const tt = data?.totals;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>
          {t('analytics.title')}
        </Typography>
        <ToggleButtonGroup size="small" exclusive value={range} onChange={pick}>
          {ANALYTICS_RANGES.map((r) => (
            <ToggleButton key={r} value={r} sx={{ textTransform: 'none', px: 1.5, py: 0.25, fontSize: 12 }}>
              {t(`analytics.range.${r}`)}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        {state === 'loading' && <CircularProgress size={16} />}
      </Box>

      {state === 'unavailable' && (
        <Alert severity="warning" sx={{ mb: 1.5 }}>{t('analytics.unavailable')}</Alert>
      )}
      {state === 'forbidden' && <Alert severity="error" sx={{ mb: 1.5 }}>{t('analytics.forbidden')}</Alert>}
      {state === 'error' && <Alert severity="error" sx={{ mb: 1.5 }}>{t('analytics.error')}</Alert>}

      {data && tt && (
        <>
          <Box sx={{ display: 'flex', gap: 1.25, flexWrap: 'wrap', mb: 1.5 }}>
            <Tile label={t('analytics.visitors')} value={String(tt.visitors)} />
            <Tile label={t('analytics.visits')} value={String(tt.visits)} />
            <Tile label={t('analytics.pageviews')} value={String(tt.pageviews)} />
            <Tile label={t('analytics.bounce')} value={formatPercent(tt.bounce_rate)} />
            <Tile label={t('analytics.avgVisit')} value={formatDuration(tt.avg_visit_s)} />
          </Box>

          <Paper elevation={0} sx={{ ...PANEL, mb: 1.5 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)', mb: 0.5 }}>
              {data.unit === 'hour' ? t('analytics.chartHourly') : t('analytics.chartDaily')}
            </Typography>
            <Box sx={{ height: 220 }}>
              <ResponsiveContainer>
                <ComposedChart data={chartData(data)} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
                  <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 10 }} width={36} />
                  <RcTooltip />
                  <Bar dataKey="pageviews" name={t('analytics.pageviews')} fill={bars} fillOpacity={0.55} />
                  <Line dataKey="visitors" name={t('analytics.visitors')} stroke={line} strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </Box>
          </Paper>

          <Box sx={{ display: 'flex', gap: 1.25, flexWrap: 'wrap' }}>
            <TopList title={t('analytics.topPages')} rows={data.pages} empty={t('analytics.noData')} />
            <TopList title={t('analytics.countries')} rows={data.countries} empty={t('analytics.noData')}
              map={(n) => countryName(n, i18n.language)} />
            <TopList title={t('analytics.referrers')} rows={data.referrers} empty={t('analytics.noData')} />
            <TopList title={t('analytics.browsers')} rows={data.browsers} empty={t('analytics.noData')} />
            <TopList title={t('analytics.os')} rows={data.os} empty={t('analytics.noData')} />
            <TopList title={t('analytics.devices')} rows={data.devices} empty={t('analytics.noData')} />
          </Box>
        </>
      )}
    </Box>
  );
};

export default AnalyticsSection;
