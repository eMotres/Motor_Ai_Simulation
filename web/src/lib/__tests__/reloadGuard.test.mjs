import test from 'node:test';
import assert from 'node:assert/strict';
import { decideStaleChunkRecovery, recordStaleChunkRecovery, RELOAD_GUARD_KEY } from '../reloadGuard.ts';

function storage(value = null, options = {}) {
  const data = new Map(value == null ? [] : [[RELOAD_GUARD_KEY, String(value)]]);
  return {
    data,
    getItem(key) { if (options.readThrows) throw new Error('blocked'); return data.get(key) ?? null; },
    setItem(key, next) { if (options.writeThrows) throw new Error('blocked'); data.set(key, next); },
    removeItem(key) { data.delete(key); },
  };
}

test('first failure records the timestamp before allowing one reload', () => {
  const state = storage();
  assert.equal(recordStaleChunkRecovery(state, 100_000), 'reload');
  assert.equal(state.data.get(RELOAD_GUARD_KEY), '100000');
});

test('a second failure at 59,999 ms shows the banner without resetting the timestamp', () => {
  const state = storage(100_000);
  assert.equal(decideStaleChunkRecovery('100000', 159_999), 'banner');
  assert.equal(state.data.get(RELOAD_GUARD_KEY), '100000');
});

test('a failure at exactly 60,000 ms may reload and starts a new guard window', () => {
  const state = storage(100_000);
  assert.equal(recordStaleChunkRecovery(state, 160_000), 'reload');
  assert.equal(state.data.get(RELOAD_GUARD_KEY), '160000');
});

test('malformed and future timestamps conservatively show the banner', () => {
  assert.equal(decideStaleChunkRecovery('not-a-timestamp', 200_000), 'banner');
  assert.equal(decideStaleChunkRecovery('200001', 200_000), 'banner');
});

test('unavailable storage and denied reads show the banner', () => {
  assert.equal(recordStaleChunkRecovery(null, 100_000), 'banner');
  assert.equal(recordStaleChunkRecovery(storage(null, { readThrows: true }), 100_000), 'banner');
});

test('denied writes show the banner instead of reloading into a loop', () => {
  assert.equal(recordStaleChunkRecovery(storage(null, { writeThrows: true }), 100_000), 'banner');
  assert.equal(recordStaleChunkRecovery(storage(40_000, { writeThrows: true }), 100_000), 'banner');
});

test('a failure after 60 seconds can make one fresh attempt', () => {
  assert.equal(decideStaleChunkRecovery('40000', 100_000), 'reload');
});
