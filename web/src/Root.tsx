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
import React, { Suspense, lazy, useEffect } from 'react';
import { useAuth } from './contexts/AuthContext';
import { isPreferencesPath } from './lib/commPrefsLogic';
import LandingShell from './components/landing/LandingShell';
import { showsLandingShell } from './lib/rootGate';
import { watchChunkImport } from './lib/chunkReload';
import { resolveSchemasRoute } from './lib/schemasRoute';
import { parseProductionLink } from './lib/productionLink';

const App = lazy(watchChunkImport(() => import('./App')));
const OAuthConsent = lazy(watchChunkImport(() => import('./components/auth/OAuthConsent')));
const CommPrefsPage = lazy(watchChunkImport(() => import('./components/auth/CommPrefsPage')));
// the door behind a shared production-documents link: asks the server whether the caller is a member before the workspace opens
const ShareLinkEntry = lazy(watchChunkImport(() => import('./modules/production/components/ShareLinkEntry')));

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
  // read ONCE: the Production page rewrites the address bar later, and the tree must not change shape (that would remount App)
  const [entryLink] = React.useState(() => parseProductionLink(window.location.pathname, window.location.search));
  useEffect(() => {
    const route = resolveSchemasRoute(window.location.href);
    if (route.redirected) window.history.replaceState(null, '', route.href);
  }, []);
  // OAuth consent for MCP connectors (docs/MCP_2026-09-28.md) and the communication
  // preferences (mailed link, account menu) are pages of their own.
  if (path === '/agent-consent') return <Suspense fallback={<Loading />}><OAuthConsent /></Suspense>;
  if (isPreferencesPath(path)) return <Suspense fallback={<Loading />}><CommPrefsPage /></Suspense>;
  if (showsLandingShell(!!user, resolved, enforced)) return <LandingShell />;
  // A shared link to a production's documents (owner 2026-10-19): the link grants nothing, the server decides (a stranger gets a
  // one-line "request access" page with no names).  A signed-out visitor got the landing above, with the sign-in dialog open.
  const shareLink = user ? entryLink : null;
  if (shareLink) {
    return (
      <Suspense fallback={<Loading />}>
        <ShareLinkEntry link={shareLink}><Suspense fallback={<Loading />}><App /></Suspense></ShareLinkEntry>
      </Suspense>
    );
  }
  return <Suspense fallback={<Loading />}><App /></Suspense>;
};

export default Root;
