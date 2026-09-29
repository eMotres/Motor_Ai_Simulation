// Admin · Logs / Events — sessions, auth events, and support tickets: the
// forensic record of who is signed in and what they asked for.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Typography, Paper, Chip, Table, TableBody, TableCell, TableHead, TableRow, Select, MenuItem,
} from '@mui/material';
import SessionsSection from '../SessionsSection';
import AdminAuditSection from '../AdminAuditSection';
import SupportSettings, { type SupportCfg } from '../SupportSettings';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

interface AdminTicket {
  id: string; uid: string | null; type: string; title: string; description: string;
  status: string; email: string | null; createdAt: number | null;
}
const TICKET_STATUSES = ['open', 'in_progress', 'resolved', 'closed'] as const;
const T_STATUS_COLOR: Record<string, string> = { open: '#60a5fa', in_progress: '#fbbf24', resolved: '#4ade80', closed: 'var(--text-3)' };
const T_TYPE_COLOR: Record<string, string> = { bug: '#f87171', feature: '#a78bfa', question: 'var(--text-3)' };

const fmtDate = (ms?: number | null) =>
  ms ? new Date(ms).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';

const LogsSection: React.FC = () => {
  const [tickets, setTickets] = useState<AdminTicket[]>([]);
  const [supportCfg, setSupportCfg] = useState<SupportCfg | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [tk, sc] = await Promise.all([
      fetch(`${API}/api/admin/tickets`).then((r) => (r.ok ? r.json() : { tickets: [] })).catch(() => ({ tickets: [] })),
      fetch(`${API}/api/admin/support`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    // Ticket storage was Firestore; without it the backend serves a flagged
    // mock set — never show invented tickets as if they were real.
    setTickets(tk.source === 'mock' ? [] : (tk.tickets || [])); setSupportCfg(sc);
  }, []);
  useEffect(() => { void load(); }, [load]);

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

  return (
    <Box>
      <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)', mb: 1.5 }}>Logs &amp; events</Typography>

      <SessionsSection />

      <AdminAuditSection />

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
              <TableCell>Type</TableCell><TableCell>Title</TableCell><TableCell>User</TableCell>
              <TableCell>Created</TableCell><TableCell>Status</TableCell>
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
    </Box>
  );
};

export default LogsSection;
