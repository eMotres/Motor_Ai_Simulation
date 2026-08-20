/**
 * AdminPanel — user management + usage statistics (admin only).
 *
 * Accounts live in OUR registry (backend config/users.json, /api/auth/users) —
 * the Firebase Admin SDK era is over. Admin actions: create account, change
 * plan (tier), disable/enable, reset password, delete. Stats are computed
 * from the registry itself.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Typography, Paper, Chip, Button, CircularProgress, Select, MenuItem,
  Table, TableBody, TableCell, TableHead, TableRow, Tooltip,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import BlockIcon from '@mui/icons-material/Block';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import KeyIcon from '@mui/icons-material/Key';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as RcTooltip,
} from 'recharts';
import SupportSettings, { type SupportCfg } from './SupportSettings';
import ModulesPanel from './ModulesPanel';
import PassportManager from './PassportManager';
import { ConfirmDialog, type ConfirmState } from '../common/PromptDialogs';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

const TIERS = ['free', 'pro', 'team', 'admin'] as const;
const TIER_COLOR: Record<string, string> = {
  anon: 'var(--text-4)', free: 'var(--text-3)', pro: '#3b82f6', team: '#a855f7', admin: '#fbbf24',
};

interface RegistryUser {
  email: string; tier: string; name: string; disabled: boolean; created?: string | null;
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

const fmtCreated = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
const fmtDate = (ms?: number | null) =>
  ms ? new Date(ms).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';

const StatCard: React.FC<{ label: string; value: React.ReactNode; sub?: string; color?: string }> =
  ({ label, value, sub, color }) => (
    <Box sx={CARD}>
      <Typography sx={LABEL}>{label}</Typography>
      <Typography sx={{ fontSize: 24, fontWeight: 800, color: color ?? 'var(--text-0)', lineHeight: 1.2 }}>{value}</Typography>
      {sub && <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{sub}</Typography>}
    </Box>
  );

// ── Create / reset-password dialogs ─────────────────────────────────────────

const CreateUserDialog: React.FC<{
  open: boolean; onClose: () => void; onCreated: () => void;
}> = ({ open, onClose, onCreated }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [tier, setTier] = useState<string>('free');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/auth/users`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password, tier, name: name.trim() }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(j.detail ?? `HTTP ${r.status}`); return; }
      setEmail(''); setPassword(''); setName(''); setTier('free');
      onClose(); onCreated();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem' }}>New account</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: '8px !important' }}>
        <TextField size="small" label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        <TextField size="small" label="Password (min 8 chars)" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <TextField size="small" label="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
        <Select size="small" value={tier} onChange={(e) => setTier(e.target.value)}>
          {TIERS.map((t) => <MenuItem key={t} value={t} sx={{ fontSize: 13 }}>{t}</MenuItem>)}
        </Select>
        {err && <Typography variant="caption" color="error">{err}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={{ textTransform: 'none' }}>Cancel</Button>
        <Button variant="contained" disabled={busy || !email.trim() || password.length < 8}
          onClick={() => void submit()} sx={{ textTransform: 'none' }}>Create</Button>
      </DialogActions>
    </Dialog>
  );
};

const ResetPasswordDialog: React.FC<{
  email: string | null; onClose: () => void; onDone: (msg: string) => void;
}> = ({ email, onClose, onDone }) => {
  const [password, setPassword] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setPassword(''); setErr(null); }, [email]);

  const submit = async () => {
    if (busy || !email) return;
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/auth/users/${encodeURIComponent(email)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(j.detail ?? `HTTP ${r.status}`); return; }
      onClose(); onDone(`password reset for ${email}`);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  return (
    <Dialog open={!!email} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem' }}>Reset password — {email}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: '8px !important' }}>
        <TextField size="small" label="New password (min 8 chars)" type="password"
          value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
        {err && <Typography variant="caption" color="error">{err}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={{ textTransform: 'none' }}>Cancel</Button>
        <Button variant="contained" disabled={busy || password.length < 8}
          onClick={() => void submit()} sx={{ textTransform: 'none' }}>Reset</Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Panel ───────────────────────────────────────────────────────────────────

const AdminPanel: React.FC = () => {
  const [users, setUsers] = useState<RegistryUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tickets, setTickets] = useState<AdminTicket[]>([]);
  const [supportCfg, setSupportCfg] = useState<SupportCfg | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [resetFor, setResetFor] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [u, tk, sc] = await Promise.all([
        fetch(`${API}/api/auth/users`).then((r) => { if (!r.ok) throw new Error(`users HTTP ${r.status}`); return r.json(); }),
        fetch(`${API}/api/admin/tickets`).then((r) => (r.ok ? r.json() : { tickets: [] })).catch(() => ({ tickets: [] })),
        fetch(`${API}/api/admin/support`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      // Ticket storage was Firestore; without it the backend serves a flagged
      // mock set — never show invented tickets as if they were real.
      setUsers(u.users || []); setTickets(tk.source === 'mock' ? [] : (tk.tickets || [])); setSupportCfg(sc);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to load');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const patchUser = async (email: string, body: object) => {
    setBusy(email);
    try {
      const r = await fetch(`${API}/api/auth/users/${encodeURIComponent(email)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (r.ok) {
        const j = await r.json();
        setUsers((us) => us.map((u) => (u.email === email ? { ...u, ...j.user } : u)));
      }
    } catch { /* keep prior state */ } finally { setBusy(null); }
  };

  const deleteUser = (email: string) => setConfirm({
    title: `Delete account ${email}?`,
    body: 'The account and its password are removed permanently. Their saved motors/config data are not touched.',
    onConfirm: () => {
      void (async () => {
        setBusy(email);
        try {
          const r = await fetch(`${API}/api/auth/users/${encodeURIComponent(email)}`, { method: 'DELETE' });
          if (r.ok) setUsers((us) => us.filter((u) => u.email !== email));
        } catch { /* keep prior state */ } finally { setBusy(null); }
      })();
    },
  });

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

  // Stats straight from the registry.
  const byTier = useMemo(() => {
    const m: Record<string, number> = {};
    for (const u of users) m[u.tier] = (m[u.tier] ?? 0) + 1;
    return m;
  }, [users]);
  const disabledCount = useMemo(() => users.filter((u) => u.disabled).length, [users]);
  const paid = (byTier.pro ?? 0) + (byTier.team ?? 0);
  const signups = useMemo(() => {
    const dated = users
      .map((u) => (u.created ? u.created.slice(0, 10) : null))
      .filter((d): d is string => !!d)
      .sort();
    const out: { date: string; total: number }[] = [];
    let total = 0;
    for (const d of dated) {
      total += 1;
      if (out.length && out[out.length - 1].date === d) out[out.length - 1].total = total;
      else out.push({ date: d, total });
    }
    return out;
  }, [users]);

  return (
    <Box sx={{ height: '100%', overflowY: 'auto', p: 2 }}>
      {/* header */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 18, fontWeight: 800, color: 'var(--text-0)' }}>Admin · Users &amp; statistics</Typography>
        <Box sx={{ flex: 1 }} />
        {notice && <Typography sx={{ fontSize: 11, color: '#34d399' }}>✓ {notice}</Typography>}
        <Button size="small" startIcon={<PersonAddIcon sx={{ fontSize: 16 }} />} onClick={() => setCreateOpen(true)}
          variant="outlined" sx={{ textTransform: 'none', fontSize: 12 }}>
          Add account
        </Button>
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
            This endpoint is admin-only — make sure you're signed in with an admin account.
          </Typography>
        </Paper>
      )}

      {!loading && !error && (
        <>
          {/* summary cards */}
          <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', mb: 2 }}>
            <StatCard label="Total accounts" value={users.length} sub={`${disabledCount} disabled`} />
            <StatCard label="Paid plans" value={paid} sub="pro + team" color="#3b82f6" />
            <StatCard label="Admins" value={byTier.admin ?? 0} sub="registry tier (ADMIN_EMAILS extra)" color="#fbbf24" />
          </Box>

          {/* tier breakdown + signups */}
          <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', mb: 2 }}>
            <Paper sx={{ ...PANEL, flex: '1 1 280px', minWidth: 260 }}>
              <Typography sx={{ ...LABEL, mb: 1 }}>Plan breakdown</Typography>
              <Box sx={{ display: 'flex', height: 14, borderRadius: 1, overflow: 'hidden', mb: 1.5 }}>
                {TIERS.map((t) => {
                  const n = byTier[t] ?? 0;
                  const pct = users.length ? (n / users.length) * 100 : 0;
                  return pct > 0 ? <Box key={t} sx={{ width: `${pct}%`, bgcolor: TIER_COLOR[t] }} title={`${t}: ${n}`} /> : null;
                })}
              </Box>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1.5 }}>
                {TIERS.map((t) => (
                  <Box key={t} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Box sx={{ width: 10, height: 10, borderRadius: '2px', bgcolor: TIER_COLOR[t] }} />
                    <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t}</Typography>
                    <Typography sx={{ fontSize: 12, color: 'var(--text-0)', fontWeight: 700 }}>{byTier[t] ?? 0}</Typography>
                  </Box>
                ))}
              </Box>
            </Paper>

            <Paper sx={{ ...PANEL, flex: '2 1 420px', minWidth: 320 }}>
              <Typography sx={{ ...LABEL, mb: 1 }}>Signups over time (cumulative)</Typography>
              <ResponsiveContainer width="100%" height={150}>
                <AreaChart data={signups} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
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
                    labelStyle={{ color: 'var(--text-2)' }} formatter={(v: number) => [v, 'total accounts']} />
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
                  <TableCell>Created</TableCell>
                  <TableCell align="center">Status</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {users.map((u) => (
                  <TableRow key={u.email} hover sx={{ opacity: u.disabled ? 0.55 : 1 }}>
                    <TableCell>
                      <Typography sx={{ fontSize: 13, color: 'var(--text-0)', fontWeight: 600 }}>{u.email}</Typography>
                      {u.name && u.name !== u.email.split('@')[0] && (
                        <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>{u.name}</Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Select
                        value={u.tier} variant="standard" disableUnderline disabled={busy === u.email}
                        onChange={(e) => void patchUser(u.email, { tier: e.target.value })}
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
                    <TableCell sx={{ color: 'var(--text-2)' }}>{fmtCreated(u.created)}</TableCell>
                    <TableCell align="center">
                      {u.disabled ? (
                        <Button size="small" disabled={busy === u.email} onClick={() => void patchUser(u.email, { disabled: false })}
                          startIcon={<CheckCircleIcon sx={{ fontSize: 14 }} />}
                          sx={{ color: '#4ade80', textTransform: 'none', fontSize: 11, minWidth: 0 }}>
                          Enable
                        </Button>
                      ) : (
                        <Button size="small" disabled={busy === u.email} onClick={() => void patchUser(u.email, { disabled: true })}
                          startIcon={<BlockIcon sx={{ fontSize: 14 }} />}
                          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 11, minWidth: 0, '&:hover': { color: '#f87171' } }}>
                          Disable
                        </Button>
                      )}
                    </TableCell>
                    <TableCell align="right">
                      <Tooltip title="Reset password" arrow>
                        <span>
                          <Button size="small" disabled={busy === u.email} onClick={() => setResetFor(u.email)}
                            sx={{ color: 'var(--text-3)', minWidth: 0, px: 0.5 }}>
                            <KeyIcon sx={{ fontSize: 16 }} />
                          </Button>
                        </span>
                      </Tooltip>
                      <Tooltip title="Delete account" arrow>
                        <span>
                          <Button size="small" disabled={busy === u.email} onClick={() => deleteUser(u.email)}
                            sx={{ color: 'var(--text-3)', minWidth: 0, px: 0.5, '&:hover': { color: '#f87171' } }}>
                            <DeleteOutlineIcon sx={{ fontSize: 16 }} />
                          </Button>
                        </span>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
                {users.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>
                      No accounts yet — Google sign-ins appear here automatically; password accounts via “Add account”.
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

      <CreateUserDialog open={createOpen} onClose={() => setCreateOpen(false)}
        onCreated={() => { setNotice('account created'); void load(); }} />
      <ResetPasswordDialog email={resetFor} onClose={() => setResetFor(null)}
        onDone={(m) => setNotice(m)} />
      <ConfirmDialog state={confirm} onClose={() => setConfirm(null)} />
    </Box>
  );
};

export default AdminPanel;
