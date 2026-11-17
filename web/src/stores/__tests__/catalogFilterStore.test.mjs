// node --test: the catalog filter store (owner 2026-11-17): per account, remembered, saved sets.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const mem = new Map();
globalThis.localStorage = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };
const { useCatalogFilterStore: S } = await import('../catalogFilterStore.ts');

test('the filter and the saved sets are remembered per account; "my current" is one click', () => {
  S.getState().bindAccount('owner@example.test');
  S.getState().set({ stages: ['simulation', 'in_production'], state: 'active' });
  S.getState().saveSet('My current');
  assert.equal(S.getState().activeSet, 'My current');
  S.getState().set({ running: true });
  assert.equal(S.getState().activeSet, null);                                   // changed after the save: no set is "active"
  S.getState().applySet('My current');
  assert.deepEqual([S.getState().filter.stages, S.getState().filter.running, S.getState().activeSet], [['simulation', 'in_production'], false, 'My current']);
  S.getState().bindAccount('client@example.test');                              // another account: its own memory
  assert.deepEqual([S.getState().filter.stages, S.getState().sets.length], [[], 0]);
  S.getState().bindAccount('owner@example.test');                               // back: everything as it was left
  assert.deepEqual([S.getState().sets.map((s) => s.name), S.getState().activeSet, S.getState().filter.stages], [['My current'], 'My current', ['simulation', 'in_production']]);
  S.getState().clear();
  assert.deepEqual([S.getState().filter.stages, S.getState().filter.state], [[], 'active']);
  S.getState().deleteSet('My current');
  assert.equal(S.getState().sets.length, 0);
  assert.ok([...mem.keys()].every((k) => k.startsWith('view.motors.filters.')), [...mem.keys()].join(' '));
});
