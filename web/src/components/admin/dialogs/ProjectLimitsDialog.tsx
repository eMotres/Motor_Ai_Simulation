// Admin · Users — the open-projects overrides of ALL e-mails, including addresses that have no account yet (they apply at sign-up).
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, Table, TableBody, TableCell, TableHead, TableRow,
  TextField, Typography,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import HelpTip from '../../common/HelpTip';
import { parseProjectLimit, projectLimitLabel, type ProjectLimit } from '../projectLimit';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

interface Row { email: string; limit: ProjectLimit; account: boolean }

const ProjectLimitsDialog: React.FC<{ open: boolean; onClose: () => void; onChanged: () => void }> = ({ open, onClose, onChanged }) => {
  const [rows, setRows] = useState<Row[]>([]);
  const [defaultLimit, setDefaultLimit] = useState(2);
  const [email, setEmail] = useState('');
  const [value, setValue] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/auth/project-limits`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setRows(Array.isArray(j.limits) ? j.limits : []);
      if (typeof j.default === 'number') setDefaultLimit(j.default);
      setErr(null);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, []);
  useEffect(() => { if (open) { setEmail(''); setValue(''); void load(); } }, [open, load]);

  const put = async (target: string, limit: ProjectLimit): Promise<boolean> => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API}/api/auth/project-limits/${encodeURIComponent(target)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ limit }),
      });
      if (!r.ok) { const j = await r.json().catch(() => ({})); setErr(typeof j.detail === 'string' ? j.detail : `HTTP ${r.status}`); return false; }
      await load(); onChanged();
      return true;
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); return false; } finally { setBusy(false); }
  };

  const parsed = parseProjectLimit(value);
  const addable = !busy && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()) && parsed.ok && parsed.value !== null;
  const add = async () => {
    if (!parsed.ok) return;
    if (await put(email.trim(), parsed.value)) { setEmail(''); setValue(''); }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem', display: 'flex', alignItems: 'center', gap: 1 }}>
        Project limits
        <HelpTip title={`How many open projects an account may own. Everybody has the default (${defaultLimit}); the addresses below have their own number or unlimited. An address with no account yet is fine: the limit applies as soon as it signs up. Staff are never limited.`} />
      </DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', pt: 0.5, mb: 1.5 }}>
          <TextField size="small" type="email" label="E-mail" value={email} onChange={(e) => setEmail(e.target.value)} sx={{ flex: 1 }} />
          <TextField size="small" label="Limit" placeholder="number or unlimited" value={value} onChange={(e) => setValue(e.target.value)}
            error={!parsed.ok} sx={{ width: 170 }} />
          <Button variant="outlined" disabled={!addable} onClick={() => void add()} sx={{ textTransform: 'none' }}>Set</Button>
        </Box>
        {err && <Typography sx={{ color: '#f87171', fontSize: 12, mb: 1 }}>{err}</Typography>}
        {rows.length === 0 ? (
          <Typography sx={{ fontSize: 12.5, color: 'var(--text-3)' }}>No overrides: every account has the default ({defaultLimit}).</Typography>
        ) : (
          <Table size="small">
            <TableHead><TableRow><TableCell>E-mail</TableCell><TableCell>Limit</TableCell><TableCell>Account</TableCell><TableCell /></TableRow></TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.email}>
                  <TableCell sx={{ wordBreak: 'break-all' }}>{r.email}</TableCell>
                  <TableCell>{projectLimitLabel(r.limit, defaultLimit)}</TableCell>
                  <TableCell>{r.account ? 'yes' : 'not yet'}</TableCell>
                  <TableCell align="right">
                    <IconButton size="small" aria-label={`remove the limit of ${r.email}`} disabled={busy} onClick={() => void put(r.email, null)}>
                      <DeleteOutlineIcon sx={{ fontSize: 16 }} />
                    </IconButton>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose} sx={{ textTransform: 'none' }}>Close</Button></DialogActions>
    </Dialog>
  );
};

export default ProjectLimitsDialog;
