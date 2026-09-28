// Sign-in dialog: tabs "Google | E-mail". Google = the official GIS button
// (when VITE_GOOGLE_CLIENT_ID is set). E-mail = sign in / create account /
// forgot password, plus the two mailed links (?verify=, ?reset=). Every path
// ends in OUR backend token — see lib/localAuth.ts.
import React, { useEffect, useRef, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, Box, TextField, Button,
  Typography, CircularProgress, Tabs, Tab, Link,
} from '@mui/material';
import {
  decodeJwtPayload, googleExchange, loadGis, passwordLogin,
  registerAccount, verifyEmail, requestPasswordReset, confirmPasswordReset,
  pendingEmailLink, clearEmailLink, PASSWORD_MIN_LEN,
  GOOGLE_CLIENT_ID, type SessionUser,
} from '../../lib/localAuth';
import HelpTip from '../common/HelpTip';

interface Props {
  open: boolean;
  onClose: () => void;
  onSignedIn: (token: string, user: SessionUser) => void;
}

type Mode = 'signin' | 'register' | 'forgot' | 'reset';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const HELP: Record<Mode, string> = {
  signin: 'Same address via Google or password = one account. A new e-mail account works after you confirm the mailed link.',
  register: `At least ${PASSWORD_MIN_LEN} characters; common passwords are refused. We mail a link valid 24 h; until you open it the account cannot sign in.`,
  forgot: 'If an account uses this address we mail a reset link (24 h, one use). Also sets a password for a Google-only account.',
  reset: 'Setting a new password signs out every other session.',
};

