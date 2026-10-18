// Admin · Activity — how long each signed-in account works with the system
// (GET /api/admin/activity, /api/admin/activity/user).  Heartbeat-based, server clock; see activity.py.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Paper, Typography, Table, TableBody, TableCell, TableHead, TableRow, TableSortLabel,
  CircularProgress, Alert, Button, Dialog, DialogTitle, DialogContent, useTheme,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RcTooltip } from 'recharts';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../common/HelpTip';
import LiveNowPanel from '../LiveNowPanel';
import {
  dayChartData, formatActive, formatTopTabs, sortRows, totalSeconds,
  type ActivityDay, type ActivityRow, type ActivitySortKey,
} from '../activityPanel';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

const UserDetail: React.FC<{ email: string | null; onClose: () => void }> = ({ email, onClose }) => {
  const { t } = useTranslation('admin');
  const theme = useTheme();
  const [days, setDays] = useState<ActivityDay[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!email) return;
    setDays(null); setFailed(false);
    fetch(`${API}/api/admin/activity/user?email=${encodeURIComponent(email)}&days=30`)
      .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then((d) => setDays(d.days ?? []))
      .catch(() => setFailed(true));
  }, [email]);
  return (
    <Dialog open={!!email} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: 15, fontWeight: 800 }}>{email}</DialogTitle>
      <DialogContent>
        {failed && <Alert severity="error">{t('activity.error')}</Alert>}
        {!days && !failed && <CircularProgress size={18} />}
        {days && (
          <>
            <Typography sx={{ fontSize: 12, color: 'var(--text-2)', mb: 1 }}>
              {t('activity.detailLine', { total: formatActive(totalSeconds(days)) })}
            </Typography>
            <Box sx={{ height: 220 }}>
              <ResponsiveContainer>
                <BarChart data={dayChartData(days)} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
                  <XAxis dataKey="label" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                  <YAxis allowDecimals={false} tick={{ fontSize: 10 }} width={36} unit=" min" />
                  <RcTooltip />
                  <Bar dataKey="minutes" name={t('activity.minutes')} fill={theme.palette.primary.main} />
                </BarChart>
              </ResponsiveContainer>
            </Box>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
};

const ActivitySection: React.FC = () => {
  const { t } = useTranslation('admin');
  const [rows, setRows] = useState<ActivityRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [sortKey, setSortKey] = useState<ActivitySortKey>('d7_s');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [detail, setDetail] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(false);
    try {
      const r = await fetch(`${API}/api/admin/activity`);
      if (!r.ok) throw new Error(String(r.status));
      setRows((await r.json()).users ?? []);
    } catch { setError(true); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const sorted = useMemo(() => sortRows(rows, sortKey, sortDir), [rows, sortKey, sortDir]);
  const toggle = (k: ActivitySortKey) => {
    if (sortKey === k) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(k); setSortDir(k === 'user' ? 'asc' : 'desc'); }
  };
  const head = (k: ActivitySortKey, label: string, align: 'left' | 'right' = 'right') => (
    <TableCell align={align}>
      <TableSortLabel active={sortKey === k} direction={sortDir} onClick={() => toggle(k)}>{label}</TableSortLabel>
    </TableCell>
  );
  const tabLabel = (tab: string) => t(`activity.tabs.${tab}`, { defaultValue: tab });

  return (
    <Box>
      <LiveNowPanel />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>{t('activity.title')}</Typography>
        <HelpTip title={t('activity.help')} />
        <Box sx={{ flex: 1 }} />
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} onClick={() => void load()}
          disabled={loading} sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>
          {t('activity.refresh')}
        </Button>
      </Box>
      <Typography sx={{ fontSize: 12, color: 'var(--text-2)', mb: 1.5 }}>{t('activity.line')}</Typography>

      {loading && <CircularProgress size={18} />}
      {error && !loading && <Alert severity="error">{t('activity.error')}</Alert>}
      {!loading && !error && (
        <Paper sx={{ ...PANEL, p: 0, overflow: 'auto' }}>
          <Table size="small" sx={{
            '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
            '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
          }}>
            <TableHead>
              <TableRow>
                {head('user', t('activity.user'), 'left')}
                {head('today_s', t('activity.today'))}
                {head('d7_s', t('activity.d7'))}
                {head('d30_s', t('activity.d30'))}
                {head('sessions_7d', t('activity.sessions7'))}
                {head('last_seen', t('activity.lastSeen'))}
                <TableCell>{t('activity.topTabs')}</TableCell>
                {head('runs_30d', t('activity.runs'))}
              </TableRow>
            </TableHead>
            <TableBody>
              {sorted.map((r) => (
                <TableRow key={r.user} hover onClick={() => setDetail(r.user)} sx={{ cursor: 'pointer' }}>
                  <TableCell sx={{ color: 'var(--text-0)', fontWeight: 600 }}>{r.user}</TableCell>
                  <TableCell align="right">{formatActive(r.today_s)}</TableCell>
                  <TableCell align="right">{formatActive(r.d7_s)}</TableCell>
                  <TableCell align="right">{formatActive(r.d30_s)}</TableCell>
                  <TableCell align="right">{r.sessions_7d}</TableCell>
                  <TableCell align="right" sx={{ color: 'var(--text-2)', whiteSpace: 'nowrap' }}>
                    {r.last_seen ? new Date(r.last_seen * 1000).toLocaleString() : '—'}
                  </TableCell>
                  <TableCell sx={{ color: 'var(--text-2)' }}>{formatTopTabs(r.top_tabs, tabLabel) || '—'}</TableCell>
                  <TableCell align="right">{r.runs_7d} / {r.runs_30d}</TableCell>
                </TableRow>
              ))}
              {sorted.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>
                    {t('activity.empty')}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}
      <UserDetail email={detail} onClose={() => setDetail(null)} />
    </Box>
  );
};

export default ActivitySection;
