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
/** Forensic breadcrumb: WHY the last session was cleared, and by what answer.
 *  Written on every clear so the next bug report can say more than "it logged
 *  me out again" (the daily sign-outs of Aug–Sep 2026 had no client evidence
 *  at all). */
const LAST_LOGOUT_KEY = 'auth.lastLogout';

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

/** Swap in a renewed token, keeping the stored user (sliding renewal). */
export function updateToken(token: string): void {
  try { localStorage.setItem(TOKEN_KEY, token); } catch { /* ignore */ }
}

/** Clear the session AND leave a note saying why. `reason` is the server's
 *  authError (expired / revoked / bad_signature / …) or a client-side cause. */
export function clearSession(reason = 'unknown', meResponse?: unknown): void {
  try {
    const note = {
      ts: new Date().toISOString(),
      reason,
      sid: getSessionSid(),
      tokenExp: getTokenExpiry(),
      meResponse: meResponse ?? null,
      userAgent: navigator.userAgent,
    };
    // eslint-disable-next-line no-console
    console.warn('[auth] session cleared —', reason, note);
    localStorage.setItem(LAST_LOGOUT_KEY, JSON.stringify(note));
  } catch { /* ignore */ }
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch { /* ignore */ }
}

/** The note left by the last clearSession, for the bug report. */
export function lastLogout(): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(LAST_LOGOUT_KEY);
    return raw ? (JSON.parse(raw) as Record<string, unknown>) : null;
  } catch { return null; }
}

/** POST /api/auth/logout — revokes the session server-side before we forget
 *  the token. Best effort: a failure must never block signing out locally. */
export async function serverLogout(): Promise<void> {
  const token = getStoredToken();
  if (!token) return;
  try {
    await fetch(`${API}/api/auth/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch { /* offline / backend down — the local clear still happens */ }
}

/** This session's server-side id, read from our own token (display only). */
export function getSessionSid(): string | null {
  const t = getStoredToken();
  if (!t) return null;
  const sid = decodeJwtPayload(t).sid;
  return typeof sid === 'string' && sid ? sid : null;
}

/** `exp` of the stored token in ms, or null. Display + diagnostics only. */
export function getTokenExpiry(): number | null {
  const t = getStoredToken();
  if (!t) return null;
  const exp = decodeJwtPayload(t).exp;
  return typeof exp === 'number' ? exp * 1000 : null;
}

export interface SessionRow {
  sid: string; email: string; created: number; lastSeen: number; expires: number;
  userAgent: string; ip: string; loginMethod: string; revoked: boolean;
}

/** My own sessions (GET /api/auth/sessions). */
export async function listMySessions(): Promise<{ current: string | null; sessions: SessionRow[] }> {
  const token = getStoredToken();
  const r = await fetch(`${API}/api/auth/sessions`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!r.ok) throw new Error(`sessions HTTP ${r.status}`);
  const j = await r.json() as { current: string | null; sessions: SessionRow[] };
  return { current: j.current ?? null, sessions: j.sessions ?? [] };
}

/** Revoke one of my sessions. */
export async function revokeMySession(sid: string): Promise<void> {
  const token = getStoredToken();
  const r = await fetch(`${API}/api/auth/sessions/${encodeURIComponent(sid)}/revoke`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!r.ok) throw new Error(`revoke HTTP ${r.status}`);
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
