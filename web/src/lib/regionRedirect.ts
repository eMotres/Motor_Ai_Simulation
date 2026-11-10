// Two regions (docs/regions/DESIGN.md): the answer to the server's 421 `wrong_region`.  A signed-in account reached the
// other region's server; the server names the account's home base URL and the tab moves there, keeping the path.  Pure
// (no imports) so node --test can run it; lib/apiAuth.ts does the navigation, lib/localAuth.ts consumes a hand-off.
//
// Stage 3: the 421 may carry a hand-off ticket (`handoff`, minted by the refusing server once it knew who the caller is)
// - the tab then goes to `<base>/region-handoff#t=<ticket>&next=<path>`, which opens a session there without a second
// sign-in; or `signin: true` (a sign-in / sign-up route refused an address the other server hosts) - the tab goes to the
// same path there with `?signin=1`, which opens the sign-in dialog.

/** Hosts a redirect may go to: aerostator.com and its subdomains, https only.  Anything else is ignored. */
export const regionHostAllowed = (host: string): boolean => {
  const h = String(host || '').toLowerCase();
  return h === 'aerostator.com' || h.endsWith('.aerostator.com');
};

/** The page of the target server that consumes a hand-off ticket. */
export const HANDOFF_PATH = '/region-handoff';

const TICKET_RE = /^v1\.[A-Za-z0-9_-]{16,1800}\.[0-9a-f]{64}$/;

/** A same-origin app path (`/x?y`), never `//host`, a backslash or a scheme; anything else is `/`. */
export const safeNextPath = (raw: unknown): string => {
  const s = typeof raw === 'string' ? raw : '';
  if (!s.startsWith('/') || s.startsWith('//') || s.includes('\\') || /[\u0000-\u001f]/.test(s)) return '/';
  if (s === HANDOFF_PATH || s.startsWith(HANDOFF_PATH + '?') || s.startsWith(HANDOFF_PATH + '#')) return '/';
  return s.length > 2000 ? '/' : s;
};

/** The URL to move the tab to, or null when the answer is not a usable `wrong_region` 421. */
export const wrongRegionTarget = (status: number, body: unknown, currentHref: string): string | null => {
  if (status !== 421 || !body || typeof body !== 'object') return null;
  const d = (body as { detail?: unknown }).detail as
    { code?: unknown; base_url?: unknown; handoff?: unknown; signin?: unknown } | undefined;
  if (!d || d.code !== 'wrong_region' || typeof d.base_url !== 'string') return null;
  let base: URL; let cur: URL;
  try { base = new URL(d.base_url); cur = new URL(currentHref); } catch { return null; }
  if (base.protocol !== 'https:' || !regionHostAllowed(base.hostname)) return null;
  if (base.host === cur.host) return null;                        // never loop onto the server that refused
  const curPath = cur.pathname === HANDOFF_PATH ? '/' : cur.pathname;
  if (typeof d.handoff === 'string' && TICKET_RE.test(d.handoff)) {
    const next = safeNextPath(`${curPath}${cur.search}`);
    return `${base.origin}${HANDOFF_PATH}#t=${d.handoff}&next=${encodeURIComponent(next)}`;
  }
  if (d.signin === true) {
    const q = new URLSearchParams(cur.search);
    q.set('signin', '1');
    return `${base.origin}${curPath}?${q.toString()}`;
  }
  return `${base.origin}${curPath}${cur.search}${cur.hash}`;
};

/** On the hand-off page: the ticket and where to go after, read from the fragment (never sent to a server log). */
export const handoffFromLocation = (pathname: string, hash: string): { ticket: string; next: string } | null => {
  if (pathname !== HANDOFF_PATH) return null;
  const q = new URLSearchParams(String(hash || '').replace(/^#/, ''));
  const ticket = q.get('t') || '';
  if (!TICKET_RE.test(ticket)) return null;
  return { ticket, next: safeNextPath(q.get('next')) };
};
