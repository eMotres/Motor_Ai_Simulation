// Sign-in dialog: tabs "Google | E-mail". Google = the official GIS button
// (when VITE_GOOGLE_CLIENT_ID is set). E-mail = sign in / create account /
// forgot password, plus the two mailed links (?verify=, ?reset=). Every path
// ends in OUR backend token — see lib/localAuth.ts.
import React, { useEffect, useRef, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, Box, TextField, Button,
  Typography, CircularProgress, Tabs, Tab, Link, FormControlLabel, Checkbox,
} from '@mui/material';
import { CONSENT_LINE, CONSENT_HELP } from '../../lib/newsletterApi';
import {
  decodeJwtPayload, googleExchange, loadGis, passwordLogin,
  registerAccount, verifyEmail, requestPasswordReset, confirmPasswordReset,
  pendingEmailLink, clearEmailLink, PASSWORD_MIN_LEN,
  GOOGLE_CLIENT_ID, type SessionUser,
} from '../../lib/localAuth';
import HelpTip from '../common/HelpTip';
import { useTranslation } from 'react-i18next';

interface Props {
  open: boolean;
  onClose: () => void;
  onSignedIn: (token: string, user: SessionUser) => void;
}

type Mode = 'signin' | 'register' | 'forgot' | 'reset';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const LoginDialog: React.FC<Props> = ({ open, onClose, onSignedIn }) => {
  const { t } = useTranslation('common');
  const min = PASSWORD_MIN_LEN;
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
  // Newsletter consent: UNCHECKED by default (GDPR); a ref so the GIS callback
  // registered once still reads the current box.
  const [newsletter, setNewsletter] = useState(false);
  const newsletterRef = useRef(false);
  newsletterRef.current = newsletter;

  const consentBox = (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <FormControlLabel
        control={<Checkbox size="small" checked={newsletter}
          onChange={(e) => setNewsletter(e.target.checked)} />}
        label={<Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('auth.consent.line', { defaultValue: CONSENT_LINE })}</Typography>}
        sx={{ mr: 0 }} />
      <HelpTip i18nKey="auth.consent.help" ns="common" title={CONSENT_HELP} />
    </Box>
  );

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
        setInfo(t('auth.info.emailConfirmed'));
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
            void googleExchange(resp.credential, newsletterRef.current)
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
  const emailErr = mode !== 'reset' && !EMAIL_RE.test(email.trim()) ? t('auth.validation.email') : '';
  const needsNewPw = mode === 'register' || mode === 'reset';
  const pwErr = mode === 'forgot' ? ''
    : !password ? t('auth.validation.passwordRequired')
    : needsNewPw && password.length < PASSWORD_MIN_LEN ? t('auth.validation.passwordMin', { min })
    : '';
  const pw2Err = needsNewPw && password2 !== password ? t('auth.validation.passwordMismatch') : '';
  const nameErr = mode === 'register' && !name.trim() ? t('auth.validation.nameRequired') : '';
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
        const j = await registerAccount(email.trim(), password, name.trim(), newsletter);
        setPassword(''); setPassword2(''); setTouched(false); setNewsletter(false);
        setMode('signin');
        setInfo(j.message || t('auth.info.checkConfirmation'));
      } else if (mode === 'forgot') {
        const j = await requestPasswordReset(email.trim());
        setTouched(false);
        setMode('signin');
        setInfo(j.message || t('auth.info.checkReset'));
      } else if (mode === 'reset' && resetToken) {
        const j = await confirmPasswordReset(resetToken, password);
        setResetToken(null); setPassword(''); setPassword2(''); setTouched(false);
        if (j.email) setEmail(j.email);
        setMode('signin');
        setInfo(t('auth.info.passwordChanged'));
      }
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const show = (msg: string) => (touched && msg ? msg : undefined);
  const title = t(`auth.title.${mode}`);
  const cta = t(`auth.cta.${mode}`);

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
            <Tab value="email" label={t('auth.field.email')} />
          </Tabs>
        ) : null}

        {tab === 'google' && GOOGLE_CLIENT_ID ? (
          <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1, py: 1 }}>
            <div ref={gButtonRef} />
            {consentBox}
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mt: -0.75 }}>{t('auth.consent.newAccountOnly')}</Typography>
            {gisErr && <Typography variant="caption" color="error">{gisErr}</Typography>}
            {err && <Typography variant="caption" color="error">{err}</Typography>}
          </Box>
        ) : (
          <Box component="form" noValidate
            onSubmit={(e) => { e.preventDefault(); void submit(); }}
            sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 0.5 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Typography sx={{ fontSize: 12, color: 'var(--text-3)', flex: 1 }}>
                {t(`auth.lead.${mode}`)}
              </Typography>
              <HelpTip i18nKey={`auth.help.${mode}`} ns="common" values={{ min }} />
            </Box>
            {info && <Typography variant="caption" sx={{ color: '#34d399' }}>{info}</Typography>}
            {mode === 'register' && (
              <TextField size="small" label={t('auth.field.name')} autoComplete="name" required
                value={name} onChange={(e) => setName(e.target.value)} fullWidth
                error={!!show(nameErr)} helperText={show(nameErr)} />
            )}
            {mode !== 'reset' && (
              <TextField size="small" label={t('auth.field.email')} type="email" autoComplete="username" required
                value={email} onChange={(e) => setEmail(e.target.value)} fullWidth
                error={!!show(emailErr)} helperText={show(emailErr)} />
            )}
            {mode !== 'forgot' && (
              <TextField size="small" label={needsNewPw ? t('auth.field.newPassword', { min }) : t('auth.field.password')}
                type="password" required
                autoComplete={needsNewPw ? 'new-password' : 'current-password'}
                value={password} onChange={(e) => setPassword(e.target.value)} fullWidth
                error={!!show(pwErr)} helperText={show(pwErr)} />
            )}
            {needsNewPw && (
              <TextField size="small" label={t('auth.field.repeatPassword')} type="password" autoComplete="new-password" required
                value={password2} onChange={(e) => setPassword2(e.target.value)} fullWidth
                error={!!show(pw2Err)} helperText={show(pw2Err)} />
            )}
            {mode === 'register' && consentBox}
            {err && <Typography variant="caption" color="error" role="alert">{err}</Typography>}
            <Button type="submit" variant="contained" disabled={busy}
              sx={{ textTransform: 'none' }}>
              {busy ? <CircularProgress size={18} sx={{ color: 'inherit' }} /> : cta}
            </Button>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
              {mode === 'signin' ? (
                <>
                  <Link component="button" type="button" onClick={() => switchMode('register')}>{t('auth.link.createAccount')}</Link>
                  <Link component="button" type="button" onClick={() => switchMode('forgot')}>{t('auth.link.forgot')}</Link>
                </>
              ) : (
                <Link component="button" type="button" onClick={() => switchMode('signin')}>{t('auth.link.back')}</Link>
              )}
            </Box>
          </Box>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default LoginDialog;
