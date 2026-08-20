// Self-hosted auth session (replaces Firebase Auth — project deleted 2026-08-20).
//
// Identity comes from Google Identity Services (the official button → ID token)
// or an email/password account; either way the backend exchanges it for OUR
// 30-day HS256 token (POST /api/auth/google | /api/auth/login), which is what
// every API call carries. Rights (tier/admin) always live server-side in
// config/users.json + ADMIN_EMAILS — nothing here is trusted for authorization.
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

export const GOOGLE_CLIENT_ID =
  ((import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined) ?? '').trim();

const TOKEN_KEY = 'mas.auth.token';
const USER_KEY = 'mas.auth.user';

export interface SessionUser {
  email: string;
  name: string;
  picture?: string;
  tier: string;
}

export function getStoredToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

// ── Session role (set by AuthContext from /api/me) ──────────────────────────
// Modules outside React (the motor store, duty apply) ask this to decide
// between "edit the shared server config" (owner) and "work on a client-side
// copy" (ordinary user on an enforced backend).
let _role = { isAdmin: false, enforced: false };

export function setSessionRole(role: { isAdmin: boolean; enforced: boolean }): void {
  _role = { ...role };
}

/** True when this session may write the SHARED server config: the backend is
 *  unenforced (local dev) or the account is admin. */
export function canWriteServer(): boolean {
  return !_role.enforced || _role.isAdmin;
}

export function getStoredUser(): SessionUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as SessionUser) : null;
  } catch { return null; }
}

export function storeSession(token: string, user: SessionUser): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch { /* private mode — session just won't survive a reload */ }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch { /* ignore */ }
}

async function post(path: string, body: unknown): Promise<{ token: string; user: SessionUser }> {
  const r = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((j as { detail?: string }).detail || `login failed (${r.status})`);
  return j as { token: string; user: SessionUser };
}

/** Email/password → our token. Throws with a human message on failure. */
export function passwordLogin(email: string, password: string) {
  return post('/api/auth/login', { email, password });
}

/** GIS ID token (1-hour life) → our 30-day token. */
export function googleExchange(credential: string) {
  return post('/api/auth/google', { credential });
}

/** Best-effort decode of a JWT payload (display-only fields like name/picture). */
export function decodeJwtPayload(token: string): Record<string, unknown> {
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(decodeURIComponent(escape(atob(b64))));
  } catch { return {}; }
}

// ── Google Identity Services script (loaded once, on demand) ────────────────
type GisId = {
  initialize: (cfg: object) => void;
  renderButton: (el: HTMLElement, cfg: object) => void;
  disableAutoSelect: () => void;
};

let gisPromise: Promise<GisId> | null = null;

export function loadGis(): Promise<GisId> {
  if (!GOOGLE_CLIENT_ID) return Promise.reject(new Error('VITE_GOOGLE_CLIENT_ID is not set'));
  if (gisPromise) return gisPromise;
  gisPromise = new Promise((resolve, reject) => {
    const w = window as unknown as { google?: { accounts?: { id?: GisId } } };
    if (w.google?.accounts?.id) { resolve(w.google.accounts.id); return; }
    const s = document.createElement('script');
    s.src = 'https://accounts.google.com/gsi/client';
    s.async = true;
    s.onload = () => {
      const id = (window as unknown as { google?: { accounts?: { id?: GisId } } })
        .google?.accounts?.id;
      if (id) resolve(id);
      else reject(new Error('GIS script loaded but google.accounts.id is missing'));
    };
    s.onerror = () => { gisPromise = null; reject(new Error('failed to load Google sign-in script')); };
    document.head.appendChild(s);
  });
  return gisPromise;
}
