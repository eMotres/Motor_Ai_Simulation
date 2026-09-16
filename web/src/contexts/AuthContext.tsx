// Auth context — self-hosted sessions (Google Identity Services or
// email/password → our HS256 token, lib/localAuth.ts). Firebase is gone.
// Roles are ALWAYS resolved server-side via /api/me; nothing client-side is
// trusted for authorization.
import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import Snackbar from '@mui/material/Snackbar';
import Alert from '@mui/material/Alert';
import { installFetchAuth, setTokenGetter } from '../lib/apiAuth';
import {
  clearSession, getStoredToken, getStoredUser, loadGis, storeSession,
  setSessionRole, serverLogout, updateToken, GOOGLE_CLIENT_ID, type SessionUser,
} from '../lib/localAuth';
import LoginDialog from '../components/auth/LoginDialog';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

// Install the fetch interceptor at MODULE LOAD, not in an effect: React runs
// child effects BEFORE the provider's, so a panel's first fetch fired from its
// own mount effect would go out WITHOUT the Bearer header and 401 on gated
// endpoints (seen live: the field view hit "your_tier: anon" while signed in
// as admin).  The token getter reads localStorage synchronously — no state to
// wait for.
installFetchAuth();
setTokenGetter(async () => getStoredToken());

/** Minimal signed-in user shape (Firebase's User replaced). uid === email. */
export interface AuthUser {
  uid: string;
  email: string;
  displayName: string;
  photoURL?: string;
}

export interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  /** Auth system availability — always true now (self-hosted). */
  enabled: boolean;
  /** Plan tier resolved from the backend (anon/free/pro/team/admin). */
  tier: string;
  /** True when the signed-in account is an admin (or local dev). Gates the admin UI. */
  isAdmin: boolean;
  /** True when the backend enforces auth (production). When false, role restrictions are off. */
  enforced: boolean;
  /** Has `/api/me` ANSWERED yet?
   *
   *  `enforced` starts false, so before the first answer every consumer reads
   *  "this backend does not enforce auth" — which for ~100 ms makes an
   *  anonymous visitor to a CLOSED server look signed in.  That window is what
   *  fired the geometry and schema probes at a door that 401s them, red in the
   *  console of the very first page a visitor sees (live, 2026-09-16).  Wait on
   *  this before acting on `enforced`; a restored session (`user`) needs no
   *  wait, so nothing about a signed-in boot changes. */
  resolved: boolean;
  /** Opens the sign-in dialog (Google button + email/password). */
  signIn: () => Promise<void>;
  logout: () => Promise<void>;
  /** Our backend token (null if signed out). */
  getToken: () => Promise<string | null>;
}

const AuthCtx = createContext<AuthState>({
  user: null, loading: false, enabled: true, tier: 'anon', isAdmin: false, enforced: false,
  resolved: false,
  signIn: async () => {}, logout: async () => {}, getToken: async () => null,
});

export const useAuth = () => useContext(AuthCtx);

