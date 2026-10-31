// Self-hosted auth session (replaces Firebase Auth — project deleted 2026-08-20).
//
// Identity comes from Google Identity Services (the official button → ID token)
// or an email/password account; either way the backend exchanges it for OUR
// 30-day HS256 token (POST /api/auth/google | /api/auth/login), which is what
// every API call carries. Rights (role/admin) always live server-side in
// config/users.json + ADMIN_EMAILS — nothing here is trusted for authorization.
import { acceptancePayload, emptyAcceptance, resolveLegalUrl, type LegalLang } from './legalLogic';

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
  role: string;
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
  if (!r.ok) {
    const e = new ApiErr((j as { detail?: string }).detail || `login failed (${r.status})`);
    e.code = (j as { code?: string }).code;
    e.params = (j as { params?: Record<string, unknown> }).params;
    throw e;
  }
  return j as { token: string; user: SessionUser };
}

/** Email/password → our token. Throws with a human message on failure. */
export function passwordLogin(email: string, password: string) {
  return post('/api/auth/login', { email, password });
}

/** An API error with the stable `code` + `params` the backend adds beside the English detail (api_errors.py), so the dialog can
 *  show it in the interface language. */
export class ApiErr extends Error {
  code?: string;
  params?: Record<string, unknown>;
}

async function postPlain(path: string, body: unknown): Promise<{ message?: string; email?: string }> {
  const r = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new ApiErr((j as { detail?: string }).detail || `request failed (${r.status})`);
    e.code = (j as { code?: string }).code;
    e.params = (j as { params?: Record<string, unknown> }).params;
    throw e;
  }
  return j as { message?: string; email?: string };
}

/** Self-service sign-up. The answer never says whether the address existed.
 *  `returnTo`: the OAuth consent page (`/agent-consent?request=…`) when the
 *  sign-up started inside an AI app's authorization window — the mailed link
 *  then opens that page again and the pending authorization is kept alive. */
export function registerAccount(email: string, password: string, name: string, commPrefs: Partial<Record<string, boolean>> = {},
  lang = 'en', returnTo?: string, next?: string, terms: TermsAcceptance = acceptancePayload(emptyAcceptance(), '')) {
  return postPlain('/api/auth/register', {
    email, password, name, comm_prefs: commPrefs, lang, ...terms, ...(returnTo ? { return_to: returnTo } : {}),
    // a shared link the visitor opened before registering (lib/productionLink.ts): the confirmation page leads on to it
    ...(next ? { next } : {}),
  });
}

/** Mail the confirmation link again (the server limits it to 3 an hour per address and answers the same whatever the address is). */
export function resendVerification(email: string, next?: string) {
  return postPlain('/api/auth/register/resend', { email, ...(next ? { next } : {}) });
}

/** An unconfirmed account fixes a mistyped address; the password it was registered with proves it is theirs. */
export function changeSignupEmail(email: string, password: string, newEmail: string, lang = 'en') {
  return postPlain('/api/auth/register/change-email', { email, password, new_email: newEmail, lang });
}

/** Consume the mailed confirmation link.  `continuation`: the link's
 *  single-use `continue` secret (a sign-up started in an AI app's
 *  authorization window).  The server derives where to go back from it and
 *  answers `return_to` only when it is valid for the confirmed address. */
export function verifyEmail(token: string, continuation?: string) {
  return postPlain('/api/auth/verify', { token, ...(continuation ? { continuation } : {}) }) as
    Promise<{ message?: string; email?: string; authorization_pending?: boolean; return_to?: string }>;
}

/** The `continue` secret of a mailed confirmation link, if any. */
export function pendingContinuation(): string | undefined {
  try {
    return new URLSearchParams(window.location.search).get('continue') ?? undefined;
  } catch { return undefined; }
}

/** `?signup=1` / `?signin=1` in the address bar (links the MCP discovery
 *  tools hand out): open the dialog in that mode. */
