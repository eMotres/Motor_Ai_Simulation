// node --test — deep links into the app and the return-to logic that carries them through sign-in (owner 2026-10-26).
// Imports appLink.ts itself (Node >= 22.18 / 24; else skipped).  The server re-validates every path it is given
// (tests/test_production_share_link.py): the valid/hostile lists below are the same ones, so browser and server agree.
import test from 'node:test';
import assert from 'node:assert/strict';

let L = null;
try { L = await import('../appLink.ts'); } catch { /* old Node */ }
const t = L ? test : test.skip;

const DIE = 'CIANO14 40 new';
const PROD = '/production/CIANO14%2040%20new/L12';

const split = (p) => { const i = p.indexOf('?'); return i < 0 ? [p, ''] : [p.slice(0, i), p.slice(i)]; };
const parse = (p) => L.parseAppLink(...split(p));

t('production links: root, every tab, item, documents filters round-trip', () => {
  assert.deepEqual(parse(PROD), { kind: 'production', die: DIE, config: 'L12' });
  for (const tab of L.PRODUCTION_TABS) {
    const link = parse(`${PROD}/${tab}`);
    assert.deepEqual(link, { kind: 'production', die: DIE, config: 'L12', tab });
    assert.equal(L.appLinkPath(link), `${PROD}/${tab}`);
  }
  assert.equal(L.appLinkPath(parse(`${PROD}/changes?item=1.2`)), `${PROD}/changes?item=1.2`);
  assert.deepEqual(parse(`${PROD}/documents?since=2026-10-19&ids=doc_a,doc-b`),
    { kind: 'production', die: DIE, config: 'L12', tab: 'documents', since: '2026-10-19', ids: ['doc_a', 'doc-b'] });
  assert.equal(L.appLinkPath(parse(`${PROD}/documents?ids=doc_a`)), `${PROD}/documents?ids=doc_a`);
  assert.equal(L.appLinkPath(parse(`${PROD}/discussion?item=th_Abc-1&signin=1&verify=zz`)), `${PROD}/discussion?item=th_Abc-1`);   // unknown keys dropped
});

t('project links: project, views, motor step, item round-trip', () => {
  assert.deepEqual(parse('/projects/erium-x1'), { kind: 'project', id: 'erium-x1' });
  for (const view of L.PROJECT_VIEWS) assert.equal(L.appLinkPath(parse(`/projects/erium-x1/${view}`)), `/projects/erium-x1/${view}`);
  const step = parse('/projects/erium-x1/motors?die=CIANO28%2085&config=L12');
  assert.deepEqual(step, { kind: 'project', id: 'erium-x1', view: 'motors', die: 'CIANO28 85', config: 'L12' });
  assert.equal(L.appLinkPath(step), '/projects/erium-x1/motors?die=CIANO28%2085&config=L12');
  assert.equal(L.appLinkPath(parse('/projects/erium-x1/messages?item=Ab-1_x')), '/projects/erium-x1/messages?item=Ab-1_x');
});

t('the same hostile and foreign addresses the server refuses', () => {
  for (const p of [
    '/', '/?verify=abc', '/agent-consent?request=1', '/admin', '/api/auth/logout', '/production', '/projects', '/projects/', '/projects/Bad_Id',
    '//evil.com/production/a/b/documents', '//evil.com/projects/p1', '/production/../admin/x/documents', '/production/a/..%2fb/documents',
    '/production/a%2Fb/c/documents', '/production/a%5Cb/c/documents', '/production/%2e%2e/b/documents', '/production//b/documents',
    '/production/a/b/documents?since=yesterday', '/production/a/b/documents?since=2026-13-45', '/production/a/b/documents?ids=a//b',
    '/production/a/b/documents?ids=<script>', '/production/' + 'a'.repeat(90) + '/b/documents',
    '/production/a/b/secrets', '/production/a/b/bom?since=2026-10-19', '/production/a/b/bom?ids=x', '/production/a/b/bom?item=<x>',
    '/projects/p1/admin', '/projects/p1/../p2', '/projects/p1//motors', '/projects/p1/motors?die=a', '/projects/p1/motors?config=a',
    '/projects/p1/motors?die=../x&config=y', '/projects/p1/motors?die=a&config=b&item=' + 'x'.repeat(90), '/projects/' + 'a'.repeat(70),
    '/projects/p1\\evil', '/production/a/b/documents\n', '/production/a/b/documents\u0000',
  ]) assert.equal(parse(p), null, p);
});

