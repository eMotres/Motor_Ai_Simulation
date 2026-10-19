// node --test — shareable production documents link: build, parse, return-path validation, "new since" (owner 2026-10-19).
// Imports productionLink.ts itself (Node >= 22.18 / 24; else skipped).  The server re-validates every path it is given
// (tests/test_production_share_link.py); this file covers what the browser builds, parses and hands to the sign-up.
import test from 'node:test';
import assert from 'node:assert/strict';

let L = null;
try { L = await import('../productionLink.ts'); } catch { /* old Node */ }
const t = L ? test : test.skip;

const DIE = 'CIANO14 40 new';
const PATH = '/production/CIANO14%2040%20new/L12/documents';

t('build: readable, stable, encoded', () => {
  assert.equal(L.productionLinkUrl('https://aerostator.com/', { die: DIE, config: 'L12' }), `https://aerostator.com${PATH}`);
  assert.equal(L.productionLinkPath({ die: DIE, config: 'L12', since: '2026-10-19' }), `${PATH}?since=2026-10-19`);
  assert.equal(L.productionLinkPath({ die: DIE, config: 'L12', ids: ['doc_a', 'doc-b'] }), `${PATH}?ids=doc_a,doc-b`);
});

t('build refuses what the server would refuse', () => {
  assert.equal(L.productionLinkPath({ die: DIE, config: '../x' }), null);
  assert.equal(L.productionLinkPath({ die: 'a/b', config: 'c' }), null);
  assert.equal(L.productionLinkPath({ die: DIE, config: 'L12', since: 'soon' }), null);
  assert.equal(L.productionLinkPath({ die: DIE, config: 'L12', since: '2026-02-30' }), null);
  assert.equal(L.productionLinkPath({ die: DIE, config: 'L12', ids: ['<x>'] }), null);
  assert.equal(L.productionLinkPath({ die: DIE, config: 'L12', ids: Array.from({ length: 51 }, (_, i) => `d${i}`) }), null);
});

t('parse round-trips the built link', () => {
  const link = { die: DIE, config: 'L12', since: '2026-10-19', ids: ['doc_a'] };
  const p = L.productionLinkPath(link);
  const [pathname, search] = p.split('?');
  assert.deepEqual(L.parseProductionLink(pathname, `?${search}`), link);
  assert.deepEqual(L.parseProductionLink(PATH), { die: DIE, config: 'L12' });
  assert.deepEqual(L.parseProductionLink(`${PATH}/`, '?signin=1&verify=x'), { die: DIE, config: 'L12' });   // unknown keys ignored
});

t('parse refuses foreign, hostile and malformed addresses', () => {
  for (const [p, q] of [
    ['/', ''], ['/production', ''], ['/production/a/b', ''], ['/production/a/b/parts', ''], ['/admin', ''],
    ['//evil.com/production/a/b/documents', ''], ['/production/../x/documents', ''], ['/production/a%2Fb/c/documents', ''],
    ['/production/a%5Cb/c/documents', ''], ['/production/%ZZ/c/documents', ''], ['/production/a/b/documents\n', ''],
    ['/production/a/b/documents', '?since=yesterday'], ['/production/a/b/documents', '?ids=a//b'],
    ['/production/a/b/documents', '?ids='.concat(Array.from({ length: 60 }, (_, i) => `d${i}`).join(','))],
    ['/production/' + 'a'.repeat(90) + '/b/documents', ''],
  ]) assert.equal(L.parseProductionLink(p, q), null, `${p}${q}`);
});

t('the sign-up return path is the canonical link path or nothing', () => {
  assert.equal(L.shareReturnPath('/production/CIANO14 40 new/L12/documents', '?since=2026-10-19&signin=1'), `${PATH}?since=2026-10-19`);
  assert.equal(L.shareReturnPath('/', ''), undefined);
  assert.equal(L.shareReturnPath('/agent-consent', '?request=1'), undefined);
  assert.equal(L.shareReturnPath('//evil.com', ''), undefined);
});

t('isProductionLinkPath', () => {
  assert.ok(L.isProductionLinkPath(PATH));
  assert.ok(!L.isProductionLinkPath('/production/a/b'));
  assert.ok(!L.isProductionLinkPath('/'));
});

t('new since: on or after the day; unreadable dates are not new', () => {
  assert.ok(L.isNewDocument('2026-10-19T00:00:01Z', '2026-10-19'));
  assert.ok(L.isNewDocument('2026-10-20', '2026-10-19'));
  assert.ok(!L.isNewDocument('2026-10-18T23:59:59Z', '2026-10-19'));
  assert.ok(!L.isNewDocument('garbage', '2026-10-19'));
  assert.ok(!L.isNewDocument(undefined, '2026-10-19'));
  assert.ok(!L.isNewDocument('2026-10-20', undefined));
});

t('documentsForLink: since marks, ids narrows, no link shows all', () => {
  const docs = [
    { id: 'a', uploaded_at: '2026-10-17T10:00:00Z' }, { id: 'b', uploaded_at: '2026-10-19T08:00:00Z' },
    { id: 'c', uploaded_at: '2026-10-20T08:00:00Z' },
  ];
  let v = L.documentsForLink(docs, { since: '2026-10-19' });
  assert.deepEqual(v.shown.map((d) => d.id), ['a', 'b', 'c']);
  assert.deepEqual([...v.newIds].sort(), ['b', 'c']);
  assert.equal(v.narrowed, false);
  v = L.documentsForLink(docs, { ids: ['c', 'zzz-not-visible'] });
  assert.deepEqual(v.shown.map((d) => d.id), ['c']);                          // an id the caller cannot see simply matches nothing
  assert.equal(v.narrowed, true);
  v = L.documentsForLink(docs, null);
  assert.equal(v.shown.length, 3);
  assert.equal(v.newIds.size, 0);
});

t('latest upload day and who may share', () => {
  assert.equal(L.latestUploadDay([{ uploaded_at: '2026-10-17T10:00:00Z' }, { uploaded_at: '2026-10-19T08:00:00Z' }, { uploaded_at: 'x' }]), '2026-10-19');
  assert.equal(L.latestUploadDay([]), '');
  assert.ok(L.canShareLink('owner', false));
  assert.ok(L.canShareLink('admin', true));
  assert.ok(L.canShareLink(null, true));
  assert.ok(!L.canShareLink('engineer', false));
  assert.ok(!L.canShareLink('oem', false));
  assert.ok(!L.canShareLink(null, false));
});