const LoginDialog: React.FC<Props> = ({ open, onClose, onSignedIn }) => {
  const [tab, setTab] = useState<'google' | 'email'>(GOOGLE_CLIENT_ID ? 'google' : 'email');
  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [password2, setPassword2] = useState('');
  const [resetToken, setResetToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [gisErr, setGisErr] = useState<string | null>(null);
  const gButtonRef = useRef<HTMLDivElement>(null);

  // A mailed link in the address bar: consume it once, then scrub the URL.
  useEffect(() => {
    if (!open) return;
    const link = pendingEmailLink();
    if (!link) return;
    clearEmailLink();
    setTab('email');
    if (link.kind === 'reset') {
      setResetToken(link.token); setMode('reset'); setErr(null); setInfo(null);
      return;
    }
    setMode('signin'); setBusy(true); setErr(null);
    void verifyEmail(link.token)
      .then((j) => {
        if (j.email) setEmail(j.email);
        setInfo('E-mail confirmed — sign in with your password.');
      })
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(false));
  }, [open]);

  // Render the official Google button whenever the Google tab is shown.
  useEffect(() => {
    if (!open || !GOOGLE_CLIENT_ID || tab !== 'google') return;
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
  }, [open, onSignedIn, tab]);

  const switchMode = (m: Mode) => {
    setMode(m); setErr(null); setInfo(null); setTouched(false);
    setPassword(''); setPassword2('');
  };

  // Loud, field-level validation (shown after the first submit attempt).
  const emailErr = mode !== 'reset' && !EMAIL_RE.test(email.trim()) ? 'enter a valid e-mail address' : '';
  const needsNewPw = mode === 'register' || mode === 'reset';
  const pwErr = mode === 'forgot' ? ''
    : !password ? 'enter a password'
    : needsNewPw && password.length < PASSWORD_MIN_LEN ? `at least ${PASSWORD_MIN_LEN} characters`
    : '';
  const pw2Err = needsNewPw && password2 !== password ? 'passwords do not match' : '';
  const nameErr = mode === 'register' && !name.trim() ? 'enter your name' : '';
  const invalid = !!(emailErr || pwErr || pw2Err || nameErr);

  const submit = async () => {
    setTouched(true);
    if (busy || invalid) return;
    setBusy(true); setErr(null); setInfo(null);
    try {
      if (mode === 'signin') {
        const { token, user } = await passwordLogin(email.trim(), password);
        setEmail(''); setPassword('');
        onSignedIn(token, user);
      } else if (mode === 'register') {
        const j = await registerAccount(email.trim(), password, name.trim());
        setPassword(''); setPassword2(''); setTouched(false);
        setMode('signin');
        setInfo(j.message || 'Check your inbox for the confirmation link.');
      } else if (mode === 'forgot') {
        const j = await requestPasswordReset(email.trim());
        setTouched(false);
        setMode('signin');
        setInfo(j.message || 'Check your inbox for the reset link.');
      } else if (mode === 'reset' && resetToken) {
        const j = await confirmPasswordReset(resetToken, password);
        setResetToken(null); setPassword(''); setPassword2(''); setTouched(false);
        if (j.email) setEmail(j.email);
        setMode('signin');
        setInfo('Password changed — sign in with the new one.');
      }
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const show = (msg: string) => (touched && msg ? msg : undefined);
  const title = mode === 'register' ? 'Create account'
    : mode === 'forgot' ? 'Reset password'
    : mode === 'reset' ? 'Set a new password' : 'Sign in';
  const cta = mode === 'register' ? 'Create account'
    : mode === 'forgot' ? 'Send reset link'
    : mode === 'reset' ? 'Set password' : 'Sign in';

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: '1rem', pb: 0.5, display: 'flex', alignItems: 'center', gap: 0.75 }}>
        {title}
      </DialogTitle>
      <DialogContent>
        {GOOGLE_CLIENT_ID ? (
          <Tabs value={tab} onChange={(_, v) => { setTab(v); setErr(null); }} variant="fullWidth"
            sx={{ minHeight: 34, mb: 1.5, '& .MuiTab-root': { minHeight: 34, textTransform: 'none', fontSize: 13 } }}>
            <Tab value="google" label="Google" />
            <Tab value="email" label="E-mail" />
          </Tabs>
        ) : null}

        {tab === 'google' && GOOGLE_CLIENT_ID ? (
          <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1, py: 1 }}>
            <div ref={gButtonRef} />
            {gisErr && <Typography variant="caption" color="error">{gisErr}</Typography>}
            {err && <Typography variant="caption" color="error">{err}</Typography>}
          </Box>
        ) : (
          <Box component="form" noValidate
            onSubmit={(e) => { e.preventDefault(); void submit(); }}
            sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 0.5 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Typography sx={{ fontSize: 12, color: 'var(--text-3)', flex: 1 }}>
                {mode === 'register' ? 'We mail you a confirmation link.'
                  : mode === 'forgot' ? 'We mail you a reset link.'
                  : mode === 'reset' ? 'Choose a new password.'
                  : 'Sign in with your e-mail and password.'}
              </Typography>
              <HelpTip title={HELP[mode]} />
            </Box>
            {info && <Typography variant="caption" sx={{ color: '#34d399' }}>{info}</Typography>}
            {mode === 'register' && (
              <TextField size="small" label="Name" autoComplete="name" required
                value={name} onChange={(e) => setName(e.target.value)} fullWidth
                error={!!show(nameErr)} helperText={show(nameErr)} />
            )}
            {mode !== 'reset' && (
              <TextField size="small" label="E-mail" type="email" autoComplete="username" required
                value={email} onChange={(e) => setEmail(e.target.value)} fullWidth
                error={!!show(emailErr)} helperText={show(emailErr)} />
            )}
            {mode !== 'forgot' && (
              <TextField size="small" label={needsNewPw ? `New password (min ${PASSWORD_MIN_LEN})` : 'Password'}
                type="password" required
                autoComplete={needsNewPw ? 'new-password' : 'current-password'}
                value={password} onChange={(e) => setPassword(e.target.value)} fullWidth
                error={!!show(pwErr)} helperText={show(pwErr)} />
            )}
            {needsNewPw && (
              <TextField size="small" label="Repeat password" type="password" autoComplete="new-password" required
                value={password2} onChange={(e) => setPassword2(e.target.value)} fullWidth
                error={!!show(pw2Err)} helperText={show(pw2Err)} />
            )}
            {err && <Typography variant="caption" color="error" role="alert">{err}</Typography>}
            <Button type="submit" variant="contained" disabled={busy}
              sx={{ textTransform: 'none' }}>
              {busy ? <CircularProgress size={18} sx={{ color: 'inherit' }} /> : cta}
            </Button>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
              {mode === 'signin' ? (
                <>
                  <Link component="button" type="button" onClick={() => switchMode('register')}>Create account</Link>
                  <Link component="button" type="button" onClick={() => switchMode('forgot')}>Forgot password?</Link>
                </>
              ) : (
                <Link component="button" type="button" onClick={() => switchMode('signin')}>Back to sign in</Link>
              )}
            </Box>
          </Box>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default LoginDialog;
