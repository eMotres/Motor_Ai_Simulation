/** Series-shaping for the Admin · Overview live-load panel, tested without a
 * browser (`liveLoadSeries.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeNodeSeries, mergeLoadSeries, isCollectingHistory } from '../liveLoadSeries.ts';

test('merges per-node points by ts, prefixes keys, and means the cluster CPU', () => {
  const rows = mergeNodeSeries({
    eu1: [{ ts: 100, cpu: 20, mem: 40 }, { ts: 160, cpu: 60, mem: 50 }],
    eu2: [{ ts: 100, cpu: 80, mem: 10 }],
  });
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], { ts: 100, eu1_cpu: 20, eu1_mem: 40, eu2_cpu: 80, eu2_mem: 10, cluster_cpu: 50 });
  // only one node reported at ts=160 -> cluster mean is just that node's value
  assert.equal(rows[1].cluster_cpu, 60);
});

test('no nodes -> empty series, not a crash', () => {
  assert.deepEqual(mergeNodeSeries({}), []);
});

test('rows are sorted by ts even if nodes report out of order', () => {
  const rows = mergeNodeSeries({
    a: [{ ts: 200, cpu: 1, mem: 1 }, { ts: 100, cpu: 2, mem: 2 }],
  });
  assert.deepEqual(rows.map((r) => r.ts), [100, 200]);
});

// ── mergeLoadSeries: user CPU % + outside-app CPU % into one stacked chart ──
test('merges user and outside-app rows sharing a ts into one row', () => {
  const rows = mergeLoadSeries(
    [{ ts: 100, alice: 40, other: 5 }],
    [{ ts: 100, mesher_1: 60, host: 10 }],
  );
  assert.deepEqual(rows, [{ ts: 100, alice: 40, other: 5, mesher_1: 60, host: 10 }]);
});

test('a ts present in only one of the two series is kept as-is', () => {
  const rows = mergeLoadSeries(
    [{ ts: 100, alice: 40 }],
    [{ ts: 160, mesher_1: 60 }],
  );
  assert.deepEqual(rows, [{ ts: 100, alice: 40 }, { ts: 160, mesher_1: 60 }]);
});

test('mergeLoadSeries sorts by ts and handles empty inputs', () => {
  assert.deepEqual(mergeLoadSeries([], []), []);
  const rows = mergeLoadSeries([{ ts: 200, alice: 1 }], [{ ts: 100, host: 2 }]);
  assert.deepEqual(rows.map((r) => r.ts), [100, 200]);
});

test('a plain "other" from the user series is not clobbered by the outside-app overflow bucket', () => {
  // job_usage.user_load_series's overflow bucket is "other"; cluster_monitor's
  // is the distinct "outside-other" precisely so this merge cannot collide.
  const rows = mergeLoadSeries(
    [{ ts: 100, alice: 40, other: 5 }],
    [{ ts: 100, mesher_1: 60, 'outside-other': 3 }],
  );
  assert.deepEqual(rows, [{ ts: 100, alice: 40, other: 5, mesher_1: 60, 'outside-other': 3 }]);
});

// ── isCollectingHistory: "collecting since HH:MM" vs a broken-looking chart ─
test('short series soon after monitoring started -> collecting', () => {
  const now = 10_000;
  assert.equal(isCollectingHistory(1, now - 300, now), true);
  assert.equal(isCollectingHistory(0, now - 3599, now), true);
});

test('short series long after monitoring started -> genuinely idle, not "collecting"', () => {
  const now = 10_000;
  assert.equal(isCollectingHistory(1, now - 7200, now), false);
});

test('enough points -> never flagged as collecting, monitoringSince unknown -> never flagged', () => {
  const now = 10_000;
  assert.equal(isCollectingHistory(5, now - 60, now), false);
  assert.equal(isCollectingHistory(0, null, now), false);
});
