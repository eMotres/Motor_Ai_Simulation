// Admin · account dialogs — create a password account, reset a password.
import React, { useEffect, useState } from 'react';
import {
  Button, Dialog, DialogTitle, DialogContent, DialogActions, TextField, Select, MenuItem, Typography,
} from '@mui/material';
import { TIERS } from './InviteDialog';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export const CreateUserDialog: React.FC<{
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

export const ResetPasswordDialog: React.FC<{
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
