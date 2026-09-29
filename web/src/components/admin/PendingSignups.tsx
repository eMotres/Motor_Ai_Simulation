/**
 * Admin · Pending sign-ups — e-mail/password accounts whose address is not
 * proven yet. With SMTP unset the confirmation link only reaches the server
 * log, so every new sign-up lands here and the owner approves it by hand.
 * Approve = the account may sign in (role user, no motors granted).
 * Reject = delete the row (same DELETE as the users table).
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Paper, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  Button, CircularProgress,
} from '@mui/material';
import HelpTip from '../common/HelpTip';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

interface PendingRow { email: string; name: string; created: string | null; reason: string }

const REASON: Record<string, string> = {
  smtp_not_configured: 'no SMTP — link only in server log',
  mail_throttled: 'mail throttled',
  awaiting_link: 'link mailed, not opened',
};

const PendingSignups: React.FC<{ onChanged?: () => void }> = ({ onChanged }) => {
  const [rows, setRows] = useState<PendingRow[]>([]);
  const [smtp, setSmtp] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/auth/pending`);
      if (!r.ok) throw new Error(`pending HTTP ${r.status}`);
      const j = await r.json() as { pending: PendingRow[]; smtp: boolean };
      setRows(j.pending ?? []); setSmtp(!!j.smtp); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const act = async (email: string, how: 'approve' | 'reject') => {
    setBusy(email);
    try {
      const r = how === 'approve'
        ? await fetch(`${API}/api/auth/pending/${encodeURIComponent(email)}/approve`, { method: 'POST' })
        : await fetch(`${API}/api/auth/users/${encodeURIComponent(email)}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`${how} HTTP ${r.status}`);
      setRows((rs) => rs.filter((x) => x.email !== email));
      onChanged?.();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5, mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1 }}>
        <Typography sx={{ fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          Pending sign-ups ({rows.length})
        </Typography>
        <HelpTip title={smtp
          ? 'E-mail accounts that have not opened their confirmation link yet. Approve only if you know the person.'
          : 'SMTP is not configured (SMTP_HOST/PORT/USER/PASS, MAIL_FROM, PUBLIC_APP_URL in /etc/motres/api.env), so confirmation links go to the server log only. Approve people you know.'} />
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
      {rows.length === 0 ? (
        <Typography sx={{ fontSize: 12, color: 'var(--text-4)' }}>None.</Typography>
      ) : (
        <Table size="small" sx={{ '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 } }}>
          <TableHead>
            <TableRow><TableCell>E-mail</TableCell><TableCell>Name</TableCell><TableCell>Signed up</TableCell><TableCell>State</TableCell><TableCell /></TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.email}>
                <TableCell>{r.email}</TableCell>
                <TableCell>{r.name}</TableCell>
                <TableCell>{(r.created ?? '').replace('T', ' ').slice(0, 16)}</TableCell>
                <TableCell>{REASON[r.reason] ?? r.reason}</TableCell>
                <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                  {busy === r.email ? <CircularProgress size={14} /> : (
                    <>
                      <Button size="small" onClick={() => void act(r.email, 'approve')} sx={{ textTransform: 'none', fontSize: 12 }}>Approve</Button>
                      <Button size="small" color="error" onClick={() => void act(r.email, 'reject')} sx={{ textTransform: 'none', fontSize: 12 }}>Reject</Button>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Paper>
  );
};

export default PendingSignups;
