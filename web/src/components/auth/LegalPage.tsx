// Standalone Privacy Policy (/privacy, /zh-cn/privacy) and Terms & Conditions (/terms, /zh-cn/terms) pages
// for the AeroStator app at aerostator.com, adapted from the eMotres marketing-site texts.
//
// Readable without sign-in, supports EN and zh-CN with an inline switcher, and light/dark theme toggle.
//
// [OWNER REVIEW] Items marked for owner review before the 19.10 launch:
// 1. Privacy Policy — "Last updated: October 2026" date (updated from May 2025 on emotres.com).
// 2. Privacy Policy §2 & §4 — Exact retention period for per-user activity time (heartbeat logs) and server logs,
//    and retention of workspace simulation data & models until account deletion (7-day grace period) vs. signed NDA
//    and model download audit records retained for as long as claims may arise under the NDA.
// 3. Privacy Policy §3 — Legal basis mapping for app-specific processing: contract performance (Art. 6(1)(b) GDPR)
//    for account, workspace simulations/models, and NDA/download records; legitimate interest (Art. 6(1)(f) GDPR)
//    for cookieless Umami analytics, security logs, and per-user activity time in the admin view; consent (Art. 6(1)(a) GDPR)
//    for Google Analytics cookies and opt-in launch/topic mailing at /preferences.
// 4. Privacy Policy §5 — Sub-processor list for the app: Hetzner Online GmbH (servers in Germany) and Google LLC
//    (optional Google sign-in and consent-gated Google Analytics outside China), replacing Firebase Hosting from emotres.com,
//    plus optional anonymised dataset sharing if opted in by the user.
// 5. Terms & Conditions — Scope combining use of the AeroStator web application (accounts, Apache-2.0 software licence vs.
//    patent-pending AeroStator Core technology, NDA-gated 3-D/STEP downloads, "as is" simulation disclaimer) with the
//    commercial terms for custom-made motor orders from emotres.com (lead times, 50% deposit, GLS/DHL shipping, 1-year warranty).
// 6. Terms & Conditions §13 — Governing law and jurisdiction: Republic of Slovenia / courts of Ljubljana (as in emotres.com),
//    noting that a signed NDA or separate written contract prevails where it specifies its own dispute resolution (e.g. HKIAC).
import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, Button, CssBaseline, IconButton, Link, Paper, ThemeProvider, Tooltip, Typography,
} from '@mui/material';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import LightModeIcon from '@mui/icons-material/LightMode';
import { useTranslation } from 'react-i18next';
import BrandLogo from '../common/BrandLogo';
import CookieSettingsLink from '../common/CookieSettingsLink';
import LanguageSwitcher from '../common/LanguageSwitcher';
import AnalyticsConsent from '../common/AnalyticsConsent';
import { useAuth } from '../../contexts/AuthContext';
import { PRIVACY_URL, SOURCE_URL, TERMS_URL } from '../../lib/localAuth';
import {
  PRIVACY_SECTION_IDS, TERMS_SECTION_IDS, type LegalPageKind,
} from '../../lib/legalLogic';
import { buildAppTheme, loadThemeMode, saveThemeMode, type AppMode } from '../../theme';

export const COMPANY_NAME = 'MOTRES d.o.o.';
export const COMPANY_ADDRESS = 'Kotnikova 34, Ljubljana 1000, Slovenia (EU)';
export const COMPANY_REG = '7255691000';
export const COMPANY_VAT = 'SI22399933';
export const SUPPORT_EMAIL = 'support@aerostator.com';
export const PREFERENCES_PATH = '/preferences';
export const IP_RS_URL = 'https://www.ip-rs.si';

const cardSx = {
  p: { xs: 2, sm: 2.5 },
  mb: 2,
  border: '1px solid var(--line-soft)',
  bgcolor: 'var(--panel-2)',
  borderRadius: 1.5,
} as const;

const bodySx = {
  fontSize: { xs: '0.88rem', sm: '0.92rem' },
  color: 'var(--text-2)',
  lineHeight: 1.65,
} as const;

const h2Sx = {
  fontSize: { xs: '1rem', sm: '1.08rem' },
  fontWeight: 700,
  color: 'var(--text)',
  mb: 1,
} as const;