t('build refuses what the server would refuse', () => {
  assert.equal(L.appLinkPath({ kind: 'production', die: DIE, config: '../x' }), null);
  assert.equal(L.appLinkPath({ kind: 'production', die: DIE, config: 'L12', tab: 'nope' }), null);
  assert.equal(L.appLinkPath({ kind: 'production', die: DIE, config: 'L12', tab: 'bom', since: '2026-10-19' }), null);
  assert.equal(L.appLinkPath({ kind: 'project', id: 'Bad Id' }), null);
  assert.equal(L.appLinkPath({ kind: 'project', id: 'p1', die: 'only' }), null);
  assert.equal(L.appLinkPath({ kind: 'project', id: 'p1', view: 'admin' }), null);
});

t('isAppLinkPath and the sign-up return path', () => {
  assert.ok(L.isAppLinkPath(PROD) && L.isAppLinkPath(`${PROD}/changes`) && L.isAppLinkPath('/projects/erium-x1/motors'));
  assert.ok(!L.isAppLinkPath('/') && !L.isAppLinkPath('/production') && !L.isAppLinkPath('/agent-consent') && !L.isAppLinkPath('/admin'));
  assert.equal(L.appLinkReturnPath('/projects/erium-x1/motors', '?die=CIANO28 85&config=L12&signin=1'), '/projects/erium-x1/motors?die=CIANO28%2085&config=L12');
  assert.equal(L.appLinkReturnPath('/', ''), undefined);
  assert.equal(L.appLinkReturnPath('//evil.com', ''), undefined);
});

t('safeReturnTarget: same-origin app paths only, no open redirect', () => {
  assert.equal(L.safeReturnTarget(`${PROD}/changes?item=1.1`), `${PROD}/changes?item=1.1`);
  assert.equal(L.safeReturnTarget('/production/CIANO14 40 new/L12/bom'), `${PROD}/bom`);
  for (const bad of [null, undefined, 42, '', 'projects/p1', '//evil.com/projects/p1', '///evil.com', '/\\evil.com', 'https://evil.com/projects/p1',
    'http://localhost:5173/projects/p1', 'javascript:alert(1)', 'data:text/html,x', '/admin', '/?next=//evil.com', '/projects/p1/../../admin',
    '/api/auth/logout', '/agent-consent?request=1']) assert.equal(L.safeReturnTarget(bad), null, String(bad));
});

const fakeStorage = () => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, v); }, removeItem: (k) => { m.delete(k); }, m };
};

t('a signed-out visitor on a link: the target is remembered and survives to the sign-in', () => {
  const s = fakeStorage();
  const target = L.rememberReturnTo(s, '/projects/erium-x1/messages', '?item=m1&signin=1');
  assert.equal(target, '/projects/erium-x1/messages?item=m1');
  assert.equal(s.m.get(L.RETURN_KEY), target);
  // sign-in done in the same page: the address bar still holds the link -> nothing to restore
  assert.equal(L.addressAfterSignIn('/projects/erium-x1/messages', '?item=m1', L.takeReturnTo(s)), null);
  assert.equal(s.m.size, 0, 'consumed once');
});

t('the address bar lost the link (confirmation page, reload on "/"): it is restored from the remembered target', () => {
  const s = fakeStorage();
  L.rememberReturnTo(s, PROD.replace('%20', ' ') + '/changes', '?item=1.1');
  assert.equal(L.addressAfterSignIn('/', '?signin=1', L.takeReturnTo(s)), `${PROD}/changes?item=1.1`);
});

t('a poisoned or foreign remembered value is never followed', () => {
  const s = fakeStorage();
  for (const bad of ['https://evil.example/x', '//evil.example', '/admin', 'javascript:alert(1)', '/projects/p1/../../admin']) {
    s.setItem(L.RETURN_KEY, bad);
    assert.equal(L.takeReturnTo(s), null, bad);
    assert.equal(L.addressAfterSignIn('/', '', bad), null, bad);
  }
  // nothing is remembered for a page that is not a link, and a blocked storage never throws
  assert.equal(L.rememberReturnTo(s, '/admin', ''), null);
  assert.equal(s.m.size, 0);
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } };
  assert.equal(L.rememberReturnTo(blocked, '/projects/p1', ''), '/projects/p1');
  assert.equal(L.takeReturnTo(blocked), null);
  assert.equal(L.takeReturnTo(null), null);
});
