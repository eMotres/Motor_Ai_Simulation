// "Confirm your e-mail": a thin banner while the signed-in account's address is not confirmed, and the one-line notice when the server
// refuses an action for that reason (lib/apiAuth.ts dispatches `email-unverified`).  Both offer Resend and Change address.
// The account works from the first minute; only a short list of actions waits for the link (src/motor_ai_sim/verified_gate.py).
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, Dialog, DialogContent, DialogTitle, Link, Snackbar, TextField, Typography,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { useAuth } from '../../contexts/AuthContext';
import { changeSignupEmail, passwordLogin, resendVerification, storeSession } from '../../lib/localAuth';
import { UNVERIFIED_EVENT, canChangeAddress, resendWait, type UnverifiedRefusal } from '../../lib/unverified';

const UnverifiedBar: React.FC = () => {
  const { t, i18n } = useTranslation('common');
  const { user, emailVerified, refresh } = useAuth();
  const [refused, setRefused] = useState<UnverifiedRefusal | null>(null);
  const [lastSent, setLastSent] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [changing, setChanging] = useState(false);
  const [newEmail, setNewEmail] = useState('');
  const [password, setPassword] = useState('');
  const tRef = useRef(t);
  tRef.current = t;

  const email = user?.email ?? refused?.email ?? '';
  const unverified = !!user && !emailVerified;

  useEffect(() => {
    const on = (e: Event) => setRefused((e as CustomEvent<UnverifiedRefusal>).detail ?? { email: '', gate: '' });
    window.addEventListener(UNVERIFIED_EVENT, on);
    return () => window.removeEventListener(UNVERIFIED_EVENT, on);
  }, []);
  useEffect(() => {
    if (lastSent == null) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [lastSent]);
  // the address was confirmed (the link opened in this or another window): everything unlocks
  useEffect(() => { if (emailVerified) { setRefused(null); setMsg(null); } }, [emailVerified]);
  // another window may have opened the link: look again when this one gets the focus back
  useEffect(() => {
    if (!unverified) return undefined;
    const on = () => { void refresh(); };
    window.addEventListener('focus', on);
    return () => window.removeEventListener('focus', on);
  }, [unverified, refresh]);

  const wait = resendWait(lastSent, now);
  const resend = useCallback(async () => {
    if (!email || busy || wait > 0) return;
    setBusy(true); setMsg(null);
    try {
      await resendVerification(email);
      setLastSent(Date.now()); setNow(Date.now());
      setMsg({ ok: true, text: tRef.current('verify.resent', { email }) });
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }, [email, busy, wait]);

  const change = async () => {
    if (!canChangeAddress(email, newEmail, password) || busy) return;
    setBusy(true); setMsg(null);
    try {
      const lang = i18n.resolvedLanguage === 'zh-CN' ? 'zh-CN' : 'en';
      await changeSignupEmail(email, password, newEmail.trim(), lang);
      // the account moved to the new address: the old session names the old one, so sign in again with the same password
      const { token, user: u } = await passwordLogin(newEmail.trim(), password);
      storeSession(token, u);
      window.location.reload();
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); setBusy(false); }
  };

  if (!user) return null;
  const actions = (
    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
      <Button size="small" color="inherit" disabled={busy || wait > 0} onClick={() => void resend()}
        sx={{ textTransform: 'none', fontSize: 12, textDecoration: 'underline', minWidth: 0, py: 0 }}>
        {wait > 0 ? t('verify.resendWait', { seconds: wait }) : t('verify.resend')}
      </Button>
      <Link component="button" type="button" color="inherit" sx={{ fontSize: 12 }}
        onClick={() => { setChanging(true); setMsg(null); setNewEmail(''); setPassword(''); }}>
        {t('verify.change')}
      </Link>
    </Box>
  );

  return (
    <>
      {unverified && (
        <Box data-testid="unverified-banner" role="status"
          sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', px: 2, py: 0.4, fontSize: 12.5,
            bgcolor: '#fef3c7', color: '#78350f', borderBottom: '1px solid #fcd34d' }}>
          <span>{t('verify.banner', { email: user.email })}</span>
          <HelpTip title={t('verify.help', { email: user.email })} />
          {actions}
          {msg && <Typography component="span" sx={{ fontSize: 12, color: msg.ok ? '#166534' : '#991b1b' }}>{msg.text}</Typography>}
        </Box>
      )}
      <Snackbar open={refused !== null && unverified} autoHideDuration={12000} onClose={() => setRefused(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'center' }}>
        <Alert severity="warning" variant="filled" onClose={() => setRefused(null)}
          sx={{ fontSize: 14, fontWeight: 600, alignItems: 'center' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
            <span>{t('verify.line')}</span>
            <HelpTip title={t('verify.help', { email })} />
            {actions}
          </Box>
        </Alert>
      </Snackbar>
      <Dialog open={changing} onClose={() => setChanging(false)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ fontSize: '1rem' }}>{t('verify.change')}</DialogTitle>
        <DialogContent>
          <Box component="form" noValidate onSubmit={(e) => { e.preventDefault(); void change(); }}
            sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, pt: 0.5 }}>
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('verify.changeHelp', { email })}</Typography>
            <TextField size="small" type="email" label={t('auth.sent.newEmail')} value={newEmail} autoComplete="off" fullWidth
              onChange={(e) => setNewEmail(e.target.value)} />
            <TextField size="small" type="password" label={t('auth.sent.password')} value={password} autoComplete="current-password" fullWidth
              onChange={(e) => setPassword(e.target.value)} />
            {msg && !msg.ok && <Typography variant="caption" color="error" role="alert">{msg.text}</Typography>}
            <Button type="submit" variant="contained" size="small" disabled={busy || !canChangeAddress(email, newEmail, password)}
              sx={{ textTransform: 'none' }}>
              {busy ? <CircularProgress size={16} sx={{ color: 'inherit' }} /> : t('auth.sent.change')}
            </Button>
          </Box>
        </DialogContent>
      </Dialog>
    </>
  );
};

export default UnverifiedBar;