export function requestedAuthMode(): 'register' | 'signin' | null {
  try {
    const q = new URLSearchParams(window.location.search);
    if (q.get('signup') === '1') return 'register';
    if (q.get('signin') === '1') return 'signin';
  } catch { /* no window */ }
  return null;
}

/** The acceptance part of a sign-up request (legalLogic.acceptancePayload): `{accept_terms, terms_version, country, business_name, capacity, accept_label}`. */
export type TermsAcceptance = ReturnType<typeof acceptancePayload>;

/** Public legal / source links shown under the sign-up form.  They default to the in-app pages (/terms, /privacy and the agreement,
 *  with the /zh-cn/ prefix in Chinese); VITE_TERMS_URL / VITE_PRIVACY_URL still override at build time. */
export const termsUrl = (lang: LegalLang = 'en') => resolveLegalUrl(import.meta.env.VITE_TERMS_URL, 'terms', lang);
export const privacyUrl = (lang: LegalLang = 'en') => resolveLegalUrl(import.meta.env.VITE_PRIVACY_URL, 'privacy', lang);
export const agreementUrl = (lang: LegalLang = 'en') => resolveLegalUrl(import.meta.env.VITE_AGREEMENT_URL, 'platform-agreement', lang);
export const SOURCE_URL = (import.meta.env.VITE_SOURCE_URL as string | undefined) ?? 'https://github.com/eMotres/Motor_Ai_Simulation';

/** Ask for a reset link (also how a Google-only account gets a password). */
export function requestPasswordReset(email: string) {
  return postPlain('/api/auth/reset/request', { email });
}

/** Set a new password from a reset link; signs out every session. */
export function confirmPasswordReset(token: string, password: string) {
  return postPlain('/api/auth/reset/confirm', { token, password });
}

export const PASSWORD_MIN_LEN = 10;

/** A mailed link in the address bar: `?verify=…` or `?reset=…`. */
export function pendingEmailLink(): { kind: 'verify' | 'reset'; token: string } | null {
  try {
    const q = new URLSearchParams(window.location.search);
    const v = q.get('verify');
    if (v) return { kind: 'verify', token: v };
    const r = q.get('reset');
    if (r) return { kind: 'reset', token: r };
  } catch { /* no window */ }
  return null;
}

/** Remove the link token from the address bar (it must not linger in history). */
export function clearEmailLink(): void {
  try {
    const u = new URL(window.location.href);
    u.searchParams.delete('verify');
    u.searchParams.delete('reset');
    u.searchParams.delete('continue');
    window.history.replaceState(null, '', u.pathname + u.search + u.hash);
  } catch { /* ignore */ }
}

/** GIS ID token (1-hour life) → our 30-day token. */
/** `commPrefs` = the e-mail opt-in boxes under the button (only the ticked ones); the server applies them on the FIRST
 *  sign-in only and records them with the wording shown in `lang` (comm_prefs.py).  `terms` = the sign-up box: the server needs it
 *  for a brand-new account only (422 `terms_required` otherwise); an existing account signs in without it. */
export function googleExchange(credential: string, commPrefs: Partial<Record<string, boolean>> = {}, lang = 'en',
  terms: TermsAcceptance = acceptancePayload(emptyAcceptance(), '')) {
  return post('/api/auth/google', { credential, comm_prefs: commPrefs, lang, ...terms });
}

/** Best-effort decode of a JWT payload (display-only fields like name/picture). */
export function decodeJwtPayload(token: string): Record<string, unknown> {
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(decodeURIComponent(escape(atob(b64))));
  } catch { return {}; }
}

// ── Google Identity Services script (loaded once, ONLY on a click) ───────────
// accounts.google.com is blocked in mainland China.  Nothing at page load, in a
// render effect or in a dialog's mount may call loadGis(): only a click on a
// "Sign in with Google" button does (LoginDialog, AccountDataDialog), so a
// blocked Google host can never delay the app.  A host that neither answers nor
// refuses is cut off after GIS_TIMEOUT_MS and reported as GisUnreachableError.
type GisId = {
  initialize: (cfg: object) => void;
  renderButton: (el: HTMLElement, cfg: object) => void;
  prompt?: () => void;
  disableAutoSelect: () => void;
};