const LegalPage: React.FC<{ kind: LegalPageKind }> = ({ kind }) => {
  const { t } = useTranslation('common');
  const { user, resolved, signIn } = useAuth();
  const [themeMode, setThemeMode] = useState<AppMode>(() => loadThemeMode());
  useEffect(() => { saveThemeMode(themeMode); }, [themeMode]);
  const theme = useMemo(() => buildAppTheme(themeMode), [themeMode]);

  const pageTitle = t(`legal.${kind}.title`);
  useEffect(() => {
    document.title = `${pageTitle} — AeroStator`;
  }, [pageTitle]);

  const sections = kind === 'privacy' ? PRIVACY_SECTION_IDS : TERMS_SECTION_IDS;
  const otherKind: LegalPageKind = kind === 'privacy' ? 'terms' : 'privacy';
  const otherHref = otherKind === 'privacy' ? PRIVACY_URL : TERMS_URL;

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ minHeight: '100vh', bgcolor: 'var(--bg)', color: 'var(--text)', display: 'flex', flexDirection: 'column' }}>
        <Box component="header" sx={{
          display: 'flex', alignItems: 'center', gap: 1, px: { xs: 1.5, sm: 2.5 }, py: 1.25,
          borderBottom: '1px solid var(--line-soft)', bgcolor: 'background.paper', flexWrap: 'wrap',
        }}>
          <Link href="/" underline="none" color="inherit"
            sx={{ display: 'inline-flex', alignItems: 'center', gap: 1 }}>
            <BrandLogo size={28} />
            <Typography sx={{ fontWeight: 800, fontSize: 16 }}>
              AeroStator{' '}
              <Box component="span" sx={{ fontWeight: 300, opacity: 0.7 }}>Core</Box>
            </Typography>
          </Link>
          <Box sx={{ flex: 1 }} />
          <LanguageSwitcher variant="inline" />
          <Tooltip title={themeMode === 'light' ? t('theme.dark') : t('theme.light')}>
            <IconButton size="small" aria-label={themeMode === 'light' ? t('theme.dark') : t('theme.light')}
              onClick={() => setThemeMode((m) => (m === 'light' ? 'dark' : 'light'))}>
              {themeMode === 'light' ? <DarkModeIcon fontSize="small" /> : <LightModeIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
          {!user && resolved && (
            <Button size="small" variant="outlined" onClick={() => void signIn()}
              sx={{ textTransform: 'none', fontSize: 12 }}>
              {t('account.signIn')}
            </Button>
          )}
        </Box>

        <Box component="main" sx={{ flex: 1, width: '100%', maxWidth: 760, mx: 'auto', px: { xs: 2, sm: 3 }, py: { xs: 3, sm: 4 } }}>
          <Box sx={{ mb: 3 }}>
            <Typography component="h1" sx={{ fontSize: { xs: '1.6rem', sm: '2rem' }, fontWeight: 800, mb: 0.5 }}>
              {pageTitle}
            </Typography>
            <Typography sx={{ fontSize: '0.82rem', color: 'var(--text-3)' }}>
              {t('legal.lastUpdated')}
              {' · '}
              <Link href={otherHref} underline="hover">{t(`legal.${otherKind}.title`)}</Link>
              {' · '}
              <Link href={PREFERENCES_PATH} underline="hover">{t('commPrefs.title')}</Link>
            </Typography>
          </Box>

          {sections.map((secId, idx) => (
            <Paper key={secId} component="section" variant="outlined" sx={cardSx} data-testid={`legal-${kind}-${secId}`}>
              <Typography component="h2" sx={h2Sx}>
                {idx + 1}. {t(`legal.${kind}.sections.${secId}.title`)}
              </Typography>
              <Typography component="p" sx={bodySx}>
                {t(`legal.${kind}.sections.${secId}.body`)}
              </Typography>
              {kind === 'privacy' && secId === 'analytics' && (
                <Typography component="p" sx={{ ...bodySx, mt: 1 }}>
                  <Link href={PREFERENCES_PATH} underline="hover">{t('commPrefs.title')}</Link>
                  <CookieSettingsLink prefix=" · " />
                </Typography>
              )}
              {kind === 'privacy' && secId === 'rights' && (
                <Typography component="p" sx={{ ...bodySx, mt: 1 }}>
                  <Link href={`mailto:${SUPPORT_EMAIL}`} underline="hover">{SUPPORT_EMAIL}</Link>
                  {' · '}
                  <Link href={PREFERENCES_PATH} underline="hover">{t('commPrefs.title')}</Link>
                  {' · '}
                  <Link href={IP_RS_URL} target="_blank" rel="noopener noreferrer" underline="hover">ip-rs.si</Link>
                </Typography>
              )}
              {kind === 'terms' && secId === 'license' && (
                <Typography component="p" sx={{ ...bodySx, mt: 1 }}>
                  <Link href={SOURCE_URL} target="_blank" rel="noopener noreferrer" underline="hover">
                    {t('auth.terms.source')}
                  </Link>
                </Typography>
              )}
              {kind === 'terms' && secId === 'nda' && (
                <Typography component="p" sx={{ ...bodySx, mt: 1 }}>
                  <Link href={PREFERENCES_PATH} underline="hover">{t('commPrefs.title')}</Link>
                </Typography>
              )}
              {secId === 'contact' && (
                <Typography component="p" sx={{ ...bodySx, mt: 1 }}>
                  <strong>{COMPANY_NAME}</strong>, {COMPANY_ADDRESS} ({COMPANY_REG} · VAT {COMPANY_VAT}) —{' '}
                  <Link href={`mailto:${SUPPORT_EMAIL}`} underline="hover">{SUPPORT_EMAIL}</Link>
                </Typography>
              )}
            </Paper>
          ))}

          <Box component="footer" sx={{
            mt: 3, pt: 2, borderTop: '1px solid var(--line-soft)',
            display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap',
            fontSize: 12.5, color: 'var(--text-3)',
          }}>
            <Link href="/" underline="hover">{t('commPrefs.openApp')}</Link>
            <Link href={otherHref} underline="hover">{t(`legal.${otherKind}.title`)}</Link>
            <Link href={PREFERENCES_PATH} underline="hover">{t('commPrefs.title')}</Link>
            <CookieSettingsLink />
            <Box sx={{ flex: 1 }} />
            <Typography component="span" sx={{ fontSize: 11.5, color: 'var(--text-4)' }}>
              {COMPANY_NAME}
            </Typography>
          </Box>
        </Box>

        <AnalyticsConsent />
      </Box>
    </ThemeProvider>
  );
};

export default LegalPage;
