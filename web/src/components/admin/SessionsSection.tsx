// Admin · Sessions — who holds a live token, and what the server decided about
// every token it was shown.
//
// This is the instrument the daily-sign-out investigation lacked. The session
// table answers "which browser is that, and when did it last talk to us"; the
// event table answers "was the token REJECTED, and for what reason" — expired,
// revoked, bad_signature, unknown_user, disabled, or store_unavailable (our own
// file lock, which is explicitly not a logout).
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Paper, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  Button, Chip, Tooltip, TextField, CircularProgress,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { shortUA } from '../auth/SessionsDialog';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

interface AdminSession {
  sid: string; email: string; created: number; lastSeen: number; expires: number;
  userAgent: string; ip: string; loginMethod: string; revoked: boolean;
}
interface AuthEvent {
  ts: string; event: string; email: string; sid: string; reason: string;
  ip: string; user_agent: string; path: string;
}

/** Colour by what the line MEANS: green = fine, red = the user lost a session,
 *  amber = our own store hiccuped (not the user's fault). */
const EVENT_COLOR: Record<string, string> = {
  login: '#4ade80', logout: 'var(--text-3)', renew: '#60a5fa',
  reject: '#f87171', store_unavailable: '#fbbf24', revoke: '#a78bfa',
};
const REASON_COLOR: Record<string, string> = {
  expired: '#fbbf24', revoked: '#a78bfa', bad_signature: '#f87171',
  malformed: '#f87171', unknown_user: '#f87171', disabled: '#f87171',
  store_unavailable: '#fbbf24', ok: '#4ade80',
};

