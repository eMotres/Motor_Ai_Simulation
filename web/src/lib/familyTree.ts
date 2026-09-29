/**
 * ONE fetch of `/api/family/tree` per render, however many components ask.
 *
 * The Motors tab is `MotorsCatalog` plus one embedded `FamilyCatalog` per Ø
 * section, and every one of them fetched the tree on mount and again on every
 * `family-changed` event — eight identical 900 KB requests fired in the same
 * tick, each a full YAML parse of every die on the server, queued behind one
 * another (measured 2026-09-13: ~1 s each, the tab "loading" for the sum of
 * them; user: "почему каждый раз так долго загружается меню motors?").
 *
 * Rules:
 *  - concurrent callers share the in-flight request;
 *  - a copy younger than `MEMO_MS` is served without a request — the mount
 *    burst of N sections is one fetch;
 *  - `fresh: true` (a mutation, a `family-changed` event, a failed-load retry)
 *    discards the memo — but still joins a request that began AFTER the
 *    discard, so N listeners of one event make one fresh request, not N;
 *  - the tree stays per-account and `cache: 'no-store'` at the HTTP layer,
 *    exactly as before: nothing here outlives the page or crosses accounts.
 */
const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';
const MEMO_MS = 1500;

/** What an anonymous visitor is told on a server that publishes nothing to the
 *  public (backend `PUBLIC_EXHIBIT=0` → 401 here).  Lives beside the fetch, not
 *  in one of the two catalog components, so they cannot import each other. */
export const SIGN_IN_NOTE =
  'Sign in to see the motor catalog — use the Sign in button above.';

export interface FamilyTree {
  dies: unknown[];
  can_write?: boolean;
  note?: string;
  [k: string]: unknown;
}

let generation = 0;
let memo: { t: number; gen: number; value: FamilyTree } | null = null;
let inflight: { gen: number; promise: Promise<FamilyTree> } | null = null;

/** Forget the memoised tree — the next fetch goes to the server. */
export function invalidateFamilyTree(): void {
  generation += 1;
  memo = null;
}

export function fetchFamilyTree(opts: { fresh?: boolean } = {}): Promise<FamilyTree> {
  if (opts.fresh) invalidateFamilyTree();
  if (memo && memo.gen === generation && Date.now() - memo.t < MEMO_MS) {
    return Promise.resolve(memo.value);
  }
  if (inflight && inflight.gen === generation) return inflight.promise;
  const gen = generation;
  const promise = fetch(`${API}/api/family/tree`, { cache: 'no-store' })
    .then(r => {
      if (!r.ok) {
        // The STATUS travels with the error: a server with PUBLIC_EXHIBIT=0
        // answers 401 to an anonymous visitor, and "sign in" is not the same
        // situation as "the backend is restarting" — one asks the user for
        // something, the other must retry by itself (callers below).
        const err = new Error(`HTTP ${r.status}`) as Error & { status?: number };
        err.status = r.status;
        throw err;
      }
      return r.json() as Promise<FamilyTree>;
    })
    .then(v => {
      if (gen === generation) memo = { t: Date.now(), gen, value: v };
      return v;
    })
    .finally(() => {
      if (inflight && inflight.gen === gen) inflight = null;
    });
  inflight = { gen, promise };
  return promise;
}
