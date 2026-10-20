// Global fetch interceptor: attaches the Firebase ID token (as a Bearer header)
// to requests aimed at our backend, when a user is signed in. One install point
// covers every existing and future fetch() call site — no per-call refactor.
//
// Scoped strictly to the backend API base, so Firebase SDK / third-party calls
// are never touched. No-op when signed out (free endpoints stay anonymous).
import { refusalReasonKey } from './accessUi';
import { sharedGet } from './sharedGet';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');
const DOCUMENT_URL = new URL(window.location.href);
const API_URL = new URL(API || '/', DOCUMENT_URL);

function isApiUrl(raw: string): boolean {
  try {
    // A relative fetch is relative to the page, even when the API is hosted on
    // another origin. Resolving it against API_URL would leak that API bearer.
    const candidate = new URL(raw, DOCUMENT_URL);
    const basePath = API_URL.pathname.replace(/\/$/, '');
    return candidate.origin === API_URL.origin
      && (!basePath || candidate.pathname === basePath || candidate.pathname.startsWith(`${basePath}/`));
  } catch {
    return false;
  }
}

let tokenGetter: (() => Promise<string | null>) | null = null;
const idleClosedTokens = new Set<string>();

/** Fired on `window` when the server answers that the CURRENT session was closed for idling. */
export const AUTH_IDLE_EVENT = 'auth-idle-timeout';

/** AuthProvider keeps this pointed at the current user's getIdToken(). */
export function setTokenGetter(fn: (() => Promise<string | null>) | null): void {
  tokenGetter = fn;
}

/** True when this exact token has already received the server's idle close. */
export function isIdleClosedToken(token: string | null | undefined): boolean {
  return !!token && idleClosedTokens.has(token);
}

// ── Per-request active geometry (multi-user isolation, P2) ──────────────────
// The store points this at its live geometry (JSON). The interceptor appends it
// as `?geo=` to geometry/simulation COMPUTE requests, so the backend computes
// the signed-in user's OWN design (stateless) instead of a shared global config.
// docs/MULTI_USER_PLAN.md. No-op if unset/null (backend falls back to config).
let geoGetter: (() => string | null) | null = null;

/** The store keeps this pointed at JSON.stringify(active numeric geometry). */
export function setGeoGetter(fn: (() => string | null) | null): void {
  geoGetter = fn;
}

// ── Per-request material override (multi-user, Stage 2b) ────────────────────
// Sends the signed-in user's assignment + the props of any non-built-in material
// in use, as `mat=` on simulation PHYSICS requests, so the FEM solve uses the
// user's own materials. No-op when only built-ins are assigned (getter → null).
let matGetter: (() => string | null) | null = null;

/** A component keeps this pointed at the resolved material-override JSON (or null). */
export function setMatGetter(fn: (() => string | null) | null): void {
  matGetter = fn;
}

/** True once the override is RESOLVABLE — assignments and library loaded, so
 *  a physics request made now carries the same `mat=` a request made later
 *  would.  A request fired before this point goes out WITHOUT the override and
 *  lands on a different backend cache key than the solve it is looking for
 *  (measured 2026-09-04: the field view's after-reload probe missed the
 *  picture the same page had solved nine minutes earlier).  MaterialOverrideSync
 *  fires `mat-override-ready` on the window when this flips true. */
export function matOverrideReady(): boolean {
  try { return !!matGetter && matGetter() !== null; } catch { return false; }
}

// POST /api/kernel/run is NOT matched by the interceptor's COMPUTE_RE (it
// rewrites URLs, not JSON bodies), so callers that go through the kernel must
// put the SAME overrides into the payload themselves — otherwise the run
// solves the shared config while the field views beside it show the user's
// copy (two different machines on one screen).
export function currentGeoJson(): string | null {
  try { return geoGetter ? geoGetter() : null; } catch { return null; }
}
export function currentMatJson(): string | null {
  try { return matGetter ? matGetter() : null; } catch { return null; }
}

// Endpoints whose result depends on the cross-section geometry. Covers the
// geometry meshes, ALL analytical + FEM physics endpoints (physics, physics/
// field2d|torque_sweep|sweep|fem_*|thermal_field2d — each accepts ?geo=), and
// the FEM mesh build. Endpoints that don't declare `geo` simply ignore it.
const COMPUTE_RE =
  /\/api\/(geometry\/mesh(2d|_extruded)?|simulation\/(physics(\/[a-z0-9_]+)?|mesh\/build2d(_sliding_band)?))(\?|$)/;

