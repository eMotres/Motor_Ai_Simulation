import React from 'react';
import { Box, Button, Link, Paper, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useUIStore } from '../../stores/uiStore';
import {
  getAnalytics, pageViewPath, OPEN_COOKIE_SETTINGS_EVENT, type AnalyticsState,
} from '../../lib/analytics';

/** Where the privacy information lives (the app has no privacy page of its own). */
const PRIVACY_URL = 'https://emotres.com';

/** Live analytics state (re-read on every consent change and when the UI language changes). */
export function useAnalyticsState(): AnalyticsState {
  const a = getAnalytics();
  const { i18n } = useTranslation('common');
  const [, bump] = React.useReducer((n: number) => n + 1, 0);
  React.useEffect(() => a.subscribe(bump), [a]);
  // the locale is part of the block rules (zh-CN never loads Google)
  void i18n.language;
  return a.state();
}

/**
 * Mounted once, outside every gate (the landing needs it too).  Starts analytics after
 * the app has mounted, sends sanitised page views on tab changes, and shows the small
 * consent bar when the visitor has not chosen yet.  Renders nothing at all where
 * analytics is blocked (China, Do Not Track, Global Privacy Control).
 */
const AnalyticsConsent: React.FC = () => {
  const { t } = useTranslation('common');
  const a = getAnalytics();
  const state = useAnalyticsState();
  const [reopened, setReopened] = React.useState(false);
  const activeTab = useUIStore((s) => s.activeTab);

  React.useEffect(() => { a.start(); }, [a]);
  React.useEffect(() => {
    a.pageView(pageViewPath(window.location.pathname, activeTab));
  }, [a, activeTab]);
  React.useEffect(() => {
    const open = () => setReopened(true);
    window.addEventListener(OPEN_COOKIE_SETTINGS_EVENT, open);
    return () => window.removeEventListener(OPEN_COOKIE_SETTINGS_EVENT, open);
  }, []);

  if (state === 'blocked' || (state !== 'unknown' && !reopened)) return null;

  const choose = (accept: boolean) => {
    if (accept) a.accept(); else a.decline();
    setReopened(false);
  };

  return (
    <Paper role="region" aria-label={t('cookies.title')} elevation={6}
      sx={{
        position: 'fixed', bottom: 12, left: 12, right: { xs: 12, sm: 'auto' }, zIndex: 1400,
        maxWidth: 560, px: 1.5, py: 1, display: 'flex', alignItems: 'center',
        gap: 1.5, flexWrap: 'wrap', border: '1px solid', borderColor: 'divider',
      }}>
      <Typography sx={{ fontSize: '0.8rem', flex: '1 1 260px' }}>
        {t('cookies.text')}{' '}
        <Link href={PRIVACY_URL} target="_blank" rel="noopener noreferrer" underline="always"
          color="inherit">
          {t('cookies.privacy')}
        </Link>
      </Typography>
      <Box sx={{ display: 'flex', gap: 1, ml: 'auto' }}>
        <Button size="small" variant="text" onClick={() => choose(false)}
          sx={{ textTransform: 'none', fontSize: '0.78rem' }}>
          {t('cookies.decline')}
        </Button>
        <Button size="small" variant="contained" onClick={() => choose(true)}
          sx={{ textTransform: 'none', fontSize: '0.78rem' }}>
          {t('cookies.accept')}
        </Button>
      </Box>
    </Paper>
  );
};

/** "Cookie settings" link/menu item target: reopens the bar. Hidden where analytics is blocked. */
export function openCookieSettings() {
  window.dispatchEvent(new Event(OPEN_COOKIE_SETTINGS_EVENT));
}

export default AnalyticsConsent;
