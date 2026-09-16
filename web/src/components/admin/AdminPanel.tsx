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
  Checkbox, FormControlLabel, Switch,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import BlockIcon from '@mui/icons-material/Block';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import KeyIcon from '@mui/icons-material/Key';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import MailOutlineIcon from '@mui/icons-material/MailOutline';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as RcTooltip,
} from 'recharts';
import SupportSettings, { type SupportCfg } from './SupportSettings';
import SessionsSection from './SessionsSection';
import ModulesPanel from './ModulesPanel';
import PassportManager from './PassportManager';
import { ConfirmDialog, type ConfirmState } from '../common/PromptDialogs';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

const TIERS = ['free', 'pro', 'team', 'admin'] as const;
const TIER_COLOR: Record<string, string> = {
  anon: 'var(--text-4)', free: 'var(--text-3)', pro: '#3b82f6', team: '#a855f7', admin: '#fbbf24',
};

/** Which catalog motors an account may open — registry data, not a tier. */
interface MotorGrants { all: boolean; dies: string[] }
interface RegistryUser {
  email: string; tier: string; name: string; disabled: boolean; created?: string | null;
  motors?: MotorGrants;
}
interface CatalogDie {
  name: string; stator_diameter: number | null;
  slots?: number | null; poles?: number | null;
  configs: number; duties: number;
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

// ── Motor access ────────────────────────────────────────────────────────────
// A new account is granted NOTHING and sees an empty catalog; the vendor picks
// its motors here. "All motors" also covers dies added later.

/** The catalog picker itself — shared by the grants dialog and the invite row,
 *  so a motor list that reads one way in one of them cannot read another way in
 *  the other. */
const MotorPicker: React.FC<{
  dies: CatalogDie[] | null; all: boolean; picked: Set<string>;
  onAll: (v: boolean) => void; onToggle: (name: string) => void;
}> = ({ dies, all, picked, onAll, onToggle }) => {
  // Grouped by stator diameter — the same hierarchy the Motors tab shows.
  const groups = useMemo(() => {
    const m = new Map<string, CatalogDie[]>();
    for (const d of dies ?? []) {
      const k = d.stator_diameter == null ? '—' : String(d.stator_diameter);
      m.set(k, [...(m.get(k) ?? []), d]);
    }
    return [...m.entries()].sort((a, b) => Number(a[0]) - Number(b[0]));
  }, [dies]);

  return (
    <>
      <FormControlLabel
        control={<Switch size="small" checked={all} onChange={(e) => onAll(e.target.checked)} />}
        label={<Typography sx={{ fontSize: 13 }}>All motors <span style={{ color: 'var(--text-4)' }}>(including ones added later)</span></Typography>}
      />
      {dies === null && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2, color: 'var(--text-3)' }}>
          <CircularProgress size={16} /> <Typography sx={{ fontSize: 12 }}>Loading catalog…</Typography>
        </Box>
      )}
      {dies !== null && groups.map(([dia, list]) => (
        <Box key={dia} sx={{ mt: 1.5, opacity: all ? 0.45 : 1 }}>
          <Typography sx={{ ...LABEL, color: '#60a5fa' }}>Ø {dia} mm</Typography>
          {list.map((d) => (
            <FormControlLabel key={d.name} sx={{ display: 'flex', ml: 0 }}
              control={<Checkbox size="small" disabled={all} checked={all || picked.has(d.name)}
                onChange={() => onToggle(d.name)} sx={{ py: 0.25 }} />}
              label={
                <Typography sx={{ fontSize: 12.5, color: 'var(--text-1)' }}>
                  {d.name}
                  <span style={{ color: 'var(--text-4)' }}>
                    {'  '}· {d.configs} config{d.configs === 1 ? '' : 's'}
                  </span>
                </Typography>
              } />
          ))}
        </Box>
      ))}
      {dies !== null && dies.length === 0 && (
        <Typography sx={{ fontSize: 12, color: 'var(--text-4)', py: 2 }}>
          The catalog has no dies yet.
        </Typography>
      )}
    </>
  );
};

/** Load the catalog once per dialog opening. */
const useCatalog = (open: boolean) => {
  const [dies, setDies] = useState<CatalogDie[] | null>(null);
  useEffect(() => {
    if (!open) return;
    setDies(null);
    fetch(`${API}/api/admin/motors`, { cache: 'no-store' })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((j) => setDies(j.dies ?? []))
      .catch(() => setDies([]));
  }, [open]);
  return dies;
};

