// Sign-in dialog: tabs "Google | E-mail". Google = a plain "Sign in with Google"
// button of OURS; Google's script is fetched only when it is clicked (it is
// blocked in mainland China and must never delay the app), then the official
// GIS button appears in its place. E-mail = sign in / create account /
// forgot password, plus the two mailed links (?verify=, ?reset=). Every path
// ends in OUR backend token — see lib/localAuth.ts.
import React, { useEffect, useRef, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, Box, TextField, Button,
  Typography, CircularProgress, Tabs, Tab, Link, Tooltip,
} from '@mui/material';
import {
  decodeJwtPayload, googleExchange, loadGis, GisUnreachableError, passwordLogin,
  registerAccount, verifyEmail, requestPasswordReset, confirmPasswordReset, resendVerification, changeSignupEmail, ApiErr,
  pendingEmailLink, clearEmailLink, pendingContinuation, PASSWORD_MIN_LEN,
  GOOGLE_CLIENT_ID, SOURCE_URL, type SessionUser,
} from '../../lib/localAuth';
import {
  TERMS_VERSION, acceptancePayload, emptyAcceptance, isTermsRequired, showTermsHint, signupAllowed, type AcceptanceForm,
} from '../../lib/legalLogic';
import { isChineseVisitor } from '../../i18n/detectInitialLanguage';
import { appLinkReturnPath } from '../../lib/appLink';
import HelpTip from '../common/HelpTip';
import CommPrefsBoxes from '../common/CommPrefsBoxes';
import TermsBox from '../common/TermsBox';
import { emptyTopics, tickedTopics, type Topics } from '../../lib/commPrefsLogic';
import { useTranslation } from 'react-i18next';

interface Props {
  open: boolean;
  onClose: () => void;
  onSignedIn: (token: string, user: SessionUser) => void;
  /** Open on "Create account" instead of "Sign in". */
  initialMode?: 'signin' | 'register';
  /** Open on this method; default = e-mail for a Chinese browser, else Google when configured. */
  initialTab?: 'google' | 'email';
  /** OAuth consent page to come back to after the e-mail confirmation. */
  returnTo?: string;
}

type Mode = 'signin' | 'register' | 'forgot' | 'reset';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Google first, except where Google is unreachable by default (mainland China). */
const defaultTab = (): 'google' | 'email' =>
  GOOGLE_CLIENT_ID && !isChineseVisitor() ? 'google' : 'email';

