import test from 'node:test';
import assert from 'node:assert/strict';
import { sweepContentKey, isRealEdit, mayPush } from '../sweepSync.ts';

const five = () => ({
  variations: {
    magnet_fill_up: { mode: 'optimize', min: 0.3, max: 0.35, step: 0.05, scope: '||80|4|6|7' },
    magnet_fill_radius: { mode: 'optimize', min: 0.36, max: 0.44, step: 0.08, scope: '||80|4|6|7' },
    stator_fillet_r1: { mode: 'optimize', min: 0.1, max: 0.2, step: 0.1, scope: '||80|4|6|7' },
    rotor_fill_r: { mode: 'optimize', min: 0.1, max: 0.15, step: 0.05, scope: '||80|4|6|7' },
    current_a: { mode: 'optimize', min: 0.18, max: 0.19, step: 0.01, scope: 'D|C|80|4|6|7' },
  },
  operatingPoints: [{ rpm: 3950, gamma_deg: 0, current_a: 85 }, { rpm: 3950, gamma_deg: 0, current_a: 85 }],
  rippleThreshold: 5,
});

test('the content key ignores the edit stamp and key order', () => {
  const a = five(); const b = { ...five(), updatedAt: 123 };
  b.variations = Object.fromEntries(Object.entries(b.variations).reverse());
  assert.equal(sweepContentKey(a), sweepContentKey(b));
});

test('an automatic re-apply of the same operating point is not an edit; a typed range is', () => {
  const a = five();
  const known = sweepContentKey(a);
  assert.equal(isRealEdit(known, { ...five(), updatedAt: 9 }), false);
  const edited = five(); edited.variations.magnet_fill_radius.max = 0.45;
  assert.equal(isRealEdit(known, edited), true);
  assert.equal(isRealEdit(null, a), true);
});

test('a push never overwrites a server copy newer than the one this browser last saw', () => {
  assert.equal(mayPush(100, 100), true);
  assert.equal(mayPush(100, 0), true);       // the server has no stamped copy
  assert.equal(mayPush(100, 250), false);    // somebody else saved after this browser loaded
  assert.equal(mayPush(300, 250), true);
});

test('stale tab scenario: a tab opened with an older copy cannot replace the five-variable set', () => {
  // tab A saved the five variables at T=200; tab B booted with the T=100 copy (three variables, two fixed)
  const server = { ...five(), updatedAt: 200 };
  const stale = five();
  stale.variations.magnet_fill_up.mode = 'fixed';
  stale.variations.magnet_fill_radius = { mode: 'fixed', min: 0.36, max: 0.37, step: 0.01 };
  stale.updatedAt = 100;
  const staleKnown = sweepContentKey(stale);
  // mounting the Sweep panel re-applies the operating point: same content -> not an edit -> nothing is stamped or pushed
  assert.equal(isRealEdit(staleKnown, { ...stale, operatingPoints: stale.operatingPoints.map((p) => ({ ...p })) }), false);
  // even a real edit in the stale tab is refused at push time
  assert.equal(mayPush(stale.updatedAt, server.updatedAt), false);
});