const when = (s?: number | null) =>
  (s ? new Date(s * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : '—');

const SessionsSection: React.FC = () => {
  const [sessions, setSessions] = useState<AdminSession[]>([]);
  const [events, setEvents] = useState<AuthEvent[]>([]);
  const [filter, setFilter] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (email: string) => {
    setBusy(true); setErr(null);
    const q = email ? `?email=${encodeURIComponent(email)}` : '';
    try {
      const [s, e] = await Promise.all([
        fetch(`${API}/api/admin/sessions${q}`).then((r) => { if (!r.ok) throw new Error(`sessions HTTP ${r.status}`); return r.json(); }),
        fetch(`${API}/api/admin/auth_events?limit=100${email ? `&email=${encodeURIComponent(email)}` : ''}`)
          .then((r) => (r.ok ? r.json() : { events: [] })).catch(() => ({ events: [] })),
      ]);
      setSessions(s.sessions ?? []); setEvents(e.events ?? []);
    } catch (x) { setErr(x instanceof Error ? x.message : String(x)); } finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(''); }, [load]);

  const revoke = async (sid: string) => {
    setBusy(true);
    try {
      await fetch(`${API}/api/admin/sessions/${encodeURIComponent(sid)}/revoke`, { method: 'POST' });
      await load(filter.trim());
    } finally { setBusy(false); }
  };

  const revokeAll = async (email: string) => {
    setBusy(true);
    try {
      await fetch(`${API}/api/admin/users/${encodeURIComponent(email)}/revoke_all`, { method: 'POST' });
      await load(filter.trim());
    } finally { setBusy(false); }
  };

  // Sessions grouped by account, newest account activity first.
  const byUser = React.useMemo(() => {
    const m = new Map<string, AdminSession[]>();
    for (const s of sessions) m.set(s.email, [...(m.get(s.email) ?? []), s]);
    return [...m.entries()].sort((a, b) => (b[1][0]?.lastSeen ?? 0) - (a[1][0]?.lastSeen ?? 0));
  }, [sessions]);

  return (
    <>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 3, mb: 1 }}>
        <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>Sessions</Typography>
        <Tooltip arrow title="Every live sign-in and every decision the server made about a token. A reject line names its reason; store_unavailable is our own file lock and never signs anyone out.">
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
            {sessions.filter((s) => !s.revoked).length} live · {events.length} recent events
          </Typography>
        </Tooltip>
        <Box sx={{ flex: 1 }} />
        <TextField size="small" placeholder="filter by email" value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void load(filter.trim()); }}
          sx={{ '& .MuiInputBase-input': { fontSize: 12, py: 0.5 } }} />
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} disabled={busy}
          onClick={() => void load(filter.trim())}
          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>Refresh</Button>
        {busy && <CircularProgress size={14} />}
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171', mb: 1 }}>{err}</Typography>}

      <Paper sx={{ ...PANEL, p: 0, overflow: 'hidden' }}>
        <Table size="small" sx={{
          '& td, & th': { borderColor: 'var(--panel)', fontSize: 12 },
          '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
        }}>
          <TableHead>
            <TableRow>
              <TableCell>Browser</TableCell>
              <TableCell>IP</TableCell>
              <TableCell>Created</TableCell>
              <TableCell>Last seen</TableCell>
              <TableCell>Expires</TableCell>
              <TableCell>Method</TableCell>
              <TableCell align="right" />
            </TableRow>
          </TableHead>
          <TableBody>
            {byUser.map(([email, rows]) => (
              <React.Fragment key={email}>
                <TableRow sx={{ bgcolor: 'var(--panel)' }}>
                  <TableCell colSpan={6} sx={{ fontWeight: 700, color: 'var(--text-0)' }}>
                    {email} <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>· {rows.filter((r) => !r.revoked).length} live</span>
                  </TableCell>
                  <TableCell align="right">
                    <Button size="small" disabled={busy} onClick={() => void revokeAll(email)}
                      sx={{ textTransform: 'none', fontSize: 11, color: 'var(--text-3)', '&:hover': { color: '#f87171' } }}>
                      Revoke all
                    </Button>
                  </TableCell>
                </TableRow>
                {rows.map((s) => (
                  <TableRow key={s.sid} hover sx={{ opacity: s.revoked ? 0.45 : 1 }}>
                    <TableCell>
                      <Tooltip arrow title={`${s.userAgent || 'no user agent'} · sid ${s.sid}`}>
                        <span>{shortUA(s.userAgent)}</span>
                      </Tooltip>
                    </TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{s.ip || '—'}</TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{when(s.created)}</TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{when(s.lastSeen)}</TableCell>
                    <TableCell sx={{ color: 'var(--text-2)' }}>{when(s.expires)}</TableCell>
                    <TableCell>
                      <Chip label={s.loginMethod} size="small" sx={{ height: 17, fontSize: 9.5, bgcolor: 'var(--panel-2)', color: 'var(--text-2)' }} />
                    </TableCell>
                    <TableCell align="right">
                      {s.revoked
                        ? <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>revoked</Typography>
                        : (
                          <Button size="small" disabled={busy} onClick={() => void revoke(s.sid)}
                            sx={{ textTransform: 'none', fontSize: 11, color: 'var(--text-3)', '&:hover': { color: '#f87171' } }}>
                            Revoke
                          </Button>
                        )}
                    </TableCell>
                  </TableRow>
                ))}
              </React.Fragment>
            ))}
            {sessions.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} sx={{ textAlign: 'center', color: 'var(--text-4)', py: 3 }}>
                  No sessions recorded yet — they appear from the next sign-in.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>

      <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)', mt: 2, mb: 0.75 }}>
        Recent auth events
      </Typography>
      <Paper sx={{ ...PANEL, p: 0, overflow: 'hidden', maxHeight: 420, overflowY: 'auto' }}>
        <Table size="small" stickyHeader sx={{
          '& td, & th': { borderColor: 'var(--panel)', fontSize: 11.5 },
          '& th': { bgcolor: 'var(--panel-2)', color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10 },
        }}>
          <TableHead>
            <TableRow>
              <TableCell>When</TableCell>
              <TableCell>Event</TableCell>
              <TableCell>Reason</TableCell>
              <TableCell>Account</TableCell>
              <TableCell>Browser</TableCell>
              <TableCell>Path</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {events.map((e, i) => (
              <TableRow key={`${e.ts}-${e.sid}-${i}`} hover>
                <TableCell sx={{ color: 'var(--text-2)', whiteSpace: 'nowrap' }}>{e.ts?.replace('T', ' ')}</TableCell>
                <TableCell>
                  <Chip label={e.event} size="small"
                    sx={{ height: 17, fontSize: 9.5, bgcolor: 'var(--panel-2)', color: EVENT_COLOR[e.event] ?? 'var(--text-2)' }} />
                </TableCell>
                <TableCell>
                  {e.reason
                    ? <Chip label={e.reason} size="small"
                        sx={{ height: 17, fontSize: 9.5, bgcolor: 'var(--panel-2)', color: REASON_COLOR[e.reason] ?? 'var(--text-3)' }} />
                    : <span style={{ color: 'var(--text-4)' }}>—</span>}
                </TableCell>
                <TableCell sx={{ color: 'var(--text-2)' }}>{e.email || '—'}</TableCell>
                <TableCell sx={{ color: 'var(--text-2)' }}>
                  <Tooltip arrow title={`${e.user_agent || 'no user agent'} · ${e.ip || 'no ip'} · sid ${e.sid || '-'}`}>
                    <span>{shortUA(e.user_agent)}</span>
                  </Tooltip>
                </TableCell>
                <TableCell sx={{ color: 'var(--text-4)' }}>{e.path || '—'}</TableCell>
              </TableRow>
            ))}
            {events.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} sx={{ textAlign: 'center', color: 'var(--text-4)', py: 3 }}>
                  No auth events logged yet.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>
    </>
  );
};

export default SessionsSection;
