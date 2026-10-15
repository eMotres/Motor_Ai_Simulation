/**
 * What to do when `/api/me` cannot be read (network error, timeout, 5xx, an HTML error page).
 *
 * `enforced` starts false until the backend has ANSWERED.  Treating "could not ask" as
 * "answered: not enforced" opened the whole workspace ("Local Mode", ~1 MB gzip) for an
 * anonymous visitor on a flaky link (mainland China) instead of the landing.  So:
 *
 *   - a PRODUCTION build (aerostator.com, enforced auth) never invents an answer: it stays
 *     unresolved, the landing stays on screen, and `/api/me` is retried with backoff;
 *   - a DEV build (`vite`, a local unenforced server) keeps the old behaviour, so the
 *     workspace still opens when the local API is down ("Local Mode");
 *   - the server's own explicit `{enforced:false}` answer is a real answer and is applied
 *     by the caller, not decided here.
 *
 * Pure, so node can test it.
 */

export type MeFailureAction = 'local-mode' | 'retry';

export function meFailureAction(isDevBuild: boolean): MeFailureAction {
  return isDevBuild ? 'local-mode' : 'retry';
}

/** Backoff before retry `attempt` (0-based): 2 s, 4 s, 8 s, 16 s, then every 30 s. */
export function meRetryDelayMs(attempt: number): number {
  const n = Math.max(0, Math.floor(attempt));
  return Math.min(30_000, 2_000 * 2 ** n);
}

/** `/api/me` answers that are not an answer: a gateway error with or without a JSON body. */
export function meResponseUsable(status: number): boolean {
  return status < 500;
}
