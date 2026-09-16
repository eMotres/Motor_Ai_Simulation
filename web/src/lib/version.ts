// App version (stamped from /VERSION at build time by vite.config) + a runtime
// check that compares it to the backend's /api/version, so the UI can warn on
// frontend/backend skew (a real failure mode: a half-deployed release).
declare const __APP_VERSION__: string;
declare const __APP_GIT_SHA__: string;

export const APP_VERSION: string =
  typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.0';
export const APP_GIT_SHA: string =
  typeof __APP_GIT_SHA__ !== 'undefined' ? __APP_GIT_SHA__ : 'dev';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

export interface BackendVersion { version: string; gitSha?: string; builtAt?: string | null; }

export interface VersionCheck {
  frontend: string;
  backend: string | null;
  /** true when frontend & backend differ at MAJOR.MINOR (patch diffs are ignored). */
  skew: boolean;
}

const majorMinor = (v: string) => v.split('.').slice(0, 2).join('.');

/** Is the backend there at all?
 *
 *  `/api/version` is the one engineering-free route that answers an ANONYMOUS
 *  caller on every deployment (auth.anonymous_allowed), which makes it the only
 *  honest liveness probe before sign-in: on a closed server (PUBLIC_EXHIBIT=0)
 *  the geometry/schema fetches that normally set `connectedToApi` all 401, and
 *  the header would call a perfectly healthy backend "Local Mode".
 */
export async function backendReachable(): Promise<boolean> {
  try {
    return (await fetch(`${API}/api/version`, { cache: 'no-store' })).ok;
  } catch { return false; }
}

export async function checkBackendVersion(): Promise<VersionCheck> {
  try {
    const r = await fetch(`${API}/api/version`, { cache: 'no-store' });
    if (!r.ok) return { frontend: APP_VERSION, backend: null, skew: false };
    const b = (await r.json()) as BackendVersion;
    const backend = b?.version ?? null;
    const skew = !!backend && majorMinor(backend) !== majorMinor(APP_VERSION);
    return { frontend: APP_VERSION, backend, skew };
  } catch {
    return { frontend: APP_VERSION, backend: null, skew: false };
  }
}
