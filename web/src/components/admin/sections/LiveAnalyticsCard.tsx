// Admin · Live Analytics — real-time visitors, active signed-in accounts and recent events.
// Auto-refreshes every 10s while open. Privacy: no IP address is displayed.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box, Paper, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  CircularProgress, Alert, Chip, IconButton, Tooltip,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useTranslation } from 'react-i18next';
import {
  countryName, formatRelativeTime,
  type LiveAnalyticsPayload, type LiveEvent, type LiveUser,
} from '../analyticsPanel';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5 } as const;
const SUB_PANEL = { bgcolor: 'var(--panel-1)', border: '1px solid var(--line-soft)', borderRadius: 1, p: 1.25 } as const;
const POLL_INTERVAL_MS = 10000;

function eventChipProps(type: string): { labelKey: string; color: 'default' | 'primary' | 'secondary' | 'success' | 'warning' | 'info' } {
  switch (type) {
    case 'signup':
      return { labelKey: 'analytics.live.eventSignup', color: 'success' };
    case 'login':
      return { labelKey: 'analytics.live.eventLogin', color: 'primary' };
    case 'email_confirmed':
      return { labelKey: 'analytics.live.eventEmailConfirmed', color: 'success' };
    case 'nda_request':
      return { labelKey: 'analytics.live.eventNda', color: 'warning' };
    case 'run_started':
      return { labelKey: 'analytics.live.eventRun', color: 'secondary' };
    case 'admin_action':
      return { labelKey: 'analytics.live.eventAdminAction', color: 'info' };
    case 'page_view':
      return { labelKey: 'analytics.live.eventPageView', color: 'default' };
    default:
      return { labelKey: type, color: 'default' };
  }
}

