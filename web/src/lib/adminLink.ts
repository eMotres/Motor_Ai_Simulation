/** Deep link into an Admin section (owner's daily report, 2026-10-27): `/?tab=admin&section=digests&draft=d-<16 hex>` opens the
 *  Admin tab on that section (and, for Digests, expands that draft).  Pure (no React, no window): `node --test` imports it.
 *  A link GRANTS NOTHING: the Admin tab is still gated by the account's role, and the digest routes by `require_admin`. */
export interface AdminLink { section: string; draft?: string }

const SECTION = /^[A-Za-z0-9]{1,32}$/;
const DRAFT = /^d-[0-9a-f]{16}$/;

/** Parse `window.location.search`; null when it is not an admin link (or any part is malformed). */
export function parseAdminLink(search: string): AdminLink | null {
  if (typeof search !== 'string' || search.length > 200) return null;
  let q: URLSearchParams;
  try { q = new URLSearchParams(search); } catch { return null; }
  if (q.get('tab') !== 'admin') return null;
  const section = q.get('section') ?? '';
  if (!SECTION.test(section)) return null;
  const draft = q.get('draft');
  if (draft !== null && !DRAFT.test(draft)) return null;
  return { section, ...(draft ? { draft } : {}) };
}
