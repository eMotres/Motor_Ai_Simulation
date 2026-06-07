import React, { createContext, useContext, useEffect, useState } from 'react';
import { onAuthStateChanged, signInWithPopup, signOut, type User } from 'firebase/auth';
import { auth, googleProvider, firebaseEnabled } from '../lib/firebase';

export interface AuthState {
  user: User | null;
  loading: boolean;
  enabled: boolean;
  signIn: () => Promise<void>;
  logout: () => Promise<void>;
  /** Firebase ID token for authenticating backend calls (null if signed out). */
  getToken: () => Promise<string | null>;
}

const AuthCtx = createContext<AuthState>({
  user: null, loading: false, enabled: false,
  signIn: async () => {}, logout: async () => {}, getToken: async () => null,
});

export const useAuth = () => useContext(AuthCtx);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(firebaseEnabled);

  useEffect(() => {
    if (!firebaseEnabled || !auth) { setLoading(false); return; }
    return onAuthStateChanged(auth, (u) => { setUser(u); setLoading(false); });
  }, []);

  const signIn = async () => {
    if (firebaseEnabled && auth && googleProvider) await signInWithPopup(auth, googleProvider);
  };
  const logout = async () => {
    if (firebaseEnabled && auth) await signOut(auth);
  };
  const getToken = async () => (user ? user.getIdToken() : null);

  return (
    <AuthCtx.Provider value={{ user, loading, enabled: firebaseEnabled, signIn, logout, getToken }}>
      {children}
    </AuthCtx.Provider>
  );
};
