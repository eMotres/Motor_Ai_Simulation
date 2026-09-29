// Admin · Logs: the append-only admin audit (GET /api/admin/audit). Who did what
// to whom and when; break-glass reads of another user's data are flagged.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Typography, Paper, Chip, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip,
} from '@mui/material';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;

interface AuditRow {
  ts: number; iso: string; actor: string; action: string; target: string;
  subject: string; details: Record<string, unknown>; break_glass: boolean;
}

const AdminAuditSection: React.FC = () => {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [filter, setFilter] = useState('');

  const load = useCallback(async () => {
    const j = await fetch(`${API}/api/admin/audit?limit=300`)
      .then((r) => (r.ok ? r.json() : { entries: [] })).catch(() => ({ entries: [] }));
    setRows(j.entries || []);
  }, []);
  useEffect(() => { void load(); }, [load]);

  const f = filter.trim().toLowerCase();
  const shown = f ? rows.filter((r) => `${r.actor} ${r.action} ${r.target} ${r.subject}`.toLowerCase().includes(f)) : rows;

  return (
    <Box sx={{ mt: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1 }}>
        <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>Admin audit</Typography>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{shown.length} of {rows.length}</Typography>
        <Box sx={{ flex: 1 }} />
        <TextField size="small" placeholder="filter" value={filter} onChange={(e) => setFilter(e.target.value)}
          sx={{ '& input': { fontSize: 12, py: 0.5 } }} />
      </Box>
      <Paper sx={{ ...PANEL, p: 0, overflow: 'auto', maxHeight: 420 }}>
        <Table size="small" stickyHeader sx={{
          '& td, & th': { borderColor: 'var(--panel)', fontSize: 12 },
          '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, bgcolor: 'var(--panel-2)' },
        }}>
          <TableHead>
            <TableRow>
              <TableCell>When (UTC)</TableCell><TableCell>Admin</TableCell><TableCell>Action</TableCell>
              <TableCell>Target</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {shown.map((r, i) => (
              <TableRow key={`${r.ts}-${i}`} hover>
                <TableCell sx={{ color: 'var(--text-2)', whiteSpace: 'nowrap' }}>{r.iso?.replace('T', ' ').replace('Z', '')}</TableCell>
                <TableCell sx={{ color: 'var(--text-2)' }}>{r.actor}</TableCell>
                <TableCell>
                  <Tooltip title={JSON.stringify(r.details || {})} arrow>
                    <span>{r.action}</span>
                  </Tooltip>
                  {r.break_glass && <Chip label="break-glass" size="small" sx={{ ml: 1, height: 16, fontSize: 9, color: '#fbbf24' }} />}
                </TableCell>
                <TableCell sx={{ color: 'var(--text-2)' }}>{r.target}</TableCell>
              </TableRow>
            ))}
            {shown.length === 0 && (
              <TableRow><TableCell colSpan={4} sx={{ color: 'var(--text-4)', textAlign: 'center', py: 3 }}>No admin actions recorded.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>
    </Box>
  );
};

export default AdminAuditSection;
