/**
 * AdminPanel — user management + usage statistics (admin only).
 *
 * Fetches /api/admin/users + /api/admin/stats (gated to tier=admin on the
 * backend). The data source is Firebase Auth + Firestore via the Admin SDK;
 * when that's unavailable (local dev / no credentials) the backend returns a
 * flagged MOCK dataset and we show a "demo data" banner.
 *
 * Actions: change a user's plan (tier custom claim) and disable / enable an
 * account. These are optimistic local updates; Refresh re-pulls the truth.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Typography, Paper, Chip, Button, CircularProgress, Select, MenuItem,
  Table, TableBody, TableCell, TableHead, TableRow, Tooltip,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import BlockIcon from '@mui/icons-material/Block';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as RcTooltip,
} from 'recharts';
import SupportSettings, { type SupportCfg } from './SupportSettings';
import ModulesPanel from './ModulesPanel';
import PassportManager from './PassportManager';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

const TIERS = ['free', 'pro', 'team', 'admin'] as const;
const TIER_COLOR: Record<string, string> = {
  anon: 'var(--text-4)', free: 'var(--text-3)', pro: '#3b82f6', team: '#a855f7', admin: '#fbbf24',
};

interface AdminUser {
  uid: string; email: string | null; displayName?: string;
  createdAt: number | null; lastLoginAt: number | null;
  disabled: boolean; tier: string; designCount: number;
}
interface Stats {
  source: string; total: number; disabled: number; designs: number;
  byTier: Record<string, number>; active7: number; active30: number;
  signups: { date: string; count: number; total: number }[];
}
interface AdminTicket {
  id: string; uid: string | null; type: string; title: string; description: string;
  status: string; email: string | null; createdAt: number | null;
}
const TICKET_STATUSES = ['open', 'in_progress', 'resolved', 'closed'] as const;
const T_STATUS_COLOR: Record<string, string> = { open: '#60a5fa', in_progress: '#fbbf24', resolved: '#4ade80', closed: 'var(--text-3)' };
const T_TYPE_COLOR: Record<string, string> = { bug: '#f87171', feature: '#a78bfa', question: 'var(--text-3)' };

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 2 } as const;
const CARD = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, px: 2, py: 1.25, flex: 1, minWidth: 130 } as const;
const LABEL = { fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' } as const;

const fmtDate = (ms?: number | null) =>
  ms ? new Date(ms).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const rel = (ms?: number | null) => {
  if (!ms) return 'never';
  const d = (Date.now() - ms) / 86_400_000;
  if (d < 1) return 'today';
  if (d < 2) return 'yesterday';
  if (d < 30) return `${Math.floor(d)}d ago`;
  if (d < 365) return `${Math.floor(d / 30)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
};

const StatCard: React.FC<{ label: string; value: React.ReactNode; sub?: string; color?: string }> =
  ({ label, value, sub, color }) => (
    <Box sx={CARD}>
      <Typography sx={LABEL}>{label}</Typography>
      <Typography sx={{ fontSize: 24, fontWeight: 800, color: color ?? 'var(--text-0)', lineHeight: 1.2 }}>{value}</Typography>
      {sub && <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{sub}</Typography>}
    </Box>
  );

const AdminPanel: React.FC = () => {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [source, setSource] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [tickets, setTickets] = useState<AdminTicket[]>([]);
  const [supportCfg, setSupportCfg] = useState<SupportCfg | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [u, s, tk, sc] = await Promise.all([
        fetch(`${API}/api/admin/users`).then((r) => { if (!r.ok) throw new Error(`users HTTP ${r.status}`); return r.json(); }),
        fetch(`${API}/api/admin/stats`).then((r) => { if (!r.ok) throw new Error(`stats HTTP ${r.status}`); return r.json(); }),
        fetch(`${API}/api/admin/tickets`).then((r) => (r.ok ? r.json() : { tickets: [] })).catch(() => ({ tickets: [] })),
        fetch(`${API}/api/admin/support`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      setUsers(u.users || []); setSource(u.source || ''); setStats(s); setTickets(tk.tickets || []); setSupportCfg(sc);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to load');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const changeTier = async (uid: string, tier: string) => {
    setBusy(uid);
    try {
      await fetch(`${API}/api/admin/users/${uid}/tier`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tier }),
      });
      setUsers((us) => us.map((u) => (u.uid === uid ? { ...u, tier } : u)));
    } catch { /* keep prior state */ } finally { setBusy(null); }
  };
  const toggleDisabled = async (uid: string, disabled: boolean) => {
    setBusy(uid);
    try {
      await fetch(`${API}/api/admin/users/${uid}/disable`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ disabled }),
      });
      setUsers((us) => us.map((u) => (u.uid === uid ? { ...u, disabled } : u)));
    } catch { /* keep prior state */ } finally { setBusy(null); }
  };
  const changeTicketStatus = async (t: AdminTicket, status: string) => {
    setBusy(t.id);
    try {
      await fetch(`${API}/api/admin/tickets/status`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uid: t.uid, id: t.id, status }),
      });
      setTickets((ts) => ts.map((x) => (x.id === t.id ? { ...x, status } : x)));
    } catch { /* keep prior state */ } finally { setBusy(null); }
  };

  const paid = useMemo(() => {
    const t = stats?.byTier ?? {};
    return (t.pro ?? 0) + (t.team ?? 0);
  }, [stats]);

  return (
    <Box sx={{ height: '100%', overflowY: 'auto', p: 2 }}>
      {/* header */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 18, fontWeight: 800, color: 'var(--text-0)' }}>Admin · Users &amp; statistics</Typography>
        {source === 'mock' && (
          <Tooltip title="Firebase Admin SDK isn't configured here — showing sample users. Real data appears in production once the Cloud Run service account has Firebase Auth access.">
            <Chip label="DEMO DATA" size="small" sx={{ bgcolor: '#422006', color: '#fbbf24', fontWeight: 700, fontSize: 10, height: 20 }} />
          </Tooltip>
        )}
        {source === 'firebase' && (
          <Chip label="LIVE" size="small" sx={{ bgcolor: 'var(--ok-bg)', color: '#4ade80', fontWeight: 700, fontSize: 10, height: 20 }} />
        )}
        <Box sx={{ flex: 1 }} />
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} onClick={() => void load()} disabled={loading}
          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>
          Refresh
        </Button>
      </Box>

      {loading && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, color: 'var(--text-3)', py: 4 }}>
          <CircularProgress size={18} /> <Typography sx={{ fontSize: 13 }}>Loading users…</Typography>
        </Box>
      )}
      {error && !loading && (
        <Paper sx={{ ...PANEL, borderColor: '#7f1d1d', mb: 2 }}>
          <Typography sx={{ color: '#f87171', fontSize: 13, fontWeight: 700 }}>Couldn't load admin data</Typography>
          <Typography sx={{ color: 'var(--text-2)', fontSize: 12, mt: 0.5 }}>{error}</Typography>
          <Typography sx={{ color: 'var(--text-4)', fontSize: 11, mt: 1 }}>
            In production this endpoint is admin-only — make sure you're signed in with an admin account.
          </Typography>
        </Paper>
      )}

      {!loading && !error && stats && (
        <>
          {/* summary cards */}
          <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', mb: 2 }}>
            <StatCard label="Total users" value={stats.total} sub={`${stats.disabled} disabled`} />
            <StatCard label="Active · 30d" value={stats.active30} sub={`${stats.active7} in last 7d`} color="#4ade80" />
            <StatCard label="Paid plans" value={paid} sub="pro + team" color="#3b82f6" />
            <StatCard label="Saved designs" value={stats.designs} sub="across all users" color="#a78bfa" />
          </Box>


          {/* tier breakdown + signups */}
          <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', mb: 2 }}>
            <Paper sx={{ ...PANEL, flex: '1 1 280px', minWidth: 260 }}>
              <Typography sx={{ ...LABEL, mb: 1 }}>Plan breakdown</Typography>
              <Box sx={{ display: 'flex', height: 14, borderRadius: 1, overflow: 'hidden', mb: 1.5 }}>
                {TIERS.map((t) => {
                  const n = stats.byTier[t] ?? 0;
                  const pct = stats.total ? (n / stats.total) * 100 : 0;
                  return pct > 0 ? <Box key={t} sx={{ width: `${pct}%`, bgcolor: TIER_COLOR[t] }} title={`${t}: ${n}`} /> : null;
                })}
              </Box>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1.5 }}>
                {TIERS.map((t) => (
                  <Box key={t} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Box sx={{ width: 10, height: 10, borderRadius: '2px', bgcolor: TIER_COLOR[t] }} />
                    <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t}</Typography>
                    <Typography sx={{ fontSize: 12, color: 'var(--text-0)', fontWeight: 700 }}>{stats.byTier[t] ?? 0}</Typography>
                  </Box>
                ))}
              </Box>
            </Paper>

            <Paper sx={{ ...PANEL, flex: '2 1 420px', minWidth: 320 }}>
              <Typography sx={{ ...LABEL, mb: 1 }}>Signups over time (cumulative)</Typography>
              <ResponsiveContainer width="100%" height={150}>
                <AreaChart data={stats.signups} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                  <defs>
                    <linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.5} />
                      <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--panel)" strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fill: 'var(--text-4)', fontSize: 9 }} minTickGap={28} />
                  <YAxis tick={{ fill: 'var(--text-4)', fontSize: 9 }} allowDecimals={false} width={28} />
                  <RcTooltip contentStyle={{ backgroundColor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 6, fontSize: 11 }}
                    labelStyle={{ color: 'var(--text-2)' }} formatter={(v: number) => [v, 'total users']} />
                  <Area type="monotone" dataKey="total" stroke="#3b82f6" strokeWidth={1.25} fill="url(#sg)" />
                </AreaChart>
              </ResponsiveContainer>
            </Paper>
          </Box>

          {/* user table */}
          <Paper sx={{ ...PANEL, p: 0, overflow: 'hidden' }}>
            <Table size="small" sx={{
              '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
              '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
            }}>
              <TableHead>
                <TableRow>
                  <TableCell>User</TableCell>
                  <TableCell>Plan</TableCell>
                  <TableCell>Signed up</TableCell>
                  <TableCell>Last seen</TableCell>
                  <TableCell align="right">Designs</TableCell>
                  <TableCell align="center">Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {users.map((u) => (
                  <TableRow key={u.uid} hover sx={{ opacity: u.disabled ? 0.55 : 1 }}>
                    <TableCell>
                      <Typography sx={{ fontSize: 13, color: 'var(--text-0)', fontWeight: 600 }}>{u.email ?? u.uid}</Typography>
                      {u.displayName && u.displayName !== (u.email ?? '').split('@')[0] && (
                        <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>{u.displayName}</Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Select
                        value={u.tier} variant="standard" disableUnderline disabled={busy === u.uid}
                        onChange={(e) => void changeTier(u.uid, e.target.value)}
                        sx={{
                          fontSize: 12, fontWeight: 700, color: TIER_COLOR[u.tier] ?? 'var(--text-2)',
                          '& .MuiSelect-select': { py: 0.25, pr: '20px !important' },
                          '& svg': { color: 'var(--text-4)' },
                        }}
                      >
                        {TIERS.map((t) => (
                          <MenuItem key={t} value={t} sx={{ fontSize: 12, color: TIER_COLOR[t] }}>{t}</MenuItem>
                        ))}
                      </Select>
                    </TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{fmtDate(u.createdAt)}</TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{rel(u.lastLoginAt)}</TableCell>
                    <TableCell align="right" sx={{ color: u.designCount ? 'var(--text-0)' : 'var(--text-4)', fontWeight: 600 }}>{u.designCount}</TableCell>
                    <TableCell align="center">
                      {u.disabled ? (
                        <Button size="small" disabled={busy === u.uid} onClick={() => void toggleDisabled(u.uid, false)}
                          startIcon={<CheckCircleIcon sx={{ fontSize: 14 }} />}
                          sx={{ color: '#4ade80', textTransform: 'none', fontSize: 11, minWidth: 0 }}>
                          Enable
                        </Button>
                      ) : (
                        <Button size="small" disabled={busy === u.uid} onClick={() => void toggleDisabled(u.uid, true)}
                          startIcon={<BlockIcon sx={{ fontSize: 14 }} />}
                          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 11, minWidth: 0, '&:hover': { color: '#f87171' } }}>
                          Disable
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
                {users.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>
                      No users yet.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </Paper>

          {/* support tickets */}
          <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mt: 3, mb: 1 }}>
            <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>Support tickets</Typography>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
              {tickets.length} · {tickets.filter((t) => t.status === 'open').length} open
            </Typography>
          </Box>
          <Paper sx={{ ...PANEL, p: 0, overflow: 'hidden' }}>
            <Table size="small" sx={{
              '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
              '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
            }}>
              <TableHead>
                <TableRow>
                  <TableCell>Type</TableCell>
                  <TableCell>Title</TableCell>
                  <TableCell>User</TableCell>
                  <TableCell>Created</TableCell>
                  <TableCell>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {tickets.map((t) => (
                  <TableRow key={t.id} hover>
                    <TableCell>
                      <Chip label={t.type} size="small" sx={{ height: 18, fontSize: 9.5, bgcolor: 'var(--panel-2)', color: T_TYPE_COLOR[t.type] ?? 'var(--text-3)' }} />
                    </TableCell>
                    <TableCell>
                      <Typography sx={{ fontSize: 13, color: 'var(--text-0)', fontWeight: 600 }}>{t.title}</Typography>
                      {t.description && (
                        <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)', maxWidth: 380, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {t.description}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{t.email ?? t.uid}</TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{fmtDate(t.createdAt)}</TableCell>
                    <TableCell>
                      <Select
                        value={t.status} variant="standard" disableUnderline disabled={busy === t.id}
                        onChange={(e) => void changeTicketStatus(t, e.target.value)}
                        sx={{
                          fontSize: 12, fontWeight: 700, color: T_STATUS_COLOR[t.status] ?? 'var(--text-2)',
                          '& .MuiSelect-select': { py: 0.25, pr: '20px !important' }, '& svg': { color: 'var(--text-4)' },
                        }}
                      >
                        {TICKET_STATUSES.map((s) => (
                          <MenuItem key={s} value={s} sx={{ fontSize: 12, color: T_STATUS_COLOR[s] }}>{s.replace('_', ' ')}</MenuItem>
                        ))}
                      </Select>
                    </TableCell>
                  </TableRow>
                ))}
                {tickets.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>No tickets yet.</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </Paper>

          {supportCfg && <SupportSettings cfg={supportCfg} onSaved={() => void load()} />}

          <PassportManager />

          <ModulesPanel />
        </>
      )}
    </Box>
  );
};

export default AdminPanel;
