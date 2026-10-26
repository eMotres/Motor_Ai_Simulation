/** Deep links into the app and how they survive sign-in (owner 2026-10-26: a link from an activity e-mail must open THAT page
 *  right after sign-in).
 *
 *  Pure (no React, no window): `node --test` imports this file.  The shapes mirror the server's allow-list
 *  (`return_path.safe_app_path`, tests/test_production_share_link.py): the server re-validates everything it is given; this file
 *  decides what the browser parses from the address bar, hands to the sign-up (`next`) and remembers across a sign-in.
 *
 *    /production/<die>/<config>                         the production
 *    /production/<die>/<config>/<tab>[?item=<id>]       one of its tabs (bom, tooling, assembly, timeline, documents,
 *                                                       discussion, proposals, changes); documents also take since= / ids=
 *    /projects/<id>[/<view>][?die=&config=&item=]       a development project, a view (specification, motors, messages, notes),
 *                                                       a motor step (die + config)
 *
 *  ONLY same-origin relative paths of these shapes are ever accepted or produced - never a URL, never `//host` (no open redirect).
 *  A link GRANTS NOTHING: the app asks the server who the caller is after sign-in. */
import { parseProductionLink, validDay, MAX_LINK_IDS } from './productionLink.ts';

export const PRODUCTION_TABS = ['bom', 'tooling', 'assembly', 'timeline', 'documents', 'discussion', 'proposals', 'changes', 'tests'] as const;
export type ProductionTabId = (typeof PRODUCTION_TABS)[number];
export const PROJECT_VIEWS = ['specification', 'motors', 'messages', 'notes'] as const;
export type ProjectViewId = (typeof PROJECT_VIEWS)[number];

export type AppLink =
  | { kind: 'production'; die: string; config: string; tab?: ProductionTabId; item?: string; since?: string; ids?: string[] }
  | { kind: 'project'; id: string; view?: ProjectViewId; die?: string; config?: string; item?: string };

const SEG = /^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$/;
const PROJECT_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const ITEM = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const MAX_LEN = 280;

const goodName = (s: string) => SEG.test(s) && !s.includes('..');

/** Parse the address bar (`pathname` + `search`); null when it is not an app link.  Unknown query keys (`signin`, `verify`, ...) are ignored. */
export function parseAppLink(pathname: string, search = ''): AppLink | null {
  if (typeof pathname !== 'string' || typeof search !== 'string' || pathname.length + search.length > MAX_LEN) return null;
  if (/[\u0000-\u001f\u007f\\]/.test(pathname + search) || pathname.startsWith('//')) return null;
  const q = new URLSearchParams(search);
  const item = q.get('item');
  if (item !== null && !ITEM.test(item)) return null;

  const prod = /^\/production\/([^/]+)\/([^/]+)(?:\/([a-z]+))?\/?$/.exec(pathname);
  if (prod) {
    const tab = prod[3];
    if (tab !== undefined && !(PRODUCTION_TABS as readonly string[]).includes(tab)) return null;
    if (tab === 'documents') {
      const link = parseProductionLink(pathname, search);
      if (!link) return null;
      return { kind: 'production', die: link.die, config: link.config, tab: 'documents', ...(link.since ? { since: link.since } : {}),
        ...(link.ids ? { ids: link.ids } : {}), ...(item ? { item } : {}) };
    }
    if (q.has('since') || q.has('ids')) return null;                // those belong to the documents tab only
    let die: string; let config: string;
    try { die = decodeURIComponent(prod[1]); config = decodeURIComponent(prod[2]); } catch { return null; }
    if (!goodName(die) || !goodName(config)) return null;
    return { kind: 'production', die, config, ...(tab ? { tab: tab as ProductionTabId } : {}), ...(item ? { item } : {}) };
  }

  const proj = /^\/projects\/([^/]+)(?:\/([a-z]+))?\/?$/.exec(pathname);
  if (proj) {
    const id = proj[1];
    if (id.length > 64 || !PROJECT_ID.test(id)) return null;
    const view = proj[2];
    if (view !== undefined && !(PROJECT_VIEWS as readonly string[]).includes(view)) return null;
    const die = q.get('die'); const config = q.get('config');
    if ((die === null) !== (config === null)) return null;         // a motor step needs both
    if (die !== null && config !== null && (!goodName(die) || !goodName(config))) return null;
    return { kind: 'project', id, ...(view ? { view: view as ProjectViewId } : {}),
      ...(die !== null && config !== null ? { die, config } : {}), ...(item ? { item } : {}) };
  }
  return null;
}

