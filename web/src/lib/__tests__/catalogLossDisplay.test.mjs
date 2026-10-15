import test from 'node:test';
import assert from 'node:assert/strict';
import { catalogLossDisplay } from '../catalogLossDisplay.ts';

test('an absent catalogue loss stays unavailable', () => {
  assert.deepEqual(catalogLossDisplay({ loss_w: null }), {
    available: false, totalW: null, electromagneticW: null, mechanicalW: null,
    mechanicalKnown: false, mechanicalDerived: false,
  });
  assert.equal(catalogLossDisplay({}).totalW, null);
  assert.equal(catalogLossDisplay({ loss_w: NaN }).totalW, null);
});

test('a computed physical zero remains a real zero', () => {
  const zero = catalogLossDisplay({ loss_w: 0 });
  assert.equal(zero.available, true);
  assert.equal(zero.totalW, 0);
  assert.equal(zero.mechanicalKnown, false);
});

test('known mechanical loss is included without fabricating an absent term', () => {
  const shown = catalogLossDisplay({ loss_w: 8, loss_mech_w: 2, loss_mech_derived: true });
  assert.equal(shown.totalW, 10);
  assert.equal(shown.mechanicalKnown, true);
  assert.equal(shown.mechanicalDerived, true);
});
