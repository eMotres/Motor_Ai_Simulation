// node --test: GA4 load rules (lib/analytics.ts, owner 2026-10-14).
// Imports the TypeScript module directly (Node >= 22.18 / 24).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createAnalytics, blockReason, umamiBlockReason, pageViewPath, CONSENT_STORAGE_KEY,
  DEFAULT_GA_ID, DEFAULT_UMAMI_WEBSITE_ID, UMAMI_HOST }
  from '../analytics.ts';

function rig(over = {}) {
  const store = new Map(over.stored ? [[CONSENT_STORAGE_KEY, over.stored]] : []);
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => { store.set(k, v); } };
  const win = {};
  const appended = [];
  const doc = {
    createElement: (tag) => { const attrs = {}; return { tag, attrs, setAttribute: (k, v) => { attrs[k] = v; } }; },
    head: { appendChild: (el) => appended.push(el) } };
  const idleQueue = [];
  const a = createAnalytics({
    id: over.id === undefined ? 'G-TEST123' : over.id,
    umamiId: over.umamiId === undefined ? 'umami-site-1' : over.umamiId,
    win, doc, storage, origin: 'https://aerostator.com',
    locale: () => over.locale ?? 'en',
    pathname: () => over.pathname ?? '/',
    timeZone: () => over.timeZone ?? 'Europe/Ljubljana',
    doNotTrack: () => over.dnt ?? null,
    gpc: () => over.gpc ?? false,
    idle: (fn) => idleQueue.push(fn),
  });
  const runIdle = () => { while (idleQueue.length) idleQueue.shift()(); };
  const calls = () => (win.dataLayer ?? []).map((x) => Array.from(x));
  const gaScripts = () => appended.filter((e) => String(e.src).includes('googletagmanager'));
  const umamiScripts = () => appended.filter((e) => String(e.src).startsWith('/stats/'));
  return { a, win, appended, store, runIdle, calls, gaScripts, umamiScripts };
}

test('the default measurement id is the aerostator-web stream', () => {
  assert.equal(DEFAULT_GA_ID, 'G-R2325XYL2S');
});

test('China is never loaded: zh-CN locale, /zh-cn/ path, mainland time zones', () => {
  assert.equal(blockReason({ id: 'G-X', locale: 'zh-CN' }), 'china-locale');
  assert.equal(blockReason({ id: 'G-X', locale: 'zh' }), 'china-locale');
  assert.equal(blockReason({ id: 'G-X', locale: 'en', pathname: '/zh-cn/' }), 'china-path');
  assert.equal(blockReason({ id: 'G-X', locale: 'en', pathname: '/zh-cn' }), 'china-path');
  for (const tz of ['Asia/Shanghai', 'Asia/Chongqing', 'Asia/Harbin', 'Asia/Urumqi', 'Asia/Macau']) {
    assert.equal(blockReason({ id: 'G-X', locale: 'en', timeZone: tz }), 'china-time-zone', tz);
  }
  // Hong Kong has Google: allowed.
  assert.equal(blockReason({ id: 'G-X', locale: 'en', timeZone: 'Asia/Hong_Kong' }), null);
  assert.equal(blockReason({ id: 'G-X', locale: 'en', pathname: '/', timeZone: 'Europe/Ljubljana' }), null);
});

test('Do Not Track, Global Privacy Control and a missing id block analytics', () => {
  assert.equal(blockReason({ id: 'G-X', doNotTrack: '1' }), 'do-not-track');
  assert.equal(blockReason({ id: 'G-X', doNotTrack: '0' }), null);
  assert.equal(blockReason({ id: 'G-X', gpc: true }), 'gpc');
  assert.equal(blockReason({ id: '' }), 'no-id');
  assert.equal(blockReason({ id: null }), 'no-id');
});

test('a blocked visitor never loads, even with a stored Accept', () => {
  for (const over of [{ locale: 'zh-CN' }, { pathname: '/zh-cn/' }, { timeZone: 'Asia/Shanghai' },
    { dnt: '1' }, { gpc: true }, { id: '' }]) {
    const r = rig({ ...over, stored: 'granted' });
    assert.equal(r.a.state(), 'blocked');
    r.a.start(); r.a.accept(); r.runIdle();
    r.a.pageView('/motors');
    assert.equal(r.gaScripts().length, 0, JSON.stringify(over));
    assert.equal(r.win.dataLayer, undefined);
    assert.equal(r.win.gtag, undefined);
  }
});

