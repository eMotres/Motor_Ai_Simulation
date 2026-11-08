// Two regions (docs/regions/DESIGN.md): the answer to the server's 421 `wrong_region`.  A signed-in account reached the
// other region's server; the server names the account's home base URL and the tab moves there, keeping the path.  Pure
// (no imports) so node --test can run it; lib/apiAuth.ts does the navigation.

/** Hosts a redirect may go to: aerostator.com and its subdomains, https only.  Anything else is ignored. */
export const regionHostAllowed = (host: string): boolean => {
  const h = String(host || '').toLowerCase();
  return h === 'aerostator.com' || h.endsWith('.aerostator.com');
};

/** The URL to move the tab to, or null when the answer is not a usable `wrong_region` 421. */
export const wrongRegionTarget = (status: number, body: unknown, currentHref: string): string | null => {
  if (status !== 421 || !body || typeof body !== 'object') return null;
  const d = (body as { detail?: unknown }).detail as { code?: unknown; base_url?: unknown } | undefined;
  if (!d || d.code !== 'wrong_region' || typeof d.base_url !== 'string') return null;
  let base: URL; let cur: URL;
  try { base = new URL(d.base_url); cur = new URL(currentHref); } catch { return null; }
  if (base.protocol !== 'https:' || !regionHostAllowed(base.hostname)) return null;
  if (base.host === cur.host) return null;                        // never loop onto the server that refused
  return `${base.origin}${cur.pathname}${cur.search}${cur.hash}`;
};
