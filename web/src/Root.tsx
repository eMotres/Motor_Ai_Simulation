/**
 * Chooses what the page boots into.
 *
 *   - a signed-out visitor on a backend that enforces auth  -> `LandingShell`, which is in
 *     the first-load bundle and needs no engineering code;
 *   - anybody signed in, or a backend that does not enforce auth (the local dev server)
 *     -> `App`, the whole workspace, as a LAZY chunk;
 *   - /agent-consent and /preferences -> their own small pages, also lazy.
 *
 * Why: the workspace pulls three.js, recharts, the flow editor, the FEM viewers and the
 * admin tools (~1.2 MB gzip).  A first-time visitor in mainland China on a slow
 * cross-border link used to wait for all of it before the landing could even mount.
 * `App` itself is unchanged and still draws its own landing for the one case it can meet
 * one (a sign-out while it is mounted is handled here: `user` becomes null, `Root` swaps
 * back to the shell).
 */
import React, { Suspense, lazy } from 'react';
import { useAuth } from './contexts/AuthContext';
import { isPreferencesPath } from './lib/commPrefsLogic';
import { matchLegalPage } from './lib/legalLogic';
import LandingShell from './components/landing/LandingShell';
import { showsLandingShell } from './lib/rootGate';

const App = lazy(() => import('./App'));
const OAuthConsent = lazy(() => import('./components/auth/OAuthConsent'));
const CommPrefsPage = lazy(() => import('./components/auth/CommPrefsPage'));
const LegalPage = lazy(() => import('./components/auth/LegalPage'));

const Loading: React.FC = () => (
  <div role="status" aria-live="polite" style={{
    minHeight: '100vh', display: 'grid', placeItems: 'center', fontFamily: 'Inter, system-ui, sans-serif',
    color: '#44443f', background: '#f7f6f3',
  }}>
    <span>AeroStator Core…</span>
  </div>
);

const Root: React.FC = () => {
  const { user, resolved, enforced } = useAuth();
  const path = window.location.pathname;
  const legalKind = matchLegalPage(path);
  // OAuth consent for MCP connectors (docs/MCP_2026-09-28.md), communication
  // preferences (mailed link, account menu) and legal pages are pages of their own.
  if (path === '/agent-consent') return <Suspense fallback={<Loading />}><OAuthConsent /></Suspense>;
  if (isPreferencesPath(path)) return <Suspense fallback={<Loading />}><CommPrefsPage /></Suspense>;
  if (legalKind) return <Suspense fallback={<Loading />}><LegalPage kind={legalKind} /></Suspense>;
  if (showsLandingShell(!!user, resolved, enforced)) return <LandingShell />;
  return <Suspense fallback={<Loading />}><App /></Suspense>;
};

export default Root;
