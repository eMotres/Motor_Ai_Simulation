// Communication preferences page: /preferences (and /zh-cn/preferences).
//
// Opened from the mailed link (?t=<signed token>, no sign-in needed; &action=unsubscribe unsubscribes everything on load) or
// from the account menu when signed in.  Four opt-in boxes, all UNTICKED until the person ticks them (GDPR), a Save button, and
// "Unsubscribe from all".  Also the NDA status with its call to action: sign online when signing is open, request it when it is
// not (a support ticket, the existing path).  Backend: routes/comm_prefs.py, src/motor_ai_sim/comm_prefs.py.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Box, Button, CircularProgress, Link, Paper, ThemeProvider, CssBaseline, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BrandLogo from '../common/BrandLogo';
import CommPrefsBoxes from '../common/CommPrefsBoxes';
import HelpTip from '../common/HelpTip';
import LanguageSwitcher from '../common/LanguageSwitcher';
import NdaDialog from '../model3d/NdaDialog';
import DataSharingDialog from './DataSharingDialog';
import { useAuth } from '../../contexts/AuthContext';
import { privacyUrl } from '../../lib/localAuth';
import { buildAppTheme, loadThemeMode } from '../../theme';
import {
  changedTopics, emptyTopics, maskEmail, ndaCta, ndaRequestTicket, readLink, type Topics,
} from '../../lib/commPrefsLogic';
import {
  fileTicket, getByToken, getMine, saveByToken, saveMine, unsubscribeByToken, unsubscribeMine, type PrefsState,
} from '../../lib/commPrefsApi';

const COMPANY = 'MOTRES d.o.o., Kotnikova 34, 1000 Ljubljana, Slovenia';
const card = { p: 2, mb: 2, border: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)', borderRadius: 1.5 } as const;

