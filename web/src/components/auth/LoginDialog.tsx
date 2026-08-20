// Sign-in dialog: the official Google button (GIS, when VITE_GOOGLE_CLIENT_ID
// is set) and an email/password form. Both paths end in OUR backend token —
// see lib/localAuth.ts for the exchange.
import React, { useEffect, useRef, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, Box, TextField, Button,
  Typography, Divider, CircularProgress,
} from '@mui/material';
import {
  decodeJwtPayload, googleExchange, loadGis, passwordLogin,
  GOOGLE_CLIENT_ID, type SessionUser,
} from '../../lib/localAuth';

interface Props {
  open: boolean;
  onClose: () => void;
  onSignedIn: (token: string, user: SessionUser) => void;
}

const LoginDialog: React.FC<Props> = ({ open, onClose, onSignedIn }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [gisErr, setGisErr] = useState<string | null>(null);
  const gButtonRef = useRef<HTMLDivElement>(null);

  // Render the official Google button whenever the dialog opens.
  useEffect(() => {
    if (!open || !GOOGLE_CLIENT_ID) return;
    let cancelled = false;
    void loadGis()
      .then((gis) => {
        if (cancelled || !gButtonRef.current) return;
        gis.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: (resp: { credential?: string }) => {
            if (!resp.credential) return;
            const claims = decodeJwtPayload(resp.credential);
            void googleExchange(resp.credential)
              .then(({ token, user }) => onSignedIn(token, {
                ...user,
                name: user.name || String(claims.name ?? ''),
                picture: typeof claims.picture === 'string' ? claims.picture : undefined,
              }))
              .catch((e: Error) => setErr(e.message));
          },
        });
        gButtonRef.current.innerHTML = '';
        gis.renderButton(gButtonRef.current, {
          theme: 'outline', size: 'large', width: 280, text: 'signin_with',
        });
        setGisErr(null);
      })
      .catch((e: Error) => { if (!cancelled) setGisErr(e.message); });
    return () => { cancelled = true; };
  }, [open, onSignedIn]);

  const submit = async () => {
    if (busy || !email.trim() || !password) return;
    setBusy(true); setErr(null);
    try {
      const { token, user } = await passwordLogin(email.trim(), password);
      setEmail(''); setPassword('');
      onSignedIn(token, user);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem', pb: 1 }}>Sign in</DialogTitle>
      <DialogContent>
        {GOOGLE_CLIENT_ID ? (
          <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1, py: 1 }}>
            <div ref={gButtonRef} />
            {gisErr && (
              <Typography variant="caption" color="error">{gisErr}</Typography>
            )}
            <Divider flexItem sx={{ my: 1.5, fontSize: '0.72rem', color: 'var(--text-3)' }}>
              or with a password
            </Divider>
          </Box>
        ) : null}
        <Box component="form"
          onSubmit={(e) => { e.preventDefault(); void submit(); }}
          sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: GOOGLE_CLIENT_ID ? 0 : 1 }}>
          <TextField size="small" label="Email" type="email" autoComplete="username"
            value={email} onChange={(e) => setEmail(e.target.value)} fullWidth />
          <TextField size="small" label="Password" type="password" autoComplete="current-password"
            value={password} onChange={(e) => setPassword(e.target.value)} fullWidth />
          {err && <Typography variant="caption" color="error">{err}</Typography>}
          <Button type="submit" variant="contained" disabled={busy || !email.trim() || !password}
            sx={{ textTransform: 'none' }}>
            {busy ? <CircularProgress size={18} sx={{ color: 'inherit' }} /> : 'Sign in'}
          </Button>
        </Box>
      </DialogContent>
    </Dialog>
  );
};

export default LoginDialog;