function toAuthUser(u: SessionUser | null): AuthUser | null {
  if (!u || !u.email) return null;
  return { uid: u.email, email: u.email, displayName: u.name || u.email, photoURL: u.picture };
}

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<AuthUser | null>(() => toAuthUser(getStoredUser()));
  // The session restores synchronously from localStorage — nothing to wait for.
  const loading = false;
  const [tier, setTier] = useState<string>('anon');
  const [isAdmin, setIsAdmin] = useState<boolean>(false);
  const [enforced, setEnforced] = useState<boolean>(false);
  // Flipped by the first /api/me answer we actually APPLY — never by the
  // store-busy or provisional paths, both of which come back in a moment.
  const [resolved, setResolved] = useState<boolean>(false);
  const [loginOpen, setLoginOpen] = useState<boolean>(false);
  // One short amber line when the backend could not READ its own auth store —
  // the session is kept and retried, so the user needs to know only that the
  // pause is ours and temporary.
  const [storeBusy, setStoreBusy] = useState(false);

  // Ask the backend who we are (the fetch interceptor attaches the token).
  // Also our expiry check: a token the backend SAW and refused (expired / user
  // disabled / secret rotated) signs the session out — see the flags below.
  const loadRoleRef = React.useRef<(() => Promise<void>) | null>(null);
  const loadRole = useCallback(async () => {
    try {
      const j = await fetch(`${API}/api/me`).then((r) => r.json());
      // The backend could not READ users.json / .sessions.json / .auth_secret
      // (a Windows file lock during the atomic replace, an antivirus hold).
      // It never verified our token, so it cannot have rejected it: KEEP the
      // session and come back in 5 s.  Treating this as an expiry is how a
      // valid session could be wiped by a 40 ms file lock.
      if (j.authError === 'store_unavailable') {
        setStoreBusy(true);
        setTimeout(() => { void loadRoleRef.current?.(); }, 5000);
        return;
      }
      setStoreBusy(false);
      // A PROVISIONAL answer — anonymous, but our token was never presented
      // (the mount-time race described below) — must not be applied: doing so
      // set tier=anon / enforced=true for the ~1.5 s until the retry, the
      // App's tab guard saw fullUI=false in that window and threw the user
      // off Simulation / Sweep / Geometry onto Compare on EVERY full reload
      // (user 2026-09-13: "почему вкладка отлипает?").  Keep the last known
      // role and let the retry below settle it.
      const _stored0 = getStoredToken();
      const _provisional = _stored0 && !j.email && j.tokenRejected !== true
        && j.tokenPresented !== true;
      if (_provisional) {
        setTimeout(() => { void loadRoleRef.current?.(); }, 1500);
        return;
      }
      setTier(j.tier ?? 'anon'); setIsAdmin(Boolean(j.isAdmin)); setEnforced(Boolean(j.enforced));
      setResolved(true);
      setSessionRole({ isAdmin: Boolean(j.isAdmin), enforced: Boolean(j.enforced) });
      // Sliding renewal: inside the last 7 days the backend hands back a fresh
      // 30-day token for the SAME session. Swap it in silently.
      if (typeof j.renewedToken === 'string' && j.renewedToken) updateToken(j.renewedToken);
      // Drop the stored session ONLY when the server says it SAW our token and
      // refused it (expired / revoked / account gone).  An answer that is
      // merely anonymous means the request went out without the header — a
      // client-side race, not an expiry — and wiping the session there is what
      // made the login "keep expiring" (2026-08-21).  Older backends send
      // neither flag: fall back to the old test so nothing regresses.
      const stored = getStoredToken();
      const rejected = j.tokenRejected === true
        || (j.tokenRejected === undefined && j.tokenPresented === undefined && stored && !j.email);
      if (stored && rejected) { clearSession(String(j.authError ?? 'rejected'), j); setUser(null); }
      else if (stored && !j.email) {
        // eslint-disable-next-line no-console
        console.warn('[auth] /api/me answered anonymous but our token was not '
          + 'presented — keeping the session; retrying role resolution.');
        setTimeout(() => { void loadRoleRef.current?.(); }, 1500);
      }
    } catch { setTier('anon'); setIsAdmin(false); setEnforced(false); setResolved(true); }
  }, []);
  loadRoleRef.current = loadRole;

  useEffect(() => {
    void loadRole();
  }, [loadRole]);

  const onSignedIn = useCallback((token: string, u: SessionUser) => {
    storeSession(token, u);
    setUser(toAuthUser(u));
    setLoginOpen(false);
    void loadRole();
  }, [loadRole]);

  const signIn = useCallback(async () => { setLoginOpen(true); }, []);

  const logout = useCallback(async () => {
    // Revoke server-side FIRST, while we still hold the token: a copy of it
    // must stop working the moment the user signs out.
    await serverLogout();
    clearSession('user_signed_out');
    setUser(null);
    if (GOOGLE_CLIENT_ID) {
      // Stop Google from silently re-selecting this account next time.
      try { (await loadGis()).disableAutoSelect(); } catch { /* not loaded — fine */ }
    }
    void loadRole();
  }, [loadRole]);

  const getToken = useCallback(async () => getStoredToken(), []);

  return (
    <AuthCtx.Provider value={{ user, loading, enabled: true, tier, isAdmin, enforced, resolved, signIn, logout, getToken }}>
      {children}
      <LoginDialog open={loginOpen} onClose={() => setLoginOpen(false)} onSignedIn={onSignedIn} />
      <Snackbar open={storeBusy} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}>
        <Alert severity="warning" variant="outlined" sx={{ fontSize: 12, py: 0.25 }}>
          Auth store busy — retrying, your session is kept.
        </Alert>
      </Snackbar>
    </AuthCtx.Provider>
  );
};
