/**
 * Admin · Users — one compact table (search, role filter, sortable columns);
 * clicking a row opens a side drawer with details and actions. Accounts live
 * in OUR registry (backend config/users.json, /api/auth/users).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Typography, Paper, Chip, Button, CircularProgress, Select, MenuItem,
  Table, TableBody, TableCell, TableHead, TableRow, TextField, TableSortLabel,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import MailOutlineIcon from '@mui/icons-material/MailOutline';
import { ConfirmDialog, type ConfirmState } from '../../common/PromptDialogs';
import { CreateUserDialog, ResetPasswordDialog } from '../dialogs/AccountDialogs';
import MotorsDialog, { type RegistryUser } from '../dialogs/MotorsDialog';
import { ROLES } from '../dialogs/InviteDialog';
import UserDrawer from '../UserDrawer';
import { useTranslation } from 'react-i18next';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const ROLE_COLOR: Record<string, string> = {
  anon: 'var(--text-4)', user: 'var(--text-3)', admin: '#fbbf24',
};
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

const fmtCreated = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';

interface SessionRow { email: string; lastSeen: number; revoked: boolean }
interface UsageRow { key: string; jobs: number; cpu_h: number }

type SortKey = 'email' | 'role' | 'created' | 'lastLogin';

const UsersSection: React.FC<{ onInvite: (email?: string) => void; notice: string | null; setNotice: (m: string | null) => void }> = ({
  onInvite, notice, setNotice,
}) => {
  const { t } = useTranslation('admin');
  const [users, setUsers] = useState<RegistryUser[]>([]);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [usage, setUsage] = useState<UsageRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [resetFor, setResetFor] = useState<string | null>(null);
  const [motorsFor, setMotorsFor] = useState<RegistryUser | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<string>('all');
  const [sortKey, setSortKey] = useState<SortKey>('created');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [u, s, us] = await Promise.all([
        fetch(`${API}/api/auth/users`).then((r) => { if (!r.ok) throw new Error(`users HTTP ${r.status}`); return r.json(); }),
        fetch(`${API}/api/admin/sessions`).then((r) => (r.ok ? r.json() : { sessions: [] })).catch(() => ({ sessions: [] })),
        fetch(`${API}/api/admin/usage?by=user&days=30`).then((r) => (r.ok ? r.json() : { rows: [] })).catch(() => ({ rows: [] })),
      ]);
      setUsers(u.users || []); setSessions(s.sessions ?? []); setUsage(us.rows ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to load');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const sessionsByEmail = useMemo(() => {
    const m = new Map<string, SessionRow[]>();
    for (const s of sessions) m.set(s.email, [...(m.get(s.email) ?? []), s]);
    return m;
  }, [sessions]);
  const usageByEmail = useMemo(() => new Map(usage.map((r) => [r.key, r])), [usage]);

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
          if (r.ok) { setUsers((us) => us.filter((u) => u.email !== email)); setSelected(null); }
        } catch { /* keep prior state */ } finally { setBusy(null); }
      })();
    },
  });

  const revokeAllSessions = async (email: string) => {
    setBusy(email);
    try {
      await fetch(`${API}/api/admin/users/${encodeURIComponent(email)}/revoke_all`, { method: 'POST' });
      await load();
    } finally { setBusy(null); }
  };

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let rows = users.filter((u) =>
      (roleFilter === 'all' || u.role === roleFilter) &&
      (!q || u.email.toLowerCase().includes(q) || (u.name ?? '').toLowerCase().includes(q)));
    const lastLogin = (email: string) => {
      const live = (sessionsByEmail.get(email) ?? []).filter((s) => !s.revoked);
      return live.length ? Math.max(...live.map((s) => s.lastSeen)) : 0;
    };
    rows = [...rows].sort((a, b) => {
      let cmp = 0;
      if (sortKey === 'email') cmp = a.email.localeCompare(b.email);
      else if (sortKey === 'role') cmp = a.role.localeCompare(b.role);
      else if (sortKey === 'created') cmp = (a.created ?? '').localeCompare(b.created ?? '');
      else if (sortKey === 'lastLogin') cmp = lastLogin(a.email) - lastLogin(b.email);
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return rows;
  }, [users, search, roleFilter, sortKey, sortDir, sessionsByEmail]);

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(k); setSortDir('asc'); }
  };

  const selectedUser = users.find((u) => u.email === selected) ?? null;
  const selLive = selected ? (sessionsByEmail.get(selected) ?? []).filter((s) => !s.revoked).length : 0;
  const selLastLogin = selected ? (() => {
    const live = (sessionsByEmail.get(selected) ?? []).filter((s) => !s.revoked);
    return live.length ? Math.max(...live.map((s) => s.lastSeen)) : null;
  })() : null;
  const selUsage = selected ? usageByEmail.get(selected) : undefined;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>Users</Typography>
        {notice && <Typography sx={{ fontSize: 11, color: '#34d399' }}>✓ {notice}</Typography>}
        <Box sx={{ flex: 1 }} />
        <TextField size="small" placeholder="Search email or name" value={search}
          onChange={(e) => setSearch(e.target.value)} sx={{ minWidth: 200, '& .MuiInputBase-input': { fontSize: 12.5, py: 0.6 } }} />
        <Select size="small" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)} sx={{ fontSize: 12.5 }}>
          <MenuItem value="all" sx={{ fontSize: 12.5 }}>All roles</MenuItem>
          {ROLES.map((r) => <MenuItem key={r} value={r} sx={{ fontSize: 12.5 }}>{r}</MenuItem>)}
        </Select>
        <Button size="small" startIcon={<MailOutlineIcon sx={{ fontSize: 16 }} />} onClick={() => onInvite()}
          variant="outlined" sx={{ textTransform: 'none', fontSize: 12 }}>Invite</Button>
        <Button size="small" startIcon={<PersonAddIcon sx={{ fontSize: 16 }} />} onClick={() => setCreateOpen(true)}
          variant="outlined" sx={{ textTransform: 'none', fontSize: 12 }}>Add account</Button>
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} onClick={() => void load()} disabled={loading}
          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>Refresh</Button>
      </Box>

      {loading && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, color: 'var(--text-3)', py: 4 }}>
          <CircularProgress size={18} /> <Typography sx={{ fontSize: 13 }}>Loading users…</Typography>
        </Box>
      )}
      {error && !loading && (
        <Paper sx={{ ...PANEL, borderColor: '#7f1d1d', p: 2 }}>
          <Typography sx={{ color: '#f87171', fontSize: 13, fontWeight: 700 }}>Couldn't load admin data</Typography>
          <Typography sx={{ color: 'var(--text-2)', fontSize: 12, mt: 0.5 }}>{error}</Typography>
        </Paper>
      )}

      {!loading && !error && (
        <Paper sx={{ ...PANEL, p: 0, overflow: 'hidden' }}>
          <Table size="small" sx={{
            '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
            '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
          }}>
            <TableHead>
              <TableRow>
                <TableCell><TableSortLabel active={sortKey === 'email'} direction={sortDir} onClick={() => toggleSort('email')}>User</TableSortLabel></TableCell>
                <TableCell><TableSortLabel active={sortKey === 'role'} direction={sortDir} onClick={() => toggleSort('role')}>Role</TableSortLabel></TableCell>
                <TableCell><TableSortLabel active={sortKey === 'created'} direction={sortDir} onClick={() => toggleSort('created')}>Created</TableSortLabel></TableCell>
                <TableCell><TableSortLabel active={sortKey === 'lastLogin'} direction={sortDir} onClick={() => toggleSort('lastLogin')}>Last login</TableSortLabel></TableCell>
                <TableCell align="right">CPU-h 30 d</TableCell>
                <TableCell align="center">Motors</TableCell>
                <TableCell>{t('motors.colDefault')}</TableCell>
                <TableCell align="center">Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map((u) => {
                const live = (sessionsByEmail.get(u.email) ?? []).filter((s) => !s.revoked);
                const last = live.length ? Math.max(...live.map((s) => s.lastSeen)) : 0;
                const uh = usageByEmail.get(u.email);
                const g = u.motors;
                const motorsLabel = u.role === 'admin' ? 'all' : g?.all ? 'all' : g?.dies?.length ? String(g.dies.length) : '—';
                return (
                  <TableRow key={u.email} hover onClick={() => setSelected(u.email)}
                    sx={{ opacity: u.disabled ? 0.55 : 1, cursor: 'pointer' }}>
                    <TableCell>
                      <Typography sx={{ fontSize: 13, color: 'var(--text-0)', fontWeight: 600 }}>{u.email}</Typography>
                    </TableCell>
                    <TableCell>
                      <Chip size="small" label={u.role} sx={{ height: 18, fontSize: 11, fontWeight: 700, bgcolor: 'var(--panel)', color: ROLE_COLOR[u.role] ?? 'var(--text-2)' }} />
                    </TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{fmtCreated(u.created)}</TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{last ? new Date(last * 1000).toLocaleDateString() : '—'}</TableCell>
                    <TableCell align="right" sx={{ color: 'var(--text-2)' }}>{uh ? uh.cpu_h.toFixed(1) : '—'}</TableCell>
                    <TableCell align="center" sx={{ color: 'var(--text-3)' }}>{motorsLabel}</TableCell>
                    <TableCell sx={{ color: 'var(--text-2)', whiteSpace: 'nowrap' }}>
                      {g?.default ? `${g.default.die} / ${g.default.config}` : '—'}
                    </TableCell>
                    <TableCell align="center">
                      <Chip size="small" label={u.disabled ? 'disabled' : 'active'}
                        sx={{ height: 18, fontSize: 10, bgcolor: 'var(--panel)', color: u.disabled ? '#f87171' : '#4ade80' }} />
                    </TableCell>
                  </TableRow>
                );
              })}
              {filtered.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>
                    {users.length === 0
                      ? 'No accounts yet — Google sign-ins appear here automatically; password accounts via "Add account".'
                      : 'No accounts match this search/filter.'}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <UserDrawer
        user={selectedUser}
        liveSessions={selLive}
        lastLogin={selLastLogin}
        cpuH30d={selUsage?.cpu_h ?? null}
        jobs30d={selUsage?.jobs ?? null}
        busy={busy === selected}
        onClose={() => setSelected(null)}
        onRole={(role) => void patchUser(selected!, { role })}
        onToggleDisabled={() => void patchUser(selected!, { disabled: !selectedUser?.disabled })}
        onDelete={() => selected && deleteUser(selected)}
        onResetPassword={() => selected && setResetFor(selected)}
        onOpenMotors={() => selectedUser && setMotorsFor(selectedUser)}
        onRevokeAllSessions={() => selected && void revokeAllSessions(selected)}
      />

      <CreateUserDialog open={createOpen} onClose={() => setCreateOpen(false)}
        onCreated={() => { setNotice('account created'); void load(); }} />
      <ResetPasswordDialog email={resetFor} onClose={() => setResetFor(null)}
        onDone={(m) => setNotice(m)} />
      <MotorsDialog user={motorsFor} onClose={() => setMotorsFor(null)}
        onSaved={(email, motors) => {
          setUsers((us) => us.map((u) => (u.email === email ? { ...u, motors } : u)));
          setNotice(`motors updated for ${email}`);
        }} />
      <ConfirmDialog state={confirm} onClose={() => setConfirm(null)} />
    </Box>
  );
};

export default UsersSection;
