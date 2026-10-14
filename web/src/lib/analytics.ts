// Google Analytics 4, the careful way (owner 2026-10-14).
//
// Rules, in order of weight:
//   1. The China-access fix (PR #171) stands: NOTHING in index.html, a pre-rendered
//      page or the CSS names or loads Google.  gtag.js is fetched by this module,
//      from JavaScript, only after the app has mounted and the browser is idle.
//      (web/src/i18n/__tests__/chinaAccess.test.mjs enforces the first half.)
//   2. Never for China: the UI is Chinese (zh-CN locale or the /zh-cn/ path), or the
//      browser time zone is a mainland-China / Macau zone.  Hong Kong is NOT blocked:
//      Google is reachable there.  Also never when the browser sends Do Not Track
//      (navigator.doNotTrack = "1") or Global Privacy Control.
//   3. GDPR / ePrivacy (MOTRES d.o.o., Slovenia): Consent Mode v2 with everything
//      denied by default, and stricter than the mode requires: gtag.js is not even
//      requested until the visitor has pressed "Accept".  Undecided and declined
//      visitors send Google nothing at all (no cookieless pings either).
//   4. Page views carry the path only (origin + pathname, never the query string or
//      hash: ?verify=, ?reset= and ?continue= links hold one-time codes).  The tab
//      name stands in for a route because the app has no router.  No user id, no
//      e-mail, no die / configuration names are ever sent.
//
//   5. Self-hosted Umami (owner 2026-10-14) runs alongside GA4: cookieless, same-origin
//      (/stats/script.js and /stats/api/send on aerostator.com, our own server, no
//      third party), so it is loaded for EVERY visitor, China included, and does not
//      wait for the GA consent choice.  It still respects Do Not Track and Global
//      Privacy Control, loads only after mount + idle, and gets the same path-only page
//      views (auto-track is off; the tracker never sees a query string or hash).
//
// The GA measurement id lives here only; override with VITE_GA_ID at build time
// (empty string = GA off).  The Umami website id likewise: VITE_UMAMI_WEBSITE_ID.  Pure logic with injected dependencies, so
// web/src/lib/__tests__/analytics.test.mjs runs it under node --test.

/** GA4 property "aerostator-core-simulation", web stream "aerostator-web". */
export const DEFAULT_GA_ID = 'G-R2325XYL2S';

/** Self-hosted Umami website (public: the tracker sends it with every hit anyway). */
export const DEFAULT_UMAMI_WEBSITE_ID = '8fe8df44-5870-4f08-bfc4-16a79aa44f17';
/** Same-origin mount of the Umami public endpoints (nginx); the dashboard is not exposed. */
export const UMAMI_HOST = '/stats';

export const CONSENT_STORAGE_KEY = 'analytics.consent';

/** Mainland-China zones (and Macau).  Hong Kong is deliberately absent. */
export const NO_ANALYTICS_TIME_ZONES: readonly string[] = [
  'Asia/Shanghai', 'Asia/Chongqing', 'Asia/Chungking', 'Asia/Harbin',
  'Asia/Urumqi', 'Asia/Kashgar', 'Asia/Macau', 'Asia/Macao', 'PRC',
];

export type ConsentChoice = 'granted' | 'denied';
/** unknown = nothing stored yet (the bar asks); blocked = analytics can never run here. */
export type AnalyticsState = 'unknown' | ConsentChoice | 'blocked';

export type BlockReason =
  | 'no-id' | 'china-locale' | 'china-path' | 'china-time-zone' | 'do-not-track' | 'gpc';

export interface BlockInput {
  id?: string | null;
  locale?: string | null;
  pathname?: string | null;
  timeZone?: string | null;
  doNotTrack?: string | null;
  gpc?: boolean | null;
}

/** Why analytics must not run for this visitor, or null when it may (after consent). */
export function blockReason(i: BlockInput): BlockReason | null {
  if (!i.id) return 'no-id';
  const loc = String(i.locale ?? '').trim().toLowerCase().replace(/_/g, '-');
  if (loc === 'zh' || loc.startsWith('zh-')) return 'china-locale';
  if (/^\/zh-cn(\/|$)/i.test(String(i.pathname ?? ''))) return 'china-path';
  if (typeof i.timeZone === 'string' && NO_ANALYTICS_TIME_ZONES.includes(i.timeZone.trim())) {
    return 'china-time-zone';
  }
  if (i.doNotTrack === '1' || i.doNotTrack === 'yes') return 'do-not-track';
  if (i.gpc === true) return 'gpc';
  return null;
}

