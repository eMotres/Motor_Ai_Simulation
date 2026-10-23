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

t('ZIP download visibility and route follow member role and selected language', () => {
  for (const role of ['owner', 'engineer', 'oem', 'admin']) assert.equal(H.canDownloadProductionDocuments(role), true);
  assert.equal(H.canDownloadProductionDocuments(null), false);
  assert.equal(H.productionDocumentsZipPath('L12/alpha', '40%', 'zh'), '/api/production/L12%2Falpha/40%25/documents.zip?lang=zh');
  assert.equal(H.productionDocumentsZipPath('L12', '40', 'en'), '/api/production/L12/40/documents.zip?lang=en');
});

t('ZIP filename prefers decoded RFC 5987 UTF-8 revision name and falls back safely', () => {
  assert.equal(H.productionZipFilename(
    `attachment; filename="L12_rev1.2_documents.zip"; filename*=UTF-8''L12_%E8%BD%B4_rev1.2_documents.zip`,
    'fallback.zip',
  ), 'L12_\u8f74_rev1.2_documents.zip');
  assert.equal(H.productionZipFilename(
    `attachment; filename="L12_rev1.2_documents.zip"; filename*=UTF-8''%E0%A4%A`,
    'fallback.zip',
  ), 'L12_rev1.2_documents.zip');
  assert.equal(H.productionZipFilename('attachment', 'fallback.zip'), 'fallback.zip');
});

t('ZIP error status maps to a localized UI message key', () => {
  assert.equal(H.productionZipErrorKey(413), 'tooLarge');
  assert.equal(H.productionZipErrorKey(403), 'access');
  assert.equal(H.productionZipErrorKey(503), 'unavailable');
  assert.equal(H.productionZipErrorKey(500), 'failed');
  assert.equal(H.productionZipErrorKey(null), 'failed');
});

t('process document view stays HTML while download requests and names a PDF', () => {
  assert.equal(H.productionProcessDocumentPath('L12', '40', 'en', 'view'),
    '/api/production/L12/40/process_document?lang=en');
  assert.equal(H.productionProcessDocumentPath('L12', '40', 'zh', 'download'),
    '/api/production/L12/40/process_document?lang=zh&download=true');
  assert.equal(H.productionProcessDocumentFilename('L12', '40', 'zh'), 'L12_40_process_zh.pdf');
  assert.equal(H.processDocumentErrorKey('download', 503), 'pdfUnavailable');
  assert.equal(H.processDocumentErrorKey('download', 413), 'pdfTooLarge');
  assert.equal(H.processDocumentErrorKey('view', 503), null);
  assert.equal(H.processDocumentErrorKey('view', 413), null);
  assert.equal(H.processDocumentErrorKey('download', 500), null);
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
  assert.equal(summary.by_currency.length, 1);
  const c = summary.by_currency[0];
  assert.equal(c.currency, 'CNY');
  // parts subtotal = 100 + 50 = 150
  assert.equal(c.parts_subtotal, 150);
  assert.equal(c.extra_subtotal, 20);
  assert.equal(c.cost_per_motor, 170);
  // price = 170 / (1 - 0.20) = 170 / 0.8 = 212.5
  assert.equal(c.price_per_motor, 212.5);
  assert.equal(c.order_parts_total, 1500);
  assert.equal(c.order_extra_total, 200);
  assert.equal(c.order_cost_total, 1700);
  assert.equal(c.order_price_total, 2125);
  assert.equal(summary.missing_prices_count, 1);
  assert.equal(summary.warnings.length, 1);
});

t('formatAmount: 2 decimals, thin-space thousands, the same in every locale', () => {
  const T = ' ';
  assert.equal(H.formatAmount(325), '325.00');
  assert.equal(H.formatAmount(3.25), '3.25');
  assert.equal(H.formatAmount(1234), `1${T}234.00`);
  assert.equal(H.formatAmount(1234567.891), `1${T}234${T}567.89`);
  assert.equal(H.formatAmount(999.999), `1${T}000.00`);
  assert.equal(H.formatAmount(0), '0.00');
  assert.equal(H.formatAmount(-1234.5), `-1${T}234.50`);
  assert.equal(H.formatAmount(-0.001), '0.00');
  assert.equal(H.formatAmount(null), '—');
  assert.equal(H.formatAmount(undefined), '—');
  assert.equal(H.formatAmount(NaN), '—');
});

