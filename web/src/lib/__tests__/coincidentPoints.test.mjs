// node --test — lib/coincidentPoints.ts and lib/endWindingPin.ts.
//
// Bug this exists for (owner 2026-10-30): a sweep over motor_length 90/95 at
// one pinned k_end returned identical (N·m/kg, η) for each 90 mm point and its
// 95 mm twin; the 90 mm markers were drawn under the 95 mm ones and vanished.
import test from 'node:test';
import assert from 'node:assert/strict';
import { coincidentGroups, coincidentOffsetPx } from '../coincidentPoints.ts';
import { endWindingPin, geoKeyHash } from '../endWindingPin.ts';

test('two points at the same spot are both kept and spread apart', () => {
  const g = coincidentGroups([
    { x: 39.62, y: 95.40 }, { x: 39.62, y: 95.40 }, { x: 41.0, y: 95.1 },
  ]);
  assert.deepEqual(g, [{ i: 0, n: 2 }, { i: 1, n: 2 }, null]);
  const dx = g.map(c => coincidentOffsetPx(c));
  assert.equal(dx[2], 0);
  assert.ok(dx[0] < 0 && dx[1] > 0);
  assert.equal(dx[0], -dx[1]);
});

test('points equal at the label precision count as one spot', () => {
  const g = coincidentGroups([{ x: 39.6201, y: 95.4012 }, { x: 39.6249, y: 95.3951 }]);
  assert.equal(g[0]?.n, 2);
  assert.equal(coincidentGroups([{ x: 39.62, y: 95.40 }, { x: 39.64, y: 95.40 }])[0], null);
});

test('three at one spot: the middle one stays on the true position', () => {
  const g = coincidentGroups([{ x: 1, y: 2 }, { x: 1, y: 2 }, { x: 1, y: 2 }]);
  assert.deepEqual(g.map(c => coincidentOffsetPx(c)), [-7, 0, 7]);
});

function withStorage(items, fn) {
  const store = new Map(Object.entries(items));
  globalThis.localStorage = {
    getItem: k => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k),
  };
  try { return fn(); } finally { delete globalThis.localStorage; }
}

test('a study pins k_end only when it was typed on this geometry', () => {
  const geo = { motor_length: 95, turns: 14 };
  const key = geoKeyHash(JSON.stringify(geo));
  // the coil's own number (not typed) → 0 = each point's own coil
  assert.deepEqual(withStorage({ 'sim.endWinding': '1.359', 'sim.endWindingTypedOn': '""' },
    () => endWindingPin(geo)), { k: 0, typed: false });
  assert.deepEqual(withStorage({ 'sim.endWinding': '1.359' }, () => endWindingPin(geo)),
    { k: 0, typed: false });
  // typed on THIS geometry → pinned
  assert.deepEqual(withStorage({ 'sim.endWinding': '1.5', 'sim.endWindingTypedOn': JSON.stringify(key) },
    () => endWindingPin(geo)), { k: 1.5, typed: true });
  // typed on another geometry → not an override of this one
  assert.deepEqual(withStorage({ 'sim.endWinding': '1.5', 'sim.endWindingTypedOn': JSON.stringify(key) },
    () => endWindingPin({ ...geo, motor_length: 90 })), { k: 0, typed: false });
});