export const LiveAnalyticsCard: React.FC = () => {
  const { t, i18n } = useTranslation('admin');
  const [data, setData] = useState<LiveAnalyticsPayload | null>(null);
  const [state, setState] = useState<'loading' | 'ok' | 'forbidden' | 'error'>('loading');
  const [refreshing, setRefreshing] = useState(false);
  const mountedRef = useRef(true);

  const fetchLive = useCallback(async (isManual = false) => {
    if (isManual) setRefreshing(true);
    try {
      const res = await fetch(`${API}/api/admin/analytics/live`);
      if (!mountedRef.current) return;
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) setState('forbidden');
        else setState('error');
        return;
      }
      const json = (await res.json()) as LiveAnalyticsPayload;
      if (!mountedRef.current) return;
      setData(json);
      setState('ok');
    } catch {
      if (mountedRef.current) setState('error');
    } finally {
      if (mountedRef.current && isManual) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void fetchLive(false);

    const timer = setInterval(() => {
      void fetchLive(false);
    }, POLL_INTERVAL_MS);

    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, [fetchLive]);

  const totalOnline = (data?.active_users.length ?? 0) + (data?.anonymous.active ?? 0);
  const nowSec = Date.now() / 1000;

  return (
    <Paper elevation={0} sx={{ ...PANEL, mb: 2 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 1, mb: 1.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
          {/* Pulsing Live Dot */}
          <Box
            sx={{
              width: 9,
              height: 9,
              borderRadius: '50%',
              bgcolor: '#22c55e',
              boxShadow: '0 0 8px rgba(34, 197, 94, 0.8)',
              animation: 'liveDotPulse 2s infinite ease-in-out',
              '@keyframes liveDotPulse': {
                '0%': { opacity: 1, transform: 'scale(1)' },
                '50%': { opacity: 0.35, transform: 'scale(0.85)' },
                '100%': { opacity: 1, transform: 'scale(1)' },
              },
            }}
          />
          <Typography sx={{ fontSize: 14, fontWeight: 800, color: 'var(--text-0)' }}>
            {t('analytics.live.title')}
          </Typography>
          {data && (
            <Chip
              size="small"
              variant="outlined"
              label={t('analytics.live.totalOnline', {
                total: totalOnline,
                signedIn: data.active_users.length,
                anonymous: data.anonymous.active,
              })}
              sx={{ fontSize: 11, fontWeight: 600, height: 22, color: 'var(--text-1)' }}
            />
          )}
        </Box>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>
            {t('analytics.live.autoRefresh')}
          </Typography>
          <Tooltip title={t('activity.refresh')}>
            <span>
              <IconButton
                size="small"
                onClick={() => void fetchLive(true)}
                disabled={refreshing}
                sx={{ p: 0.5 }}
              >
                {refreshing ? <CircularProgress size={16} /> : <RefreshIcon sx={{ fontSize: 18 }} />}
              </IconButton>
            </span>
          </Tooltip>
        </Box>
      </Box>

      {/* Exact "active now": sessions with user input in the last 5 min, and open (not idle-closed) sessions */}
      {data?.sessions && (
        <Tooltip title={t('analytics.live.sessionsTip', { minutes: Math.round((data.sessions.idle_timeout_s || 0) / 60) })}>
          <Typography sx={{ fontSize: 12, color: 'var(--text-1)', mb: 1.25, display: 'inline-block' }}>
            {t('analytics.live.sessionsLine', { active: data.sessions.active_sessions, open: data.sessions.open_sessions })}
          </Typography>
        </Tooltip>
      )}

      {/* States */}
      {state === 'loading' && !data && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2 }}>
          <CircularProgress size={18} />
          <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('analytics.live.loading')}</Typography>
        </Box>
      )}

      {state === 'forbidden' && (
        <Alert severity="error" sx={{ mb: 1 }}>{t('analytics.forbidden')}</Alert>
      )}

      {state === 'error' && !data && (
        <Alert severity="error" sx={{ mb: 1 }}>{t('analytics.live.error')}</Alert>
      )}

      {data && (
        <>
          {/* Top Row: Active signed-in accounts + Anonymous Umami */}
          <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', mb: 1.5 }}>
            {/* Signed-in Active Users */}
            <Paper elevation={0} sx={{ ...SUB_PANEL, flex: '1 1 360px', minWidth: 300 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>
                  {t('analytics.live.signedInTitle')}
                </Typography>
                <Chip
                  size="small"
                  label={data.active_users.length}
                  color={data.active_users.length > 0 ? 'primary' : 'default'}
                  sx={{ height: 20, fontSize: 11, fontWeight: 700 }}
                />
              </Box>

              {data.active_users.length === 0 ? (
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)', py: 1 }}>
                  {t('analytics.live.noActiveUsers')}
                </Typography>
              ) : (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ fontSize: 11, py: 0.5, color: 'var(--text-2)', border: 0 }}>
                        {t('activity.user')}
                      </TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.5, color: 'var(--text-2)', border: 0 }}>
                        {t('analytics.live.tab')}
                      </TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.5, color: 'var(--text-2)', border: 0 }}>
                        {t('verification.col.country')}
                      </TableCell>
                      <TableCell align="right" sx={{ fontSize: 11, py: 0.5, color: 'var(--text-2)', border: 0 }}>
                        {t('activity.lastSeen')}
                      </TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {data.active_users.map((u: LiveUser) => (
                      <TableRow key={u.user}>
                        <TableCell sx={{ fontSize: 12, py: 0.5, border: 0, fontWeight: 600 }}>
                          {u.name ? (
                            <Box>
                              <Typography sx={{ fontSize: 12, fontWeight: 600, color: 'var(--text-0)', lineHeight: 1.2 }}>
                                {u.name}
                              </Typography>
                              <Typography sx={{ fontSize: 10, color: 'var(--text-2)', lineHeight: 1.1 }}>
                                {u.user}
                              </Typography>
                            </Box>
                          ) : (
                            <Typography sx={{ fontSize: 12, fontWeight: 600, color: 'var(--text-0)' }}>
                              {u.user}
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell sx={{ fontSize: 11, py: 0.5, border: 0 }}>
                          <Chip
                            size="small"
                            variant="outlined"
                            label={t(`activity.tabs.${u.tab}`, { defaultValue: u.tab || '–' })}
                            sx={{ height: 20, fontSize: 10 }}
                          />
                        </TableCell>
                        <TableCell sx={{ fontSize: 11, py: 0.5, border: 0, color: 'var(--text-1)' }}>
                          {u.country ? countryName(u.country, i18n.language) : '–'}
                        </TableCell>
                        <TableCell align="right" sx={{ fontSize: 11, py: 0.5, border: 0, color: 'var(--text-2)' }}>
                          {formatRelativeTime(u.last_beat, nowSec)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Paper>

            {/* Anonymous Visitors (Umami) */}
            <Paper elevation={0} sx={{ ...SUB_PANEL, flex: '1 1 360px', minWidth: 300 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>
                  {t('analytics.live.anonymousTitle')}
                </Typography>
                <Chip
                  size="small"
                  label={data.anonymous.active}
                  color={data.anonymous.active > 0 ? 'secondary' : 'default'}
                  sx={{ height: 20, fontSize: 11, fontWeight: 700 }}
                />
              </Box>

              {!data.anonymous.configured ? (
                <Typography sx={{ fontSize: 12, color: 'var(--text-2)', py: 1 }}>
                  {t('analytics.live.umamiUnconfigured')}
                </Typography>
              ) : (
                <Box>
                  <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1 }}>
                    <Typography sx={{ fontSize: 24, fontWeight: 800, color: 'var(--text-0)' }}>
                      {data.anonymous.active}
                    </Typography>
                    <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>
                      {t('analytics.live.activeVisitorsNow')}
                    </Typography>
                  </Box>

                  <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                    {/* Top Pages Last 30m */}
                    <Box sx={{ flex: '1 1 140px', minWidth: 130 }}>
                      <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-1)', mb: 0.5 }}>
                        {t('analytics.live.topPages30m')}
                      </Typography>
                      {data.anonymous.pages.length === 0 ? (
                        <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>
                          {t('analytics.noData')}
                        </Typography>
                      ) : (
                        <Table size="small">
                          <TableBody>
                            {data.anonymous.pages.slice(0, 5).map((p) => (
                              <TableRow key={p.name}>
                                <TableCell sx={{ fontSize: 11, py: 0.25, border: 0, wordBreak: 'break-all' }}>
                                  {p.name}
                                </TableCell>
                                <TableCell align="right" sx={{ fontSize: 11, py: 0.25, border: 0, fontWeight: 600 }}>
                                  {p.count}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      )}
                    </Box>

                    {/* Top Countries Last 30m */}
                    <Box sx={{ flex: '1 1 140px', minWidth: 130 }}>
                      <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-1)', mb: 0.5 }}>
                        {t('analytics.live.topCountries30m')}
                      </Typography>
                      {data.anonymous.countries.length === 0 ? (
                        <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>
                          {t('analytics.noData')}
                        </Typography>
                      ) : (
                        <Table size="small">
                          <TableBody>
                            {data.anonymous.countries.slice(0, 5).map((c) => (
                              <TableRow key={c.name}>
                                <TableCell sx={{ fontSize: 11, py: 0.25, border: 0 }}>
                                  {countryName(c.name, i18n.language)}
                                </TableCell>
                                <TableCell align="right" sx={{ fontSize: 11, py: 0.25, border: 0, fontWeight: 600 }}>
                                  {c.count}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      )}
                    </Box>
                  </Box>
                </Box>
              )}
            </Paper>
          </Box>

          {/* Bottom Section: Live Event Feed */}
          <Paper elevation={0} sx={{ ...SUB_PANEL }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
              <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>
                {t('analytics.live.feedTitle')}
              </Typography>
              <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>
                {t('analytics.live.eventCount', { count: data.events.length })}
              </Typography>
            </Box>

            {data.events.length === 0 ? (
              <Typography sx={{ fontSize: 12, color: 'var(--text-2)', py: 1 }}>
                {t('analytics.live.noEvents')}
              </Typography>
            ) : (
              <Box sx={{ maxHeight: 280, overflowY: 'auto' }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ fontSize: 11, py: 0.5, bgcolor: 'var(--panel-1)', color: 'var(--text-2)', border: 0 }}>
                        {t('verification.col.when')}
                      </TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.5, bgcolor: 'var(--panel-1)', color: 'var(--text-2)', border: 0 }}>
                        {t('analytics.live.eventType')}
                      </TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.5, bgcolor: 'var(--panel-1)', color: 'var(--text-2)', border: 0 }}>
                        {t('activity.user')}
                      </TableCell>
                      <TableCell sx={{ fontSize: 11, py: 0.5, bgcolor: 'var(--panel-1)', color: 'var(--text-2)', border: 0 }}>
                        {t('verification.col.summary')}
                      </TableCell>
                      <TableCell align="right" sx={{ fontSize: 11, py: 0.5, bgcolor: 'var(--panel-1)', color: 'var(--text-2)', border: 0 }}>
                        {t('verification.col.country')}
                      </TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {data.events.map((ev: LiveEvent) => {
                      const chip = eventChipProps(ev.type);
                      return (
                        <TableRow key={ev.id}>
                          <TableCell sx={{ fontSize: 11, py: 0.5, border: 0, color: 'var(--text-2)', whiteSpace: 'nowrap' }}>
                            <Tooltip title={ev.iso || ''}>
                              <span>{formatRelativeTime(ev.ts, nowSec)}</span>
                            </Tooltip>
                          </TableCell>
                          <TableCell sx={{ fontSize: 11, py: 0.5, border: 0 }}>
                            <Chip
                              size="small"
                              color={chip.color}
                              label={t(chip.labelKey, { defaultValue: ev.type })}
                              sx={{ height: 20, fontSize: 10, fontWeight: 600 }}
                            />
                          </TableCell>
                          <TableCell sx={{ fontSize: 11, py: 0.5, border: 0, color: 'var(--text-0)', fontWeight: ev.user ? 600 : 400 }}>
                            {ev.user || t('analytics.live.anonymousUser')}
                          </TableCell>
                          <TableCell sx={{ fontSize: 11, py: 0.5, border: 0, color: 'var(--text-1)', wordBreak: 'break-all' }}>
                            {ev.details || '–'}
                          </TableCell>
                          <TableCell align="right" sx={{ fontSize: 11, py: 0.5, border: 0, color: 'var(--text-2)', whiteSpace: 'nowrap' }}>
                            {ev.country ? countryName(ev.country, i18n.language) : '–'}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </Box>
            )}
          </Paper>
        </>
      )}
    </Paper>
  );
};

export default LiveAnalyticsCard;
