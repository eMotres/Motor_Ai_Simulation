/**
 * The signed-out front door as a bundle of its own.
 *
 * A visitor without a session (the landing's audience, often on a slow cross-border
 * link) needs: the header, the landing, the cookie bar and the sign-in dialog.  It does
 * NOT need the engineering workspace (three.js, recharts, the flow editor, the FEM
 * viewers, the admin tools), which is ~97 % of the JavaScript.  `Root` therefore mounts
 * this shell for an anonymous visitor and loads `App` (a lazy chunk) only when somebody
 * is signed in or the backend does not enforce auth (the local dev server).
 *
 * The header is the same bar `App` draws (brand, version, connection chip, sign-in,
 * theme toggle) minus the workspace-only reset button.  The support assistant is also
 * lazy and starts after the page is idle, so it never competes with the first paint.
 */
import React, { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { AppBar, Box, Chip, CssBaseline, IconButton, ThemeProvider, Toolbar, Tooltip, Typography }
  from '@mui/material';
import CloudOffIcon from '@mui/icons-material/CloudOff';
import CloudSyncIcon from '@mui/icons-material/CloudSync';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import LightModeIcon from '@mui/icons-material/LightMode';
import { useTranslation } from 'react-i18next';
import { buildAppTheme, loadThemeMode, saveThemeMode, type AppMode } from '../../theme';
import { backendReachable } from '../../lib/version';
import BrandLogo from '../common/BrandLogo';
import AuthButton from '../auth/AuthButton';
import AnalyticsConsent from '../common/AnalyticsConsent';
import RefusedNotice from '../common/RefusedNotice';
import { VersionBadge } from '../VersionBadge';
import Landing from './Landing';

const SupportWidget = lazy(() => import('../support/SupportWidget'));

/** Mount `children` after the browser has been idle (or 3 s), never during the first paint. */
const WhenIdle: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const w = window as unknown as {
      requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(() => setReady(true), { timeout: 3000 });
      return () => w.cancelIdleCallback?.(id);
    }
    const id = window.setTimeout(() => setReady(true), 1500);
    return () => window.clearTimeout(id);
  }, []);
  return ready ? <>{children}</> : null;
};

const LandingShell: React.FC = () => {
  const { t: tr } = useTranslation('common');
  const [themeMode, setThemeMode] = useState<AppMode>(() => loadThemeMode());
  useEffect(() => { saveThemeMode(themeMode); }, [themeMode]);
  const theme = useMemo(() => buildAppTheme(themeMode), [themeMode]);

  // The header chip while signed out: /api/version is the one route an anonymous caller
  // may use on every deployment (lib/version.ts).
  const [backendUp, setBackendUp] = useState(false);
  useEffect(() => {
    let alive = true;
    const probe = () => { void backendReachable().then((ok) => { if (alive) setBackendUp(ok); }); };
    probe();
    const id = setInterval(probe, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
        <AppBar position="static" elevation={0}
          sx={{ backgroundColor: 'background.paper', borderBottom: '1px solid',
            borderColor: 'divider', flexShrink: 0 }}>
          <Toolbar variant="dense" sx={{ gap: 1 }}>
            <BrandLogo size={28} />
            <Typography variant="h6" sx={{ mr: 1, letterSpacing: 0.3 }}>
              AeroStator{' '}
              <Box component="span" sx={{ fontWeight: 300, opacity: 0.7 }}>Core</Box>
            </Typography>
            <VersionBadge />
            {backendUp ? (
              <Chip icon={<CloudSyncIcon />} label={tr('status.connected')} color="success" size="small" />
            ) : (
              <Chip icon={<CloudOffIcon />} label={tr('status.localMode')} color="warning" size="small" />
            )}
            <Box sx={{ flexGrow: 1 }} />
            <AuthButton />
            <Tooltip title={themeMode === 'light' ? tr('theme.dark') : tr('theme.light')}>
              <IconButton size="small"
                onClick={() => setThemeMode((m) => (m === 'light' ? 'dark' : 'light'))}>
                {themeMode === 'light'
                  ? <DarkModeIcon fontSize="small" />
                  : <LightModeIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
          </Toolbar>
        </AppBar>
        <Landing />
      </Box>
      <RefusedNotice />
      <WhenIdle>
        <Suspense fallback={null}><SupportWidget /></Suspense>
      </WhenIdle>
      {/* Consent bar + GA4 (lib/analytics.ts): loads only after mount, idle and Accept; never for China */}
      <AnalyticsConsent />
    </ThemeProvider>
  );
};

export default LandingShell;