t('formatMoney / formatCurrencyAmounts: the currency code travels with the amount', () => {
  const T = ' ';
  const N = ' ';
  assert.equal(H.formatMoney(325, 'CNY'), `325.00${N}CNY`);
  assert.equal(H.formatMoney(325), `325.00${N}CNY`); // default currency
  assert.equal(H.formatMoney(12.5, 'EUR'), `12.50${N}EUR`);
  assert.equal(H.formatMoney(null, 'EUR'), '—');
  assert.equal(H.formatCurrencyAmounts([]), '—');
  assert.equal(H.formatCurrencyAmounts([{ currency: 'USD', amount: 1234 }]), `1${T}234.00${N}USD`);
  assert.equal(
    H.formatCurrencyAmounts([{ currency: 'CNY', amount: 1234 }, { currency: 'EUR', amount: 56 }]),
    `CNY${N}1${T}234.00 · EUR${N}56.00`,
  );
  assert.deepEqual(H.currencyOptions('USD'), ['CNY', 'USD', 'EUR']);
  assert.deepEqual(H.currencyOptions('JPY'), ['CNY', 'USD', 'EUR', 'JPY']);
  assert.deepEqual(H.currencyOptions(), ['CNY', 'USD', 'EUR']);
});

t('calcCostSummary: mixed currencies give one subtotal per currency, never one summed number', () => {
  const bom = [
    { part_id: 'a', qty_per_motor: 1, order_total: 100, unit_price: 3.25 },
    { part_id: 'b', qty_per_motor: 1, order_total: 100, unit_price: 10 },
    { part_id: 'c', qty_per_motor: 1, order_total: 100, unit_price: 5 },
    { part_id: 'd', qty_per_motor: 1, order_total: 100, unit_price: null },
  ];
  const costs = [
    { part_id: 'a', unit_price: 3.25, currency: 'CNY', note: null },
    { part_id: 'b', unit_price: 10, currency: 'EUR', note: null },
    { part_id: 'c', unit_price: 5, currency: 'CNY', note: null },
  ];
  const extras = [
    { id: 'e1', name: { en: 'Pack', zh: '包装', src_lang: 'en', status: { en: 'confirmed', zh: 'confirmed' } }, amount: 4, currency: 'EUR' },
    { id: 'e2', name: { en: 'Ship', zh: '运输', src_lang: 'en', status: { en: 'confirmed', zh: 'confirmed' } }, amount: 2 }, // default CNY
    { id: 'e3', name: { en: 'Tax', zh: '税', src_lang: 'en', status: { en: 'confirmed', zh: 'confirmed' } }, amount: 7, currency: 'USD' },
  ];
  const s = H.calcCostSummary(bom, costs, extras, 50, 100);
  assert.deepEqual(s.by_currency.map((x) => x.currency), ['CNY', 'USD', 'EUR']);
  const by = Object.fromEntries(s.by_currency.map((x) => [x.currency, x]));
  assert.equal(by.CNY.parts_subtotal, 8.25); // 3.25 + 5, not + 10
  assert.equal(by.CNY.extra_subtotal, 2);
  assert.equal(by.CNY.cost_per_motor, 10.25);
  assert.equal(by.CNY.price_per_motor, 20.5); // 10.25 / (1 - 0.5)
  assert.equal(by.CNY.order_price_total, 2050);
  assert.equal(by.EUR.parts_subtotal, 10);
  assert.equal(by.EUR.extra_subtotal, 4);
  assert.equal(by.EUR.cost_per_motor, 14);
  assert.equal(by.USD.parts_subtotal, 0);
  assert.equal(by.USD.extra_subtotal, 7);
  assert.equal(s.missing_prices_count, 1);
  const line = H.formatCurrencyAmounts(s.by_currency.map((x) => ({ currency: x.currency, amount: x.cost_per_motor })));
  assert.equal(line, 'CNY 10.25 · USD 7.00 · EUR 14.00');
  // nothing priced at all: still one (default) currency, zeros
  const empty = H.calcCostSummary([], [], [], 0, 5);
  assert.deepEqual(empty.by_currency.map((x) => [x.currency, x.cost_per_motor]), [['CNY', 0]]);
});

t('procurement permissions: OEM can edit procurement, only editors can edit schedule', () => {
  assert.equal(H.canEditProcurement('oem'), true);
  assert.equal(H.canEditProcurement('engineer'), true);
  assert.equal(H.canEditProcurement('admin'), true);
  assert.equal(H.canEditProcurement(null), false);

  assert.equal(H.canEditSchedule('oem'), false);
  assert.equal(H.canEditSchedule('engineer'), true);
  assert.equal(H.canEditSchedule('owner'), true);
  assert.equal(H.canEditScheduleOem('oem'), true);
});