export const GIS_SRC = 'https://accounts.google.com/gsi/client';
export const GIS_TIMEOUT_MS = 8000;

/** Google's script cannot be reached from this network (blocked, offline or too slow). */
export class GisUnreachableError extends Error {
  constructor(message = 'Google sign-in is not reachable from your network') {
    super(message);
    this.name = 'GisUnreachableError';
  }
}

let gisPromise: Promise<GisId> | null = null;

function currentGis(): GisId | undefined {
  return (window as unknown as { google?: { accounts?: { id?: GisId } } }).google?.accounts?.id;
}

/** True once the script has been loaded in this page (never loads it). */
export function isGisLoaded(): boolean { return !!currentGis(); }

export function loadGis(timeoutMs: number = GIS_TIMEOUT_MS): Promise<GisId> {
  if (!GOOGLE_CLIENT_ID) return Promise.reject(new Error('VITE_GOOGLE_CLIENT_ID is not set'));
  if (gisPromise) return gisPromise;
  const p = new Promise<GisId>((resolve, reject) => {
    const have = currentGis();
    if (have) { resolve(have); return; }
    const s = document.createElement('script');
    let done = false;
    const fail = (e: Error) => {
      if (done) return;
      done = true;
      window.clearTimeout(timer);
      s.remove();
      gisPromise = null;            // a later click may try again
      reject(e);
    };
    const timer = window.setTimeout(() => fail(new GisUnreachableError()), timeoutMs);
    s.src = GIS_SRC;
    s.async = true;
    s.onload = () => {
      const id = currentGis();
      if (!id) { fail(new GisUnreachableError()); return; }
      done = true;
      window.clearTimeout(timer);
      resolve(id);
    };
    s.onerror = () => fail(new GisUnreachableError());
    document.head.appendChild(s);
  });
  gisPromise = p;
  return p;
}

// ── my data: export + account deletion (GDPR; routes/account_data.py) ───────
function authHeaders(json = false): Record<string, string> {
  const h: Record<string, string> = {};
  const token = getStoredToken();
  if (token) h.Authorization = `Bearer ${token}`;
  if (json) h['Content-Type'] = 'application/json';
  return h;
}

export interface DeletionState { pending: { requested_at: number; due_at: number } | null; grace_days: number }

export async function getDeletionState(): Promise<DeletionState> {
  const r = await fetch(`${API}/api/account/deletion`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`deletion status HTTP ${r.status}`);
  return r.json() as Promise<DeletionState>;
}

/** Schedule my account for deletion; needs the password or a fresh Google credential. */
export async function requestAccountDeletion(proof: { password?: string; google_credential?: string }): Promise<DeletionState> {
  const r = await fetch(`${API}/api/account/deletion`, {
    method: 'POST', headers: authHeaders(true), body: JSON.stringify(proof),
  });
  const j = await r.json().catch(() => ({})) as { detail?: string } & DeletionState;
  if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
  return j;
}

export async function cancelAccountDeletion(): Promise<void> {
  const r = await fetch(`${API}/api/account/deletion`, { method: 'DELETE', headers: authHeaders() });
  if (!r.ok) throw new Error(`cancel HTTP ${r.status}`);
}

/** A signed, single-use, 15-minute download link for my data (ZIP). */
export async function requestDataExport(): Promise<{ url: string; expires_at: number }> {
  const r = await fetch(`${API}/api/account/export`, { method: 'POST', headers: authHeaders() });
  if (!r.ok) throw new Error(`export HTTP ${r.status}`);
  const j = await r.json() as { url: string; expires_at: number };
  return { url: `${API}${j.url}`, expires_at: j.expires_at };
}
