// Auth context — self-hosted sessions (Google Identity Services or
// email/password → our HS256 token, lib/localAuth.ts). Firebase is gone.
// Roles are ALWAYS resolved server-side via /api/me; nothing client-side is
// trusted for authorization.
import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { installFetchAuth, setTokenGetter } from '../lib/apiAuth';
import {
  clearSession, getStoredToken, getStoredUser, loadGis, storeSession,
  setSessionRole, GOOGLE_CLIENT_ID, type SessionUser,
} from '../lib/localAuth';
import LoginDialog from '../components/auth/LoginDialog';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

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
  /** Opens the sign-in dialog (Google button + email/password). */
  signIn: () => Promise<void>;
  logout: () => Promise<void>;
  /** Our backend token (null if signed out). */
  getToken: () => Promise<string | null>;
}

const AuthCtx = createContext<AuthState>({
  user: null, loading: false, enabled: true, tier: 'anon', isAdmin: false, enforced: false,
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
  const [loginOpen, setLoginOpen] = useState<boolean>(false);

  // Ask the backend who we are (the fetch interceptor attaches the token).
  // Also our expiry check: a stored token the backend no longer accepts
  // (expired / user disabled / secret rotated) comes back email:null → sign out.
  const loadRole = useCallback(async () => {
    try {
      const j = await fetch(`${API}/api/me`).then((r) => r.json());
      setTier(j.tier ?? 'anon'); setIsAdmin(Boolean(j.isAdmin)); setEnforced(Boolean(j.enforced));
      setSessionRole({ isAdmin: Boolean(j.isAdmin), enforced: Boolean(j.enforced) });
      if (getStoredToken() && !j.email) { clearSession(); setUser(null); }
    } catch { setTier('anon'); setIsAdmin(false); setEnforced(false); }
  }, []);

  useEffect(() => {
    installFetchAuth();
    setTokenGetter(async () => getStoredToken());
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
    clearSession();
    setUser(null);
    if (GOOGLE_CLIENT_ID) {
      // Stop Google from silently re-selecting this account next time.
      try { (await loadGis()).disableAutoSelect(); } catch { /* not loaded — fine */ }
    }
    void loadRole();
  }, [loadRole]);

  const getToken = useCallback(async () => getStoredToken(), []);

  return (
    <AuthCtx.Provider value={{ user, loading, enabled: true, tier, isAdmin, enforced, signIn, logout, getToken }}>
      {children}
      <LoginDialog open={loginOpen} onClose={() => setLoginOpen(false)} onSignedIn={onSignedIn} />
    </AuthCtx.Provider>
  );
};