test('nothing loads before the visitor accepts (unknown or declined)', () => {
  const r = rig();
  assert.equal(r.a.state(), 'unknown');
  r.a.start(); r.runIdle();
  r.a.pageView('/motors'); r.runIdle();
  assert.equal(r.gaScripts().length, 0);
  assert.equal(r.win.gtag, undefined);
  r.a.decline(); r.a.start(); r.runIdle();
  assert.equal(r.a.state(), 'denied');
  assert.equal(r.store.get(CONSENT_STORAGE_KEY), 'denied');
  assert.equal(r.gaScripts().length, 0);
  assert.equal(r.win.gtag, undefined);
});

test('Accept loads gtag.js from idle, with consent default denied before config', () => {
  const r = rig();
  r.a.accept();
  assert.equal(r.gaScripts().length, 0, 'not synchronously: waits for idle');
  r.runIdle();
  assert.equal(r.gaScripts().length, 1);
  assert.equal(r.gaScripts()[0].src, 'https://www.googletagmanager.com/gtag/js?id=G-TEST123');
  assert.equal(r.gaScripts()[0].async, true);
  const c = r.calls();
  const iDefault = c.findIndex((x) => x[0] === 'consent' && x[1] === 'default');
  const iUpdate = c.findIndex((x) => x[0] === 'consent' && x[1] === 'update');
  const iConfig = c.findIndex((x) => x[0] === 'config');
  assert.ok(iDefault >= 0 && iDefault < iUpdate && iUpdate < iConfig, JSON.stringify(c));
  assert.deepEqual(c[iDefault][2], {
    ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied',
    analytics_storage: 'denied',
  });
  assert.deepEqual(c[iUpdate][2], { analytics_storage: 'granted' });
  assert.equal(c[iConfig][1], 'G-TEST123');
  assert.equal(c[iConfig][2].anonymize_ip, true);
  assert.equal(c[iConfig][2].send_page_view, false);
  assert.equal(c[iConfig][2].allow_google_signals, false);
  assert.equal(c[iConfig][2].allow_ad_personalization_signals, false);
  // ad_* are never granted anywhere
  assert.ok(!JSON.stringify(c).includes('"ad_storage":"granted"'));
  assert.equal(r.store.get(CONSENT_STORAGE_KEY), 'granted');
});

test('a returning Accept loads only from start() (after mount, idle), once', () => {
  const r = rig({ stored: 'granted' });
  assert.equal(r.appended.length, 0, 'creating the instance loads nothing');
  r.a.start();
  assert.equal(r.appended.length, 0, 'start() only schedules');
  r.runIdle(); r.a.start(); r.runIdle();
  assert.equal(r.gaScripts().length, 1);
  assert.equal(r.umamiScripts().length, 1);
});

