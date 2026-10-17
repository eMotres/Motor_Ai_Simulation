// node --test — pure helpers of the Production module (revisions, bilingual display, caller standing, BOM, replies).
// Imports helpers.ts itself (Node >= 22.18 / 24; else skipped). No network.
import test from 'node:test';
import assert from 'node:assert/strict';

let H = null;
try { H = await import('../helpers.ts'); } catch { /* old Node */ }
const t = H ? test : test.skip;

const bi = (en, zh, src, st) => ({ en, zh, src_lang: src, status: st });

t('revision numbers: parse, increment, numeric order', () => {
  assert.deepEqual(H.parseRevision('1.10'), [1, 10]);
  assert.equal(H.parseRevision('1'), null);
  assert.equal(H.parseRevision(null), null);
  assert.equal(H.nextRevision('1.1'), '1.2');
  assert.equal(H.nextRevision('1.9'), '1.10');
  assert.equal(H.nextRevision('2.0'), '2.1');
  assert.equal(H.nextRevision(''), '1.0');
  assert.ok(H.compareRevisions('1.10', '1.9') > 0);
  assert.ok(H.compareRevisions('1.0', '2.0') < 0);
  const revs = ['1.1', '1.10', '1.2'].map((number) => ({ number, acknowledgements: [] }));
  assert.deepEqual(H.sortRevisions(revs).map((r) => r.number), ['1.10', '1.2', '1.1']);
});

t('unacknowledged revisions of one account, oldest first, e-mail case ignored', () => {
  const revs = [
    { number: '1.1', acknowledgements: [{ email: 'A@B.com', time: 'x' }] },
    { number: '1.2', acknowledgements: [] },
    { number: '1.3', acknowledgements: [{ email: 'c@d.com', time: 'x' }] },
  ];
  assert.deepEqual(H.unackedRevisions(revs, 'a@b.com').map((r) => r.number), ['1.2', '1.3']);
  assert.equal(H.hasAcked(revs[0], 'a@b.com'), true);
});

t('content language follows the interface language', () => {
  assert.equal(H.contentLang('zh-CN'), 'zh');
  assert.equal(H.contentLang('en'), 'en');
  assert.equal(H.contentLang(undefined), 'en');
  assert.equal(H.otherLang('zh'), 'en');
});

t('makeBilingual fills only the author side; the other side is pending', () => {
  const b = H.makeBilingual('Shaft', 'en');
  assert.deepEqual(b, { en: 'Shaft', zh: '', src_lang: 'en', status: { en: 'confirmed', zh: 'pending' } });
  assert.equal(H.needsConfirmation(b), true);
  assert.equal(H.needsConfirmation(bi('a', '甲', 'en', { en: 'confirmed', zh: 'confirmed' })), false);
});

t('displayText: reader language, machine badge, original toggle, pending fallback', () => {
  const b = bi('Shaft', '轴', 'zh', { en: 'machine', zh: 'confirmed' });
  const en = H.displayText(b, 'en');
  assert.equal(en.text, 'Shaft'); assert.equal(en.machine, true); assert.equal(en.isOriginal, false);
  const zh = H.displayText(b, 'zh');
  assert.equal(zh.text, '轴'); assert.equal(zh.machine, false); assert.equal(zh.isOriginal, true);
  const orig = H.displayText(b, 'en', true);
  assert.equal(orig.text, '轴'); assert.equal(orig.machine, false);
  // translation never arrived: the reader sees the original and a pending flag
  const p = H.displayText(bi('', '轴', 'zh', { en: 'pending', zh: 'confirmed' }), 'en');
  assert.equal(p.text, '轴'); assert.equal(p.pending, true); assert.equal(p.lang, 'zh');
  // confirmed translation: no badge
  const c = H.displayText(bi('Shaft', '轴', 'zh', { en: 'confirmed', zh: 'confirmed' }), 'en');
  assert.equal(c.machine, false); assert.equal(c.pending, false);
  assert.equal(H.displayText(null, 'en').text, '');
});

t('caller standing and what each role may do', () => {
  const members = [{ email: 'o@x.com', role: 'owner' }, { email: 'p@x.com', role: 'oem' }];
  assert.equal(H.callerRole(members, 'O@x.com', false), 'owner');
  assert.equal(H.callerRole(members, 'p@x.com', false), 'oem');
  assert.equal(H.callerRole(members, 'z@x.com', true), 'admin');
  assert.equal(H.callerRole(members, 'z@x.com', false), null);
  assert.equal(H.canEditRole('owner'), true);
  assert.equal(H.canEditRole('engineer'), true);
  assert.equal(H.canEditRole('admin'), true);
  assert.equal(H.canEditRole('oem'), false);
  assert.equal(H.canEditRole(null), false);
  assert.equal(H.canProposeRole('oem'), true);
  assert.equal(H.canProposeRole(null), false);
  assert.equal(H.canAckRole('oem'), true);
});