export function installFetchAuth(): void {
  const w = window as unknown as { __fetchAuthInstalled?: boolean };
  if (w.__fetchAuthInstalled) return;
  w.__fetchAuthInstalled = true;

  const orig = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    let url = '';
    try {
      url =
        typeof input === 'string' ? input
        : input instanceof URL ? input.href
        : input instanceof Request ? input.url
        : String(input);
      if (tokenGetter && isApiUrl(url)) {
        const token = await tokenGetter();
        const headers = new Headers(
          init?.headers ?? (input instanceof Request ? input.headers : undefined),
        );
        const existing = headers.get('Authorization');
        if (existing && /^Bearer\s+/i.test(existing) && idleClosedTokens.has(existing.replace(/^Bearer\s+/i, '').trim())) {
          headers.delete('Authorization');
          init = { ...init, headers };
        } else if (token && !idleClosedTokens.has(token) && !existing) {
            headers.set('Authorization', `Bearer ${token}`);
            init = { ...init, headers };
        }
      }
      // Also strip a stale explicit Request/header after sign-out, when there is
      // no token getter result to enter the branch above.
      if (isApiUrl(url)) {
        const currentHeaders = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
        const currentAuth = currentHeaders.get('Authorization');
        if (currentAuth && /^Bearer\s+/i.test(currentAuth) && idleClosedTokens.has(currentAuth.replace(/^Bearer\s+/i, '').trim())) {
          currentHeaders.delete('Authorization');
          init = { ...init, headers: currentHeaders };
        }
      }
      // P2: attach the active geometry to COMPUTE requests so the backend builds
      // THIS client's design (stateless) instead of the shared global config.
      if (geoGetter && isApiUrl(url) && COMPUTE_RE.test(url) && !/[?&]geo=/.test(url)) {
        const geo = geoGetter();
        if (geo) {
          const newUrl = url + (url.includes('?') ? '&' : '?') + 'geo=' + encodeURIComponent(geo);
          input = input instanceof Request ? new Request(newUrl, input) : newUrl;
        }
      }
      // Stage 2b: material override on simulation physics requests. Recompute the
      // URL from the current input (it may have been rewritten by the geo block).
      if (matGetter) {
        const cur =
          typeof input === 'string' ? input
          : input instanceof URL ? input.href
          : input instanceof Request ? input.url
          : String(input);
        if (isApiUrl(cur) && /\/api\/simulation\/physics/.test(cur) && !/[?&]mat=/.test(cur)) {
          const mat = matGetter();
          if (mat) {
            const newUrl = cur + (cur.includes('?') ? '&' : '?') + 'mat=' + encodeURIComponent(mat);
            input = input instanceof Request ? new Request(newUrl, input) : newUrl;
          }
        }
      }
    } catch {
      /* never let auth wiring break a request */
    }
    // The bearer this request actually carries (to tell a late answer to an OLD token from the current one).
    let usedToken: string | null = null;
    try {
      const h = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined)).get('Authorization');
      if (h && /^Bearer /i.test(h)) usedToken = h.slice(7).trim();
    } catch { /* ignore */ }
    // identical catalogue reads in flight share one request (lib/sharedGet)
    const res = await sharedGet(orig, API, input as RequestInfo | URL, init);
    // The server closed this session for idling (30 min without use, sessions.py): it says so on EVERY answer to
    // that token (X-Auth-Reason), so the app learns from any request, not only from a 401.
    try {
      if (!isApiUrl(url)) return res;
      let idleReason = res.headers.get('x-auth-reason') === 'idle_timeout';
      if (!idleReason && res.status === 401) {
        const body = await res.clone().json().catch(() => null);
        idleReason = body?.reason === 'idle_timeout';
      }
      if (usedToken && idleReason && isApiUrl(String(res.url || url))) {
        const cur = tokenGetter ? await tokenGetter() : null;
        // Do not let a late answer for a prior session close a newly signed-in user.
        const firstClose = !idleClosedTokens.has(usedToken);
        idleClosedTokens.add(usedToken);
        if (cur === usedToken && firstClose) {
          window.dispatchEvent(new CustomEvent(AUTH_IDLE_EVENT));
        }
      }
    } catch { /* a notice must never break a request */ }
    // A call the server's role gate REFUSED must never fail silently: say why,
    // in one line (components/common/RefusedNotice).  Only the gate's own body
    // ({detail, required_role, your_role}) counts - any other 401/403 is the
    // caller's to explain.
    try {
      if ((res.status === 401 || res.status === 403) && isApiUrl(String(res.url || url))) {
        void res.clone().json().then((b) => {
          const key = refusalReasonKey(res.status, b);
          if (key) window.dispatchEvent(new CustomEvent('api-refused', { detail: { key } }));
        }).catch(() => { /* not JSON */ });
      }
    } catch { /* a notice must never break a request */ }
    return res;
  };
}
