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
import LiveAnalyticsCard from './LiveAnalyticsCard';

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

interface AccountAnalyticsPayload {
  range: AnalyticsRange;
  accounts: { total: number; new: number };
  nda: { total: number; new: number; online_total: number; admin_flag_total: number };
  series: Array<{ t: string; accounts: number; nda: number }>;
}

const AccountTile: React.FC<{ label: string; value: number; totalLabel: string }> = ({ label, value, totalLabel }) => (
  <Paper elevation={0} sx={{ ...PANEL, minWidth: 150, flex: '1 1 150px' }}>
    <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>{label}</Typography>
    <Typography sx={{ fontSize: 22, fontWeight: 800, color: 'var(--text-0)' }}>{value}</Typography>
    <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>
      {totalLabel}
    </Typography>
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
  const [accountData, setAccountData] = useState<AccountAnalyticsPayload | null>(null);
  const [accountState, setAccountState] = useState<'loading' | 'ok' | 'forbidden' | 'error'>('loading');

  const load = useCallback(async (r: AnalyticsRange, signal: AbortSignal) => {
    setData(null);
    setState('loading');
    try {
      const res = await fetch(`${API}/api/admin/analytics?range=${r}`, { signal });
      if (signal.aborted) return;
      if (!res.ok) { setData(null); setState(analyticsErrorKind(res.status)); return; }
      const payload = await res.json() as AnalyticsPayload;
      if (signal.aborted) return;
      setData(payload);
      setState('ok');
    } catch { if (!signal.aborted) { setData(null); setState('error'); } }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void load(range, controller.signal);
    return () => controller.abort();
  }, [range, load]);

  useEffect(() => {
    const controller = new AbortController();
    setAccountData(null);
    setAccountState('loading');
    void fetch(`${API}/api/admin/analytics/accounts?range=${range}`, { signal: controller.signal })
      .then(async (res) => {
        if (controller.signal.aborted) return;
        if (!res.ok) {
          setAccountData(null);
          setAccountState(res.status === 401 || res.status === 403 ? 'forbidden' : 'error');
          return;
        }
        const payload = await res.json() as AccountAnalyticsPayload;
        if (controller.signal.aborted) return;
        setAccountData(payload);
        setAccountState('ok');
      })
      .catch(() => {
        if (!controller.signal.aborted) { setAccountData(null); setAccountState('error'); }
      });
    return () => controller.abort();
  }, [range]);

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
      <LiveAnalyticsCard />

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
        {(state === 'loading' || accountState === 'loading') && <CircularProgress size={16} />}
      </Box>

      {state === 'unavailable' && (
        <Alert severity="warning" sx={{ mb: 1.5 }}>{t('analytics.unavailable')}</Alert>
      )}
      {state === 'forbidden' && <Alert severity="error" sx={{ mb: 1.5 }}>{t('analytics.forbidden')}</Alert>}
      {state === 'error' && <Alert severity="error" sx={{ mb: 1.5 }}>{t('analytics.error')}</Alert>}
      {accountState === 'forbidden' && <Alert severity="error" sx={{ mb: 1.5 }}>{t('analytics.forbidden')}</Alert>}
      {accountState === 'error' && <Alert severity="error" sx={{ mb: 1.5 }}>{t('analytics.accountsError')}</Alert>}

      {(data || accountData) && (
        <>
          <Box sx={{ display: 'flex', gap: 1.25, flexWrap: 'wrap', mb: 1.5 }}>
            {tt && <>
              <Tile label={t('analytics.visitors')} value={String(tt.visitors)} />
              <Tile label={t('analytics.visits')} value={String(tt.visits)} />
              <Tile label={t('analytics.pageviews')} value={String(tt.pageviews)} />
              <Tile label={t('analytics.bounce')} value={formatPercent(tt.bounce_rate)} />
              <Tile label={t('analytics.avgVisit')} value={formatDuration(tt.avg_visit_s)} />
            </>}
            {accountData && <>
              <AccountTile label={t('analytics.registered')} value={accountData.accounts.new}
                totalLabel={t('analytics.totalLine', { count: accountData.accounts.total })} />
              <AccountTile label={t('analytics.ndaSigned')} value={accountData.nda.new}
                totalLabel={t('analytics.totalLine', { count: accountData.nda.total })} />
            </>}
          </Box>

          {data && tt && <Paper elevation={0} sx={{ ...PANEL, mb: 1.5 }}>
            <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)', mb: 0.5 }}>
              {data.unit === 'hour' ? t('analytics.chartHourly') : t('analytics.chartDaily')}
            </Typography>
            <Box sx={{ height: 220 }}>
              <ResponsiveContainer>
                <ComposedChart data={chartData(data)} margin={{ top: 5, right: 0, bottom: 0, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
                  <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                  {/* two scales (owner 2026-10-17): page views on the left, visitors on the right, each axis in its series' colour */}
                  <YAxis yAxisId="views" allowDecimals={false} tick={{ fontSize: 10, fill: bars }} width={36}
                    label={{ value: t('analytics.pageviews'), angle: -90, position: 'insideLeft', fontSize: 10, fill: bars }} />
                  <YAxis yAxisId="visitors" orientation="right" allowDecimals={false} tick={{ fontSize: 10, fill: line }} width={36}
                    label={{ value: t('analytics.visitors'), angle: 90, position: 'insideRight', fontSize: 10, fill: line }} />
                  <RcTooltip />
                  <Bar yAxisId="views" dataKey="pageviews" name={t('analytics.pageviews')} fill={bars} fillOpacity={0.55} />
                  <Line yAxisId="visitors" dataKey="visitors" name={t('analytics.visitors')} stroke={line} strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </Box>
          </Paper>}

          {data && <Box sx={{ display: 'flex', gap: 1.25, flexWrap: 'wrap' }}>
            <TopList title={t('analytics.topPages')} rows={data.pages} empty={t('analytics.noData')} />
            <TopList title={t('analytics.countries')} rows={data.countries} empty={t('analytics.noData')}
              map={(n) => countryName(n, i18n.language)} />
            <TopList title={t('analytics.referrers')} rows={data.referrers} empty={t('analytics.noData')} />
            <TopList title={t('analytics.browsers')} rows={data.browsers} empty={t('analytics.noData')} />
            <TopList title={t('analytics.os')} rows={data.os} empty={t('analytics.noData')} />
            <TopList title={t('analytics.devices')} rows={data.devices} empty={t('analytics.noData')} />
          </Box>}
        </>
      )}
    </Box>
  );
};

export default AnalyticsSection;