const MotorsDialog: React.FC<{
  user: RegistryUser | null; onClose: () => void;
  onSaved: (email: string, motors: MotorGrants) => void;
}> = ({ user, onClose, onSaved }) => {
  const dies = useCatalog(!!user);
  const [all, setAll] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!user) return;
    setErr(null);
    setAll(user.motors?.all === true);
    setPicked(new Set(user.motors?.dies ?? []));
  }, [user]);

  const toggle = (name: string) => setPicked((s) => {
    const n = new Set(s);
    if (n.has(name)) n.delete(name); else n.add(name);
    return n;
  });

  const save = async () => {
    if (!user || busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/admin/users/${encodeURIComponent(user.email)}/motors`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ all, dies: [...picked] }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(j.detail ?? `HTTP ${r.status}`); return; }
      onSaved(user.email, j.motors as MotorGrants);
      onClose();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  return (
    <Dialog open={!!user} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem' }}>
        Motors — {user?.email}
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>
          The catalog this account sees. Nothing granted = empty catalog.
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ pt: '8px !important' }}>
        <MotorPicker dies={dies} all={all} picked={picked} onAll={setAll} onToggle={toggle} />
        {err && <Typography variant="caption" color="error" sx={{ display: 'block', mt: 1 }}>{err}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={{ textTransform: 'none' }}>Cancel</Button>
        <Button variant="contained" disabled={busy} onClick={() => void save()}
          sx={{ textTransform: 'none' }}>Save</Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Invite ──────────────────────────────────────────────────────────────────
// One call creates the account, its plan, its motors and its workspace. NO
// E-MAIL IS SENT (the host blocks outbound SMTP) — the admin is the messenger,
// which is why the dialog says so instead of implying a delivery.

const InviteDialog: React.FC<{
  open: boolean; onClose: () => void; onInvited: (msg: string) => void;
}> = ({ open, onClose, onInvited }) => {
  const dies = useCatalog(open);
  const [email, setEmail] = useState('');
  const [tier, setTier] = useState<string>('free');
  const [note, setNote] = useState('');
  const [all, setAll] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setEmail(''); setTier('free'); setNote(''); setAll(false);
    setPicked(new Set()); setErr(null);
  }, [open]);

  const toggle = (name: string) => setPicked((s) => {
    const n = new Set(s);
    if (n.has(name)) n.delete(name); else n.add(name);
    return n;
  });

  const submit = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/admin/invite`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim(), tier, note: note.trim(),
          motors: all ? 'all' : [...picked],
        }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(j.detail ?? `HTTP ${r.status}`); return; }
      onClose();
      onInvited(`${email.trim()} invited — tell them to sign in with Google (no e-mail was sent)`);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem' }}>
        Invite
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>
          Creates the account, its plan and its motors. No e-mail is sent — they sign in with Google.
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: '8px !important' }}>
        <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
          <Tooltip title="The Google address they will sign in with" arrow>
            <TextField size="small" label="Email" type="email" value={email} autoFocus
              onChange={(e) => setEmail(e.target.value)} sx={{ flex: '2 1 220px' }} />
          </Tooltip>
          <Select size="small" value={tier} onChange={(e) => setTier(e.target.value)} sx={{ flex: '0 0 110px' }}>
            {TIERS.map((t) => <MenuItem key={t} value={t} sx={{ fontSize: 13 }}>{t}</MenuItem>)}
          </Select>
          <TextField size="small" label="Note (optional)" value={note}
            onChange={(e) => setNote(e.target.value)} sx={{ flex: '2 1 200px' }} />
        </Box>
        <Box>
          <MotorPicker dies={dies} all={all} picked={picked} onAll={setAll} onToggle={toggle} />
        </Box>
        {err && <Typography variant="caption" color="error">{err}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={{ textTransform: 'none' }}>Cancel</Button>
        <Button variant="contained" disabled={busy || !email.trim()}
          onClick={() => void submit()} sx={{ textTransform: 'none' }}>Invite</Button>
      </DialogActions>
    </Dialog>
  );
};

const MotorsCell: React.FC<{ user: RegistryUser; onOpen: () => void }> = ({ user, onOpen }) => {
  const g = user.motors;
  const isAll = g?.all === true;
  const n = g?.dies?.length ?? 0;
  const label = isAll ? 'all' : n > 0 ? String(n) : '—';
  const tip = isAll ? 'Every motor in the catalog, present and future'
    : n > 0 ? `${g!.dies.join(', ')} — click to change`
      : 'No motors granted — this account sees an empty catalog';
  return (
    <Tooltip title={tip} arrow>
      <Chip label={label} size="small" clickable onClick={onOpen}
        sx={{
          height: 20, minWidth: 34, fontSize: 11, fontWeight: 700,
          bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)',
          color: isAll ? '#4ade80' : n > 0 ? 'var(--text-0)' : 'var(--text-4)',
        }} />
    </Tooltip>
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
  const [inviteOpen, setInviteOpen] = useState(false);
  const [resetFor, setResetFor] = useState<string | null>(null);
  const [motorsFor, setMotorsFor] = useState<RegistryUser | null>(null);
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
        <Tooltip title="Creates the account, its plan and its motors, and seeds its workspace. No e-mail is sent — tell them to sign in with Google with this address." arrow>
          <Button size="small" startIcon={<MailOutlineIcon sx={{ fontSize: 16 }} />} onClick={() => setInviteOpen(true)}
            variant="outlined" sx={{ textTransform: 'none', fontSize: 12 }}>
            Invite
          </Button>
        </Tooltip>
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
                  <TableCell align="center">
                    <Tooltip title="Which catalog motors this account can open. A new account is granted none." arrow>
                      <span>Motors</span>
                    </Tooltip>
                  </TableCell>
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
                    <TableCell align="center">
                      {u.tier === 'admin'
                        ? (
                          <Tooltip title="Admins see the whole catalog — grants do not apply" arrow>
                            <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>all</Typography>
                          </Tooltip>
                        )
                        : <MotorsCell user={u} onOpen={() => setMotorsFor(u)} />}
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
                    <TableCell colSpan={6} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>
                      No accounts yet — Google sign-ins appear here automatically; password accounts via “Add account”.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </Paper>

          {/* sessions + auth events */}
          <SessionsSection />

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
      <InviteDialog open={inviteOpen} onClose={() => setInviteOpen(false)}
        onInvited={(m) => { setNotice(m); void load(); }} />
      <ResetPasswordDialog email={resetFor} onClose={() => setResetFor(null)}
        onDone={(m) => setNotice(m)} />
      <MotorsDialog user={motorsFor} onClose={() => setMotorsFor(null)}
        onSaved={(email, motors) => {
          // Reflect the saved grants in the table without a reload.
          setUsers((us) => us.map((u) => (u.email === email ? { ...u, motors } : u)));
          setNotice(`motors updated for ${email}`);
        }} />
      <ConfirmDialog state={confirm} onClose={() => setConfirm(null)} />
    </Box>
  );
};

export default AdminPanel;
