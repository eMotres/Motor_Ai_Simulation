import React, { createContext, useContext, useEffect, useState } from 'react';
import { onAuthStateChanged, signInWithPopup, signOut, type User } from 'firebase/auth';
import { auth, googleProvider, firebaseEnabled } from '../lib/firebase';
import { installFetchAuth, setTokenGetter } from '../lib/apiAuth';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export interface AuthState {
  user: User | null;
  loading: boolean;
  enabled: boolean;
  /** Plan tier resolved from the backend (anon/free/pro/team/admin). */
  tier: string;
  /** True when the signed-in account is an admin (or local dev). Gates the admin UI. */
  isAdmin: boolean;
  /** True when the backend enforces auth (production). When false, role restrictions are off. */
  enforced: boolean;
  signIn: () => Promise<void>;
  logout: () => Promise<void>;
  /** Firebase ID token for authenticating backend calls (null if signed out). */
  getToken: () => Promise<string | null>;
}

const AuthCtx = createContext<AuthState>({
  user: null, loading: false, enabled: false, tier: 'anon', isAdmin: false, enforced: false,
  signIn: async () => {}, logout: async () => {}, getToken: async () => null,
});

export const useAuth = () => useContext(AuthCtx);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(firebaseEnabled);
  // Role is resolved server-side (the token + ADMIN_EMAILS), so we ask the
  // backend via /api/me rather than trusting anything client-side.
  const [tier, setTier] = useState<string>('anon');
  const [isAdmin, setIsAdmin] = useState<boolean>(false);
  const [enforced, setEnforced] = useState<boolean>(false);

  // Install the fetch interceptor once, and keep it pointed at the live token.
  useEffect(() => {
    installFetchAuth();
    // Await the initial auth restore before deciding there's no token — otherwise
    // a fetch fired on mount (e.g. the Simulation auto-run after an F5) races
    // ahead of Firebase rehydrating the user and goes out anonymous → 401.
    setTokenGetter(async () => {
      if (!auth) return null;
      try { await auth.authStateReady(); } catch { /* older SDK: fall through */ }
      return auth.currentUser ? auth.currentUser.getIdToken() : null;
    });
    // Ask the backend who we are (the fetch interceptor attaches the token).
    const loadRole = async () => {
      try {
        const j = await fetch(`${API}/api/me`).then((r) => r.json());
        setTier(j.tier ?? 'anon'); setIsAdmin(Boolean(j.isAdmin)); setEnforced(Boolean(j.enforced));
      } catch { setTier('anon'); setIsAdmin(false); setEnforced(false); }
    };
    if (!firebaseEnabled || !auth) { setLoading(false); void loadRole(); return; }
    return onAuthStateChanged(auth, (u) => { setUser(u); setLoading(false); void loadRole(); });
  }, []);

  const signIn = async () => {
    if (firebaseEnabled && auth && googleProvider) await signInWithPopup(auth, googleProvider);
  };
  const logout = async () => {
    if (firebaseEnabled && auth) await signOut(auth);
  };
  const getToken = async () => (user ? user.getIdToken() : null);

  return (
    <AuthCtx.Provider value={{ user, loading, enabled: firebaseEnabled, tier, isAdmin, enforced, signIn, logout, getToken }}>
      {children}
    </AuthCtx.Provider>
  );
};