t('procurement status calculation priority: received > partial > late > at_risk > confirmed > ordered > to_order > not_ordered', () => {
  const today = '2026-10-20';

  // not_ordered
  assert.equal(H.computeClientProcurementStatus({ today }), 'not_ordered');

  // to_order
  assert.equal(H.computeClientProcurementStatus({
    need_by: '2026-10-25',
    lead_time_days: 10,
    today,
  }), 'to_order');

  // ordered
  assert.equal(H.computeClientProcurementStatus({
    ordered_on: '2026-10-16',
    need_by: '2026-10-30',
    lead_time_days: 5,
    today,
  }), 'ordered');

  // confirmed
  assert.equal(H.computeClientProcurementStatus({
    ordered_on: '2026-10-16',
    confirmed_on: '2026-10-22',
    need_by: '2026-10-30',
    today,
  }), 'confirmed');

  // at_risk: arrival > need_by
  assert.equal(H.computeClientProcurementStatus({
    ordered_on: '2026-10-16',
    confirmed_on: '2026-11-05',
    need_by: '2026-10-30',
    today,
  }), 'at_risk');

  // late: arrival < today and not received
  assert.equal(H.computeClientProcurementStatus({
    ordered_on: '2026-10-10',
    confirmed_on: '2026-10-18',
    need_by: '2026-10-30',
    today,
  }), 'late');

  // partial: some accepted
  assert.equal(H.computeClientProcurementStatus({
    ordered_on: '2026-10-10',
    confirmed_on: '2026-10-25',
    need_by: '2026-10-30',
    qty_ordered: 10,
    receipts: [{ accepted: 4, rejected: 0, date: '2026-10-18' }],
    today,
  }), 'partial');

  // received: accepted >= needed
  assert.equal(H.computeClientProcurementStatus({
    ordered_on: '2026-10-10',
    confirmed_on: '2026-10-25',
    need_by: '2026-10-30',
    qty_ordered: 10,
    receipts: [{ accepted: 10, rejected: 0, date: '2026-10-19' }],
    today,
  }), 'received');
});

t('Gantt date math: daysBetween, dateToPx, dateSpanToPx, formatShortDate', () => {
  assert.equal(H.daysBetween('2026-10-01', '2026-10-10'), 9);
  assert.equal(H.daysBetween('2026-10-10', '2026-10-01'), -9);
  assert.equal(H.daysBetween('', '2026-10-10'), 0);

  // dateToPx: 5 days offset * 24px = 120px
  assert.equal(H.dateToPx('2026-10-06', '2026-10-01', 24), 120);

  // dateSpanToPx: inclusive 5 days [Oct 1..Oct 5] * 20px = 100px
  assert.equal(H.dateSpanToPx('2026-10-01', '2026-10-05', 20), 100);

  assert.equal(H.formatShortDate('2026-10-15'), '10-15');
  assert.equal(H.formatShortDate(null), '—');
});

