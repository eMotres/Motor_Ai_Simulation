import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runSliced } from '../slicedWork.ts';

test('the first slice runs at once, the rest after a deferral, every item exactly once', () => {
  let clock = 0; const queue = []; const done = []; let repaints = 0;
  runSliced([1, 2, 3, 4, 5], (x) => { done.push(x); clock += 5; }, { sliceMs: 10, alive: () => true, afterSlice: () => { repaints += 1; },
    now: () => clock, defer: (fn) => queue.push(fn) });
  assert.deepEqual(done, [1, 2]);                       // 2 x 5 ms = one 10 ms slice
  while (queue.length) queue.shift()();
  assert.deepEqual(done, [1, 2, 3, 4, 5]);
  assert.equal(repaints, 3);
});

test('a new model stops the old work; cancel stops it too', () => {
  let alive = true; const queue = []; const done = [];
  runSliced([1, 2, 3], (x) => done.push(x), { sliceMs: 0, alive: () => alive, now: () => 0, defer: (fn) => queue.push(fn) });
  alive = false;
  while (queue.length) queue.shift()();
  assert.deepEqual(done, [1]);
  const q2 = []; const d2 = [];
  const cancel = runSliced([1, 2, 3], (x) => d2.push(x), { sliceMs: 0, alive: () => true, now: () => 0, defer: (fn) => { q2.push(fn); return q2.length; }, cancelDefer: () => q2.pop() });
  cancel();
  while (q2.length) q2.shift()();
  assert.deepEqual(d2, [1]);
});

test('no items: nothing runs', () => {
  let ran = false;
  runSliced([], () => { ran = true; }, { sliceMs: 5, alive: () => true, afterSlice: () => { ran = true; } });
  assert.equal(ran, false);
});
