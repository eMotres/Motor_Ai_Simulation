// ── One request in flight per catalogue read (owner 2026-10-19: Configure's load fired the same reads 2-4 times) ──
// Several panels read the same catalogue answers on a load - the references, the family tree and context, a machine's
// configure context, the device list.  Identical GETs that are IN FLIGHT at the same time share one request; each
// caller gets its own clone of the answer.  Nothing is cached beyond the flight; a request with an abort signal or a
// body, another account's request (its Authorization) and every other endpoint are never shared.
//
// Pure, no imports: web/src/lib/__tests__/apiAuthShared.test.mjs imports this file; lib/apiAuth.ts installs it.

export const SHARED_GET_RE =
  /\/api\/(family\/(tree|context)|catalog\/references|catalog\/[^/?]+\/configure_context|controller\/devices|propellers)(\?|$)/;
const inflight = new Map<string, Promise<Response>>();

export function sharedGet(orig: typeof fetch, apiBase: string, input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href
    : typeof Request !== 'undefined' && input instanceof Request ? input.url : String(input);
  const isReq = typeof Request !== 'undefined' && input instanceof Request;
  const method = (init?.method ?? (isReq ? (input as Request).method : 'GET')).toUpperCase();
  if (method !== 'GET' || init?.body != null || init?.signal || (isReq && (input as Request).signal?.aborted)
      || !url.startsWith(apiBase) || !SHARED_GET_RE.test(url)) {
    return orig(input, init);
  }
  const auth = new Headers(init?.headers ?? (isReq ? (input as Request).headers : undefined)).get('Authorization') ?? '';
  const key = `${url}|${auth}`;
  let p = inflight.get(key);
  if (!p) {
    const mine = orig(input, init);
    p = mine;
    inflight.set(key, mine);
    const drop = () => { if (inflight.get(key) === mine) inflight.delete(key); };
    mine.then(drop, drop);
  }
  return p.then((r) => r.clone());
}