t('assembly tree: canonical mapping and default hierarchy', () => {
  assert.equal(H.matchDefaultParent('Stator core 40_12', '定子铁芯 40_12'), 'sub_stator');
  assert.equal(H.matchDefaultParent('Coil 40_12', '线圈 40_12'), 'sub_stator');
  assert.equal(H.matchDefaultParent('Slot insulation', '槽绝缘'), 'sub_stator');
  assert.equal(H.matchDefaultParent('Coil-fixing ring 40', '线圈固定环 40'), 'sub_stator');
  assert.equal(H.matchDefaultParent('Phase board 40 rev C', '相板 40 rev C'), 'sub_stator');
  assert.equal(H.matchDefaultParent('Lead wire', '引线'), 'sub_stator');

  assert.equal(H.matchDefaultParent('Rotor core 40_12', '转子铁芯 40_12'), 'sub_rotor');
  assert.equal(H.matchDefaultParent('Magnet 40_12', '磁钢 40_12'), 'sub_rotor');
  assert.equal(H.matchDefaultParent('Shaft 40_12', '轴 40_12'), 'sub_rotor');

  assert.equal(H.matchDefaultParent('Front holder 40', '前支架 40'), 'sub_housing');
  assert.equal(H.matchDefaultParent('Back holder 40', '后支架 40'), 'sub_housing');
  assert.equal(H.matchDefaultParent('Bearing 618/8-2Z C3', '轴承 618/8-2Z C3'), 'sub_housing');
  assert.equal(H.matchDefaultParent('Socket head screw M2.5', '内六角螺钉 M2.5'), 'sub_housing');
  assert.equal(H.matchDefaultParent('Prop_fix 40', '螺旋桨固定片 40'), 'sub_housing');

  // Build tree from raw unparented parts
  const rawParts = [
    { id: 'p_sc', name: { en: 'Stator core 40_12', zh: '定子铁芯 40_12' }, kind: 'made', qty_per_motor: 1 },
    { id: 'p_mg', name: { en: 'Magnet 40_12', zh: '磁钢 40_12' }, kind: 'purchased', qty_per_motor: 14 },
    { id: 'p_fh', name: { en: 'Front holder 40', zh: '前支架 40' }, kind: 'made', qty_per_motor: 1 },
  ];

  const tree = H.buildAssemblyTree(rawParts);
  assert.equal(tree.id, 'motor');
  assert.equal(tree.sub_assemblies.length, 3);
  const subIds = tree.sub_assemblies.map((s) => s.node.id);
  assert.ok(subIds.includes('sub_stator'));
  assert.ok(subIds.includes('sub_rotor'));
  assert.ok(subIds.includes('sub_housing'));

  const statorSub = tree.sub_assemblies.find((s) => s.node.id === 'sub_stator');
  assert.equal(statorSub.children.length, 1);
  assert.equal(statorSub.children[0].id, 'p_sc');
});

t('assembly cost roll-up: sub-assembly cost is the sum of children', () => {
  const parts = [
    { id: 'sub_s', name: { en: 'Stator assembly', zh: '定子总成' }, kind: 'assembly', qty_per_motor: 1, parent_id: 'motor' },
    { id: 'sub_r', name: { en: 'Rotor assembly', zh: '转子总成' }, kind: 'assembly', qty_per_motor: 1, parent_id: 'motor' },
    { id: 'p_core', name: { en: 'Stator core', zh: '定子铁芯' }, kind: 'made', qty_per_motor: 1, parent_id: 'sub_s' },
    { id: 'p_coil', name: { en: 'Coil', zh: '线圈' }, kind: 'made', qty_per_motor: 6, parent_id: 'sub_s' },
    { id: 'p_mag', name: { en: 'Magnet', zh: '磁钢' }, kind: 'purchased', qty_per_motor: 14, parent_id: 'sub_r' },
    { id: 'p_shaft', name: { en: 'Shaft', zh: '转轴' }, kind: 'made', qty_per_motor: 1, parent_id: 'sub_r' },
  ];

  const costRows = [
    { part_id: 'p_core', unit_price: 110, currency: 'CNY' },
    { part_id: 'p_coil', unit_price: 65, currency: 'CNY' },
    { part_id: 'p_mag', unit_price: 85, currency: 'CNY' },
    { part_id: 'p_shaft', unit_price: 40, currency: 'CNY' },
  ];

  const rollups = H.calcAssemblyRollup(parts, costRows);
  assert.equal(rollups.get('p_core'), 110);
  assert.equal(rollups.get('p_coil'), 65);
  // Stator assembly sum = 110 + 65 = 175
  assert.equal(rollups.get('sub_s'), 175);
  // Rotor assembly sum = 85 + 40 = 125
  assert.equal(rollups.get('sub_r'), 125);
  // Motor total = 175 + 125 = 300
  assert.equal(rollups.get('motor'), 300);

  // Formatted tree includes line_totals
  const tree = H.buildAssemblyTree(parts, costRows, 10);
  const sNode = tree.sub_assemblies.find((s) => s.node.id === 'sub_s').node;
  assert.equal(sNode.unit_price, 175);
  assert.equal(sNode.line_total, 1750);
});

t('formatReceiptTime: formats ISO timestamp or returns date/dash', () => {
  assert.equal(H.formatReceiptTime('2026-10-17T14:30:00Z'), '2026-10-17 14:30');
  assert.equal(H.formatReceiptTime('2026-10-17T09:15'), '2026-10-17 09:15');
  assert.equal(H.formatReceiptTime('2026-10-17'), '2026-10-17');
  assert.equal(H.formatReceiptTime(''), '—');
  assert.equal(H.formatReceiptTime(null), '—');
});