/** Origin-relative path without query or hash; the tab (if any) becomes the last segment. */
export function pageViewPath(pathname: string | null | undefined, tab?: string | null): string {
  let p = String(pathname ?? '/').split(/[?#]/)[0] || '/';
  if (!p.startsWith('/')) p = '/' + p;
  const safe = String(tab ?? '').replace(/[^a-zA-Z0-9_-]/g, '');
  if (!safe) return p;
  return p.replace(/\/+$/, '') + '/' + safe;
}

/** Umami is cookieless and ours, so only the visitor's own opt-outs apply. */
export function umamiBlockReason(i: BlockInput & { umamiId?: string | null }):
  'no-id' | 'do-not-track' | 'gpc' | null {
  if (!i.umamiId) return 'no-id';
  if (i.doNotTrack === '1' || i.doNotTrack === 'yes') return 'do-not-track';
  if (i.gpc === true) return 'gpc';
  return null;
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem'> | null | undefined;

export function readConsent(storage: StorageLike): ConsentChoice | null {
  try {
    const v = storage?.getItem(CONSENT_STORAGE_KEY);
    return v === 'granted' || v === 'denied' ? v : null;
  } catch { return null; }
}

function writeConsent(storage: StorageLike, v: ConsentChoice): void {
  try { storage?.setItem(CONSENT_STORAGE_KEY, v); } catch { /* private window / blocked storage */ }
}

export interface AnalyticsDeps {
  id: string | null | undefined;
  /** Umami website id (null/empty = Umami off). */
  umamiId?: string | null;
  /** Receives dataLayer and gtag.  window in the browser. */
  win: Record<string, any>;
  doc: { createElement(tag: string): any; head: { appendChild(el: any): unknown } };
  storage: StorageLike;
  /** Origin that page_location is built on (location.origin). */
  origin: string;
  /** Live values, read at decision time. */
  locale: () => string | null;
  pathname: () => string | null;
  timeZone: () => string | null;
  doNotTrack: () => string | null;
  gpc: () => boolean | null;
  /** Run fn once the page is idle (requestIdleCallback with a timeout fallback). */
  idle: (fn: () => void) => void;
}

export function createAnalytics(d: AnalyticsDeps) {
  let loaded = false;
  let umamiStarted = false;
  let umamiReady = false;
  let lastPath: string | null = null;
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((l) => { try { l(); } catch { /* listener bug */ } });

  const reason = () => blockReason({
    id: d.id, locale: d.locale(), pathname: d.pathname(), timeZone: d.timeZone(),
    doNotTrack: d.doNotTrack(), gpc: d.gpc(),
  });

  function state(): AnalyticsState {
    if (reason()) return 'blocked';
    return readConsent(d.storage) ?? 'unknown';
  }

  function ensureGtag() {
    d.win.dataLayer = d.win.dataLayer || [];
    if (typeof d.win.gtag !== 'function') {
      // Google's snippet pushes the `arguments` object, not an array.
      d.win.gtag = function gtag() { d.win.dataLayer.push(arguments); };
    }
  }

  function send(path: string) {
    d.win.gtag('event', 'page_view', { page_path: path, page_location: d.origin + path });
  }

  /** Consent Mode v2 default -> granted update -> config, then the script element. */
  function load() {
    if (loaded || state() !== 'granted') return;
    loaded = true;
    ensureGtag();
    const gtag = d.win.gtag;
    d.win[`ga-disable-${d.id}`] = false;
    gtag('consent', 'default', {
      ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied',
      analytics_storage: 'denied',
    });
    gtag('consent', 'update', { analytics_storage: 'granted' });
    gtag('js', new Date());
    gtag('config', d.id, {
      anonymize_ip: true,
      send_page_view: false,             // the app sends sanitised page views itself
      allow_google_signals: false,       // no advertising features
      allow_ad_personalization_signals: false,
    });
    if (lastPath) send(lastPath);        // the view that happened while we waited
    const s = d.doc.createElement('script');
    s.async = true;
    s.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(String(d.id))}`;
    d.doc.head.appendChild(s);
  }

  const umamiReason = () => umamiBlockReason({
    umamiId: d.umamiId, doNotTrack: d.doNotTrack(), gpc: d.gpc(),
  });

  function umamiSend(path: string) {
    try {
      // keep Umami's own fields, replace the URL with the path (no query, no hash)
      d.win.umami?.track((props: Record<string, unknown>) => ({ ...props, url: path }));
    } catch { /* a broken tracker must never break the app */ }
  }

  function loadUmami() {
    if (umamiStarted || umamiReason()) return;
    umamiStarted = true;
    const s = d.doc.createElement('script');
    s.defer = true;
    s.src = `${UMAMI_HOST}/script.js`;
    s.setAttribute('data-website-id', String(d.umamiId));
    s.setAttribute('data-host-url', UMAMI_HOST);
    s.setAttribute('data-auto-track', 'false');
    s.setAttribute('data-exclude-search', 'true');
    s.setAttribute('data-exclude-hash', 'true');
    s.onload = () => { umamiReady = true; if (lastPath) umamiSend(lastPath); };
    d.doc.head.appendChild(s);
  }

  return {
    state,
    umamiBlockReason: umamiReason,
    isUmamiStarted: () => umamiStarted,
    blockReason: reason,
    isLoaded: () => loaded,
    subscribe(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; },

    /** Called once after the app has mounted: Umami (idle) for everyone who has not
     *  opted out, gtag.js (idle) for a returning "Accept". */
    start() {
      if (!umamiReason()) d.idle(loadUmami);
      if (state() === 'granted') d.idle(load);
    },
    accept() {
      if (reason()) return;
      writeConsent(d.storage, 'granted');
      emit();
      d.idle(load);
    },
    decline() {
      writeConsent(d.storage, 'denied');
      if (loaded) {
        d.win.gtag?.('consent', 'update', { analytics_storage: 'denied' });
        d.win[`ga-disable-${d.id}`] = true;
      }
      emit();
    },
    /** Path only; the caller builds it with pageViewPath().  Held back until consent + load. */
    pageView(path: string) {
      if (path === lastPath) return;
      lastPath = path;
      if (umamiReady && !umamiReason()) umamiSend(path);
      if (!loaded || state() !== 'granted') return;
      send(path);
    },
  };
}

/* ── the live instance ─────────────────────────────────────────────────────── */

function safeStorage(): StorageLike {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}

function idleRunner(fn: () => void) {
  const w = window as any;
  if (typeof w.requestIdleCallback === 'function') w.requestIdleCallback(fn, { timeout: 4000 });
  else setTimeout(fn, 2000);
}

function configuredId(): string {
  const v = (import.meta as any).env?.VITE_GA_ID;
  return typeof v === 'string' ? v.trim() : DEFAULT_GA_ID;
}

function configuredUmamiId(): string {
  const v = (import.meta as any).env?.VITE_UMAMI_WEBSITE_ID;
  return typeof v === 'string' ? v.trim() : DEFAULT_UMAMI_WEBSITE_ID;
}

let live: ReturnType<typeof createAnalytics> | null = null;

/** The browser instance (created on first use, never at import time). */
export function getAnalytics() {
  if (!live) {
    live = createAnalytics({
      id: configuredId(),
      umamiId: configuredUmamiId(),
      win: window as any,
      doc: document,
      storage: safeStorage(),
      origin: location.origin,
      locale: () => { try { return document.documentElement.lang || null; } catch { return null; } },
      pathname: () => { try { return location.pathname; } catch { return null; } },
      timeZone: () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { return null; } },
      doNotTrack: () => { try { return navigator.doNotTrack ?? null; } catch { return null; } },
      gpc: () => { try { return (navigator as any).globalPrivacyControl === true; } catch { return null; } },
      idle: idleRunner,
    });
  }
  return live;
}

/** Event the "Cookie settings" links fire to reopen the consent bar. */
export const OPEN_COOKIE_SETTINGS_EVENT = 'aerostator:open-cookie-settings';
