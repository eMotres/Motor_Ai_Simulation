/**
 * node --test — lib/perSetCurrent.ts: six-phase current per set
 * (owner 2026-10-29, option 1).  The engineer enters the PER-SET current of a
 * two-set winding; storage and solver keep the both-sets (equivalent 3-phase)
 * current.  A 3-phase winding's parallel paths are never sets.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { currentText, perSetFromTotal, setsOf, totalFromPerSet } from '../perSetCurrent.ts';

test('only phases 6 is a two-set winding', () => {
  assert.equal(setsOf({ phases: 6 }), 2);
  assert.equal(setsOf({ phases: '6' }), 2);
  assert.equal(setsOf({ phases: 3 }), 1);
  assert.equal(setsOf(null), 1);
  // 4S-2P on three phases: two parallel paths, ONE set
  assert.equal(setsOf({ phases: 3, n_parallel: 2, connection: '4S-2P' }), 1);
});

test('per set in, both sets stored — and back, exactly', () => {
  assert.equal(totalFromPerSet(99.8, 2), 199.6);
  assert.equal(perSetFromTotal(199.6, 2), 99.8);
  assert.equal(totalFromPerSet(99.8, 1), 99.8);
  assert.equal(perSetFromTotal(199.6, 1), 199.6);
  for (const v of [0, 12.34, 199.7, 707]) assert.equal(perSetFromTotal(totalFromPerSet(v, 2), 2), v);
});

test('messages say per set for two sets, plain A otherwise', () => {
  assert.equal(currentText(199.6, 2), '99.8 A per set');
  assert.equal(currentText(199.6, 1), '199.6 A');
  assert.equal(currentText(NaN, 2), '—');
});