t('OEM members without a signed NDA are flagged; other roles are not', () => {
  const p = {
    members: [{ email: 'p@x.com', role: 'oem' }, { email: 'e@x.com', role: 'engineer' }],
    members_without_nda: ['P@x.com', 'e@x.com', 'ghost@x.com'],
  };
  assert.deepEqual(H.oemWithoutNda(p), ['P@x.com']);
  assert.deepEqual(H.oemWithoutNda({ members: p.members }), []);
  assert.equal(H.memberLabel(p.members[0]), 'p@x.com (oem)');
  assert.equal(H.productionKey({ die: 'CIANO14 40 new', config: 'L12' }), 'CIANO14 40 new / L12');
});

t('BOM: order total follows the per-motor quantity and the order quantity', () => {
  assert.equal(H.orderTotal(2, 100), 200);
  assert.equal(H.orderTotal(0.5, 3), 1.5);
  assert.equal(H.orderTotal(NaN, 100), 0);
  const rows = [{ part_id: 'a', qty_per_motor: 1, order_total: 100 }, { part_id: 'b', qty_per_motor: 2, order_total: 200 }];
  const out = H.withQty(rows, 'b', 3, 100);
  assert.deepEqual(out[1], { part_id: 'b', qty_per_motor: 3, order_total: 300 });
  assert.deepEqual(out[0], rows[0]);
  assert.equal(rows[1].qty_per_motor, 2);   // the input is not mutated
  assert.deepEqual(H.sortByBom([{ id: 'c' }, { id: 'b' }, { id: 'a' }], rows).map((p) => p.id), ['a', 'b', 'c']);
});

t('list replies: array or keyed object; errors: string or {code, message} detail', () => {
  assert.deepEqual(H.unwrapList([1, 2], 'parts'), [1, 2]);
  assert.deepEqual(H.unwrapList({ parts: [3] }, 'parts'), [3]);
  assert.deepEqual(H.unwrapList({ other: [3] }, 'parts'), []);
  assert.deepEqual(H.unwrapList(null, 'parts'), []);
  assert.deepEqual(H.errorInfo(403, { detail: { code: 'nda_required', message: 'Sign the NDA' } }), { code: 'nda_required', message: 'Sign the NDA' });
  assert.deepEqual(H.errorInfo(404, { detail: 'Not found' }), { code: null, message: 'Not found' });
  assert.deepEqual(H.errorInfo(500, null), { code: null, message: 'HTTP 500' });
  assert.equal(H.safeFileName('a/b:c.pdf'), 'a_b_c.pdf');
  assert.equal(H.safeFileName('///'), '_');
});

t('costs: permissions and summary calculation including margin and warnings', () => {
  assert.equal(H.canViewCosts('owner'), true);
  assert.equal(H.canViewCosts('engineer'), true);
  assert.equal(H.canViewCosts('admin'), true);
  assert.equal(H.canViewCosts('oem', false), false);
  assert.equal(H.canViewCosts('oem', true), true);
  assert.equal(H.canViewCosts(null), false);

  assert.equal(H.canEditCosts('owner'), true);
  assert.equal(H.canEditCosts('engineer'), true);
  assert.equal(H.canEditCosts('admin'), true);
  assert.equal(H.canEditCosts('oem'), false);

  const bom = [
    { part_id: 'p1', qty_per_motor: 1, order_total: 10, unit_price: 100 },
    { part_id: 'p2', qty_per_motor: 2, order_total: 20, unit_price: 50 },
    { part_id: 'p3', qty_per_motor: 1, order_total: 10, unit_price: null },
  ];
  const extras = [
    { id: 'e1', name: { en: 'Packaging', zh: '包装', src_lang: 'en', status: { en: 'confirmed', zh: 'confirmed' } }, amount: 20 },
  ];
  const summary = H.calcCostSummary(bom, [], extras, 20, 10);
  // parts subtotal = 100 + 50 = 150
  assert.equal(summary.parts_subtotal, 150);
  assert.equal(summary.extra_subtotal, 20);
  assert.equal(summary.cost_per_motor, 170);
  // price = 170 / (1 - 0.20) = 170 / 0.8 = 212.5
  assert.equal(summary.price_per_motor, 212.5);
  assert.equal(summary.order_parts_total, 1500);
  assert.equal(summary.order_extra_total, 200);
  assert.equal(summary.order_cost_total, 1700);
  assert.equal(summary.order_price_total, 2125);
  assert.equal(summary.missing_prices_count, 1);
  assert.equal(summary.warnings.length, 1);
});
