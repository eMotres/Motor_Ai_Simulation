// Global fetch interceptor: attaches the Firebase ID token (as a Bearer header)
// to requests aimed at our backend, when a user is signed in. One install point
// covers every existing and future fetch() call site — no per-call refactor.
//
// Scoped strictly to the backend API base, so Firebase SDK / third-party calls
// are never touched. No-op when signed out (free endpoints stay anonymous).
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

let tokenGetter: (() => Promise<string | null>) | null = null;

/** AuthProvider keeps this pointed at the current user's getIdToken(). */
export function setTokenGetter(fn: (() => Promise<string | null>) | null): void {
  tokenGetter = fn;
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
    try {
      const url =
        typeof input === 'string' ? input
        : input instanceof URL ? input.href
        : input instanceof Request ? input.url
        : String(input);
      if (tokenGetter && url.startsWith(API)) {
        const token = await tokenGetter();
        if (token) {
          const headers = new Headers(
            init?.headers ?? (input instanceof Request ? input.headers : undefined),
          );
          if (!headers.has('Authorization')) {
            headers.set('Authorization', `Bearer ${token}`);
            init = { ...init, headers };
          }
        }
      }
      // P2: attach the active geometry to COMPUTE requests so the backend builds
      // THIS client's design (stateless) instead of the shared global config.
      if (geoGetter && url.startsWith(API) && COMPUTE_RE.test(url) && !/[?&]geo=/.test(url)) {
        const geo = geoGetter();
        if (geo) {
          const newUrl = url + (url.includes('?') ? '&' : '?') + 'geo=' + encodeURIComponent(geo);
          input = input instanceof Request ? new Request(newUrl, input) : newUrl;
        }
      }
    } catch {
      /* never let auth wiring break a request */
    }
    return orig(input as RequestInfo | URL, init);
  };
}
