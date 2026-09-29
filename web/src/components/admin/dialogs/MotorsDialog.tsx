// Admin · per-user Motors dialog — which catalog motors an account may open.
import React, { useEffect, useState } from 'react';
import { Button, Dialog, DialogTitle, DialogContent, DialogActions, Typography } from '@mui/material';
import HelpTip from '../../common/HelpTip';
import { MotorPicker, useCatalog, type MotorGrants } from './MotorPicker';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export interface RegistryUser {
  email: string; role: string; name: string; disabled: boolean; created?: string | null;
  motors?: MotorGrants;
}

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
      <DialogTitle sx={{ fontSize: '1rem', display: 'flex', alignItems: 'center', gap: 0.75 }}>
        Motors — {user?.email}
        <HelpTip title="The catalog this account sees." />
        <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-4)', ml: 'auto' }}>
          Nothing granted = empty catalog.
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

export default MotorsDialog;
export type { MotorGrants };
