// node --test - the naming rule for new motors (twin of tests/test_die_names.py).
// Imports lib/dieNameRule.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let R = null;
try { R = await import('../dieNameRule.ts'); } catch { /* old Node */ }
const t = R ? test : test.skip;

const REAL = [
  ['100 mm · 24s-28p mid-torque', '100 mm'],
  ['40 mm · 12s-14p high-speed', '40 mm'],
  ['CIANO 150_40', 'CIANO 150_40'],
  ['CIANO10 200', 'CIANO10 200'],
  ['CIANO10 200 opt', 'CIANO10 200'],
  ['CIANO14 30_10', 'CIANO14 30_10'],
  ['CIANO14 40 new', 'CIANO14 40'],
  ['CIANO14 50 edited', 'CIANO14 50'],
  ['CIANO28 150_35', 'CIANO28 150_35'],
  ['CIANO28 150_35 new', 'CIANO28 150_35'],
  ['CIANO28 85 20RSW175', 'CIANO28 85'],
  ['CIANO28 85 20SW1200', 'CIANO28 85'],
  ['CILN28', 'CILN28'],
  ['M1 850Nm', 'M1'],
  ['CIANO56 400_80 Ø400 48s56p opt3', 'CIANO56 400_80'],
  ['CIANO14 50 edited 12s16p', 'CIANO14 50'],
  ['CIANO14 edited Ø60 12s14p', 'CIANO14'],
  ['CIANO56 400_80 vadim 2', 'CIANO56 400_80'],
];

for (const [name, base] of REAL) {
  t(`family base of "${name}"`, () => assert.equal(R.familyBase(name), base));
}

t('empty and odd shapes', () => {
  assert.equal(R.familyBase(''), '');
  assert.equal(R.familyBase('   '), '');
  assert.equal(R.familyBase('Ø400 48s56p'), 'Ø400');
});
