// The legal texts, read-only and readable signed out: /terms, /privacy and /platform-agreement (the agreement and its Schedules), each
// also under /zh-cn/.  The text is served verbatim by GET /api/legal/{doc}?lang= (the English text governs, zh-CN is a full mirror);
// the language follows the interface language.  Light/dark like the other standalone pages, no horizontal scroll at phone width.
import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Box, Button, CircularProgress, CssBaseline, Link, Paper, ThemeProvider, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BrandLogo from '../common/BrandLogo';
import CookieSettingsLink from '../common/CookieSettingsLink';
import LanguageSwitcher from '../common/LanguageSwitcher';
import LegalMarkdown from './LegalMarkdown';
import { useAuth } from '../../contexts/AuthContext';
import { buildAppTheme, loadThemeMode } from '../../theme';
import { fetchLegalText, type LegalText } from '../../lib/legalApi';
import { LEGAL_PAGES, PAGE_DOCS, legalPath, type LegalLang, type LegalPageKind } from '../../lib/legalLogic';

const COMPANY = 'MOTRES d.o.o., Kotnikova 34, 1000 Ljubljana, Slovenia';

const LegalPage: React.FC<{ kind: LegalPageKind }> = ({ kind }) => {
  const { t, i18n } = useTranslation('common');
  const { user, resolved, signIn } = useAuth();
  const theme = useMemo(() => buildAppTheme(loadThemeMode()), []);
  const lang: LegalLang = (i18n.resolvedLanguage ?? 'en') === 'zh-CN' ? 'zh-CN' : 'en';
  const [texts, setTexts] = useState<LegalText[] | null>(null);
  const [failed, setFailed] = useState(false);

  // the address follows the language (a /zh-cn/ link opened by someone who chose English shows /terms, and the switcher moves it back)
  useEffect(() => {
    try {
      const want = legalPath(kind, lang);
      if (window.location.pathname.replace(/\/$/, '') !== want) window.history.replaceState(null, '', want + window.location.search + window.location.hash);
    } catch { /* no history API: the page still reads */ }
  }, [kind, lang]);

  const title = t(`legal.page.${kind}`);
  useEffect(() => { document.title = `${title} - AeroStator`; }, [title]);

  useEffect(() => {
    const ctl = new AbortController();
    setTexts(null); setFailed(false);
    Promise.all(PAGE_DOCS[kind].map((d) => fetchLegalText(d, lang, ctl.signal)))
      .then(setTexts)
      .catch((e: Error) => { if (e.name !== 'AbortError') setFailed(true); });
    return () => ctl.abort();
  }, [kind, lang]);

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ minHeight: '100vh', bgcolor: 'var(--bg)', color: 'var(--text)', overflowX: 'hidden' }}>
        <Box component="header" sx={{ display: 'flex', alignItems: 'center', gap: 1, px: { xs: 2, sm: 2.5 }, py: 1.25, borderBottom: '1px solid var(--line-soft)', flexWrap: 'wrap' }}>
          <Link href={lang === 'zh-CN' ? '/zh-cn/' : '/'} underline="none" color="inherit" sx={{ display: 'inline-flex', alignItems: 'center', gap: 1 }}>
            <BrandLogo size={28} />
            <Typography sx={{ fontWeight: 800, fontSize: 16 }}>AeroStator</Typography>
          </Link>
          <Box sx={{ flex: 1 }} />
          <LanguageSwitcher variant="inline" />
          {!user && resolved && (
            <Button size="small" variant="outlined" onClick={() => void signIn()} sx={{ textTransform: 'none', fontSize: 12 }}>
              {t('commPrefs.signIn')}
            </Button>
          )}
        </Box>

        <Box component="main" sx={{ maxWidth: 780, mx: 'auto', px: { xs: 2, sm: 3 }, py: { xs: 2.5, sm: 4 } }}>
          <Box component="nav" aria-label={t('legal.page.nav')} sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', fontSize: 13, mb: 2 }}>
            {LEGAL_PAGES.map((k) => (k === kind
              ? <Box key={k} component="span" aria-current="page" sx={{ fontWeight: 700 }}>{t(`legal.page.${k}`)}</Box>
              : <Link key={k} href={legalPath(k, lang)} underline="hover">{t(`legal.page.${k}`)}</Link>))}
          </Box>

          {failed && <Alert severity="error" sx={{ fontSize: 12.5 }}>{t('legal.page.loadError')}</Alert>}
          {!texts && !failed && <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress size={22} /></Box>}
          {texts && texts.map((x, i) => (
            <Paper key={x.doc} component="section" variant="outlined" data-testid={`legal-${x.doc}`}
              sx={{ p: { xs: 2, sm: 3 }, mb: 2, border: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)', borderRadius: 1.5, overflowWrap: 'anywhere' }}>
              <LegalMarkdown text={x.text} />
              {i === texts.length - 1 && (
                <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)', mt: 1 }}>
                  {t('legal.page.version', { version: x.version })}
                </Typography>
              )}
            </Paper>
          ))}

          <Box component="footer" sx={{ mt: 3, pt: 2, borderTop: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', fontSize: 12.5, color: 'var(--text-3)' }}>
            <Link href="/" underline="hover">{t('commPrefs.openApp')}</Link>
            <Link href={lang === 'zh-CN' ? '/zh-cn/preferences' : '/preferences'} underline="hover">{t('commPrefs.title')}</Link>
            <CookieSettingsLink />
            <Box sx={{ flex: 1 }} />
            <Typography component="span" sx={{ fontSize: 11.5, color: 'var(--text-4)' }}>{COMPANY}</Typography>
          </Box>
        </Box>
      </Box>
    </ThemeProvider>
  );
};

export default LegalPage;