const LoginDialog: React.FC<Props> = ({ open, onClose, onSignedIn, initialMode, initialTab, returnTo }) => {
  const { t, i18n } = useTranslation(['common', 'errors']);
  const min = PASSWORD_MIN_LEN;
  const [tab, setTab] = useState<'google' | 'email'>(
    () => (GOOGLE_CLIENT_ID ? (initialTab ?? defaultTab()) : 'email'));
  // 'idle' = nothing fetched yet; 'loading' = the click is waiting for Google's script;
  // 'ready' = Google's own button is shown; 'unreachable' = blocked / too slow.
  const [gisState, setGisState] = useState<'idle' | 'loading' | 'ready' | 'unreachable'>('idle');
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
  // After sign-up: the "check your inbox" screen (resend, or fix a mistyped address).
  const [sent, setSent] = useState<{ email: string; ok: string | null } | null>(null);
  const [changing, setChanging] = useState(false);
  const [newEmail, setNewEmail] = useState('');
  const [cooldown, setCooldown] = useState(0);
  useEffect(() => { if (!open) { setSent(null); setChanging(false); } }, [open]);
  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const id = window.setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => window.clearTimeout(id);
  }, [cooldown]);
  // an error from the API in the interface language (the server adds a stable code + params beside the English text)
  const errText = (e: unknown): string => {
    const x = e as ApiErr;
    if (x && x.code && !x.code.startsWith('http.')) {
      return t(x.code, { ns: 'errors', ...(x.params ?? {}), defaultValue: x.message }) as string;
    }
    return (e as Error).message;
  };
  const gButtonRef = useRef<HTMLDivElement | null>(null);
  // E-mail opt-in topics (communication preferences): every box UNTICKED by default (GDPR); a ref so the GIS callback
  // registered once still reads the current boxes.  The server records them with the wording shown (comm_prefs.py).
  const [topics, setTopics] = useState<Topics>(emptyTopics());
  const topicsRef = useRef<Topics>(topics);
  topicsRef.current = topics;
  const uiLang = (i18n.resolvedLanguage ?? 'en') === 'zh-CN' ? 'zh-CN' : 'en';
  const langRef = useRef(uiLang);
  langRef.current = uiLang;

  const consentBox = <CommPrefsBoxes value={topics} onChange={setTopics} compact />;

  // Creating an account (form, first Google sign-in, sign-up from an AI app's consent page) needs the unticked terms box (Terms of
  // Use + Platform Agreement + Privacy Policy, owner 2026-10-27); the buttons stay off until it is ticked.  Refs so the GIS callback
  // registered once still reads the current state.  Signing in to an existing account never asks.
  const [acceptance, setAcceptance] = useState<AcceptanceForm>(emptyAcceptance());
  const acceptanceRef = useRef<AcceptanceForm>(acceptance);
  acceptanceRef.current = acceptance;
  const modeRef = useRef<Mode>('signin');
  modeRef.current = mode;
  const termsBox = <TermsBox value={acceptance} onChange={setAcceptance} compact />;
  const noBoxHint = t('legal.accept.hint');

  const termsLine = (
    <Typography sx={{ fontSize: 11, color: 'var(--text-3)', lineHeight: 1.5 }}>
      {t('auth.terms.license')}{' '}
      <Link href={SOURCE_URL} target="_blank" rel="noopener noreferrer">{t('auth.terms.source')}</Link>
    </Typography>
  );

  // A mailed link in the address bar: consume it once, then scrub the URL.
  // Without one, open in the mode the caller asked for (e.g. "Create account"
  // from the OAuth consent page).
  useEffect(() => {
    if (!open) return;
    if (initialTab && (initialTab === 'email' || GOOGLE_CLIENT_ID)) setTab(initialTab);
    const link = pendingEmailLink();
    if (!link) {
      if (initialMode === 'register') { setTab('email'); setMode('register'); setErr(null); setInfo(null); }
      else if (initialMode === 'signin') { setMode('signin'); setErr(null); setInfo(null); }
      return;
    }
    const continuation = pendingContinuation();
    clearEmailLink();
    setTab('email');
    if (link.kind === 'reset') {
      setResetToken(link.token); setMode('reset'); setErr(null); setInfo(null);
      return;
    }
    setMode('signin'); setBusy(true); setErr(null);
    // A sign-up started in an AI app's authorization window: the server
    // resumes that authorization only from the link's own continuation, bound
    // to this address.  Anything else must not stay on a consent page.
    void verifyEmail(link.token, continuation)
      .then((j) => {
        const onConsent = window.location.pathname === '/agent-consent';
        if (onConsent && !j.return_to) { window.location.replace('/?signin=1'); return; }
        if (j.return_to && `${window.location.pathname}${window.location.search}` !== j.return_to) {
          window.location.replace(j.return_to); return;
        }
        if (j.email) setEmail(j.email);
        setInfo(t('auth.info.emailConfirmed'));
      })
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(false));
  }, [open, initialMode, initialTab]);

  // The Google script is fetched HERE and nowhere else: on the click, with a
  // timeout.  When it arrives, Google's own button replaces ours (one more click).
  const startGoogle = () => {
    if (!GOOGLE_CLIENT_ID || gisState === 'loading') return;
    setGisState('loading'); setErr(null); setGisErr(null);
    void loadGis()
      .then((gis) => {
        gis.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: (resp: { credential?: string }) => {
            if (!resp.credential) return;
            const claims = decodeJwtPayload(resp.credential);
            // creating an account with Google needs the box; the official button cannot be disabled, so it is checked here too
            if (modeRef.current === 'register' && !signupAllowed({ creating: true, form: acceptanceRef.current })) { setErr(noBoxHint); return; }
            void googleExchange(resp.credential, tickedTopics(topicsRef.current), langRef.current,
              acceptancePayload(acceptanceRef.current, TERMS_VERSION))
              .then(({ token, user }) => onSignedIn(token, {
                ...user,
                name: user.name || String(claims.name ?? ''),
                picture: typeof claims.picture === 'string' ? claims.picture : undefined,
              }))
              .catch((e: Error) => {
                // a brand-new Google account that was started without the box: show it, the next click on Google carries it
                if (isTermsRequired(e)) { setMode('register'); setAcceptance(emptyAcceptance()); setErr(t('legal.accept.needBox')); return; }
                setErr(e.message);
              });
          },
        });
        if (gButtonRef.current) {
          gButtonRef.current.innerHTML = '';
          gis.renderButton(gButtonRef.current, {
            theme: 'outline', size: 'large', width: 280, text: 'signin_with',
          });
        }
        setGisState('ready');
        try { gis.prompt?.(); } catch { /* One Tap is optional */ }
        // Google's renderButton can fail silently (blocked iframe, origin, FedCM): an empty box would leave
        // NO way in (ours is hidden once 'ready').  If nothing was drawn, say so and offer e-mail (owner 2026-10-15).
        window.setTimeout(() => {
          const box = gButtonRef.current;
          if (box && !box.querySelector('iframe, [role="button"]') && box.getBoundingClientRect().height < 10) {
            setGisState('unreachable');
            setGisErr(t('auth.gisUnreachable'));
          }
        }, 2500);
      })
      .catch((e: Error) => {
        setGisState('unreachable');
        setGisErr(e instanceof GisUnreachableError || /load|script|google/i.test(e.message)
          ? t('auth.gisUnreachable') : e.message);
      });
  };

  // The Dialog unmounts its content on close (and the Google tab on a tab switch), so Google's button is gone
  // while gisState stays 'ready' - which hides ours: an empty box with nothing to click (owner 2026-10-21, on a
  // second sign-in; only a page reload helped).  The box mounts inside the Dialog's portal AFTER this component's
  // effects ran, so a callback ref (it fires when the node itself attaches) redraws Google's button there; the
  // script and its initialize() are already done.  If Google draws nothing, offer the e-mail fallback.
  const gisStateRef = useRef(gisState);
  gisStateRef.current = gisState;
  const setGButtonBox = (node: HTMLDivElement | null) => {
    gButtonRef.current = node;
    if (!node || node.firstChild || gisStateRef.current !== 'ready') return;
    void loadGis().then((gis) => {
      if (node.isConnected && !node.firstChild) {
        gis.renderButton(node, { theme: 'outline', size: 'large', width: 280, text: 'signin_with' });
      }
      window.setTimeout(() => {
        if (node.isConnected && !node.querySelector('iframe, [role="button"]') && node.getBoundingClientRect().height < 10) {
          setGisState('unreachable');
          setGisErr(t('auth.gisUnreachable'));
        }
      }, 2500);
    }).catch(() => setGisState('idle'));
  };

  const switchMode = (m: Mode) => {
    setMode(m); setErr(null); setInfo(null); setTouched(false); setAcceptance(emptyAcceptance());
    setPassword(''); setPassword2('');
    setSent(null); setChanging(false);
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
  const creating = mode === 'register';
  const boxHint = showTermsHint({ creating, form: acceptance });

  const submit = async () => {
    setTouched(true);
    if (busy || invalid || !signupAllowed({ creating, form: acceptance })) return;
    setBusy(true); setErr(null); setInfo(null);
    try {
      if (mode === 'signin') {
        const { token, user } = await passwordLogin(email.trim(), password);
        setEmail(''); setPassword('');
        onSignedIn(token, user);
      } else if (mode === 'register') {
        await registerAccount(email.trim(), password, name.trim(), tickedTopics(topics), uiLang, returnTo,
          appLinkReturnPath(window.location.pathname, window.location.search), acceptancePayload(acceptance, TERMS_VERSION));
        setPassword(''); setPassword2(''); setTouched(false); setTopics(emptyTopics()); setAcceptance(emptyAcceptance());
        setMode('signin');
        setSent({ email: email.trim(), ok: null }); setChanging(false); setNewEmail(''); setCooldown(60);
        setInfo(null);
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
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    if (!sent || busy || cooldown > 0) return;
    setBusy(true); setErr(null);
    try {
      await resendVerification(sent.email, appLinkReturnPath(window.location.pathname, window.location.search));
      setSent({ ...sent, ok: t('auth.sent.resent') }); setCooldown(60);
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };

  const changeAddress = async () => {
    if (!sent || busy) return;
    if (!EMAIL_RE.test(newEmail.trim()) || !password) { setTouched(true); return; }
    setBusy(true); setErr(null);
    try {
      await changeSignupEmail(sent.email, password, newEmail.trim(), i18n.resolvedLanguage === 'zh-CN' ? 'zh-CN' : 'en');
      setSent({ email: newEmail.trim(), ok: t('auth.sent.changed', { email: newEmail.trim() }) });
      setChanging(false); setNewEmail(''); setPassword(''); setTouched(false); setCooldown(60);
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };

  const show = (msg: string) => (touched && msg ? msg : undefined);
  const title = sent && mode === 'signin' ? t('auth.sent.title') : t(`auth.title.${mode}`);
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
            <Tooltip title={boxHint ? noBoxHint : ''}>
              <Box component="span" sx={{ display: 'block' }}>
                {/* the official Google button cannot be disabled: while the box is unticked it is dimmed and takes no clicks */}
                <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1,
                  ...(boxHint ? { opacity: 0.45, pointerEvents: 'none' as const } : {}) }} data-testid="google-area" aria-disabled={boxHint}>
                  <div ref={setGButtonBox} />
                  {gisState !== 'ready' && (
                    <Button variant="outlined" onClick={startGoogle} disabled={gisState === 'loading' || boxHint}
                      data-testid="google-signin"
                      sx={{ textTransform: 'none', width: 280, py: 1, fontWeight: 600 }}>
                      {gisState === 'loading' ? <CircularProgress size={18} sx={{ color: 'inherit' }} /> : t('auth.google.signIn')}
                    </Button>
                  )}
                </Box>
              </Box>
            </Tooltip>
            {/* The e-mail topics are asked only when creating an account (owner 2026-10-24); a returning user
                changes them on the account page (CommPrefsPage), not at every sign-in. */}
            {mode === 'register' ? (
              <>
                {consentBox}
                <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mt: -0.75 }}>{t('auth.consent.newAccountOnly')}</Typography>
                {termsBox}
              </>
            ) : (
              <Link component="button" type="button" sx={{ fontSize: 12 }} onClick={() => switchMode('register')}>
                {t('auth.link.createAccount')}
              </Link>
            )}
            {termsLine}
            {gisErr && (
              <Typography variant="caption" color="error" role="alert">
                {gisErr}{' '}
                <Link component="button" type="button" onClick={() => { setTab('email'); setErr(null); }}>
                  {t('auth.google.useEmail')}
                </Link>
              </Typography>
            )}
            {err && <Typography variant="caption" color="error">{err}</Typography>}
          </Box>
        ) : sent && mode === 'signin' ? (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, pt: 0.5 }} data-testid="signup-sent">
            <Typography sx={{ fontSize: 13 }}>
              {t(returnTo ? 'auth.sent.textReturn' : 'auth.sent.text', { email: sent.email })}
            </Typography>
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('auth.sent.spam')}</Typography>
            {sent.ok && <Typography variant="caption" sx={{ color: '#34d399' }} role="status">{sent.ok}</Typography>}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
              <Button size="small" variant="outlined" disabled={busy || cooldown > 0} onClick={() => void resend()}
                sx={{ textTransform: 'none' }}>
                {cooldown > 0 ? t('auth.sent.resendWait', { seconds: cooldown }) : t('auth.sent.resend')}
              </Button>
              <Link component="button" type="button" sx={{ fontSize: 12 }}
                onClick={() => { setChanging((c) => !c); setErr(null); setNewEmail(''); setPassword(''); setTouched(false); }}>
                {t('auth.sent.wrong')}
              </Link>
            </Box>
            {changing && (
              <Box component="form" noValidate onSubmit={(e) => { e.preventDefault(); void changeAddress(); }}
                sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                  <Typography sx={{ fontSize: 12, color: 'var(--text-3)', flex: 1 }}>{t('auth.sent.newEmail')}</Typography>
                  <HelpTip title={t('auth.sent.changeHelp')} />
                </Box>
                <TextField size="small" type="email" autoComplete="off" value={newEmail} required fullWidth
                  label={t('auth.sent.newEmail')} onChange={(e) => setNewEmail(e.target.value)}
                  error={touched && !EMAIL_RE.test(newEmail.trim())} />
                <TextField size="small" type="password" autoComplete="current-password" value={password} required fullWidth
                  label={t('auth.sent.password')} onChange={(e) => setPassword(e.target.value)}
                  error={touched && !password} />
                <Button type="submit" variant="contained" size="small" disabled={busy} sx={{ textTransform: 'none' }}>
                  {busy ? <CircularProgress size={16} sx={{ color: 'inherit' }} /> : t('auth.sent.change')}
                </Button>
              </Box>
            )}
            {err && <Typography variant="caption" color="error" role="alert">{err}</Typography>}
            <Box sx={{ fontSize: 12 }}>
              <Link component="button" type="button" onClick={() => switchMode('signin')}>{t('auth.link.back')}</Link>
            </Box>
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
            {mode === 'register' && termsBox}
            {mode === 'register' && termsLine}
            {err && <Typography variant="caption" color="error" role="alert">{err}</Typography>}
            <Tooltip title={boxHint ? noBoxHint : ''}>
              <Box component="span" sx={{ display: 'block' }}>
                <Button type="submit" variant="contained" fullWidth disabled={busy || boxHint}
                  sx={{ textTransform: 'none' }}>
                  {busy ? <CircularProgress size={18} sx={{ color: 'inherit' }} /> : cta}
                </Button>
              </Box>
            </Tooltip>
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
