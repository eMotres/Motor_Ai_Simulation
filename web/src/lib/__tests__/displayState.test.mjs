// node --test: Active / Parked / Archived list helpers (lib/displayState.ts).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { activityAfter, filterCounts, filterRows, stateOf } from '../displayState.ts';

const rec = (state, at = '2026-11-10T00:00:00Z') => ({ state, explicit: true, default: 'active', reason: '', by: 'a', at });
const map = { a: rec('active'), p: rec('parked'), x: rec('archived') };
const rows = [{ id: 'a' }, { id: 'p' }, { id: 'x' }, { id: 'new' }];
const st = (r) => stateOf(map, r.id);

test('an unknown object is active', () => {
  assert.equal(stateOf(map, 'new'), 'active');
  assert.equal(stateOf(undefined, 'p'), 'active');
});

test('filter and counts', () => {
  assert.deepEqual(filterRows(rows, 'active', st).map((r) => r.id), ['a', 'new']);
  assert.deepEqual(filterRows(rows, 'parked', st).map((r) => r.id), ['p']);
  assert.equal(filterRows(rows, 'all', st).length, 4);
  assert.equal(filterRows(rows, 'parked', st, true).length, 4);   // a search finds every state
  const c = filterCounts(rows, st, (r) => r.id !== 'a');
  assert.deepEqual(c.counts, { active: 2, parked: 1, archived: 1, all: 4 });
  assert.deepEqual(c.woke, { active: 0, parked: 1, archived: 1, all: 2 });   // activity in an active row is not "woke"
});

test('activity after hiding never changes the state, it only marks the row', () => {
  assert.equal(activityAfter(map.p, ['2026-11-12T00:00:00Z']), true);
  assert.equal(activityAfter(map.p, ['2026-11-01T00:00:00Z', null]), false);
  assert.equal(activityAfter(map.p, [], 2), true);
  assert.equal(activityAfter(map.a, ['2026-11-12T00:00:00Z'], 5), false);
  assert.equal(activityAfter(rec('parked', ''), ['2026-11-12T00:00:00Z']), false);
  assert.equal(stateOf(map, 'p'), 'parked');
});