const CommPrefsPage: React.FC = () => {
  const { t, i18n } = useTranslation('common');
  const { user, resolved, signIn } = useAuth();
  const theme = useMemo(() => buildAppTheme(loadThemeMode()), []);
  const lang = (i18n.resolvedLanguage ?? 'en') === 'zh-CN' ? 'zh-CN' : 'en';
  const { token, action } = useMemo(() => readLink(window.location.search), []);

  const [state, setState] = useState<PrefsState | null>(null);
  const [draft, setDraft] = useState<Topics>(emptyTopics());
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [ndaOpen, setNdaOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [ndaAsked, setNdaAsked] = useState(false);
  const [ndaErr, setNdaErr] = useState<string | null>(null);
  const unsubDone = useRef(false);

  // the link carries a credential: never send it on as a referrer
  useEffect(() => {
    const m = document.createElement('meta');
    m.name = 'referrer'; m.content = 'no-referrer';
    document.head.appendChild(m);
    document.title = t('commPrefs.title');
    return () => { m.remove(); };
  }, [t]);

  const apply = useCallback((s: PrefsState) => { setState(s); setDraft(s.topics); }, []);

  // `t` changes identity as namespaces load: keep it out of the effect's dependencies, or a failing load would loop
  const tRef = useRef(t);
  tRef.current = t;
  const userEmail = user?.email ?? '';

  const load = useCallback(async () => {
    const tt = tRef.current;
    setLoadErr(null);
    try {
      if (token && action === 'unsubscribe' && !unsubDone.current) {
        unsubDone.current = true;                     // runs in JS only: a link scanner that merely fetches the page cannot unsubscribe anyone
        apply(await unsubscribeByToken(token, lang));
        setMsg({ ok: true, text: tt('commPrefs.unsubscribed') });
      } else if (token) {
        apply(await getByToken(token));
      } else {
        apply(await getMine());
      }
    } catch (e) {
      setLoadErr(token ? tt('commPrefs.invalidLink') : tt('commPrefs.loadError', { message: (e as Error).message }));
    }
  }, [token, action, lang, apply]);

  // one load per link / signed-in account (not per render)
  useEffect(() => {
    if (token || (resolved && userEmail)) void load();
  }, [token, resolved, userEmail, load]);

  const dirty = state ? Object.keys(changedTopics(state.topics, draft)).length > 0 : false;

  const run = async (fn: () => Promise<PrefsState>, okText: string, errKey: string) => {
    setBusy(true); setMsg(null);
    try { apply(await fn()); setMsg({ ok: true, text: okText }); }
    catch (e) { setMsg({ ok: false, text: t(errKey, { message: (e as Error).message }) }); }
    finally { setBusy(false); }
  };
  const save = () => state && run(
    () => (token ? saveByToken(token, changedTopics(state.topics, draft), lang) : saveMine(changedTopics(state.topics, draft), lang)),
    t('commPrefs.saved'), 'commPrefs.saveError');
  const unsubscribeAll = () => run(
    () => (token ? unsubscribeByToken(token, lang) : unsubscribeMine(lang)), t('commPrefs.unsubscribed'), 'commPrefs.saveError');

  // NDA actions act on the SIGNED-IN account, so they are offered only when it is the account of this link
  const signedInHere = !!user && (!token || !state || maskEmail(user.email ?? '') === state.account);
  const cta = state ? ndaCta(state.nda, signedInHere) : null;
  const askNda = async () => {
    setNdaErr(null);
    try { await fileTicket(ndaRequestTicket(lang)); setNdaAsked(true); }
    catch (e) { setNdaErr(t('commPrefs.nda.requestError', { message: (e as Error).message })); }
  };

  const needSignIn = !token && resolved && !user;
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ minHeight: '100vh', bgcolor: 'var(--bg)', color: 'var(--text)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 2, py: 1.25, borderBottom: '1px solid var(--line-soft)' }}>
          <BrandLogo size={30} />
          <Typography sx={{ fontWeight: 800, fontSize: 16 }}>AeroStator</Typography>
          <Box sx={{ flex: 1 }} />
          <LanguageSwitcher variant="inline" />
          {!user && resolved && (
            <Button size="small" variant="outlined" onClick={() => void signIn()} sx={{ textTransform: 'none', fontSize: 12, ml: 1 }}>
              {t('commPrefs.signIn')}
            </Button>
          )}
        </Box>

        <Box sx={{ maxWidth: 640, mx: 'auto', px: 2, py: 3 }}>
          <Paper variant="outlined" sx={card} data-testid="comm-prefs-card">
            <Typography component="h1" sx={{ fontSize: 18, fontWeight: 800 }}>{t('commPrefs.title')}</Typography>
            {state && <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mb: 1.5 }}>{t('commPrefs.for', { account: state.account })}</Typography>}

            {needSignIn && (
              <Box sx={{ mt: 1.5 }}>
                <Typography sx={{ fontSize: 13, mb: 1 }}>{t('commPrefs.signInHint')}</Typography>
                <Button size="small" variant="contained" onClick={() => void signIn()} sx={{ textTransform: 'none' }}>{t('commPrefs.signIn')}</Button>
              </Box>
            )}
            {loadErr && <Alert severity="error" sx={{ fontSize: 12.5, mt: 1.5 }}>{loadErr}</Alert>}
            {!state && !loadErr && !needSignIn && <Box sx={{ p: 2, textAlign: 'center' }}><CircularProgress size={22} /></Box>}

            {state && (
              <>
                <CommPrefsBoxes value={draft} onChange={setDraft} disabled={busy} />
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1.5, flexWrap: 'wrap' }}>
                  <Button size="small" variant="contained" disabled={busy || !dirty} onClick={() => void save()} sx={{ textTransform: 'none' }}>
                    {t('commPrefs.save')}
                  </Button>
                  <Button size="small" color="inherit" disabled={busy || (state.unsubscribed_all && !dirty)} onClick={() => void unsubscribeAll()}
                    sx={{ textTransform: 'none' }}>
                    {t('commPrefs.unsubscribeAll')}
                  </Button>
                  <HelpTip title={t('commPrefs.unsubscribeAllHelp')} />
                </Box>
                {msg && <Alert severity={msg.ok ? 'success' : 'error'} sx={{ fontSize: 12.5, mt: 1.5 }}>{msg.text}</Alert>}
                {!msg && state.unsubscribed_all && <Alert severity="info" sx={{ fontSize: 12.5, mt: 1.5 }}>{t('commPrefs.unsubscribed')}</Alert>}
                <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)', mt: 1.5 }}>
                  {t('commPrefs.legal')}{' '}
                  <Link href={privacyUrl(lang)} target="_blank" rel="noopener noreferrer">{t('commPrefs.privacy')}</Link>
                  {' · '}{t('commPrefs.textVersion', { version: state.text_version })}
                </Typography>
              </>
            )}
          </Paper>

          {state && cta && (
            <Paper variant="outlined" sx={card} data-testid="comm-prefs-nda">
              <Typography component="h2" sx={{ fontSize: 15, fontWeight: 800 }}>{t('commPrefs.nda.title')}</Typography>
              <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)', mb: 1 }}>
                {cta === 'signed' ? t('commPrefs.nda.signed') : t('commPrefs.nda.intro')}
              </Typography>
              {cta === 'sign' && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                  <Button size="small" variant="contained" onClick={() => setNdaOpen(true)} sx={{ textTransform: 'none' }}>{t('commPrefs.nda.sign')}</Button>
                  <HelpTip title={t('commPrefs.nda.signHelp')} />
                </Box>
              )}
              {cta === 'request' && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                  <Button size="small" variant="contained" disabled={ndaAsked} onClick={() => void askNda()} sx={{ textTransform: 'none' }}>
                    {t('commPrefs.nda.request')}
                  </Button>
                  <HelpTip title={t('commPrefs.nda.requestHelp')} />
                </Box>
              )}
              {(cta === 'signin_sign' || cta === 'signin_request') && (
                <Button size="small" variant="outlined" onClick={() => void signIn()} sx={{ textTransform: 'none' }}>
                  {cta === 'signin_sign' ? t('commPrefs.nda.signinSign') : t('commPrefs.nda.signinRequest')}
                </Button>
              )}
              {ndaAsked && <Alert severity="success" sx={{ fontSize: 12.5, mt: 1 }}>{t('commPrefs.nda.requested')}</Alert>}
              {ndaErr && <Alert severity="error" sx={{ fontSize: 12.5, mt: 1 }}>{ndaErr}</Alert>}
            </Paper>
          )}

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', fontSize: 13 }}>
            <Link href="/">{t('commPrefs.openApp')}</Link>
            {user && <Link component="button" type="button" onClick={() => setShareOpen(true)}>{t('commPrefs.dataSharing')}</Link>}
          </Box>
          <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mt: 2 }}>{COMPANY}</Typography>
        </Box>

        <NdaDialog open={ndaOpen} onClose={() => setNdaOpen(false)}
          onSigned={() => { setNdaOpen(false); void load(); }} />
        <DataSharingDialog open={shareOpen} onClose={() => setShareOpen(false)} />
      </Box>
    </ThemeProvider>
  );
};

export default CommPrefsPage;