test('page views: path only, held until loaded, no duplicates, flushed in order', () => {
  const r = rig();
  r.a.pageView('/motors');            // before consent: remembered, not sent
  r.a.accept(); r.runIdle();
  let views = r.calls().filter((x) => x[0] === 'event' && x[1] === 'page_view');
  assert.equal(views.length, 1);
  assert.deepEqual(views[0][2], { page_path: '/motors', page_location: 'https://aerostator.com/motors' });
  r.a.pageView('/motors');            // same path: ignored
  r.a.pageView('/compare');
  views = r.calls().filter((x) => x[0] === 'event' && x[1] === 'page_view');
  assert.deepEqual(views.map((v) => v[2].page_path), ['/motors', '/compare']);
  for (const v of views) assert.doesNotMatch(JSON.stringify(v), /[?#@]/);
});

test('decline after accept updates consent to denied and disables the stream', () => {
  const r = rig();
  r.a.accept(); r.runIdle();
  r.a.decline();
  const last = r.calls().at(-1);
  assert.deepEqual(last, ['consent', 'update', { analytics_storage: 'denied' }]);
  assert.equal(r.win['ga-disable-G-TEST123'], true);
  const n = r.calls().length;
  r.a.pageView('/cost');
  assert.equal(r.calls().length, n, 'no page view once declined');
});

test('pageViewPath drops query and hash and keeps only a safe tab segment', () => {
  assert.equal(pageViewPath('/', 'motors'), '/motors');
  assert.equal(pageViewPath('/?verify=abc&email=a@b.c#x', 'compare'), '/compare');
  assert.equal(pageViewPath('/agent-consent?code=1', null), '/agent-consent');
  assert.equal(pageViewPath('/app/', 'model3d'), '/app/model3d');
  assert.equal(pageViewPath('/x', 'a b@c.d'), '/x/abcd');
});

test('a throwing storage still yields a sane state', () => {
  const a = createAnalytics({
    id: 'G-X', win: {}, doc: { createElement: () => ({}), head: { appendChild() {} } },
    storage: { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } },
    origin: 'https://aerostator.com', locale: () => 'en', pathname: () => '/',
    timeZone: () => 'Europe/Paris', doNotTrack: () => null, gpc: () => false, idle: (f) => f(),
  });
  assert.equal(a.state(), 'unknown');
  assert.doesNotThrow(() => a.decline());
});

// ── self-hosted Umami: cookieless, same-origin, for everyone who has not opted out ──

test('Umami defaults: the website id and the same-origin /stats mount', () => {
  assert.equal(DEFAULT_UMAMI_WEBSITE_ID, '8fe8df44-5870-4f08-bfc4-16a79aa44f17');
  assert.equal(UMAMI_HOST, '/stats');
});

test('Umami blocks only on missing id, Do Not Track and Global Privacy Control', () => {
  assert.equal(umamiBlockReason({ umamiId: 'x' }), null);
  assert.equal(umamiBlockReason({ umamiId: '' }), 'no-id');
  assert.equal(umamiBlockReason({ umamiId: 'x', doNotTrack: '1' }), 'do-not-track');
  assert.equal(umamiBlockReason({ umamiId: 'x', gpc: true }), 'gpc');
  // locale / path / time zone are not inputs: China is allowed (same-origin, no Google)
  assert.equal(umamiBlockReason({ umamiId: 'x', locale: 'zh-CN', timeZone: 'Asia/Shanghai' }), null);
});

test('Umami loads for China and without any GA consent, but only from start() at idle', () => {
  for (const over of [{ locale: 'zh-CN' }, { pathname: '/zh-cn/' }, { timeZone: 'Asia/Shanghai' }, {}]) {
    const r = rig(over);
    assert.equal(r.appended.length, 0);
    r.a.start();
    assert.equal(r.appended.length, 0, 'start() only schedules');
    r.runIdle();
    assert.equal(r.umamiScripts().length, 1, JSON.stringify(over));
    assert.equal(r.gaScripts().length, 0, 'no GA without consent');
    const s = r.umamiScripts()[0];
    assert.equal(s.src, '/stats/script.js');
    assert.equal(s.attrs['data-website-id'], 'umami-site-1');
    assert.equal(s.attrs['data-host-url'], '/stats');
    assert.equal(s.attrs['data-auto-track'], 'false');
  }
});

test('Umami never loads with Do Not Track, Global Privacy Control or no id', () => {
  for (const over of [{ dnt: '1' }, { gpc: true }, { umamiId: '' }]) {
    const r = rig(over);
    r.a.start(); r.runIdle(); r.a.pageView('/motors');
    assert.equal(r.umamiScripts().length, 0, JSON.stringify(over));
  }
});

test('Umami page views: path only, held until the tracker is ready, no duplicates', () => {
  const r = rig();
  const sent = [];
  r.win.umami = { track: (fn) => { sent.push(fn({ url: '/?verify=SECRET&e=a@b.c#h', title: 'T', website: 'w' })); } };
  r.a.start();
  r.a.pageView('/motors');
  r.runIdle();
  assert.equal(sent.length, 0, 'not before the script has loaded');
  r.umamiScripts()[0].onload();
  assert.deepEqual(sent, [{ url: '/motors', title: 'T', website: 'w' }]);
  r.a.pageView('/motors');
  r.a.pageView('/compare');
  assert.deepEqual(sent.map((x) => x.url), ['/motors', '/compare']);
  assert.doesNotMatch(JSON.stringify(sent), /SECRET|@|[?#]/);
});

test('declining GA does not stop Umami (cookieless), and a broken tracker never throws', () => {
  const r = rig();
  r.a.start(); r.runIdle(); r.umamiScripts()[0].onload();
  r.a.decline();
  r.win.umami = { track() { throw new Error('boom'); } };
  assert.doesNotThrow(() => r.a.pageView('/cost'));
});
