/**
 * The common catalogue browser's logic, tested without a browser.
 * `catalogLogic.ts` has type-only imports, so Node's type stripping loads the
 * SHIPPED module directly.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  filterCards, facet, provBadge, provOf, provFor, flattenBody, fmtValue,
  compareRows, toggleCompare, sourceHref,
} from '../catalogLogic.ts';

const CARDS = [
  { id: '61811-2RS1', kind: 'bearing', manufacturer: 'SKF', status: 'validated',
    description: 'deep groove, contact seals', cols: { type: 'deep_groove', d: 55 } },
  { id: '71910 CE/HCP4A', kind: 'bearing', manufacturer: 'SKF', status: 'active',
    description: 'super-precision angular contact', cols: { type: 'angular_contact', d: 50 } },
  { id: 'WCMS900B170E53', kind: 'device', manufacturer: 'YangZhou GuoYang', status: 'active',
    description: '1700 V module', cols: { type: 'sic_mosfet', package: '62 mm module' } },
];

test('search matches every word, case-insensitively, across id/maker/description/type', () => {
  assert.deepEqual(filterCards(CARDS, { q: 'skf angular' }).map((c) => c.id), ['71910 CE/HCP4A']);
  assert.deepEqual(filterCards(CARDS, { q: '62 MM' }).map((c) => c.id), ['WCMS900B170E53']);
  assert.equal(filterCards(CARDS, {}).length, 3);
});

test('facet filters combine', () => {
  assert.deepEqual(filterCards(CARDS, { manufacturer: 'SKF', status: 'validated' }).map((c) => c.id),
    ['61811-2RS1']);
  assert.deepEqual(filterCards(CARDS, { type: 'sic_mosfet' }).map((c) => c.id), ['WCMS900B170E53']);
  assert.deepEqual(facet(CARDS, 'manufacturer'), ['SKF', 'YangZhou GuoYang']);
  assert.deepEqual(facet(CARDS, 'type'), ['angular_contact', 'deep_groove', 'sic_mosfet']);
});

test('badges and the default provenance rule', () => {
  assert.equal(provBadge({ type: 'datasheet' }), 'D');
  assert.equal(provBadge({ type: 'measured' }), 'M');
  assert.equal(provBadge({ type: 'estimate' }), 'E');
  assert.equal(provBadge({ type: 'derived' }), '∂');
  assert.equal(provBadge(undefined), 'D');
  const env = { sources: [{ id: 'skf_catalogue' }], prov: { C_kn: { type: 'datasheet', verify: true } } };
  assert.equal(provOf(env, 'C_kn').verify, true);
  assert.deepEqual(provOf(env, 'd'), { type: 'datasheet', src: 'skf_catalogue', default: true });
  const dev = { sources: [{ id: 'datasheet' }], prov: { r_ds_on: { type: 'datasheet', note: 'p.2' } } };
  assert.equal(provFor(dev, 'r_ds_on.curves').note, 'p.2');
});

test('flattenBody goes one level deep, lists stay whole', () => {
  const f = flattenBody({ d: 55, friction: { R1: 4.7e-7, S1: 6.5e-3 }, temp: [-30, 110] });
  assert.deepEqual(f.map(([k]) => k), ['d', 'friction.R1', 'friction.S1', 'temp']);
});

test('fmtValue: null is "not published", tiny and huge numbers go exponential', () => {
  assert.equal(fmtValue(null), '—');
  assert.equal(fmtValue(1.0e8), '1.000e8');
  assert.equal(fmtValue(4.7e-7), '4.700e-7');
  assert.equal(fmtValue(9.56), '9.56');
});

test('compare: union of fields, differences flagged, text left out', () => {
  const rows = compareRows([
    { body: { d: 55, D: 72, note: 'x', friction: { R1: 4.7e-7 } } },
    { body: { d: 55, D: 80, note: 'y', friction: { R1: 5.03e-7, R3: 1.9e-12 } } },
  ]);
  const by = Object.fromEntries(rows.map((r) => [r.field, r]));
  assert.equal(by.d.differs, false);
  assert.equal(by.D.differs, true);
  assert.equal(by['friction.R3'].values[0], '—');
  assert.equal(by.note, undefined);
});

test('compare set holds at most three, the oldest drops', () => {
  let s = [];
  for (const id of ['a', 'b', 'c', 'd']) s = toggleCompare(s, id);
  assert.deepEqual(s, ['b', 'c', 'd']);
  assert.deepEqual(toggleCompare(s, 'c'), ['b', 'd']);
});

test('only http(s) sources become links', () => {
  assert.equal(sourceHref({ id: 's', url: 'https://skf.com/x' }), 'https://skf.com/x');
  assert.equal(sourceHref({ id: 's', url: null, file: 'docs/datasheets/a.pdf' }), null);
  assert.equal(sourceHref({ id: 's', url: 'javascript:alert(1)' }), null);
});