/** The canonical path (+ query) of a link; null when a part is not valid. */
export function appLinkPath(link: AppLink): string | null {
  if (link.kind === 'project') {
    if (!PROJECT_ID.test(link.id) || link.id.length > 64) return null;
    if (link.view && !(PROJECT_VIEWS as readonly string[]).includes(link.view)) return null;
    const q: string[] = [];
    if (link.die || link.config) {
      if (!link.die || !link.config || !goodName(link.die) || !goodName(link.config)) return null;
      q.push(`die=${encodeURIComponent(link.die)}`, `config=${encodeURIComponent(link.config)}`);
    }
    if (link.item) { if (!ITEM.test(link.item)) return null; q.push(`item=${link.item}`); }
    return `/projects/${link.id}${link.view ? `/${link.view}` : ''}${q.length ? `?${q.join('&')}` : ''}`;
  }
  if (!goodName(link.die) || !goodName(link.config)) return null;
  if (link.tab && !(PRODUCTION_TABS as readonly string[]).includes(link.tab)) return null;
  if ((link.since || link.ids?.length) && link.tab !== 'documents') return null;
  const q: string[] = [];
  if (link.since) { if (!validDay(link.since)) return null; q.push(`since=${link.since}`); }
  if (link.ids?.length) {
    if (link.ids.length > MAX_LINK_IDS || !link.ids.every((i) => /^[A-Za-z0-9_-]{1,40}$/.test(i))) return null;
    q.push(`ids=${link.ids.join(',')}`);
  }
  if (link.item) { if (!ITEM.test(link.item)) return null; q.push(`item=${link.item}`); }
  const base = `/production/${encodeURIComponent(link.die)}/${encodeURIComponent(link.config)}${link.tab ? `/${link.tab}` : ''}`;
  return q.length ? `${base}?${q.join('&')}` : base;
}

/** Is this address-bar path an app link at all (cheap test for the sign-in dialog and the router)? */
export function isAppLinkPath(pathname: string): boolean {
  return /^\/(production\/[^/]+\/[^/]+(\/[a-z]+)?|projects\/[^/]+(\/[a-z]+)?)\/?$/.test(pathname);
}

/** The path to hand to the sign-up (`next`) when the visitor is on a valid link right now; undefined otherwise. */
export function appLinkReturnPath(pathname: string, search = ''): string | undefined {
  const link = parseAppLink(pathname, search);
  return link ? appLinkPath(link) ?? undefined : undefined;
}

/** Same-origin relative path -> canonical link path, anything else (a URL, `//host`, `javascript:`, an unknown route) -> null. */
export function safeReturnTarget(raw: unknown): string | null {
  if (typeof raw !== 'string' || !raw.startsWith('/') || raw.startsWith('//')) return null;
  let u: URL;
  try { u = new URL(raw, 'http://app.invalid'); } catch { return null; }
  if (u.origin !== 'http://app.invalid') return null;
  const link = parseAppLink(u.pathname, u.search);
  return link ? appLinkPath(link) : null;
}

// -- the target that must survive a sign-in -------------------------------------------------------------------------------------------

export const RETURN_KEY = 'app.returnTo.v1';
export interface TargetStorage { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }

/** A signed-out visitor landed on an app link: remember the canonical target for after the sign-in (tab-local storage; every
 *  access guarded - a blocked storage just means the address bar has to carry the link, which it does). */
export function rememberReturnTo(storage: TargetStorage | null | undefined, pathname: string, search = ''): string | null {
  const target = appLinkReturnPath(pathname, search) ?? null;
  if (!target || !storage) return target;
  try { storage.setItem(RETURN_KEY, target); } catch { /* storage blocked */ }
  return target;
}

/** The remembered target (re-validated: storage is data, not trust), consumed. */
export function takeReturnTo(storage: TargetStorage | null | undefined): string | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(RETURN_KEY);
    storage.removeItem(RETURN_KEY);
    return safeReturnTarget(raw);
  } catch { return null; }
}

/** What to do right after a sign-in: where must the address bar point so the app opens the remembered page?
 *  null = leave it (it already is an app link, or nothing was remembered). */
export function addressAfterSignIn(currentPathname: string, currentSearch: string, remembered: string | null): string | null {
  if (parseAppLink(currentPathname, currentSearch)) return null;     // the link is still in the bar: nothing to restore
  return safeReturnTarget(remembered);
}
