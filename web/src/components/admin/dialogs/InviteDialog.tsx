// Admin · Invite — one call creates the account, its role, its motors and its
// workspace. NO E-MAIL IS SENT (the host blocks outbound SMTP) — the admin is
// the messenger, which is why the dialog says so instead of implying delivery.
// Shared by the Users section ("Invite" button) and the Sign-ups section
// (VisitorRequests "Invite" on a visitor request) — one dialog, one behavior.
import React, { useEffect, useState } from 'react';
import {
  Box, Button, Dialog, DialogTitle, DialogContent, DialogActions,
  TextField, Select, MenuItem, Tooltip, Typography,
} from '@mui/material';
import HelpTip from '../../common/HelpTip';
import { MotorPicker, useCatalog } from './MotorPicker';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
export const ROLES = ['user', 'admin'] as const;

const InviteDialog: React.FC<{
  open: boolean; onClose: () => void; onInvited: (msg: string) => void;
  /** Pre-fill, so "Invite" on a visitor request opens this already addressed. */
  email?: string;
}> = ({ open, onClose, onInvited, email: prefill }) => {
  const dies = useCatalog(open);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<string>('user');
  const [note, setNote] = useState('');
  const [all, setAll] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setEmail(prefill ?? ''); setRole('user'); setNote(''); setAll(false);
    setPicked(new Set()); setErr(null);
  }, [open, prefill]);

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
          email: email.trim(), role, note: note.trim(),
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
      <DialogTitle sx={{ fontSize: '1rem', display: 'flex', alignItems: 'center', gap: 0.75 }}>
        Invite
        <HelpTip title="Creates the account, its role and its motors." />
        <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-4)', ml: 'auto' }}>
          No e-mail is sent — they sign in with Google.
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: '8px !important' }}>
        <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
          <Tooltip title="The Google address they will sign in with" arrow>
            <TextField size="small" label="Email" type="email" value={email} autoFocus
              onChange={(e) => setEmail(e.target.value)} sx={{ flex: '2 1 220px' }} />
          </Tooltip>
          <Select size="small" value={role} onChange={(e) => setRole(e.target.value)} sx={{ flex: '0 0 110px' }}>
            {ROLES.map((r) => <MenuItem key={r} value={r} sx={{ fontSize: 13 }}>{r}</MenuItem>)}
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

export default InviteDialog;
