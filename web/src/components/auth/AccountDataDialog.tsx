// "My data": export everything we hold about me, or delete my account.
// Deletion is a request with a grace period (default 7 days) that can be
// cancelled here; it needs the password (or a fresh Google sign-in).
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, Typography, Box, Alert,
} from '@mui/material';
import {
  GOOGLE_CLIENT_ID, cancelAccountDeletion, getDeletionState, loadGis, requestAccountDeletion,
  requestDataExport, type DeletionState,
} from '../../lib/localAuth';

const fmt = (s: number) => new Date(s * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
type Msg = { kind: 'error' | 'success' | 'info'; text: string };
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

const AccountDataDialog: React.FC<{ open: boolean; onClose: () => void }> = ({ open, onClose }) => {
  const [state, setState] = useState<DeletionState | null>(null);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);
  const gRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try { setState(await getDeletionState()); } catch (e) { setMsg({ kind: 'error', text: errText(e) }); }
  }, []);
  useEffect(() => { if (open) { setMsg(null); setPassword(''); void load(); } }, [open, load]);

  const schedule = useCallback(async (proof: { password?: string; google_credential?: string }) => {
    setBusy(true); setMsg(null);
    try {
      const s = await requestAccountDeletion(proof);
      setState(s); setPassword('');
      setMsg({ kind: 'info', text: 'Deletion scheduled. You can cancel it until the date shown.' });
    } catch (e) { setMsg({ kind: 'error', text: errText(e) }); } finally { setBusy(false); }
  }, []);

  // Google accounts confirm with a fresh Google credential instead of a password.
  useEffect(() => {
    if (!open || !GOOGLE_CLIENT_ID || state?.pending) return;
    let live = true;
    loadGis().then((gis) => {
      if (!live || !gRef.current) return;
      gis.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: (resp: { credential?: string }) => {
          if (resp.credential) void schedule({ google_credential: resp.credential });
        },
      });
      gis.renderButton(gRef.current, { size: 'small', text: 'continue_with' });
    }).catch(() => {});
    return () => { live = false; };
  }, [open, state?.pending, schedule]);

  const doExport = async () => {
    setBusy(true); setMsg(null);
    try {
      const { url } = await requestDataExport();
      window.location.assign(url);
      setMsg({ kind: 'success', text: 'Download started. The link works once and expires after 15 minutes.' });
    } catch (e) { setMsg({ kind: 'error', text: errText(e) }); } finally { setBusy(false); }
  };

  const cancel = async () => {
    setBusy(true);
    try { await cancelAccountDeletion(); await load(); setMsg({ kind: 'success', text: 'Deletion cancelled.' }); }
    catch (e) { setMsg({ kind: 'error', text: errText(e) }); } finally { setBusy(false); }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem' }}>My data</DialogTitle>
      <DialogContent sx={{ pt: '8px !important' }}>
        {msg && <Alert severity={msg.kind} sx={{ mb: 1.5, fontSize: 12 }}>{msg.text}</Alert>}
        <Typography sx={{ fontSize: 13, fontWeight: 700, mb: 0.5 }}>Export</Typography>
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mb: 1 }}>
          A ZIP with your account, consents, sessions, keys, machines and results.
        </Typography>
        <Button size="small" variant="outlined" disabled={busy} onClick={() => void doExport()}>Export my data</Button>

        <Box sx={{ mt: 3 }}>
          <Typography sx={{ fontSize: 13, fontWeight: 700, mb: 0.5, color: '#f87171' }}>Delete account</Typography>
          {state?.pending ? (
            <>
              <Typography sx={{ fontSize: 12, mb: 1 }}>
                Your account and all its data will be deleted on {fmt(state.pending.due_at)}.
              </Typography>
              <Button size="small" variant="contained" disabled={busy} onClick={() => void cancel()}>Cancel deletion</Button>
            </>
          ) : (
            <>
              <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mb: 1 }}>
                Everything is removed {state?.grace_days ?? 7} days after you confirm; you can cancel until then.
              </Typography>
              <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                <TextField size="small" type="password" label="Password" value={password}
                  onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
                <Button size="small" color="error" variant="outlined" disabled={busy || !password}
                  onClick={() => void schedule({ password })}>Delete my account</Button>
              </Box>
              {GOOGLE_CLIENT_ID && (
                <Box sx={{ mt: 1 }}>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mb: 0.5 }}>Google account? Confirm with Google:</Typography>
                  <div ref={gRef} />
                </Box>
              )}
            </>
          )}
        </Box>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
};

export default AccountDataDialog;
