/** Shareable link to a production's documents page (owner 2026-10-19):
 *    https://aerostator.com/production/<die>/<config>/documents?since=2026-10-19
 *    https://aerostator.com/production/<die>/<config>/documents?ids=doc_a,doc_b
 *
 *  Pure (no React, no window): the node test imports it.  The rules mirror the server's `return_path.safe_app_path` (the server
 *  re-validates everything it is given - this file only decides what the browser itself builds, parses and offers to the
 *  sign-up as a return path).  A link GRANTS NOTHING: the page behind it asks the server who the caller is. */

const SEG = /^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const ID = /^[A-Za-z0-9_-]{1,40}$/;
export const MAX_LINK_IDS = 50;
const MAX_LEN = 280;

export interface ProductionLink {
  die: string;
  config: string;
  /** keep documents uploaded on/after this day (YYYY-MM-DD) highlighted as new */
  since?: string;
  /** show only these document ids */
  ids?: string[];
}

export function validDay(v: string): boolean {
  if (!DATE.test(v)) return false;
  const d = new Date(`${v}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === v;
}

/** The path (+ query) of a link; null when a part is not valid. */
export function productionLinkPath(link: ProductionLink): string | null {
  if (!SEG.test(link.die) || !SEG.test(link.config) || link.die.includes('..') || link.config.includes('..')) return null;
  const q: string[] = [];
  if (link.since) { if (!validDay(link.since)) return null; q.push(`since=${link.since}`); }
  if (link.ids?.length) {
    if (link.ids.length > MAX_LINK_IDS || !link.ids.every((i) => ID.test(i))) return null;
    q.push(`ids=${link.ids.join(',')}`);
  }
  const path = `/production/${encodeURIComponent(link.die)}/${encodeURIComponent(link.config)}/documents`;
  return q.length ? `${path}?${q.join('&')}` : path;
}

/** The full URL to copy. */
export function productionLinkUrl(origin: string, link: ProductionLink): string | null {
  const p = productionLinkPath(link);
  return p ? `${origin.replace(/\/+$/, '')}${p}` : null;
}

/** Parse the address bar (`pathname` + `search`); null when it is not a valid production documents link. */
export function parseProductionLink(pathname: string, search = ''): ProductionLink | null {
  if (typeof pathname !== 'string' || pathname.length + search.length > MAX_LEN) return null;
  if (/[\u0000-\u001f\u007f\\]/.test(pathname)) return null;
  const m = /^\/production\/([^/]+)\/([^/]+)\/documents\/?$/.exec(pathname);
  if (!m) return null;
  let die: string; let config: string;
  try { die = decodeURIComponent(m[1]); config = decodeURIComponent(m[2]); } catch { return null; }
  if (!SEG.test(die) || !SEG.test(config) || die.includes('..') || config.includes('..')) return null;
  const out: ProductionLink = { die, config };
  const q = new URLSearchParams(search);
  const since = q.get('since');
  if (since) { if (!validDay(since)) return null; out.since = since; }
  const ids = q.get('ids');
  if (ids) {
    const list = ids.split(',').filter(Boolean);
    if (!list.length || list.length > MAX_LINK_IDS || !list.every((i) => ID.test(i))) return null;
    out.ids = list;
  }
  return out;
}

/** The path to hand to the sign-up (`next`) when the visitor is on a valid link right now; undefined otherwise. */
export function shareReturnPath(pathname: string, search = ''): string | undefined {
  const link = parseProductionLink(pathname, search);
  return link ? productionLinkPath(link) ?? undefined : undefined;
}

/** Is this document new for a `since` day?  (uploaded on or after that day; an unreadable date is not new) */
export function isNewDocument(uploadedAt: string | undefined | null, since: string | undefined): boolean {
  if (!since || !uploadedAt) return false;
  const day = String(uploadedAt).slice(0, 10);
  return validDay(day) && day >= since;
}

/** What the documents view lists for a link: `ids` narrows the list (unknown / not visible ids simply do not match), `since`
 *  only marks. */
export function documentsForLink<T extends { id: string; uploaded_at?: string }>(
  docs: T[], link: Pick<ProductionLink, 'since' | 'ids'> | null | undefined,
): { shown: T[]; newIds: Set<string>; narrowed: boolean } {
  const newIds = new Set(docs.filter((d) => isNewDocument(d.uploaded_at, link?.since)).map((d) => d.id));
  const narrowed = !!link?.ids?.length;
  const shown = narrowed ? docs.filter((d) => link!.ids!.includes(d.id)) : docs;
  return { shown, newIds, narrowed };
}

/** The day of the newest upload (YYYY-MM-DD), or '' - the default of the "new since" date picker. */
export function latestUploadDay(docs: Array<{ uploaded_at?: string }>): string {
  let best = '';
  for (const d of docs) {
    const day = String(d.uploaded_at ?? '').slice(0, 10);
    if (validDay(day) && day > best) best = day;
  }
  return best;
}

/** Who may copy the link: admins and the production's owners (owner 2026-10-19). */
export function canShareLink(role: string | null | undefined, isAdmin: boolean): boolean {
  return isAdmin || role === 'owner';
}

/** Is this address-bar path a production link at all (cheap test for the sign-in dialog and the router)? */
export function isProductionLinkPath(pathname: string): boolean {
  return /^\/production\/[^/]+\/[^/]+\/documents\/?$/.test(pathname);
}
